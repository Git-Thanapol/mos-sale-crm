"""Template helpers that reproduce the CRM V2 prototype's small UI rules."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from django import template
from django.utils.html import format_html

from crm.domain.bangkok import BANGKOK_TZ

register = template.Library()

# StatusChip, App.tsx:34-37. Order matters: danger wins over warn over ok.
_DANGER = re.compile(r"OVERDUE|STOPPED|FAILED|BLOCKING")
_WARN = re.compile(r"TODAY|PAUSED|WARNING")
_OK = re.compile(r"COMMITTED|ASSIGNED|DONE")

# th-TH abbreviated months, as Intl.DateTimeFormat('th-TH', {dateStyle: 'medium'}).
_THAI_MONTHS = (
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
)
_DASH = "—"


def chip_tone(value: str) -> str:
    value = value or ""
    if _DANGER.search(value):
        return "danger"
    if _WARN.search(value):
        return "warn"
    if _OK.search(value):
        return "ok"
    return "neutral"


@register.simple_tag
def v2_chip(value):
    value = "" if value is None else str(value)
    return format_html('<span class="chip {}">{}</span>', chip_tone(value), value)


def _to_bangkok(moment):
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            raise ValueError("naive datetime: refusing to guess a timezone")
        return moment.astimezone(BANGKOK_TZ)
    return moment


@register.filter
def bangkok(moment):
    """'26 ต.ค. 2569 10:00': Bangkok wall clock, Buddhist year, unpadded day."""
    if moment is None or moment == "":
        return _DASH
    local = _to_bangkok(moment)
    text = f"{local.day} {_THAI_MONTHS[local.month - 1]} {local.year + 543}"
    if isinstance(local, datetime):
        text += f" {local:%H:%M}"
    return text


@register.filter
def bangkok_short(moment):
    """'10/10/69': the last-order-age column's dd/mm/yy in the Buddhist year."""
    if moment is None or moment == "":
        return _DASH
    local = _to_bangkok(moment)
    if isinstance(local, date):
        return f"{local.day:02d}/{local.month:02d}/{(local.year + 543) % 100:02d}"
    return _DASH


@register.filter
def baht(amount):
    """'฿1,428' like toLocaleString(); a missing amount is '฿—'."""
    if amount is None or amount == "":
        return "฿—"
    text = f"{Decimal(amount):,.2f}".rstrip("0").rstrip(".")
    return f"฿{text}"


@register.filter
def order_state(order):
    """The mapped machine status when this repo's vocabulary has one, else the raw text."""
    from crm.leads.order_status import to_machine_status

    return to_machine_status(order.order_status) or order.order_status or "UNKNOWN"
