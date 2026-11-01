"""Ownership policies, proven against the prototype's own behaviour.

The parametrized test replays every case in tests/fixtures/crmv2/ownership.json,
whose `expected` values were produced by executing $PROTO's resolveOwnership.
The named tests below pin the rules 02_LOCKED_BUSINESS_RULES.md states in
prose, so a future reader sees the rule and not only a fixture id.
"""

from datetime import datetime, timezone

import pytest

from crm.domain.ownership import (
    OwnershipOrderFacts,
    derive_routing,
    evaluate_stopped_reactivation,
    resolve_ownership,
)
from tests.unit.test_parity_fixtures import load_cases

pytestmark = pytest.mark.unit

CARETAKER = "user-ts-crm-1"
OTHER = "user-ts-upsell-1"
ACTIVE = frozenset({CARETAKER, OTHER, "user-lead-crm"})
T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


def _dt(millis: int | None) -> datetime | None:
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _millis(moment: datetime | None) -> int | None:
    if moment is None:
        return None
    return round(moment.timestamp() * 1000)


def build_input(raw: dict) -> dict:
    """Map a prototype fixture's camelCase/millis input onto the Python call."""
    order = raw["order"]
    return {
        "policy": raw["policy"],
        "team_code": raw["teamId"],
        "current_caretaker_user_id": raw["currentCaretakerUserId"],
        "ownership_started_at": _dt(raw["ownershipStartedAt"]),
        "lifecycle_status": raw["lifecycleStatus"],
        "lock_active": raw["lockActive"],
        "order": OwnershipOrderFacts(
            order_id=order["orderId"],
            ordered_at=_dt(order["orderedAt"]),
            closer_user_id=order["closerUserId"],
            closer_team_code=order["closerTeamId"],
        ),
        "active_user_ids": frozenset(raw["activeUserIds"]),
    }


def facts(closer_user_id=CARETAKER, order_id="700010", ordered_at=None, closer_team_code="team-crm"):
    return OwnershipOrderFacts(
        order_id=order_id,
        ordered_at=ordered_at or T0,
        closer_user_id=closer_user_id,
        closer_team_code=None if closer_user_id is None else closer_team_code,
    )


@pytest.mark.parametrize("case", load_cases("ownership"), ids=lambda c: c["name"])
def test_ownership_matches_prototype(case):
    decision = resolve_ownership(**build_input(case["input"]))
    expected = case["expected"]
    assert decision.caretaker_user_id == expected["caretakerUserId"]
    assert decision.changed is expected["changed"]
    assert decision.reason == expected["reason"]
    assert _millis(decision.new_segment_started_at) == expected["newSegmentStartedAt"]
    if expected["conflict"] is None:
        assert decision.conflict is None
    else:
        assert decision.conflict is not None
        assert decision.conflict.kind == expected["conflict"]["kind"]
        assert decision.conflict.blocking is expected["conflict"]["blocking"]
        assert list(decision.conflict.options) == expected["conflict"]["options"]


def test_exclusive_lead_is_never_stolen_by_another_closer():
    decision = resolve_ownership(
        policy="EXCLUSIVE",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=False,
        order=facts(closer_user_id=OTHER),
        active_user_ids=ACTIVE,
    )
    assert decision.caretaker_user_id == CARETAKER
    assert decision.changed is False
    assert decision.reason == "EXCLUSIVE_HELD"
    # Non-blocking on purpose: the order is legitimate and must still be
    # recorded; only ownership is held.
    assert decision.conflict.blocking is False
    assert decision.conflict.options == ("KEEP_CURRENT", "REASSIGN")


def test_exclusive_assigns_the_first_closer_when_there_is_no_caretaker():
    decision = resolve_ownership(
        policy="EXCLUSIVE",
        team_code="CRM_TEAM",
        current_caretaker_user_id=None,
        ownership_started_at=None,
        lifecycle_status="NEW",
        lock_active=False,
        order=facts(closer_user_id=CARETAKER),
        active_user_ids=ACTIVE,
    )
    assert decision.changed is True
    assert decision.caretaker_user_id == CARETAKER
    assert decision.reason == "INITIAL_ASSIGNMENT"
    assert decision.new_segment_started_at == T0


def test_a_resigned_closer_has_no_active_identity():
    decision = resolve_ownership(
        policy="EXCLUSIVE",
        team_code="CRM_TEAM",
        current_caretaker_user_id=None,
        ownership_started_at=None,
        lifecycle_status="NEW",
        lock_active=False,
        order=facts(closer_user_id="user-ts-resigned"),
        active_user_ids=ACTIVE,
    )
    assert decision.changed is False
    assert decision.caretaker_user_id is None
    assert decision.reason == "NO_ACTIVE_CLOSER"


def test_latest_order_follows_the_latest_valid_closer():
    decision = resolve_ownership(
        policy="LATEST_ORDER",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=False,
        order=facts(closer_user_id=OTHER),
        active_user_ids=ACTIVE,
    )
    assert decision.changed is True
    assert decision.caretaker_user_id == OTHER
    assert decision.reason == "LATEST_ORDER"


