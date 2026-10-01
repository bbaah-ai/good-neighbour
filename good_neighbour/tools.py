"""Tools exposed to the LLM agents (and reused directly by function nodes).

Design rules enforced here:

* **Descriptive names** — each tool says exactly what it does.
* **Docstrings + type hints** — ADK builds the tool declaration from them, so
  every parameter is documented.
* **Explicit schemas** — arguments are validated through the Pydantic models in
  :mod:`good_neighbour.schemas`; results are ``model_dump()`` of typed models.
* **Guided errors** — tools never raise to the model. They return a
  :class:`~good_neighbour.schemas.ToolError` with a ``recovery_instruction``.
* **Idempotency** — the only side-effecting tool, ``notify_recipient_org``,
  is idempotent on ``dispatch_id``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from pydantic import ValidationError

from . import config
from .schemas import (
    CapacityResult,
    DonorSite,
    GeoPoint,
    NotificationReceipt,
    RecipientOrg,
    RecipientSearch,
    RecipientSearchResult,
    SafetyWindow,
    ToolError,
)

logger = logging.getLogger("good_neighbour.tools")

# --- Data access -------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_orgs() -> list[RecipientOrg]:
    raw = json.loads((config.DATA_DIR / "recipient_orgs.json").read_text())
    return [RecipientOrg(**o) for o in raw["orgs"]]


@lru_cache(maxsize=1)
def _load_sites() -> list[dict[str, Any]]:
    return json.loads((config.DATA_DIR / "donor_sites.json").read_text())["sites"]


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance between two points in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _validation_error(exc: ValidationError, recovery: str) -> dict[str, Any]:
    first = exc.errors()[0]
    return ToolError(
        error_code="INVALID_ARGUMENTS",
        message=f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}",
        recovery_instruction=recovery,
    ).model_dump()


# --- Tools -------------------------------------------------------------------


def lookup_donor_site(name_or_address: str) -> dict[str, Any]:
    """Resolve a donor's site name or street address to coordinates.

    Use this before searching for recipients when the submission names a
    venue ("MTCC", "Royal York", "PATH food court") or a downtown address but
    gives no latitude/longitude.

    Args:
      name_or_address: Free-text venue name, alias, or address fragment.

    Returns:
      A ``DonorSite`` dict with ``location.lat``/``location.lng`` on success, or
      a ``ToolError`` whose ``recovery_instruction`` explains how to proceed
      when the site is unknown.

    """
    needle = (name_or_address or "").strip().lower()
    if not needle:
        return ToolError(
            error_code="EMPTY_QUERY",
            message="No site name or address was provided.",
            recovery_instruction="Ask for the pickup venue or set location_assumed=true and "
            "use the downtown default coordinates.",
        ).model_dump()
    for site in _load_sites():
        haystack = [site["name"].lower(), site["address"].lower(), *site["aliases"]]
        if any(needle in h or h in needle for h in haystack):
            return DonorSite(
                site_id=site["site_id"],
                name=site["name"],
                address=site["address"],
                location=GeoPoint(**site["location"]),
                site_type=site["site_type"],
            ).model_dump(mode="json")
    return ToolError(
        error_code="SITE_NOT_FOUND",
        message=f"No known donor site matches '{name_or_address}'.",
        recovery_instruction="Proceed with the downtown Toronto default location "
        f"({config.DEFAULT_PICKUP_LAT}, {config.DEFAULT_PICKUP_LNG}) and set "
        "location_assumed=true so the coordinator can confirm the address.",
    ).model_dump()


def find_recipient_orgs(
    latitude: float,
    longitude: float,
    meals: int,
    perishable: bool,
    radius_km: float = config.DEFAULT_RADIUS_KM,
) -> dict[str, Any]:
    """Find recipient organizations near a pickup point that can take a donation.

    Filters the roster by remaining capacity (>= ``meals``), by refrigeration
    when the food is perishable, and by whether the org accepts prepared food;
    ranks the survivors by distance.

    Args:
      latitude: Pickup latitude in decimal degrees.
      longitude: Pickup longitude in decimal degrees.
      meals: Number of servings to place.
      perishable: True if the food needs cold/hot holding on arrival.
      radius_km: Search radius; widen up to 25 km if nothing is found.

    Returns:
      ``RecipientSearchResult`` with up to five ranked ``candidates`` (each
      including ``distance_km``), or a ``ToolError`` describing how to widen
      the search.

    """
    try:
        query = RecipientSearch(
            latitude=latitude,
            longitude=longitude,
            meals=meals,
            perishable=perishable,
            radius_km=radius_km,
        )
    except ValidationError as exc:
        return _validation_error(
            exc, "Fix the argument and call find_recipient_orgs again; radius_km must be 0–25."
        )

    origin = GeoPoint(lat=query.latitude, lng=query.longitude)
    candidates: list[RecipientOrg] = []
    for org in _load_orgs():
        if org.remaining_capacity_meals < query.meals:
            continue
        if query.perishable and not org.has_refrigeration:
            continue
        if not org.accepts_prepared_food:
            continue
        distance = haversine_km(origin, org.location)
        if distance <= query.radius_km:
            candidates.append(org.model_copy(update={"distance_km": round(distance, 2)}))
    candidates.sort(key=lambda o: (o.distance_km or 0.0, -o.remaining_capacity_meals))

    if not candidates:
        next_radius = min(query.radius_km * 2, config.MAX_RADIUS_KM)
        return ToolError(
            error_code="NO_ORGS_IN_RADIUS",
            message=f"No org within {query.radius_km} km can take {query.meals} "
            f"{'perishable ' if query.perishable else ''}meals right now.",
            recovery_instruction=(
                f"Call find_recipient_orgs again with radius_km={next_radius}. "
                "If still empty at 25 km, split the donation across two smaller lots "
                "or recommend do_not_dispatch."
            ),
        ).model_dump()
    return RecipientSearchResult(candidates=candidates[:5], radius_km=query.radius_km).model_dump(
        mode="json"
    )


def check_org_capacity(org_id: str, meals: int) -> dict[str, Any]:
    """Confirm a specific organization can accept a given number of meals today.

    Args:
      org_id: Identifier returned by ``find_recipient_orgs`` (e.g. ``org-corktown-table``).
      meals: Servings you intend to send.

    Returns:
      ``CapacityResult`` with ``can_accept`` and the remaining headroom, or a
      ``ToolError`` if the org id is unknown.

    """
    if meals < 1:
        return ToolError(
            error_code="INVALID_MEALS",
            message="meals must be at least 1.",
            recovery_instruction="Re-read the triage meals_estimate and call again.",
        ).model_dump()
    for org in _load_orgs():
        if org.org_id == org_id:
            ok = org.remaining_capacity_meals >= meals
            return CapacityResult(
                org_id=org.org_id,
                name=org.name,
                can_accept=ok,
                remaining_capacity_meals=org.remaining_capacity_meals,
                reason=(
                    "Within today's remaining capacity."
                    if ok
                    else f"Only {org.remaining_capacity_meals} meals of headroom left today."
                ),
            ).model_dump()
    return ToolError(
        error_code="ORG_NOT_FOUND",
        message=f"Unknown org_id '{org_id}'.",
        recovery_instruction=(
            "Call find_recipient_orgs first and use an org_id from its candidates."
        ),
    ).model_dump()


_HOLDING_RULES: dict[str, tuple[float, str]] = {
    "hot": (4.0, "hot-held prepared food: 4 h from preparation (time/temperature control)"),
    "cold": (48.0, "refrigerated prepared food: 48 h from preparation"),
    "frozen": (72.0, "frozen food: 72 h holding window before transfer must complete"),
    "ambient": (24.0, "ambient shelf-stable or baked goods: 24 h from preparation"),
}


def estimate_food_safety_window(food_type: str, storage: str, prepared_at: str) -> dict[str, Any]:
    """Estimate how long a donation remains safe to redistribute.

    Applies conservative holding-time rules by storage condition. This is a
    planning aid for the coordinator, not a substitute for a food handler's
    judgement.

    Args:
      food_type: Short description, e.g. ``"hot catered entrees"``.
      storage: One of ``hot``, ``cold``, ``frozen``, ``ambient``.
      prepared_at: ISO-8601 timestamp (UTC preferred) when the food was prepared.

    Returns:
      ``SafetyWindow`` with ``safe_until`` and ``hours_remaining`` (may be
      negative if already expired), or a ``ToolError`` for bad inputs.

    """
    rule = _HOLDING_RULES.get((storage or "").lower())
    if rule is None:
        return ToolError(
            error_code="UNKNOWN_STORAGE",
            message=f"storage must be one of {sorted(_HOLDING_RULES)}; got '{storage}'.",
            recovery_instruction="Infer the storage condition from the description "
            "(e.g. 'trays from a buffet' → hot) and call again.",
        ).model_dump()
    try:
        prepared = datetime.fromisoformat(prepared_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ToolError(
            error_code="INVALID_TIMESTAMP",
            message=f"prepared_at '{prepared_at}' is not ISO-8601.",
            recovery_instruction="Use a format like 2026-10-01T15:30:00Z. If the donor gave "
            "only a clock time, assume today's date.",
        ).model_dump()
    if prepared.tzinfo is None:
        prepared = prepared.replace(tzinfo=UTC)
    hours, basis = rule
    safe_until = prepared + timedelta(hours=hours)
    remaining = (safe_until - datetime.now(UTC)).total_seconds() / 3600
    return SafetyWindow(
        food_type=food_type,
        storage=storage.lower(),  # type: ignore[arg-type]
        safe_until=safe_until,
        hours_remaining=round(remaining, 2),
        basis=basis,
    ).model_dump(mode="json")


# Idempotency registry for notifications. In production this would be a table
# keyed by dispatch_id; in-process is sufficient for the demo and tests.
_NOTIFICATIONS: dict[str, NotificationReceipt] = {}


def notify_recipient_org(
    dispatch_id: str,
    org_id: str,
    meals: int,
    pickup_deadline: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Send the pickup notification to a recipient organization.

    This is the only tool with a side effect. It is idempotent: calling it twice
    with the same ``dispatch_id`` returns the original receipt with
    ``already_notified=true`` instead of sending again. In this reference
    implementation the channel is ``simulated``; swap in SMS/email/webhook.

    Args:
      dispatch_id: Unique id for this hand-off (from the DispatchOrder).
      org_id: Recipient organization id.
      meals: Servings being sent.
      pickup_deadline: ISO-8601 deadline, if any.
      notes: Handling conditions from the risk review.

    Returns:
      ``NotificationReceipt`` or a ``ToolError`` (unknown org, bad args).

    """
    if not dispatch_id:
        return ToolError(
            error_code="MISSING_DISPATCH_ID",
            message="dispatch_id is required for idempotent delivery.",
            recovery_instruction="Use the dispatch_id from the approved DispatchOrder.",
        ).model_dump()
    if dispatch_id in _NOTIFICATIONS:
        receipt = _NOTIFICATIONS[dispatch_id].model_copy(update={"already_notified": True})
        return receipt.model_dump(mode="json")
    if not any(o.org_id == org_id for o in _load_orgs()):
        return ToolError(
            error_code="ORG_NOT_FOUND",
            message=f"Unknown org_id '{org_id}'.",
            recovery_instruction="Do not retry blindly; return to matching and pick a valid org.",
        ).model_dump()
    receipt = NotificationReceipt(
        dispatch_id=dispatch_id,
        org_id=org_id,
        channel="simulated",
        sent_at=datetime.now(UTC),
    )
    _NOTIFICATIONS[dispatch_id] = receipt
    logger.info(
        "notification.sent",
        extra={
            "dispatch_id": dispatch_id,
            "org_id": org_id,
            "meals": meals,
            "pickup_deadline": pickup_deadline,
            "has_notes": bool(notes),
        },
    )
    return receipt.model_dump(mode="json")


def reset_notification_registry() -> None:
    """Test helper: clear the idempotency registry."""
    _NOTIFICATIONS.clear()
