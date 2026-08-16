from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from crm.catalog import selectors, services
from crm.catalog.forms import ProductCreateForm, ProductImportForm
from crm.catalog.models import Product
from crm.catalog.selectors import (
    DEFAULT_SORT_LABEL,
    DEFAULT_STATUS_LABEL,
    PRODUCT_SORT_OPTIONS,
    PRODUCT_STATUS_OPTIONS,
)
from crm.core.identity import clean
from crm.core.permissions import can_edit_products, require_permission


def _redirect_to_list(request):
    qs = request.POST.get("base_qs", "")
    url = reverse("catalog:list")
    return redirect(f"{url}?{qs}" if qs else url)


@login_required
def index(request):
    # Legacy: every logged-in user can view Product Master; only
    # can_edit_products may create/edit/import/bulk-act — enforced by
    # require_permission on the write views below, and by simply not
    # rendering those controls here.
    status_label = request.GET.get("status") or DEFAULT_STATUS_LABEL
    if status_label not in PRODUCT_STATUS_OPTIONS:
        status_label = DEFAULT_STATUS_LABEL
    sort_label = request.GET.get("sort") or DEFAULT_SORT_LABEL
    if sort_label not in PRODUCT_SORT_OPTIONS:
        sort_label = DEFAULT_SORT_LABEL
    search = request.GET.get("search", "")
    page_number = request.GET.get("page", 1)

    page = selectors.product_page(status_label, sort_label, search, page_number)
    is_editor = can_edit_products(request.user)

    qs = request.GET.copy()
    qs.pop("page", None)
    base_qs = qs.urlencode()

    # Delete-readiness is a query, not a mutation, but its result table is
    # too large to carry through a redirect querystring — stash it in the
    # session for exactly one render, matching the read-then-display shape
    # of the legacy in-page report.
    readiness_report = request.session.pop("product_delete_readiness", None)
    if readiness_report:
        for row in readiness_report:
            row["status_label"] = selectors.STATUS_LABELS.get(row["status"], row["status"])
            row["reason_label"] = selectors.REASON_LABELS.get(row["reason"], row["reason"])

    context = {
        "page": page,
        "status_options": list(PRODUCT_STATUS_OPTIONS.keys()),
        "status_label": status_label,
        "sort_options": list(PRODUCT_SORT_OPTIONS.keys()),
        "sort_label": sort_label,
        "search": search,
        "is_editor": is_editor,
        "base_qs": base_qs,
        "create_form": ProductCreateForm() if is_editor else None,
        "import_form": ProductImportForm() if is_editor else None,
        "readiness_report": readiness_report,
    }
    return render(request, "catalog/index.html", context)


@login_required
@require_permission("can_edit_products")
def create(request):
    if request.method != "POST":
        return redirect("catalog:list")
    form = ProductCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "กรุณากรอก SKU และชื่อสินค้า")
        return _redirect_to_list(request)

    services.create_or_merge_product(
        sku=form.cleaned_data["sku"],
        product_name=form.cleaned_data["product_name"],
        product_group=form.cleaned_data["product_group"],
        actor_email=request.user.email,
    )
    messages.success(request, "เพิ่มสินค้าแล้ว")
    return _redirect_to_list(request)


@login_required
@require_permission("can_edit_products")
def save_row(request, product_id: int):
    if request.method != "POST":
        return redirect("catalog:list")
    product = get_object_or_404(Product, pk=product_id)
    if product.archived_at:
        raise PermissionDenied("ไม่สามารถแก้ไขสินค้าที่เก็บถาวรได้")

    sku = clean(request.POST.get("sku", ""))
    product_name = clean(request.POST.get("product_name", ""))
    product_group = clean(request.POST.get("product_group", ""))
    is_active = request.POST.get("is_active") == "on"

    if not sku or not product_name:
        messages.error(request, "กรุณากรอก SKU และชื่อสินค้า")
        return _redirect_to_list(request)

    product.sku = sku
    product.product_name = product_name
    product.product_group = product_group or "ทั่วไป"
    product.is_active = is_active
    product.updated_by = request.user.email
    update_fields = [
        "sku", "product_name", "product_group", "is_active", "sku_number", "updated_by", "updated_at",
    ]
    try:
        product.save(update_fields=update_fields)
    except IntegrityError:
        messages.error(request, "บันทึกไม่สำเร็จ: SKU/ชื่อสินค้า/กลุ่มสินค้านี้ซ้ำกับรายการอื่น")
        return _redirect_to_list(request)

    messages.success(request, "บันทึกสินค้าแล้ว")
    return _redirect_to_list(request)


