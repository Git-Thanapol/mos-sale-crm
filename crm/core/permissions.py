"""Authorization matrix — 1:1 port of crm_streamlit/permissions.py.

Function names are kept identical to the legacy module on purpose: the audit
docs in docs/legacy/ reference these names directly, and this module is the
single source of truth referenced from routes (views), querysets
(crm.core.scoping), and object-level checks.

Do not map roles onto django.contrib.auth Groups/Permissions. The matrix is
not additive (ADMIN outranks EDITOR for user/product/import management but is
deliberately WEAKER than EDITOR for export/owner-assign/order-delete/system
pages/follow-up) — see docs/DECISIONS.md "Preserved verbatim". Groups would
make that asymmetry look like a bug and invite "fixing" it.
"""

from __future__ import annotations

from typing import Protocol

ROLE_ADMIN = "ADMIN"
ROLE_EDITOR = "EDITOR"
ROLE_STAFF = "พนักงาน"
ROLE_VIEWER = "ทั่วไป"
ROLE_TELESELL = ROLE_STAFF
ROLE_STAFF_READONLY = ROLE_VIEWER

ROLE_TELESELL_ALIASES = {ROLE_TELESELL, "TELESELL"}
ROLE_STAFF_ALIASES = {ROLE_STAFF_READONLY, "STAFF"}
ROLE_USER_ALIASES = {"USER"}
SYSTEM_VIEW_ROLES = {ROLE_EDITOR}
ORDER_DELETE_ROLES = {ROLE_EDITOR, ROLE_STAFF, "STAFF"}

ROLE_OPTIONS = ["EDITOR", "ADMIN", ROLE_STAFF, "TELESELL", "STAFF", "USER", ROLE_VIEWER]


class HasRoleAndStaffCode(Protocol):
    """Structural type: Django's accounts.User satisfies this without inheritance."""

    role: str
    staff_code: str


def clean(value: object) -> str:
    return str(value or "").strip()


def normalize_role(role: object) -> str:
    value = clean(role)
    upper_value = value.upper()
    if upper_value in {"ADMIN", "EDITOR", "TELESELL", "STAFF", "USER"}:
        return upper_value
    return value


def user_role(user: HasRoleAndStaffCode | None) -> str:
    return normalize_role(getattr(user, "role", None) if user else None)


def _normalized_roles(roles: set[str]) -> set[str]:
    return {normalize_role(role) for role in roles}


def is_telesell(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) in _normalized_roles(ROLE_TELESELL_ALIASES)


def is_staff_limited(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) in _normalized_roles(
        ROLE_TELESELL_ALIASES | ROLE_STAFF_ALIASES | ROLE_USER_ALIASES
    )


def can_manage_all(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) in {ROLE_ADMIN, ROLE_EDITOR}


def can_edit_users(user: HasRoleAndStaffCode | None) -> bool:
    return can_manage_all(user)


def can_edit_products(user: HasRoleAndStaffCode | None) -> bool:
    return can_manage_all(user)


def can_import_excel(user: HasRoleAndStaffCode | None) -> bool:
    return can_manage_all(user)


def can_add_manual_order(user: HasRoleAndStaffCode | None) -> bool:
    return can_manage_all(user) or is_telesell(user)


def can_export_customers(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) == ROLE_EDITOR


def can_assign_customer_owner(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) == ROLE_EDITOR


def can_delete_order(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) in _normalized_roles(ORDER_DELETE_ROLES)


def can_view_system_page(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) in _normalized_roles(SYSTEM_VIEW_ROLES)


def can_manage_system_page(user: HasRoleAndStaffCode | None) -> bool:
    return can_manage_all(user)


def can_view_followup(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) == ROLE_EDITOR or is_staff_limited(user)


def can_view_followup_owner_filter(user: HasRoleAndStaffCode | None) -> bool:
    return user_role(user) == ROLE_EDITOR


