"""Global Unassigned is a pre-Lead intake queue (D019), with deterministic
identity (D024).

02_LOCKED_BUSINESS_RULES.md:
- Global Unassigned is used when the Team is unknown. No Lead exists yet.
  Do NOT create a fake GLOBAL_UNASSIGNED team, and do NOT create a Lead with
  teamId = null.
- D024: one unresolved source Order corresponds to one deterministic intake.
  The Import Batch ID is not intake identity. The same customer + source
  orderId re-imported reuses the same intake; a different orderId is a
  separate intake. An existing Lead does not automatically route a
  Team-unknown Order.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.db.utils import IntegrityError

from crm.intake.models import IntakeItem
from crm.intake.services import resolve_intake_to_team, upsert_intake
from crm.leads.models import Lead, LeadAuditEvent
from crm.teams.models import Team
from tests.db.factories import ROLE_EDITOR, make_customer, make_lead, make_team, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def customer():
    return make_customer(_uid="0000001")


def test_same_customer_and_source_order_across_two_batches_creates_one_intake(customer):
    # D024 acceptance
    first, created_first = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER", source_ref="batch-A"
    )
    second, created_second = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER", source_ref="batch-B"
    )
    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert IntakeItem.objects.filter(customer=customer).count() == 1


def test_a_different_source_order_creates_a_separate_intake(customer):
    upsert_intake(customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    upsert_intake(customer=customer, source_order_id="SO-2", source_kind="LITE_DETAIL_ORDER")
    assert IntakeItem.objects.filter(customer=customer).count() == 2


def test_the_batch_reference_updates_but_identity_does_not(customer):
    first, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER", source_ref="batch-A"
    )
    second, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER", source_ref="batch-B"
    )
    assert first.id == second.id
    second.refresh_from_db()
    assert second.source_ref == "batch-B"


def test_the_unique_constraint_backs_the_deterministic_identity(customer):
    upsert_intake(customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    with pytest.raises(IntegrityError):
        IntakeItem.objects.create(
            customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
        )


def test_intake_never_creates_a_lead_before_it_is_resolved(customer):
    upsert_intake(customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    assert not Lead.objects.filter(customer=customer).exists()


def test_an_existing_lead_does_not_auto_route_a_team_unknown_order(customer):
    make_lead(customer=customer, team_code="CRM_TEAM")
    item, created = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    assert created is True
    assert item.status == "GLOBAL_UNASSIGNED"
    assert item.assigned_team_id is None
    assert item.created_lead_id is None


def test_resolving_reuses_the_existing_lead_for_that_team(editor, customer):
    existing = make_lead(customer=customer, team_code="CRM_TEAM")
    item, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )

    lead = resolve_intake_to_team(actor=editor, intake=item, team=existing.team)

    assert lead.id == existing.id
    assert Lead.objects.filter(customer=customer, team=existing.team).count() == 1
    item.refresh_from_db()
    assert item.status == "CONVERTED"
    assert item.resolved_by_id == editor.id
    assert item.resolved_at is not None
    assert item.created_lead_id == existing.id


def test_resolving_creates_the_lead_when_none_exists(editor, customer):
    item, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    team = make_team("CRM_TEAM")

    lead = resolve_intake_to_team(actor=editor, intake=item, team=team)

    assert lead.lead_key == f"CRM_TEAM:{customer.customer_uid}"
    assert lead.team_id == team.id
    assert lead.routing_state == "TEAM_UNASSIGNED"


def test_resolving_is_audited(editor, customer):
    item, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    lead = resolve_intake_to_team(actor=editor, intake=item, team=make_team("CRM_TEAM"))
    event = LeadAuditEvent.objects.get(lead=lead, action="INTAKE_CONVERTED")
    assert event.actor_id == editor.id
    assert event.payload["intake_id"] == item.id


def test_resolving_an_already_converted_intake_is_rejected(editor, customer):
    item, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    team = make_team("CRM_TEAM")
    resolve_intake_to_team(actor=editor, intake=item, team=team)
    with pytest.raises(ValueError):
        resolve_intake_to_team(actor=editor, intake=item, team=team)


def test_two_intakes_for_one_customer_resolve_to_the_same_lead(editor, customer):
    first, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    second, _ = upsert_intake(
        customer=customer, source_order_id="SO-2", source_kind="LITE_DETAIL_ORDER"
    )
    team = make_team("CRM_TEAM")

    lead_a = resolve_intake_to_team(actor=editor, intake=first, team=team)
    lead_b = resolve_intake_to_team(actor=editor, intake=second, team=team)

    assert lead_a.id == lead_b.id
    assert Lead.objects.filter(customer=customer).count() == 1


def test_an_unknown_source_kind_is_rejected(customer):
    with pytest.raises(ValueError):
        upsert_intake(customer=customer, source_order_id="SO-1", source_kind="SPREADSHEET_OF_DOOM")


def test_no_team_named_global_unassigned_exists(editor, customer):
    # D019: Global Unassigned is a queue, never a Team, and never a null-team Lead.
    item, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    resolve_intake_to_team(actor=editor, intake=item, team=make_team("CRM_TEAM"))
    assert not Team.objects.filter(code__icontains="GLOBAL_UNASSIGNED").exists()
    assert Lead._meta.get_field("team").null is False


def test_an_intake_with_no_source_order_is_still_deterministic(customer):
    # A customer may arrive with no order reference at all; identity then rests
    # on the customer plus the empty source id, so a re-import still reuses it.
    first, created_first = upsert_intake(
        customer=customer, source_order_id="", source_kind="CRM_CUSTOMERS"
    )
    second, created_second = upsert_intake(
        customer=customer, source_order_id="", source_kind="CRM_CUSTOMERS"
    )
    assert created_first is True
    assert created_second is False
    assert first.id == second.id