@login_required
@require_permission("can_edit_products")
def deactivate(request, product_id: int):
    if request.method != "POST":
        return redirect("catalog:list")
    product = get_object_or_404(Product, pk=product_id)
    if product.is_active:
        product.is_active = False
        product.updated_by = request.user.email
        product.save(update_fields=["is_active", "updated_by", "updated_at"])
        messages.success(request, "ปิดใช้งานสินค้าแล้ว")
    return _redirect_to_list(request)


@login_required
@require_permission("can_edit_products")
def bulk_action(request):
    if request.method != "POST":
        return redirect("catalog:list")

    action = request.POST.get("action", "")
    raw_ids = request.POST.getlist("product_id")
    try:
        product_ids = [int(pid) for pid in raw_ids]
    except ValueError:
        messages.error(request, "รหัสสินค้าที่เลือกไม่ถูกต้อง กรุณาเลือกสินค้าใหม่")
        return _redirect_to_list(request)

    if not product_ids:
        messages.error(request, "กรุณาเลือกสินค้าอย่างน้อย 1 รายการ")
        return _redirect_to_list(request)

    if action == "check_readiness":
        request.session["product_delete_readiness"] = selectors.product_delete_readiness(product_ids)
        return _redirect_to_list(request)

    if request.POST.get("confirm") != "on":
        messages.error(request, "กรุณายืนยันก่อนดำเนินการ")
        return _redirect_to_list(request)

    if action in ("activate", "deactivate"):
        is_active = action == "activate"
        updated = services.bulk_set_active(product_ids, is_active, request.user.email)
        label = "เปิดใช้งาน" if is_active else "ปิดใช้งาน"
        messages.success(request, f"{label}สินค้า {updated:,} รายการแล้ว")
    elif action == "archive":
        result = services.archive_products(product_ids, request.POST.get("reason", ""), request.user.email)
        messages.success(
            request,
            f"เก็บถาวรสำเร็จ: เลือก {result['requested']:,} / อัปเดต {result['updated']:,} / "
            f"ข้าม {result['skipped']:,} รายการ",
        )
    elif action == "restore":
        result = services.restore_products(product_ids, request.user.email)
        messages.success(
            request,
            f"Restore สำเร็จ: เลือก {result['requested']:,} / อัปเดต {result['updated']:,} / "
            f"ข้าม {result['skipped']:,} รายการ",
        )
    else:
        messages.error(request, "ไม่รู้จักคำสั่งนี้")

    return _redirect_to_list(request)


@login_required
@require_permission("can_edit_products")
def import_view(request):
    if request.method == "POST":
        form = ProductImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                result = services.import_products_workbook(form.cleaned_data["file"], request.user.email)
            except services.WorkbookFormatError as exc:
                messages.error(request, f"นำเข้าไม่สำเร็จ: {exc}")
            else:
                messages.success(
                    request,
                    f"นำเข้าสินค้าใหม่ {result['created']:,} รายการแล้ว "
                    f"(ข้ามซ้ำ {result['duplicate']:,}, ข้อมูลไม่ครบ {result['invalid']:,})",
                )
        else:
            messages.error(request, "กรุณาเลือกไฟล์ .xlsx")
    return redirect("catalog:list")


@login_required
def options(request):
    """SKU/product-name suggestions for the manual-order form's autocomplete
    (any logged-in user who can add manual orders needs this, not just
    product editors — no can_edit_products gate here).
    """
    results = selectors.product_options(request.GET.get("q", ""))
    return JsonResponse(results, safe=False)
