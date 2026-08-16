from __future__ import annotations

from django.db import models

from crm.core.scoping import StaffScopedQuerySet
from crm.core.thai import (
    DEFAULT_FOLLOWUP_PRIORITY,
    DEFAULT_LEAD_STATUS,
    FOLLOWUP_PRIORITY_OPTIONS,
    FOLLOWUP_STATUS_OPTIONS,
    LEAD_STATUS_OPTIONS,
)
from crm.core.thai import priority_rank as compute_priority_rank


class Followup(models.Model):
    """One row per customer (OneToOne, not the legacy's separate
    customer_key-keyed table with two incompatible key formats — see
    docs/DECISIONS.md #10). `status` is the ONE canonical vocabulary;
    `priority_rank` is stored so no Thai literal ever needs to appear in an
    ORDER BY again (the fix for the CP874 mojibake bug, see
    docs/DECISIONS.md #9).
    """

    customer = models.OneToOneField(
        "customers.Customer", on_delete=models.CASCADE, related_name="followup"
    )

    lead_status = models.CharField(
        max_length=32, choices=[(v, v) for v in LEAD_STATUS_OPTIONS], default=DEFAULT_LEAD_STATUS
    )
    status = models.CharField(
        max_length=32, choices=[(v, v) for v in FOLLOWUP_STATUS_OPTIONS], default="none"
    )
    priority = models.CharField(
        max_length=16,
        choices=[(v, v) for v in FOLLOWUP_PRIORITY_OPTIONS],
        default=DEFAULT_FOLLOWUP_PRIORITY,
    )
    priority_rank = models.SmallIntegerField(editable=False, default=2)

    next_followup_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    staff_code = models.CharField(max_length=32, blank=True, db_index=True)
    owner_display = models.CharField(max_length=128, blank=True)
    updated_by = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STAFF_CODE_PATH = "customer__staff_code"
    objects = StaffScopedQuerySet.as_manager()

    class Meta:
        db_table = "crm_followup"
        indexes = [
            models.Index(
                fields=["staff_code", "next_followup_date", "-priority_rank", "id"],
                name="ix_followup_queue",
            ),
            models.Index(
                fields=["next_followup_date", "-priority_rank", "id"], name="ix_followup_queue_all"
            ),
            models.Index(fields=["status", "lead_status", "priority_rank"], name="ix_followup_status"),
            models.Index(
                fields=["next_followup_date"],
                name="ix_followup_due_open",
                condition=~models.Q(status="done"),
            ),
        ]

    def save(self, *args, **kwargs):
        self.priority_rank = compute_priority_rank(self.priority)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Followup(customer_id={self.customer_id}, priority={self.priority})"
