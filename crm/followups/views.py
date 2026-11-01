from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import (
    can_add_manual_order,
    can_edit_customer_lead,
    can_export_customers,
    can_manage_all,
    can_edit_lead,
    can_reassign_caretaker,
    can_resolve_intake,
    can_review_any_lead,
    can_view_followup_owner_filter,
    require_permission,
)
from crm.accounts.models import User
from crm.domain.enums import CONTACT_ATTEMPT_RESULTS, STOP_REASONS
from crm.followcycles.models import Appointment
from crm.followcycles.selectors import cycle_history
from crm.leads.models import Lead
from crm.leads import presenters
from crm.leads.selectors import (
    LeadQueueFilters,
    global_unassigned_queue,
    lead_queue,
    my_work_queue,
    reallocation_review_queue,
    stopped_review_queue,
    team_unassigned_queue,
)
from crm.core.thai import FOLLOWUP_PRIORITY_OPTIONS, FOLLOWUP_STATUS_LABELS, LEAD_STATUS_LABELS
from crm.followups.forms import AddOrderForm, FollowupEditForm
from crm.followups.models import Followup
from crm.followups.selectors import (
    DEFAULT_PAGE_SIZE,
    FollowupFilters,
    followup_filter_options,
    followup_page,
    followup_summary,
)
from crm.followups.services import save_followup
from crm.orders.services import LineItem, OwnerConflictError, save_order_for_customer



def _back_to_list_url(request, followup_id: int) -> str:
    qs = request.POST.copy() if request.method == "POST" else request.GET.copy()
    qs.pop("csrfmiddlewaretoken", None)
    for key in list(qs.keys()):
        if key.startswith(("sku_", "product_name_", "qty_", "amount_")) or key in {
            "lead_status", "status", "priority", "next_followup_date", "clear_date", "note",
            "order_no", "sale_type", "url", "address",
        }:
            qs.pop(key, None)
    qs["followup_id"] = str(followup_id)
    return f"{reverse('followups:list')}?{qs.urlencode()}#detail-{followup_id}"


