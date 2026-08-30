"""Online-order channel from the shipping/sales platform exports
(InoutManageMain + AllLiteDetailOrder), joined on online_order_no. See
feedback/Update_import_file/issue.md for the source column list.

Deliberately separate from crm.orders.Order/OrderLine — different channel,
own numbering (0 overlap with existing order/tracking numbers on the sample
data), and the source data allows the same SKU to appear twice in one order
at different prices (original sale + upsell line), which the existing
OrderLine.ux_line_order_sku_name constraint would silently merge. Never
feeds Order/OrderLine, dashboard, or team-sales totals.
"""

from __future__ import annotations

from django.db import models

from crm.core.scoping import StaffScopedQuerySet


class OnlineOrder(models.Model):
    online_order_no = models.CharField(max_length=32, unique=True)
    internal_order_no = models.CharField(max_length=32, blank=True, db_index=True)

    carrier = models.CharField(max_length=64, blank=True)
    tracking_no = models.CharField(max_length=64, blank=True, db_index=True)
    shop_name = models.CharField(max_length=64, blank=True)
    order_status = models.CharField(max_length=64, blank=True)
    ordered_at = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=64, blank=True)
    sales_staff = models.CharField(max_length=128, blank=True)

    recipient_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    customer_name = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)
    province = models.CharField(max_length=128, blank=True)
    district = models.CharField(max_length=128, blank=True)
    subdistrict = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)

    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="online_orders"
    )

    # Which source file has landed for this order — explains a blank phone
    # (AllLiteDetailOrder-only orders never carry one; see issue analysis).
    shipping_imported_at = models.DateTimeField(null=True, blank=True)
    detail_imported_at = models.DateTimeField(null=True, blank=True)
    import_batch_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STAFF_CODE_PATH = "customer__staff_code"
    objects = StaffScopedQuerySet.as_manager()

    class Meta:
        db_table = "crm_online_order"
        indexes = [
            models.Index(fields=["-ordered_at", "-id"], name="ix_online_order_recent"),
            models.Index(fields=["customer", "-ordered_at"], name="ix_online_order_customer"),
        ]

    def __str__(self) -> str:
        return self.online_order_no


class OnlineOrderLine(models.Model):
    order = models.ForeignKey(OnlineOrder, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()

    sku = models.CharField(max_length=64, blank=True, db_index=True)
    product_name = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(default=0)

    # Per-LINE, not per-order — verified against the sample data: 1,932
    # orders have some lines with no upsell staff and others upsold by a
    # different (Tele*) staffer, at a different price than the original
    # line for the same SKU. This is the reason for the separate model.
    upsell_staff = models.CharField(max_length=128, blank=True)
    upsell_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    source_row_number = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "crm_online_order_line"
        constraints = [
            models.UniqueConstraint(fields=["order", "line_no"], name="ux_online_order_line_no"),
        ]

    def __str__(self) -> str:
        return f"{self.sku} x{self.quantity}"
