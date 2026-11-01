"""Missed-channel counting, proven against the prototype.

The counter is recomputed from Order history plus the ownership baseline, never
incremented (11_DO_NOT_CHANGE_CHECKLIST: "missedChannel is recomputed from
authority/history, not blind increment"). That is what makes repeated imports,
stale files, rollbacks and out-of-order arrivals settle on the same number.

Review Focus #3 lives here: two orders with the identical timestamp must
produce one deterministic result regardless of input order.
"""

from datetime import datetime, timedelta, timezone

import pytest

from crm.domain.missed_channel import (
    MissedChannelOrder,
    missed_channel_group,
    recompute_missed_channel,
)
from tests.unit.test_parity_fixtures import load_cases

pytestmark = pytest.mark.unit

CARETAKER = "user-ts-crm-1"
OTHER = "user-ts-upsell-1"
T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=1)
T2 = T0 + timedelta(seconds=2)


def _dt(millis: int) -> datetime:
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def build_input(raw: dict) -> dict:
    return {
        "baseline_started_at": _dt(raw["baselineStartedAt"]),
        "caretaker_user_id": raw["caretakerUserId"],
        "orders": [
            MissedChannelOrder(
                order_id=o["orderId"],
                ordered_at=_dt(o["orderedAt"]),
                closer_user_id=o["closerUserId"],
                order_status=o["orderStatus"],
            )
            for o in raw["orders"]
        ],
        "policy": raw.get("policy"),
    }


@pytest.mark.parametrize("case", load_cases("missed_channel"), ids=lambda c: c["name"])
def test_missed_channel_matches_prototype(case):
    result = recompute_missed_channel(**build_input(case["input"]))
    assert result.count == case["expected"]["count"]
    assert [e.reason for e in result.explanations] == [
        e["reason"] for e in case["expected"]["explanations"]
    ]
    assert [e.order_id for e in result.explanations] == [
        e["orderId"] for e in case["expected"]["explanations"]
    ]
    assert [e.counted for e in result.explanations] == [
        e["counted"] for e in case["expected"]["explanations"]
    ]


def test_tied_timestamps_break_on_order_id_deterministically():
    # Review Focus #3
    a = MissedChannelOrder("B", T1, OTHER, "DELIVERED")
    b = MissedChannelOrder("A", T1, CARETAKER, "DELIVERED")
    forward = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[a, b]
    )
    reverse = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[b, a]
    )
    assert forward.count == reverse.count
    assert [e.order_id for e in forward.explanations] == ["A", "B"]
    assert [e.order_id for e in reverse.explanations] == ["A", "B"]


def test_reimporting_the_same_order_never_inflates_the_count():
    order = MissedChannelOrder("O1", T1, None, "DELIVERED")
    assert (
        recompute_missed_channel(
            baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[order, order]
        ).count
        == 1
    )


def test_cancelled_and_cod_returned_never_count():
    orders = [
        MissedChannelOrder("O1", T1, None, "CANCELLED"),
        MissedChannelOrder("O2", T2, None, "COD_RETURNED"),
    ]
    result = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=orders
    )
    assert result.count == 0
    assert [e.reason for e in result.explanations] == [
        "EXCLUDED_CANCELLED",
        "EXCLUDED_COD_RETURNED",
    ]


def test_caretaker_closing_a_valid_order_resets_the_count_to_zero():
    orders = [
        MissedChannelOrder("O1", T1, None, "DELIVERED"),
        MissedChannelOrder("O2", T2, CARETAKER, "DELIVERED"),
    ]
    assert (
        recompute_missed_channel(
            baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=orders
        ).count
        == 0
    )


def test_orders_before_the_baseline_are_not_counted():
    order = MissedChannelOrder("O1", T0 - timedelta(days=1), None, "DELIVERED")
    result = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[order]
    )
    assert result.count == 0
    assert result.explanations[0].reason == "BEFORE_BASELINE"


def test_a_marketplace_order_counts_when_the_policy_says_so():
    order = MissedChannelOrder("O1", T1, None, "DELIVERED")
    result = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[order]
    )
    assert result.count == 1
    assert result.explanations[0].reason == "COUNTED_NO_CLOSER"


def test_the_policy_can_disable_counting_entirely():
    order = MissedChannelOrder("O1", T1, None, "DELIVERED")
    result = recompute_missed_channel(
        baseline_started_at=T0,
        caretaker_user_id=CARETAKER,
        orders=[order],
        policy={"enabled": False},
    )
    assert result.count == 0
    assert result.explanations == ()


def test_an_order_with_no_caretaker_on_the_lead_still_counts():
    order = MissedChannelOrder("O1", T1, CARETAKER, "DELIVERED")
    result = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id=None, orders=[order]
    )
    assert result.count == 1
    assert result.explanations[0].reason == "COUNTED_OTHER_CLOSER"


def test_group_labels_do_not_collapse_the_stored_integer():
    assert [missed_channel_group(n) for n in (0, 1, 2, 7)] == ["0", "1", "2", "3+"]


def test_an_unknown_order_status_raises_rather_than_counting_silently():
    # The strict guard from crm/domain/enums.py. The persistence adapter maps
    # this repo's free-text statuses before calling in; a raw value reaching
    # the pure function is a bug, not something to default.
    order = MissedChannelOrder("O1", T1, None, "สำเร็จ")
    with pytest.raises(ValueError, match="UNKNOWN_ORDER_STATUS"):
        recompute_missed_channel(
            baseline_started_at=T0, caretaker_user_id=CARETAKER, orders=[order]
        )
