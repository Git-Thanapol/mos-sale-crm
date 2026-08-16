import pytest
from django.urls import reverse

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.orders.models import Order

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR", staff_code="S0001", staff_name="Editor One")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def telesell_factory(client):
    def _make(email: str, staff_code: str, staff_name: str = ""):
        user = User.objects.create_user(
            email=email, password="x", role="พนักงาน", staff_code=staff_code, staff_name=staff_name or staff_code
        )
        user.must_change_password = False
        user.save()
        return user

    return _make


def _post_order(client, **overrides):
    data = {
        "customer_name": "ทดสอบ",
        "phone1": "0899200001",
        "phone2": "",
        "order_no": "T001",
        "sale_type": "NEW_ORDER",
        "url": "",
        "address": "",
        "sku_0": "SP001",
        "product_name_0": "x",
        "qty_0": "1",
        "amount_0": "100",
    }
    data.update(overrides)
    return client.post(reverse("orders:new"), data, follow=True)


def test_new_customer_created_with_editor_chosen_owner(client, editor):
    resp = _post_order(client, staff_code="S0001")
    assert resp.status_code == 200
    customer = Customer.objects.get(phone1="0899200001")
    assert customer.staff_code == "S0001"
    assert customer.owner_display == "Editor One"
    order = Order.objects.get(customer=customer, order_no="T001")
    assert order.total_amount == 100


def test_new_customer_owner_locked_to_telesell_actor(client, telesell_factory):
    user = telesell_factory("staff1@example.com", "S0005", "Staff Five")
    client.force_login(user)

    # even if a staff_code were submitted, the field isn't rendered/trusted
    # for non-managers — the view never reads it for them
    resp = _post_order(client, phone1="0899200002")
    assert resp.status_code == 200
    customer = Customer.objects.get(phone1="0899200002")
    assert customer.staff_code == "S0005"
    assert customer.owner_display == "Staff Five"


def test_telesell_blocked_from_existing_other_staff_customer(client, telesell_factory):
    attacker = telesell_factory("attacker@example.com", "S0006")
    victim = Customer.objects.create(
        phone_key="0899200003", phone1="0899200003", customer_name="Victim", staff_code="S0007", owner_display="Owner Seven"
    )
    client.force_login(attacker)

    resp = _post_order(client, phone1="0899200003", customer_name="Victim", order_no="HACK001")
    assert resp.status_code == 200
    assert "มีผู้ดูแลแล้ว" in resp.content.decode("utf-8")
    assert not Order.objects.filter(customer=victim, order_no="HACK001").exists()


def test_telesell_can_add_order_to_own_existing_customer(client, telesell_factory):
    user = telesell_factory("staff2@example.com", "S0008")
    own = Customer.objects.create(
        phone_key="0899200004", phone1="0899200004", customer_name="Mine", staff_code="S0008", owner_display="Staff"
    )
    client.force_login(user)

    resp = _post_order(client, phone1="0899200004", customer_name="Mine", order_no="OWN001")
    assert resp.status_code == 200
    assert Order.objects.filter(customer=own, order_no="OWN001").exists()


def test_manual_order_persists_province_city_postal_code(client, editor):
    resp = _post_order(
        client, staff_code="S0001", phone1="0899200099",
        province="กรุงเทพมหานคร", city="พระนคร", postal_code="10200",
        address="ตำบลบางขุนพรหม 123 ถนนทดสอบ",
    )
    assert resp.status_code == 200
    customer = Customer.objects.get(phone1="0899200099")
    assert customer.province == "กรุงเทพมหานคร"
    assert customer.city == "พระนคร"
    assert customer.postal_code == "10200"
    assert customer.address == "ตำบลบางขุนพรหม 123 ถนนทดสอบ"


def test_manual_order_requires_at_least_one_phone(client, editor):
    resp = _post_order(client, phone1="", phone2="", staff_code="S0001")
    assert resp.status_code == 200
    assert not Customer.objects.filter(customer_name="ทดสอบ").exists()


def test_manual_order_page_requires_can_add_manual_order(client):
    viewer = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    viewer.must_change_password = False
    viewer.save()
    client.force_login(viewer)

    resp = client.get(reverse("orders:new"))
    assert resp.status_code == 403
