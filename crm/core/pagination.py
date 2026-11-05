"""Numeric pager matching the legacy caption format exactly:
"หน้า 6 / 1,990 · ทั้งหมด 19,891 รายการ"

Offset pagination, kept deliberately — see plan §5.2. Page size is a closed
set so a crafted querystring can't force an expensive scan.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_PAGE_SIZES = (10, 20, 50)
DEFAULT_PAGE_SIZE = 10


def clamp_page_size(value: int | str | None) -> int:
    try:
        size = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in ALLOWED_PAGE_SIZES else DEFAULT_PAGE_SIZE


@dataclass(frozen=True)
class Page:
    items: list
    page: int
    page_size: int
    total_rows: int

    @property
    def total_pages(self) -> int:
        if self.total_rows == 0:
            return 1
        return -(-self.total_rows // self.page_size)  # ceil div

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def window(self, radius: int = 2) -> list[int | None]:
        """Page numbers to render, with None standing in for an ellipsis.
        e.g. [1, None, 4, 5, 6, 7, 8, None, 1990]
        """
        total = self.total_pages
        if total <= (radius * 2) + 3:
            return list(range(1, total + 1))

        pages: list[int | None] = [1]
        lo = max(2, self.page - radius)
        hi = min(total - 1, self.page + radius)
        if lo > 2:
            pages.append(None)
        pages.extend(range(lo, hi + 1))
        if hi < total - 1:
            pages.append(None)
        pages.append(total)
        return pages


def clamp_page(value: int | str | None, total_pages: int) -> int:
    try:
        page = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return min(max(page, 1), max(total_pages, 1))


# --- keyset pagination -------------------------------------------------------
# The offset pager above stays for the legacy follow-up list. The Lead
# operational queues use keyset instead, because
# 06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md requires "offset pagination count =
# 0 for large operational lists" and forbids per-page COUNT(*) on them. See
# docs/DECISIONS.md for why both coexist.

import base64
import json
from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class KeysetPage:
    items: list
    #: Opaque cursor for the next page, or None at the end.
    next_cursor: str | None
    has_more: bool


def encode_cursor(values: tuple) -> str:
    """Opaque, URL-safe cursor. Opaque so callers cannot build one by hand and
    couple themselves to the sort key.
    """
    raw = json.dumps(list(values), separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str | None) -> list | None:
    """Decode a cursor, or None when it is absent or unusable.

    Never raises. A cursor travels in a querystring, where it gets truncated,
    re-encoded and hand-edited; degrading to "start from the beginning" is
    correct behaviour, not a swallowed error.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(decoded)
    except Exception:
        return None
    if not isinstance(value, list) or len(value) != 2:
        return None
    if not isinstance(value[1], int) or isinstance(value[1], bool):
        return None
    if value[0] is not None and not isinstance(value[0], str):
        return None
    if value[0] is not None:
        # Shape validation is not enough: base64 of ["not-a-date", 5] passes
        # every check above and then raises in the caller's fromisoformat,
        # which is a 500 on a hand-edited querystring. Parse it here so the
        # "never raises" contract actually holds.
        from datetime import datetime

        try:
            datetime.fromisoformat(value[0])
        except ValueError:
            return None
    return value


def decode_cursor_parts(raw: str | None, kinds: tuple[str, ...]) -> list | None:
    """Decode a cursor of a known shape into typed values, or None.

    `kinds` has one entry per cursor element: "int", or "datetime?" for an
    ISO-8601 string that may also be null. Like decode_cursor this never
    raises and treats any mismatch (wrong length, wrong type, unparseable
    date) as "no cursor", because a cursor arrives in a hand-editable
    querystring. Datetimes come back parsed.
    """
    from datetime import datetime

    if not raw or not raw.strip():
        return None
    text = raw.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return None
    if not isinstance(value, list) or len(value) != len(kinds):
        return None
    parsed: list = []
    for kind, item in zip(kinds, value):
        if kind == "int":
            if not isinstance(item, int) or isinstance(item, bool):
                return None
            parsed.append(item)
        elif kind == "datetime?":
            if item is None:
                parsed.append(None)
            elif isinstance(item, str):
                try:
                    parsed.append(datetime.fromisoformat(item))
                except ValueError:
                    return None
            else:
                return None
        else:
            raise ValueError(f"unknown cursor kind {kind!r}")
    return parsed
