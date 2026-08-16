from __future__ import annotations

from django.db import models

from crm.core.scoping import StaffScopedQuerySet

SALE_TYPE_NEW_ORDER = "NEW_ORDER"
SALE_TYPE_UPSELL = "UPSELL"
SALE_TYPE_FOLLOW = "FOLLOW"  # excluded from revenue — see docs/DECISIONS.md invariant 6
SALE_TYPE_CHOICES = [
    (SALE_TYPE_NEW_ORDER, SALE_TYPE_NEW_ORDER),
    (SALE_TYPE_UPSELL, SALE_TYPE_UPSELL),
    (SALE_TYPE_FOLLOW, SALE_TYPE_FOLLOW),
]
REVENUE_SALE_TYPES = (SALE_TYPE_NEW_ORDER, SALE_TYPE_UPSELL)


class Order(models.Model):
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="orders")

    order_no = models.CharField(max_length=64, blank=True)
    order_date = models.DateField(null=True, blank=True)
    sale_type = models.CharField(max_length=16, choices=SALE_TYPE_CHOICES, default=SALE_TYPE_NEW_ORDER)
    order_status = models.CharField(max_length=64, blank=True)
    carrier = models.CharField(max_length=64, blank=True)
    tracking_no = models.CharField(max_length=64, blank=True)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    owner_display = models.CharField(max_length=128, blank=True)
    staff_code = models.CharField(max_length=32, blank=True, db_index=True)

    source_type = models.CharField(max_length=32, blank=True)  # 'manual' | 'import'
    uploaded_by = models.CharField(max_length=255, blank=True)
    import_batch_id = models.UUIDField(null=True, blank=True)
    source_import_row = models.ForeignKey(
        "imports.StagingImportRow", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STAFF_CODE_PATH = "staff_code"
    objects = StaffScopedQuerySet.as_manager()

    class Meta:
        db_table = "crm_order"
        constraints = [
            models.UniqueConstraint(
                fields=["order_no", "customer"],
                name="ux_order_no_customer",
                condition=~models.Q(order_no=""),
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "-order_date", "-id"], name="ix_order_customer_date"),
            models.Index(
                fields=["order_date", "staff_code"],
                name="ix_order_revenue_staff",
                condition=models.Q(sale_type__in=REVENUE_SALE_TYPES),
            ),
            models.Index(
                fields=["order_date", "owner_display"],
                name="ix_order_revenue_owner",
                condition=models.Q(sale_type__in=REVENUE_SALE_TYPES),
            ),
            models.Index(fields=["uploaded_by", "created_at"], name="ix_order_uploaded_by_created"),
            models.Index(fields=["import_batch_id"], name="ix_order_batch"),
        ]

    def __str__(self) -> str:
        return self.order_no or f"Order(id={self.pk})"


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")

    sku = models.CharField(max_length=64, blank=True)
    product_name = models.CharField(max_length=255, blank=True)
    # Legacy `quantity` column type is ambiguous (int in one migration,
    # numeric in the runtime DDL that actually won — see
    # docs/legacy/data-layer-report.md). Business invariant 7 requires
    # qty > 0 and line items are always whole units, so PositiveIntegerField
    # is the deliberate, simpler choice here rather than carrying the
    # legacy ambiguity forward.
    quantity = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    source_import_row = models.ForeignKey(
        "imports.StagingImportRow", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "crm_order_line"
        constraints = [
            models.UniqueConstraint(fields=["order", "sku", "product_name"], name="ux_line_order_sku_name"),
        ]
        indexes = [
            models.Index(fields=["sku"], name="ix_line_sku"),
        ]

    def __str__(self) -> str:
        return f"{self.sku} x{self.quantity}"
