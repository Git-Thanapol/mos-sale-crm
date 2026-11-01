"""The ported vocabularies must match the prototype verbatim, in source order.

Order is part of the contract: these tuples drive UI option lists, so sorting
or "tidying" them changes what a user sees. The values are stable machine
codes — Thai labels live in the presentation layer, so renaming a label never
rewrites stored data (02_LOCKED_BUSINESS_RULES.md).
"""

import pytest

from crm.domain import enums as ENUMS

pytestmark = pytest.mark.unit


def test_roles_are_the_three_prototype_roles():
    assert ENUMS.ROLES == ("ADMIN", "TEAM_LEAD", "TELESALE")


def test_ownership_policies_are_exactly_the_three_locked_policies():
    assert ENUMS.OWNERSHIP_POLICIES == ("EXCLUSIVE", "LATEST_ORDER", "LATEST_ORDER_WITH_LOCK")


def test_stopped_is_a_lifecycle_status_not_a_routing_state():
    assert "STOPPED" in ENUMS.LEAD_LIFECYCLE_STATUSES
    assert "STOPPED" not in ENUMS.ROUTING_STATES
    assert ENUMS.ROUTING_STATES == ("ASSIGNED", "TEAM_UNASSIGNED")


def test_lifecycle_statuses_are_in_prototype_order():
    assert ENUMS.LEAD_LIFECYCLE_STATUSES == (
        "NEW",
        "FOLLOWING",
        "WAITING_CUSTOMER",
        "WAITING_FOLLOWUP",
        "STOPPED",
    )


def test_computed_statuses_are_separate_from_lifecycle_statuses():
    assert ENUMS.COMPUTED_LEAD_STATUSES == (
        "APPOINTMENT_TODAY",
        "APPOINTMENT_FUTURE",
        "OVERDUE",
    )
    assert not set(ENUMS.COMPUTED_LEAD_STATUSES) & set(ENUMS.LEAD_LIFECYCLE_STATUSES)


def test_stop_reasons_are_the_three_locked_reasons():
    assert ENUMS.STOP_REASONS == ("CONTACT_LIMIT_REACHED", "MANUAL_STOP", "OTHER")


def test_intake_statuses_describe_a_pre_lead_queue():
    assert ENUMS.INTAKE_STATUSES == ("GLOBAL_UNASSIGNED", "CONVERTED", "ARCHIVED")


def test_order_payment_and_shipping_statuses_stay_separate_vocabularies():
    assert ENUMS.ORDER_STATUSES == (
        "PENDING_CONFIRMATION",
        "WAITING_SHIPMENT",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
        "COD_RETURNED",
    )
    assert ENUMS.PAYMENT_STATUSES == (
        "UNPAID",
        "PENDING_VERIFICATION",
        "PAID",
        "COD_PENDING",
        "COD_COLLECTED",
        "REFUNDED",
    )
    assert ENUMS.SHIPPING_STATUSES == (
        "NOT_SHIPPED",
        "LABEL_CREATED",
        "PICKED_UP",
        "IN_TRANSIT",
        "DELIVERED",
        "FAILED",
        "RETURNING",
        "RETURNED",
    )


def test_cancelled_and_cod_returned_are_the_only_invalid_sales_statuses():
    invalid = [s for s in ENUMS.ORDER_STATUSES if not ENUMS.is_order_valid_for_sales(s)]
    assert invalid == ["CANCELLED", "COD_RETURNED"]


def test_terminal_negative_is_exactly_cancelled_and_cod_returned():
    negative = [s for s in ENUMS.ORDER_STATUSES if ENUMS.is_order_terminal_negative(s)]
    assert negative == ["CANCELLED", "COD_RETURNED"]


def test_contact_attempt_results_include_no_answer_and_ordered():
    assert ENUMS.CONTACT_ATTEMPT_RESULTS == (
        "NO_ANSWER",
        "BUSY",
        "CALLBACK_REQUESTED",
        "WRONG_NUMBER",
        "REFUSED",
        "ORDERED",
        "OTHER",
    )


def test_appointment_statuses_are_the_prototype_set():
    assert ENUMS.APPOINTMENT_STATUSES == ("SCHEDULED", "DONE", "MISSED", "CANCELLED")


def test_cycle_end_reasons_are_the_prototype_set():
    assert ENUMS.CYCLE_END_REASONS == (
        "NEW_ORDER",
        "CUSTOMER_ORDERED",
        "MANUAL_CLOSE",
        "STOPPED",
        "TEAM_CHANGED",
    )


def test_identity_match_kinds_never_auto_merge_an_ambiguous_customer():
    assert ENUMS.IDENTITY_MATCH_KINDS == ("AUTO_MATCH", "HUMAN_REVIEW", "NEW_CUSTOMER")


def test_identifier_types_are_the_prototype_set():
    assert ENUMS.IDENTIFIER_TYPES == ("PHONE", "CHANNEL_ID", "LEGACY_ID")


def test_audit_actions_cover_every_action_the_spec_requires_recorded():
    # 08_SECURITY_AND_AUTH_REQUIREMENTS.md "Audit"
    for action in (
        "OWNERSHIP_CHANGE",
        "STATUS_CHANGE",
        "IDENTITY_MERGE",
        "EXPORT",
        "CUSTOMER_HISTORY_VIEW",
        "INTAKE_CONVERTED",
        "NOTE_ADDED",
        "CONTACT_ATTEMPT_ADDED",
        "APPOINTMENT_SET",
    ):
        assert action in ENUMS.AUDIT_ACTIONS


def test_every_vocabulary_is_an_immutable_tuple_of_str():
    # Public vocabularies only. A leading underscore marks an internal
    # membership set (e.g. _TERMINAL_NEGATIVE_ORDER_STATUSES), where a
    # frozenset is the right type and ordering carries no contract.
    names = [n for n in dir(ENUMS) if n.isupper() and not n.startswith("_")]
    assert names, "no vocabularies found"
    for name in names:
        value = getattr(ENUMS, name)
        assert isinstance(value, tuple), name
        assert all(isinstance(v, str) for v in value), name


def test_unknown_status_is_not_silently_treated_as_valid():
    with pytest.raises(ValueError):
        ENUMS.is_order_valid_for_sales("NOT_A_REAL_STATUS")