@login_required
@require_permission("can_view_followup")
def index(request):
    filters = FollowupFilters.from_query(request.GET)
    page_number = request.GET.get("page", 1)
    page_size = request.GET.get("page_size", DEFAULT_PAGE_SIZE)

    page = followup_page(request.user, filters, page_number, page_size)
    summary = followup_summary(request.user, filters)
    options = followup_filter_options(request.user)

    detail_row = None
    edit_form = None
    order_form = None
    can_edit = False
    followup_id = request.GET.get("followup_id")
    if followup_id:
        detail_row = next((row for row in page.items if str(row.id) == followup_id), None)
        if detail_row:
            can_edit = can_edit_customer_lead(request.user, detail_row.customer)
            if can_edit:
                edit_form = FollowupEditForm(initial={
                    "lead_status": detail_row.lead_status,
                    "status": detail_row.status,
                    "priority": detail_row.priority,
                    "next_followup_date": detail_row.next_followup_date,
                    "note": detail_row.note,
                })
            if detail_row.customer.owner_display and can_add_manual_order(request.user):
                order_form = AddOrderForm(initial={"sale_type": "NEW_ORDER"})

    # `page` and `followup_id` are always appended fresh by the row/pager
    # links themselves — stripped here so clicking a different row's
    # "ติดตาม" link doesn't accumulate a stale followup_id from whichever
    # row was previously open (duplicate query keys "work" because
    # QueryDict.get() takes the last value, but the resulting URL is
    # wrong and shouldn't be handed to users to bookmark/share).
    qs = request.GET.copy()
    qs.pop("page", None)
    qs.pop("followup_id", None)
    base_qs = qs.urlencode()

    qs_no_priority = request.GET.copy()
    qs_no_priority.pop("page", None)
    qs_no_priority.pop("followup_id", None)
    qs_no_priority.pop("priority", None)
    base_qs_no_priority = qs_no_priority.urlencode()

    # --- CRM V2 Lead surface -------------------------------------------
    # The legacy Followup list above is untouched. These sections are additive
    # and each one is omitted entirely for a user who may not act on it --
    # the nav keeps its show-but-gray behaviour, but a review queue a telesale
    # cannot use is noise, not a disabled affordance.
    lead_filters = LeadQueueFilters.from_query(request.GET)
    lead_cursor = request.GET.get("lead_cursor") or None
    lead_section = (request.GET.get("lead_section") or "queue").strip()

    may_review = can_review_any_lead(request.user)
    may_resolve_intake = can_resolve_intake(request.user)

    if lead_section == "my_work":
        lead_page = my_work_queue(request.user, after_cursor=lead_cursor)
    elif lead_section == "team_unassigned" and may_review:
        lead_page = team_unassigned_queue(request.user, after_cursor=lead_cursor)
    elif lead_section == "stopped" and may_review:
        lead_page = stopped_review_queue(request.user, after_cursor=lead_cursor)
    elif lead_section == "reallocation" and may_review:
        lead_page = reallocation_review_queue(request.user, after_cursor=lead_cursor)
    else:
        lead_section = "queue"
        lead_page = lead_queue(request.user, filters=lead_filters, after_cursor=lead_cursor)

    lead_rows = [presenters.decorate(lead) for lead in lead_page.items]

    # The detail panel. Resolved from the scoped queue rather than by a bare
    # pk lookup, so ?lead_id= cannot reach a lead the user may not see.
    lead_detail = None
    detail_can_edit = detail_can_review = False
    detail_cycles: list = []
    detail_appointments: list = []
    detail_caretaker_options: list = []
    requested_lead_id = (request.GET.get("lead_id") or "").strip()
    if not requested_lead_id.isdigit():
        # A non-numeric lead_id reaches the ORM as a pk and raises
        # ValueError("Field 'id' expected a number"), which is a 500 on a
        # hand-edited querystring. Treated as "no lead requested".
        requested_lead_id = ""
    if requested_lead_id:
        detail_lead = (
            Lead.objects.select_related("customer", "team", "caretaker")
            .for_user(request.user)
            .filter(pk=requested_lead_id)
            .first()
        )
        if detail_lead is not None:
            lead_detail = presenters.decorate(detail_lead)
            detail_can_edit = can_edit_lead(request.user, detail_lead)
            detail_can_review = can_reassign_caretaker(request.user, detail_lead)
            detail_cycles = cycle_history(request.user, detail_lead)
            detail_appointments = list(
                Appointment.objects.filter(lead=detail_lead, status="SCHEDULED").order_by(
                    "scheduled_at"
                )
            )
            if detail_can_review:
                detail_caretaker_options = list(
                    User.objects.filter(is_active=True)
                    .exclude(staff_code="")
                    .order_by("staff_name", "email")[:200]
                )
    intake_page = global_unassigned_queue(request.user) if may_resolve_intake else None

    lead_qs = request.GET.copy()
    lead_qs.pop("lead_cursor", None)
    lead_qs.pop("lead_section", None)

    context = {
        "page": page,
        "filters": filters,
        "lead_page": lead_page,
        "lead_rows": lead_rows,
        "lead_filters": lead_filters,
        "lead_section": lead_section,
        "lead_base_qs": lead_qs.urlencode(),
        "intake_page": intake_page,
        "may_review_leads": may_review,
        "may_resolve_intake": may_resolve_intake,
        "lead_detail": lead_detail,
        "detail_can_edit": detail_can_edit,
        "detail_can_review": detail_can_review,
        "detail_cycles": detail_cycles,
        "detail_appointments": detail_appointments,
        "detail_caretaker_options": detail_caretaker_options,
        "detail_next": request.get_full_path(),
        "age_preset_options": presenters.age_preset_options(),
        "contact_result_options": [
            (value, presenters.CONTACT_RESULT_LABELS.get(value, value))
            for value in CONTACT_ATTEMPT_RESULTS
        ],
        "stop_reason_options": [
            (value, presenters.STOP_REASON_LABELS.get(value, value)) for value in STOP_REASONS
        ],
        "lifecycle_options": list(presenters.LIFECYCLE_LABELS.items()),
        "summary": summary,
        "options": options,
        "priority_tabs": FOLLOWUP_PRIORITY_OPTIONS,
        "lead_status_options": list(LEAD_STATUS_LABELS.items()),
        "followup_status_options": list(FOLLOWUP_STATUS_LABELS.items()),
        "can_view_owner_filter": can_view_followup_owner_filter(request.user),
        "can_export": can_export_customers(request.user),
        "base_qs": base_qs,
        "base_qs_no_priority": base_qs_no_priority,
        "detail_row": detail_row,
        "can_edit": can_edit,
        "edit_form": edit_form,
        "order_form": order_form,
    }
    return render(request, "followups/index.html", context)


