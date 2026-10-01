"""The dispatch graph and the ``App`` that ADK tooling loads.

Graph (see README for the picture)::

    START ─▶ screen_submission ─┬─ blocked ─▶ reject_submission
                                └─ clean ───▶ triage_agent (Flash)
                                                  │
                                            route_by_risk (policy, no LLM)
                                         ┌────────┴─────────┐
                                     low_risk           needs_review
                                         │                  │
                                    auto_match        matcher_agent (Flash)
                                   ┌─────┴────┐             │
                               matched     no_match ───▶ risk_reviewer_agent (Pro)
                                   │                        │
                                   │                 coordinator_approval ⏸ (HITL)
                                   │              ┌──────────┼───────────┐
                                   │          approved    revise      rejected
                                   │              │          │            │
                                   └──▶ dispatch_donation    └▶ matcher   └▶ reject_submission
                                              │
                                        record_outcome (async memory write)

``build_workflow`` accepts replacement nodes so tests can swap each LLM
specialist for a deterministic function and exercise every edge offline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ``.env`` sits beside this file. Load it before ``config`` snapshots os.environ.
load_dotenv(Path(__file__).resolve().parent / ".env")

from google.adk import Workflow  # noqa: E402
from google.adk.apps import App, ResumabilityConfig  # noqa: E402
from google.adk.apps.app import EventsCompactionConfig  # noqa: E402
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer  # noqa: E402
from google.adk.models.google_llm import Gemini  # noqa: E402

from . import config  # noqa: E402
from .agents import build_matcher_agent, build_risk_reviewer_agent, build_triage_agent  # noqa: E402
from .nodes import (  # noqa: E402
    auto_match,
    coordinator_approval,
    dispatch_donation,
    record_outcome,
    reject_submission,
    route_by_risk,
    screen_submission,
)
from .observability import configure_observability  # noqa: E402
from .secrets import hydrate_model_credentials  # noqa: E402

configure_observability()
hydrate_model_credentials()

Node = Callable[..., Any] | Any


def build_workflow(
    *,
    triage: Node | None = None,
    matcher: Node | None = None,
    risk_reviewer: Node | None = None,
    name: str = config.APP_NAME,
) -> Workflow:
    """Assemble the dispatch graph.

    Args:
        triage: Node that turns screened text into a ``DonationTriage``.
            Defaults to the Gemini Flash triage agent.
        matcher: Node that proposes a recipient (``MatchProposal``).
            Defaults to the Gemini Flash matcher agent.
        risk_reviewer: Node that returns a ``RiskAssessment``.
            Defaults to the Gemini Pro risk reviewer.
        name: Workflow name (also the agent name shown in ``adk web``).

    """
    triage = triage or build_triage_agent()
    matcher = matcher or build_matcher_agent()
    risk_reviewer = risk_reviewer or build_risk_reviewer_agent()

    return Workflow(
        name=name,
        description=(
            "Turns a surplus-food offer into a safe, dispatched hand-off. "
            "Small non-perishable lots are auto-dispatched without a model call; "
            "everything else is matched, risk-reviewed, and held for a human coordinator."
        ),
        input_schema=str,
        edges=[
            ("START", screen_submission),
            (screen_submission, {"blocked": reject_submission, "clean": triage}),
            (triage, route_by_risk),
            (route_by_risk, {"low_risk": auto_match, "needs_review": matcher}),
            (auto_match, {"matched": dispatch_donation, "no_match": matcher}),
            (matcher, risk_reviewer),
            (risk_reviewer, coordinator_approval),
            (
                coordinator_approval,
                {"approved": dispatch_donation, "revise": matcher, "rejected": reject_submission},
            ),
            (dispatch_donation, record_outcome),
        ],
    )


def build_app(root: Workflow | None = None, *, name: str = config.APP_NAME) -> App:
    """Wrap a workflow in an ``App`` with resumability and history compaction.

    * ``ResumabilityConfig(is_resumable=True)`` lets the coordinator gate pause
      the invocation and resume it later from the persisted session.
    * ``EventsCompactionConfig`` summarizes older events every 5 invocations
      so a long-lived coordinator session does not grow without bound. The
      root node is a ``Workflow`` (no model of its own), so we provide the
      Flash-backed summarizer explicitly.
    """
    return App(
        name=name,
        root_agent=root or build_workflow(),
        resumability_config=ResumabilityConfig(is_resumable=True),
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=5,
            overlap_size=1,
            summarizer=LlmEventSummarizer(llm=Gemini(model=config.FAST_MODEL)),
        ),
    )


root_agent = build_workflow()
app = build_app(root_agent)
