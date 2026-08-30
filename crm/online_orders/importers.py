"""Parsing for the two online-order platform exports (InoutManageMain +
AllLiteDetailOrder — see feedback/Update_import_file/issue.md for the
requested column list, columns identified by letter).

Column letters are read POSITIONALLY, not by header name. Two verified
hazards in the real export rule out name-based lookup: InoutManageMain has
three different columns literally headed "ผู้รับ" (recipient name AC,
province AS, phone AW — the source export mislabels the latter two), and
AllLiteDetailOrder's shop header carries a trailing zero-width space
("ร้านค้า​"). A header-signature check on a handful of unambiguous
anchor columns guards that the layout is still what we expect; it does not
drive the field mapping.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from openpyxl.utils import column_index_from_string

from crm.core.identity import BANGKOK_TZ, clean, normalize_phone

# field -> column letter, from the issue's requested column list
MAIN_COLUMNS = {
    "online_order_no": "A",
    "carrier": "H",
    "tracking_no": "I",
    "internal_order_no": "P",
    "shop_name": "R",
    "address": "AB",
    "recipient_name": "AC",
    "province": "AS",  # mislabeled "ผู้รับ" in the source header
    "district": "AT",
    "subdistrict": "AU",
    "postal_code": "AV",
    "phone": "AW",  # mislabeled "ผู้รับ" in the source header
    "customer_name": "AZ",
    "total_amount": "BD",
}

DETAIL_COLUMNS = {
    "internal_order_no": "A",
    "online_order_no": "B",
    "order_status": "D",
    "carrier": "E",
    "tracking_no": "F",
    "ordered_at": "G",
    "shop_name": "H",
    "total_amount": "J",
    "province": "K",
    "district": "L",
    "subdistrict": "M",
    "recipient_name": "N",
    "postal_code": "O",
    "sales_staff": "P",
    "payment_method": "R",
    "upsell_staff": "S",  # line-level, not order-level — varies within an order
    "upsell_amount": "T",  # line-level, not order-level — varies within an order
    "sku": "V",
    "product_name": "W",
    "quantity": "Z",
}

# Anchor columns used only to confirm the layout still matches, never used
# to locate a data column. Deliberately avoid the mislabeled/ZWSP columns.
MAIN_SIGNATURE = {
    "A": "หมายเลขคำสั่งซื้อออนไลน์",
    "H": "บริษัทขนส่ง",
    "P": "หมายเลขออเดอร์ภายใน",
    "AV": "รหัสไปรษณีย์ของผู้รับ",
}
DETAIL_SIGNATURE = {
    "A": "หมายเลขออเดอร์ภายใน",
    "B": "หมายเลขคำสั่งซื้อออนไลน์",
    "D": "สถานะคำสั่งซื้อ",
    "V": "รหัสสินค้า",
}


def _letter_index(letter: str) -> int:
    return column_index_from_string(letter) - 1


# Zero-width space/non-joiner/joiner + BOM — AllLiteDetailOrder's shop
# header is literally "ร้านค้า" + U+200B (verified against the sample file).
_INVISIBLE_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")


def _norm_header(value: object) -> str:
    text = clean(value)
    for ch in _INVISIBLE_CHARS:
        text = text.replace(ch, "")
    return " ".join(text.split())


def _matches_signature(header_row, signature: dict[str, str]) -> bool:
    for letter, expected in signature.items():
        idx = _letter_index(letter)
        if idx >= len(header_row) or _norm_header(header_row[idx]) != expected:
            return False
    return True


def is_online_main_format(header_row) -> bool:
    return _matches_signature(header_row, MAIN_SIGNATURE)


def is_online_detail_format(header_row) -> bool:
    return _matches_signature(header_row, DETAIL_SIGNATURE)


def _id_like(value: object) -> str:
    """Excel sometimes stores an id-like column (tracking no, postal code,
    internal order no) as a number — strip the trailing '.0' a plain
    clean(value) would otherwise leave in. Same rule as
    crm.imports.services._wide_id_like.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean(value)


def to_decimal(value: object) -> Decimal | None:
    text = clean(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_ordered_at(value: object):
    """'2025-12-30 23:11:52' (or a real datetime cell) -> aware datetime in
    BANGKOK_TZ, or None. crm.core.identity.parse_date is the wrong helper
    here — it returns a date-only ISO string and drops the time.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        text = clean(value)
        if not text:
            return None
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BANGKOK_TZ)
    return dt


def _row_getter(header_row, columns: dict[str, str]):
    idx = {name: _letter_index(letter) for name, letter in columns.items()}

    def get(row, name):
        i = idx[name]
        return row[i] if i < len(row) else None

    return get


def iter_main_records(data_rows):
    """Yields (record, row_number) for InoutManageMain — one row per order."""
    get = _row_getter(None, MAIN_COLUMNS)
    for row_number, row in enumerate(data_rows, start=2):
        online_order_no = clean(get(row, "online_order_no"))
        if not online_order_no:
            continue
        yield {
            "online_order_no": online_order_no,
            "internal_order_no": _id_like(get(row, "internal_order_no")),
            "carrier": clean(get(row, "carrier")),
            "tracking_no": _id_like(get(row, "tracking_no")),
            "shop_name": clean(get(row, "shop_name")),
            "address": clean(get(row, "address")),
            "recipient_name": clean(get(row, "recipient_name")),
            "province": clean(get(row, "province")),
            "district": clean(get(row, "district")),
            "subdistrict": clean(get(row, "subdistrict")),
            "postal_code": _id_like(get(row, "postal_code")),
            "phone": normalize_phone(get(row, "phone")),
            "customer_name": clean(get(row, "customer_name")),
            "total_amount": to_decimal(get(row, "total_amount")),
        }, row_number


def iter_detail_rows(data_rows):
    """Yields (record, row_number) for AllLiteDetailOrder — one row per
    order LINE. Grouping into orders + line ordinals happens in
    crm.online_orders.services, not here.
    """
    get = _row_getter(None, DETAIL_COLUMNS)
    for row_number, row in enumerate(data_rows, start=2):
        online_order_no = clean(get(row, "online_order_no"))
        if not online_order_no:
            continue
        yield {
            "online_order_no": online_order_no,
            "internal_order_no": _id_like(get(row, "internal_order_no")),
            "order_status": clean(get(row, "order_status")),
            "carrier": clean(get(row, "carrier")),
            "tracking_no": _id_like(get(row, "tracking_no")),
            "ordered_at": parse_ordered_at(get(row, "ordered_at")),
            "shop_name": clean(get(row, "shop_name")),
            "total_amount": to_decimal(get(row, "total_amount")),
            "province": clean(get(row, "province")),
            "district": clean(get(row, "district")),
            "subdistrict": clean(get(row, "subdistrict")),
            "recipient_name": clean(get(row, "recipient_name")),
            "postal_code": _id_like(get(row, "postal_code")),
            "sales_staff": clean(get(row, "sales_staff")),
            "payment_method": clean(get(row, "payment_method")),
            "upsell_staff": clean(get(row, "upsell_staff")),
            "upsell_amount": to_decimal(get(row, "upsell_amount")),
            "sku": clean(get(row, "sku")),
            "product_name": clean(get(row, "product_name")),
            "quantity": get(row, "quantity"),
        }, row_number
