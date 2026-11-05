"""Follow-up cycles, notes, contact attempts and appointments.

Four things that all hang off a Lead and change together, so they live in one
app rather than being split by technical layer.

The rules these models exist to hold (11_DO_NOT_CHANGE_CHECKLIST.md):
- Historical follow-up cycles are not deleted. A new Order ends the current
  cycle and starts another; the old one keeps its reason and stays queryable.
- Notes are Lead/Cycle-owned, never Order-owned. A note may reference an
  Order, but deleting that Order must not delete the note.
"""

from __future__ import annotations

from django.db import models

from crm.domain.enums import (
    APPOINTMENT_STATUSES,
    CONTACT_ATTEMPT_RESULTS,
    CYCLE_END_REASONS,
)

CYCLE_STATUSES = ("ACTIVE", "ENDED")

#: Only NO_ANSWER feeds the streak. The prototype's policy key is
#: `maxNoAnswerAttempts`, "consecutive no-answer attempts before
#: STOP_RECOMMENDED" -- counting BUSY or REFUSED as well would reach the
#: threshold faster than the team configured, and a stop recommendation is a
#: step toward ending contact with a customer.
NO_ANSWER_RESULTS = frozenset({"NO_ANSWER"})


class FollowupCycle(models.Model):
    lead = models.ForeignKey("leads.Lead", on_delete=models.CASCADE, related_name="cycles")
    trigger_order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=16, choices=[(v, v) for v in CYCLE_STATUSES], default="ACTIVE")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(
        max_length=32, choices=[(v, v) for v in CYCLE_END_REASONS], blank=True
    )
    #: While true, this cycle's follow-up lock holds ownership under
    #: LATEST_ORDER_WITH_LOCK. Defaults false so a new cycle does not silently
    #: protect every lead from reassignment.
    lock_active = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_followup_cycle"
        constraints = [
            models.UniqueConstraint(
                fields=["lead"],
                condition=models.Q(status="ACTIVE"),
                name="ux_cycle_one_active",
            ),
        ]
        indexes = [
            models.Index(fields=["lead", "-started_at"], name="ix_cycle_lead_started"),
            models.Index(
                fields=["lead"],
                name="ix_cycle_active",
                condition=models.Q(status="ACTIVE"),
            ),
        ]

    def __str__(self) -> str:
        return f"FollowupCycle(lead={self.lead_id}, status={self.status})"


class Note(models.Model):
    """Belongs to the Lead, optionally to a cycle, and only *references* an Order.

    `order` is SET_NULL on purpose: 11_DO_NOT_CHANGE_CHECKLIST requires notes
    to stay Lead/Cycle-owned, so deleting an Order must leave the note intact
    with a null reference rather than cascading it away.
    """

    lead = models.ForeignKey("leads.Lead", on_delete=models.CASCADE, related_name="notes")
    cycle = models.ForeignKey(
        FollowupCycle, null=True, blank=True, on_delete=models.SET_NULL, related_name="notes"
    )
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    body = models.TextField()
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_lead_note"
        indexes = [
            models.Index(fields=["lead", "-created_at"], name="ix_note_lead_created"),
            models.Index(fields=["cycle", "-created_at"], name="ix_note_cycle_created"),
        ]

    def __str__(self) -> str:
        return f"Note(lead={self.lead_id})"


class ContactAttempt(models.Model):
    """One call attempt and its result.

    "ติดต่อไม่ได้" is a RESULT, not a lifecycle status
    (02_LOCKED_BUSINESS_RULES.md). Nothing here writes lifecycle_status; the
    service decides whether to recommend a stop, and only a team that opted
    into autoStop gets an automatic one.
    """

    lead = models.ForeignKey("leads.Lead", on_delete=models.CASCADE, related_name="contact_attempts")
    cycle = models.ForeignKey(
        FollowupCycle, null=True, blank=True, on_delete=models.SET_NULL, related_name="contact_attempts"
    )
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    attempted_at = models.DateTimeField()
    result = models.CharField(max_length=32, choices=[(v, v) for v in CONTACT_ATTEMPT_RESULTS])
    note = models.TextField(blank=True)

    class Meta:
        db_table = "crm_contact_attempt"
        indexes = [
            models.Index(fields=["lead", "-attempted_at"], name="ix_attempt_lead_time"),
            models.Index(fields=["cycle", "-attempted_at"], name="ix_attempt_cycle_time"),
        ]

    def __str__(self) -> str:
        return f"ContactAttempt(lead={self.lead_id}, result={self.result})"


class Appointment(models.Model):
    """A scheduled follow-up, always in Asia/Bangkok.

    `timezone_name` is stored and constrained rather than assumed, so a future
    multi-timezone deployment cannot silently reinterpret existing rows. The
    field is named timezone_name because `timezone` would shadow
    django.utils.timezone in every module that imports both.
    """

    lead = models.ForeignKey("leads.Lead", on_delete=models.CASCADE, related_name="appointments")
    cycle = models.ForeignKey(
        FollowupCycle, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments"
    )
    scheduled_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=32, default="Asia/Bangkok")
    status = models.CharField(
        max_length=16, choices=[(v, v) for v in APPOINTMENT_STATUSES], default="SCHEDULED"
    )
    note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_appointment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(timezone_name="Asia/Bangkok"),
                name="chk_appointment_bangkok",
            ),
        ]
        indexes = [
            models.Index(fields=["lead", "-scheduled_at"], name="ix_appointment_lead_time"),
            models.Index(
                fields=["scheduled_at"],
                name="ix_appointment_open",
                condition=models.Q(status="SCHEDULED"),
            ),
        ]

    def __str__(self) -> str:
        return f"Appointment(lead={self.lead_id}, at={self.scheduled_at})"
