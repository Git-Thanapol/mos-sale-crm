"""Read queries for /team-sales. Ported from crm_data/team_sales.py.

Two simplifications the normalized schema buys over the legacy SQL:

- Manual vs imported rows: legacy reconstructed this with a 3-way OR
  (`source_type='manual' or source_file_name='manual_order' or
  raw_data->>'source'='manual_order'`) because the column didn't reliably
  exist. Order.source_type is a real, always-populated field here, so it's
  just `order__source_type="manual"`.
- Quantity fallback: legacy coalesced `d.quantity` against two different
  raw-jsonb-parsed reads (english/Thai header) defaulting to 1, because
  quantity wasn't guaranteed populated. OrderLine.quantity is a required
  PositiveIntegerField, so `Sum("quantity")` needs no fallback chain.

What's kept identical to the legacy behavior (see docs/legacy/data-layer-
report.md §2.3, and the divergence table against the dashboard's sales
report noted there): team totals are manual-orders-only and unscoped by
staff_code/owner (this page is EDITOR-only, so "unscoped" is intentional,
not a bug); Top 10 uses an INNER join to the assignment (unassigned rows
silently excluded, matching the legacy empty-state message); attribution
is by Order.uploaded_by (an email) matched to the team assignment that was
open at Order.created_at — never "the team as of right now".
"""

from __future__ import annotations

from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Lower, Trim

from crm.accounts.models import User
from crm.orders.models import REVENUE_SALE_TYPES, OrderLine
from crm.teams.models import TeamAssignment

# {"CRM_TEAM": "CRM Team", "UPSELL_TEAM": "Upsell Team"}
TEAM_CODES: dict[str, str] = dict(TeamAssignment.TEAM_CHOICES)
UNASSIGNED_TEAM_CODE = "UNASSIGNED"
UNASSIGNED_TEAM_NAME = "ยังไม่เลือกทีม"


def _validate_sale_type(sale_type_filter: str | None) -> list[str]:
    if not sale_type_filter:
        return list(REVENUE_SALE_TYPES)
    if sale_type_filter not in REVENUE_SALE_TYPES:
        raise ValueError("sale_type_filter must be NEW_ORDER, UPSELL, or None")
    return [sale_type_filter]


def _scoped_lines(start_date, end_date, sale_type_filter: str | None):
    sale_types = _validate_sale_type(sale_type_filter)
    lines = (
        OrderLine.objects.select_related("order")
        .filter(
            order__source_type="manual",
            order__sale_type__in=sale_types,
            order__created_at__date__gte=start_date,
            order__created_at__date__lte=end_date,
        )
        .annotate(uploaded_by_norm=Lower(Trim("order__uploaded_by")))
    )
    team_at_order = (
        TeamAssignment.objects.filter(
            user__email=OuterRef("uploaded_by_norm"),
            effective_from__lte=OuterRef("order__created_at"),
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=OuterRef("order__created_at")))
        .order_by("-effective_from")
    )
    return lines.annotate(
        team_code=Subquery(team_at_order.values("team_code")[:1]),
        assignment_user_id=Subquery(team_at_order.values("user_id")[:1]),
    )


def team_sales_summary(start_date, end_date, sale_type_filter: str | None = None) -> dict:
    lines = _scoped_lines(start_date, end_date, sale_type_filter).order_by()
    agg = lines.values("team_code").annotate(
        sales_amount=Sum("amount"),
        order_count=Count("order_id", distinct=True),
        row_count=Count("id"),
    )

    teams = {
        code: {"team_code": code, "team_name": name, "order_count": 0, "sales_amount": 0, "row_count": 0}
        for code, name in TEAM_CODES.items()
    }
    unassigned = {
        "team_code": UNASSIGNED_TEAM_CODE, "team_name": UNASSIGNED_TEAM_NAME,
        "order_count": 0, "sales_amount": 0, "row_count": 0,
    }

    for row in agg:
        code = row["team_code"]
        entry = {
            "team_code": code or UNASSIGNED_TEAM_CODE,
            "team_name": TEAM_CODES.get(code, UNASSIGNED_TEAM_NAME),
            "order_count": row["order_count"] or 0,
            "sales_amount": row["sales_amount"] or 0,
            "row_count": row["row_count"] or 0,
        }
        if code in teams:
            teams[code] = entry
        else:
            unassigned = entry

    return {
        "teams": list(teams.values()),
        "unassigned": unassigned,
        "unassigned_count": unassigned["row_count"],
    }


def team_top_products(
    start_date, end_date, team_code: str | None = None, sale_type_filter: str | None = None, limit: int = 10
) -> list[dict]:
    if not (1 <= limit <= 100):
        raise ValueError("limit must be between 1 and 100")

    lines = _scoped_lines(start_date, end_date, sale_type_filter).order_by()
    if team_code:
        if team_code not in TEAM_CODES:
            raise ValueError("team_code must be CRM_TEAM, UPSELL_TEAM, or None")
        lines = lines.filter(team_code=team_code)
    else:
        # Legacy Top 10 INNER JOINs the assignment — unassigned rows drop
        # out entirely here, unlike the summary above which LEFT JOINs and
        # buckets them. That asymmetry is what the empty-state message
        # ("...เพราะยังไม่ได้จัด User เข้าทีม") is about; preserved as-is.
        lines = lines.exclude(team_code__isnull=True)

    rows = (
        lines.values("sku", "product_name")
        .annotate(total_quantity=Sum("quantity"), order_count=Count("order_id", distinct=True))
        .order_by("-total_quantity", "product_name", "sku")[:limit]
    )
    return list(rows)


def team_assignment_users() -> list[dict]:
    users = User.objects.filter(is_active=True).order_by("staff_name", "email")
    current_by_user_id = {
        a.user_id: a for a in TeamAssignment.objects.filter(effective_to__isnull=True)
    }
    result = []
    for u in users:
        current = current_by_user_id.get(u.id)
        result.append({
            "user_id": u.id,
            "email": u.email,
            "role": u.role,
            "staff_name": u.staff_name,
            "current_team_code": current.team_code if current else None,
            "current_team_name": TEAM_CODES.get(current.team_code) if current else None,
        })
    return result
