from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import can_delete_order, can_manage_all
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.reporting.selectors import (
    dashboard_kpis,
    sales_daily,
    sales_report_owner_options,
    sales_rows,
    sales_summary,
)
from crm.reporting.services import delete_sales_lines

RANGE_OPTIONS = ["วันนี้", "เลือกวันเดียว", "เลือกช่วงวันที่", "เมื่อวาน", "7 วัน", "30 วัน", "เดือนนี้"]


def _resolve_range(label: str, single_date=None, start=None, end=None):
    today = timezone.localdate()
    if label == "เลือกวันเดียว" and single_date:
        return single_date, single_date
    if label == "เลือกช่วงวันที่" and start and end:
        return start, end
    if label == "เมื่อวาน":
        y = today - timedelta(days=1)
        return y, y
    if label == "7 วัน":
        return today - timedelta(days=6), today
    if label == "30 วัน":
        return today - timedelta(days=29), today
    if label == "เดือนนี้":
        return today.replace(day=1), today
    return today, today


@login_required
def dashboard(request):
    range_label = request.GET.get("range", "วันนี้")
    single_date = request.GET.get("single_date") or None
    start_date_param = request.GET.get("start_date") or None
    end_date_param = request.GET.get("end_date") or None
    start_date, end_date = _resolve_range(range_label, single_date, start_date_param, end_date_param)

    kpis = dashboard_kpis(request.user, start_date, end_date)

    is_manager = can_manage_all(request.user)
    owner_filter = request.GET.get("owner", ALL) if is_manager else ALL
    owner_options = sales_report_owner_options(request.user) if is_manager else []

    summary = sales_summary(request.user, start_date, end_date, owner_filter)
    rows = sales_rows(request.user, start_date, end_date, owner_filter)
    daily = sales_daily(request.user, start_date, end_date, owner_filter)

    # Simple day x sale_type pivot for a lightweight CSS bar chart (no
    # charting library vendored — see static/crm/vendor/README.md).
    days = sorted({d["day"] for d in daily})
    pivot = {day: {"NEW_ORDER": 0, "UPSELL": 0} for day in days}
    for d in daily:
        pivot[d["day"]][d["order__sale_type"]] = float(d["sales_amount"] or 0)
    max_amount = max((v for day in pivot.values() for v in day.values()), default=0) or 1

    context = {
        "kpis": kpis,
        "range_options": RANGE_OPTIONS,
        "range_label": range_label,
        "start_date": start_date,
        "end_date": end_date,
        "is_manager": is_manager,
        "owner_filter": owner_filter,
        "owner_options": owner_options,
        "summary": summary,
        "rows": rows,
        "chart": [{"day": day, "values": values} for day, values in pivot.items()],
        "max_amount": max_amount,
        "can_delete": can_delete_order(request.user),
        "base_qs": request.GET.urlencode(),
    }
    return render(request, "reporting/dashboard.html", context)


@login_required
def delete_sales(request):
    if request.method == "POST":
        line_ids = request.POST.getlist("line_id")
        try:
            deleted = delete_sales_lines(request.user, line_ids)
        except Exception as exc:
            messages.error(request, f"ลบคำสั่งซื้อไม่สำเร็จ: {exc}")
        else:
            messages.success(request, f"ลบคำสั่งซื้อสำเร็จ {deleted:,} รายการ")
    base_qs = request.POST.get("base_qs", "")
    return redirect(f"{reverse('reporting:dashboard')}?{base_qs}")
