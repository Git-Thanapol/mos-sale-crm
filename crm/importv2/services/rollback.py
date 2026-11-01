"""Rollback: undo exactly what one import batch wrote, from its journal.

Rollback replays the before-images in reverse and trusts nothing but the
journal: it never deletes "orders with this batch id", so a row someone else
created or changed later is never touched on the strength of a guess.

Before it changes anything it checks that every row still looks the way the
import left it. If a user has since edited an imported row, rollback stops with
ROLLBACK_FAILED and changes nothing at all: undoing the import over a person's
later edit is a decision for a human. Rollback is resumable: each undone entry is
stamped, so an interrupted run continues where it stopped.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from crm.domain.import_state import needs_rollback
from crm.importv2.models import ImportBatch, ImportJournalEntry, ImportOperation
from crm.importv2.services.commit import _remember, _replay, _require_admin
from crm.importv2.services.journal import MODELS, restore_values, snapshot
from crm.importv2.services.state import transition

DEFAULT_ROLLBACK_CHUNK_SIZE = 100


def _pending(batch: ImportBatch):
    return batch.journal.filter(rolled_back_at__isnull=True).order_by("-attempt", "-seq")


def find_drift(batch: ImportBatch) -> list[dict]:
    """Rows that no longer look the way this import left them."""
    latest: dict[tuple[str, int], ImportJournalEntry] = {}
    for entry in batch.journal.filter(rolled_back_at__isnull=True).order_by("attempt", "seq"):
        latest[(entry.entity, entry.entity_id)] = entry
    drift = []
    for (entity, entity_id), entry in latest.items():
        row = MODELS[entity].objects.filter(pk=entity_id).first()
        if entry.op == "DELETE":
            if row is not None:
                drift.append({"entity": entity, "id": entity_id, "why": "row was recreated after the import"})
        elif row is None:
            drift.append({"entity": entity, "id": entity_id, "why": "row was deleted after the import"})
        elif snapshot(row) != entry.after:
            drift.append({"entity": entity, "id": entity_id, "why": "row was edited after the import"})
    return drift


def _undo(entry: ImportJournalEntry, totals: dict) -> None:
    model = MODELS[entry.entity]
    if entry.op == "CREATE":
        model.objects.filter(pk=entry.entity_id).delete()
        totals["deleted"] += 1
    elif entry.op == "UPDATE":
        model.objects.filter(pk=entry.entity_id).update(**restore_values(model, entry.before))
        totals["restored"] += 1
    else:  # DELETE: put the row back with its original id
        if not model.objects.filter(pk=entry.entity_id).exists():
            model(**restore_values(model, entry.before)).save(force_insert=True)
        totals["recreated"] += 1
    entry.rolled_back_at = timezone.now()
    entry.save(update_fields=["rolled_back_at"])


def rollback_batch(
    *,
    actor,
    batch: ImportBatch,
    reason: str,
    operation_key: str,
    chunk_size: int = DEFAULT_ROLLBACK_CHUNK_SIZE,
) -> dict:
    """Undo a committed (or partly committed) batch. Admin only, idempotent per key."""
    _require_admin(actor)
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay
    if batch.status != "ROLLING_BACK" and not needs_rollback(batch.status):
        raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} cannot be rolled back")
    chunk_size = max(1, min(int(chunk_size), 500))

    if batch.status != "ROLLING_BACK":
        transition(batch, "ROLLING_BACK", actor=actor, reason=reason or "rollback")

    drift = find_drift(batch)
    if drift:
        transition(batch, "ROLLBACK_FAILED", actor=actor, reason=f"{len(drift)} row(s) changed after the import")
        return _remember(
            batch, operation_key, "rollback",
            {"status": "ROLLBACK_FAILED", "drift": drift[:50], "deleted": 0, "restored": 0, "recreated": 0},
        )

    totals = {"deleted": 0, "restored": 0, "recreated": 0}
    while True:
        chunk = list(_pending(batch)[:chunk_size])
        if not chunk:
            break
        with transaction.atomic():
            for entry in chunk:
                _undo(entry, totals)

    with transaction.atomic():
        transition(batch, "ROLLED_BACK", actor=actor, reason=reason or "rollback complete")
        return _remember(batch, operation_key, "rollback", {"status": "ROLLED_BACK", "drift": [], **totals})


__all__ = ["rollback_batch", "find_drift", "DEFAULT_ROLLBACK_CHUNK_SIZE", "ImportOperation"]
