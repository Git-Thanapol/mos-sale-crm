"""November 2026 client files: LiteDetailOrder (43 columns), InoutManageMain, and the
unique-less phone_key they rely on.

The real exports declare `<dimension ref="A1"/>`, which made openpyxl's read-only
mode return a single cell (0 rows, 1 header) - the reason Lite "could not be imported".
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import openpyxl
import pytest

from crm.customers.models import Customer
from crm.customers.services import find_or_create_customer
from crm.importv2.services.commit import commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview
from crm.importv2.services.upload import create_batch, read_table
from crm.orders.models import Order, OrderLine
from tests.db.factories import ROLE_EDITOR, make_customer, make_order, make_user
from tests.db.importv2_helpers import PROFILES

ALL_PROFILES = json.loads((Path(__file__).resolve().parents[2] / "crm/importv2/profiles.json").read_text("utf-8"))

pytestmark = [pytest.mark.db, pytest.mark.django_db]

LITE = next(p for p in ALL_PROFILES if p["profileId"] == "litedetailorder" and p["version"] == 1)
LITE_V2 = next(p for p in ALL_PROFILES if p["profileId"] == "litedetailorder" and p["version"] == 2)
INOUT = PROFILES["inoutmanagemain"]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


def _xlsx(headers, rows, *, broken_dimension=True) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    raw = out.getvalue()
    if not broken_dimension:
        return raw
    # Rewrite <dimension ref="A1:..."/> to "A1", as the client's exporter does.
    source = zipfile.ZipFile(io.BytesIO(raw))
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1"/>', data)
            zout.writestr(item, data)
    return target.getvalue()


def lite_row(order="800001", sku="SP491", qty=2, amount="290.00", name="มาลัย ยั่งยืน", status="จัดส่งแล้ว"):
    row = [None] * len(LITE_V2["headerSignature"])
    for index, value in {
        0: order, 1: "2117948406170591232", 4: status, 8: "04/11/2026", 9: "Facebook",
        17: "ปราจีนบุรี", 18: "เมืองปราจีนบุรี", 19: "โคกไม้ลาย", 20: name, 21: "25230",
        22: "แอดมิน", 31: sku, 32: "ผ้าเช็ดแว่น", 34: "145.00", 35: qty, 36: amount,
    }.items():
        row[index] = value
    return row


def inout_row(order="800001", phone="0897755425", name="มาลัย ยั่งยืน", total=338):
    row = [None] * len(INOUT["headerSignature"])
    for index, value in {0: "2115102770377461760", 1: "จัดส่งแล้ว", 15: order, 48: phone,
                         50: "04/11/2026", 51: name, 55: total, 43: "MiniShop"}.items():
        row[index] = value
    return row


def test_a_sheet_that_declares_a_one_cell_dimension_is_still_read_in_full():
    raw = _xlsx(LITE_V2["headerSignature"], [lite_row("1"), lite_row("2"), lite_row("3")])
    table = read_table(raw, "LiteDetailOrder.xlsx")
    assert len(table["headers"]) == 43 and len(table["rows"]) == 3


def test_the_43_column_lite_export_is_recognised_and_supported(editor):
    batch = create_batch(actor=editor, file_name="LiteDetailOrder.xlsx", raw=_xlsx(LITE_V2["headerSignature"], [lite_row()]))
    assert (batch.source_kind, batch.mapping_profile_version) == ("LITE_DETAIL_ORDER", 2)
    assert "SOURCE_NOT_SUPPORTED" not in [i["code"] for i in batch.validation["issues"]]
    assert batch.validation["ok"] is True


def test_the_old_30_column_lite_layout_stays_refused(editor):
    batch = create_batch(actor=editor, file_name="LiteDetailOrder.xlsx", raw=_xlsx(LITE["headerSignature"], []))
    assert batch.mapping_profile_version == 1
    assert "SOURCE_NOT_SUPPORTED" in [i["code"] for i in batch.validation["issues"]]


def test_lite_joins_the_customer_holding_the_order_number_and_replaces_its_lines(editor):
    customer = make_customer(phone1="0897755425", phone_key="0897755425", customer_name="มาลัย ยั่งยืน")
    order = make_order(customer=customer, order_no="800001", total_amount="1")
    OrderLine.objects.create(order=order, sku="OLD", product_name="old", quantity=1, amount=1)
    raw = _xlsx(LITE_V2["headerSignature"], [lite_row("800001", amount="100.00"), lite_row("800001", sku="SP2", amount="190.00")])
    batch = create_batch(actor=editor, file_name="LiteDetailOrder.xlsx", raw=raw)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    assert Order.objects.filter(order_no="800001").count() == 1  # joined, not duplicated
    assert Customer.objects.count() == 1
    order.refresh_from_db()
    assert sorted(order.lines.values_list("sku", flat=True)) == ["SP2", "SP491"]
    assert float(order.total_amount) == 290.0


def test_lite_orders_with_no_customer_source_are_excluded_and_reported_not_invented(editor):
    raw = _xlsx(LITE_V2["headerSignature"], [lite_row("999001"), lite_row("999002")])
    batch = create_batch(actor=editor, file_name="LiteDetailOrder.xlsx", raw=raw)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    issue = next(i for i in batch.validation["issues"] if i["code"] == "NO_CUSTOMER_SOURCE")
    assert issue["severity"] == "WARNING" and "2 รายการ" in issue["message"]
    assert batch.validation["ok"] is True
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    assert Order.objects.count() == 0 and Customer.objects.count() == 0


def test_inout_creates_the_order_with_its_total_and_no_blank_line(editor):
    raw = _xlsx(INOUT["headerSignature"], [inout_row()])
    batch = create_batch(actor=editor, file_name="InoutManageMain.xlsx", raw=raw)
    assert batch.source_kind == "INOUT_MANAGE_MAIN" and batch.validation["ok"] is True
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    order = Order.objects.get(order_no="800001")
    assert float(order.total_amount) == 338.0
    assert order.lines.count() == 0
    assert order.customer.phone1 == "0897755425"


def test_inout_then_lite_completes_the_order(editor):
    inout = create_batch(actor=editor, file_name="InoutManageMain.xlsx", raw=_xlsx(INOUT["headerSignature"], [inout_row()]))
    build_preview(actor=editor, batch=inout)
    inout.refresh_from_db()
    confirm_batch(actor=editor, batch=inout, preview_hash=inout.preview_hash, operation_key="c1")
    commit_batch(actor=editor, batch=inout, operation_key="m1")

    lite = create_batch(actor=editor, file_name="LiteDetailOrder.xlsx", raw=_xlsx(LITE_V2["headerSignature"], [lite_row(amount="338.00")]))
    build_preview(actor=editor, batch=lite)
    lite.refresh_from_db()
    assert "NO_CUSTOMER_SOURCE" not in [i["code"] for i in lite.validation["issues"]]
    confirm_batch(actor=editor, batch=lite, preview_hash=lite.preview_hash, operation_key="c2")
    commit_batch(actor=editor, batch=lite, operation_key="m2")
    assert Customer.objects.count() == 1 and Order.objects.count() == 1
    assert Order.objects.get().lines.count() == 1


def test_inout_joins_by_order_number_even_when_the_phone_is_shared(editor):
    # Two customers share the phone, which alone would need a person to decide.
    owner = make_customer(phone1="0897755425", phone_key="0897755425#1", customer_name="คนแรก")
    make_customer(phone1="0897755425", phone_key="0897755425#2", customer_name="คนที่สอง")
    make_order(customer=owner, order_no="800001")
    batch = create_batch(actor=editor, file_name="InoutManageMain.xlsx", raw=_xlsx(INOUT["headerSignature"], [inout_row()]))
    build_preview(actor=editor, batch=batch)
    assert batch.conflicts.count() == 0
    assert batch.plans.get().customer_id == owner.pk


def test_uploading_inout_again_asks_nothing_and_adds_nothing(editor):
    def run(key):
        batch = create_batch(actor=editor, file_name="InoutManageMain.xlsx", raw=_xlsx(INOUT["headerSignature"], [inout_row()]))
        build_preview(actor=editor, batch=batch)
        batch.refresh_from_db()
        assert batch.conflicts.count() == 0
        confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key=f"c{key}")
        commit_batch(actor=editor, batch=batch, operation_key=f"m{key}")

    run(1)
    # Another customer on the same phone must not turn the re-upload into a question.
    make_customer(phone1="0897755425", phone_key="0897755425#9", customer_name="คนอื่น")
    run(2)
    assert Order.objects.filter(order_no="800001").count() == 1


def test_lite_first_then_inout_then_lite_again_ends_complete(editor):
    def run(headers, row, name, key):
        batch = create_batch(actor=editor, file_name=name, raw=_xlsx(headers, [row]))
        build_preview(actor=editor, batch=batch)
        batch.refresh_from_db()
        confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key=f"c{key}")
        commit_batch(actor=editor, batch=batch, operation_key=f"m{key}")

    lite = lite_row(amount="338.00")
    run(LITE_V2["headerSignature"], lite, "LiteDetailOrder.xlsx", 1)
    assert Order.objects.count() == 0  # skipped: no customer source yet
    run(INOUT["headerSignature"], inout_row(), "InoutManageMain.xlsx", 2)
    assert Order.objects.get().lines.count() == 0
    run(LITE_V2["headerSignature"], lite, "LiteDetailOrder.xlsx", 3)
    assert Order.objects.get().lines.count() == 1


def test_two_customers_may_share_a_phone_key_and_find_or_create_takes_the_oldest():
    first = make_customer(phone_key="0811111111", phone1="0811111111")
    make_customer(phone_key="0811111111", phone1="0811111111")  # was an IntegrityError before customers.0006
    found, created = find_or_create_customer("0811111111", "", {})
    assert created is False and found.pk == first.pk
