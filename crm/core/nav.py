"""Sidebar navigation — single source of truth, mirrors nav_utils.py.

Each entry is (label, url_name, permission_predicate_or_None). An item whose
predicate fails renders disabled rather than being omitted, matching the
legacy sidebar's behavior of showing-but-graying-out unreachable pages.
"""

from __future__ import annotations

from crm.core import permissions as perm

NAV_GROUPS: list[tuple[str, list[tuple[str, str, object]]]] = [
    ("ภาพรวม", [
        ("Dashboard", "reporting:dashboard", None),
        ("ยอดขายทีม", "teams:list", perm.can_view_team_sales),
        ("ตารางยอดขายรายวัน", "matrix:index", perm.can_view_daily_matrix),
    ]),
    ("ลูกค้า", [
        ("ติดตามลูกค้า", "followups:list", perm.can_view_followup),
        ("เพิ่มคำสั่งซื้อ", "orders:new", perm.can_add_manual_order),
        ("นำเข้า Excel", "imports:upload", perm.can_import_excel),
        ("คำสั่งซื้อออนไลน์", "online_orders:list", perm.can_view_followup),
    ]),
    ("ข้อมูล", [
        ("สินค้า", "catalog:list", None),
    ]),
    ("ระบบ", [
        ("Sync / System Status", "core:system_status", perm.can_view_system_page),
        ("User / Role", "accounts:user_list", perm.can_edit_users),
        ("Settings", "core:settings", perm.can_view_system_page),
    ]),
]


def nav_context(request):
    """Template context processor: adds `nav_groups` with each item's
    `allowed` flag resolved for request.user. Registered in
    config/settings/base.py TEMPLATES.
    """
    user = getattr(request, "user", None)
    resolved = []
    for group_label, items in NAV_GROUPS:
        resolved_items = [
            {
                "label": label,
                "url_name": url_name,
                "allowed": predicate(user) if predicate else True,
            }
            for label, url_name, predicate in items
        ]
        resolved.append({"label": group_label, "items": resolved_items})
    return {"nav_groups": resolved}
