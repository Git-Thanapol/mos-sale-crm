"""Review Focus #4, for the role it actually names.

The plan's Review Focus #4 is about a พนักงาน (telesale), and
06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md section B lists "Telesale cannot
access another Team's protected Lead" as an acceptance example.

The hole this file closes: ownership in the legacy schema is per-CUSTOMER
(Customer.staff_code), and one customer can hold a Lead in several teams. A
predicate that only compares staff_code therefore matches every one of that
customer's leads, across every team.

A telesale may reach a lead when either is true:
  - they are its caretaker (it is their own work), or
  - the lead's team is one of their teams AND the customer is theirs.

Caretaker is kept as a path on purpose: with team assignments sparse in this
deployment, team-only scoping would empty the page for a telesale who
demonstrably owns the work.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.utils import timezone

from crm.core.permissions import can_edit_lead, can_view_lead
from crm.leads.models import Lead
from crm.teams.models import TeamAssignment
from tests.db.factories import make_customer, make_lead, make_team, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def shared_customer():
    return make_customer(_uid="0000001", staff_code="S0001")


@pytest.fixture
def telesale_in_crm():
    user = make_user(email="ts@example.com", staff_code="S0001")
    TeamAssignment.objects.create(
        user=user, team_code="CRM_TEAM", effective_from=timezone.now()
    )
    return user


def test_telesale_cannot_view_the_same_customers_other_team_lead(
    shared_customer, telesale_in_crm
):
    make_team("CRM_TEAM")
    make_team("UPSELL_TEAM")
    own = make_lead(customer=shared_customer, team_code="CRM_TEAM")
    other = make_lead(customer=shared_customer, team_code="UPSELL_TEAM")

    assert can_view_lead(telesale_in_crm, own) is True
    assert can_view_lead(telesale_in_crm, other) is False
    assert can_edit_lead(telesale_in_crm, other) is False


def test_the_queryset_agrees_with_the_predicate(shared_customer, telesale_in_crm):
    make_team("CRM_TEAM")
    make_team("UPSELL_TEAM")
    own = make_lead(customer=shared_customer, team_code="CRM_TEAM")
    other = make_lead(customer=shared_customer, team_code="UPSELL_TEAM")

    visible = set(Lead.objects.for_user(telesale_in_crm).values_list("pk", flat=True))
    assert own.id in visible
    assert other.id not in visible


def test_a_telesale_always_reaches_a_lead_they_caretake(shared_customer, telesale_in_crm):
    # Even in a team they are not assigned to: it is their own work, and with
    # team assignments sparse this is the path that keeps the page usable.
    make_team("UPSELL_TEAM")
    mine = make_lead(
        customer=shared_customer, team_code="UPSELL_TEAM", caretaker=telesale_in_crm
    )
    assert can_view_lead(telesale_in_crm, mine) is True
    assert mine.id in set(Lead.objects.for_user(telesale_in_crm).values_list("pk", flat=True))


def test_a_telesale_still_cannot_reach_another_staff_members_customer(telesale_in_crm):
    make_team("CRM_TEAM")
    theirs = make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0002"), team_code="CRM_TEAM"
    )
    assert can_view_lead(telesale_in_crm, theirs) is False
    assert theirs.id not in set(
        Lead.objects.for_user(telesale_in_crm).values_list("pk", flat=True)
    )


def test_an_expired_team_assignment_does_not_grant_access(shared_customer):
    make_team("CRM_TEAM")
    user = make_user(email="ex@example.com", staff_code="S0001")
    TeamAssignment.objects.create(
        user=user,
        team_code="CRM_TEAM",
        effective_from=timezone.now() - timezone.timedelta(days=60),
        effective_to=timezone.now() - timezone.timedelta(days=30),
    )
    lead = make_lead(customer=shared_customer, team_code="CRM_TEAM")
    assert can_view_lead(user, lead) is False