def can_view_team_sales(user: HasRoleAndStaffCode | None) -> bool:
    # Legacy gate is an inline `user_role(user) != ROLE_EDITOR` check (see
    # pages/team_sales.py), not can_manage_all — ADMIN is deliberately
    # blocked here even though ADMIN can manage users/products/imports.
    return user_role(user) == ROLE_EDITOR


def can_view_daily_matrix(user: HasRoleAndStaffCode | None) -> bool:
    # New page (2026-07-31), placed right after Team Sales in the nav and
    # gated the same way — EDITOR-only, ADMIN deliberately excluded.
    return user_role(user) == ROLE_EDITOR


def can_edit_customer_lead(user: HasRoleAndStaffCode | None, customer) -> bool:
    if not user:
        return False
    if user_role(user) == ROLE_EDITOR:
        return True
    if not is_telesell(user):
        return False

    staff_code = clean(getattr(user, "staff_code", ""))
    if not staff_code:
        return False

    customer_staff_code = clean(getattr(customer, "staff_code", ""))
    return bool(customer_staff_code and customer_staff_code == staff_code)


# --- Thai permission-denial messages, verbatim from docs/parity/thai_strings.json ---
MSG_FOLLOWUP_BLOCKED = "หน้านี้ใช้ได้เฉพาะ EDITOR และพนักงานที่ดูแลลูกค้า"
MSG_IMPORT_PAGE_BLOCKED = "หน้านี้ใช้ได้เฉพาะ EDITOR และพนักงานที่มีสิทธิเพิ่มคำสั่งซื้อ"
MSG_TEAM_SALES_BLOCKED = "คุณไม่มีสิทธิ์เข้าดูหน้ายอดขายทีม"
MSG_SYSTEM_PAGE_BLOCKED = "หน้านี้เป็นระบบหลังบ้าน เฉพาะ EDITOR เท่านั้น"
MSG_CUSTOMER_DETAIL_DENIED = "ไม่มีสิทธิ์เข้าถึงข้อมูลนี้"
MSG_ROW_NO_EDIT_RIGHT = "ไม่มีสิทธิ์แก้ไขรายการนี้"
MSG_OWNER_ASSIGN_DENIED = "ไม่มีสิทธิ์มอบหมายผู้ดูแล"
MSG_URL_UPDATE_DENIED = "ไม่มีสิทธิ์อัปเดต URL"
MSG_GENERIC_DENIED = "ไม่มีสิทธิ์เข้าถึง"

_PREDICATE_MESSAGES: dict[str, str] = {
    "can_view_followup": MSG_FOLLOWUP_BLOCKED,
    "can_add_manual_order": MSG_IMPORT_PAGE_BLOCKED,
    "can_view_system_page": MSG_SYSTEM_PAGE_BLOCKED,
    "can_view_team_sales": MSG_TEAM_SALES_BLOCKED,
    "can_view_daily_matrix": MSG_TEAM_SALES_BLOCKED,
}

_PREDICATES = {
    "can_manage_all": can_manage_all,
    "can_edit_users": can_edit_users,
    "can_edit_products": can_edit_products,
    "can_import_excel": can_import_excel,
    "can_add_manual_order": can_add_manual_order,
    "can_export_customers": can_export_customers,
    "can_assign_customer_owner": can_assign_customer_owner,
    "can_delete_order": can_delete_order,
    "can_view_system_page": can_view_system_page,
    "can_manage_system_page": can_manage_system_page,
    "can_view_followup": can_view_followup,
    "can_view_followup_owner_filter": can_view_followup_owner_filter,
    "can_view_team_sales": can_view_team_sales,
    "can_view_daily_matrix": can_view_daily_matrix,
}


def require_permission(predicate_name: str):
    """Route-level enforcement layer. Usage: @require_permission("can_view_followup").

    Raises django.core.exceptions.PermissionDenied with the exact Thai message
    the legacy page showed via st.warning + st.stop(). The 403 template
    renders exception.args[0] verbatim.
    """
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    predicate = _PREDICATES[predicate_name]
    message = _PREDICATE_MESSAGES.get(predicate_name, MSG_GENERIC_DENIED)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not predicate(request.user):
                raise PermissionDenied(message)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


