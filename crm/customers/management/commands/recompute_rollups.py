"""Backfill/repair crm_customer.last_order_date, .last_order, .order_count
and crm_order.total_amount from the authoritative crm_order/crm_order_line
rows. Safe to re-run any time; every write path is also supposed to keep
these current incrementally, so this is a repair tool, not the primary
mechanism.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Sum

from crm.customers.models import Customer
from crm.orders.models import Order


class Command(BaseCommand):
    help = "Recompute Customer rollups and Order.total_amount from Order/OrderLine."

    def handle(self, *args, **options):
        order_totals_updated = 0
        for order in Order.objects.all().iterator():
            total = order.lines.aggregate(total=Sum("amount"))["total"]
            if total != order.total_amount:
                order.total_amount = total
                order.save(update_fields=["total_amount"])
                order_totals_updated += 1

        customers_updated = 0
        for customer in Customer.objects.all().iterator():
            agg = customer.orders.aggregate(last_date=Max("order_date"), count=Count("id"))
            last_order = customer.orders.order_by("-order_date", "-id").first()
            changed = (
                customer.last_order_date != agg["last_date"]
                or customer.order_count != agg["count"]
                or customer.last_order_id != (last_order.id if last_order else None)
            )
            if changed:
                customer.last_order_date = agg["last_date"]
                customer.order_count = agg["count"]
                customer.last_order = last_order
                customer.save(update_fields=["last_order_date", "order_count", "last_order", "updated_at"])
                customers_updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"updated {order_totals_updated} order total(s), {customers_updated} customer rollup(s)"
        ))
