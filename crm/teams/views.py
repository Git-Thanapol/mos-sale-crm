from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.accounts.models import User
from crm.core.permissions import require_permission
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.teams import selectors, services
from crm.teams.models import TeamAssignment

SALE_TYPE_OPTIONS = [ALL, "NEW_ORDER", "UPSELL"]
TEAM_FILTER_OPTIONS = [ALL] + [name for _, name in TeamAssignment.TEAM_CHOICES]
TEAM_LABEL_TO_CODE = {name: code for code, name in TeamAssignment.TEAM_CHOICES}

# Assignment-form dropdown: an extra "no team" choice on top of the two real teams.
ASSIGNMENT_UNSET_LABEL = "ยังไม่เลือกทีม"
ASSIGNMENT_OPTIONS = {ASSIGNMENT_UNSET_LABEL: None, **TEAM_LABEL_TO_CODE}


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return default


@login_required
@require_permission("can_view_team_sales")
def index(request):
    today = timezone.localdate()
    start_date = _parse_date(request.GET.get("start_date"), today.replace(day=1))
    end_date = _parse_date(request.GET.get("end_date"), today)

    sale_type_label = request.GET.get("sale_type") or ALL
    sale_type_filter = None if sale_type_label == ALL else sale_type_label

    team_label = request.GET.get("team") or ALL
    team_code_filter = TEAM_LABEL_TO_CODE.get(team_label)

    error = None
    summary = None
    top_products: list[dict] = []
    if start_date > end_date:
        error = "วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด"
    else:
        summary = selectors.team_sales_summary(start_date, end_date, sale_type_filter)
        top_products = selectors.team_top_products(
            start_date, end_date, team_code_filter, sale_type_filter, limit=10
        )

    qs = request.GET.copy()
    qs.pop("page", None)
    base_qs = qs.urlencode()

    assignment_users = selectors.team_assignment_users()
    for row in assignment_users:
        # Resolve up front so the template can do a plain equality check —
        # Django's {% if %} parser doesn't support parenthesized boolean
        # expressions, so "selected if this label OR (unset AND no team)"
        # has to be collapsed to one value here instead of in the template.
        row["current_label"] = row["current_team_name"] or ASSIGNMENT_UNSET_LABEL

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "sale_type_options": SALE_TYPE_OPTIONS,
        "sale_type_label": sale_type_label,
        "team_options": TEAM_FILTER_OPTIONS,
        "team_label": team_label,
        "error": error,
        "summary": summary,
        "top_products": top_products,
        "assignment_users": assignment_users,
        "assignment_options": list(ASSIGNMENT_OPTIONS.keys()),
        "base_qs": base_qs,
    }
    return render(request, "teams/index.html", context)


@login_required
@require_permission("can_view_team_sales")
def save_assignment(request, user_id: int):
    if request.method != "POST":
        return redirect("teams:list")

    target = get_object_or_404(User, pk=user_id)
    label = request.POST.get("team_choice", ASSIGNMENT_UNSET_LABEL)
    team_code = ASSIGNMENT_OPTIONS.get(label)

    try:
        result = services.assign_team(actor=request.user, target_user=target, team_code=team_code)
    except ValueError:
        messages.error(request, f"บันทึกทีมของ {target.email} ไม่สำเร็จ กรุณาลองใหม่อีกครั้ง")
    else:
        if result.changed:
            messages.success(request, f"บันทึกทีมของ {target.email} แล้ว")
        else:
            messages.info(request, f"ทีมของ {target.email} ไม่มีการเปลี่ยนแปลง")

    base_qs = request.POST.get("base_qs", "")
    url = reverse("teams:list")
    return redirect(f"{url}?{base_qs}" if base_qs else url)
