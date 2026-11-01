"""Thai date and Bangkok time helpers (port of the import-facing parts of
src/domain/time.ts). Times are epoch milliseconds, as in the prototype.

Several results deliberately reproduce JavaScript quirks that the prototype's
behaviour (and therefore the parity fixtures) depends on: a four-digit year
below 100 means 19xx (`Date.UTC`), `%` keeps the sign of its left operand, and
`Math.round` rounds halves up. See tests/unit/test_import_mapping_parity.py.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

BANGKOK_OFFSET_MS = 7 * 60 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_EXCEL_EPOCH_MS = -2209161600000  # Date.UTC(1899, 11, 30)

_THAI_DATE = re.compile(r"^([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})$")
_TIME = re.compile(r"^([0-9]{1,2}):([0-9]{2})$")
_ISO_DAY = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")


def _days_since_epoch(year: int, month: int, day: int) -> int:
    """Date.UTC day count, including its 0..99 -> 1900..1999 year rule and
    day-of-month rollover (Feb 30 is March 2)."""
    if 0 <= year <= 99:
        year += 1900
    return date(year, month, 1).toordinal() - _EPOCH_ORDINAL + (day - 1)


def bangkok_day_key(ts: int) -> str:
    """`YYYY-MM-DD` for the Bangkok calendar day containing `ts`."""
    days = (ts + BANGKOK_OFFSET_MS) // DAY_MS
    return (date(1970, 1, 1) + timedelta(days=days)).isoformat()


def _js_mod(a: int, b: int) -> int:
    return a - b * int(a / b)


def start_of_bangkok_day(ts: int) -> int:
    """Start of the Bangkok day containing `ts`. Reproduces the prototype's
    arithmetic, whose `%` truncates toward zero for pre-1970 instants."""
    local = ts + BANGKOK_OFFSET_MS
    return local - _js_mod(local, DAY_MS) - BANGKOK_OFFSET_MS


def parse_thai_date(text: str) -> int | None:
    """`31/08/2569` (Buddhist era) or `31/08/2026` to the start of that Bangkok
    day, or None when the input is not a date."""
    trimmed = text.strip()
    if not trimmed:
        return None
    match = _THAI_DATE.match(trimmed)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year > 2400:
        year -= 543  # Buddhist era -> Gregorian
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    utc_midnight = _days_since_epoch(year, month, day) * DAY_MS
    # Reject a day that rolled into the next month (31/04, 29/02 off a leap year).
    rolled = date(1970, 1, 1) + timedelta(days=utc_midnight // DAY_MS)
    if rolled.month != month or rolled.day != day:
        return None
    return utc_midnight - BANGKOK_OFFSET_MS


def excel_serial_to_millis(serial) -> int | None:
    """Excel serial date (1900 system) to epoch millis, as Excel and SheetJS do."""
    if not isinstance(serial, (int, float)) or isinstance(serial, bool):
        return None
    if not math.isfinite(serial) or serial <= 0:
        return None
    return math.floor(_EXCEL_EPOCH_MS + serial * DAY_MS + 0.5)  # Math.round: halves up


def bangkok_time_on_day(day_key: str, hhmm: str) -> int | None:
    """`HH:mm` (the Tele sales report) onto a Bangkok day key, or None."""
    match = _TIME.match(hhmm.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    iso = _ISO_DAY.match(day_key)
    if not iso:
        return None
    year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    midnight = _days_since_epoch(year, month, day) * DAY_MS
    return midnight + hours * 3_600_000 + minutes * 60_000 - BANGKOK_OFFSET_MS
