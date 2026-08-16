import pytest
from django.urls import reverse

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.followups.models import Followup
from crm.orders.models import Order

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def telesell_factory(client):
    def _make(email: str, staff_code: str):
        user = User.objects.create_user(email=email, password="x", role="พนักงาน", staff_code=staff_code)
        user.must_change_password = False
        user.save()
        return user

    return _make


def test_save_followup_updates_fields_and_priority_rank(client, editor):
    customer = Customer.objects.create(phone_key="0899000001", phone1="0899000001", customer_name="A")
    followup = Followup.objects.create(customer=customer, priority="NEW")

    resp = client.post(
        reverse("followups:save", args=[followup.id]),
        {
            "lead_status": "interested",
            "status": "scheduled",
            "priority": "VIP",
            "next_followup_date": "2026-08-01",
            "note": "test",
        },
        follow=True,
    )
    followup.refresh_from_db()
    assert resp.status_code == 200
    assert followup.lead_status == "interested"
    assert followup.priority == "VIP"
    assert followup.priority_rank == 5  # not the stale default(2) — save() recomputed it


def test_save_followup_denies_other_staff_customer(client, telesell_factory):
    """A telesell user must not be able to edit another staff member's
    follow-up by guessing the ID. save_followup_view catches this as a
    soft redirect+flash-message (matching the legacy inline
    st.error("ไม่มีสิทธิ์แก้ไขรายการนี้") behavior for this specific
    action) rather than a hard 403 — what matters is that the write never
    happens, which is what this test actually pins.
    """
    user = telesell_factory("staff1@example.com", "S0001")
    other_customer = Customer.objects.create(
        phone_key="0899000002", phone1="0899000002", customer_name="B", staff_code="S0002"
    )
    followup = Followup.objects.create(customer=other_customer, staff_code="S0002")
    client.force_login(user)

    resp = client.post(
        reverse("followups:save", args=[followup.id]),
        {"lead_status": "interested", "status": "none", "priority": "NEW", "note": ""},
        follow=True,
    )
    assert resp.status_code == 200
    assert "ไม่มีสิทธิ์แก้ไขรายการนี้" in resp.content.decode("utf-8")
    followup.refresh_from_db()
    assert followup.lead_status == "new"  # unchanged — the write never happened


def test_add_order_denies_other_staff_customer_direct_url(client, telesell_factory):
    """Regression test for a real vulnerability found during manual testing:
    add_order_view's route-level permission (can_add_manual_order) is role-
    only. Without an explicit object-level check, any telesell user could
    POST an order onto another staff member's customer by guessing the
    followup ID — the legacy app only avoided this because Streamlit's
    popup was reachable exclusively through an already-scoped list; Django
    exposes the URL directly, so the object-level check must be explicit.
    """
    attacker = telesell_factory("attacker@example.com", "S0001")
    victim_customer = Customer.objects.create(
        phone_key="0899000003",
        phone1="0899000003",
        customer_name="Victim",
        staff_code="S0002",
        owner_display="พนักงาน S0002",
    )
    followup = Followup.objects.create(customer=victim_customer, staff_code="S0002")
    client.force_login(attacker)

    orders_before = Order.objects.filter(customer=victim_customer).count()
    resp = client.post(
        reverse("followups:add_order", args=[followup.id]),
        {
            "order_no": "HACKED001",
            "sale_type": "NEW_ORDER",
            "url": "",
            "address": "",
            "sku_0": "SP001",
            "product_name_0": "x",
            "qty_0": "1",
            "amount_0": "100",
        },
    )
    assert resp.status_code == 403
    assert Order.objects.filter(customer=victim_customer).count() == orders_before


def test_add_order_succeeds_for_own_customer(client, telesell_factory):
    user = telesell_factory("staff2@example.com", "S0003")
    customer = Customer.objects.create(
        phone_key="0899000004",
        phone1="0899000004",
        customer_name="Own",
        staff_code="S0003",
        owner_display="พนักงาน S0003",
    )
    followup = Followup.objects.create(customer=customer, staff_code="S0003")
    client.force_login(user)

    resp = client.post(
        reverse("followups:add_order", args=[followup.id]),
        {
            "order_no": "OWN001",
            "sale_type": "NEW_ORDER",
            "url": "",
            "address": "",
            "sku_0": "SP001",
            "product_name_0": "x",
            "qty_0": "2",
            "amount_0": "200",
        },
        follow=True,
    )
    assert resp.status_code == 200
    order = Order.objects.get(customer=customer, order_no="OWN001")
    assert order.total_amount == 200
    assert order.staff_code == "S0003"  # locked to customer's owner, not editable via this form


def test_add_order_blocked_when_customer_has_no_owner(client, editor):
    customer = Customer.objects.create(phone_key="0899000005", phone1="0899000005", customer_name="NoOwner")
    followup = Followup.objects.create(customer=customer)

    resp = client.post(
        reverse("followups:add_order", args=[followup.id]),
        {"order_no": "X", "sale_type": "NEW_ORDER", "url": "", "address": "", "sku_0": "SP001", "product_name_0": "x", "qty_0": "1"},
        follow=True,
    )
    assert resp.status_code == 200
    assert "ยังไม่มีผู้ดูแล" in resp.content.decode("utf-8")
    assert not Order.objects.filter(customer=customer).exists()


def test_add_order_merges_same_sku_and_name(client, editor):
    customer = Customer.objects.create(
        phone_key="0899000006", phone1="0899000006", customer_name="Merge", owner_display="X", staff_code="S0009"
    )
    followup = Followup.objects.create(customer=customer, staff_code="S0009")

    client.post(
        reverse("followups:add_order", args=[followup.id]),
        {
            "order_no": "MERGE001",
            "sale_type": "NEW_ORDER",
            "url": "",
            "address": "",
            "sku_0": "SP001",
            "product_name_0": "Same",
            "qty_0": "2",
            "amount_0": "100",
            "sku_1": "SP001",
            "product_name_1": "Same",
            "qty_1": "3",
            "amount_1": "150",
            "sku_2": "SP001",
            "product_name_2": "Different",
            "qty_2": "1",
            "amount_2": "50",
        },
    )
    order = Order.objects.get(customer=customer, order_no="MERGE001")
    lines = list(order.lines.order_by("product_name"))
    assert len(lines) == 2  # "Same" merged, "Different" separate
    same_line = order.lines.get(product_name="Same")
    assert same_line.quantity == 5  # 2 + 3
    assert same_line.amount == 250  # 100 + 150
