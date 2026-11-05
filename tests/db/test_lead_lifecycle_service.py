"""Stopping, reactivating and closing appointments go through services.

08_SECURITY_AND_AUTH_REQUIREMENTS.md lists "status changes" in the must-audit
set, and the plan's Global Constraints require every mutation to re-check
permission in its service rather than only at the route. The stop/reactivate
views previously assigned the fields inline: no audit row, no actor, and the
lead kept an ACTIVE follow-up cycle plus any ownership lock.

An appointment also had no way to leave SCHEDULED, which made
Lead.next_follow_at permanent -- and since active_appointment is derived from
it, one appointment ever set removed a lead from reallocation review forever.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from crm.followcycles.models import Appointment, FollowupCycle
from crm.followcycles.services import (
    cancel_appointment,
    complete_appointment,
    open_cycle,
    set_appointment,
)
from crm.leads.models import LeadAuditEvent
from crm.leads.selectors import reallocation_review_queue
from crm.leads.services import reactivate_lead, stop_lead
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def lead():
    return make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))


def test_stopping_writes_a_status_change_audit_event(editor, lead):
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    event = LeadAuditEvent.objects.get(lead=lead, action="STATUS_CHANGE")
    assert event.actor_id == editor.id
    assert event.payload["from"] == "NEW"
    assert event.payload["to"] == "STOPPED"
    assert event.payload["stopped_reason"] == "MANUAL_STOP"


def test_stopping_ends_the_active_cycle_with_reason_stopped(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    cycle.refresh_from_db()
    assert cycle.status == "ENDED"
    assert cycle.ended_reason == "STOPPED"
    lead.refresh_from_db()
    assert lead.current_cycle_id is None


def test_stopping_releases_an_ownership_lock(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    cycle.lock_active = True
    cycle.save(update_fields=["lock_active"])
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    cycle.refresh_from_db()
    assert cycle.lock_active is False


def test_an_unknown_stop_reason_is_rejected(editor, lead):
    with pytest.raises(ValueError):
        stop_lead(actor=editor, lead=lead, reason="BECAUSE")


def test_a_telesale_cannot_stop_a_lead_even_through_the_service(lead):
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001")
    with pytest.raises(PermissionDenied):
        stop_lead(actor=telesell, lead=lead, reason="MANUAL_STOP")
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_reactivating_is_audited_and_clears_the_stop_fields(editor, lead):
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    reactivate_lead(actor=editor, lead=lead, reason="customer called back")
    lead.refresh_from_db()
    assert lead.lifecycle_status == "FOLLOWING"
    assert lead.stopped_reason == ""
    assert lead.stopped_at is None
    events = LeadAuditEvent.objects.filter(lead=lead, action="STATUS_CHANGE").order_by("id")
    assert events.last().payload["to"] == "FOLLOWING"
    assert events.last().reason == "customer called back"


def test_reactivating_a_lead_that_is_not_stopped_is_rejected(editor, lead):
    with pytest.raises(ValueError):
        reactivate_lead(actor=editor, lead=lead, reason="x")


def test_completing_an_appointment_clears_next_follow_at(editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    complete_appointment(actor=editor, appointment=appointment)
    appointment.refresh_from_db()
    lead.refresh_from_db()
    assert appointment.status == "DONE"
    assert lead.next_follow_at is None


def test_cancelling_an_appointment_falls_back_to_the_next_scheduled_one(editor, lead):
    soon = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    later = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=5)
    )
    cancel_appointment(actor=editor, appointment=soon)
    lead.refresh_from_db()
    assert lead.next_follow_at == later.scheduled_at
    soon.refresh_from_db()
    assert soon.status == "CANCELLED"


def test_closing_an_already_closed_appointment_is_rejected(editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    complete_appointment(actor=editor, appointment=appointment)
    with pytest.raises(ValueError):
        complete_appointment(actor=editor, appointment=appointment)


def test_a_completed_appointment_stops_shielding_a_lead_from_reallocation(editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    lead = make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0001"),
        caretaker=caretaker,
        last_order_at=timezone.now() - timezone.timedelta(days=200),
    )
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    assert lead.id not in {r.id for r in reallocation_review_queue(editor, limit=50).items}

    complete_appointment(actor=editor, appointment=appointment)
    assert lead.id in {r.id for r in reallocation_review_queue(editor, limit=50).items}


def test_appointment_closure_is_audited(editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    complete_appointment(actor=editor, appointment=appointment)
    assert LeadAuditEvent.objects.filter(lead=lead, action="APPOINTMENT_SET").count() >= 1
    assert Appointment.objects.filter(lead=lead, status="DONE").count() == 1
    assert FollowupCycle.objects.filter(lead=lead).count() >= 0
