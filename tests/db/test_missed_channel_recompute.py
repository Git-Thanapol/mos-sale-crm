"""Recomputing missed-channel from this repo's real Order rows.

The adapter's job is the part the pure function cannot do: map
crm_order.order_status (free-text Thai: 'สำเร็จ' and blank are the only values
in the live data) onto the prototype's machine codes, and resolve the closer
from staff_code.

D023 is pinned here: for HX63-migrated leads, counting starts at the snapshot
baseline 2026-10-13T12:31:55Z and earlier orders are never retroactively
counted.

Run via: docker compose exec web python -m pytest tests/db
"""

from datetime import datetime, timedelta, timezone

import pytest

from crm.leads.models import OwnershipSegment
from crm.leads.order_status import HX63_SNAPSHOT_BASELINE, to_machine_status
from crm.leads.recompute import recompute_lead_missed_channel
from crm.leads.services import reassign_caretaker
from tests.db.factories import ROLE_EDITOR, T0, make_lead, make_order, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]

T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)


@pytest.fixture
def actors():
    return {
        "editor": make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001"),
        "u1": make_user(email="u1@example.com", staff_code="S0001", staff_name="หนึ่ง"),
        "u2": make_user(email="u2@example.com", staff_code="S0002", staff_name="สอง"),
    }


def test_the_live_status_vocabulary_maps_to_machine_codes():
    # These are the only two values present in crm_order.order_status.
    assert to_machine_status("สำเร็จ") == "DELIVERED"
    assert to_machine_status("") == "PENDING_CONFIRMATION"
    assert to_machine_status(None) == "PENDING_CONFIRMATION"


def test_an_unmapped_status_is_reported_rather_than_guessed():
    assert to_machine_status("สถานะที่ไม่รู้จัก") is None


def test_recompute_counts_a_marketplace_order_against_the_caretaker(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    assert recompute_lead_missed_channel(lead=lead) == 1


def test_recompute_is_idempotent(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    first = recompute_lead_missed_channel(lead=lead)
    assert recompute_lead_missed_channel(lead=lead) == first


def test_recompute_writes_the_count_onto_the_lead(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    recompute_lead_missed_channel(lead=lead)
    lead.refresh_from_db()
    assert lead.missed_channel_count == 1


def test_an_order_closed_by_the_caretaker_resets_the_count(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    make_order(customer=lead.customer, closer=actors["u1"], ordered_at=T2)
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_caretaker_change_resets_the_baseline_so_earlier_orders_stop_counting(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    assert recompute_lead_missed_channel(lead=lead) == 1

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=actors["u2"], reason="handover")
    lead.refresh_from_db()
    # The new ownership segment starts now, so the older order is before the
    # baseline and no longer counts.
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_hx63_baseline_suppresses_pre_snapshot_history(actors):
    # D023
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(
        lead=lead, caretaker=actors["u1"], started_at=datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    make_order(
        customer=lead.customer, closer=None, ordered_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assert recompute_lead_missed_channel(lead=lead) == 1
    assert recompute_lead_missed_channel(lead=lead, hx63_baseline=HX63_SNAPSHOT_BASELINE) == 0


def test_the_hx63_constant_is_the_documented_snapshot_instant():
    assert HX63_SNAPSHOT_BASELINE == datetime(2026, 10, 13, 12, 31, 55, tzinfo=timezone.utc)


def test_a_lead_with_no_orders_counts_zero(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_a_lead_with_no_ownership_segment_uses_its_creation_as_the_baseline(actors):
    # Review Focus #5: a lead with no history must not count everything ever.
    lead = make_lead(caretaker=None)
    make_order(customer=lead.customer, closer=None, ordered_at=T0 - timedelta(days=365))
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_an_unmapped_order_status_is_excluded_and_does_not_raise(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1, order_status="ไม่รู้จัก")
    # The page must still render. The order is excluded, not counted and not
    # silently treated as a valid sale.
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_orders_of_other_customers_are_not_counted(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(closer=None, ordered_at=T1)  # a different customer
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_a_team_that_disables_the_policy_counts_zero(actors):
    lead = make_lead(
        caretaker=actors["u1"],
        policy={"ownershipPolicy": "EXCLUSIVE", "missedChannel": {"enabled": False}},
    )
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_migrated_baseline_survives_a_plain_recompute_for_backfill_segment(actors):
    lead = make_lead(caretaker=actors["u1"])
    lead.migrated_baseline_at = HX63_SNAPSHOT_BASELINE
    lead.save(update_fields=["migrated_baseline_at"])
    seg_start = HX63_SNAPSHOT_BASELINE + timedelta(days=10)
    OwnershipSegment.objects.create(
        lead=lead, caretaker=actors["u1"], started_at=seg_start, reason="BACKFILL"
    )
    # Strictly between the HX63 baseline day and the segment start.
    make_order(customer=lead.customer, closer=None, ordered_at=HX63_SNAPSHOT_BASELINE + timedelta(days=3))
    assert recompute_lead_missed_channel(lead=lead) == 1


def test_a_later_reassignment_segment_still_resets_the_baseline(actors):
    lead = make_lead(caretaker=actors["u1"])
    lead.migrated_baseline_at = HX63_SNAPSHOT_BASELINE
    lead.save(update_fields=["migrated_baseline_at"])
    seg_start = HX63_SNAPSHOT_BASELINE + timedelta(days=10)
    OwnershipSegment.objects.create(
        lead=lead, caretaker=actors["u1"], started_at=seg_start, reason="REASSIGN"
    )
    make_order(customer=lead.customer, closer=None, ordered_at=HX63_SNAPSHOT_BASELINE + timedelta(days=3))
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_same_bangkok_day_as_midday_baseline_is_not_counted(actors):
    # Date-only orders: an order on the baseline's own Bangkok day (19:31 local)
    # cannot be proven to be after it, so it is excluded conservatively.
    lead = make_lead(caretaker=actors["u1"])
    lead.migrated_baseline_at = HX63_SNAPSHOT_BASELINE
    lead.save(update_fields=["migrated_baseline_at"])
    OwnershipSegment.objects.create(
        lead=lead, caretaker=actors["u1"], started_at=HX63_SNAPSHOT_BASELINE + timedelta(days=10),
        reason="BACKFILL",
    )
    make_order(customer=lead.customer, closer=None, ordered_at=HX63_SNAPSHOT_BASELINE)
    assert recompute_lead_missed_channel(lead=lead) == 0
