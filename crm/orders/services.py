"""Write paths for orders. Every entry point is one transaction; the
merge rule and the owner-conflict check are centralized here so the
Follow-up "add order" popup and the future standalone Manual Order page
(Phase 4 continuation) share one implementation instead of drifting like
the legacy ui/manual_order_ui.py / pages/followup.py near-duplicates did.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from crm.core.identity import clean, normalize_phone
from crm.core.permissions import can_edit_customer_lead, can_manage_all
from crm.customers.models import Customer
from crm.customers.services import find_or_create_customer
from crm.orders.models import Order, OrderLine


@dataclass
class LineItem:
    sku: str
    product_name: str
    qty: int
    amount: Decimal | None = None


@dataclass
class SaveOrderResult:
    order: Order
    lines_created: int = 0
    lines_merged: int = 0
    duplicate_lock_warning: str = ""


def merge_lines(order: Order, items: list[LineItem]) -> tuple[int, int]:
    """Invariant 7: same SKU AND same product name -> merge quantity;
    same SKU, different name -> separate line. The DB's
    ux_line_order_sku_name unique constraint is the authoritative
    backstop if this ever races.
    """
    created = merged = 0
    for item in items:
        if item.qty <= 0:
            continue
        line, was_created = OrderLine.objects.get_or_create(
            order=order,
            sku=item.sku,
            product_name=item.product_name,
            defaults={"quantity": item.qty, "amount": item.amount},
        )
        if was_created:
            created += 1
        else:
            line.quantity += item.qty
            if item.amount is not None:
                line.amount = (line.amount or Decimal("0")) + item.amount
            line.save(update_fields=["quantity", "amount"])
            merged += 1
    return created, merged


def find_owner_conflict(phone1: str, phone2: str, allowed_staff_codes: set[str]) -> Customer | None:
    """Duplicate-phone lock. Checks EXISTENCE over the FULL match set —
    not the legacy's 50-row cap, which silently let a conflicting owner
    past position 50 slip through undetected (docs/DECISIONS.md #12).
    """
    phones = [p for p in (normalize_phone(phone1), normalize_phone(phone2)) if p]
    if not phones:
        return None

    allowed = {clean(c).casefold() for c in allowed_staff_codes if clean(c)}
    candidates = (
        Customer.objects.filter(Q(phone1__in=phones) | Q(phone2__in=phones))
        .exclude(staff_code="")
    )
    for candidate in candidates:
        if clean(candidate.staff_code).casefold() not in allowed:
            return candidate
    return None


@transaction.atomic
def save_order_for_customer(
    *,
    actor,
    customer: Customer,
    order_no: str,
    order_date,
    sale_type: str,
    url: str,
    address: str,
    province: str = "",
    city: str = "",
    postal_code: str = "",
    items: list[LineItem],
    bypass_owner_conflict: bool = False,
) -> SaveOrderResult:
    """The single write path for "add an order to an existing customer" —
    used by the Follow-up page's add-order action. Owner/staff_code are
    LOCKED to the customer's existing values (invariant: manual order
    owner assignment is EDITOR-only and happens through owner-assignment,
    not through this form) — see docs/legacy/data-layer-report.md
    §"Manual Order Workflow".

    The owner-conflict check runs INSIDE this same transaction (closing
    the legacy TOCTOU gap between the check and the write).
    """
    if not bypass_owner_conflict:
        actor_staff_code = clean(getattr(actor, "staff_code", ""))
        conflict = find_owner_conflict(
            customer.phone1, customer.phone2, {customer.staff_code, actor_staff_code}
        )
        if conflict is not None:
            raise OwnerConflictError(conflict)

    order = Order.objects.select_for_update().filter(customer=customer, order_no=order_no).first() if order_no else None
    if order is None:
        order = Order.objects.create(
            customer=customer,
            order_no=order_no,
            order_date=order_date,
            sale_type=sale_type,
            owner_display=customer.owner_display,
            staff_code=customer.staff_code,
            source_type="manual",
            uploaded_by=clean(getattr(actor, "email", "")),
        )

    if url:
        customer.url = url
    if address:
        customer.address = address
    if province:
        customer.province = province
    if city:
        customer.city = city
    if postal_code:
        customer.postal_code = postal_code

    created, merged = merge_lines(order, items)

    total = order.lines.aggregate(total=Sum("amount"))["total"]
    order.total_amount = total
    order.save(update_fields=["total_amount"])

    if customer.last_order_date is None or (order.order_date and order.order_date >= customer.last_order_date):
        customer.last_order_date = order.order_date
        customer.last_order = order
    customer.order_count = customer.orders.count()
    customer.save(update_fields=[
        "last_order_date", "last_order", "order_count",
        "url", "address", "province", "city", "postal_code", "updated_at",
    ])

    return SaveOrderResult(order=order, lines_created=created, lines_merged=merged)


class OwnerConflictError(Exception):
    def __init__(self, conflicting_customer: Customer):
        self.conflicting_customer = conflicting_customer
        super().__init__(f"มีผู้ดูแลแล้ว: {conflicting_customer.owner_display or conflicting_customer.staff_code}")


@transaction.atomic
def save_manual_order(
    *,
    actor,
    customer_name: str,
    phone1: str,
    phone2: str,
    url: str,
    address: str,
    order_no: str,
    order_date,
    sale_type: str,
    chosen_owner_display: str,
    chosen_staff_code: str,
    items: list[LineItem],
    province: str = "",
    city: str = "",
    postal_code: str = "",
) -> SaveOrderResult:
    """The standalone /orders/new form. Unlike save_order_for_customer
    (which always acts on an already-known, already-owned customer from a
    Follow-up row), this path may be creating a brand-new customer, so
    owner/staff_code have to be decided here rather than just copied:

    - Manager (EDITOR/ADMIN): picks any owner from the dropdown, for both
      new and existing customers (mirrors legacy's `force_owner_update`).
    - Non-manager (telesell): owner is locked to their own identity,
      matching legacy's manual-order-panel behavior.

    For a non-manager acting on an EXISTING customer found by phone, the
    real authorization question is "does this customer already belong to
    me" — reusing can_edit_customer_lead here rather than
    find_owner_conflict, because find_owner_conflict's job is a different
    check (does some OTHER customer sharing one of these phone numbers
    belong to someone else), and deliberately treats "this same customer,
    same phone" as a non-conflict by design.
    """
    is_manager = can_manage_all(actor)
    if is_manager:
        owner_display, staff_code = chosen_owner_display, chosen_staff_code
    else:
        owner_display = getattr(actor, "staff_name", "") or clean(getattr(actor, "email", ""))
        staff_code = clean(getattr(actor, "staff_code", ""))

    customer, created = find_or_create_customer(
        phone1, phone2,
        {
            "customer_name": customer_name, "owner_display": owner_display, "staff_code": staff_code,
            "province": province, "city": city, "postal_code": postal_code,
        },
    )

    if not created:
        if not is_manager and not can_edit_customer_lead(actor, customer):
            raise OwnerConflictError(customer)
        customer.customer_name = customer_name or customer.customer_name
        if is_manager:
            customer.owner_display = owner_display
            customer.staff_code = staff_code
        customer.save(update_fields=["customer_name", "owner_display", "staff_code", "updated_at"])

    return save_order_for_customer(
        actor=actor,
        customer=customer,
        order_no=order_no,
        order_date=order_date,
        sale_type=sale_type,
        url=url,
        address=address,
        province=province,
        city=city,
        postal_code=postal_code,
        items=items,
        bypass_owner_conflict=True,  # already checked above, correctly, for this call site
    )
