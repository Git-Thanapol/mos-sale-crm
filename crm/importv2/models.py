"""Persistence for the CRM V2 import pipeline.

The prototype kept batches, staged rows, conflicts and the before-image journal
in Firestore sub-collections behind a CommitStore. Here they are ordinary
tables. The shapes mirror the prototype's entities so the pure rules in
crm.domain (state machine, confirm gate, row evaluation, conflict scope) apply
to them unchanged; the services translate between rows and those dicts.
"""

from __future__ import annotations

import uuid

from django.db import models

from crm.domain.enums import SOURCE_KINDS
from crm.domain.import_state import IMPORT_BATCH_STATUSES

CONFLICT_KINDS = ("OWNERSHIP", "IDENTITY", "REACTIVATION", "MAPPING", "STALE", "REVERSAL", "STOPPED_TARGET")
CONFLICT_SEVERITIES = ("BLOCKING", "WARNING")
CONFLICT_STATUSES = ("OPEN", "RESOLVED", "AUTO_RESOLVED")
CONFLICT_RESOLUTIONS = (
    "AUTO_MATCH",
    "NEW_CUSTOMER",
    "KEEP_CURRENT",
    "REASSIGN",
    "REACTIVATE",
    "KEEP_STOPPED",
    "KEEP_EXISTING",
    "ACCEPT_INCOMING",
    "APPROVE_REVERSAL",
)
ROW_CLASSIFICATIONS = ("NEW_ORDER", "SAFE_UPDATE", "UNCHANGED", "STALE_INPUT", "CONFLICT", "INVALID")
JOURNAL_ENTITIES = ("CUSTOMER", "ORDER", "ORDER_LINE", "LEAD", "INTAKE")
JOURNAL_OPS = ("CREATE", "UPDATE", "DELETE")


def _choices(values):
    return [(value, value) for value in values]


class ImportBatch(models.Model):
    batch_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file_name = models.CharField(max_length=255)
    source_kind = models.CharField(max_length=32, choices=_choices(SOURCE_KINDS))
    mapping_profile_id = models.CharField(max_length=64)
    mapping_profile_version = models.PositiveIntegerField(default=1)
    file_sha256 = models.CharField(max_length=64)
    #: The uploaded bytes, kept for audit and re-preview. Capped at upload.
    raw_bytes = models.BinaryField(null=True, blank=True, editable=False)

    status = models.CharField(
        max_length=24, choices=_choices(IMPORT_BATCH_STATUSES), default="PREVIEW"
    )
    row_count = models.PositiveIntegerField(default=0)
    #: Output of ImportValidation (issues with BLOCK / WARNING severity).
    validation = models.JSONField(default=dict, blank=True)
    preview_hash = models.CharField(max_length=64, blank=True)
    preview_aggregate = models.JSONField(default=dict, blank=True)

    #: Bangkok calendar day, plus how it was chosen and who confirmed it.
    batch_business_date = models.DateField(null=True, blank=True)
    batch_date_meta = models.JSONField(default=dict, blank=True)

    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    committed_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    #: Append-only list of {from, to, at, actor, reason} (prototype transitionHistory).
    transition_history = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_import_batch"
        indexes = [models.Index(fields=["-created_at", "-id"], name="ix_importbatch_recent")]

    def __str__(self) -> str:
        return f"{self.file_name} [{self.status}]"


class ImportRowPlan(models.Model):
    """One staged ORDER (the file is line-level; lines of one order are grouped)."""

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="plans")
    #: Index of the first source row of the group (1-based, as the operator sees it).
    row_index = models.PositiveIntegerField()
    order_id = models.CharField(max_length=64)
    #: Prototype RowFacts plus the line items and the customer fields to write.
    facts = models.JSONField(default=dict)
    classification = models.CharField(max_length=16, choices=_choices(ROW_CLASSIFICATIONS))
    evaluation = models.JSONField(default=dict)
    #: Identity outcome: NEW_CUSTOMER / AUTO_MATCH / HUMAN_REVIEW and the candidate.
    identity_kind = models.CharField(max_length=16, blank=True)
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: Source rows that failed mapping; the group is INVALID when non-empty.
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "crm_import_row_plan"
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_index"], name="ux_importplan_batch_row"),
        ]
        indexes = [models.Index(fields=["batch", "classification"], name="ix_importplan_class")]


class ImportConflict(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="conflicts")
    #: Stable id within the batch: `{row_index}:{kind}:{n}`.
    conflict_id = models.CharField(max_length=64)
    row_index = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=_choices(CONFLICT_KINDS))
    severity = models.CharField(max_length=8, choices=_choices(CONFLICT_SEVERITIES))
    status = models.CharField(max_length=14, choices=_choices(CONFLICT_STATUSES), default="OPEN")
    team = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    affected_team_codes = models.JSONField(default=list, blank=True)
    summary = models.TextField()
    source_row_ref = models.CharField(max_length=128, blank=True)
    options = models.JSONField(default=list)
    #: Optimistic-concurrency version, bumped on every resolution.
    version = models.PositiveIntegerField(default=1)
    resolution = models.CharField(max_length=24, blank=True, choices=_choices(CONFLICT_RESOLUTIONS))
    resolved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "crm_import_conflict"
        constraints = [
            models.UniqueConstraint(fields=["batch", "conflict_id"], name="ux_importconflict_batch_id"),
        ]
        indexes = [
            models.Index(fields=["batch", "status", "severity"], name="ix_importconflict_open"),
        ]
        ordering = ["row_index", "id"]


class ImportJournalEntry(models.Model):
    """Before/after image of one business write, the basis of rollback."""

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="journal")
    attempt = models.PositiveIntegerField()
    seq = models.PositiveIntegerField()
    entity = models.CharField(max_length=16, choices=_choices(JOURNAL_ENTITIES))
    entity_id = models.BigIntegerField()
    op = models.CharField(max_length=8, choices=_choices(JOURNAL_OPS))
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "crm_import_journal"
        constraints = [
            models.UniqueConstraint(fields=["batch", "attempt", "seq"], name="ux_importjournal_seq"),
        ]
        indexes = [models.Index(fields=["entity", "entity_id"], name="ix_importjournal_entity")]


class ImportOperation(models.Model):
    """Idempotency record: replaying an operation key returns the stored result."""

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="operations")
    operation_key = models.CharField(max_length=128)
    action = models.CharField(max_length=24)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_import_operation"
        constraints = [
            models.UniqueConstraint(fields=["batch", "operation_key"], name="ux_importop_batch_key"),
        ]
