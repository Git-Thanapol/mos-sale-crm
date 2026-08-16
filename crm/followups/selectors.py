"""Read queries for /followup. Every function takes `user` first and scopes
itself via Followup.objects.for_user() — see crm.core.scoping.

Ported behavior notes vs crm_streamlit/pages/followup.py:
- Ordering matches the legacy intent (next_followup_date asc nulls last,
  priority desc, done-sorts-last, most-recently-updated first) but reads
  `priority_rank` instead of comparing Thai literals, so sort and filter
  finally agree (docs/DECISIONS.md #9).
- The "product" column/filter has no direct analogue on Followup in the
  normalized schema (the legacy row WAS an order line; here Followup is
  one-per-customer). We show/filter on the customer's most recent Order's
  line items instead — a deliberate, documented adaptation, not a bug.
- render_followup_sections' KPI strip is recomputed here as real dataset-wide
  aggregates over the full filtered+scoped queryset (before pagination),
  not the page-local approximation the legacy code used. Strict
  correctness improvement, same four labels.
- The legacy priority tabs are plain toggle buttons with no counts — ported
  as-is, no invented count badges.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Case, F, Q, Value, When
from django.utils import timezone

from crm.catalog.models import Product
from crm.core.identity import normalize_phone
from crm.core.pagination import Page, clamp_page, clamp_page_size
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.core.thai import normalize_priority
from crm.customers.models import Customer
from crm.followups.models import Followup

DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True)
class FollowupFilters:
    lead_status: str = ALL
    followup_status: str = ALL
    priority: str = ALL
    product: str = ALL
    owner: str = ALL
    keyword: str = ""
    date_mode: str = "all"  # all | today | single | range
    date_start: str = ""
    date_end: str = ""

    @classmethod
    def from_query(cls, query: dict) -> "FollowupFilters":
        return cls(
            lead_status=query.get("lead_status", ALL) or ALL,
            followup_status=query.get("followup_status", ALL) or ALL,
            priority=query.get("priority", ALL) or ALL,
            product=query.get("product", ALL) or ALL,
            owner=query.get("owner", ALL) or ALL,
            keyword=(query.get("keyword") or "").strip(),
            date_mode=query.get("date_mode", "all") or "all",
            date_start=query.get("date_start", "") or "",
            date_end=query.get("date_end", "") or "",
        )


def _resolve_date_range(filters: FollowupFilters) -> tuple[str, str]:
    today = timezone.localdate().isoformat()
    if filters.date_mode == "today":
        return today, today
    if filters.date_mode == "single" and filters.date_start:
        return filters.date_start, filters.date_start
    if filters.date_mode == "range" and filters.date_start:
        return filters.date_start, filters.date_end or filters.date_start
    return "", ""


def _base_queryset(user, filters: FollowupFilters):
    qs = (
        Followup.objects.select_related("customer", "customer__last_order")
        .prefetch_related("customer__last_order__lines")
        .for_user(user)
    )

    if filters.lead_status != ALL:
        qs = qs.filter(lead_status=filters.lead_status)
    if filters.followup_status != ALL:
        qs = qs.filter(status=filters.followup_status)
    if filters.priority != ALL:
        qs = qs.filter(priority=normalize_priority(filters.priority))
    if filters.owner != ALL:
        # customer__owner_display, not Followup's own (denormalized,
        # display-only) owner_display copy — same staleness class as
        # STAFF_CODE_PATH, caught by
        # tests/view/test_followup_page.py::test_followup_filters_compose_keyword_with_digits_and_owner
        qs = qs.filter(customer__owner_display=filters.owner)

    keyword = filters.keyword
    phone = normalize_phone(keyword)
    if phone:
        qs = qs.filter(Q(customer__phone1__icontains=phone) | Q(customer__phone2__icontains=phone))
    elif keyword:
        qs = qs.filter(
            Q(customer__customer_name__icontains=keyword)
            | Q(customer__orders__order_no__icontains=keyword)
        ).distinct()

    if filters.product != ALL:
        qs = qs.filter(
            Q(customer__last_order__lines__sku__icontains=filters.product)
            | Q(customer__last_order__lines__product_name__icontains=filters.product)
        ).distinct()

    date_start, date_end = _resolve_date_range(filters)
    if date_start and date_end:
        qs = qs.filter(next_followup_date__range=(date_start, date_end))
    elif date_start:
        qs = qs.filter(next_followup_date=date_start)

    return qs


def followup_page(user, filters: FollowupFilters, page: int, page_size: int) -> Page:
    qs = _base_queryset(user, filters).annotate(
        status_sort=Case(When(status="done", then=Value(1)), default=Value(0))
    ).order_by(
        F("next_followup_date").asc(nulls_last=True),
        "-priority_rank",
        "status_sort",
        "-updated_at",
    )

    page_size = clamp_page_size(page_size)
    total = qs.count()
    total_pages = max(-(-total // page_size), 1) if total else 1
    page = clamp_page(page, total_pages)
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return Page(items=items, page=page, page_size=page_size, total_rows=total)


def followup_summary(user, filters: FollowupFilters) -> dict[str, int]:
    """Dataset-wide KPI strip (due today / overdue / other / done) over the
    full filtered+scoped set — not just the current page.
    """
    qs = _base_queryset(user, filters)
    today = timezone.localdate()
    done = qs.filter(status="done").count()
    due_today = qs.filter(next_followup_date=today).exclude(status="done").count()
    overdue = qs.filter(next_followup_date__lt=today).exclude(status="done").count()
    total = qs.count()
    other = max(total - due_today - overdue - done, 0)
    return {"due_today": due_today, "overdue": overdue, "other": other, "done": done, "total": total}


def followup_filter_options(user) -> dict[str, list[str]]:
    owners = list(
        Customer.objects.for_user(user)
        .exclude(owner_display="")
        .order_by("owner_display")
        .values_list("owner_display", flat=True)
        .distinct()[:500]
    )
    products = list(
        Product.objects.filter(is_active=True, archived_at__isnull=True)
        .order_by("sort_order", "product_name")
        .values_list("product_name", flat=True)
        .distinct()[:1000]
    )
    return {"owners": owners, "products": products}
