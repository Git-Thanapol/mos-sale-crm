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
    def _make(role: str = "ทั่วไป", staff_code: str = "") -> FakeUser:
        return FakeUser(role=role, staff_code=staff_code)

    return _make
