"""Deterministic function nodes of the dispatch graph.

These are the parts of the workflow that must behave identically every time:
the security screen, the policy router, the zero-LLM fast path, the
human-approval gate, the side-effecting dispatch, and the async memory write.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from google.adk import Context, Event
from google.adk.events import RequestInput
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.workflow import node
from google.genai import types
from pydantic import ValidationError

from . import config
from .guardrails import redact_pii, scan_injection
from .observability import log_intent, log_outcome, traced
from .schemas import (
    ApprovalDecision,
    ApprovalRequestPayload,
    DispatchOrder,
    DonationSubmission,
    DonationTriage,
    MatchProposal,
    RejectionNotice,
    RiskAssessment,
    ScreenedSubmission,
)
from .tools import find_recipient_orgs, notify_recipient_org

logger = logging.getLogger("good_neighbour.nodes")

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


# --- 1. Pre-LLM security screen -----------------------------------------------


def screen_submission(node_input: str):
    """Redact PII and scan for prompt injection before any model sees the text.

    Accepts either free text (chat) or a JSON ``DonationSubmission`` (Pub/Sub).
    Routes to ``blocked`` or ``clean``.
    """
    text = (node_input or "").strip()
    submission: DonationSubmission | None = None
    if text.startswith("{"):
        try:
            submission = DonationSubmission.model_validate_json(text)
        except ValidationError:
            submission = None

    donation_id = (
        submission.donation_id
        if submission and submission.donation_id
        else f"don-{uuid4().hex[:8]}"
    )
    redaction = redact_pii(text)
    findings = scan_injection(redaction.text)

    if submission is not None:
        submission = submission.model_copy(
            update={
                "donation_id": donation_id,
                "description": redact_pii(submission.description).text,
                "donor_name": redact_pii(submission.donor_name or "").text or None,
            }
        )

    screened = ScreenedSubmission(
        donation_id=donation_id,
        redacted_text=redaction.text,
        submission=submission,
        redactions=redaction.categories,
        injection_findings=findings,
        blocked=bool(findings),
        block_reason=(
            f"Prompt-injection heuristics fired: {', '.join(findings)}" if findings else None
        ),
    )
    logger.info(
        "screen.result",
        extra={
            "donation_id": donation_id,
            "redactions": redaction.categories,
            "blocked": screened.blocked,
            "findings": findings,
        },
    )
    yield Event(
        message=(
            f"🛡️ Screened donation `{donation_id}` — "
            f"{len(redaction.categories)} PII field(s) redacted"
            + (f"; **blocked** ({screened.block_reason})." if screened.blocked else ".")
        )
    )
    yield Event(
        output=screened.model_dump(mode="json"),
        route="blocked" if screened.blocked else "clean",
        state={"donation_id": donation_id, "screen": screened.model_dump(mode="json")},
    )


def reject_submission(ctx: Context, node_input: Any):
    """Terminal node for blocked or declined submissions."""
    donation_id = ctx.state.get("donation_id", "unknown")
    if isinstance(node_input, dict) and node_input.get("stage"):
        notice = RejectionNotice(**node_input)
    else:
        reason = (
            node_input.get("block_reason") if isinstance(node_input, dict) else None
        ) or "Submission did not pass the intake screen."
        notice = RejectionNotice(donation_id=donation_id, reason=reason, stage="screen")
    logger.warning("submission.rejected", extra=notice.model_dump())
    yield Event(message=f"⛔ Donation `{notice.donation_id}` not dispatched: {notice.reason}")
    yield Event(output=notice.model_dump(mode="json"), state={"outcome": "rejected"})


# --- 2. Policy router (zero tokens) -------------------------------------------


def route_by_risk(node_input: DonationTriage):
    """Decide whether a donation can be auto-dispatched or needs human review.

    Pure policy, no model: perishable food, large lots, temperature-controlled
    storage, or an assumed location all require a coordinator.
    """
    triage = node_input
    reasons: list[str] = []
    if triage.perishable:
        reasons.append("perishable")
    if triage.storage in ("hot", "cold"):
        reasons.append(f"storage={triage.storage}")
    if triage.meals_estimate > config.AUTO_DISPATCH_MAX_MEALS:
        reasons.append(f"meals>{config.AUTO_DISPATCH_MAX_MEALS}")
    if triage.location_assumed:
        reasons.append("location_assumed")
    route = "needs_review" if reasons else "low_risk"
    logger.info(
        "router.decision",
        extra={"donation_id": triage.donation_id, "route": route, "reasons": reasons},
    )
    dump = triage.model_dump(mode="json")
    yield Event(
        message=(
            f"🧭 Route: **{route}**"
            + (f" ({', '.join(reasons)})" if reasons else " — eligible for zero-LLM auto-dispatch")
        )
    )
    yield Event(
        output=dump,
        route=route,
        state={
            "triage": dump,
            "triage_json": json.dumps(dump),
            "risk_route": route,
            "route_reasons": reasons,
        },
    )


# --- 3. Fast path --------------------------------------------------------------


def auto_match(node_input: DonationTriage):
    """Deterministic nearest-org match for small, non-perishable lots.

    Falls back to the LLM matcher (``no_match``) if nothing is in radius.
    """
    triage = node_input
    started = log_intent("auto_match", donation_id=triage.donation_id, meals=triage.meals_estimate)
    with traced("auto_match", donation_id=triage.donation_id):
        result = find_recipient_orgs(
            latitude=triage.pickup_location.lat,
            longitude=triage.pickup_location.lng,
            meals=triage.meals_estimate,
            perishable=False,
            radius_km=config.DEFAULT_RADIUS_KM,
        )
    if result.get("status") == "error":
        log_outcome("auto_match", started, ok=False, error_code=result.get("error_code"))
        yield Event(message="🔁 No recipient in the default radius — handing to the matcher.")
        yield Event(
            output={
                "triage": triage.model_dump(mode="json"),
                "revision_note": "Auto-match found no org in radius; please match manually.",
            },
            route="no_match",
        )
        return
    top = result["candidates"][0]
    order = DispatchOrder(
        dispatch_id=f"dsp-{uuid4().hex[:8]}",
        donation_id=triage.donation_id,
        org_id=top["org_id"],
        org_name=top["name"],
        meals=triage.meals_estimate,
        pickup_location=triage.pickup_location,
        pickup_deadline=triage.pickup_deadline,
        path="auto",
    )
    log_outcome("auto_match", started, ok=True, org_id=order.org_id, distance_km=top["distance_km"])
    yield Event(
        message=(
            f"⚡ Auto-matched to **{order.org_name}** ({top['distance_km']} km) "
            "— no model call needed."
        )
    )
    yield Event(
        output=order.model_dump(mode="json"),
        route="matched",
        state={"dispatch_order": order.model_dump(mode="json")},
    )


# --- 4. Human-in-the-loop gate ---------------------------------------------------


def _parse_decision(raw: Any) -> ApprovalDecision:
    if isinstance(raw, dict):
        if "decision" in raw:
            return ApprovalDecision.model_validate(raw)
        raw = raw.get("result", raw)
    text = str(raw).strip().lower()
    if text.startswith(("approve", "yes", "ok", "y")):
        return ApprovalDecision(decision="approve")
    if text.startswith(("revise", "change", "different")):
        return ApprovalDecision(decision="revise", note=str(raw))
    return ApprovalDecision(decision="reject", note=str(raw))


@node(rerun_on_resume=True)
async def coordinator_approval(ctx: Context, node_input: RiskAssessment) -> AsyncIterator[Any]:
    """Hard stop: a human coordinator must approve, revise, or reject.

    Emits a ``RequestInput`` and pauses the workflow. On resume the decision
    becomes a route. Policy override: an ``unsafe`` risk level cannot be
    approved — the gate converts it to a rejection and says why.
    """
    review_count = int(ctx.state.get("review_count", 0))
    interrupt_id = f"coordinator_review_{review_count}"
    triage = DonationTriage(**ctx.state["triage"])
    match = MatchProposal(**ctx.state["match_proposal"])
    risk = node_input

    raw = ctx.resume_inputs.get(interrupt_id) if ctx.resume_inputs else None
    if raw is None:
        payload = ApprovalRequestPayload(donation=triage, match=match, risk=risk)
        log_intent(
            "hitl:request_approval",
            donation_id=triage.donation_id,
            org_id=match.org_id,
            risk_level=risk.risk_level,
            review_round=review_count,
        )
        yield Event(
            message=(
                f"⏸️ **Coordinator approval required** for `{triage.donation_id}`\n"
                f"- Proposed recipient: {match.org_name} ({match.distance_km} km)\n"
                f"- Risk: **{risk.risk_level}** — {risk.summary}\n"
                + (f"- Conditions: {'; '.join(risk.conditions)}\n" if risk.conditions else "")
                + "Reply `approve`, `revise <note>`, or `reject <reason>`."
            )
        )
        yield RequestInput(
            interrupt_id=interrupt_id,
            message="Approve dispatch? (approve / revise / reject)",
            payload=payload.model_dump(mode="json"),
            response_schema=ApprovalDecision,
        )
        return

    decision = _parse_decision(raw)
    state: dict[str, Any] = {
        "review_count": review_count + 1,
        "last_decision": decision.model_dump(),
    }
    logger.info(
        "hitl.decision",
        extra={
            "donation_id": triage.donation_id,
            "decision": decision.decision,
            "round": review_count,
        },
    )

    if decision.decision == "approve" and risk.risk_level == "unsafe":
        notice = RejectionNotice(
            donation_id=triage.donation_id,
            reason=f"Policy override: risk level 'unsafe' cannot be approved. {risk.summary}",
            stage="coordinator",
        )
        yield Event(
            message="🚫 Approval refused by policy — the risk reviewer flagged this lot as unsafe."
        )
        yield Event(output=notice.model_dump(mode="json"), route="rejected", state=state)
        return

    if decision.decision == "approve":
        order = DispatchOrder(
            dispatch_id=f"dsp-{uuid4().hex[:8]}",
            donation_id=triage.donation_id,
            org_id=match.org_id,
            org_name=match.org_name,
            meals=triage.meals_estimate,
            pickup_location=triage.pickup_location,
            pickup_deadline=triage.pickup_deadline or risk.safe_until,
            path="approved",
            conditions=list(risk.conditions),
            approved_by="coordinator",
        )
        state["dispatch_order"] = order.model_dump(mode="json")
        yield Event(message=f"✅ Approved by coordinator → {order.org_name}.")
        yield Event(output=order.model_dump(mode="json"), route="approved", state=state)
        return

    if decision.decision == "revise":
        yield Event(message=f"✏️ Coordinator requested a revision: {decision.note or 'no note'}")
        yield Event(
            output={
                "triage": triage.model_dump(mode="json"),
                "revision_note": decision.note or "Coordinator requested a different recipient.",
            },
            route="revise",
            state=state,
        )
        return

    notice = RejectionNotice(
        donation_id=triage.donation_id,
        reason=decision.note or "Rejected by coordinator.",
        stage="coordinator",
    )
    yield Event(output=notice.model_dump(mode="json"), route="rejected", state=state)


# --- 5. Dispatch (the only side effect) ------------------------------------------


def dispatch_donation(node_input: DispatchOrder):
    """Notify the recipient. Logs INTENT before and OUTCOME after the call."""
    order = node_input
    started = log_intent(
        "dispatch:notify_recipient_org",
        dispatch_id=order.dispatch_id,
        donation_id=order.donation_id,
        org_id=order.org_id,
        meals=order.meals,
        path=order.path,
    )
    with traced(
        "dispatch.notify_recipient_org", dispatch_id=order.dispatch_id, org_id=order.org_id
    ):
        receipt = notify_recipient_org(
            dispatch_id=order.dispatch_id,
            org_id=order.org_id,
            meals=order.meals,
            pickup_deadline=order.pickup_deadline.isoformat() if order.pickup_deadline else None,
            notes="; ".join(order.conditions),
        )
    ok = receipt.get("status") == "ok"
    log_outcome(
        "dispatch:notify_recipient_org",
        started,
        ok=ok,
        dispatch_id=order.dispatch_id,
        org_id=order.org_id,
        channel=receipt.get("channel"),
        error_code=None if ok else receipt.get("error_code"),
    )
    if ok:
        yield Event(
            message=(
                f"🚚 Dispatched `{order.dispatch_id}`: {order.meals} meals → **{order.org_name}** "
                f"(path: {order.path}; receipt via {receipt['channel']})."
            )
        )
    else:
        yield Event(message=f"⚠️ Notification failed: {receipt.get('message')}")
    yield Event(
        output={"order": order.model_dump(mode="json"), "receipt": receipt},
        state={"last_receipt": receipt, "outcome": "dispatched" if ok else "notify_failed"},
    )


# --- 6. Async memory consolidation ------------------------------------------------


async def _persist_memory(ctx: Context, text: str, order: dict[str, Any]) -> None:
    """Write one memory, portable across memory back-ends.

    Vertex AI Memory Bank supports direct ``add_memory`` writes; the local
    SQLite / in-memory services only ingest events, so we fall back to
    ``add_events_to_memory`` with a synthetic event carrying the same text.
    """
    metadata = {"dispatch_id": order["dispatch_id"], "org_id": order["org_id"]}
    content = types.Content(role="user", parts=[types.Part(text=text)])
    try:
        try:
            await ctx.add_memory(
                memories=[
                    MemoryEntry(
                        content=content,
                        author="good_neighbour",
                        timestamp=datetime.now(UTC).isoformat(),
                        custom_metadata=metadata,
                    )
                ]
            )
        except NotImplementedError:
            await ctx.add_events_to_memory(
                events=[Event(author="good_neighbour", content=content)],
                custom_metadata=metadata,
            )
        logger.info("memory.persisted", extra={"dispatch_id": order["dispatch_id"]})
    except ValueError:
        logger.info("memory.skipped", extra={"reason": "no memory service configured"})
    except Exception:  # noqa: BLE001 - background work must never crash the run
        logger.exception("memory.persist_failed", extra={"dispatch_id": order.get("dispatch_id")})


async def record_outcome(ctx: Context, node_input: dict[str, Any]):
    """Write the dispatch outcome to long-term memory *without* blocking the turn."""
    order = node_input["order"]
    triage = ctx.state.get("triage", {})
    text = (
        f"Dispatched {order['meals']} meals of {triage.get('food_type', 'food')} "
        f"from {triage.get('pickup_site_name') or 'an unnamed site'} to {order['org_name']} "
        f"({order['org_id']}) via the {order['path']} path."
    )
    task = asyncio.create_task(_persist_memory(ctx, text, order))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    yield Event(message="📒 Outcome recorded; donor history is being updated in the background.")
    yield Event(
        output={
            "status": "dispatched",
            "dispatch_id": order["dispatch_id"],
            "org_id": order["org_id"],
        },
        state={
            "user:last_dispatch": {"dispatch_id": order["dispatch_id"], "org_id": order["org_id"]},
            "user:dispatch_count": int(ctx.state.get("user:dispatch_count", 0)) + 1,
        },
    )


async def await_background_tasks() -> None:
    """Flush pending memory writes (call before a CLI process exits)."""
    if _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
