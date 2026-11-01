"""Backfilling Leads from the existing Customer rows.

The live database has ~216k customers whose ownership is a staff_code string.
This command turns each owned customer into a Lead with an open ownership
segment, and queues each unowned one as Global Unassigned intake -- which is
what D019 says a customer with no known team is.

It must be idempotent and restartable: a 216k-row backfill that cannot be
re-run safely is a backfill nobody dares run twice.

Run via: docker compose exec web python -m pytest tests/db
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from crm.intake.models import IntakeItem
from crm.leads.models import Lead, OwnershipSegment
from crm.leads.recompute import recompute_lead_missed_channel
from crm.teams.models import TeamAssignment
from tests.db.factories import T0, make_customer, make_order, make_team, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def teams():
    return make_team("CRM_TEAM"), make_team("UPSELL_TEAM")


def test_backfill_creates_one_lead_per_owned_customer():
    make_customer(_uid="0000001", staff_code="S0001")
    make_customer(_uid="0000002", staff_code="S0002")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 2


def test_backfill_is_idempotent():
    make_customer(_uid="0000001", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 1
    assert OwnershipSegment.objects.filter(ended_at__isnull=True).count() == 1


def test_unowned_customers_become_intake_not_leads():
    make_customer(_uid="0000003", staff_code="")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 0
    assert IntakeItem.objects.count() == 1
    assert IntakeItem.objects.get().status == "GLOBAL_UNASSIGNED"


def test_intake_backfill_is_idempotent_too():
    make_customer(_uid="0000003", staff_code="")
    call_command("backfill_leads", team_code="CRM_TEAM")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert IntakeItem.objects.count() == 1


def test_dry_run_writes_nothing():
    make_customer(_uid="0000001", staff_code="S0001")
    make_customer(_uid="0000002", staff_code="")
    call_command("backfill_leads", team_code="CRM_TEAM", dry_run=True)
    assert Lead.objects.count() == 0
    assert IntakeItem.objects.count() == 0
    assert OwnershipSegment.objects.count() == 0


def test_the_lead_gets_an_open_ownership_segment():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_user(email="s1@example.com", staff_code="S0001", staff_name="หนึ่ง")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)
    segment = OwnershipSegment.objects.get(lead=lead)
    assert segment.ended_at is None
    assert lead.ownership_started_at is not None


def test_the_caretaker_is_resolved_from_staff_code():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    user = make_user(email="s1@example.com", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)
    assert lead.caretaker_id == user.id
    assert lead.routing_state == "ASSIGNED"
    assert lead.access_user_ids == [str(user.id)]


def test_a_staff_code_matching_no_user_leaves_the_lead_team_unassigned():
    customer = make_customer(_uid="0000001", staff_code="GHOST")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)
    assert lead.caretaker_id is None
    assert lead.routing_state == "TEAM_UNASSIGNED"


def test_the_team_comes_from_the_caretakers_open_assignment():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    user = make_user(email="s1@example.com", staff_code="S0001")
    TeamAssignment.objects.create(
        user=user, team_code="UPSELL_TEAM", effective_from=timezone.now() - timedelta(days=30)
    )
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.get(customer=customer).team.code == "UPSELL_TEAM"


def test_the_fallback_team_is_used_when_there_is_no_assignment():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_user(email="s1@example.com", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.get(customer=customer).team.code == "CRM_TEAM"


def test_backfill_sets_the_order_rollups():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_user(email="s1@example.com", staff_code="S0001")
    make_order(customer=customer, closer=None, ordered_at=T0 + timedelta(days=5))
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)
    assert lead.last_order_at is not None
    assert lead.total_orders == 1


def test_backfill_does_not_count_pre_existing_orders_as_missed_channel():
    """The backfill opens the ownership baseline at migration time, so history
    before it is not retroactively counted.

    This mirrors D023, which fixes the HX63 baseline at the snapshot instant
    precisely so migrated leads do not arrive blaming a caretaker for
    marketplace orders that predate the rule being enforced. Counting starts
    accruing from cutover.
    """
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_user(email="s1@example.com", staff_code="S0001")
    make_order(customer=customer, closer=None, ordered_at=T0 + timedelta(days=5))
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.get(customer=customer).missed_channel_count == 0


def test_an_order_after_the_backfill_does_count():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_user(email="s1@example.com", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)

    make_order(customer=customer, closer=None, ordered_at=timezone.now() + timedelta(days=1))
    assert recompute_lead_missed_channel(lead=lead) == 1


def test_an_order_closed_by_the_caretaker_after_backfill_leaves_it_at_zero():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    user = make_user(email="s1@example.com", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)

    make_order(customer=customer, closer=user, ordered_at=timezone.now() + timedelta(days=1))
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_an_unknown_fallback_team_is_rejected_before_writing_anything():
    make_customer(_uid="0000001", staff_code="S0001")
    with pytest.raises(Exception):
        call_command("backfill_leads", team_code="NO_SUCH_TEAM")
    assert Lead.objects.count() == 0


def test_a_customer_already_holding_a_lead_is_left_alone():
    from tests.db.factories import make_lead

    customer = make_customer(_uid="0000001", staff_code="S0001")
    existing = make_lead(customer=customer, team_code="CRM_TEAM")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.filter(customer=customer).count() == 1
    assert Lead.objects.get(customer=customer).id == existing.id
