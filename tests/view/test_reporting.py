from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.orders.models import Order, OrderLine
from crm.reporting.selectors import sales_rows, sales_summary

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def _seed_orders(n=5):
    for i in range(n):
        customer = Customer.objects.create(
            phone_key=f"08977{i:05d}", phone1=f"08977{i:05d}", customer_name=f"C{i}"
        )
        order = Order.objects.create(
            customer=customer, order_no=f"ORD{i}", sale_type="NEW_ORDER", order_date=timezone.localdate()
        )
        OrderLine.objects.create(order=order, sku="SP1", product_name="P", quantity=1, amount=100)
        OrderLine.objects.create(order=order, sku="SP2", product_name="Q", quantity=1, amount=50)


def test_sales_summary_matches_row_totals(editor):
    """Regression test: sales_summary's aggregation once leaked the
    row-display ordering from _sales_lines into its GROUP BY (a classic
    Django gotcha — order_by fields get folded into the grouping), which
    silently overwrote the per-sale_type total with just the last line's
    amount instead of the real sum. Caught by comparing against the
    independently-computed row-level total.
    """
    _seed_orders(5)
    from django.utils import timezone
    today = timezone.localdate()

    summary = sales_summary(editor, today, today, "ทั้งหมด")
    rows = sales_rows(editor, today, today, "ทั้งหมด", limit=10000)

    assert len(rows) == 10  # 5 orders x 2 lines
    assert summary["TOTAL"]["sales_amount"] == sum(Decimal(str(r["amount"])) for r in rows)
    assert summary["NEW_ORDER"]["sales_amount"] == Decimal("750")  # 5 orders x (100+50)
    assert summary["NEW_ORDER"]["order_count"] == 5


def test_dashboard_renders(client, editor):
    resp = client.get(reverse("reporting:dashboard"))
    assert resp.status_code == 200


def test_dashboard_follow_sale_type_excluded_from_revenue(client, editor):
    """Invariant 6: FOLLOW sale_type must never count toward revenue."""
    customer = Customer.objects.create(phone_key="0899700001", phone1="0899700001", customer_name="F")
    order = Order.objects.create(customer=customer, order_no="FOLLOWORD", sale_type="FOLLOW")
    OrderLine.objects.create(order=order, sku="SPX", product_name="X", quantity=1, amount=99999)

    from django.utils import timezone
    today = timezone.localdate()
    summary = sales_summary(editor, today, today, "ทั้งหมด")
    assert summary["TOTAL"]["sales_amount"] == 0
    assert summary["TOTAL"]["order_count"] == 0


def test_dashboard_scopes_to_own_staff_code_for_telesell(client):
    telesell = User.objects.create_user(email="staff@example.com", password="x", role="พนักงาน", staff_code="S0001")
    telesell.must_change_password = False
    telesell.save()
    client.force_login(telesell)

    own = Customer.objects.create(phone_key="0899700002", phone1="0899700002", customer_name="Own", staff_code="S0001")
    other = Customer.objects.create(phone_key="0899700003", phone1="0899700003", customer_name="Other", staff_code="S0002")
    today = timezone.localdate()
    own_order = Order.objects.create(
        customer=own, order_no="OWNORD", sale_type="NEW_ORDER", staff_code="S0001", order_date=today
    )
    OrderLine.objects.create(order=own_order, sku="S1", product_name="A", quantity=1, amount=500)
    other_order = Order.objects.create(
        customer=other, order_no="OTHERORD", sale_type="NEW_ORDER", staff_code="S0002", order_date=today
    )
    OrderLine.objects.create(order=other_order, sku="S2", product_name="B", quantity=1, amount=999)

    summary = sales_summary(telesell, today, today, "ทั้งหมด")
    assert summary["TOTAL"]["sales_amount"] == 500  # only their own order


def test_delete_sales_requires_can_delete_order(client):
    viewer = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    viewer.must_change_password = False
    viewer.save()
    client.force_login(viewer)

    customer = Customer.objects.create(phone_key="0899700004", phone1="0899700004", customer_name="D")
    order = Order.objects.create(customer=customer, order_no="DELORD", sale_type="NEW_ORDER", source_type="manual")
    line = OrderLine.objects.create(order=order, sku="S3", product_name="C", quantity=1, amount=100)

    resp = client.post(reverse("reporting:delete_sales"), {"line_id": [str(line.id)], "base_qs": ""}, follow=True)
    assert resp.status_code == 200
    assert OrderLine.objects.filter(id=line.id).exists()  # not deleted


def test_delete_sales_blocks_import_rows(client, editor):
    customer = Customer.objects.create(phone_key="0899700005", phone1="0899700005", customer_name="E")
    order = Order.objects.create(customer=customer, order_no="IMPORD", sale_type="NEW_ORDER", source_type="import")
    line = OrderLine.objects.create(order=order, sku="S4", product_name="D", quantity=1, amount=100)

    resp = client.post(reverse("reporting:delete_sales"), {"line_id": [str(line.id)], "base_qs": ""}, follow=True)
    assert resp.status_code == 200
    assert OrderLine.objects.filter(id=line.id).exists()  # import rows never deletable here


def test_delete_sales_telesell_cannot_delete_other_staff_order(client):
    telesell = User.objects.create_user(email="staff2@example.com", password="x", role="พนักงาน", staff_code="S0001")
    telesell.must_change_password = False
    telesell.save()
    client.force_login(telesell)

    customer = Customer.objects.create(phone_key="0899700006", phone1="0899700006", customer_name="F", staff_code="S0002")
    order = Order.objects.create(customer=customer, order_no="OTHERDEL", sale_type="NEW_ORDER", source_type="manual", staff_code="S0002")
    line = OrderLine.objects.create(order=order, sku="S5", product_name="E", quantity=1, amount=100)

    resp = client.post(reverse("reporting:delete_sales"), {"line_id": [str(line.id)], "base_qs": ""}, follow=True)
    assert resp.status_code == 200
    assert OrderLine.objects.filter(id=line.id).exists()


def test_delete_sales_editor_can_delete_manual_order(client, editor):
    customer = Customer.objects.create(phone_key="0899700007", phone1="0899700007", customer_name="G")
    order = Order.objects.create(customer=customer, order_no="DELOK", sale_type="NEW_ORDER", source_type="manual")
    line = OrderLine.objects.create(order=order, sku="S6", product_name="F", quantity=1, amount=100)

    resp = client.post(reverse("reporting:delete_sales"), {"line_id": [str(line.id)], "base_qs": ""}, follow=True)
    assert resp.status_code == 200
    assert not OrderLine.objects.filter(id=line.id).exists()
