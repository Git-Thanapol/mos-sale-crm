"""Read queries for /customers and /customers/<id>.

Deliberately NOT staff-scoped by default (Customer.objects.for_user is not
called here) — this mirrors the legacy `enforce_user_scope=False` on
fetch_customer_page, a documented, signed-off carve-out (see
docs/DECISIONS.md #11 and settings.CRM_SCOPE_CUSTOMERS_LIST). Every logged-in
role can browse the customers list; per-row edit rights are still gated by
can_edit_customer_lead in the view/template layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q

from crm.core.pagination import Page, clamp_page, clamp_page_size
from crm.core.permissions import can_manage_all, clean
from crm.core.thai import ALL_OPTION_LABEL as ALL
from crm.customers.models import Customer

DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True)
class CustomerFilters:
    staff: str = ALL  # owner_display exact match, matching legacy's "ผู้ดูแล" dropdown
    keyword: str = ""

    @classmethod
    def from_query(cls, query: dict) -> "CustomerFilters":
        return cls(staff=query.get("staff", ALL) or ALL, keyword=(query.get("keyword") or "").strip())


def _base_queryset(filters: CustomerFilters):
    qs = Customer.objects.select_related("last_order").prefetch_related("last_order__lines")

    if filters.staff != ALL:
        qs = qs.filter(owner_display=filters.staff)

    keyword = filters.keyword
    if keyword:
        qs = qs.filter(
            Q(customer_name__icontains=keyword)
            | Q(phone1__icontains=keyword)
            | Q(phone2__icontains=keyword)
            | Q(postal_code__icontains=keyword)
            | Q(orders__order_no__icontains=keyword)
        ).distinct()

    return qs


def customer_page(user, filters: CustomerFilters, page: int, page_size: int) -> Page:
    qs = _base_queryset(filters)
    if settings.CRM_SCOPE_CUSTOMERS_LIST:
        qs = qs.for_user(user)
    # Online orders (crm.online_orders) can create phone-only customers with
    # no crm_order rollup, so last_order_date is NULL — sort those last
    # rather than let Postgres's default NULLS FIRST on DESC put them at
    # the top. See ix_customer_recent_nl (crm.customers migration) for the
    # matching index; plain "-last_order_date" no longer uses an index scan.
    qs = qs.order_by(F("last_order_date").desc(nulls_last=True), "-updated_at", "-id")

    page_size = clamp_page_size(page_size)
    total = qs.count()
    total_pages = max(-(-total // page_size), 1) if total else 1
    page = clamp_page(page, total_pages)
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return Page(items=items, page=page, page_size=page_size, total_rows=total)


def customer_export_queryset(user, filters: CustomerFilters):
    """Unpaginated queryset for the XLSX export — same filters as the
    list page (legacy: fetch_customer_export_rows shares build_customer_where
    with fetch_customer_page), also unscoped per the same
    CRM_SCOPE_CUSTOMERS_LIST carve-out. Export access itself is gated
    separately by can_export_customers (EDITOR only).
    """
    qs = _base_queryset(filters)
    if settings.CRM_SCOPE_CUSTOMERS_LIST:
        qs = qs.for_user(user)
    return qs.order_by(F("last_order_date").desc(nulls_last=True), "-updated_at", "-id")


def owner_assignment_options() -> list[tuple[str, str]]:
    """(staff_name, staff_code) pairs for the owner-assignment dropdown,
    sourced from active users with a staff_code — the Django-native
    replacement for the legacy fetch_owner_user_options(active_only=True)
    query against crm_user_roles.
    """
    from crm.accounts.models import User

    return list(
        User.objects.filter(is_active=True)
        .exclude(staff_code="")
        .exclude(staff_name="")
        .order_by("staff_name")
        .values_list("staff_name", "staff_code")
        .distinct()
    )


def customer_filter_options() -> dict[str, list[str]]:
    owners = list(
        Customer.objects.exclude(owner_display="")
        .order_by("owner_display")
        .values_list("owner_display", flat=True)
        .distinct()[:500]
    )
    return {"owners": owners}


def customer_order_history(customer: Customer, limit: int = 500):
    return customer.orders.prefetch_related("lines").order_by("-order_date", "-id")[:limit]


def customer_360(user, customer_id: int) -> Customer:
    """Raises PermissionDenied (not a 404 or an empty result) for a
    non-manager viewing another staff member's customer — direct-URL access
    must be denied, not merely hidden from the list. See docs/DECISIONS.md
    invariant 5.
    """
    try:
        customer = Customer.objects.select_related("last_order").prefetch_related(
            "last_order__lines", "followup"
        ).get(pk=customer_id)
    except Customer.DoesNotExist as exc:
        raise PermissionDenied("ไม่พบข้อมูลลูกค้าใน ระบบ") from exc

    if can_manage_all(user):
        return customer

    user_staff_code = clean(getattr(user, "staff_code", ""))
    if user_staff_code and user_staff_code == clean(customer.staff_code):
        return customer

    raise PermissionDenied("ไม่มีสิทธิ์เข้าถึงข้อมูลนี้")
