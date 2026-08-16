import pytest
from django.urls import reverse

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.followups.models import Followup

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


def test_save_follow_marker_updates_status(client, editor):
    customer = Customer.objects.create(phone_key="0899100001", phone1="0899100001", customer_name="A")
    resp = client.post(reverse("customers:follow_marker", args=[customer.id]), {"status": "scheduled"}, follow=True)
    assert resp.status_code == 200
    followup = Followup.objects.get(customer=customer)
    assert followup.status == "scheduled"


def test_save_follow_marker_denies_other_staff_customer(client, telesell_factory):
    user = telesell_factory("staff1@example.com", "S0001")
    other = Customer.objects.create(
        phone_key="0899100002", phone1="0899100002", customer_name="B", staff_code="S0002"
    )
    client.force_login(user)

    resp = client.post(reverse("customers:follow_marker", args=[other.id]), {"status": "done"}, follow=True)
    assert resp.status_code == 200
    assert "ไม่มีสิทธิ์แก้ไขรายการนี้" in resp.content.decode("utf-8")
    assert not Followup.objects.filter(customer=other).exists()


def test_save_follow_marker_allows_own_customer(client, telesell_factory):
    user = telesell_factory("staff2@example.com", "S0003")
    own = Customer.objects.create(
        phone_key="0899100003", phone1="0899100003", customer_name="C", staff_code="S0003"
    )
    client.force_login(user)

    resp = client.post(reverse("customers:follow_marker", args=[own.id]), {"status": "done"}, follow=True)
    assert resp.status_code == 200
    assert Followup.objects.get(customer=own).status == "done"


def test_assign_owner_cascades_to_orders_and_followup(client, editor):
    from crm.orders.models import Order

    customer = Customer.objects.create(
        phone_key="0899100004", phone1="0899100004", customer_name="D",
        owner_display="เก่า", staff_code="S0001",
    )
    Order.objects.create(customer=customer, order_no="O1", owner_display="เก่า", staff_code="S0001")
    Followup.objects.create(customer=customer, owner_display="เก่า", staff_code="S0001")

    resp = client.post(
        reverse("customers:assign_owner", args=[customer.id]),
        {"staff_code": "S0002", "owner_display": "ใหม่"},
        follow=True,
    )
    assert resp.status_code == 200
    customer.refresh_from_db()
    assert customer.staff_code == "S0002"
    assert customer.owner_display == "ใหม่"
    assert Order.objects.get(customer=customer).staff_code == "S0002"
    assert Followup.objects.get(customer=customer).staff_code == "S0002"


def test_assign_owner_denied_for_non_editor(client, telesell_factory):
    """can_assign_customer_owner is EDITOR-only — even ADMIN is denied
    (docs/DECISIONS.md, ADMIN weaker than EDITOR, preserved verbatim).
    """
    user = telesell_factory("staff3@example.com", "S0001")
    customer = Customer.objects.create(
        phone_key="0899100005", phone1="0899100005", customer_name="E", staff_code="S0001"
    )
    client.force_login(user)

    resp = client.post(
        reverse("customers:assign_owner", args=[customer.id]),
        {"staff_code": "S0009", "owner_display": "x"},
        follow=True,
    )
    assert resp.status_code == 200
    assert "ไม่มีสิทธิ์มอบหมายผู้ดูแล" in resp.content.decode("utf-8")
    customer.refresh_from_db()
    assert customer.staff_code == "S0001"  # unchanged


def test_assign_url_updates_customer(client, editor):
    customer = Customer.objects.create(phone_key="0899100006", phone1="0899100006", customer_name="F")
    resp = client.post(
        reverse("customers:assign_url", args=[customer.id]), {"url": "https://example.com/p"}, follow=True
    )
    assert resp.status_code == 200
    customer.refresh_from_db()
    assert customer.url == "https://example.com/p"


def test_assign_url_rejects_blank(client, editor):
    customer = Customer.objects.create(phone_key="0899100007", phone1="0899100007", customer_name="G", url="old")
    resp = client.post(reverse("customers:assign_url", args=[customer.id]), {"url": ""}, follow=True)
    assert resp.status_code == 200
    assert "กรุณากรอก URL ก่อนบันทึก" in resp.content.decode("utf-8")
    customer.refresh_from_db()
    assert customer.url == "old"
