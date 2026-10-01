"""Shared fixtures: deterministic stand-ins for the three LLM specialists.

Every test runs offline. The stubs honour the same input/output contracts as
the real agents (a ``DonationTriage`` dict in, a ``MatchProposal`` /
``RiskAssessment`` dict out), so the graph edges under test are the real ones.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

# Keep tests hermetic: no API key, console tracing off, AI Studio mode.
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "FALSE")
os.environ["GN_TRACE_EXPORTER"] = "none"
os.environ["GN_LOG_LEVEL"] = "WARNING"

from google.adk import Event  # noqa: E402
from google.genai import types  # noqa: E402

from good_neighbour import tools  # noqa: E402
from good_neighbour.schemas import GeoPoint  # noqa: E402

KING_AND_BAY = GeoPoint(lat=43.6487, lng=-79.3817)


def make_triage(**overrides: Any) -> dict[str, Any]:
    """A low-risk triage record (bread, ambient, 12 meals) unless overridden."""
    base: dict[str, Any] = {
        "donation_id": "don-test0001",
        "food_type": "bread and pastries",
        "meals_estimate": 12,
        "perishable": False,
        "storage": "ambient",
        "prepared_at": None,
        "pickup_deadline": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        "pickup_location": KING_AND_BAY.model_dump(),
        "pickup_site_name": "Test Bakery",
        "location_assumed": False,
        "allergens": ["gluten"],
        "notes": None,
    }
    base.update(overrides)
    return base


def stub_triage_factory(triage: dict[str, Any]):
    """Return a function node that ignores its input and emits ``triage``."""

    def triage_stub(node_input: dict):
        yield Event(output=triage, state={"triage": triage})

    return triage_stub


def stub_matcher(node_input: Any):
    """Pick the nearest org via the real tool; never calls a model."""
    triage = (
        node_input["triage"]
        if isinstance(node_input, dict) and "triage" in node_input
        else node_input
    )
    loc = triage["pickup_location"]
    found = tools.find_recipient_orgs(
        latitude=loc["lat"],
        longitude=loc["lng"],
        meals=triage["meals_estimate"],
        perishable=triage["perishable"],
        radius_km=10,
    )
    top = found["candidates"][0]
    proposal = {
        "donation_id": triage["donation_id"],
        "org_id": top["org_id"],
        "org_name": top["name"],
        "distance_km": top["distance_km"],
        "rationale": "stub: nearest org with capacity",
        "alternatives": [c["org_id"] for c in found["candidates"][1:3]],
    }
    yield Event(output=proposal, state={"match_proposal": proposal})


def stub_risk_factory(level: str = "medium"):
    """Return a risk-reviewer node that always reports ``level``."""

    def risk_stub(node_input: Any):
        match = node_input
        assessment = {
            "donation_id": match["donation_id"],
            "risk_level": level,
            "hazards": ["time/temperature abuse"] if level != "low" else [],
            "safe_until": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "recommendation": "do_not_dispatch"
            if level == "unsafe"
            else "dispatch_with_conditions",
            "conditions": ["Keep hot-held above 60°C", "Transport within 30 minutes"],
            "summary": f"stub assessment: {level}",
        }
        yield Event(output=assessment, state={"risk_assessment": assessment})

    return risk_stub


@pytest.fixture(autouse=True)
def _reset_notifications():
    tools.reset_notification_registry()
    yield
    tools.reset_notification_registry()


def user_message(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])
