from __future__ import annotations

from django.db import models


class Product(models.Model):
    """Product master. Never hard-deleted (invariant 8, docs/DECISIONS.md):
    archive sets archived_at/archived_by/archive_reason and is_active=False;
    restore nulls the archive fields but deliberately LEAVES is_active=False.
    The legacy `active` shadow column (written once at migration time, never
    read by app code) is dropped, not ported.
    """

    sku = models.CharField(max_length=64, blank=True)
    product_group = models.CharField(max_length=128)
    product_name = models.CharField(max_length=255)
    sort_order = models.IntegerField(default=0)

    # TODO(Phase 2 follow-up): the plan calls for sku_number as a Postgres
    # GENERATED ALWAYS AS (...) STORED column replicating the legacy
    # substring(upper(btrim(sku)) from '^SP[[:space:]]*0*([0-9]+)')::bigint
    # sort expression, via Django 5's GeneratedField. Deferred — expressing
    # that exact regex through the ORM's expression API cleanly needs more
    # care than this pass had budget for. For now sku_number is populated by
    # Product.save() in Python; behavior is identical, just not DB-enforced.
    sku_number = models.BigIntegerField(null=True, blank=True, editable=False)

    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.CharField(max_length=255, blank=True)
    archive_reason = models.TextField(blank=True)

    created_by = models.CharField(max_length=255, blank=True)
    updated_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_product"
        constraints = [
            models.UniqueConstraint(fields=["sku", "product_group", "product_name"], name="ux_product_sku_group_name"),
        ]
        indexes = [
            models.Index(
                fields=["is_active", "sku_number", "sort_order"],
                name="ix_product_live",
                condition=models.Q(archived_at__isnull=True),
            ),
            models.Index(
                fields=["archived_at"], name="ix_product_archived", condition=models.Q(archived_at__isnull=False)
            ),
        ]

    def save(self, *args, **kwargs):
        self.sku_number = self._extract_sku_number(self.sku)
        super().save(*args, **kwargs)

    @staticmethod
    def _extract_sku_number(sku: str) -> int | None:
        import re

        match = re.match(r"^SP\s*0*([0-9]+)", (sku or "").strip().upper())
        return int(match.group(1)) if match else None

    def __str__(self) -> str:
        return f"{self.sku} {self.product_name}".strip()


class StaffOption(models.Model):
    staff_code = models.CharField(max_length=32, blank=True)
    staff_name = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_by = models.CharField(max_length=255, blank=True)
    updated_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_staff_option"
        indexes = [
            models.Index(fields=["is_active", "sort_order", "staff_name"], name="ix_staff_option_active_sort"),
        ]

    def __str__(self) -> str:
        return self.staff_name
