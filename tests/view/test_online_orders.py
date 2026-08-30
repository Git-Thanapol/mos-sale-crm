"""Covers the two online-order import formats (InoutManageMain,
AllLiteDetailOrder — see feedback/Update_import_file/issue.md) and the
/online-orders list page. Mirrors tests/view/test_import_excel.py's
pattern: workbooks built in memory with openpyxl, uploaded via
imports:upload, RQ runs inline (config.settings.test ASYNC: False).
"""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from openpyxl.utils import column_index_from_string

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.online_orders.models import OnlineOrder

pytestmark = [pytest.mark.view, pytest.mark.django_db]

MAIN_WIDTH = 56  # A..BD
DETAIL_WIDTH = 26  # A..Z


def _row(width: int, values: dict[str, object]) -> list:
    row = [None] * width
    for letter, value in values.items():
        row[column_index_from_string(letter) - 1] = value
    return row


def _main_header() -> list:
    return _row(MAIN_WIDTH, {
        "A": "หมายเลขคำสั่งซื้อออนไลน์",
        "H": "บริษัทขนส่ง",
        "I": "หมายเลขพัสดุ",
        "P": "หมายเลขออเดอร์ภายใน",
        "R": "ชื่อร้านค้า",
        "AB": "ที่อยู่ผู้รับ",
        "AC": "ผู้รับ",
        "AS": "ผู้รับ",
        "AT": "เขต/อำเภอของผู้รับ",
        "AU": "แขวง/ตำบลของผู้รับ",
        "AV": "รหัสไปรษณีย์ของผู้รับ",
        "AW": "ผู้รับ",
        "AZ": "ชื่อลูกค้า",
        "BD": "จำนวนเงินทั้งหมด",
    })


def _detail_header() -> list:
    return _row(DETAIL_WIDTH, {
        "A": "หมายเลขออเดอร์ภายใน",
        "B": "หมายเลขคำสั่งซื้อออนไลน์",
        "D": "สถานะคำสั่งซื้อ",
        "E": "บริษัทขนส่ง",
        "F": "เลขพัสดุ",
        "G": "เวลาสั่งซื้อ",
        "H": "ร้านค้า",
        "J": "ราคาสินค้าทั้งหมด",
        "K": "จังหวัด",
        "L": "เมือง",
        "M": "แขวง/ตำบล",
        "N": "ผู้รับ",
        "O": "รหัสไปรษณีย์",
        "P": "พนักงานขาย",
        "R": "วิธีการชำระเงิน",
        "S": "พนักงาน Upsell",
        "T": "ยอดขาย Upsell",
        "V": "รหัสสินค้า",
        "W": "ชื่อสินค้า",
        "Z": "จำนวน",
    })


