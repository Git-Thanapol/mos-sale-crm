"""Rollback: undo exactly what one import batch wrote, from its journal.

Rollback replays the before-images in reverse and trusts nothing but the
journal: it never deletes "orders with this batch id", so a row someone else
created or changed later is never touched on the strength of a guess.

Safety, in layers:
 - `find_drift` is a fast pre-check over the whole journal;
 - the authoritative check happens PER ENTRY inside each chunk transaction,
   with the target row locked (`select_for_update`): the row must still look the
   way the import left it, or the chunk is rolled back untouched and the batch
   stops in ROLLBACK_FAILED;
 - a row the import CREATED is never deleted while rows the import did not
   journal still depend on it (notes, appointments, contact attempts, intake
   items, ...): deleting it would silently cascade-delete newer data;
 - drift ignores auto-managed timestamps (`updated_at`, auto_now fields), so a
   routine touch does not make a batch permanently un-rollbackable.

Rollback is resumable (each undone entry is stamped). ROLLBACK_FAILED is not a
dead end: after a human reconciles the listed rows an Admin may call
`retry_rollback`, which re-enters ROLLING_BACK and continues from the stamps.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import CASCADE
from django.utils import timezone

from crm.domain.import_state import needs_rollback
from crm.importv2.models import ImportBatch, ImportJournalEntry, ImportOperation
from crm.importv2.services.commit import _remember, _replay, _require_admin
from crm.importv2.services.journal import MODELS, restore_values, snapshot
from crm.importv2.services.locks import batch_running
from crm.importv2.services.state import transition

DEFAULT_ROLLBACK_CHUNK_SIZE = 100
_ENTITY_OF_MODEL = {model: entity for entity, model in MODELS.items()}


class RollbackBlocked(Exception):
    """A single entry cannot be undone safely. Carries the reason shown to the Admin."""

    def __init__(self, entry: ImportJournalEntry, why: str) -> None:
        super().__init__(why)
        self.item = {"entity": entry.entity, "id": entry.entity_id, "why": why}


def _auto_managed(model) -> frozenset[str]:
    """Column names the ORM stamps by itself (auto_now / auto_now_add)."""
    return frozenset(
        f.attname
        for f in model._meta.concrete_fields
        if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False)
    )


def _comparable(model, image: dict | None) -> dict | None:
    if image is None:
        return None
    ignored = _auto_managed(model)
    return {k: v for k, v in image.items() if k not in ignored}


def _same_business_state(row, image: dict | None) -> bool:
    model = type(row)
    return _comparable(model, snapshot(row)) == _comparable(model, image)


def _pending(batch: ImportBatch):
    return batch.journal.filter(rolled_back_at__isnull=True).order_by("-attempt", "-seq")


def find_drift(batch: ImportBatch) -> list[dict]:
    """Rows that no longer look the way this import left them (business fields
    only: auto-managed timestamps are ignored)."""
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
        elif not _same_business_state(row, entry.after):
            drift.append({"entity": entity, "id": entity_id, "why": "row was edited after the import"})
    return drift


def _journalled_creates(batch: ImportBatch) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for entity, entity_id in batch.journal.filter(op="CREATE").values_list("entity", "entity_id"):
        out.setdefault(entity, set()).add(entity_id)
    return out


def foreign_dependents(row, creates: dict[str, set[int]]) -> list[str]:
    """Rows that reference `row` and that this import did not create.

    Deleting `row` would cascade-delete (or null out) them, so the delete is
    refused (SET_NULL links are fine: the row survives). Rows the batch itself created are fine: rollback removes them too.
    """
    found = []
    for rel in type(row)._meta.related_objects:
        if rel.many_to_many:
            if getattr(row, rel.get_accessor_name()).exists():
                found.append(f"{rel.related_model._meta.label} (many-to-many)")
            continue
        related = rel.related_model
        if rel.on_delete is not CASCADE:
            continue  # SET_NULL keeps the row (e.g. audit trail); only CASCADE destroys data
        own = creates.get(_ENTITY_OF_MODEL.get(related, ""), set())
        qs = related._default_manager.filter(**{rel.field.name: row})
        if own:
            qs = qs.exclude(pk__in=own)
        count = qs.count()
        if count:
            found.append(f"{count} x {related._meta.label}")
    return found


def _undo(entry: ImportJournalEntry, totals: dict, creates: dict[str, set[int]]) -> None:
    """Undo one entry. Called inside the chunk transaction: the target row is
    locked and re-verified, and anything unexpected raises RollbackBlocked."""
    model = MODELS[entry.entity]
    row = model.objects.select_for_update().filter(pk=entry.entity_id).first()
    if entry.op == "DELETE":
        if row is not None:
            raise RollbackBlocked(entry, "row was recreated after the import")
        model(**restore_values(model, entry.before)).save(force_insert=True)
        totals["recreated"] += 1
    else:
        if row is None:
            raise RollbackBlocked(entry, "row was deleted after the import")
        if not _same_business_state(row, entry.after):
            raise RollbackBlocked(entry, "row was edited after the import")
        if entry.op == "CREATE":
            dependents = foreign_dependents(row, creates)
            if dependents:
                raise RollbackBlocked(
                    entry,
                    "newer data depends on this created row and would be deleted with it: "
                    + ", ".join(dependents),
                )
            row.delete()
            totals["deleted"] += 1
        else:  # UPDATE
            model.objects.filter(pk=entry.entity_id).update(**restore_values(model, entry.before))
            totals["restored"] += 1
    entry.rolled_back_at = timezone.now()
    entry.save(update_fields=["rolled_back_at"])


def _require_reason(reason: str) -> None:
    if not (reason or "").strip():
        raise ValueError("INVALID_ARGUMENT: a reason is required")


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
    _require_reason(reason)
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay
    with batch_running(batch):
        batch.refresh_from_db()
        replay = _replay(batch, operation_key)
        if replay is not None:
            return replay
        return _rollback_locked(actor=actor, batch=batch, reason=reason, operation_key=operation_key, chunk_size=chunk_size)


def retry_rollback(
    *,
    actor,
    batch: ImportBatch,
    reason: str,
    operation_key: str,
    chunk_size: int = DEFAULT_ROLLBACK_CHUNK_SIZE,
) -> dict:
    """Admin-only way out of ROLLBACK_FAILED, after a human reconciled the rows
    the failure listed. Re-enters ROLLING_BACK (audited, with the reason) and
    resumes from the entries not yet undone."""
    _require_admin(actor)
    _require_reason(reason)
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay
    with batch_running(batch):
        batch.refresh_from_db()
        replay = _replay(batch, operation_key)
        if replay is not None:
            return replay
        if batch.status != "ROLLBACK_FAILED":
            raise ValueError(f"ILLEGAL_STATE: only a ROLLBACK_FAILED batch can retry rollback, not {batch.status}")
        # crm.domain.import_state has no ROLLBACK_FAILED -> ROLLING_BACK edge;
        # state.transition allows exactly this one edge behind admin_retry.
        transition(batch, "ROLLING_BACK", actor=actor, reason=f"retry rollback: {reason.strip()}", admin_retry=True)
        return _rollback_locked(actor=actor, batch=batch, reason=reason, operation_key=operation_key, chunk_size=chunk_size)


def _failed(batch, actor, operation_key, drift: list[dict], totals: dict, why: str) -> dict:
    transition(batch, "ROLLBACK_FAILED", actor=actor, reason=why)
    return _remember(batch, operation_key, "rollback", {"status": "ROLLBACK_FAILED", "drift": drift[:50], **totals})


def _rollback_locked(*, actor, batch: ImportBatch, reason: str, operation_key: str, chunk_size: int) -> dict:
    if batch.status != "ROLLING_BACK" and not needs_rollback(batch.status):
        raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} cannot be rolled back")
    chunk_size = max(1, min(int(chunk_size), 500))

    if batch.status != "ROLLING_BACK":
        transition(batch, "ROLLING_BACK", actor=actor, reason=reason.strip())

    totals = {"deleted": 0, "restored": 0, "recreated": 0}
    drift = find_drift(batch)
    if drift:
        return _failed(batch, actor, operation_key, drift, totals, f"{len(drift)} row(s) changed after the import")

    creates = _journalled_creates(batch)
    while True:
        chunk = list(_pending(batch)[:chunk_size])
        if not chunk:
            break
        chunk_totals = {"deleted": 0, "restored": 0, "recreated": 0}
        try:
            with transaction.atomic():
                for entry in chunk:
                    _undo(entry, chunk_totals, creates)
        except RollbackBlocked as blocked:
            # The chunk's transaction rolled back: none of it was applied.
            return _failed(
                batch, actor, operation_key, [blocked.item], totals,
                f"rollback stopped at {blocked.item['entity']} {blocked.item['id']}: {blocked.item['why']}"[:200],
            )
        for key, value in chunk_totals.items():
            totals[key] += value

    with transaction.atomic():
        transition(batch, "ROLLED_BACK", actor=actor, reason=reason.strip())
        return _remember(batch, operation_key, "rollback", {"status": "ROLLED_BACK", "drift": [], **totals})


__all__ = ["rollback_batch", "retry_rollback", "find_drift", "DEFAULT_ROLLBACK_CHUNK_SIZE", "ImportOperation"]