# --- CRM V2 Lead surface -----------------------------------------------------
# Role mapping (docs/DECISIONS.md): the spec's Admin is this repo's EDITOR.
# This repo's ADMIN is NOT the spec's Admin -- the "Preserved verbatim"
# asymmetry stands, so ADMIN stays weaker for follow-up/export/owner-assign and
# must not be quietly strengthened by the predicates below.
ROLE_TEAM_LEAD = "TEAM_LEAD"

MSG_LEAD_OUT_OF_SCOPE = "ไม่มีสิทธิ์เข้าถึง Lead ของทีมอื่น"


def _is_usable(user: object) -> bool:
    """Falsy or deactivated users have no authority at all.

    Checked before every role test so a disabled account loses access without
    a separate code path (08_SECURITY: a disabled/resigned user must not retain
    operational access or caretaker authority).
    """
    if not user:
        return False
    return bool(getattr(user, "is_active", True))


def is_team_lead(user: object) -> bool:
    return _is_usable(user) and user_role(user) == ROLE_TEAM_LEAD


def team_scope(user: object) -> frozenset[str]:
    if not _is_usable(user):
        return frozenset()
    return frozenset(clean(code) for code in getattr(user, "team_scope", []) or [] if clean(code))


def _lead_team_code(lead: object) -> str:
    """The LEAD's own team.

    Deliberately not the customer's access array: that array is a union across
    every team holding a lead for the customer, so judging a single lead by it
    would let a team-A user reach a team-B lead for the same person.
    """
    team = getattr(lead, "team", None)
    return clean(getattr(team, "code", "")) if team is not None else ""


def can_view_lead(user: object, lead: object) -> bool:
    if not _is_usable(user):
        return False
    if user_role(user) == ROLE_EDITOR:
        return True
    if is_team_lead(user):
        return _lead_team_code(lead) in team_scope(user)
    return can_edit_customer_lead(user, getattr(lead, "customer", None))


def can_edit_lead(user: object, lead: object) -> bool:
    """Edit the lead's own follow-up state (status, note, appointment).

    Same gate as viewing: a telesale who may see their customer's lead may work
    it. Ownership and admin actions have their own, narrower predicates.
    """
    return can_view_lead(user, lead)


def can_reassign_caretaker(user: object, lead: object) -> bool:
    """Ownership mutation. EDITOR anywhere, TEAM_LEAD within scope, nobody else.

    A telesale never reassigns ownership, even on their own lead
    (08_SECURITY: "No ownership/admin/import authority").
    """
    if not _is_usable(user):
        return False
    if user_role(user) == ROLE_EDITOR:
        return True
    return is_team_lead(user) and _lead_team_code(lead) in team_scope(user)


def can_review_stopped(user: object, lead: object) -> bool:
    """STOPPED review: authorized reactivation/reassignment."""
    return can_reassign_caretaker(user, lead)


def can_resolve_intake(user: object) -> bool:
    """Global Unassigned is a cross-team identity decision, so EDITOR only.

    A TEAM_LEAD is scoped to one team and cannot judge which team a
    Team-unknown order belongs to.
    """
    return _is_usable(user) and user_role(user) == ROLE_EDITOR


_PREDICATES.update(
    {
        "can_resolve_intake": can_resolve_intake,
    }
)
_PREDICATE_MESSAGES.update(
    {
        "can_resolve_intake": MSG_SYSTEM_PAGE_BLOCKED,
    }
)


def can_review_any_lead(user: object) -> bool:
    """Role-level: may this user act on ownership for ANY lead?

    Used to decide whether to RENDER a review section, where there is no
    specific lead yet. EDITOR qualifies everywhere; a TEAM_LEAD qualifies when
    they hold a non-empty team scope. Per-row authority is still decided by
    can_reassign_caretaker, so this only ever controls whether a heading and
    its queue are drawn -- never whether a write is allowed.
    """
    if not _is_usable(user):
        return False
    if user_role(user) == ROLE_EDITOR:
        return True
    return is_team_lead(user) and bool(team_scope(user))
