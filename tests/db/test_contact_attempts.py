"""Contact attempts are results, never lifecycle changes.

02_LOCKED_BUSINESS_RULES.md: "ติดต่อไม่ได้ is a contact result, not lifecycle
status." A no-answer streak that reaches the team threshold therefore
RECOMMENDS a stop; only an operator who opted the team into autoStop gets an
automatic one. Store actor, time, result, note and cycle for every attempt.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.utils import timezone

from crm.followcycles.models import Appointment, ContactAttempt
from crm.followcycles.services import log_contact_attempt, open_cycle, set_appointment
from crm.leads.models import LeadAuditEvent
from tests.db.factories import ROLE_EDITOR, make_lead, make_team, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def telesell():
    return make_user(email="ts@example.com", staff_code="S0001", staff_name="พนักงานหนึ่ง")


@pytest.fixture
def lead():
    return make_lead()


def test_no_answer_attempts_accumulate_a_streak(telesell, lead):
    outcome = None
    for _ in range(3):
        outcome = log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    assert outcome.no_answer_streak == 3
    lead.refresh_from_db()
    assert lead.no_answer_streak == 3


def test_a_reached_result_resets_the_streak(telesell, lead):
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    outcome = log_contact_attempt(actor=telesell, lead=lead, result="CALLBACK_REQUESTED")
    assert outcome.no_answer_streak == 0
    lead.refresh_from_db()
    assert lead.no_answer_streak == 0


def test_reaching_the_threshold_recommends_but_never_performs_a_stop(telesell):
    team = make_team("CRM_TEAM", policy={"ownershipPolicy": "EXCLUSIVE", "maxNoAnswerAttempts": 2})
    lead = make_lead(team=team)

    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    outcome = log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")

    assert outcome.stop_recommended is True
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"
    assert lead.stopped_reason == ""


def test_below_the_threshold_nothing_is_recommended(telesell):
    team = make_team("CRM_TEAM", policy={"ownershipPolicy": "EXCLUSIVE", "maxNoAnswerAttempts": 3})
    lead = make_lead(team=team)
    assert log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER").stop_recommended is False


def test_a_team_that_opted_into_auto_stop_does_stop_the_lead(telesell):
    team = make_team(
        "CRM_TEAM",
        policy={"ownershipPolicy": "EXCLUSIVE", "maxNoAnswerAttempts": 1, "autoStop": True},
    )
    lead = make_lead(team=team)

    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")

    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"
    assert lead.stopped_reason == "CONTACT_LIMIT_REACHED"
    assert lead.stopped_at is not None


def test_contact_attempt_does_not_change_lifecycle_status_by_default(telesell, lead):
    before = lead.lifecycle_status
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    lead.refresh_from_db()
    assert lead.lifecycle_status == before


def test_attempt_records_actor_time_result_note_and_cycle(editor, telesell, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    attempt = log_contact_attempt(
        actor=telesell, lead=lead, result="BUSY", note="สายไม่ว่าง"
    ).attempt
    assert attempt.user_id == telesell.id
    assert attempt.result == "BUSY"
    assert attempt.note == "สายไม่ว่าง"
    assert attempt.cycle_id == cycle.id
    assert attempt.attempted_at is not None


def test_an_unknown_contact_result_is_rejected(telesell, lead):
    with pytest.raises(ValueError):
        log_contact_attempt(actor=telesell, lead=lead, result="SHOUTED_AT_CLOUD")


def test_every_attempt_is_kept_as_history(telesell, lead):
    for result in ("NO_ANSWER", "BUSY", "ORDERED"):
        log_contact_attempt(actor=telesell, lead=lead, result=result)
    assert ContactAttempt.objects.filter(lead=lead).count() == 3


def test_an_attempt_is_audited(telesell, lead):
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    event = LeadAuditEvent.objects.get(lead=lead, action="CONTACT_ATTEMPT_ADDED")
    assert event.actor_id == telesell.id
    assert event.payload["result"] == "NO_ANSWER"


def test_setting_an_appointment_updates_the_lead_next_follow_at(editor, lead):
    when = timezone.now() + timezone.timedelta(days=2)
    appointment = set_appointment(actor=editor, lead=lead, scheduled_at=when, note="นัดโทร")
    lead.refresh_from_db()
    assert lead.next_follow_at == appointment.scheduled_at
    assert appointment.status == "SCHEDULED"
    assert appointment.timezone_name == "Asia/Bangkok"


def test_setting_an_appointment_is_audited(editor, lead):
    set_appointment(actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1))
    assert LeadAuditEvent.objects.filter(lead=lead, action="APPOINTMENT_SET").exists()


def test_an_appointment_attaches_to_the_active_cycle(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    assert appointment.cycle_id == cycle.id


def test_a_naive_appointment_time_is_rejected(editor, lead):
    from datetime import datetime

    with pytest.raises(ValueError):
        set_appointment(actor=editor, lead=lead, scheduled_at=datetime(2026, 12, 1, 10, 0))


def test_several_appointments_are_kept_and_the_latest_drives_next_follow_at(editor, lead):
    first = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=5)
    )
    second = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timezone.timedelta(days=1)
    )
    assert Appointment.objects.filter(lead=lead).count() == 2
    lead.refresh_from_db()
    # The soonest scheduled appointment is the one the work queue needs.
    assert lead.next_follow_at == second.scheduled_at
    assert first.scheduled_at > second.scheduled_at
