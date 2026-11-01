"""Lead = Customer + Team.

The concept the existing schema has no room for. Today ownership is a pair of
strings on Customer (`staff_code`, `owner_display`), which cannot express "this
customer is worked by two teams", cannot carry a per-team lifecycle, and has
nowhere to record an ownership baseline. 02_LOCKED_BUSINESS_RULES.md requires
Customer and Lead to stay separate concepts and a Lead to be exactly
Customer + Team.

Invariants enforced here:
- `lead_key` is `{team.code}:{customer.customer_uid}`, deterministic and
  immutable once written, so audit and ownership rows can reference it.
- `team` is NOT NULL. Team-unknown work is pre-Lead intake (D019,
  crm.intake), never a Lead with a null team and never a fake
  GLOBAL_UNASSIGNED team.
- STOPPED requires a reason, and routing_state is separate from
  lifecycle_status: a STOPPED lead keeps its owner and is not "unassigned".

The denormalised `access_*` arrays are read models maintained by
crm.leads.services; nothing accepts them from a caller.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from crm.core.scoping import StaffScopedQuerySet
from crm.domain.enums import (
    LEAD_LIFECYCLE_STATUSES,
    OWNERSHIP_POLICIES,
    ROUTING_STATES,
    STOP_REASONS,
)


class Lead(models.Model):
    #: `{team.code}:{customer.customer_uid}` — set once, never recomputed.
    lead_key = models.CharField(max_length=96, unique=True, editable=False)

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="leads"
    )
    team = models.ForeignKey("teams.Team", on_delete=models.PROTECT, related_name="leads")
    caretaker = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="caretaker_leads",
    )

    lifecycle_status = models.CharField(
        max_length=32, choices=[(v, v) for v in LEAD_LIFECYCLE_STATUSES], default="NEW"
    )
    routing_state = models.CharField(
        max_length=32, choices=[(v, v) for v in ROUTING_STATES], default="TEAM_UNASSIGNED"
    )

    ownership_started_at = models.DateTimeField(null=True, blank=True)
    #: Snapshot of the team policy at the last ownership evaluation, so a later
    #: policy change does not silently rewrite how past ownership was decided.
    ownership_policy = models.CharField(
        max_length=32, choices=[(v, v) for v in OWNERSHIP_POLICIES]
    )

    current_cycle_id = models.BigIntegerField(null=True, blank=True)

    last_order_at = models.DateTimeField(null=True, blank=True)
    last_order_id = models.BigIntegerField(null=True, blank=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_spend = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    #: Cached operational copies; the Note rows stay authoritative.
    latest_note = models.TextField(blank=True)
    latest_note_updated_at = models.DateTimeField(null=True, blank=True)

    next_follow_at = models.DateTimeField(null=True, blank=True)

    missed_channel_count = models.PositiveIntegerField(default=0)
    no_answer_streak = models.PositiveIntegerField(default=0)

    stopped_reason = models.CharField(
        max_length=32, choices=[(v, v) for v in STOP_REASONS], blank=True
    )
    stopped_at = models.DateTimeField(null=True, blank=True)

    access_team_ids = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    access_user_ids = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    STAFF_CODE_PATH = "customer__staff_code"
    #: Read by the TEAM_LEAD scoping branch (Task 14). Declared with the model
    #: so the whole scoping contract lives in one place.
    TEAM_PATH = "access_team_ids"
    objects = StaffScopedQuerySet.as_manager()

    class Meta:
        db_table = "crm_lead"
        constraints = [
            models.UniqueConstraint(fields=["team", "customer"], name="ux_lead_team_customer"),
        ]
        indexes = [
            # The operational queues: team + routing state, walked by keyset on
            # (last_order_at, id).
            models.Index(
                fields=["team", "routing_state", "last_order_at", "id"],
                name="ix_lead_queue",
            ),
            models.Index(fields=["caretaker", "next_follow_at", "id"], name="ix_lead_my_work"),
            # Reallocation review and last-order-age buckets never look at
            # STOPPED leads, so they are kept out of the index entirely.
            models.Index(
                fields=["last_order_at", "id"],
                name="ix_lead_age_open",
                condition=~models.Q(lifecycle_status="STOPPED"),
            ),
            models.Index(fields=["missed_channel_count"], name="ix_lead_missed_channel"),
        ]

    def __str__(self) -> str:
        return self.lead_key or f"Lead(id={self.pk})"

    @staticmethod
    def build_key(team_code: str, customer_uid) -> str:
        return f"{team_code}:{customer_uid}"

    def clean(self):
        super().clean()
        if self.lifecycle_status == "STOPPED" and not self.stopped_reason:
            raise ValidationError({"stopped_reason": "STOPPED requires a stoppedReason"})
        if self.lifecycle_status not in LEAD_LIFECYCLE_STATUSES:
            raise ValidationError({"lifecycle_status": f"unknown status {self.lifecycle_status!r}"})
        if self.routing_state not in ROUTING_STATES:
            raise ValidationError({"routing_state": f"unknown routing state {self.routing_state!r}"})
        if self.stopped_reason and self.stopped_reason not in STOP_REASONS:
            raise ValidationError({"stopped_reason": f"unknown reason {self.stopped_reason!r}"})
        if self.ownership_policy not in OWNERSHIP_POLICIES:
            raise ValidationError({"ownership_policy": f"unknown policy {self.ownership_policy!r}"})

    def save(self, *args, **kwargs):
        if not self.lead_key:
            self.lead_key = self.build_key(self.team.code, self.customer.customer_uid)
        super().save(*args, **kwargs)
