"""Last-order-age buckets and reallocation candidacy.

02_LOCKED_BUSINESS_RULES.md fixes the bucket set and states the rule that
matters most: "Lead should be reviewed for reallocation" is a review/filter
queue only. It must NOT auto-reassign ownership.

Review Focus #1 (Bangkok day boundary) and #5 (lead with no orders) are both
pinned here.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from crm.domain.bangkok import BANGKOK_TZ
from crm.domain.last_order_age import (
    LAST_ORDER_AGE_PRESETS,
    REALLOCATION_REVIEW_DEFAULT_DAYS,
    ProtectionSummary,
    is_reallocation_review_candidate,
    last_order_age_cutoffs,
    matches_last_order_age,
    resolve_last_order_age_filter,
)
from tests.unit.test_parity_fixtures import load_cases

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=BANGKOK_TZ)
NO_PROTECTION = ProtectionSummary(
    active_appointment=False,
    active_followup_cycle=False,
    ownership_lock_active=False,
    next_follow_at=None,
)


def _dt(millis: int | None) -> datetime | None:
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def build_input(raw: dict) -> dict:
    return {
        "last_order_at": _dt(raw["lastOrderAt"]),
        "preset": raw["preset"],
        "min_days": raw["minDays"],
        "max_days": raw["maxDays"],
        "now": _dt(raw["now"]),
    }


@pytest.mark.parametrize("case", load_cases("last_order_age"), ids=lambda c: c["name"])
def test_last_order_age_matches_prototype(case):
    assert matches_last_order_age(**build_input(case["input"])) is case["expected"]["matches"]


def test_preset_ids_and_labels_are_the_locked_set():
    assert [p.id for p in LAST_ORDER_AGE_PRESETS] == [
        "0_7",
        "8_15",
        "16_30",
        "31_45",
        "46_60",
        "61_90",
        "OVER_90",
        "NO_ORDER",
    ]
    assert LAST_ORDER_AGE_PRESETS[-1].label == "ไม่มีออเดอร์เลย"
    assert LAST_ORDER_AGE_PRESETS[0].label == "0–7 วัน"


def test_the_default_review_threshold_is_forty_five_days():
    assert REALLOCATION_REVIEW_DEFAULT_DAYS == 45


@pytest.mark.parametrize(
    "days,preset",
    [(0, "0_7"), (7, "0_7"), (8, "8_15"), (15, "8_15"), (16, "16_30"), (30, "16_30"), (91, "OVER_90")],
)
def test_bucket_boundaries_are_inclusive_at_both_ends(days, preset):
    assert matches_last_order_age(NOW - timedelta(days=days), preset, None, None, NOW) is True


@pytest.mark.parametrize("days,preset", [(8, "0_7"), (7, "8_15"), (16, "8_15"), (90, "OVER_90")])
def test_a_day_outside_the_bucket_does_not_match(days, preset):
    assert matches_last_order_age(NOW - timedelta(days=days), preset, None, None, NOW) is False


def test_late_evening_order_lands_in_the_zero_to_seven_bucket():
    # Review Focus #1 — the UTC 17:00-24:00 window is already tomorrow in
    # Bangkok, which is where a timedelta/86400 implementation drifts a day.
    ordered = datetime(2026, 10, 31, 23, 45, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 11, 1, 0, 15, tzinfo=BANGKOK_TZ)
    assert matches_last_order_age(ordered, "0_7", None, None, now) is True
    assert matches_last_order_age(ordered, "8_15", None, None, now) is False


def test_no_order_bucket_matches_only_a_null_last_order():
    # Review Focus #5
    assert matches_last_order_age(None, "NO_ORDER", None, None, NOW) is True
    assert matches_last_order_age(NOW - timedelta(days=5), "NO_ORDER", None, None, NOW) is False


@pytest.mark.parametrize("preset", ["0_7", "31_45", "OVER_90"])
def test_a_null_last_order_never_satisfies_a_numeric_bucket(preset):
    assert matches_last_order_age(None, preset, None, None, NOW) is False


def test_no_filter_matches_everything_including_a_lead_with_no_order():
    assert matches_last_order_age(None, None, None, None, NOW) is True
    assert matches_last_order_age(NOW - timedelta(days=500), None, None, None, NOW) is True


def test_no_order_cannot_be_combined_with_a_numeric_range():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter("NO_ORDER", 10, 20)


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter("LAST_CENTURY", None, None)


def test_min_days_above_max_days_raises():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter(None, 40, 10)


@pytest.mark.parametrize("bad", [-1, 36_501, 1.5, True])
def test_a_nonsensical_day_count_raises(bad):
    with pytest.raises(ValueError):
        resolve_last_order_age_filter(None, bad, None)


def test_no_filter_resolves_to_none():
    assert resolve_last_order_age_filter(None, None, None) is None
    assert last_order_age_cutoffs(None, None, None, NOW) is None


def test_cutoffs_are_bangkok_day_boundaries_with_an_exclusive_upper_bound():
    cutoffs = last_order_age_cutoffs("8_15", None, None, NOW)
    assert cutoffs.ordered_at_from.astimezone(BANGKOK_TZ).hour == 0
    assert cutoffs.ordered_at_before.astimezone(BANGKOK_TZ).hour == 0
    # 8..15 days ago inclusive => [now-15d 00:00, now-7d 00:00)
    assert cutoffs.ordered_at_from == (NOW - timedelta(days=15)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert cutoffs.ordered_at_before == (NOW - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def test_an_open_ended_bucket_has_no_lower_timestamp_bound():
    cutoffs = last_order_age_cutoffs("OVER_90", None, None, NOW)
    assert cutoffs.ordered_at_from is None
    assert cutoffs.ordered_at_before is not None


def test_the_no_order_bucket_produces_no_timestamp_bounds():
    cutoffs = last_order_age_cutoffs("NO_ORDER", None, None, NOW)
    assert cutoffs.no_order is True
    assert cutoffs.ordered_at_from is None
    assert cutoffs.ordered_at_before is None


@pytest.mark.parametrize(
    "days,expected", [(9, False), (10, True), (15, True), (20, True), (21, False)]
)
def test_a_custom_range_is_inclusive_at_both_ends(days, expected):
    assert matches_last_order_age(NOW - timedelta(days=days), None, 10, 20, NOW) is expected


def test_lead_with_no_order_is_never_a_reallocation_candidate():
    # Review Focus #5
    assert (
        is_reallocation_review_candidate(
            last_order_at=None,
            caretaker_user_id="u1",
            lifecycle_status="FOLLOWING",
            protection=NO_PROTECTION,
            now=NOW,
            threshold_days=45,
        )
        is False
    )


def test_stopped_lead_is_never_a_reallocation_candidate():
    assert (
        is_reallocation_review_candidate(
            last_order_at=NOW - timedelta(days=200),
            caretaker_user_id="u1",
            lifecycle_status="STOPPED",
            protection=NO_PROTECTION,
            now=NOW,
            threshold_days=45,
        )
        is False
    )


def test_a_lead_with_no_caretaker_is_never_a_reallocation_candidate():
    # There is nothing to reallocate away from; it belongs to the unassigned
    # queue instead.
    assert (
        is_reallocation_review_candidate(
            last_order_at=NOW - timedelta(days=200),
            caretaker_user_id=None,
            lifecycle_status="FOLLOWING",
            protection=NO_PROTECTION,
            now=NOW,
            threshold_days=45,
        )
        is False
    )


@pytest.mark.parametrize(
    "field", ["active_appointment", "active_followup_cycle", "ownership_lock_active"]
)
def test_any_active_protection_excludes_a_lead_from_review(field):
    protection = replace(NO_PROTECTION, **{field: True})
    assert (
        is_reallocation_review_candidate(
            last_order_at=NOW - timedelta(days=200),
            caretaker_user_id="u1",
            lifecycle_status="FOLLOWING",
            protection=protection,
            now=NOW,
            threshold_days=45,
        )
        is False
    )


def test_an_unprotected_stale_lead_with_a_caretaker_is_a_candidate():
    assert (
        is_reallocation_review_candidate(
            last_order_at=NOW - timedelta(days=200),
            caretaker_user_id="u1",
            lifecycle_status="FOLLOWING",
            protection=NO_PROTECTION,
            now=NOW,
            threshold_days=45,
        )
        is True
    )


@pytest.mark.parametrize("days,expected", [(44, False), (45, True), (46, True)])
def test_the_threshold_is_inclusive(days, expected):
    assert (
        is_reallocation_review_candidate(
            last_order_at=NOW - timedelta(days=days),
            caretaker_user_id="u1",
            lifecycle_status="FOLLOWING",
            protection=NO_PROTECTION,
            now=NOW,
            threshold_days=45,
        )
        is expected
    )
