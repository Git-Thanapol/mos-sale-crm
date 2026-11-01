"""Upload: files are judged by content, and a batch writes no business data.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_upload.py
"""

import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from django.core.exceptions import PermissionDenied

from crm.customers.models import Customer
from crm.importv2.models import ImportBatch
from crm.importv2.services import upload
from crm.importv2.services.upload import UploadError, create_batch
from crm.orders.models import Order
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]

PROFILES = {p["profileId"]: p for p in json.loads(
    (Path(upload.__file__).resolve().parents[1] / "profiles.json").read_text("utf-8")
)}
CRM = PROFILES["crm-customers"]["headerSignature"]


def crm_row(order="SO-100", name="สมชาย", phone="0812345678", **over):
    row = [
        "26/10/2569", order, "LINE", "SKU1", "สินค้า", 2, "1,000", "COD", "Kerry", "TH1", "http://x", name, phone, "",
        "1 ถ.", "ต", "อ", "กรุงเทพ", "10100", "ก", "", "ข", "CRM", "สูง", "27/10/2569", "โน้ต",
    ]
    for index, value in over.items():
        row[int(index)] = value
    return row


def xlsx(rows, headers=None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(headers if headers is not None else CRM))
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def csv_bytes(rows, headers=None, bom=True):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers if headers is not None else CRM)
    writer.writerows(rows)
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1")


def test_an_xlsx_becomes_a_batch_in_preview_with_no_business_write(editor):
    raw = xlsx([crm_row(), crm_row(order="SO-101")])
    batch = create_batch(actor=editor, file_name="crm_customers_1.xlsx", raw=raw)
    assert batch.status == "PREVIEW" and batch.source_kind == "CRM_CUSTOMERS"
    assert batch.mapping_profile_id == "crm-customers" and batch.mapping_profile_version == 1
    assert batch.row_count == 2 and batch.uploaded_by_id == editor.id
    assert batch.file_sha256 == hashlib.sha256(raw).hexdigest()
    assert bytes(batch.raw_bytes) == raw
    assert batch.validation["ok"] is True
    assert not Customer.objects.exists() and not Order.objects.exists()


def test_a_utf8_csv_with_a_bom_is_accepted(editor):
    batch = create_batch(actor=editor, file_name="crm.csv", raw=csv_bytes([crm_row()]))
    assert batch.source_kind == "CRM_CUSTOMERS" and batch.row_count == 1


def test_a_date_cell_is_read_as_a_day_not_a_serial(editor):
    row = crm_row()
    row[0] = datetime(2026, 10, 26)
    batch = create_batch(actor=editor, file_name="crm.xlsx", raw=xlsx([row]))
    assert batch.validation["ok"] is True


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("evil.xlsx", b"MZ\x90\x00" + b"\x00" * 64),
        ("renamed.xlsx", b"just,text\n1,2\n"),
        ("sheet.csv", xlsx([crm_row()])),
        ("notes.txt", b"a,b\n1,2\n"),
        ("bin.csv", b"a,b\n\x00\x01\x02"),
        ("latin.csv", "ชื่อ\n".encode("tis-620")),
    ],
)
def test_a_file_whose_content_does_not_match_is_refused_and_no_batch_exists(editor, name, content):
    with pytest.raises(UploadError) as raised:
        create_batch(actor=editor, file_name=name, raw=content)
    assert raised.value.code == "UNSUPPORTED_FILE"
    assert not ImportBatch.objects.exists()


def test_an_empty_file_is_refused(editor):
    with pytest.raises(UploadError) as raised:
        create_batch(actor=editor, file_name="a.csv", raw=b"")
    assert raised.value.code == "EMPTY_FILE"


def test_a_header_only_file_is_a_batch_with_no_rows(editor):
    batch = create_batch(actor=editor, file_name="a.csv", raw=csv_bytes([]))
    assert batch.row_count == 0


def test_an_oversized_file_is_refused(editor, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(UploadError) as raised:
        create_batch(actor=editor, file_name="a.csv", raw=csv_bytes([crm_row()]))
    assert raised.value.code == "FILE_TOO_LARGE"


def test_too_many_rows_are_refused(editor, monkeypatch):
    monkeypatch.setattr(upload, "MAX_ROWS", 2)
    with pytest.raises(UploadError) as raised:
        create_batch(actor=editor, file_name="a.csv", raw=csv_bytes([crm_row(order=f"SO-{i}") for i in range(5)]))
    assert raised.value.code == "TOO_MANY_ROWS"


def test_a_file_that_matches_no_known_format_is_refused(editor):
    with pytest.raises(UploadError) as raised:
        create_batch(actor=editor, file_name="a.csv", raw=csv_bytes([["1", "2"]], headers=["x", "y"]))
    assert raised.value.code == "UNKNOWN_FORMAT"


def test_a_shifted_export_still_reaches_validation_and_is_blocked(editor):
    headers = list(CRM)
    headers[3] = "SKU ใหม่"
    batch = create_batch(actor=editor, file_name="a.csv", raw=csv_bytes([crm_row()], headers=headers))
    codes = [i["code"] for i in batch.validation["issues"]]
    assert batch.validation["ok"] is False and "HEADER_SIGNATURE_MISMATCH" in codes


def test_a_recognised_but_unsupported_source_is_blocked_not_half_imported(editor):
    tele = PROFILES["tele-sales-report"]["headerSignature"]
    rows = [[1, "09:30", "ขาย", "SO-1", "ก", 1, 100, "ข"]]
    batch = create_batch(actor=editor, file_name="Tele 26.10.69.csv", raw=csv_bytes(rows, headers=tele))
    assert batch.source_kind == "TELE_SALES_REPORT"
    assert batch.validation["ok"] is False
    assert "SOURCE_NOT_SUPPORTED" in [i["code"] for i in batch.validation["issues"]]


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL, ROLE_ADMIN])
def test_only_an_editor_may_upload(role):
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X1", team_scope=["A"])
    with pytest.raises(PermissionDenied):
        create_batch(actor=user, file_name="a.csv", raw=csv_bytes([crm_row()]))
    assert not ImportBatch.objects.exists()


def test_a_hostile_file_name_is_neutralised(editor):
    batch = create_batch(actor=editor, file_name="../../etc/<script>.csv", raw=csv_bytes([crm_row()]))
    assert "/" not in batch.file_name and "<" not in batch.file_name