def _make_main_xlsx(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(_main_header())
    for values in rows:
        ws.append(_row(MAIN_WIDTH, values))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_detail_xlsx(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(_detail_header())
    for values in rows:
        ws.append(_row(DETAIL_WIDTH, values))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


MAIN_ROW_1 = {
    "A": "2001622162938990592",
    "H": "J&T Express",
    "I": "864489380812",
    "P": "213008",
    "R": "Facebook",
    "AB": "71 หมู่ 6",
    "AC": "พรภวิษย์ บัวบาน",
    "AS": "พัทลุง",
    "AT": "เมืองพัทลุง",
    "AU": "ควนมะพร้าว",
    "AV": "93000",
    "AW": "0957575700",
    "AZ": "Pohnpawit Buaban",
    "BD": 89,
}

DETAIL_ROW_1A = {
    "A": "213008", "B": "2001622162938990592", "D": "จัดส่งแล้ว", "E": "J&T Express",
    "F": "864489380812", "G": "2025-12-30 23:11:52", "H": "Facebook", "J": "89.00",
    "K": "พัทลุง", "L": "เมืองพัทลุง", "M": "ควนมะพร้าว", "N": "พรภวิษย์ บัวบาน", "O": "93000",
    "P": "Admin Test", "R": "COD", "S": "", "T": None,
    "V": "SP001", "W": "สินค้า A", "Z": 1,
}


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def _upload(client, content: bytes):
    upload = SimpleUploadedFile("test.xlsx", content)
    return client.post(reverse("imports:upload"), {"file": upload}, follow=True)


def test_main_file_maps_mislabeled_columns(client, editor):
    """AC/AS/AW all headed 'ผู้รับ' in the source — verify province came
    from AS and phone from AW, not collapsed by header-name lookup.
    """
    resp = _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    assert resp.status_code == 200
    assert "InoutManageMain" in resp.content.decode("utf-8")

    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.province == "พัทลุง"
    assert order.phone == "0957575700"
    assert order.recipient_name == "พรภวิษย์ บัวบาน"
    assert order.customer_id is not None
    customer = Customer.objects.get(pk=order.customer_id)
    assert customer.phone1 == "0957575700"


def test_main_file_normalizes_phone(client, editor):
    row = dict(MAIN_ROW_1, AW="095-757-5700")
    _upload(client, _make_main_xlsx([row]))
    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.phone == "0957575700"
    assert Customer.objects.filter(phone1="0957575700").count() == 1


def test_detail_only_order_leaves_customer_null(client, editor):
    """No phone anywhere for a detail-only order — must not link/create a
    customer, matching the verified 365 real orphan orders.
    """
    resp = _upload(client, _make_detail_xlsx([DETAIL_ROW_1A]))
    assert resp.status_code == 200
    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.customer_id is None
    assert Customer.objects.count() == 0
    assert order.lines.count() == 1


def test_upsell_collision_keeps_two_lines_for_same_sku(client, editor):
    """Same SKU twice in one order at different prices/upsell staff — the
    case ux_line_order_sku_name on the existing OrderLine would merge.
    """
    row_original = dict(DETAIL_ROW_1A, V="SP358", W="สินค้า", Z=3, S="", T=None)
    row_upsell = dict(DETAIL_ROW_1A, V="SP358", W="สินค้า", Z=2, S="Tele Test", T=358)
    _upload(client, _make_detail_xlsx([row_original, row_upsell]))

    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    lines = list(order.lines.order_by("line_no"))
    assert len(lines) == 2
    assert [ln.quantity for ln in lines] == [3, 2]
    assert [ln.upsell_staff for ln in lines] == ["", "Tele Test"]


def test_either_upload_order_converges(client, editor):
    """Uploading main-then-detail or detail-then-main must produce the
    same final row — the core merge guarantee.
    """
    _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    _upload(client, _make_detail_xlsx([DETAIL_ROW_1A]))
    order_a = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    snapshot_a = {
        f: getattr(order_a, f)
        for f in ("carrier", "tracking_no", "shop_name", "phone", "province", "district",
                   "subdistrict", "postal_code", "recipient_name", "order_status", "sales_staff")
    }
    OnlineOrder.objects.all().delete()
    Customer.objects.all().delete()

    _upload(client, _make_detail_xlsx([DETAIL_ROW_1A]))
    _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    order_b = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    snapshot_b = {f: getattr(order_b, f) for f in snapshot_a}

    assert snapshot_a == snapshot_b


def test_blank_incoming_cell_does_not_clear_stored_value(client, editor):
    """File 1 supplies a tracking number; file 2 arrives with tracking
    blank — the stored value must survive (the 351-blank-tracking case).
    """
    _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    detail_no_tracking = dict(DETAIL_ROW_1A, F=None)
    _upload(client, _make_detail_xlsx([detail_no_tracking]))

    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.tracking_no == "864489380812"


def test_reupload_detail_drops_a_removed_line(client, editor):
    row1 = dict(DETAIL_ROW_1A, V="SP001")
    row2 = dict(DETAIL_ROW_1A, V="SP002")
    _upload(client, _make_detail_xlsx([row1, row2]))
    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.lines.count() == 2

    _upload(client, _make_detail_xlsx([row1]))
    order.refresh_from_db()
    assert order.lines.count() == 1
    assert order.lines.get().sku == "SP001"


def test_existing_customer_is_linked_not_duplicated_and_not_mutated(client, editor):
    existing = Customer.objects.create(
        phone_key="0957575700", phone1="0957575700", customer_name="เดิม",
        owner_display="X", staff_code="S1",
    )
    _upload(client, _make_main_xlsx([MAIN_ROW_1]))

    assert Customer.objects.count() == 1
    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    assert order.customer_id == existing.id
    existing.refresh_from_db()
    assert existing.customer_name == "เดิม"
    assert existing.owner_display == "X"
    assert existing.staff_code == "S1"


def test_online_import_does_not_touch_existing_order_tables(client, editor):
    from crm.imports.models import StagingImportRow
    from crm.orders.models import Order, OrderLine

    _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    _upload(client, _make_detail_xlsx([DETAIL_ROW_1A]))

    assert Order.objects.count() == 0
    assert OrderLine.objects.count() == 0
    assert StagingImportRow.objects.count() == 0


def test_page_requires_can_view_followup(client):
    # can_view_followup = EDITOR or any staff-limited role (พนักงาน/TELESELL/
    # STAFF/USER/ทั่วไป) — ADMIN is the one role deliberately excluded, the
    # same EDITOR/ADMIN asymmetry CLAUDE.md documents for the follow-up page.
    admin = User.objects.create_user(email="admin@example.com", password="x", role="ADMIN")
    admin.must_change_password = False
    admin.save()
    client.force_login(admin)
    resp = client.get(reverse("online_orders:list"))
    assert resp.status_code == 403


def test_page_lists_orders_and_expand_panel(client, editor):
    _upload(client, _make_main_xlsx([MAIN_ROW_1]))
    _upload(client, _make_detail_xlsx([DETAIL_ROW_1A]))

    resp = client.get(reverse("online_orders:list"))
    assert resp.status_code == 200
    assert "2001622162938990592" in resp.content.decode("utf-8")

    order = OnlineOrder.objects.get(online_order_no="2001622162938990592")
    resp = client.get(reverse("online_orders:list"), {"id": order.id})
    body = resp.content.decode("utf-8")
    assert f'id="detail-{order.id}"' in body
    assert "SP001" in body
