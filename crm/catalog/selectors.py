"""Read queries for /products (Product Master admin screen).

Ported from crm_streamlit/pages/products.py + crm_data/products.py. The
status/sort option labels are kept as the exact Thai UI vocabulary from
the legacy page; the underlying SQL is simplified since the normalized
crm_product table already has real is_active/archived_at columns and a
Python-computed sku_number (see Product.save()).

Delete-readiness is a best-effort *report*, never a real delete gate —
same as legacy. There is no FK from OrderLine/StagingImportRow to
Product, so matching is case-insensitive text comparison on sku/
product_name; "no usage found" is only ever "tentative," never "safe."
There is deliberately no hard-delete path anywhere in this app
(invariant 8, docs/DECISIONS.md) — only deactivate and archive/restore.
"""

from __future__ import annotations

from django.db.models import F, Q

from crm.catalog.models import Product
from crm.core.identity import clean
from crm.core.pagination import Page, clamp_page, clamp_page_size
from crm.imports.models import StagingImportRow
from crm.orders.models import OrderLine

DEFAULT_PAGE_SIZE = 10

PRODUCT_STATUS_OPTIONS: dict[str, str] = {
    "สินค้าที่เปิดใช้งาน": "active",
    "สินค้าที่ปิดใช้งาน": "inactive",
    "สินค้าทั้งหมด": "all",
    "สินค้าที่เก็บถาวร": "archived",
}
DEFAULT_STATUS_LABEL = "สินค้าที่เปิดใช้งาน"

PRODUCT_SORT_OPTIONS: dict[str, str] = {
    "SP น้อยไปมาก": "sku_asc",
    "SP มากไปน้อย": "sku_desc",
    "เพิ่มเก่าสุด": "created_asc",
    "เพิ่มล่าสุด": "created_desc",
}
DEFAULT_SORT_LABEL = "SP น้อยไปมาก"


def _apply_status(qs, status_code: str):
    if status_code == "active":
        return qs.filter(is_active=True, archived_at__isnull=True)
    if status_code == "inactive":
        return qs.filter(is_active=False, archived_at__isnull=True)
    if status_code == "archived":
        return qs.filter(archived_at__isnull=False)
    return qs.filter(archived_at__isnull=True)  # "all" — still excludes archived, matches legacy


def _apply_sort(qs, sort_code: str):
    if sort_code == "sku_desc":
        return qs.order_by(F("sku_number").desc(nulls_last=True), "-sku", "-id")
    if sort_code == "created_asc":
        return qs.order_by("created_at", "sku", "id")
    if sort_code == "created_desc":
        return qs.order_by("-created_at", "sku", "-id")
    return qs.order_by(F("sku_number").asc(nulls_last=True), "sku", "id")  # sku_asc, default


def product_options(search: str, limit: int = 20) -> list[dict]:
    """Active-product SKU suggestions for the manual-order form's SKU
    autocomplete — deliberately capped and search-filtered (never the
    legacy's "load every product on every form render").
    """
    term = clean(search)
    qs = _apply_status(Product.objects.all(), "active")
    if term:
        qs = qs.filter(Q(sku__icontains=term) | Q(product_name__icontains=term))
    qs = qs.order_by(F("sku_number").asc(nulls_last=True), "sku", "id")
    return list(qs.values("sku", "product_name")[:limit])


def product_page(
    status_label: str, sort_label: str, search: str, page: int, page_size: int = DEFAULT_PAGE_SIZE
) -> Page:
    status_code = PRODUCT_STATUS_OPTIONS.get(status_label, "active")
    sort_code = PRODUCT_SORT_OPTIONS.get(sort_label, "sku_asc")

    qs = _apply_status(Product.objects.all(), status_code)
    term = clean(search)
    if term:
        qs = qs.filter(Q(sku__icontains=term) | Q(product_name__icontains=term))
    qs = _apply_sort(qs, sort_code)

    page_size = clamp_page_size(page_size)
    total = qs.count()
    total_pages = max(-(-total // page_size), 1) if total else 1
    page = clamp_page(page, total_pages)
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return Page(items=items, page=page, page_size=page_size, total_rows=total)


# --- delete-readiness report ---

STATUS_LABELS: dict[str, str] = {
    "blocked_used": "ห้ามลบ: พบการใช้งาน",
    "tentative_no_usage": "ไม่พบการใช้งานเบื้องต้น: ยังไม่เปิดให้ลบจริง",
    "unsafe_unknown": "ห้ามลบ: ตรวจสอบไม่ครบ/ไม่ปลอดภัย",
}
REASON_LABELS: dict[str, str] = {
    "usage_found": "พบการอ้างอิงในข้อมูลขายหรือออเดอร์",
    "no_usage_found_in_text_checks": "ไม่พบจาก text-based checks ที่ตรวจได้",
    "product_not_found": "ไม่พบสินค้าใน Product Master",
    "blank_sku_and_product_name": "SKU และชื่อสินค้าว่าง จึงตรวจสอบไม่ครบ",
}


def product_delete_readiness(product_ids: list[int]) -> list[dict]:
    products = {p.id: p for p in Product.objects.filter(id__in=product_ids)}
    results = []
    for product_id in product_ids:
        product = products.get(product_id)
        if product is None:
            results.append(_readiness_row(product_id, "", "", "unsafe_unknown", "product_not_found"))
            continue

        sku = clean(product.sku)
        name = clean(product.product_name)
        if not sku and not name:
            results.append(
                _readiness_row(product_id, sku, name, "unsafe_unknown", "blank_sku_and_product_name")
            )
            continue

        sources: list[str] = []
        usage_count = 0
        if sku:
            staging_sku = StagingImportRow.objects.filter(sku__iexact=sku)
            usage_count += _count_and_flag(staging_sku, sources, "imports_sku")
            order_sku = OrderLine.objects.filter(sku__iexact=sku)
            usage_count += _count_and_flag(order_sku, sources, "order_items_sku")
        if name:
            usage_count += _count_and_flag(
                StagingImportRow.objects.filter(product_name__iexact=name), sources, "imports_name"
            )
            usage_count += _count_and_flag(
                OrderLine.objects.filter(product_name__iexact=name), sources, "order_items_name"
            )

        if usage_count:
            row = _readiness_row(product_id, sku, name, "blocked_used", "usage_found")
        else:
            row = _readiness_row(product_id, sku, name, "tentative_no_usage", "no_usage_found_in_text_checks")
        row["usage_count"] = usage_count
        row["usage_sources"] = ", ".join(sources)
        results.append(row)
    return results


def _count_and_flag(qs, sources: list[str], label: str) -> int:
    count = qs.count()
    if count:
        sources.append(label)
    return count


def _readiness_row(product_id, sku, name, status, reason) -> dict:
    return {
        "product_id": product_id,
        "sku": sku,
        "product_name": name,
        "status": status,
        "reason": reason,
        "usage_count": 0,
        "usage_sources": "",
    }
