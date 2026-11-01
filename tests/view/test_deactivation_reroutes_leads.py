"""Deactivating a user must re-route the leads they caretake.

Review Focus #2 says a disabled or resigned user loses operational access and
caretaker authority IMMEDIATELY. The pure function derive_lead_access was
already correct, and the ownership service applied it -- but nothing called
either when an admin actually clicked deactivate on /users, so the lead kept
routing_state=ASSIGNED and access_user_ids naming the resigned account until
some unrelated ownership write happened to touch it.

The operational harm is the one the plan described: the resigned person's leads
never surface in Team Unassigned, so nobody picks them up.

Run via: docker compose exec web python -m pytest tests/view
"""

import pytest
from django.core.management import call_command
from django.urls import reverse

from crm.leads.models import OwnershipSegment
from crm.leads.selectors import team_unassigned_queue
from tests.db.factories import (
    ROLE_EDITOR,
    T0,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


@pytest.fixture
def editor():
    user = make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def caretaker():
    return make_user(email="c@example.com", staff_code="S0001", staff_name="หนึ่ง")


def test_deactivating_a_user_reroutes_their_lead_to_team_unassigned(client, editor, caretaker):
    lead = make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"), caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)
    assert lead.routing_state == "ASSIGNED"

    assert client.login(email=editor.email, password=PASSWORD)
    response = client.post(reverse("accounts:deactivate_user", args=[caretaker.id]))
    assert response.status_code in (302, 303)

    lead.refresh_from_db()
    assert lead.routing_state == "TEAM_UNASSIGNED"
    assert lead.access_user_ids == []


def test_the_rerouted_lead_appears_in_team_unassigned(client, editor, caretaker):
    lead = make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)
    assert client.login(email=editor.email, password=PASSWORD)
    client.post(reverse("accounts:deactivate_user", args=[caretaker.id]))
    assert lead.id in {row.id for row in team_unassigned_queue(editor, limit=50).items}


def test_deactivation_is_audited_against_the_lead(client, editor, caretaker):
    from crm.leads.models import LeadAuditEvent

    lead = make_lead(customer=make_customer(_uid="0000003", staff_code="S0001"), caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)
    assert client.login(email=editor.email, password=PASSWORD)
    client.post(reverse("accounts:deactivate_user", args=[caretaker.id]))
    assert LeadAuditEvent.objects.filter(lead=lead, action="OWNERSHIP_CHANGE").exists()


def test_the_reconcile_command_fixes_drift(editor, caretaker):
    lead = make_lead(customer=make_customer(_uid="0000004", staff_code="S0001"), caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)
    # Simulate drift: the user was disabled without going through the view.
    caretaker.is_active = False
    caretaker.save(update_fields=["is_active"])

    call_command("reconcile_lead_access")

    lead.refresh_from_db()
    assert lead.routing_state == "TEAM_UNASSIGNED"
    assert lead.access_user_ids == []


def test_the_reconcile_command_leaves_active_caretakers_alone(editor, caretaker):
    lead = make_lead(customer=make_customer(_uid="0000005", staff_code="S0001"), caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)
    call_command("reconcile_lead_access")
    lead.refresh_from_db()
    assert lead.caretaker_id == caretaker.id
    assert lead.routing_state == "ASSIGNED"
