"""Map this repo's Order status vocabulary onto the prototype's machine codes.

The two systems do not share a status vocabulary. crm_order.order_status is a
free-text CharField, and the live data holds exactly two values:

    สำเร็จ   162688 rows
    (blank)   71466 rows

The prototype's ORDER_STATUSES are machine codes
(PENDING_CONFIRMATION ... CANCELLED, COD_RETURNED). Notably this repo has NO
cancellation or COD-return vocabulary at all, so the two statuses the
missed-channel rule must exclude cannot currently be expressed in it. That is
a gap to close when the import engine lands (Plan 2), not something to invent
here.

07_MIGRATION_AND_DATA_REQUIREMENTS.md requires an unknown status to be
"explicitly mapped/versioned or archived/deferred" and explicitly forbids
guessing. So: a value in the table below maps; anything else returns None and
the caller excludes that order and says so. Unmapped is never silently
promoted to a valid sale, because a wrong guess there inflates the
missed-channel counter against a real salesperson.
"""

from __future__ import annotations

from datetime import datetime, timezone

#: D023 — the HX63 snapshot baseline. For migrated leads, missed-channel
#: counting begins here and earlier orders are never retroactively counted.
HX63_SNAPSHOT_BASELINE = datetime(2026, 10, 13, 12, 31, 55, tzinfo=timezone.utc)

#: Deliberately explicit and small. Add a row when the business confirms a
#: meaning; do not add a fuzzy match.
ORDER_STATUS_MAP: dict[str, str] = {
    "สำเร็จ": "DELIVERED",
    # A blank status means the order exists but its state was never recorded.
    # It is a real order and not a cancellation, so it stays valid for sales
    # and therefore countable.
    "": "PENDING_CONFIRMATION",
}


def to_machine_status(raw: str | None) -> str | None:
    """Return the prototype machine code, or None when the value is unmapped.

    None is the "defer" outcome: the caller excludes the order and records
    that it did, rather than defaulting it in either direction.
    """
    return ORDER_STATUS_MAP.get((raw or "").strip())
