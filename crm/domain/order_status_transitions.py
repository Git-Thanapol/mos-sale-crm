"""Order status model (port of src/domain/order-status.ts).

Three independent axes are stored (order, payment, shipping) because a single
field cannot express "delivered but COD not yet collected". Normal progression
is monotonic; backwards movement requires an explicit, supported transition
rather than a silent import overwrite.

Verdicts: SAME, FORWARD, REVERSAL, CANCELLATION, INVALID, TERMINAL.
"""

from __future__ import annotations

_ORDER_RANK = {
    "PENDING_CONFIRMATION": 0,
    "WAITING_SHIPMENT": 1,
    "SHIPPED": 2,
    "DELIVERED": 3,
    "CANCELLED": -1,
    "COD_RETURNED": -1,
}
_NEGATIVE_TERMINAL = ("CANCELLED", "COD_RETURNED")


def classify_order_status_transition(current: str, incoming: str, allow_reversal: bool = False) -> str:
    if current == incoming:
        return "SAME"
    current_negative = current in _NEGATIVE_TERMINAL
    incoming_negative = incoming in _NEGATIVE_TERMINAL
    if incoming_negative:
        if current_negative:
            return "INVALID"
        # A cancellation/return is an explicit terminal event carried by the
        # source status, applied deliberately and audited, never a data error.
        return "CANCELLATION"
    if current_negative:
        return "TERMINAL"
    if _ORDER_RANK[incoming] > _ORDER_RANK[current]:
        return "FORWARD"
    return "REVERSAL"


# Source files routinely skip intermediate states, so forward jumps are allowed
# while backwards movement is not.
_SHIPPING_FORWARD = {
    "NOT_SHIPPED": ("LABEL_CREATED", "PICKED_UP", "IN_TRANSIT", "DELIVERED", "FAILED"),
    "LABEL_CREATED": ("PICKED_UP", "IN_TRANSIT", "DELIVERED", "FAILED"),
    "PICKED_UP": ("IN_TRANSIT", "DELIVERED", "FAILED"),
    "IN_TRANSIT": ("DELIVERED", "FAILED", "RETURNING"),
    "DELIVERED": ("RETURNING",),
    # a failed delivery may be reshipped, which restarts the forward chain
    "FAILED": ("LABEL_CREATED", "PICKED_UP", "IN_TRANSIT", "RETURNING"),
    "RETURNING": ("RETURNED",),
    "RETURNED": (),
}


def classify_shipping_transition(current: str, incoming: str, allow_reversal: bool = False) -> str:
    if current == incoming:
        return "SAME"
    if incoming in _SHIPPING_FORWARD[current]:
        # RETURNING/RETURNED are exceptional outcomes, not normal progress.
        return "CANCELLATION" if incoming in ("RETURNING", "RETURNED") else "FORWARD"
    if len(_SHIPPING_FORWARD[current]) == 0:
        return "TERMINAL"
    return "REVERSAL" if allow_reversal else "INVALID"


_PAYMENT_RANK = {
    "UNPAID": 0,
    "PENDING_VERIFICATION": 1,
    "COD_PENDING": 1,
    "PAID": 2,
    "COD_COLLECTED": 2,
    "REFUNDED": -1,
}


def classify_payment_transition(current: str, incoming: str, allow_reversal: bool = False) -> str:
    if current == incoming:
        return "SAME"
    if incoming == "REFUNDED":
        # Refunds are explicit terminal events from the source.
        return "SAME" if current == "REFUNDED" else "CANCELLATION"
    if current == "REFUNDED":
        return "TERMINAL"
    return "FORWARD" if _PAYMENT_RANK[incoming] > _PAYMENT_RANK[current] else "REVERSAL"


def status_change_allowed(verdict: str, allow_reversal: bool = False) -> bool:
    """The single place that decides whether an import may move a status. A
    reversal that was not explicitly approved is surfaced, never applied."""
    if verdict in ("SAME", "FORWARD", "CANCELLATION"):
        return True
    if verdict == "REVERSAL":
        return allow_reversal
    return False
