"""Content hashing for optimistic concurrency (port of src/import/hashing.ts).

The engine deliberately does not use a revision counter or `updated_at` as its
concurrency token: hashing the exact field set a batch intends to touch means a
rollback returns a document to the same hash (so a retry is not mistaken for a
foreign edit), an unrelated concurrent edit does not block the import, and an
edit to a field the batch wants to change is detected.

`stable_stringify` reproduces the prototype's canonical JSON byte for byte,
including its key order (JavaScript `localeCompare`), because the preview hash
is the digest an operator approves.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any


class _Absent:
    """Marker for "the field did not exist", distinct from an explicit null."""

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT = _Absent()


def is_absent_value(value: Any) -> bool:
    return value is ABSENT or (isinstance(value, dict) and value.get("__absent") is True)


def _collation_key(text: str):
    """Order keys like Node's `a.localeCompare(b)` for identifier-like keys.

    Primary level: digits before letters before everything else, letters
    compared case-insensitively. Tertiary level: lowercase before uppercase.
    Verified against the exported fixtures (tests/fixtures/crmv2/import_hashing.json).
    """
    primary = []
    for ch in text:
        if ch.isdigit():
            primary.append((0, ch))
        elif ch.isalpha():
            primary.append((1, ch.casefold()))
        else:
            primary.append((2, ch))
    tertiary = [ch.swapcase() if ch.isalpha() else ch for ch in text]
    return (primary, tertiary)


def _number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "null"
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    # JavaScript writes exponents as e+21 / e-7, Python as e+21 / e-07.
    return re.sub(r"e([+-])0*(\d)", r"e\1\2", text)


def _iso(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def stable_stringify(value: Any) -> str:
    if is_absent_value(value):
        return '{"__absent":true}'
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        return json.dumps(_iso(value))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        entries = sorted(value.items(), key=lambda item: _collation_key(str(item[0])))
        return "{" + ",".join(
            f"{json.dumps(str(key), ensure_ascii=False)}:{stable_stringify(item)}"
            for key, item in entries
        ) + "}"
    raise TypeError(f"cannot hash a {type(value).__name__}")


def sha256_hex(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def project_fields(data: dict | None, fields) -> dict:
    """Project a document onto explicit field names, marking missing ones ABSENT."""
    return {field: (data[field] if data is not None and field in data else ABSENT) for field in fields}


def hash_fields(data: dict | None, fields) -> str:
    """Hash of the given fields of a document.

    `data is None` means the document does not exist, a meaningful state that
    hashes differently from `{}`.
    """
    return sha256_hex(
        stable_stringify({"exists": data is not None, "fields": project_fields(data, fields)})
    )


def values_equal(a: Any, b: Any) -> bool:
    return stable_stringify(a) == stable_stringify(b)


def is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False
