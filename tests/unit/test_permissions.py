"""The permission matrix had ZERO test coverage in the legacy app. This is
the highest-value new test file in the whole rewrite: it pins the exact
14-predicate x 7-role truth table from docs/legacy/auth-permissions-report.md
so every asymmetry (most importantly: ADMIN is deliberately WEAKER than
EDITOR for several actions) is locked down before a single page is built.
"""

import pytest

from crm.core import permissions as perm

pytestmark = pytest.mark.unit

ROLES = ["ADMIN", "EDITOR", "พนักงาน", "TELESELL", "ทั่วไป", "STAFF", "USER"]

# 1 = allowed, 0 = denied. Rows copied verbatim from the audit's truth table.
# Columns/roles order: ADMIN, EDITOR, พนักงาน, TELESELL, ทั่วไป, STAFF, USER
MATRIX: dict[str, tuple[int, int, int, int, int, int, int]] = {
    "is_telesell":                    (0, 0, 1, 1, 0, 0, 0),
    "is_staff_limited":                (0, 0, 1, 1, 1, 1, 1),
    "can_manage_all":                  (1, 1, 0, 0, 0, 0, 0),
    "can_edit_users":                  (1, 1, 0, 0, 0, 0, 0),
    "can_edit_products":               (1, 1, 0, 0, 0, 0, 0),
    "can_import_excel":                (1, 1, 0, 0, 0, 0, 0),
    "can_add_manual_order":            (1, 1, 1, 1, 0, 0, 0),
    "can_export_customers":            (0, 1, 0, 0, 0, 0, 0),
    "can_assign_customer_owner":       (0, 1, 0, 0, 0, 0, 0),
    "can_delete_order":                (0, 1, 1, 0, 0, 1, 0),
    "can_view_system_page":            (0, 1, 0, 0, 0, 0, 0),
    "can_manage_system_page":          (1, 1, 0, 0, 0, 0, 0),
    "can_view_followup":               (0, 1, 1, 1, 1, 1, 1),
    "can_view_followup_owner_filter":  (0, 1, 0, 0, 0, 0, 0),
}

PREDICATES = {
    "is_telesell": perm.is_telesell,
    "is_staff_limited": perm.is_staff_limited,
    "can_manage_all": perm.can_manage_all,
    "can_edit_users": perm.can_edit_users,
    "can_edit_products": perm.can_edit_products,
    "can_import_excel": perm.can_import_excel,
    "can_add_manual_order": perm.can_add_manual_order,
    "can_export_customers": perm.can_export_customers,
    "can_assign_customer_owner": perm.can_assign_customer_owner,
    "can_delete_order": perm.can_delete_order,
    "can_view_system_page": perm.can_view_system_page,
    "can_manage_system_page": perm.can_manage_system_page,
    "can_view_followup": perm.can_view_followup,
    "can_view_followup_owner_filter": perm.can_view_followup_owner_filter,
}


@pytest.mark.parametrize("predicate_name", sorted(MATRIX))
def test_matrix(predicate_name, make_user):
    predicate = PREDICATES[predicate_name]
    expected_row = MATRIX[predicate_name]
    for role, expected in zip(ROLES, expected_row, strict=True):
        user = make_user(role=role, staff_code="S001")
        assert predicate(user) == bool(expected), (
            f"{predicate_name}(role={role!r}) expected {bool(expected)}, "
            f"got {predicate(user)}"
        )


def test_admin_cannot_export_customers(make_user):
    assert perm.can_export_customers(make_user(role="ADMIN")) is False
    assert perm.can_export_customers(make_user(role="EDITOR")) is True


def test_admin_cannot_assign_owner(make_user):
    assert perm.can_assign_customer_owner(make_user(role="ADMIN")) is False
    assert perm.can_assign_customer_owner(make_user(role="EDITOR")) is True


def test_admin_cannot_view_system_page_but_can_manage_it(make_user):
    admin = make_user(role="ADMIN")
    assert perm.can_view_system_page(admin) is False
    assert perm.can_manage_system_page(admin) is True


def test_admin_cannot_view_followup(make_user):
    assert perm.can_view_followup(make_user(role="ADMIN")) is False


def test_delete_order_admits_english_STAFF_not_thai_viewer(make_user):
    # ORDER_DELETE_ROLES = {EDITOR, พนักงาน, "STAFF"} — the English "STAFF"
    # literal (viewer bucket) is admitted, but its Thai alias ทั่วไป is not.
    # This asymmetry is preserved verbatim; do not "fix" it into consistency.
    assert perm.can_delete_order(make_user(role="STAFF")) is True
    assert perm.can_delete_order(make_user(role="ทั่วไป")) is False


def test_normalize_role_uppercases_only_ascii_names():
    assert perm.normalize_role("editor") == "EDITOR"
    assert perm.normalize_role(" Editor ") == "EDITOR"
    assert perm.normalize_role("telesell") == "TELESELL"
    assert perm.normalize_role("พนักงาน") == "พนักงาน"
    assert perm.normalize_role("ทั่วไป") == "ทั่วไป"


def test_unknown_role_grants_nothing(make_user):
    user = make_user(role="MANAGER", staff_code="S001")  # not a recognized role string
    assert perm.can_manage_all(user) is False
    assert perm.is_telesell(user) is False
    assert perm.is_staff_limited(user) is False
    assert perm.can_view_followup(user) is False


def test_can_edit_customer_lead_requires_matching_staff_code(make_user):
    telesell = make_user(role="พนักงาน", staff_code="S001")
    other_customer = type("Customer", (), {"staff_code": "S002"})()
    own_customer = type("Customer", (), {"staff_code": "S001"})()

    assert perm.can_edit_customer_lead(telesell, other_customer) is False
    assert perm.can_edit_customer_lead(telesell, own_customer) is True


def test_can_edit_customer_lead_denies_admin(make_user):
    admin = make_user(role="ADMIN", staff_code="S001")
    customer = type("Customer", (), {"staff_code": "S001"})()
    assert perm.can_edit_customer_lead(admin, customer) is False


def test_can_edit_customer_lead_allows_editor_regardless_of_staff_code(make_user):
    editor = make_user(role="EDITOR", staff_code="")
    customer = type("Customer", (), {"staff_code": "S999"})()
    assert perm.can_edit_customer_lead(editor, customer) is True


def test_can_edit_customer_lead_denies_telesell_with_no_staff_code(make_user):
    telesell = make_user(role="พนักงาน", staff_code="")
    customer = type("Customer", (), {"staff_code": ""})()
    assert perm.can_edit_customer_lead(telesell, customer) is False


def test_can_edit_customer_lead_denies_none_user():
    customer = type("Customer", (), {"staff_code": "S001"})()
    assert perm.can_edit_customer_lead(None, customer) is False
