"""The lead action routes (notes, attempts, lifecycle, appointments, reassign).

The panel partial that once rendered these forms on /followup was removed; the
forms now live on the V2 lead detail page. The server checks stay authoritative.

Run via: docker compose exec web python -m pytest tests/view
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import ContactAttempt, Note
from crm.followcycles.services import set_appointment
from crm.leads.models import OwnershipSegment
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    T0,
    make_customer,
    make_lead,
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


def test_a_lead_outside_the_users_scope_renders_no_panel(client, lead):
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0002")
    login(client, telesell)
    body = detail(client, lead.id)
    assert f'id="lead-{lead.id}"' not in body


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
        == 404  # out of scope: no existence oracle
    )


def test_reassigning_through_the_form_opens_a_segment(client, editor, lead):
    from datetime import timedelta

    from crm.teams.models import TeamAssignment

    caretaker = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S0001")
    TeamAssignment.objects.create(
        user=caretaker, team_code=lead.team.code, effective_from=timezone.now() - timedelta(days=1)
    )
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
