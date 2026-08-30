from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import (
    can_assign_customer_owner,
    can_edit_customer_lead,
    require_permission,
)
from crm.core.thai import FOLLOWUP_STATUS_LABELS
from crm.customers.export import build_export_workbook
from crm.customers.models import Customer
from crm.customers.selectors import (
    DEFAULT_PAGE_SIZE,
    CustomerFilters,
    customer_360,
    customer_export_queryset,
    customer_filter_options,
    customer_order_history,
    customer_page,
    owner_assignment_options,
)
from crm.customers.services import assign_owner, assign_url, save_follow_marker
from crm.online_orders.selectors import online_orders_for_customer


def _back_to_list_url(request, customer_id: int) -> str:
    qs = request.GET.copy()
    qs.pop("page", None)
    qs.pop("customer_id", None)
    qs["customer_id"] = str(customer_id)
    return f"{reverse('customers:list')}?{qs.urlencode()}#detail-{customer_id}"


@login_required
def index(request):
    filters = CustomerFilters.from_query(request.GET)
    page_number = request.GET.get("page", 1)
    page_size = request.GET.get("page_size", DEFAULT_PAGE_SIZE)

    page = customer_page(request.user, filters, page_number, page_size)
    options = customer_filter_options()

    qs = request.GET.copy()
    qs.pop("page", None)
    qs.pop("customer_id", None)
    base_qs = qs.urlencode()

    detail = None
    detail_id = request.GET.get("customer_id")
    if detail_id:
        detail = next((c for c in page.items if str(c.id) == detail_id), None)
        if detail:
            detail = {
                "customer": detail,
                "orders": customer_order_history(detail),
                "can_edit": can_edit_customer_lead(request.user, detail),
                "can_assign_owner": can_assign_customer_owner(request.user),
                "owner_options": owner_assignment_options() if can_assign_customer_owner(request.user) else [],
                "followup_status_options": list(FOLLOWUP_STATUS_LABELS.items()),
                "current_status": getattr(getattr(detail, "followup", None), "status", "none"),
            }

    context = {
        "page": page,
        "filters": filters,
        "options": options,
        "base_qs": base_qs,
        "detail": detail,
    }
    return render(request, "customers/index.html", context)


@login_required
@require_permission("can_export_customers")
def export_xlsx(request):
    filters = CustomerFilters.from_query(request.GET)
    qs = customer_export_queryset(request.user, filters)
    content = build_export_workbook(qs)

    stamp = timezone.now().strftime("%Y%m%d_%H%M")
    filename = f"crm_customers_{stamp}.xlsx"
    response = HttpResponse(
        content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def detail(request, customer_id: int):
    customer = customer_360(request.user, customer_id)
    orders = customer_order_history(customer)
    products_bought = {}
    for order in orders:
        for line in order.lines.all():
            key = (line.sku, line.product_name)
            entry = products_bought.setdefault(
                key, {"sku": line.sku, "product_name": line.product_name, "count": 0, "latest": order.order_date}
            )
            entry["count"] += 1
            if order.order_date and (entry["latest"] is None or order.order_date > entry["latest"]):
                entry["latest"] = order.order_date

    context = {
        "customer": customer,
        "orders": orders,
        "products_bought": list(products_bought.values()),
        "can_edit": can_edit_customer_lead(request.user, customer),
        "online_orders": online_orders_for_customer(customer),
    }
    return render(request, "customers/detail.html", context)


@login_required
def save_follow_marker_view(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id)
    if request.method == "POST":
        try:
            save_follow_marker(request.user, customer, request.POST.get("status", "none"))
            messages.success(request, "อัปเดตสถานะติดตามแล้ว")
        except PermissionDenied as exc:
            messages.error(request, str(exc))
    return redirect(_back_to_list_url(request, customer_id))


@login_required
def assign_owner_view(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id)
    if request.method == "POST":
        staff_code = request.POST.get("staff_code", "")
        owner_display = request.POST.get("owner_display", "")
        if not staff_code:
            messages.error(request, "ไม่พบ staff_code ของผู้ดูแลที่เลือก")
        else:
            try:
                updated = assign_owner(request.user, customer, owner_display, staff_code)
                messages.success(request, f"อัปเดตผู้ดูแลแล้ว {updated:,} แถว")
            except PermissionDenied as exc:
                messages.error(request, str(exc))
    return redirect(_back_to_list_url(request, customer_id))


@login_required
def assign_url_view(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id)
    if request.method == "POST":
        try:
            assign_url(request.user, customer, request.POST.get("url", ""))
            messages.success(request, "อัปเดต URL แล้ว")
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(_back_to_list_url(request, customer_id))
