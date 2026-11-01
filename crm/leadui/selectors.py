"""Read queries that belong to the CRM V2 pages rather than to the lead domain."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import F, Q, Sum

from crm.core.pagination import KeysetPage, decode_cursor_parts, encode_cursor
from crm.customers.models import Customer
from crm.domain.enums import is_order_valid_for_sales
from crm.leads.models import Lead
from crm.leads.order_status import ORDER_STATUS_MAP
from crm.leads.selectors import _seek_keys as seek_keys  # the shared keyset predicate
from crm.orders.models import Order

HISTORY_PAGE_SIZE = 25
HISTORY_CURSOR_KINDS = ("datetime?", "int")

#: Raw statuses whose mapped meaning counts as a sale. An unmapped status is
#: never guessed (crm/leads/order_status.py), so it is not in this list.
_SALE_STATUSES = [
    raw for raw, mapped in ORDER_STATUS_MAP.items() if is_order_valid_for_sales(mapped)
]


def visible_customer(user, customer_id: int) -> Customer | None:
    """A customer the user may open: one in their own scope, or one they hold a
    visible Lead of. The second clause is what lets a team lead (who has no
    staff-coded customers) and a caretaker reach the customer from a lead row.
    """
    customer = (
        Customer.objects.filter(pk=customer_id)
        .filter(
            Q(pk__in=Customer.objects.for_user(user).values("pk"))
            | Q(pk__in=Lead.objects.for_user(user).values("customer_id"))
        )
        .first()
    )
    return customer


@dataclass(frozen=True)
class OrderSummary:
    last_order_no: str
    total_orders: int
    total_spend: Decimal


def order_summary(customer: Customer) -> OrderSummary:
    orders = Order.objects.filter(customer=customer)
    newest = orders.order_by(F("order_date").desc(nulls_last=True), "-id").first()
    spend = orders.filter(order_status__in=_SALE_STATUSES).aggregate(total=Sum("total_amount"))[
        "total"
    ]
    return OrderSummary(
        last_order_no=(newest.order_no if newest else "") or "",
        total_orders=orders.count(),
        total_spend=spend or Decimal("0"),
    )


def visible_leads(user, customer: Customer) -> list[Lead]:
    return list(
        Lead.objects.select_related("team")
        .for_user(user)
        .filter(customer=customer)
        .order_by("team__code", "id")
    )


def history_cursor_is_first_page(after_cursor: str | None) -> bool:
    return decode_cursor_parts(after_cursor, HISTORY_CURSOR_KINDS) is None


def order_history(customer: Customer, after_cursor: str | None = None) -> KeysetPage:
    """Newest first, keyset on (order_date DESC NULLS LAST, id DESC), 25 a page."""
    qs = Order.objects.filter(customer=customer)
    qs = seek_keys(qs, [("order_date", True), ("id", True)], after_cursor)
    qs = qs.order_by(F("order_date").desc(nulls_last=True), "-id")
    rows = list(qs[: HISTORY_PAGE_SIZE + 1])
    items = rows[:HISTORY_PAGE_SIZE]
    has_more = len(rows) > HISTORY_PAGE_SIZE
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            (last.order_date.isoformat() if last.order_date else None, last.id)
        )
    return KeysetPage(items=items, next_cursor=next_cursor, has_more=has_more)
