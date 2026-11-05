"""Team Unassigned and STOPPED Review, as in the prototype's ReviewQueue.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_review_queues.py
"""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.db.factories import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"
TEAM = "leadui:team_unassigned"
STOPPED = "leadui:stopped_review"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def get(client, user, route, **params):
    login(client, user)
    return client.get(reverse(route), params)


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def team_lead():
    return make_user(
        email="tl@example.com", role="TEAM_LEAD", staff_code="L001", team_scope=["TEAM_A"]
    )


def test_team_unassigned_title_subtitle_and_toolbar(client, editor):
    body = get(client, editor, TEAM).content.decode()
    assert "<h1>Team Unassigned</h1>" in body
    assert "<p>Lead มี Team แล้ว แต่ยังไม่มี caretaker</p>" in body
    assert '<form class="toolbar" method="get" data-v2-filters>' in body
    assert 'aria-label="ระยะห่างจากออเดอร์ล่าสุด Team Unassigned"' in body
    assert "ทุกช่วงออเดอร์ล่าสุด" in body


def test_stopped_review_title_subtitle_and_no_toolbar(client, editor):
    body = get(client, editor, STOPPED).content.decode()
    assert "<h1>STOPPED Review</h1>" in body
    assert "<p>STOPPED เป็น lifecycle state แยกจาก Unassigned</p>" in body
    assert 'class="toolbar"' not in body


def test_team_unassigned_lists_only_unassigned_non_stopped_leads(client, editor):
    carer = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S1")
    waiting = make_lead(customer=make_customer())
    assigned = make_lead(customer=make_customer(), caretaker=carer)
    stopped = make_lead(customer=make_customer(), lifecycle_status="STOPPED")
    body = get(client, editor, TEAM).content.decode()
    assert f"lead/{waiting.id}/" in body
    assert f"lead/{assigned.id}/" not in body
    assert f"lead/{stopped.id}/" not in body


def test_stopped_queue_lists_every_stopped_lead_regardless_of_routing(client, editor):
    # STOPPED is a lifecycle state, never an "unassigned" one.
    carer = make_user(email="c2@example.com", role=ROLE_TELESELL, staff_code="S1")
    stopped_assigned = make_lead(
        customer=make_customer(), caretaker=carer, lifecycle_status="STOPPED"
    )
    stopped_unassigned = make_lead(customer=make_customer(), lifecycle_status="STOPPED")
    following = make_lead(customer=make_customer(), lifecycle_status="FOLLOWING")
    body = get(client, editor, STOPPED).content.decode()
    assert f"lead/{stopped_assigned.id}/" in body
    assert f"lead/{stopped_unassigned.id}/" in body
    assert f"lead/{following.id}/" not in body
    assert ">หยุดติดตาม</span>" in body


def test_a_team_lead_sees_only_their_scoped_team_in_both_queues(client, team_lead):
    mine = make_lead(customer=make_customer(), team_code="TEAM_A")
    other = make_lead(customer=make_customer(), team_code="TEAM_B")
    mine_stopped = make_lead(customer=make_customer(), team_code="TEAM_A", lifecycle_status="STOPPED")
    other_stopped = make_lead(customer=make_customer(), team_code="TEAM_B", lifecycle_status="STOPPED")
    team = get(client, team_lead, TEAM).content.decode()
    assert f"lead/{mine.id}/" in team and f"lead/{other.id}/" not in team
    stopped = get(client, team_lead, STOPPED).content.decode()
    assert f"lead/{mine_stopped.id}/" in stopped and f"lead/{other_stopped.id}/" not in stopped


def test_customer_names_are_not_links_in_review_queues(client, editor):
    lead = make_lead(customer=make_customer(customer_name="ลูกค้าคิว"))
    body = get(client, editor, TEAM).content.decode()
    assert "ลูกค้าคิว" in body
    assert reverse("leadui:customer_detail", args=[lead.customer_id]) not in body
    assert f"lead/{lead.id}/" in body


@pytest.mark.parametrize("route", [TEAM, STOPPED])
def test_roles_without_review_authority_get_404(client, route):
    telesale = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S1")
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="V1")
    admin = make_user(email="a@example.com", role=ROLE_ADMIN, staff_code="A1")
    empty_scope_lead = make_user(
        email="tl0@example.com", role="TEAM_LEAD", staff_code="L0", team_scope=[]
    )
    for user in (telesale, viewer, admin, empty_scope_lead):
        assert get(client, user, route).status_code == 404, user.email


@pytest.mark.parametrize("route", [TEAM, STOPPED])
def test_anonymous_is_sent_to_login(client, route):
    assert client.get(reverse(route)).status_code == 302


def test_age_preset_filters_team_unassigned(client, editor):
    now = timezone.now()
    no_order = make_lead(customer=make_customer(), last_order_at=None)
    recent = make_lead(customer=make_customer(), last_order_at=now - timedelta(days=2))
    body = get(client, editor, TEAM, age_preset="NO_ORDER").content.decode()
    assert f"lead/{no_order.id}/" in body and f"lead/{recent.id}/" not in body
    assert re.search(r'<option value="NO_ORDER"\s+selected', body)


@pytest.mark.parametrize("route", [TEAM, STOPPED])
@pytest.mark.parametrize("cursor", ["garbage", "e30", "W10", "WzEsMl0"])
def test_garbage_cursor_returns_the_first_page_200(client, editor, route, cursor):
    status = "STOPPED" if route == STOPPED else "NEW"
    lead = make_lead(customer=make_customer(), lifecycle_status=status)
    response = get(client, editor, route, cursor=cursor)
    assert response.status_code == 200
    assert f"lead/{lead.id}/" in response.content.decode()


def test_nonsense_age_preset_is_a_200(client, editor):
    make_lead(customer=make_customer())
    assert get(client, editor, TEAM, age_preset="foo").status_code == 200


def test_pager_links_on_a_long_queue(client, editor):
    for _ in range(27):
        make_lead(customer=make_customer(), lifecycle_status="STOPPED")
    first = get(client, editor, STOPPED).content.decode()
    assert "หน้าถัดไป" in first and "หน้าแรก" not in first
    nxt = re.search(r'href="([^"]*)">หน้าถัดไป', first).group(1).replace("&amp;", "&")
    second = client.get(reverse(STOPPED) + nxt).content.decode()
    assert "หน้าแรก" in second and first.count("เปิด Lead") == 25 and second.count("เปิด Lead") == 2


@pytest.mark.parametrize("route", [TEAM, STOPPED])
def test_empty_queue_shows_the_state_box(client, editor, route):
    body = get(client, editor, route).content.decode()
    assert '<div class="state-box">ยังไม่มีรายการในคิวนี้</div>' in body
    assert "<table" not in body
