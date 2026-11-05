"""Batch status transitions, enforced by the ported state machine.

Every change of ImportBatch.status goes through `transition`, so the guards in
crm.domain.import_state (blocking conflicts, explicit confirmation, too late to
cancel) apply no matter which service or view asks.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.utils import timezone as dj_timezone

from crm.domain.import_state import apply_transition
from crm.importv2.models import ImportBatch


def _ms(moment: datetime | None) -> int | None:
    return None if moment is None else round(moment.timestamp() * 1000)


def _dt(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _label(user) -> str | None:
    return None if user is None else (user.email or str(user.pk))


def batch_record(batch: ImportBatch) -> dict:
    """The batch as the state machine's plain-dict record."""
    return {
        "status": batch.status,
        "transition_history": list(batch.transition_history or []),
        "attempt_count": batch.attempt_count,
        "confirmed_by": _label(batch.confirmed_by),
        "confirmed_at": _ms(batch.confirmed_at),
        "committed_at": _ms(batch.committed_at),
        "rolled_back_at": _ms(batch.rolled_back_at),
    }


def transition(
    batch: ImportBatch,
    to_status: str,
    *,
    actor,
    reason: str = "",
    blocking_conflicts_open: int | None = None,
    confirmed_by=None,
) -> bool:
    """Move the batch, raising ImportTransitionError when the machine forbids it.

    Returns True when the status actually changed (a self-transition only
    appends a history entry). Saves the batch.
    """
    batch.refresh_from_db(
        fields=["status", "transition_history", "attempt_count", "confirmed_by", "confirmed_at", "committed_at", "rolled_back_at"]
    )
    now = round(dj_timezone.now().timestamp() * 1000)
    context = {
        "actor": _label(actor) or "system",
        "at": now,
        "reason": reason,
        "blocking_conflicts_open": blocking_conflicts_open,
        "confirmed_by": _label(confirmed_by),
    }
    result = apply_transition(batch_record(batch), to_status, context)
    record = result["batch"]
    batch.status = record["status"]
    batch.transition_history = record["transition_history"]
    batch.attempt_count = record["attempt_count"]
    if record["confirmed_at"] is not None and batch.confirmed_at is None:
        batch.confirmed_at = _dt(record["confirmed_at"])
        batch.confirmed_by = confirmed_by or batch.confirmed_by or actor
    if record["committed_at"] is not None:
        batch.committed_at = _dt(record["committed_at"])
    if record["rolled_back_at"] is not None:
        batch.rolled_back_at = _dt(record["rolled_back_at"])
    batch.save()
    return result["changed"]
