"""Unit tests for the deterministic tools and their guided error contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from good_neighbour import tools

KING_BAY = dict(latitude=43.6487, longitude=-79.3817)


def test_find_recipient_orgs_returns_sorted_candidates():
    result = tools.find_recipient_orgs(**KING_BAY, meals=10, perishable=False)
    assert result["status"] == "ok"
    distances = [c["distance_km"] for c in result["candidates"]]
    assert distances == sorted(distances)
    assert all(c["remaining_capacity_meals"] >= 10 for c in result["candidates"])


def test_find_recipient_orgs_perishable_requires_refrigeration():
    result = tools.find_recipient_orgs(**KING_BAY, meals=10, perishable=True, radius_km=10)
    assert result["status"] == "ok"
    assert all(c["has_refrigeration"] for c in result["candidates"])


def test_find_recipient_orgs_rejects_bad_coordinates_with_guidance():
    result = tools.find_recipient_orgs(latitude=999, longitude=0, meals=10, perishable=False)
    assert result["status"] == "error"
    assert result["error_code"]
    assert result["recovery_instruction"]


def test_find_recipient_orgs_no_match_tells_model_what_to_try_next():
    # Ottawa, 1 km radius → nothing in range; the error says how to widen.
    result = tools.find_recipient_orgs(
        latitude=45.4215, longitude=-75.6972, meals=10, perishable=False, radius_km=1
    )
    assert result["status"] == "error"
    assert result["error_code"] == "NO_ORGS_IN_RADIUS"
    assert "radius_km=2" in result["recovery_instruction"]


def test_lookup_donor_site_resolves_alias():
    result = tools.lookup_donor_site("MTCC")
    assert result["status"] == "ok"
    assert "Convention" in result["name"]
    assert -80 < result["location"]["lng"] < -79


def test_lookup_donor_site_unknown_returns_default_hint():
    result = tools.lookup_donor_site("Totally Unknown Place 123")
    assert result["status"] == "error"
    assert result["error_code"] == "SITE_NOT_FOUND"
    assert "location_assumed" in result["recovery_instruction"]


def test_check_org_capacity_flags_over_capacity():
    org = tools.find_recipient_orgs(**KING_BAY, meals=1, perishable=False)["candidates"][0]
    ok = tools.check_org_capacity(org["org_id"], org["remaining_capacity_meals"])
    too_many = tools.check_org_capacity(org["org_id"], org["remaining_capacity_meals"] + 500)
    assert ok["status"] == "ok" and ok["can_accept"] is True
    assert too_many["status"] == "ok" and too_many["can_accept"] is False
    assert "headroom" in too_many["reason"]


def test_check_org_capacity_unknown_org():
    result = tools.check_org_capacity("org-does-not-exist", 5)
    assert result["status"] == "error"
    assert result["error_code"] == "ORG_NOT_FOUND"


def test_estimate_food_safety_window_hot_food_is_short():
    prepared = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    hot = tools.estimate_food_safety_window("hot catered entrees", "hot", prepared)
    ambient = tools.estimate_food_safety_window("bread", "ambient", prepared)
    assert hot["status"] == "ok" and ambient["status"] == "ok"
    assert hot["hours_remaining"] < ambient["hours_remaining"]
    assert 2.5 < hot["hours_remaining"] <= 3.0


def test_estimate_food_safety_window_rejects_bad_timestamp():
    result = tools.estimate_food_safety_window("soup", "hot", "yesterday-ish")
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_TIMESTAMP"
    assert "2026-" in result["recovery_instruction"]


def test_estimate_food_safety_window_rejects_unknown_storage():
    result = tools.estimate_food_safety_window("soup", "lukewarm", "2026-10-01T15:00:00Z")
    assert result["status"] == "error"
    assert result["error_code"] == "UNKNOWN_STORAGE"


def test_notify_recipient_org_is_idempotent():
    org_id = tools.find_recipient_orgs(**KING_BAY, meals=1, perishable=False)["candidates"][0][
        "org_id"
    ]
    first = tools.notify_recipient_org(dispatch_id="dsp-abc", org_id=org_id, meals=10)
    second = tools.notify_recipient_org(dispatch_id="dsp-abc", org_id=org_id, meals=10)
    assert first["status"] == "ok" and first["already_notified"] is False
    assert second["already_notified"] is True
    assert first["sent_at"] == second["sent_at"]


def test_notify_recipient_org_unknown_org_does_not_record():
    result = tools.notify_recipient_org(dispatch_id="dsp-xyz", org_id="org-nope", meals=10)
    assert result["status"] == "error"
    assert result["error_code"] == "ORG_NOT_FOUND"
    assert "dsp-xyz" not in tools._NOTIFICATIONS
