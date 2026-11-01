"""Sidebar sub-menu under ติดตามลูกค้า, resolved per role.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_nav.py
"""

import pytest
from django.test import RequestFactory

from crm.core.nav import nav_context
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_user

pytestmark = [pytest.mark.view, pytest.mark.django_db]


def _request(user, view_name="reporting:dashboard"):
    request = RequestFactory().get("/")
    request.user = user

    class _Match:
        pass

    request.resolver_match = _Match()
    request.resolver_match.view_name = view_name
    return request


def _followup_item(user, view_name="reporting:dashboard"):
    groups = nav_context(_request(user, view_name))["nav_groups"]
    for group in groups:
        for item in group["items"]:
            if item["url_name"] == "followups:list":
                return item
    raise AssertionError("followups:list not in nav")


def _labels(item):
    return [child["label"] for child in item["children"]]


def test_editor_sees_every_child():
    editor = make_user(email="e@example.com", role=ROLE_EDITOR, staff_code="E1")
    assert _labels(_followup_item(editor)) == [
        "งานของฉัน",
        "Global Unassigned",
        "Team Unassigned",
        "STOPPED Review",
        "นำเข้าข้อมูล",
        "แก้ Conflict",
        "ประวัตินำเข้า",
    ]


def test_team_lead_sees_work_review_queues_conflicts_and_history():
    lead = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["A"])
    assert _labels(_followup_item(lead)) == [
        "งานของฉัน",
        "Team Unassigned",
        "STOPPED Review",
        "แก้ Conflict",
        "ประวัตินำเข้า",
    ]


def test_telesale_sees_only_my_work():
    ts = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S1")
    assert _labels(_followup_item(ts)) == ["งานของฉัน"]


def test_viewer_sees_no_lead_children():
    # The legacy follow-up page stays reachable for this role (parent allowed);
    # the lead workspace is not theirs, so no sub-menu.
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER)
    assert _followup_item(viewer)["children"] == []


def test_admin_repo_role_sees_no_review_children():
    admin = make_user(email="a@example.com", role=ROLE_ADMIN, staff_code="A1")
    labels = _labels(_followup_item(admin))
    assert "Team Unassigned" not in labels and "Global Unassigned" not in labels


def test_parent_and_child_active_when_a_child_view_matches():
    editor = make_user(email="e2@example.com", role=ROLE_EDITOR, staff_code="E2")
    item = _followup_item(editor, view_name="leadui:team_unassigned")
    active = [child["label"] for child in item["children"] if child["active"]]
    assert active == ["Team Unassigned"]
    assert item["active"] is True


def test_parent_not_active_on_other_pages():
    editor = make_user(email="e3@example.com", role=ROLE_EDITOR, staff_code="E3")
    item = _followup_item(editor, view_name="reporting:dashboard")
    assert item["active"] is False
    assert not any(child["active"] for child in item["children"])


def test_items_without_children_have_an_empty_list():
    editor = make_user(email="e4@example.com", role=ROLE_EDITOR, staff_code="E4")
    groups = nav_context(_request(editor))["nav_groups"]
    dashboard = groups[0]["items"][0]
    assert dashboard["url_name"] == "reporting:dashboard"
    assert dashboard["children"] == []


def test_sidebar_renders_without_the_routes_mounted_yet_and_keeps_parent_link(client):
    # Routes under `leadui:` are mounted by a later task; the template must
    # skip a child whose reverse fails instead of raising NoReverseMatch.
    editor = make_user(email="e5@example.com", role=ROLE_EDITOR, staff_code="E5")
    editor.set_password("test-pass-12345")
    editor.save()
    assert client.login(email=editor.email, password="test-pass-12345")
    body = client.get("/").content.decode()
    assert 'class="crm-sidebar__item' in body
    assert "ติดตามลูกค้า" in body
