import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import can_manage_all, require_permission
from crm.customers.selectors import owner_assignment_options
from crm.orders.forms import ManualOrderForm
from crm.orders.services import LineItem, OwnerConflictError, save_manual_order


@login_required
@require_permission("can_add_manual_order")
def new_order(request):
    is_manager = can_manage_all(request.user)
    owner_choices = owner_assignment_options() if is_manager else []
    owner_name_by_code = {code: name for name, code in owner_choices}

    if request.method == "POST":
        form = ManualOrderForm(request.POST, owner_choices=owner_choices)
        if form.is_valid():
            items = form.line_items()
            if not items:
                form.add_error(None, "กรุณาเลือกสินค้าอย่างน้อย 1 รายการ")
            else:
                try:
                    result = save_manual_order(
                        actor=request.user,
                        customer_name=form.cleaned_data["customer_name"],
                        phone1=form.cleaned_data["phone1"],
                        phone2=form.cleaned_data["phone2"],
                        url=form.cleaned_data["url"],
                        address=form.cleaned_data["address"],
                        province=form.cleaned_data["province"],
                        city=form.cleaned_data["city"],
                        postal_code=form.cleaned_data["postal_code"],
                        order_no=form.cleaned_data["order_no"],
                        order_date=timezone.localdate(),
                        sale_type=form.cleaned_data["sale_type"],
                        chosen_owner_display=owner_name_by_code.get(form.cleaned_data.get("staff_code"), "")
                        if is_manager else "",
                        chosen_staff_code=form.cleaned_data.get("staff_code", "") if is_manager else "",
                        items=[LineItem(sku=i["sku"], product_name=i["product_name"], qty=i["qty"], amount=i["amount"]) for i in items],
                    )
                except OwnerConflictError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        f"บันทึกคำสั่งซื้อสำเร็จแล้ว สินค้า {result.lines_created + result.lines_merged} รายการ "
                        f"(เพิ่มใหม่ {result.lines_created}, อัปเดต {result.lines_merged})",
                    )
                    return redirect(reverse("orders:new"))
    else:
        form = ManualOrderForm(owner_choices=owner_choices, initial={"sale_type": "NEW_ORDER"})

    address_initial = {
        "province": request.POST.get("province", ""),
        "city": request.POST.get("city", ""),
        "postal_code": request.POST.get("postal_code", ""),
    }
    return render(request, "orders/new.html", {
        "form": form, "is_manager": is_manager,
        "address_initial": json.dumps(address_initial),
    })
