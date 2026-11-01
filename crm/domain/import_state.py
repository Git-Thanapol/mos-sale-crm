"""Import batch state machine (port of src/import/state-machine.ts).

A large import cannot be one transaction, so "all or nothing" is modelled
explicitly: states, guarded transitions, a before-image journal and
compensation. The distinctions that matter operationally are preserved:
CANCELLED (nothing written), FAILED (nothing written), ROLLED_BACK (written and
fully undone) and ROLLBACK_FAILED (needs a human).

Batches are plain dicts here with snake_case keys; the persistence layer owns
the model. Times are epoch milliseconds, as in the prototype.
"""

from __future__ import annotations

from crm.domain.import_errors import ImportTransitionError

IMPORT_BATCH_STATUSES: tuple[str, ...] = (
    "PREVIEW",
    "AWAITING_RESOLUTION",
    "COMMITTING",
    "COMMITTED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "CANCELLED",
    "FAILED",
    "ROLLBACK_FAILED",
    "FAILED_RETRYABLE",
    "PAUSED_QUOTA",
)

IMPORT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "PREVIEW": ("AWAITING_RESOLUTION", "CANCELLED", "FAILED"),
    "AWAITING_RESOLUTION": ("AWAITING_RESOLUTION", "COMMITTING", "CANCELLED", "FAILED"),
    "COMMITTING": ("COMMITTED", "FAILED_RETRYABLE", "PAUSED_QUOTA", "ROLLING_BACK"),
    "COMMITTED": ("ROLLING_BACK",),
    # A failed commit always compensates first. Once compensation is complete the
    # batch may be retryable (quota/network) rather than merely rolled back.
    "ROLLING_BACK": ("ROLLED_BACK", "ROLLBACK_FAILED", "PAUSED_QUOTA", "FAILED_RETRYABLE"),
    "ROLLED_BACK": (),
    "CANCELLED": (),
    "FAILED": (),
    "ROLLBACK_FAILED": (),
    "FAILED_RETRYABLE": ("COMMITTING", "ROLLING_BACK", "CANCELLED"),
    "PAUSED_QUOTA": ("COMMITTING", "ROLLING_BACK", "CANCELLED"),
}

TERMINAL_STATUSES: tuple[str, ...] = (
    "COMMITTED",
    "ROLLED_BACK",
    "CANCELLED",
    "FAILED",
    "ROLLBACK_FAILED",
)

_CANCELLABLE_FROM = ("PREVIEW", "AWAITING_RESOLUTION", "FAILED_RETRYABLE", "PAUSED_QUOTA")


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in IMPORT_TRANSITIONS[from_status]


def assert_transition(from_status: str, to_status: str, context: dict) -> None:
    """Guards are why a transition table alone is not enough: entering COMMITTING
    without a confirmation or with open blocking conflicts must fail loudly
    rather than start a partial write.

    `context` keys: blocking_conflicts_open, confirmed_by (others are recorded
    by apply_transition).
    """
    if not can_transition(from_status, to_status):
        raise ImportTransitionError(
            from_status, to_status, "ILLEGAL_TRANSITION", "not allowed by the state machine"
        )
    if to_status == "COMMITTING":
        open_count = context.get("blocking_conflicts_open") or 0
        if open_count > 0:
            raise ImportTransitionError(
                from_status,
                to_status,
                "BLOCKING_CONFLICTS_OPEN",
                f"{open_count} blocking conflict(s) must be resolved first",
            )
        if not context.get("confirmed_by"):
            raise ImportTransitionError(
                from_status, to_status, "CONFIRMATION_REQUIRED", "an explicit confirmation is required"
            )
    if to_status == "CANCELLED" and from_status not in _CANCELLABLE_FROM:
        raise ImportTransitionError(
            from_status, to_status, "TOO_LATE_TO_CANCEL", "commit has already touched business data"
        )


def apply_transition(batch: dict, to_status: str, context: dict) -> dict:
    """Apply a transition and append an immutable history entry.

    A self-transition (AWAITING_RESOLUTION -> AWAITING_RESOLUTION) records a
    resolution event without pretending the batch moved. Returns
    `{"batch": new_batch, "changed": bool}`; the input is not mutated.
    """
    assert_transition(
        batch["status"],
        to_status,
        {**context, "confirmed_by": context.get("confirmed_by") or batch.get("confirmed_by")},
    )
    same_state = batch["status"] == to_status
    now = context["at"]
    following = {
        **batch,
        "status": to_status,
        "transition_history": [
            *batch["transition_history"],
            {
                "from": batch["status"],
                "to": to_status,
                "at": now,
                "actor": context["actor"],
                "reason": context.get("reason") or "",
            },
        ],
    }
    if to_status == "COMMITTING":
        following["attempt_count"] = batch["attempt_count"] + 1
        following["confirmed_by"] = context.get("confirmed_by") or batch.get("confirmed_by") or context["actor"]
        following["confirmed_at"] = batch.get("confirmed_at") if batch.get("confirmed_at") is not None else now
    if to_status == "COMMITTED":
        following["committed_at"] = now
    if to_status == "ROLLED_BACK":
        following["rolled_back_at"] = now
    return {"batch": following, "changed": not same_state}


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def can_retry_commit(status: str) -> bool:
    """Retry is allowed after a quota/network failure without re-uploading."""
    return status in ("FAILED_RETRYABLE", "PAUSED_QUOTA")


def needs_rollback(status: str) -> bool:
    """A batch may only be rolled back while it may still own business mutations.

    PREVIEW/AWAITING_RESOLUTION have none; ROLLED_BACK/CANCELLED/FAILED are clean.
    """
    return status in ("COMMITTING", "COMMITTED", "FAILED_RETRYABLE", "PAUSED_QUOTA")
