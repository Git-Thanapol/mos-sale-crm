"""Role gates for the import pages, and the role mapping onto the prototype's.

The prototype has ADMIN, TEAM_LEAD and TELESALE. This repo's EDITOR plays the
prototype's ADMIN; repo ADMIN is deliberately weaker (docs/DECISIONS.md) and,
like the read-only roles, has no import authority.

Run via: docker compose exec -T web python -m pytest tests/db/test_import_permissions.py
"""

import pytest

from crm.core.permissions import (
    can_import_v2,
    can_resolve_import_conflicts,
    can_view_import_history,
    import_actor_role,
)
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def build(kind):
    return {
        "editor": lambda: make_user(email="e@example.com", role=ROLE_EDITOR, staff_code="E1"),
        "inactive_editor": lambda: make_user(
            email="ie@example.com", role=ROLE_EDITOR, staff_code="E2", is_active=False
        ),
        "team_lead": lambda: make_user(
            email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["A"]
        ),
        "team_lead_no_scope": lambda: make_user(
            email="tl0@example.com", role="TEAM_LEAD", staff_code="L0", team_scope=[]
        ),
        "telesale": lambda: make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S1"),
        "admin": lambda: make_user(email="a@example.com", role=ROLE_ADMIN, staff_code="A1"),
        "viewer": lambda: make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="V1"),
    }[kind]()


#                       import  resolve  history  prototype role
EXPECTED = {
    "editor": (True, True, True, "ADMIN"),
    "inactive_editor": (False, False, False, None),
    "team_lead": (False, True, True, "TEAM_LEAD"),
    "team_lead_no_scope": (False, False, False, "TEAM_LEAD"),
    "telesale": (False, False, False, "TELESALE"),
    "admin": (False, False, False, None),
    "viewer": (False, False, False, None),
}


@pytest.mark.parametrize("kind", list(EXPECTED))
def test_import_gates_and_role_mapping(kind):
    user = build(kind)
    can_import, can_resolve, can_history, proto = EXPECTED[kind]
    assert can_import_v2(user) is can_import
    assert can_resolve_import_conflicts(user) is can_resolve
    assert can_view_import_history(user) is can_history
    assert import_actor_role(user) == proto


def test_anonymous_has_no_authority():
    assert not can_import_v2(None)
    assert not can_resolve_import_conflicts(None)
    assert import_actor_role(None) is None
