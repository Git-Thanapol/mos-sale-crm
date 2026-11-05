from types import SimpleNamespace

import pytest


class FakeUser(SimpleNamespace):
    """Lightweight stand-in satisfying permissions.HasRoleAndStaffCode,
    for tests that don't need a real accounts.User row. Level 3+ (scoping)
    tests use the real model via factory_boy once Phase 2 models exist.
    """

    def __bool__(self):
        return True


@pytest.fixture
def make_user():
    def _make(
        role: str = "ทั่วไป",
        staff_code: str = "",
        team_scope: list[str] | None = None,
        is_active: bool = True,
    ) -> FakeUser:
        """`team_scope` and `is_active` are needed by the Lead-surface
        predicates: a TEAM_LEAD is authorized per team, and a disabled account
        must lose authority without a separate code path.
        """
        return FakeUser(
            role=role,
            staff_code=staff_code,
            team_scope=list(team_scope or []),
            is_active=is_active,
        )

    return _make
