"""ออเดอร์ตกหล่นช่องทางอื่น (missed-channel) counter.

Ported from $PROTO/src/domain/missed-channel.ts.

Authority is Order history plus the ownership baseline, never the cached value
on the Lead. This is a pure function of the full relevant history, which is
what makes repeated imports, stale files, rollbacks and out-of-order imports
all settle on the same result (11_DO_NOT_CHANGE_CHECKLIST: recomputed from
authority, not blind increment).

Semantics, default team policy:
- only valid orders count; CANCELLED and COD_RETURNED never do
- +1 when the closer is not the current caretaker, including a marketplace
  order with no closer at all, and an order closed while the lead had none
- an order closed by the current caretaker resets the counter to 0
- a caretaker change starts a new baseline, so earlier orders stop counting

Three stages, in order, and the order matters: deduplicate by order id
(last occurrence wins), sort by (ordered_at, order_id), then fold. The sort
tiebreak on order_id is what makes two orders sharing a timestamp produce one
deterministic answer regardless of the order rows arrived in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from crm.domain.enums import is_order_valid_for_sales

DEFAULT_POLICY: dict[str, bool] = {
    "enabled": True,
    "countNullCloser": True,
    "resetOnCaretakerClose": True,
}


@dataclass(frozen=True)
class MissedChannelOrder:
    order_id: str
    ordered_at: datetime
    closer_user_id: str | None
    order_status: str


@dataclass(frozen=True)
class MissedChannelExplanation:
    order_id: str
    ordered_at: datetime
    closer_user_id: str | None
    counted: bool
    reason: str


@dataclass(frozen=True)
class MissedChannelResult:
    count: int
    #: Why each order did or did not count. Part of the contract, not debug
    #: output: it is what makes a surprising counter auditable instead of
    #: undiagnosable.
    explanations: tuple[MissedChannelExplanation, ...]


def recompute_missed_channel(
    *,
    baseline_started_at: datetime,
    caretaker_user_id: str | None,
    orders: Sequence[MissedChannelOrder],
    policy: Mapping[str, bool] | None = None,
) -> MissedChannelResult:
    resolved = {**DEFAULT_POLICY, **(policy or {})}
    if not resolved["enabled"]:
        return MissedChannelResult(count=0, explanations=())

    # Deduplicate by order id first: re-importing the same order must never
    # inflate the counter. Last occurrence wins, matching "latest known state".
    by_id: dict[str, MissedChannelOrder] = {}
    for order in orders:
        by_id[order.order_id] = order

    # Stable ordering makes the result independent of the order in which files
    # or rows were imported.
    ordered = sorted(by_id.values(), key=lambda o: (o.ordered_at, o.order_id))

    count = 0
    explanations: list[MissedChannelExplanation] = []
    for order in ordered:
        if order.ordered_at < baseline_started_at:
            explanations.append(
                MissedChannelExplanation(
                    order.order_id, order.ordered_at, order.closer_user_id, False, "BEFORE_BASELINE"
                )
            )
            continue

        if not is_order_valid_for_sales(order.order_status):
            explanations.append(
                MissedChannelExplanation(
                    order.order_id,
                    order.ordered_at,
                    order.closer_user_id,
                    False,
                    "EXCLUDED_CANCELLED"
                    if order.order_status == "CANCELLED"
                    else "EXCLUDED_COD_RETURNED",
                )
            )
            continue

        closed_by_caretaker = (
            caretaker_user_id is not None and order.closer_user_id == caretaker_user_id
        )

        if closed_by_caretaker and resolved["resetOnCaretakerClose"]:
            count = 0
            explanations.append(
                MissedChannelExplanation(
                    order.order_id,
                    order.ordered_at,
                    order.closer_user_id,
                    False,
                    "RESET_BY_CARETAKER",
                )
            )
            continue

        if order.closer_user_id is None and not resolved["countNullCloser"]:
            explanations.append(
                MissedChannelExplanation(
                    order.order_id, order.ordered_at, None, False, "COUNTED_NO_CLOSER"
                )
            )
            continue

        count += 1
        explanations.append(
            MissedChannelExplanation(
                order.order_id,
                order.ordered_at,
                order.closer_user_id,
                True,
                "COUNTED_NO_CLOSER" if order.closer_user_id is None else "COUNTED_OTHER_CLOSER",
            )
        )

    return MissedChannelResult(count=count, explanations=tuple(explanations))


def missed_channel_group(count: int) -> str:
    """UI grouping: 0 / 1 / 2 / 3+, without collapsing the stored integer."""
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"
