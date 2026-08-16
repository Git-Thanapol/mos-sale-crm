import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from io import BytesIO

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.imports.models import StagingImportRow

pytestmark = [pytest.mark.view, pytest.mark.django_db]


def _make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append([
        "เลขคำสั่งซื้อ", "ชื่อลูกค้า", "เบอร์โทร", "เบอร์สำรอง", "SKU", "ชื่อสินค้า", "จำนวน", "ราคา",
        "วันที่สั่งซื้อ", "ประเภทการขาย", "จังหวัด", "อำเภอ", "รหัสไปรษณีย์", "ที่อยู่", "ผู้ดูแล",
        "รหัสพนักงาน", "เลขพัสดุ", "ขนส่ง", "สถานะ",
    ])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def test_import_requires_can_import_excel(client):
    viewer = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    viewer.must_change_password = False
    viewer.save()
    client.force_login(viewer)

    resp = client.get(reverse("imports:upload"))
    assert resp.status_code == 403


def test_import_valid_row_creates_customer_order_line(client, editor):
    content = _make_xlsx([
        ["ORDWEB1", "ทดสอบ", "0899500001", "", "SP001", "สินค้า", 2, 500, "2026-07-01", "NEW_ORDER",
         "", "", "", "", "", "", "", "", ""],
    ])
    upload = SimpleUploadedFile("test.xlsx", content)
    resp = client.post(reverse("imports:upload"), {"file": upload}, follow=True)
    assert resp.status_code == 200
    assert "นำเข้าสำเร็จ" in resp.content.decode("utf-8")

    customer = Customer.objects.get(phone1="0899500001")
    order = customer.orders.get(order_no="ORDWEB1")
    line = order.lines.get()
    assert line.sku == "SP001"
    assert line.quantity == 2


def test_import_invalid_row_recorded_but_not_written_to_normalized_core(client, editor):
    content = _make_xlsx([
        ["", "", "", "", "SP002", "ไม่มีชื่อลูกค้า", 1, 100, "", "NEW_ORDER", "", "", "", "", "", "", "", "", ""],
    ])
    upload = SimpleUploadedFile("test.xlsx", content)
    resp = client.post(reverse("imports:upload"), {"file": upload}, follow=True)
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "1 แถวไม่ถูกต้อง" in body

    staging_row = StagingImportRow.objects.get(sku="SP002")
    assert staging_row.import_status == "invalid"
    assert not Customer.objects.filter(source_import_row=staging_row).exists()


def test_import_rejects_non_xlsx_file(client, editor):
    upload = SimpleUploadedFile("test.txt", b"not an excel file")
    resp = client.post(reverse("imports:upload"), {"file": upload}, follow=True)
    assert resp.status_code == 200
    assert "รองรับเฉพาะไฟล์ .xlsx" in resp.content.decode("utf-8")


def test_import_merges_same_sku_and_name_across_rows(client, editor):
    content = _make_xlsx([
        ["ORDMERGE", "ทดสอบรวม", "0899500002", "", "SP003", "Same", 2, 200, "2026-07-01", "NEW_ORDER",
         "", "", "", "", "", "", "", "", ""],
        ["ORDMERGE", "ทดสอบรวม", "0899500002", "", "SP003", "Same", 3, 300, "2026-07-01", "NEW_ORDER",
         "", "", "", "", "", "", "", "", ""],
    ])
    upload = SimpleUploadedFile("test.xlsx", content)
    client.post(reverse("imports:upload"), {"file": upload}, follow=True)

    customer = Customer.objects.get(phone1="0899500002")
    order = customer.orders.get(order_no="ORDMERGE")
    line = order.lines.get(sku="SP003", product_name="Same")
    assert line.quantity == 5  # 2 + 3
