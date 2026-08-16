"""Read queries for /dashboard. Ported from crm_data/dashboard.py, hugely
simplified by the normalized schema: the legacy version needed a
`distinct on (phone_key) ... left join lateral` over the whole table just
to find "the latest row per customer" and its matching follow-up — here
that's just Customer/Followup, already deduplicated by construction.

The FOLLOW sale_type exclusion from revenue (invariant 6) and the
Bangkok-local date grouping are the two rules most worth getting exactly
right; both are enforced here, not left to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, F, Sum
from django.utils import timezone

from crm.core.identity import clean
from crm.core.permissions import can_manage_all
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.customers.models import Customer
from crm.followups.models import Followup
from crm.orders.models import REVENUE_SALE_TYPES, Order, OrderLine


@dataclass(frozen=True)
class DashboardKPIs:
    total_customers: int
    due_today: int
    overdue: int
    interested: int
    won: int
    latest_update: str


def dashboard_kpis(user, start_date, end_date) -> DashboardKPIs:
    """All 5 cards scoped to [start_date, end_date] (the same range the sales
    report below uses), not a fixed "today" — see docs/DECISIONS.md.
    "ค้างติดตาม" (overdue) stays relative to start_date: items already
    overdue as of the start of the selected range.
    """
    customers = Customer.objects.for_user(user)
    followups = Followup.objects.for_user(user)

    total_customers = customers.filter(first_seen_at__date__range=(start_date, end_date)).count()
    due_in_range = (
        followups.filter(next_followup_date__range=(start_date, end_date))
        .exclude(status="done")
        .count()
    )
    overdue = followups.filter(next_followup_date__lt=start_date).exclude(status="done").count()
    interested = followups.filter(
        lead_status="interested", updated_at__date__range=(start_date, end_date)
    ).count()
    won = followups.filter(lead_status="won", updated_at__date__range=(start_date, end_date)).count()

    latest_customer = (
        customers.filter(first_seen_at__date__range=(start_date, end_date))
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    latest_followup = (
        followups.filter(updated_at__date__range=(start_date, end_date))
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    latest = max((t for t in (latest_customer, latest_followup) if t), default=None)

    return DashboardKPIs(
        total_customers=total_customers,
        due_today=due_in_range,
        overdue=overdue,
        interested=interested,
        won=won,
        latest_update=latest.date().isoformat() if latest else "-",
    )


def sales_report_owner_options(user) -> list[str]:
    if not can_manage_all(user):
        return []
    return list(
        Order.objects.filter(sale_type__in=REVENUE_SALE_TYPES)
        .exclude(owner_display="")
        .order_by("owner_display")
        .values_list("owner_display", flat=True)
        .distinct()
    )


def _sales_lines(user, start_date, end_date, owner_filter: str):
    """Filters on order_date (the real business date) rather than
    created_at (DB insert time) — the two only coincide for same-day manual
    orders. Bulk-imported historical orders all share one created_at (import
    wall-clock time), so filtering on created_at made every "today" report
    scan the entire historical import instead of actual today's orders; see
    conversation 2026-07-30. order_date is a plain DateField (no tz
    conversion needed, unlike created_at).
    """
    qs = OrderLine.objects.select_related("order", "order__customer").filter(
        order__sale_type__in=REVENUE_SALE_TYPES,
        order__order_date__gte=start_date,
        order__order_date__lte=end_date,
    )
    if can_manage_all(user):
        owner = clean(owner_filter)
        if owner and owner != ALL:
            qs = qs.filter(order__owner_display=owner)
    else:
        staff_code = clean(getattr(user, "staff_code", ""))
        if staff_code:
            qs = qs.filter(order__staff_code=staff_code)
        else:
            return qs.none()
    return qs.order_by("order__created_at", "order__id")


def sales_summary(user, start_date, end_date, owner_filter: str) -> dict:
    # .order_by() clears the row-display ordering _sales_lines sets for
    # sales_rows()'s benefit — left in place, it leaks into the GROUP BY
    # here (Django includes order_by fields in the grouping), splitting
    # "one group per sale_type" into "one group per order line" and
    # silently wrecking the aggregate. Caught by
    # tests/view/test_reporting.py::test_sales_summary_matches_row_totals.
    lines = _sales_lines(user, start_date, end_date, owner_filter).order_by()
    agg = (
        lines.values("order__sale_type")
        .annotate(sales_amount=Sum("amount"), order_count=Count("order_id", distinct=True))
    )
    summary = {t: {"sales_amount": 0, "order_count": 0, "aov": 0} for t in REVENUE_SALE_TYPES}
    for row in agg:
        t = row["order__sale_type"]
        summary.setdefault(t, {"sales_amount": 0, "order_count": 0, "aov": 0})
        amount = row["sales_amount"] or 0
        count = row["order_count"] or 0
        summary[t] = {
            "sales_amount": amount,
            "order_count": count,
            "aov": (amount / count) if count else 0,
        }

    total_amount = sum(v["sales_amount"] for v in summary.values())
    total_count = sum(v["order_count"] for v in summary.values())
    summary["TOTAL"] = {
        "sales_amount": total_amount,
        "order_count": total_count,
        "aov": (total_amount / total_count) if total_count else 0,
    }
    return summary


def sales_rows(user, start_date, end_date, owner_filter: str, limit: int = 1000) -> list[dict]:
    lines = _sales_lines(user, start_date, end_date, owner_filter)[:limit]
    rows = []
    for line in lines:
        order = line.order
        rows.append({
            "sale_time": timezone.localtime(order.created_at).strftime("%H:%M") if order.created_at else "-",
            "sale_type": order.sale_type,
            "order_id": order.order_no or str(order.id),
            "sku": line.sku,
            "product_name": line.product_name,
            "quantity": line.quantity,
            "amount": line.amount or 0,
            "created_staff": order.uploaded_by or order.owner_display,
            "line_id": line.id,
            "can_delete": order.source_type == "manual",
        })
    return rows


def sales_daily(user, start_date, end_date, owner_filter: str) -> list[dict]:
    # The trailing .order_by("day") happens to replace (not append to)
    # _sales_lines' row-display ordering, so this one isn't affected by
    # the GROUP BY leak described in sales_summary — but .order_by() is
    # called explicitly here up front anyway so correctness doesn't rest
    # on remembering that Django replaces rather than appends.
    lines = _sales_lines(user, start_date, end_date, owner_filter).order_by()
    return list(
        lines.annotate(day=F("order__order_date"))
        .values("day", "order__sale_type")
        .annotate(sales_amount=Sum("amount"))
        .order_by("day")
    )
