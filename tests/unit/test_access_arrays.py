"""Denormalised authorization arrays — Review Focus #2.

Ported from $PROTO/src/security/access-arrays.ts. These are read models only:
Lead/Team/caretaker plus active-user state are the authority, and every server
mutation recomputes them. A caller never supplies them as trusted input.

The load-bearing rule is that a deactivated user drops out. 08_SECURITY_AND_
AUTH_REQUIREMENTS.md: "Disabled/resigned user must not retain active access or
caretaker authority." A cached array that still names them is a privilege leak
that outlives the account.
"""

import pytest

from crm.domain.access import derive_customer_access, derive_lead_access

pytestmark = pytest.mark.unit


def test_inactive_caretaker_is_excluded_from_access_users():
    access = derive_lead_access("CRM_TEAM", "u1", frozenset())
    assert access == {"access_team_ids": ["CRM_TEAM"], "access_user_ids": []}


def test_active_caretaker_is_included():
    assert derive_lead_access("CRM_TEAM", "u1", frozenset({"u1"}))["access_user_ids"] == ["u1"]


def test_a_lead_with_no_caretaker_still_grants_team_access():
    access = derive_lead_access("CRM_TEAM", None, frozenset({"u1"}))
    assert access == {"access_team_ids": ["CRM_TEAM"], "access_user_ids": []}


def test_team_access_does_not_depend_on_the_caretaker_being_active():
    # The team keeps its claim on the lead even when the caretaker resigns;
    # that is what routes it to TEAM_UNASSIGNED rather than orphaning it.
    assert derive_lead_access("CRM_TEAM", "gone", frozenset())["access_team_ids"] == ["CRM_TEAM"]


def test_customer_access_unions_every_team_and_sorts():
    access = derive_customer_access(
        [("UPSELL_TEAM", "u2"), ("CRM_TEAM", "u1")], frozenset({"u1", "u2"})
    )
    assert access == {
        "access_team_ids": ["CRM_TEAM", "UPSELL_TEAM"],
        "access_user_ids": ["u1", "u2"],
    }


def test_customer_access_deduplicates_a_caretaker_holding_two_leads():
    access = derive_customer_access(
        [("CRM_TEAM", "u1"), ("UPSELL_TEAM", "u1")], frozenset({"u1"})
    )
    assert access == {
        "access_team_ids": ["CRM_TEAM", "UPSELL_TEAM"],
        "access_user_ids": ["u1"],
    }


def test_customer_access_drops_only_the_inactive_caretakers():
    access = derive_customer_access(
        [("CRM_TEAM", "resigned"), ("UPSELL_TEAM", "u2")], frozenset({"u2"})
    )
    assert access == {
        "access_team_ids": ["CRM_TEAM", "UPSELL_TEAM"],
        "access_user_ids": ["u2"],
    }


def test_customer_with_no_leads_has_empty_access():
    assert derive_customer_access([], frozenset({"u1"})) == {
        "access_team_ids": [],
        "access_user_ids": [],
    }


def test_output_is_deterministic_regardless_of_input_order():
    forward = derive_customer_access(
        [("A_TEAM", "u3"), ("B_TEAM", "u1"), ("C_TEAM", "u2")], frozenset({"u1", "u2", "u3"})
    )
    backward = derive_customer_access(
        [("C_TEAM", "u2"), ("B_TEAM", "u1"), ("A_TEAM", "u3")], frozenset({"u1", "u2", "u3"})
    )
    assert forward == backward
    assert forward["access_team_ids"] == ["A_TEAM", "B_TEAM", "C_TEAM"]
    assert forward["access_user_ids"] == ["u1", "u2", "u3"]


def test_blank_values_are_filtered_out():
    # The prototype's uniqueSorted drops falsy entries; an empty team code or
    # caretaker id must not become a grant that matches a blank column.
    access = derive_customer_access([("", ""), ("CRM_TEAM", "u1")], frozenset({"u1", ""}))
    assert access == {"access_team_ids": ["CRM_TEAM"], "access_user_ids": ["u1"]}


def test_returned_lists_are_plain_lists_not_sets():
    access = derive_lead_access("CRM_TEAM", "u1", frozenset({"u1"}))
    assert isinstance(access["access_team_ids"], list)
    assert isinstance(access["access_user_ids"], list)
