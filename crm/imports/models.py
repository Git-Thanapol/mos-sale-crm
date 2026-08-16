"""Landing zone / audit trail — kept verbatim alongside the normalized core
(see docs/DECISIONS.md and the plan's schema section). All 34 columns of the
legacy crm_data_imports table. Never read by list pages; read only by the
importer itself, import history, and customer export (which needs raw
Excel headers like ที่อยู่จัดส่ง/ช่องทางขาย that were never promoted to
normalized columns — ตำบล was one of these until it got its own
Customer.subdistrict/StagingImportRow.subdistrict column).
"""

from __future__ import annotations

from django.db import models


class StagingImportRow(models.Model):
    import_batch_id = models.UUIDField()
    source_file_name = models.CharField(max_length=255, blank=True)
    sheet_name = models.CharField(max_length=255, blank=True)
    row_number = models.IntegerField(null=True, blank=True)
    uploaded_by = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    raw_data = models.JSONField(default=dict, blank=True)

    order_id = models.CharField(max_length=64, blank=True)
    url = models.TextField(blank=True)
    customer_name = models.CharField(max_length=255, blank=True)
    phone1 = models.CharField(max_length=32, blank=True)
    phone2 = models.CharField(max_length=32, blank=True)
    product_name = models.CharField(max_length=255, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    order_date = models.DateField(null=True, blank=True)
    province = models.CharField(max_length=128, blank=True)
    city = models.CharField(max_length=128, blank=True)
    subdistrict = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)
    address = models.TextField(blank=True)
    tracking_no = models.CharField(max_length=64, blank=True)
    carrier = models.CharField(max_length=64, blank=True)
    order_status = models.CharField(max_length=64, blank=True)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    owner = models.CharField(max_length=128, blank=True)
    staff_code = models.CharField(max_length=32, blank=True, db_index=True)
    source_type = models.CharField(max_length=32, blank=True)  # app writes 'manual' for manual orders
    import_status = models.CharField(max_length=16, default="valid")  # 'valid' | 'invalid'
    validation_error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=64, blank=True, db_index=True)  # sha256 hex, no unique constraint
    updated_by = models.CharField(max_length=255, blank=True)
    sale_type = models.CharField(max_length=16, blank=True)  # NEW_ORDER | UPSELL | FOLLOW
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_staging_import_row"
        indexes = [
            models.Index(fields=["import_batch_id", "-uploaded_at"], name="ix_staging_batch_uploaded"),
            models.Index(fields=["order_id"], name="ix_staging_order_no"),
        ]

    def __str__(self) -> str:
        return f"StagingImportRow(id={self.pk}, order_id={self.order_id!r})"


# TODO(Phase 2 follow-up): two indexes from the plan's index set need raw SQL
# (Django's ORM Index API doesn't express jsonb_path_ops / expression+trigram
# combos cleanly) — add via a RunSQL migration once pg_trgm is confirmed
# needed by a real search box (Phase 3):
#   ix_staging_raw_data           GIN (raw_data jsonb_path_ops)
#   ix_staging_raw_order_no_trgm  GIN ((raw_data->>'เลขคำสั่งซื้อ') gin_trgm_ops)
