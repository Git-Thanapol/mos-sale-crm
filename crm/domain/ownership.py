"""Team ownership policies, ported from $PROTO/src/domain/ownership.ts.

The policy is data on the Team (`policy.ownershipPolicy`), so adding a fourth
policy means adding a branch here — never a team-name branch in business
logic (02_LOCKED_BUSINESS_RULES.md).

Branch order is load-bearing: it decides which `reason` a case reports, and
the parity fixtures pin those reasons. Do not reorder to "simplify".
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from datetime import datetime

from crm.domain.enums import ORDER_STATUSES, is_order_terminal_negative


@dataclass(frozen=True)
class OwnershipOrderFacts:
    order_id: str
    ordered_at: datetime
    closer_user_id: str | None
    closer_team_code: str | None


@dataclass(frozen=True)
class OwnershipConflict:
    kind: str
    blocking: bool
    summary: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class OwnershipDecision:
    caretaker_user_id: str | None
    #: True only when this order actually changes ownership.
    changed: bool
    #: When the new ownership segment starts; None when unchanged.
    new_segment_started_at: datetime | None
    reason: str
    conflict: OwnershipConflict | None


@dataclass(frozen=True)
class RoutingDecision:
    routing_state: str
    #: Which operational queue owns the follow-up work.
    queue: str
    #: TEAM_UNASSIGNED surfaced to admins because the team has no lead.
    escalated_to_admin: bool


@dataclass(frozen=True)
class StoppedReactivationDecision:
    requires_review: bool
    proposed_action: str | None
    #: When the same caretaker closes again, a human must choose.
    same_caretaker_as_before: bool
    team_code: str | None


def _active_closer(order: OwnershipOrderFacts, active_user_ids: frozenset[str]) -> str | None:
    """A closer who is not an active user has no identity for ownership.

    08_SECURITY_AND_AUTH_REQUIREMENTS.md: a disabled or resigned user must not
    retain caretaker authority, so a resigned closer cannot take a lead.
    """
    if order.closer_user_id and order.closer_user_id in active_user_ids:
        return order.closer_user_id
    return None


def resolve_ownership(
    *,
    policy: str,
    team_code: str,
    current_caretaker_user_id: str | None,
    ownership_started_at: datetime | None,
    lifecycle_status: str,
    lock_active: bool,
    order: OwnershipOrderFacts,
    active_user_ids: frozenset[str],
) -> OwnershipDecision:
    """Resolve ownership after a single order is imported."""
    closer = _active_closer(order, active_user_ids)

    def keep(reason: str) -> OwnershipDecision:
        return OwnershipDecision(
            caretaker_user_id=current_caretaker_user_id,
            changed=False,
            new_segment_started_at=None,
            reason=reason,
            conflict=None,
        )

    if policy == "EXCLUSIVE":
        if current_caretaker_user_id is None:
            if closer:
                return OwnershipDecision(
                    caretaker_user_id=closer,
                    changed=True,
                    new_segment_started_at=order.ordered_at,
                    reason="INITIAL_ASSIGNMENT",
                    conflict=None,
                )
            return keep("NO_ACTIVE_CLOSER")
        # A closer from another owner never steals an exclusive lead; it becomes
        # a review item instead. Non-blocking: the order itself is still
        # legitimate and must be recorded.
        if closer and closer != current_caretaker_user_id:
            held = keep("EXCLUSIVE_HELD")
            return OwnershipDecision(
                caretaker_user_id=held.caretaker_user_id,
                changed=held.changed,
                new_segment_started_at=held.new_segment_started_at,
                reason=held.reason,
                conflict=OwnershipConflict(
                    kind="OWNERSHIP",
                    blocking=False,
                    summary=(
                        "Order closed by a different user while the lead holds an active "
                        "exclusive caretaker. Ownership was kept."
                    ),
                    options=("KEEP_CURRENT", "REASSIGN"),
                ),
            )
        return keep("UNCHANGED")

    if policy in ("LATEST_ORDER", "LATEST_ORDER_WITH_LOCK"):
        if policy == "LATEST_ORDER_WITH_LOCK" and lock_active:
            return keep("LOCKED")
        # A marketplace order with no human closer never clears a caretaker.
        if not closer:
            return keep("NO_ACTIVE_CLOSER")
        if closer == current_caretaker_user_id:
            return keep("UNCHANGED")
        return OwnershipDecision(
            caretaker_user_id=closer,
            changed=True,
            new_segment_started_at=order.ordered_at,
            reason="LATEST_ORDER",
            conflict=None,
        )

    # Exhaustive guard: an unknown policy must never silently fall back.
    raise ValueError(f"UNSUPPORTED_OWNERSHIP_POLICY:{policy}")


def derive_routing(
    team_code: str | None,
    caretaker_user_id: str | None,
    caretaker_active: bool,
    team_lead_configured: bool,
) -> RoutingDecision:
    """Routing is derived, never stored as a lifecycle status.

    STOPPED is a separate concept handled by the lifecycle status, not here.
    A missing team is a programming error rather than "unassigned": Global
    Unassigned work is pre-Lead intake (D019), so it never reaches routing.
    """
    if not team_code:
        raise ValueError("TEAM_REQUIRED_FOR_LEAD_ROUTING")
    if caretaker_user_id and caretaker_active:
        return RoutingDecision(
            routing_state="ASSIGNED", queue="CARETAKER", escalated_to_admin=False
        )
    if team_lead_configured:
        return RoutingDecision(
            routing_state="TEAM_UNASSIGNED", queue="TEAM_LEAD", escalated_to_admin=False
        )
    return RoutingDecision(
        routing_state="TEAM_UNASSIGNED", queue="ADMIN", escalated_to_admin=True
    )


def evaluate_stopped_reactivation(
    *,
    lifecycle_status: str,
    prior_caretaker_user_id: str | None,
    previous_stopped_at: datetime | None,
    order: OwnershipOrderFacts,
    active_user_ids: frozenset[str],
) -> StoppedReactivationDecision:
    """A STOPPED lead receiving a new valid order is never silently reactivated.

    With no usable owner it goes to the unassigned/reactivation review queue.
    """
    if lifecycle_status != "STOPPED":
        return StoppedReactivationDecision(
            requires_review=False,
            proposed_action=None,
            same_caretaker_as_before=False,
            team_code=None,
        )
    closer = _active_closer(order, active_user_ids)
    return StoppedReactivationDecision(
        requires_review=True,
        proposed_action="REACTIVATE" if closer else "KEEP_STOPPED",
        same_caretaker_as_before=closer is not None and closer == prior_caretaker_user_id,
        team_code=order.closer_team_code,
    )


def is_order_eligible_for_ownership(
    order_status: str | None,
    ordered_at: datetime | None,
    ownership_started_at: datetime | None,
) -> bool:
    """Should this order be shown to `resolve_ownership` at all?

    `resolve_ownership` is parity-pinned and evaluates exactly one order, so
    invalid and stale orders have to be dropped by the caller BEFORE it runs:

    - a CANCELLED / COD_RETURNED order is not a sale and must not move a
      lead (an unknown or unmapped status is treated the same, never guessed);
    - an order dated before `ownership_started_at` (the instant of the order
      that decided the current ownership segment) is stale; re-importing old
      history must not roll a lead back to an earlier closer.
    An order exactly at the segment start is kept (not earlier).
    """
    if ordered_at is None:
        return False
    if order_status is None or order_status not in ORDER_STATUSES:
        return False
    if is_order_terminal_negative(order_status):
        return False
    if ownership_started_at is not None and ordered_at < ownership_started_at:
        return False
    return True


def filter_ownership_orders(
    orders: Iterable[tuple[OwnershipOrderFacts, str | None]],
    ownership_started_at: datetime | None,
) -> list[OwnershipOrderFacts]:
    """Keep eligible `(facts, order_status)` pairs, oldest first.

    Call-site contract: feed each returned order to `resolve_ownership` in
    order, advancing `ownership_started_at` to `decision.new_segment_started_at`
    whenever `decision.changed`.
    """
    kept = [
        facts
        for facts, status in orders
        if is_order_eligible_for_ownership(status, facts.ordered_at, ownership_started_at)
    ]
    return sorted(kept, key=lambda f: (f.ordered_at, f.order_id))
