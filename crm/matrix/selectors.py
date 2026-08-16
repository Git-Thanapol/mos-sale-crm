"""Read queries for /daily-sales-matrix/. Pivots the same manual-orders-only,
effective-dated-team data crm.teams.selectors.team_sales_summary() uses
(reused via _scoped_lines, not reimplemented) into one row per calendar day
of the chosen month, one column per current team member.

Columns are the CURRENT team roster (today's TeamAssignment), not a
per-day-varying roster — a static HTML table can't grow/shrink columns
mid-month, and the legacy pilot's screenshot only ever shows one fixed
column set for the whole month.

Cell highlight thresholds (confirmed with the customer 2026-07-31, see
conversation — no team-level sales target exists, only these per-person
ones):
  UPSELL member day total > 4,500 -> green, > 3,000 -> yellow
  CRM member day total > 11,000 -> green
Team/day subtotal columns are a plain sum, no highlight of their own.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Sum
from django.db.models.functions import TruncDate

from crm.accounts.models import User
from crm.matrix.models import Holiday
from crm.teams.models import TeamAssignment
from crm.teams.selectors import _scoped_lines, team_sales_summary

THAI_WEEKDAY = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
BE_YEAR_OFFSET = 543

UPSELL_YELLOW = 3000
UPSELL_GREEN = 4500
CRM_GREEN = 11000


def _nickname(user: User) -> str:
    return user.owner_alias or user.staff_name or (user.email.split("@")[0] if user.email else "-")


def _current_members(team_code: str) -> list[dict]:
    assignments = (
        TeamAssignment.objects.filter(team_code=team_code, effective_to__isnull=True)
        .select_related("user")
        .order_by("user__staff_name", "user__email")
    )
    return [{"user_id": a.user_id, "nickname": _nickname(a.user)} for a in assignments]


def _thai_date_label(d: date) -> str:
    be_year = d.year + BE_YEAR_OFFSET
    return f"{THAI_WEEKDAY[d.weekday()]} {d.day}/{d.month}/{be_year % 100:02d}"


@dataclass
class DailyMatrix:
    year: int
    month: int
    crm_members: list[dict]
    upsell_members: list[dict]
    unassigned_members: list[dict]
    rows: list[dict]
    unassigned_banner_amount: float = 0
    thresholds: dict = field(
        default_factory=lambda: {"upsell_yellow": UPSELL_YELLOW, "upsell_green": UPSELL_GREEN, "crm_green": CRM_GREEN}
    )


def _cell(amount, threshold_fn, is_leave: bool) -> dict:
    return {"amount": amount, "leave": is_leave, "highlight": None if is_leave else threshold_fn(amount)}


def _upsell_highlight(amount) -> str | None:
    if amount > UPSELL_GREEN:
        return "green"
    if amount > UPSELL_YELLOW:
        return "yellow"
    return None


def _crm_highlight(amount) -> str | None:
    return "green" if amount > CRM_GREEN else None


def daily_matrix(year: int, month: int) -> DailyMatrix:
    start_date = date(year, month, 1)
    end_date = date(year, month, calendar.monthrange(year, month)[1])

    crm_members = _current_members(TeamAssignment.TEAM_CRM)
    upsell_members = _current_members(TeamAssignment.TEAM_UPSELL)

    lines = _scoped_lines(start_date, end_date, None).annotate(day=TruncDate("order__created_at"))

    team_amounts: dict[tuple[date, int], float] = defaultdict(float)
    for row in (
        lines.exclude(team_code__isnull=True)
        .values("day", "assignment_user_id")
        .annotate(sales_amount=Sum("amount"))
    ):
        team_amounts[(row["day"], row["assignment_user_id"])] = float(row["sales_amount"] or 0)

    unassigned_amounts: dict[tuple[date, str], float] = defaultdict(float)
    for row in (
        lines.filter(team_code__isnull=True)
        .values("day", "uploaded_by_norm")
        .annotate(sales_amount=Sum("amount"))
    ):
        unassigned_amounts[(row["day"], row["uploaded_by_norm"])] = float(row["sales_amount"] or 0)

    unassigned_emails = sorted({email for (_, email) in unassigned_amounts if email})
    nickname_by_email = {
        u.email.strip().lower(): _nickname(u)
        for u in User.objects.filter(email__in=unassigned_emails)
    }
    unassigned_keys = sorted({email for (_, email) in unassigned_amounts})
    unassigned_members = [
        {"key": email, "nickname": nickname_by_email.get(email, email or "อื่นๆ")} for email in unassigned_keys
    ]

    holidays = Holiday.objects.filter(date__range=(start_date, end_date))
    holiday_days: dict[date, str] = {}
    leave_by_user_day: dict[tuple[date, int], str] = {}
    for h in holidays:
        if h.scope == Holiday.SCOPE_ALL:
            holiday_days[h.date] = h.get_status_display()
        elif h.user_id:
            leave_by_user_day[(h.date, h.user_id)] = h.get_status_display()

    rows = []
    for offset in range((end_date - start_date).days + 1):
        d = date.fromordinal(start_date.toordinal() + offset)
        is_holiday = d in holiday_days

        crm_cells = {}
        crm_total = 0.0
        for m in crm_members:
            amt = team_amounts.get((d, m["user_id"]), 0.0)
            is_leave = (d, m["user_id"]) in leave_by_user_day
            crm_cells[m["user_id"]] = _cell(amt, _crm_highlight, is_leave)
            if not is_leave:
                crm_total += amt

        upsell_cells = {}
        upsell_total = 0.0
        for m in upsell_members:
            amt = team_amounts.get((d, m["user_id"]), 0.0)
            is_leave = (d, m["user_id"]) in leave_by_user_day
            upsell_cells[m["user_id"]] = _cell(amt, _upsell_highlight, is_leave)
            if not is_leave:
                upsell_total += amt

        unassigned_cells = {}
        unassigned_total = 0.0
        for m in unassigned_members:
            amt = unassigned_amounts.get((d, m["key"]), 0.0)
            unassigned_cells[m["key"]] = {"amount": amt, "leave": False, "highlight": None}
            unassigned_total += amt

        rows.append({
            "date": d,
            "label": _thai_date_label(d),
            "is_holiday": is_holiday,
            "holiday_label": holiday_days.get(d),
            "crm_cells": crm_cells,
            "crm_total": crm_total,
            "upsell_cells": upsell_cells,
            "upsell_total": upsell_total,
            "unassigned_cells": unassigned_cells,
            "unassigned_total": unassigned_total,
            "grand_total": crm_total + upsell_total + unassigned_total,
        })

    unassigned_banner_amount = team_sales_summary(start_date, end_date)["unassigned"]["sales_amount"]

    return DailyMatrix(
        year=year,
        month=month,
        crm_members=crm_members,
        upsell_members=upsell_members,
        unassigned_members=unassigned_members,
        rows=rows,
        unassigned_banner_amount=unassigned_banner_amount,
    )


def holiday_list(year: int, month: int) -> list[Holiday]:
    start_date = date(year, month, 1)
    end_date = date(year, month, calendar.monthrange(year, month)[1])
    return list(
        Holiday.objects.filter(date__range=(start_date, end_date)).select_related("user").order_by("date")
    )


def leave_user_options() -> list[User]:
    return list(User.objects.filter(is_active=True).order_by("staff_name", "email"))
