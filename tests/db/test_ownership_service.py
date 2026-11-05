"""Ownership writes are server-mediated, atomic and audited.

08_SECURITY_AND_AUTH_REQUIREMENTS.md: "Caretaker/Team changes must be
server-mediated and must update ownership baseline, authorization/read model,
audit, missed-channel semantics." So an ownership change is one transaction
that closes the previous segment, opens a new one, recomputes the access
arrays and writes an audit row — or does none of it.

Review Focus #2 is pinned here at the persistence layer: a deactivated
caretaker loses access and the lead routes to TEAM_UNASSIGNED.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.db.utils import IntegrityError

from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment
from crm.leads.services import apply_order_to_lead, reassign_caretaker
from tests.db.factories import (
    ROLE_EDITOR,
    T0,
    make_customer,
    make_lead,
    make_order,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def actors():
    return {
        "editor": make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001"),
        "u1": make_user(email="u1@example.com", staff_code="S0001", staff_name="พนักงานหนึ่ง"),
        "u2": make_user(email="u2@example.com", staff_code="S0002", staff_name="พนักงานสอง"),
    }


def _latest_order(customer, closer=None, status="DELIVERED"):
    return make_order(customer=customer, closer=closer, ordered_at=T0, order_status=status)


def test_ownership_change_closes_the_previous_segment_and_opens_one(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    segments = list(OwnershipSegment.objects.filter(lead=lead).order_by("started_at", "id"))
    assert [s.caretaker_id for s in segments] == [actors["u1"].id, actors["u2"].id]
    assert segments[0].ended_at is not None
    assert segments[1].ended_at is None


def test_ownership_change_writes_an_audit_event_naming_the_actor(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    event = LeadAuditEvent.objects.get(lead=lead, action="OWNERSHIP_CHANGE")
    assert event.actor_id == actors["editor"].id
    assert event.payload["from"] == str(actors["u1"].id)
    assert event.payload["to"] == str(actors["u2"].id)
    assert event.payload["reason"] == "LATEST_ORDER"


def test_access_arrays_are_recomputed_on_ownership_change(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    lead.refresh_from_db()
    assert lead.access_user_ids == [str(actors["u2"].id)]
    assert lead.access_team_ids == [lead.team.code]


def test_ownership_change_moves_the_baseline_to_the_order_time(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    order = _latest_order(lead.customer, closer=actors["u2"])

    apply_order_to_lead(actor=actors["editor"], lead=lead, order=order, lock_active=False)

    lead.refresh_from_db()
    assert lead.ownership_started_at is not None
    assert OwnershipSegment.objects.get(lead=lead, ended_at__isnull=True).started_at is not None


def test_unchanged_ownership_writes_no_segment_and_no_audit_event(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    before = OwnershipSegment.objects.count()

    apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u1"]),
        lock_active=False,
    )

    assert OwnershipSegment.objects.count() == before
    assert not LeadAuditEvent.objects.filter(lead=lead, action="OWNERSHIP_CHANGE").exists()


def test_exclusive_lead_keeps_its_owner_and_reports_a_non_blocking_conflict(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "EXCLUSIVE"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    decision = apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    assert decision.changed is False
    assert decision.conflict is not None
    assert decision.conflict.blocking is False
    lead.refresh_from_db()
    assert lead.caretaker_id == actors["u1"].id


def test_a_lead_never_has_two_open_segments(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    with pytest.raises(IntegrityError):
        OwnershipSegment.objects.create(lead=lead, caretaker=actors["u2"], started_at=T0)


def test_reassign_caretaker_is_audited_with_its_reason(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=actors["u2"], reason="handover")

    lead.refresh_from_db()
    assert lead.caretaker_id == actors["u2"].id
    event = LeadAuditEvent.objects.filter(lead=lead, action="OWNERSHIP_CHANGE").latest("created_at")
    assert event.reason == "handover"
    assert event.actor_id == actors["editor"].id


def test_reassign_to_none_routes_the_lead_to_team_unassigned(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=None, reason="left the team")

    lead.refresh_from_db()
    assert lead.caretaker_id is None
    assert lead.routing_state == "TEAM_UNASSIGNED"
    assert lead.access_user_ids == []


def test_deactivated_caretaker_routes_the_lead_to_team_unassigned(actors):
    # Review Focus #2 — a disabled account must not keep operational access.
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)
    actors["u1"].is_active = False
    actors["u1"].save()

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=actors["u1"], reason="test")

    lead.refresh_from_db()
    assert lead.routing_state == "TEAM_UNASSIGNED"
    assert lead.access_user_ids == []


def test_a_resigned_closer_cannot_take_ownership_through_an_order(actors):
    lead = make_lead(caretaker=None, policy={"ownershipPolicy": "LATEST_ORDER"})
    actors["u2"].is_active = False
    actors["u2"].save()

    decision = apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    assert decision.changed is False
    assert decision.reason == "NO_ACTIVE_CLOSER"
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_an_active_lock_holds_ownership(actors):
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER_WITH_LOCK"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    decision = apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=True,
    )

    assert decision.reason == "LOCKED"
    lead.refresh_from_db()
    assert lead.caretaker_id == actors["u1"].id


def test_ineligible_order_status_does_not_move_ownership(actors):
    """Superseded rule: apply_order_to_lead drops cancelled, COD-returned and
    unmapped-status orders BEFORE resolve_ownership
    (crm.domain.ownership.is_order_eligible_for_ownership). The missed-channel
    rule keeps its own exclusions in crm/domain/missed_channel.py.
    """
    lead = make_lead(caretaker=actors["u1"], policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    decision = apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        # Any status at all, including one outside the prototype's vocabulary:
        # this repo's crm_order.order_status is free-text Thai.
        order=_latest_order(lead.customer, closer=actors["u2"], status="ยกเลิก"),
        lock_active=False,
    )

    assert decision.changed is False
    lead.refresh_from_db()
    assert lead.caretaker_id == actors["u1"].id
    assert not OwnershipSegment.objects.filter(lead=lead, caretaker=actors["u2"]).exists()


def test_assigning_a_caretaker_sets_routing_to_assigned(actors):
    lead = make_lead(caretaker=None, policy={"ownershipPolicy": "LATEST_ORDER"})

    apply_order_to_lead(
        actor=actors["editor"],
        lead=lead,
        order=_latest_order(lead.customer, closer=actors["u2"]),
        lock_active=False,
    )

    lead.refresh_from_db()
    assert lead.caretaker_id == actors["u2"].id
    assert lead.routing_state == "ASSIGNED"
    assert lead.access_user_ids == [str(actors["u2"].id)]


def test_a_team_without_a_lead_escalates_an_unassigned_lead_to_admin(actors):
    team = make_team("UPSELL_TEAM", policy={"ownershipPolicy": "EXCLUSIVE", "routing": {"teamLeadConfigured": False}})
    lead = make_lead(customer=make_customer(_uid="0000099"), team=team, caretaker=None)

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=None, reason="none available")

    lead.refresh_from_db()
    assert lead.routing_state == "TEAM_UNASSIGNED"
    event = LeadAuditEvent.objects.filter(lead=lead).latest("created_at")
    assert event.payload["escalated_to_admin"] is True


def test_segments_never_overlap_across_several_reassignments(actors):
    lead = make_lead(caretaker=actors["u1"])
    OwnershipSegment.objects.create(lead=lead, caretaker=actors["u1"], started_at=T0)

    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=actors["u2"], reason="a")
    reassign_caretaker(actor=actors["editor"], lead=lead, caretaker=actors["u1"], reason="b")

    segments = list(OwnershipSegment.objects.filter(lead=lead).order_by("started_at", "id"))
    assert len(segments) == 3
    assert [s.ended_at is None for s in segments] == [False, False, True]
    for earlier, later in zip(segments, segments[1:]):
        assert earlier.ended_at <= later.started_at
