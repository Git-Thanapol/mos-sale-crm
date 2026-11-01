"""Before-image journal: the only thing rollback trusts.

Every business row a commit creates, changes or deletes is recorded here with
its before and after image, in the order it happened. Rollback replays the
entries in reverse, so it undoes exactly what this batch did and nothing a user
did afterwards to other rows.

Images are the row's concrete column values encoded as JSON; restoring parses
them back through each field's own `to_python`.
"""

from __future__ import annotations

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Max

from crm.customers.models import Customer
from crm.followcycles.models import FollowupCycle
from crm.importv2.models import ImportBatch, ImportJournalEntry
from crm.intake.models import IntakeItem
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment
from crm.orders.models import Order, OrderLine

#: Journal entity name -> model. The commit writes these and nothing else.
MODELS = {
    "CUSTOMER": Customer,
    "ORDER": Order,
    "ORDER_LINE": OrderLine,
    "LEAD": Lead,
    "INTAKE": IntakeItem,
    "OWNERSHIP_SEGMENT": OwnershipSegment,
    "LEAD_AUDIT": LeadAuditEvent,
    "CYCLE": FollowupCycle,
}


def snapshot(instance) -> dict:
    """The row's concrete columns as JSON-safe values, keyed by column attname."""
    values = {}
    for field in instance._meta.concrete_fields:
        values[field.attname] = field.value_from_object(instance)
    return json.loads(json.dumps(values, cls=DjangoJSONEncoder))


def restore_values(model, image: dict) -> dict:
    """Parse an image back into Python values keyed by column attname."""
    fields = {f.attname: f for f in model._meta.concrete_fields}
    return {name: fields[name].to_python(value) for name, value in image.items() if name in fields}


class Journal:
    """Appends entries for one commit attempt, with a gapless sequence."""

    def __init__(self, batch: ImportBatch, attempt: int) -> None:
        self.batch = batch
        self.attempt = attempt
        last = ImportJournalEntry.objects.filter(batch=batch, attempt=attempt).aggregate(m=Max("seq"))["m"]
        self._seq = last or 0
        self.entries = 0

    def _append(self, entity: str, entity_id: int, op: str, before, after) -> None:
        self._seq += 1
        self.entries += 1
        ImportJournalEntry.objects.create(
            batch=self.batch, attempt=self.attempt, seq=self._seq,
            entity=entity, entity_id=entity_id, op=op, before=before, after=after,
        )

    def created(self, entity: str, instance) -> None:
        self._append(entity, instance.pk, "CREATE", None, snapshot(instance))

    def updated(self, entity: str, before: dict, instance) -> None:
        self._append(entity, instance.pk, "UPDATE", before, snapshot(instance))

    def deleted(self, entity: str, before: dict, pk: int) -> None:
        self._append(entity, pk, "DELETE", before, None)


class LeadTracker:
    """Journal everything a block of lead logic changes, without that logic
    knowing about the journal.

    Existing services (ownership application, rollups, reactivation) write
    leads, ownership segments, audit events and cycles. The tracker records the
    state of those tables for the customer's leads before the block and, on
    `finish`, journals the difference: new rows as CREATE, changed rows as
    UPDATE with their before image.
    """

    def __init__(self, journal: Journal, customer: Customer) -> None:
        self.journal = journal
        self.customer = customer
        self._before = self._capture()

    def _lead_ids(self) -> list[int]:
        return list(Lead.objects.filter(customer=self.customer).values_list("pk", flat=True))

    def _capture(self) -> dict:
        ids = self._lead_ids()
        return {
            "LEAD": {r.pk: snapshot(r) for r in Lead.objects.filter(pk__in=ids)},
            "OWNERSHIP_SEGMENT": {r.pk: snapshot(r) for r in OwnershipSegment.objects.filter(lead_id__in=ids)},
            "LEAD_AUDIT": {r.pk: snapshot(r) for r in LeadAuditEvent.objects.filter(lead_id__in=ids)},
            "CYCLE": {r.pk: snapshot(r) for r in FollowupCycle.objects.filter(lead_id__in=ids)},
        }

    def finish(self) -> None:
        ids = self._lead_ids()
        after_rows = {
            "LEAD": Lead.objects.filter(pk__in=ids),
            "OWNERSHIP_SEGMENT": OwnershipSegment.objects.filter(lead_id__in=ids),
            "LEAD_AUDIT": LeadAuditEvent.objects.filter(lead_id__in=ids),
            "CYCLE": FollowupCycle.objects.filter(lead_id__in=ids),
        }
        for entity in ("LEAD", "CYCLE", "OWNERSHIP_SEGMENT", "LEAD_AUDIT"):
            before = self._before[entity]
            for row in after_rows[entity].order_by("pk"):
                if row.pk not in before:
                    self.journal.created(entity, row)
                elif snapshot(row) != before[row.pk]:
                    self.journal.updated(entity, before[row.pk], row)
