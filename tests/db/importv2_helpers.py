"""Builders for import tests: CRM_CUSTOMERS workbooks and batches."""

from __future__ import annotations

import io
import json
from pathlib import Path

import openpyxl

from crm.importv2.services import upload
from crm.importv2.services.upload import create_batch

PROFILES = {
    p["profileId"]: p
    for p in json.loads((Path(upload.__file__).resolve().parents[1] / "profiles.json").read_text("utf-8"))
}
CRM_HEADERS = PROFILES["crm-customers"]["headerSignature"]

# Column positions in the crm-customers profile.
DATE, ORDER, CHANNEL, SKU, PRODUCT, QTY, PRICE, PAYMENT, CARRIER, TRACKING, URL = range(11)
NAME, PHONE, PHONE_ALT, ADDRESS, SUBDISTRICT, DISTRICT, PROVINCE, POSTAL = range(11, 19)
SELLER, UPSELL, CARETAKER, TEAM, PRIORITY, FOLLOW_DATE, FOLLOW_NOTE = range(19, 26)


def crm_row(order="SO-100", name="สมชาย", phone="0812345678", **over):
    """One line-level CRM export row. `over` maps column index (as str) to value."""
    row = [
        "26/10/2569", order, "LINE", "SKU1", "สินค้า", 2, "1,000", "COD", "Kerry", "TH1", "http://x", name, phone, "",
        "1 ถ.", "ต", "อ", "กรุงเทพ", "10100", "", "", "", "CRM Team", "สูง", "27/10/2569", "โน้ต",
    ]
    for index, value in over.items():
        row[int(index)] = value
    return row


def xlsx(rows, headers=None) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(headers if headers is not None else CRM_HEADERS))
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def batch_from(actor, rows, name="crm_customers.xlsx"):
    return create_batch(actor=actor, file_name=name, raw=xlsx(rows))
