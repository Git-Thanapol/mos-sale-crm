from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction

from crm.core.identity import clean
from crm.core.permissions import can_delete_order, can_manage_all
from crm.orders.models import OrderLine


@transaction.atomic
def delete_sales_lines(user, line_ids: list[str]) -> int:
    """Deletes manual-order line items only (import rows are never
    deletable here — matches the legacy source_type='manual' restriction).

    Legacy's delete_sales_report_records had no staff_code check beyond the
    role gate, meaning any can_delete_order user (including telesell) could
    in principle delete ANY manual order's rows by guessing IDs. Given the
    same URL-bypass class of bug found and fixed earlier in add_order_view,
    non-managers are additionally restricted here to their own orders.
    """
    if not can_delete_order(user):
        raise PermissionDenied("ไม่มีสิทธิ์ลบคำสั่งซื้อ")

    clean_ids = [clean(i) for i in (line_ids or []) if clean(i)]
    if not clean_ids:
        return 0

    qs = OrderLine.objects.filter(id__in=clean_ids, order__source_type="manual")
    if not can_manage_all(user):
        staff_code = clean(getattr(user, "staff_code", ""))
        if not staff_code:
            return 0
        qs = qs.filter(order__staff_code=staff_code)

    orders_to_check = list({line.order_id for line in qs})
    deleted, _ = qs.delete()

    # Drop now-empty manual orders too (mirrors the legacy cleanup of
    # crm_orders rows with no remaining items).
    from crm.orders.models import Order

    Order.objects.filter(id__in=orders_to_check, lines__isnull=True, source_type="manual").delete()

    return deleted
