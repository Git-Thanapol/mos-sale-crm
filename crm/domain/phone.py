"""Thai phone normalisation (port of src/domain/phone.ts).

Phone is an identity *index*, never a primary key: normalisation here is only
used to decide whether two records may refer to the same person. The raw value
is always preserved on the record.
"""

from __future__ import annotations

import math
import re

# JavaScript's \\d is ASCII-only; Python's would also accept Thai digits.
_NON_DIGIT = re.compile(r"[^0-9]")
_MOBILE = re.compile(r"^0[689][0-9]{8}$")
_LANDLINE = re.compile(r"^0[2-7][0-9]{7}$")


def normalize_phone(raw) -> dict:
    """Return `{national, e164, isMobile, isLandline, plausible}` (prototype keys)."""
    source = "" if raw is None else str(raw).strip()
    digits = _NON_DIGIT.sub("", source)

    if digits.startswith("00"):
        digits = digits[2:]  # international 00 prefix
    if digits.startswith("66") and len(digits) >= 11:
        digits = "0" + digits[2:]
    elif len(digits) == 9 and digits[0] in "689":
        digits = "0" + digits

    is_mobile = bool(_MOBILE.match(digits))
    is_landline = bool(_LANDLINE.match(digits))
    return {
        "national": digits,
        "e164": f"+66{digits[1:]}" if len(digits) >= 9 else "",
        "isMobile": is_mobile,
        "isLandline": is_landline,
        "plausible": is_mobile or is_landline,
    }


def digit_edit_distance(a: str, b: str) -> float:
    """Count differing positions of equal-length numbers; infinity otherwise."""
    if not a or not b:
        return math.inf
    if a == b:
        return 0
    if len(a) != len(b):
        return math.inf
    return sum(1 for x, y in zip(a, b) if x != y)


def looks_like_phone(value: str) -> bool:
    """True when the value looks like a phone number rather than an order id."""
    digits = _NON_DIGIT.sub("", value)
    if len(digits) < 9 or len(digits) > 12:
        return False
    return bool(re.match(r"^0[689][0-9]{8}$", digits) or re.match(r"^66[689][0-9]{8}$", digits))
