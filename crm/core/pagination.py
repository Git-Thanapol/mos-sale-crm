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
