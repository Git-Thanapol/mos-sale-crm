import pytest
from django.urls import reverse
from django.utils import timezone

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.matrix.models import Holiday
from crm.matrix.selectors import daily_matrix
from crm.orders.models import Order, OrderLine
from crm.teams.models import TeamAssignment

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client):
    user = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def test_index_requires_editor(client, viewer):
    resp = client.get(reverse("matrix:index"))
    assert resp.status_code == 403


def test_index_renders_for_editor(client, editor):
    resp = client.get(reverse("matrix:index"), {"year": 2026, "month": 7})
    assert resp.status_code == 200
    assert "ตารางยอดขายรายวัน" in resp.content.decode()


def test_daily_matrix_attributes_sales_to_current_team_member():
    crm_user = User.objects.create_user(email="crm1@example.com", password="x", role="EDITOR", staff_name="Crm1")
    TeamAssignment.objects.create(
        user=crm_user, team_code=TeamAssignment.TEAM_CRM, effective_from=timezone.now()
    )

    customer = Customer.objects.create(phone_key="0899900001", phone1="0899900001", customer_name="A")
    order = Order.objects.create(
        customer=customer, order_no="M1", sale_type="NEW_ORDER", source_type="manual",
        uploaded_by="crm1@example.com",
    )
    OrderLine.objects.create(order=order, sku="S1", product_name="P", quantity=1, amount=12000)

    matrix = daily_matrix(2026, 7)
    day_row = next(r for r in matrix.rows if r["date"] == timezone.localtime(order.created_at).date())
    cell = day_row["crm_cells"][crm_user.id]
    assert cell["amount"] == 12000
    assert cell["highlight"] == "green"  # > 11,000 CRM threshold
    assert day_row["crm_total"] == 12000


def test_holiday_marks_whole_day_row():
    Holiday.objects.create(date="2026-07-05", scope=Holiday.SCOPE_ALL, status=Holiday.STATUS_HOLIDAY)
    matrix = daily_matrix(2026, 7)
    row = next(r for r in matrix.rows if r["date"].isoformat() == "2026-07-05")
    assert row["is_holiday"] is True


def test_individual_leave_excludes_from_team_total():
    crm_user = User.objects.create_user(email="crm2@example.com", password="x", role="EDITOR", staff_name="Crm2")
    TeamAssignment.objects.create(
        user=crm_user, team_code=TeamAssignment.TEAM_CRM, effective_from=timezone.now()
    )

    customer = Customer.objects.create(phone_key="0899900002", phone1="0899900002", customer_name="B")
    order = Order.objects.create(
        customer=customer, order_no="M2", sale_type="NEW_ORDER", source_type="manual",
        uploaded_by="crm2@example.com",
    )
    OrderLine.objects.create(order=order, sku="S2", product_name="P", quantity=1, amount=5000)
    leave_date = timezone.localtime(order.created_at).date()
    Holiday.objects.create(
        date=leave_date, scope=Holiday.SCOPE_INDIVIDUAL, status=Holiday.STATUS_LEAVE, user=crm_user
    )

    matrix = daily_matrix(leave_date.year, leave_date.month)
    row = next(r for r in matrix.rows if r["date"] == leave_date)
    assert row["crm_cells"][crm_user.id]["leave"] is True
    assert row["crm_total"] == 0  # excluded, not counted as 0-but-included


def test_save_and_delete_holiday(client, editor):
    resp = client.post(
        reverse("matrix:save_holiday"),
        {"date": "2026-07-10", "scope": "ALL", "status": "HOLIDAY", "note": "test", "base_qs": ""},
        follow=True,
    )
    assert resp.status_code == 200
    holiday = Holiday.objects.get(date="2026-07-10")
    assert holiday.scope == Holiday.SCOPE_ALL

    resp = client.post(
        reverse("matrix:delete_holiday", args=[holiday.id]), {"base_qs": ""}, follow=True
    )
    assert resp.status_code == 200
    assert not Holiday.objects.filter(id=holiday.id).exists()


def test_save_individual_leave_requires_user(client, editor):
    resp = client.post(
        reverse("matrix:save_holiday"),
        {"date": "2026-07-11", "scope": "INDIVIDUAL", "status": "LEAVE", "note": "", "base_qs": ""},
        follow=True,
    )
    assert resp.status_code == 200
    assert not Holiday.objects.filter(date="2026-07-11").exists()
