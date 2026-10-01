"""Pydantic schemas shared by tools, nodes, agents, and the HITL gate.

Every boundary in the workflow — tool arguments, tool results, LLM structured
outputs, the human-approval payload — is typed here so the LLM is constrained
and the next node receives validated data rather than prose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StorageCondition = Literal["hot", "cold", "frozen", "ambient"]
RiskLevel = Literal["low", "medium", "high", "unsafe"]
ApprovalChoice = Literal["approve", "revise", "reject"]


class StrictModel(BaseModel):
    """Base model: forbid unknown fields so the LLM cannot smuggle extras."""

    model_config = ConfigDict(extra="forbid")


# --- Tool error contract -----------------------------------------------------


class ToolError(StrictModel):
    """Structured failure returned by every tool instead of raising.

    The ``recovery_instruction`` tells the model what to do next, which keeps
    the agent loop moving instead of crashing the turn.
    """

    status: Literal["error"] = "error"
    error_code: str = Field(description="Stable machine-readable code, e.g. NO_ORGS_IN_RADIUS.")
    message: str = Field(description="Human-readable explanation of what went wrong.")
    recovery_instruction: str = Field(
        description="Concrete next step the agent should take to recover."
    )


# --- Geography ---------------------------------------------------------------


class GeoPoint(StrictModel):
    """WGS-84 coordinate."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# --- Intake ------------------------------------------------------------------


class DonationSubmission(StrictModel):
    """Raw intake payload. Free-text chat is wrapped into ``description``."""

    donation_id: str | None = Field(default=None, description="Caller-supplied id, if any.")
    donor_name: str | None = None
    donor_site: str | None = Field(
        default=None, description="Known site name or address, e.g. 'MTCC South Building'."
    )
    description: str = Field(description="What is being donated, in the donor's own words.")
    meals_estimate: int | None = Field(default=None, ge=0)
    storage: StorageCondition | None = None
    prepared_at: datetime | None = None
    pickup_deadline: datetime | None = None
    location: GeoPoint | None = None


class ScreenedSubmission(StrictModel):
    """Output of the pre-LLM security screen."""

    donation_id: str
    redacted_text: str = Field(description="Submission text with PII replaced by placeholders.")
    submission: DonationSubmission | None = Field(
        default=None, description="Structured fields if the input was JSON."
    )
    redactions: list[str] = Field(default_factory=list, description="PII categories removed.")
    injection_findings: list[str] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None


# --- Triage (LLM structured output) -----------------------------------------


class DonationTriage(StrictModel):
    """Normalized, machine-actionable view of one donation."""

    donation_id: str
    food_type: str = Field(description="Short category, e.g. 'hot catered entrees', 'bread'.")
    meals_estimate: int = Field(ge=0, description="Best estimate of individual servings.")
    perishable: bool = Field(description="True if the food requires temperature control.")
    storage: StorageCondition
    prepared_at: datetime | None = Field(
        default=None, description="ISO-8601 time the food was prepared, if known."
    )
    pickup_deadline: datetime | None = None
    pickup_location: GeoPoint
    pickup_site_name: str | None = None
    location_assumed: bool = Field(
        default=False, description="True if the location defaulted to the downtown centroid."
    )
    allergens: list[str] = Field(default_factory=list)
    notes: str | None = None


# --- Tools -------------------------------------------------------------------


class RecipientSearch(StrictModel):
    """Arguments for :func:`good_neighbour.tools.find_recipient_orgs`."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    meals: int = Field(ge=1)
    perishable: bool
    radius_km: float = Field(default=5.0, gt=0, le=25)


class RecipientOrg(StrictModel):
    """One recipient organization from the roster."""

    org_id: str
    name: str
    neighbourhood: str
    org_type: str
    location: GeoPoint
    daily_capacity_meals: int = Field(ge=0)
    remaining_capacity_meals: int = Field(ge=0)
    has_refrigeration: bool
    accepts_prepared_food: bool
    distance_km: float | None = None


class RecipientSearchResult(StrictModel):
    """Ranked candidate recipients."""

    status: Literal["ok"] = "ok"
    candidates: list[RecipientOrg]
    radius_km: float


class CapacityResult(StrictModel):
    """Capacity check for a single org."""

    status: Literal["ok"] = "ok"
    org_id: str
    name: str
    can_accept: bool
    remaining_capacity_meals: int
    reason: str


class SafetyWindow(StrictModel):
    """Food-safety holding window estimate."""

    status: Literal["ok"] = "ok"
    food_type: str
    storage: StorageCondition
    safe_until: datetime
    hours_remaining: float
    basis: str = Field(description="Rule applied, e.g. 'hot-held food: 4h from preparation'.")


class DonorSite(StrictModel):
    """Known donor location resolved from a name or address."""

    status: Literal["ok"] = "ok"
    site_id: str
    name: str
    address: str
    location: GeoPoint
    site_type: str


class NotificationReceipt(StrictModel):
    """Result of notifying a recipient org (idempotent on dispatch_id)."""

    status: Literal["ok"] = "ok"
    dispatch_id: str
    org_id: str
    channel: Literal["sms", "email", "webhook", "simulated"]
    sent_at: datetime
    already_notified: bool = False


# --- Matching & review (LLM structured outputs) -------------------------------


class MatchProposal(StrictModel):
    """Matcher agent's recommendation."""

    donation_id: str
    org_id: str
    org_name: str
    distance_km: float = Field(ge=0)
    rationale: str
    alternatives: list[str] = Field(
        default_factory=list, description="Other viable org_ids, best first."
    )


class RiskAssessment(StrictModel):
    """Risk reviewer agent's verdict."""

    donation_id: str
    risk_level: RiskLevel
    hazards: list[str] = Field(default_factory=list)
    safe_until: datetime | None = None
    recommendation: Literal["dispatch", "dispatch_with_conditions", "do_not_dispatch"]
    conditions: list[str] = Field(default_factory=list)
    summary: str


# --- Human-in-the-loop -------------------------------------------------------


class ApprovalDecision(StrictModel):
    """Coordinator's response to the approval request."""

    decision: ApprovalChoice
    note: str | None = Field(default=None, description="Why, or what to change on 'revise'.")


class ApprovalRequestPayload(StrictModel):
    """Everything the coordinator needs to decide, in one object."""

    donation: DonationTriage
    match: MatchProposal
    risk: RiskAssessment


# --- Dispatch ----------------------------------------------------------------


class DispatchOrder(StrictModel):
    """Approved hand-off instruction."""

    dispatch_id: str
    donation_id: str
    org_id: str
    org_name: str
    meals: int
    pickup_location: GeoPoint
    pickup_deadline: datetime | None = None
    path: Literal["auto", "approved"]
    conditions: list[str] = Field(default_factory=list)
    approved_by: str | None = None


class RejectionNotice(StrictModel):
    """Terminal output when a submission is blocked or declined."""

    donation_id: str
    reason: str
    stage: Literal["screen", "coordinator"]
