"""Level 3 — fail-closed row-level scoping, against a real Postgres DB.

Run via: docker compose exec web python -m pytest tests/db (needs the `db`
service reachable, which only holds from inside the compose network — these
tests cannot run from a bare host venv the way tests/unit can).
"""

from types import SimpleNamespace

import pytest

from crm.customers.models import Customer
from crm.followups.models import Followup

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def make_customer(**kwargs):
    defaults = {
        "phone_key": f"09{kwargs.get('_uid', '0000000')}",
        "phone1": f"09{kwargs.get('_uid', '0000000')}",
        "customer_name": "ทดสอบ",
        "staff_code": "",
        "owner_display": "",
    }
    defaults.update({k: v for k, v in kwargs.items() if k != "_uid"})
    return Customer.objects.create(**defaults)


@pytest.fixture
def customers():
    owned_a = make_customer(_uid="0000001", staff_code="S0001", customer_name="ลูกค้าเอ")
    owned_b = make_customer(_uid="0000002", staff_code="S0002", customer_name="ลูกค้าบี")
    unassigned = make_customer(_uid="0000003", staff_code="", customer_name="ลูกค้ายังไม่มอบหมาย")
    return {"owned_a": owned_a, "owned_b": owned_b, "unassigned": unassigned}


def editor():
    return SimpleNamespace(role="EDITOR", staff_code="")


def admin():
    return SimpleNamespace(role="ADMIN", staff_code="")


def telesell(staff_code: str):
    return SimpleNamespace(role="พนักงาน", staff_code=staff_code)


def test_editor_sees_all_customers(customers):
    assert Customer.objects.for_user(editor()).count() == 3


def test_admin_sees_all_customers(customers):
    assert Customer.objects.for_user(admin()).count() == 3


def test_telesell_sees_only_own_staff_code(customers):
    result = Customer.objects.for_user(telesell("S0001"))
    assert list(result) == [customers["owned_a"]]


def test_staff_without_staff_code_sees_nothing(customers):
    """The fail-closed rule: no staff_code -> .none(), never "everything"."""
    result = Customer.objects.for_user(telesell(""))
    assert result.count() == 0


def test_blank_string_staff_code_is_treated_as_missing(customers):
    user = SimpleNamespace(role="พนักงาน", staff_code="   ")  # whitespace-only
    assert Customer.objects.for_user(user).count() == 0


def test_null_staff_code_rows_invisible_to_non_manager(customers):
    """The unassigned cohort (blank staff_code) must never leak to any
    non-manager, regardless of that user's own staff_code.
    """
    result = Customer.objects.for_user(telesell("S0001"))
    assert customers["unassigned"] not in result


def test_scope_never_falls_back_to_owner_display(customers):
    """A customer whose owner_display matches the user's identity but whose
    staff_code differs must stay invisible — staff_code is the ONLY
    authorization key (docs/DECISIONS.md invariant 1).
    """
    tricky = make_customer(_uid="0000009", staff_code="S0002", owner_display="พนักงาน S0001")
    result = Customer.objects.for_user(telesell("S0001"))
    assert tricky not in result


@pytest.mark.parametrize("role_factory", [editor, admin])
def test_followup_scoping_via_customer_staff_code_path(customers, role_factory):
    Followup.objects.create(customer=customers["owned_a"], staff_code="S0001")
    Followup.objects.create(customer=customers["owned_b"], staff_code="S0002")
    assert Followup.objects.for_user(role_factory()).count() == 2


def test_followup_scoping_fail_closed_for_staff_without_code(customers):
    Followup.objects.create(customer=customers["owned_a"], staff_code="S0001")
    assert Followup.objects.for_user(telesell("")).count() == 0


def test_followup_scoping_matches_customer_staff_code_not_own_field(customers):
    """Followup.staff_code is a denormalized copy for display; the scope
    predicate must resolve through customer__staff_code, not the
    Followup row's own (possibly stale) staff_code column.
    """
    followup = Followup.objects.create(customer=customers["owned_a"], staff_code="STALE_CODE")
    result = Followup.objects.for_user(telesell("S0001"))
    assert followup in result
    assert Followup.objects.for_user(telesell("STALE_CODE")).count() == 0