@login_required
@require_permission("can_view_followup")
def save_followup_view(request, followup_id: int):
    followup = get_object_or_404(Followup.objects.select_related("customer"), pk=followup_id)
    if request.method != "POST":
        return redirect(_back_to_list_url(request, followup_id))

    form = FollowupEditForm(request.POST)
    if not form.is_valid():
        messages.error(request, "ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบฟอร์ม")
        return redirect(_back_to_list_url(request, followup_id))

    try:
        save_followup(
            request.user,
            followup,
            lead_status=form.cleaned_data["lead_status"],
            status=form.cleaned_data["status"],
            priority=form.cleaned_data["priority"],
            next_followup_date=form.cleaned_data["next_followup_date"],
            clear_date=form.cleaned_data["clear_date"],
            note=form.cleaned_data["note"],
        )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return redirect(_back_to_list_url(request, followup_id))

    messages.success(request, "บันทึกสำเร็จแล้ว")
    return redirect(_back_to_list_url(request, followup_id))


@login_required
@require_permission("can_add_manual_order")
def add_order_view(request, followup_id: int):
    followup = get_object_or_404(Followup.objects.select_related("customer"), pk=followup_id)
    customer = followup.customer
    if request.method != "POST":
        return redirect(_back_to_list_url(request, followup_id))

    # Object-level scope check. can_add_manual_order above is route-level
    # (role only) — without this, any telesell user could POST an order
    # onto another staff member's customer by guessing the ID, since the
    # legacy app's equivalent gate only ever worked because Streamlit's
    # popup was reachable exclusively through the already-scoped follow-up
    # list. Django exposes the URL directly, so the check must be explicit
    # here too. can_edit_customer_lead is the right rule to reuse: EDITOR
    # always passes, everyone else needs a matching staff_code.
    if not can_edit_customer_lead(request.user, customer):
        raise PermissionDenied("ไม่มีสิทธิ์เพิ่มคำสั่งซื้อให้ลูกค้ารายนี้")

    if not customer.owner_display:
        messages.error(request, "รายการนี้ยังไม่มีผู้ดูแล จึงยังเพิ่มคำสั่งซื้อจาก popup นี้ไม่ได้")
        return redirect(_back_to_list_url(request, followup_id))

    form = AddOrderForm(request.POST)
    if not form.is_valid():
        messages.error(request, "ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบฟอร์ม")
        return redirect(_back_to_list_url(request, followup_id))

    items = form.line_items()
    if not items:
        messages.error(request, "กรุณาเลือกสินค้าอย่างน้อย 1 รายการ")
        return redirect(_back_to_list_url(request, followup_id))

    line_items = [LineItem(sku=i["sku"], product_name=i["product_name"], qty=i["qty"], amount=i["amount"]) for i in items]

    try:
        result = save_order_for_customer(
            actor=request.user,
            customer=customer,
            order_no=form.cleaned_data["order_no"],
            order_date=timezone.localdate(),
            sale_type=form.cleaned_data["sale_type"],
            url=form.cleaned_data["url"],
            address=form.cleaned_data["address"],
            items=line_items,
            bypass_owner_conflict=can_manage_all(request.user),
        )
    except OwnerConflictError as exc:
        messages.error(request, str(exc))
        return redirect(_back_to_list_url(request, followup_id))

    messages.success(
        request,
        f"บันทึกคำสั่งซื้อสำเร็จแล้ว สินค้า {result.lines_created + result.lines_merged} รายการ "
        f"(เพิ่มใหม่ {result.lines_created}, อัปเดต {result.lines_merged})",
    )
    return redirect(_back_to_list_url(request, followup_id))
