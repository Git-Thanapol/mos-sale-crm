"""The lead detail panel and its action forms.

C2 from review: Task 15 was reported complete without `_lead_detail.html`,
`leads:set_lifecycle` or a `lead_detail` context key, so the row's
"รายละเอียด" link was dead and the six mutation endpoints were reachable only
by a hand-crafted POST. 06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md section A
requires Lead detail, Notes, Contact Attempts, Appointments, STOP/Reactivate
and caretaker reassignment as functional acceptance.

The panel renders a form only when the matching predicate passes, and the
server check stays authoritative either way.

Run via: docker compose exec web python -m pytest tests/view
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import Appointment, ContactAttempt, Note
from crm.followcycles.services import open_cycle, set_appointment
from crm.leads.models import OwnershipSegment
from crm.teams.models import TeamAssignment
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    T0,
    make_customer,
    make_lead,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def lead():
    return make_lead(customer=make_customer(_uid="0000001", staff_code="S0001", customer_name="สมชาย"))


def detail(client, lead_id):
    return client.get(reverse("followups:list"), {"lead_id": lead_id}).content.decode()


def test_the_detail_panel_renders_for_the_requested_lead(client, editor, lead):
    login(client, editor)
    body = detail(client, lead.id)
    assert f'id="lead-{lead.id}"' in body
    assert "สมชาย" in body


def test_the_panel_shows_every_action_form_for_an_editor(client, editor, lead):
    login(client, editor)
    body = detail(client, lead.id)
    for action in (
        reverse("leads:add_note", args=[lead.id]),
        reverse("leads:log_attempt", args=[lead.id]),
        reverse("leads:set_appointment", args=[lead.id]),
        reverse("leads:set_lifecycle", args=[lead.id]),
        reverse("leads:reassign", args=[lead.id]),
        reverse("leads:stop", args=[lead.id]),
    ):
        assert action in body, action


def test_a_stopped_lead_offers_reactivate_instead_of_stop(client, editor):
    stopped = make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0001"),
        lifecycle_status="STOPPED",
        stopped_reason="MANUAL_STOP",
    )
    login(client, editor)
    body = detail(client, stopped.id)
    assert reverse("leads:reactivate", args=[stopped.id]) in body
    assert reverse("leads:stop", args=[stopped.id]) not in body


def test_the_row_link_opens_the_panel(client, editor, lead):
    login(client, editor)
    listing = client.get(reverse("followups:list")).content.decode()
    assert f"lead_id={lead.id}" in listing


def test_a_lead_outside_the_users_scope_renders_no_panel(client, lead):
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0002")
    login(client, telesell)
    body = detail(client, lead.id)
    assert f'id="lead-{lead.id}"' not in body


def test_a_telesale_sees_edit_forms_but_no_ownership_forms(client, lead):
    telesell = make_user(email="ts2@example.com", role=ROLE_TELESELL, staff_code="S0001")
    make_team("CRM_TEAM")
    TeamAssignment.objects.create(
        user=telesell, team_code="CRM_TEAM", effective_from=timezone.now()
    )
    login(client, telesell)
    body = detail(client, lead.id)
    assert reverse("leads:add_note", args=[lead.id]) in body
    assert reverse("leads:reassign", args=[lead.id]) not in body
    assert reverse("leads:stop", args=[lead.id]) not in body


def test_adding_a_note_through_the_form_returns_to_the_same_view(client, editor, lead):
    login(client, editor)
    target = f"{reverse('followups:list')}?lead_id={lead.id}"
    response = client.post(
        reverse("leads:add_note", args=[lead.id]), {"body": "คุยแล้ว", "next": target}
    )
    assert response["Location"] == target
    assert Note.objects.filter(lead=lead, body="คุยแล้ว").exists()


def test_logging_an_attempt_through_the_form_records_it(client, editor, lead):
    login(client, editor)
    client.post(reverse("leads:log_attempt", args=[lead.id]), {"result": "BUSY", "note": "x"})
    assert ContactAttempt.objects.filter(lead=lead, result="BUSY").exists()


def test_setting_the_lifecycle_status_through_the_form(client, editor, lead):
    login(client, editor)
    response = client.post(
        reverse("leads:set_lifecycle", args=[lead.id]), {"lifecycle_status": "WAITING_CUSTOMER"}
    )
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.lifecycle_status == "WAITING_CUSTOMER"


def test_stopped_cannot_be_set_through_the_lifecycle_route(client, editor, lead):
    # STOPPED carries a reason and ends the cycle, so it belongs to leads:stop.
    login(client, editor)
    assert (
        client.post(
            reverse("leads:set_lifecycle", args=[lead.id]), {"lifecycle_status": "STOPPED"}
        ).status_code
        == 400
    )
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_an_unknown_lifecycle_status_is_rejected(client, editor, lead):
    login(client, editor)
    assert (
        client.post(
            reverse("leads:set_lifecycle", args=[lead.id]), {"lifecycle_status": "LOITERING"}
        ).status_code
        == 400
    )


def test_a_scheduled_appointment_is_listed_with_close_buttons(client, editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=1)
    )
    login(client, editor)
    body = detail(client, lead.id)
    assert reverse("leads:close_appointment", args=[appointment.id]) in body


def test_closing_an_appointment_through_the_form_clears_next_follow_at(client, editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=1)
    )
    login(client, editor)
    response = client.post(
        reverse("leads:close_appointment", args=[appointment.id]), {"status": "DONE"}
    )
    assert response.status_code in (302, 303)
    appointment.refresh_from_db()
    lead.refresh_from_db()
    assert appointment.status == "DONE"
    assert lead.next_follow_at is None


def test_an_invalid_appointment_status_is_rejected(client, editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=1)
    )
    login(client, editor)
    assert (
        client.post(
            reverse("leads:close_appointment", args=[appointment.id]), {"status": "WHATEVER"}
        ).status_code
        == 400
    )


def test_a_telesale_cannot_close_another_scope_appointment(client, editor, lead):
    appointment = set_appointment(
        actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=1)
    )
    outsider = make_user(email="out@example.com", role=ROLE_TELESELL, staff_code="S0009")
    login(client, outsider)
    assert (
        client.post(
            reverse("leads:close_appointment", args=[appointment.id]), {"status": "DONE"}
        ).status_code
        == 403
    )


def test_the_panel_lists_cycle_history(client, editor, lead):
    open_cycle(actor=editor, lead=lead)
    open_cycle(actor=editor, lead=lead)
    login(client, editor)
    body = detail(client, lead.id)
    assert "ประวัติรอบติดตาม" in body
    assert "ENDED" in body


def test_reassigning_through_the_form_opens_a_segment(client, editor, lead):
    caretaker = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S0001")
    OwnershipSegment.objects.create(lead=lead, caretaker=None, started_at=T0)
    login(client, editor)
    response = client.post(
        reverse("leads:reassign", args=[lead.id]), {"caretaker": caretaker.id, "reason": "handover"}
    )
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.caretaker_id == caretaker.id
    assert OwnershipSegment.objects.filter(lead=lead, ended_at__isnull=True).count() == 1


def test_no_lead_id_renders_no_panel(client, editor, lead):
    login(client, editor)
    body = client.get(reverse("followups:list")).content.decode()
    assert f'id="lead-{lead.id}"' not in body


def test_a_nonexistent_lead_id_does_not_error(client, editor):
    login(client, editor)
    assert client.get(reverse("followups:list"), {"lead_id": "999999"}).status_code == 200


def test_a_nonnumeric_lead_id_does_not_error(client, editor):
    login(client, editor)
    assert client.get(reverse("followups:list"), {"lead_id": "abc"}).status_code == 200
