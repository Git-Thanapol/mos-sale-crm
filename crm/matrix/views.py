from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import require_permission
from crm.matrix.models import Holiday
from crm.matrix.selectors import daily_matrix, holiday_list, leave_user_options
from crm.matrix.services import create_holiday, delete_holiday

MONTH_LABELS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def _year_options(center_year: int) -> list[int]:
    return list(range(center_year - 3, center_year + 2))


@login_required
@require_permission("can_view_daily_matrix")
def index(request):
    today = timezone.localdate()
    year = int(request.GET.get("year") or today.year)
    month = int(request.GET.get("month") or today.month)

    matrix = daily_matrix(year, month)
    holidays = holiday_list(year, month)

    context = {
        "matrix": matrix,
        "year": year,
        "month": month,
        "month_label": MONTH_LABELS[month],
        "month_options": list(range(1, 13)),
        "month_labels": MONTH_LABELS,
        "year_options": _year_options(today.year),
        "holidays": holidays,
        "scope_choices": Holiday.SCOPE_CHOICES,
        "status_choices": Holiday.STATUS_CHOICES,
        "leave_users": leave_user_options(),
        "base_qs": request.GET.urlencode(),
    }
    return render(request, "matrix/index.html", context)


@login_required
@require_permission("can_view_daily_matrix")
def save_holiday(request):
    if request.method == "POST":
        try:
            create_holiday(
                date=request.POST.get("date"),
                scope=request.POST.get("scope"),
                status=request.POST.get("status"),
                user_id=request.POST.get("user_id") or None,
                note=request.POST.get("note", ""),
                created_by=request.user.email,
            )
            messages.success(request, "บันทึกวันหยุด/วันลาสำเร็จ")
        except ValueError as exc:
            messages.error(request, str(exc))
    base_qs = request.POST.get("base_qs", "")
    return redirect(f"{reverse('matrix:index')}?{base_qs}")


@login_required
@require_permission("can_view_daily_matrix")
def remove_holiday(request, holiday_id: int):
    if request.method == "POST":
        delete_holiday(holiday_id)
        messages.success(request, "ยกเลิกรายการสำเร็จ")
    base_qs = request.POST.get("base_qs", "")
    return redirect(f"{reverse('matrix:index')}?{base_qs}")
