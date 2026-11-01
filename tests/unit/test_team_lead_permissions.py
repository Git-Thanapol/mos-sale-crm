"""TEAM_LEAD scope predicates, and the role mapping onto this repo's matrix.

08_SECURITY_AND_AUTH_REQUIREMENTS.md gives three roles: Admin (full), Team Lead
(own Team only), Telesale (permitted Leads only, no ownership/admin/import
authority). This repo's matrix is EDITOR / ADMIN / พนักงาน / ทั่วไป and is
deliberately asymmetric -- docs/DECISIONS.md "Preserved verbatim" records that
ADMIN is WEAKER than EDITOR for follow-up, export and owner-assign.

The mapping (see docs/DECISIONS.md): the spec's Admin is this repo's EDITOR.
This repo's ADMIN is NOT the spec's Admin and keeps its weaker scope.
"""

import pytest

from crm.core.permissions import (
    can_edit_lead,
    can_reassign_caretaker,
    can_resolve_intake,
    can_review_stopped,
    can_view_lead,
    is_team_lead,
    team_scope,
)

pytestmark = pytest.mark.unit


class FakeLead:
    """Stands in for a Lead without a database: the predicates read only the
    team code and the lead's own access array.
    """

    def __init__(self, team_code="CRM_TEAM", staff_code="", access_team_ids=None):
        self.team = type("T", (), {"code": team_code})()
        self.customer = type("C", (), {"staff_code": staff_code})()
        self.access_team_ids = access_team_ids if access_team_ids is not None else [team_code]


def lead(team_code="CRM_TEAM", staff_code=""):
    return FakeLead(team_code=team_code, staff_code=staff_code)


def test_is_team_lead_recognises_only_the_team_lead_role(make_user):
    assert is_team_lead(make_user(role="TEAM_LEAD")) is True
    assert is_team_lead(make_user(role="EDITOR")) is False
    assert is_team_lead(make_user(role="พนักงาน")) is False


def test_team_scope_reads_the_users_authorized_teams(make_user):
    assert team_scope(make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])) == frozenset(
        {"CRM_TEAM"}
    )


def test_team_lead_may_act_only_within_their_team_scope(make_user):
    lead_user = make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])
    assert can_edit_lead(lead_user, lead("CRM_TEAM")) is True
    assert can_edit_lead(lead_user, lead("UPSELL_TEAM")) is False


def test_team_lead_with_an_empty_scope_can_do_nothing(make_user):
    assert can_edit_lead(make_user(role="TEAM_LEAD", team_scope=[]), lead("CRM_TEAM")) is False


def test_team_lead_may_reassign_within_scope_but_not_outside(make_user):
    lead_user = make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])
    assert can_reassign_caretaker(lead_user, lead("CRM_TEAM")) is True
    assert can_reassign_caretaker(lead_user, lead("UPSELL_TEAM")) is False


def test_telesale_can_edit_their_own_lead_but_not_reassign_it(make_user):
    telesell = make_user(role="พนักงาน", staff_code="S0001")
    own = lead("CRM_TEAM", staff_code="S0001")
    assert can_edit_lead(telesell, own) is True
    assert can_reassign_caretaker(telesell, own) is False


def test_telesale_cannot_touch_another_staff_members_lead(make_user):
    telesell = make_user(role="พนักงาน", staff_code="S0001")
    assert can_edit_lead(telesell, lead("CRM_TEAM", staff_code="S0002")) is False
    assert can_view_lead(telesell, lead("CRM_TEAM", staff_code="S0002")) is False


def test_telesale_cannot_resolve_intake(make_user):
    assert can_resolve_intake(make_user(role="พนักงาน", staff_code="S0001")) is False


def test_team_lead_cannot_resolve_intake(make_user):
    # Global Unassigned is a cross-team identity decision: Admin only.
    assert can_resolve_intake(make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])) is False


def test_the_specs_admin_maps_to_editor(make_user):
    editor = make_user(role="EDITOR")
    assert can_reassign_caretaker(editor, lead("CRM_TEAM")) is True
    assert can_resolve_intake(editor) is True
    assert can_review_stopped(editor, lead("CRM_TEAM")) is True


def test_this_repos_admin_is_not_the_specs_admin(make_user):
    # Preserved verbatim: ADMIN is weaker than EDITOR here.
    admin = make_user(role="ADMIN")
    assert can_reassign_caretaker(admin, lead("CRM_TEAM")) is False
    assert can_resolve_intake(admin) is False


def test_a_viewer_role_has_no_lead_authority(make_user):
    viewer = make_user(role="ทั่วไป")
    assert can_edit_lead(viewer, lead("CRM_TEAM")) is False
    assert can_reassign_caretaker(viewer, lead("CRM_TEAM")) is False


def test_a_disabled_user_has_no_lead_permissions(make_user):
    disabled = make_user(role="EDITOR", is_active=False)
    assert can_view_lead(disabled, lead("CRM_TEAM")) is False
    assert can_edit_lead(disabled, lead("CRM_TEAM")) is False
    assert can_reassign_caretaker(disabled, lead("CRM_TEAM")) is False
    assert can_resolve_intake(disabled) is False


def test_no_user_at_all_has_no_permissions():
    assert can_view_lead(None, lead("CRM_TEAM")) is False
    assert can_edit_lead(None, lead("CRM_TEAM")) is False
    assert can_resolve_intake(None) is False


def test_team_lead_scope_reads_the_leads_own_team_not_a_customer_wide_union(make_user):
    # Review Focus #4, at the predicate layer: a lead whose access array was
    # polluted with another team must still be judged by its own team.
    lead_user = make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])
    cross = FakeLead(team_code="UPSELL_TEAM", access_team_ids=["CRM_TEAM", "UPSELL_TEAM"])
    assert can_edit_lead(lead_user, cross) is False