def test_marketplace_order_with_no_closer_never_clears_a_caretaker():
    decision = resolve_ownership(
        policy="LATEST_ORDER",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=False,
        order=facts(closer_user_id=None),
        active_user_ids=ACTIVE,
    )
    assert decision.caretaker_user_id == CARETAKER
    assert decision.changed is False
    assert decision.reason == "NO_ACTIVE_CLOSER"


def test_active_lock_holds_ownership_under_latest_order_with_lock():
    decision = resolve_ownership(
        policy="LATEST_ORDER_WITH_LOCK",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=True,
        order=facts(closer_user_id=OTHER),
        active_user_ids=ACTIVE,
    )
    assert decision.reason == "LOCKED"
    assert decision.changed is False
    assert decision.conflict is None


def test_released_lock_behaves_like_latest_order():
    decision = resolve_ownership(
        policy="LATEST_ORDER_WITH_LOCK",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=False,
        order=facts(closer_user_id=OTHER),
        active_user_ids=ACTIVE,
    )
    assert decision.changed is True
    assert decision.caretaker_user_id == OTHER


def test_an_order_closed_by_the_current_caretaker_changes_nothing():
    decision = resolve_ownership(
        policy="LATEST_ORDER",
        team_code="CRM_TEAM",
        current_caretaker_user_id=CARETAKER,
        ownership_started_at=T0,
        lifecycle_status="FOLLOWING",
        lock_active=False,
        order=facts(closer_user_id=CARETAKER),
        active_user_ids=ACTIVE,
    )
    assert decision.changed is False
    assert decision.reason == "UNCHANGED"


def test_unknown_policy_raises_rather_than_falling_back():
    with pytest.raises(ValueError, match="UNSUPPORTED_OWNERSHIP_POLICY"):
        resolve_ownership(
            policy="SOMETHING_NEW",
            team_code="CRM_TEAM",
            current_caretaker_user_id=None,
            ownership_started_at=None,
            lifecycle_status="NEW",
            lock_active=False,
            order=facts(),
            active_user_ids=ACTIVE,
        )


def test_stopped_lead_receiving_an_order_always_requires_review():
    decision = evaluate_stopped_reactivation(
        lifecycle_status="STOPPED",
        prior_caretaker_user_id=CARETAKER,
        previous_stopped_at=T0,
        order=facts(closer_user_id=CARETAKER),
        active_user_ids=ACTIVE,
    )
    assert decision.requires_review is True
    assert decision.proposed_action == "REACTIVATE"
    assert decision.same_caretaker_as_before is True


def test_stopped_lead_with_no_usable_closer_stays_stopped_for_review():
    decision = evaluate_stopped_reactivation(
        lifecycle_status="STOPPED",
        prior_caretaker_user_id=None,
        previous_stopped_at=T0,
        order=facts(closer_user_id=None),
        active_user_ids=ACTIVE,
    )
    assert decision.requires_review is True
    assert decision.proposed_action == "KEEP_STOPPED"
    assert decision.same_caretaker_as_before is False


def test_a_lead_that_is_not_stopped_needs_no_reactivation_review():
    decision = evaluate_stopped_reactivation(
        lifecycle_status="FOLLOWING",
        prior_caretaker_user_id=CARETAKER,
        previous_stopped_at=None,
        order=facts(),
        active_user_ids=ACTIVE,
    )
    assert decision.requires_review is False
    assert decision.proposed_action is None


def test_routing_without_a_team_is_a_programming_error():
    with pytest.raises(ValueError, match="TEAM_REQUIRED_FOR_LEAD_ROUTING"):
        derive_routing(
            team_code=None,
            caretaker_user_id=None,
            caretaker_active=False,
            team_lead_configured=True,
        )


def test_an_active_caretaker_routes_to_the_caretaker_queue():
    decision = derive_routing("CRM_TEAM", CARETAKER, True, team_lead_configured=True)
    assert decision.routing_state == "ASSIGNED"
    assert decision.queue == "CARETAKER"
    assert decision.escalated_to_admin is False


def test_a_known_team_without_an_active_caretaker_is_team_unassigned():
    decision = derive_routing("CRM_TEAM", None, False, team_lead_configured=True)
    assert decision.routing_state == "TEAM_UNASSIGNED"
    assert decision.queue == "TEAM_LEAD"
    assert decision.escalated_to_admin is False


def test_team_without_a_lead_escalates_to_admin():
    decision = derive_routing("CRM_TEAM", None, False, team_lead_configured=False)
    assert decision.routing_state == "TEAM_UNASSIGNED"
    assert decision.queue == "ADMIN"
    assert decision.escalated_to_admin is True


def test_a_deactivated_caretaker_is_not_treated_as_ownership():
    # Review Focus #2, at the pure-function layer.
    decision = derive_routing("CRM_TEAM", "user-ts-resigned", False, team_lead_configured=True)
    assert decision.routing_state == "TEAM_UNASSIGNED"
