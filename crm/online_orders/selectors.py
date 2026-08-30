"""Read queries for /online-orders. Mirrors crm/customers/selectors.py's
shape (filters dataclass + paginated selector + filter-option lists).

Not staff-scoped: neither source file carries a staff_code, only a Thai
display name (พนักงานขาย / sales_staff), so StaffScopedQuerySet has nothing
to key on here. Page access itself is gated by can_view_followup at the
view layer (see crm/online_orders/views.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import F, Q

from crm.core.identity import normalize_phone
from crm.core.pagination import Page, clamp_page, clamp_page_size
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.online_orders.models import OnlineOrder

DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True)
class OnlineOrderFilters:
    status: str = ALL
    staff: str = ALL
    shop: str = ALL
    carrier: str = ALL
    keyword: str = ""

    @classmethod
    def from_query(cls, query: dict) -> "OnlineOrderFilters":
        return cls(
            status=query.get("status", ALL) or ALL,
            staff=query.get("staff", ALL) or ALL,
            shop=query.get("shop", ALL) or ALL,
            carrier=query.get("carrier", ALL) or ALL,
            keyword=(query.get("keyword") or "").strip(),
        )


def _base_queryset(filters: OnlineOrderFilters):
    qs = OnlineOrder.objects.select_related("customer").prefetch_related("lines")

    if filters.status != ALL:
        qs = qs.filter(order_status=filters.status)
    if filters.staff != ALL:
        qs = qs.filter(sales_staff=filters.staff)
    if filters.shop != ALL:
        qs = qs.filter(shop_name=filters.shop)
    if filters.carrier != ALL:
        qs = qs.filter(carrier=filters.carrier)

    keyword = filters.keyword
    if keyword:
        digits = normalize_phone(keyword)
        q = (
            Q(online_order_no__icontains=keyword)
            | Q(internal_order_no__icontains=keyword)
            | Q(tracking_no__icontains=keyword)
            | Q(recipient_name__icontains=keyword)
            | Q(customer_name__icontains=keyword)
        )
        if digits:
            q |= Q(phone__icontains=digits)
        qs = qs.filter(q)

    return qs


def online_order_page(filters: OnlineOrderFilters, page: int, page_size: int) -> Page:
    qs = _base_queryset(filters).order_by(F("ordered_at").desc(nulls_last=True), "-id")

    page_size = clamp_page_size(page_size)
    total = qs.count()
    total_pages = max(-(-total // page_size), 1) if total else 1
    page = clamp_page(page, total_pages)
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return Page(items=items, page=page, page_size=page_size, total_rows=total)


def online_order_filter_options() -> dict[str, list[str]]:
    def distinct(field: str) -> list[str]:
        return list(
            OnlineOrder.objects.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct()[:500]
        )

    return {
        "statuses": distinct("order_status"),
        "staff": distinct("sales_staff"),
        "shops": distinct("shop_name"),
        "carriers": distinct("carrier"),
    }


def online_orders_for_customer(customer, limit: int = 20):
    return customer.online_orders.prefetch_related("lines").order_by("-ordered_at", "-id")[:limit]
