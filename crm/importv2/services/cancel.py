"""Cancel a batch that has not written business data, or restage it.

CANCELLED means "nothing was written". So a batch may be cancelled only from a
state where that is true: PREVIEW, AWAITING_RESOLUTION, and FAILED_RETRYABLE /
PAUSED_QUOTA while its journal is empty. Anything that already journalled a
write must be rolled back instead (the refusal says so).

`restage_batch` is the way out of a stale-preview dead end: the machine has no
edge back to AWAITING_RESOLUTION, so the old batch is cancelled and a fresh one
is staged from the same bytes, ready for a new Preview.
"""

from __future__ import annotations

from django.db import transaction

from crm.importv2.models import ImportBatch
from crm.importv2.services.commit import _remember, _replay, _require_admin
from crm.importv2.services.state import transition
from crm.importv2.services.upload import create_batch

CANCELLABLE = ("PREVIEW", "AWAITING_RESOLUTION", "FAILED_RETRYABLE", "PAUSED_QUOTA")


def cancel_batch(*, actor, batch: ImportBatch, reason: str, operation_key: str) -> dict:
    _require_admin(actor)
    if len((reason or "").strip()) < 3:
        raise ValueError("INVALID_ARGUMENT: a reason of at least 3 characters is required")
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay
    with transaction.atomic():
        locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        batch.refresh_from_db()
        replay = _replay(batch, operation_key)
        if replay is not None:
            return replay
        if locked.status not in CANCELLABLE:
            raise ValueError(f"ILLEGAL_STATE: a batch in {locked.status} cannot be cancelled")
        if batch.journal.exists():
            raise ValueError(
                "ILLEGAL_STATE: this batch already wrote business data; use Rollback, not Cancel"
            )
        transition(batch, "CANCELLED", actor=actor, reason=reason.strip())
        return _remember(batch, operation_key, "cancel", {"status": "CANCELLED"})


def restage_batch(*, actor, batch: ImportBatch, reason: str, operation_key: str) -> dict:
    """Cancel `batch` and stage a new one from the same file."""
    result = cancel_batch(actor=actor, batch=batch, reason=reason, operation_key=operation_key)
    if "newBatchId" in result:
        return result
    with transaction.atomic():
        fresh = create_batch(actor=actor, file_name=batch.file_name, raw=bytes(batch.raw_bytes or b""))
        result = {**result, "newBatchId": str(fresh.batch_id)}
        op = batch.operations.get(operation_key=operation_key)
        op.result = result
        op.save(update_fields=["result"])
    return result
