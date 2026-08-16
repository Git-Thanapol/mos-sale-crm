"""Customer/order identity rules — byte-exact port of
crm_streamlit/crm_data/common.py. The 6 dedupe-key digests pinned in
tests/unit/test_identity.py are a hard contract: do not "improve" this
module without re-deriving and re-pinning those hashes.

Two intentional divergences from the legacy module, since this codebase has
no pandas dependency (Excel parsing here uses openpyxl directly, which
returns None for empty cells, never a pandas NaN/NA sentinel):

1. clean(pandas.NA) returned the literal string "<NA>" in the legacy code
   (pd.NA is not a float, so it skipped the NaN guard and fell through to
   str().strip()). That input can't occur here, so this path is dropped.
2. parse_date() no longer uses pandas.to_datetime's permissive parser.
   It handles dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd, and dd/mm/yy. If the
   Phase 2 importer encounters a real-world date format outside this set,
   extend the format list here rather than re-adding pandas.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

PHONE_RULE_MESSAGE = "ต้องเป็นตัวเลข 10 หลัก ขึ้นต้นด้วย 0 และห้ามมีสัญลักษณ์"
PHONE_REQUIRE_ONE_MESSAGE = "กรุณากรอกเบอร์โทรหรือเบอร์สำรอง"


def clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.upper() in {"NULL", "NONE", "NAN", "NAT"} else text


def normalize_phone(value: object) -> str:
    return "".join(ch for ch in clean(value) if ch.isdigit())


def make_dedupe_key(order_id: str, phone1: str, phone2: str, tracking_no: str) -> str:
    text = "|".join(
        [clean(order_id), normalize_phone(phone1), normalize_phone(phone2), clean(tracking_no)]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def phone_key(phone1: object, phone2: object, fallback_id: object) -> str:
    """The customer-identity rule. NOT transitive by design — see
    docs/legacy/data-layer-report.md "phone_key identity is not transitive".
    (0899999999, 0811111111) and (0811111111, NULL) merge to the same key;
    (0899999999, NULL) does not merge with either of those. Do not change
    this into a phone-graph union-find.
    """
    p1 = clean(phone1)
    p2 = clean(phone2)
    if p1 and p2:
        return min(p1, p2)
    return p1 or p2 or f"row:{fallback_id}"


def validate_phone_value(value: object, label: str) -> str:
    text = clean(value)
    if not text:
        return ""
    if not text.isdigit() or len(text) != 10 or not text.startswith("0"):
        return f"{label}ใส่ไม่ถูกต้อง {PHONE_RULE_MESSAGE}"
    return ""


def validate_phone_pair(phone1: object, phone2: object, require_one: bool = True) -> list[str]:
    first = clean(phone1)
    second = clean(phone2)
    if require_one and not first and not second:
        return [PHONE_REQUIRE_ONE_MESSAGE]

    errors = []
    for value, label in ((first, "เบอร์โทร"), (second, "เบอร์สำรอง")):
        error = validate_phone_value(value, label)
        if error:
            errors.append(error)
    return errors


def to_number(value: object) -> float | None:
    text = clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: object) -> str | None:
    """Dayfirst-parsed ISO date string, or None. Mirrors the legacy
    pandas.to_datetime(..., dayfirst=True) behavior without a pandas
    dependency in the new codebase.

    openpyxl hands back real date/datetime objects for Excel date cells
    (never a string), so those are formatted directly. String input covers
    manual-order form fields and CSV-style paste, handled dayfirst.
    """
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()

    text = clean(value)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def new_batch_id() -> str:
    return str(uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
