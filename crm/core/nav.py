"""Sidebar navigation — single source of truth, mirrors nav_utils.py.

Each entry is (label, url_name, permission_predicate_or_None) with an optional
4th element: a list of children, each (label, url_name, predicate_or_None).

A top-level item whose predicate fails renders disabled rather than being
omitted, matching the legacy sidebar's behavior of showing-but-graying-out
unreachable pages. A CHILD whose predicate fails is omitted instead: that is
what the CRM V2 prototype's `navigationFor` does, and a review queue a user
cannot open is noise, not a disabled affordance. Hidden entries are a
convenience only; every view re-checks authorization itself.
"""

from __future__ import annotations

from crm.core import permissions as perm

NAV_GROUPS: list[tuple] = [
    ("ภาพรวม", [
        ("Dashboard", "reporting:dashboard", None),
        ("ยอดขายทีม", "teams:list", perm.can_view_team_sales),
        ("ตารางยอดขายรายวัน", "matrix:index", perm.can_view_daily_matrix),
    ]),
    ("ลูกค้า", [
        (
            "ติดตามลูกค้า",
            "followups:list",
            perm.can_view_followup,
            [
                ("งานของฉัน", "leadui:my_work", perm.can_use_lead_workspace),
                ("Global Unassigned", "leadui:global_unassigned", perm.can_resolve_intake),
                ("Team Unassigned", "leadui:team_unassigned", perm.can_review_any_lead),
                ("STOPPED Review", "leadui:stopped_review", perm.can_review_any_lead),
                ("นำเข้าข้อมูล", "leadui:import", perm.can_import_v2),
                ("แก้ Conflict", "leadui:import_conflicts", perm.can_resolve_import_conflicts),
                ("ประวัตินำเข้า", "leadui:import_history", perm.can_view_import_history),
                ("Migration Decisions", "leadui:migration_decisions", perm.can_import_v2),
            ],
        ),
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
    current = getattr(getattr(request, "resolver_match", None), "view_name", "") or ""
    resolved = []
    for group_label, items in NAV_GROUPS:
        resolved_items = []
        for entry in items:
            label, url_name, predicate = entry[0], entry[1], entry[2]
            child_defs = entry[3] if len(entry) > 3 else []
            allowed = predicate(user) if predicate else True
            children = []
            if allowed:
                for child_label, child_url, child_predicate in child_defs:
                    if child_predicate is None or child_predicate(user):
                        children.append(
                            {
                                "label": child_label,
                                "url_name": child_url,
                                "active": current == child_url,
                            }
                        )
            resolved_items.append(
                {
                    "label": label,
                    "url_name": url_name,
                    "allowed": allowed,
                    "children": children,
                    "active": current == url_name or any(c["active"] for c in children),
                }
            )
        resolved.append({"label": group_label, "items": resolved_items})
    return {"nav_groups": resolved}
