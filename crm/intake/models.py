"""D019 Global Unassigned: the pre-Lead intake queue.

A Customer exists, but no Team has been chosen, so no Lead exists yet. This is
deliberately NOT modelled as a Team called GLOBAL_UNASSIGNED and NOT as a Lead
with a null team -- 11_DO_NOT_CHANGE_CHECKLIST forbids both, because either
one makes "unassigned" look like a place a lead can live and lets team-scoped
queries leak it.

D024 identity: (customer, source_order_id). The import batch is explicitly NOT
part of it, so the same unresolved source Order arriving in a second batch
reuses this row instead of queueing the same work twice.
"""

from __future__ import annotations

from django.db import models

from crm.domain.enums import INTAKE_STATUSES, SOURCE_KINDS


class IntakeItem(models.Model):
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="intake_items"
    )
    #: The source system's order id. Blank when the source row carried none;
    #: blank still participates in the uniqueness, so a re-import of a
    #: reference-less row reuses this intake rather than duplicating it.
    source_order_id = models.CharField(max_length=64, blank=True)
    source_kind = models.CharField(max_length=32, choices=[(v, v) for v in SOURCE_KINDS])
    #: Which file or batch last mentioned it. Provenance, never identity.
    source_ref = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=32, choices=[(v, v) for v in INTAKE_STATUSES], default="GLOBAL_UNASSIGNED"
    )
    assigned_team = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_lead = models.ForeignKey(
        "leads.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_intake_item"
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "source_order_id"],
                name="ux_intake_customer_source_order",
            ),
        ]
        indexes = [
            # The queue: unresolved items oldest first, walked by keyset.
            models.Index(
                fields=["created_at", "id"],
                name="ix_intake_open",
                condition=models.Q(status="GLOBAL_UNASSIGNED"),
            ),
            models.Index(fields=["customer"], name="ix_intake_customer"),
        ]

    def __str__(self) -> str:
        return f"IntakeItem(customer={self.customer_id}, source_order={self.source_order_id!r})"
