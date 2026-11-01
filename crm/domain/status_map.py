"""Source status to canonical status axes (port of src/import/status-map.ts).

The mapping is data, derived from the status vocabulary actually present in the
real exports, and is intentionally explicit: an unrecognised status is
reported, never guessed into DELIVERED.
"""

from __future__ import annotations

import re
import unicodedata

_UNKNOWN = {
    "orderStatus": "PENDING_CONFIRMATION",
    "paymentStatus": "UNPAID",
    "shippingStatus": "NOT_SHIPPED",
    "known": False,
    "needsReview": True,
}


def _row(order, payment, shipping, review=False):
    return {
        "orderStatus": order,
        "paymentStatus": payment,
        "shippingStatus": shipping,
        "known": True,
        "needsReview": review,
    }


_TABLE = {
    "สำเร็จ": _row("DELIVERED", "PAID", "DELIVERED"),
    "จัดส่งแล้ว": _row("SHIPPED", "COD_PENDING", "IN_TRANSIT"),
    "ยกเลิก": _row("CANCELLED", "UNPAID", "NOT_SHIPPED"),
    "ตีกลับ": _row("COD_RETURNED", "REFUNDED", "RETURNED"),
    "คืนสินค้า": _row("COD_RETURNED", "REFUNDED", "RETURNED"),
    "ชำระแล้วรอตรวจสอบ": _row("PENDING_CONFIRMATION", "PENDING_VERIFICATION", "NOT_SHIPPED"),
    "รอชำระเงิน": _row("PENDING_CONFIRMATION", "UNPAID", "NOT_SHIPPED"),
    "รอจัดส่ง": _row("WAITING_SHIPMENT", "PAID", "NOT_SHIPPED"),
    # abnormal order: keep it visible but require review rather than assuming
    "ผิดปกติ": _row("PENDING_CONFIRMATION", "UNPAID", "NOT_SHIPPED", review=True),
}


def map_source_status(raw) -> dict:
    if not raw:
        return dict(_UNKNOWN)
    key = unicodedata.normalize("NFC", raw).strip()
    return dict(_TABLE.get(key, _UNKNOWN))


def known_source_statuses() -> list[str]:
    return list(_TABLE)


def map_payment_method(raw) -> str:
    """Payment vocabulary used by the marketplace exports."""
    value = unicodedata.normalize("NFC", raw or "").strip().lower()
    if "cod" in value or "ปลายทาง" in value:
        return "COD_PENDING"
    if "โอน" in value or "transfer" in value or "paid" in value:
        return "PAID"
    if "เก็บเงิน" in value:
        return "COD_PENDING"
    return "UNPAID"


_PICKED = re.compile(r"แสกนรับ|สแกนรับ|รับพัสดุ|pickup|picked", re.IGNORECASE)
_TRANSIT = re.compile(r"กำลัง|ระหว่าง|transit|ขนส่ง", re.IGNORECASE)
_DELIVERED = re.compile(r"สำเร็จ| delivered|ส่งถึง", re.IGNORECASE)
_RETURNING = re.compile(r"ตีกลับ|return", re.IGNORECASE)
_FAILED = re.compile(r"ปัญหา|failed|ล้มเหลว", re.IGNORECASE)
_LABEL = re.compile(r"รอขนส่ง|label", re.IGNORECASE)


def map_shipping_text(raw) -> str:
    """Carrier-provided shipping text to the canonical shipping axis."""
    value = unicodedata.normalize("NFC", raw or "").strip()
    if not value:
        return "NOT_SHIPPED"
    if _PICKED.search(value):
        return "PICKED_UP"
    if _TRANSIT.search(value):
        return "IN_TRANSIT"
    if _DELIVERED.search(value):
        return "DELIVERED"
    if _RETURNING.search(value):
        return "RETURNING"
    if _FAILED.search(value):
        return "FAILED"
    if _LABEL.search(value):
        return "LABEL_CREATED"
    return "NOT_SHIPPED"
