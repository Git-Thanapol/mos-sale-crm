"""Writing an order keeps the Lead's derived state current.

Nothing called apply_order_to_lead, recompute_lead_missed_channel or open_cycle
from a production path, so after the backfill every counter on the page was
frozen while real orders kept arriving. A stale number shown confidently is
worse than no number.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.utils import timezone

from crm.followcycles.models import FollowupCycle
from crm.leads.models import Lead, OwnershipSegment
from crm.leads.sync import sync_leads_for_order
from crm.orders.services import LineItem, save_order_for_customer
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
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


def test_saving_an_order_refreshes_the_lead_rollups(editor):
    customer = make_customer(_uid="0000001", staff_code="S0001", owner_display="หนึ่ง")
    lead = make_lead(customer=customer)
    assert lead.total_orders == 0

    save_order_for_customer(
        actor=editor,
        customer=customer,
        order_no="SO-NEW-1",
        order_date=timezone.localdate(),
        sale_type="NEW_ORDER",
        url="",
        address="",
        items=[LineItem(sku="SKU-1", product_name="สินค้า", qty=1, amount=100)],
        bypass_owner_conflict=True,
    )

    lead.refresh_from_db()
    assert lead.total_orders == 1
    assert lead.last_order_at is not None
    assert lead.last_order_id is not None


def test_saving_an_order_opens_a_follow_up_cycle(editor):
    customer = make_customer(_uid="0000002", staff_code="S0001", owner_display="หนึ่ง")
    lead = make_lead(customer=customer)

    save_order_for_customer(
        actor=editor,
        customer=customer,
        order_no="SO-NEW-2",
        order_date=timezone.localdate(),
        sale_type="NEW_ORDER",
        url="",
        address="",
        items=[LineItem(sku="SKU-1", product_name="สินค้า", qty=1, amount=100)],
        bypass_owner_conflict=True,
    )

    lead.refresh_from_db()
    assert lead.current_cycle_id is not None
    assert FollowupCycle.objects.filter(lead=lead, status="ACTIVE").count() == 1


def test_a_marketplace_order_increments_missed_channel_for_the_caretaker(editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    customer = make_customer(_uid="0000003", staff_code="S0001")
    lead = make_lead(customer=customer, caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)

    order = make_order(customer=customer, closer=None, ordered_at=timezone.now())
    sync_leads_for_order(actor=editor, order=order)

    lead.refresh_from_db()
    assert lead.missed_channel_count == 1


def test_an_order_closed_by_the_caretaker_resets_the_counter(editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    customer = make_customer(_uid="0000004", staff_code="S0001")
    lead = make_lead(customer=customer, caretaker=caretaker)
    OwnershipSegment.objects.create(lead=lead, caretaker=caretaker, started_at=T0)

    sync_leads_for_order(
        actor=editor, order=make_order(customer=customer, closer=None, ordered_at=timezone.now())
    )
    lead.refresh_from_db()
    assert lead.missed_channel_count == 1

    sync_leads_for_order(
        actor=editor,
        order=make_order(customer=customer, closer=caretaker, ordered_at=timezone.now()),
    )
    lead.refresh_from_db()
    assert lead.missed_channel_count == 0


def test_every_team_lead_for_the_customer_is_synced(editor):
    customer = make_customer(_uid="0000005", staff_code="S0001")
    make_team("CRM_TEAM")
    make_team("UPSELL_TEAM")
    a = make_lead(customer=customer, team_code="CRM_TEAM")
    b = make_lead(customer=customer, team_code="UPSELL_TEAM")

    touched = sync_leads_for_order(
        actor=editor, order=make_order(customer=customer, closer=None, ordered_at=timezone.now())
    )

    assert touched == 2
    for lead in (a, b):
        lead.refresh_from_db()
        assert lead.total_orders == 1


def test_a_customer_with_no_lead_is_a_no_op(editor):
    customer = make_customer(_uid="0000006", staff_code="S0001")
    order = make_order(customer=customer, closer=None, ordered_at=timezone.now())
    assert sync_leads_for_order(actor=editor, order=order) == 0
    assert not Lead.objects.filter(customer=customer).exists()


def test_a_stopped_lead_does_not_get_a_new_cycle(editor):
    customer = make_customer(_uid="0000007", staff_code="S0001")
    lead = make_lead(
        customer=customer, lifecycle_status="STOPPED", stopped_reason="MANUAL_STOP"
    )
    sync_leads_for_order(
        actor=editor, order=make_order(customer=customer, closer=None, ordered_at=timezone.now())
    )
    assert FollowupCycle.objects.filter(lead=lead).count() == 0


def test_rollups_are_recomputed_not_incremented(editor):
    customer = make_customer(_uid="0000008", staff_code="S0001")
    lead = make_lead(customer=customer)
    order = make_order(customer=customer, closer=None, ordered_at=timezone.now())

    sync_leads_for_order(actor=editor, order=order)
    sync_leads_for_order(actor=editor, order=order)

    lead.refresh_from_db()
    assert lead.total_orders == 1
