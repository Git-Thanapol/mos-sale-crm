from __future__ import annotations

from django.db import models
from django.db.models import F

from crm.core.scoping import StaffScopedQuerySet


class Customer(models.Model):
    """The customer-identity anchor. phone_key is computed once at write
    time by crm.core.identity.phone_key and stored — this is the single
    biggest structural fix over the legacy schema: no row_number() window
    function over the whole table on every page render, just an indexed
    unique lookup. See docs/legacy/data-layer-report.md and
    tests/unit/test_identity.py::test_phone_key_rule_is_not_transitive for
    why the rule must not be "improved" into a phone-graph union-find.
    """

    phone_key = models.CharField(max_length=64, unique=True)
    phone1 = models.CharField(max_length=32, blank=True)
    phone2 = models.CharField(max_length=32, blank=True)
    customer_name = models.CharField(max_length=255, blank=True)

    province = models.CharField(max_length=128, blank=True)
    city = models.CharField(max_length=128, blank=True)
    subdistrict = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)
    address = models.TextField(blank=True)
    url = models.TextField(blank=True)

    owner_display = models.CharField(max_length=128, blank=True)  # Thai display name
    staff_code = models.CharField(max_length=32, blank=True, db_index=True)  # THE authorization key

    # Rollups, maintained by crm.orders.services on write and by
    # `manage.py recompute_rollups` for backfill/repair.
    last_order_date = models.DateField(null=True, blank=True)
    last_order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    order_count = models.PositiveIntegerField(default=0)

    source_import_row = models.ForeignKey(
        "imports.StagingImportRow", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STAFF_CODE_PATH = "staff_code"
    objects = StaffScopedQuerySet.as_manager()

    class Meta:
        db_table = "crm_customer"
        indexes = [
            models.Index(
                fields=["staff_code", "-last_order_date", "-id"], name="ix_customer_scope_recent"
            ),
            # NULLS LAST, not the plain-field default (NULLS FIRST on DESC) —
            # crm.online_orders can create phone-only customers with no
            # crm_order rollup (last_order_date stays NULL), and those must
            # sort to the bottom of the list, not the top. Matches the
            # ordering in crm.customers.selectors.customer_page /
            # customer_export_queryset — keep both in sync.
            models.Index(
                F("last_order_date").desc(nulls_last=True),
                F("updated_at").desc(),
                F("id").desc(),
                name="ix_customer_recent_nl",
            ),
        ]

    def __str__(self) -> str:
        return self.customer_name or self.phone_key
