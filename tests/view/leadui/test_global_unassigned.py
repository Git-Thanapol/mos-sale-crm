"""Global Unassigned: pre-Lead intake awaiting a Team (D019).

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_global_unassigned.py
"""

import re

import pytest
from django.urls import reverse

from crm.intake.models import IntakeItem
from crm.leads.models import Lead
from tests.db.factories import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    make_customer,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"
URL = "leadui:global_unassigned"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def get(client, user, **params):
    login(client, user)
    return client.get(reverse(URL), params)


def intake(customer=None, order_id="7000010", ref="fixture:1", **kwargs):
    return IntakeItem.objects.create(
        customer=customer or make_customer(),
        source_order_id=order_id,
        source_kind="LITE_DETAIL_ORDER",
        source_ref=ref,
        **kwargs,
    )


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def teams():
    return make_team("CRM_TEAM"), make_team("UPSELL_TEAM")


def test_title_subtitle_and_the_d019_callout_verbatim(client, editor):
    body = get(client, editor).content.decode()
    assert "<h1>Global Unassigned</h1>" in body
    assert "<p>Customer มีอยู่แล้ว แต่ยังไม่รู้ Team — ยังไม่มี Lead</p>" in body
    assert (
        '<div class="callout">D019: เลือก Team จริงเท่านั้น ระบบจึงสร้าง Lead; '
        "ไม่ใช้ Team ปลอมและไม่สร้าง Lead ที่ teamId ว่าง</div>"
    ) in body


@pytest.mark.parametrize("role", [ROLE_TELESELL, ROLE_VIEWER, ROLE_ADMIN, "TEAM_LEAD"])
def test_only_an_editor_may_open_it(client, role):
    user = make_user(
        email=f"{role}@example.com", role=role, staff_code="X1", team_scope=["CRM_TEAM"]
    )
    assert get(client, user).status_code == 404


def test_anonymous_is_sent_to_login(client):
    assert client.get(reverse(URL)).status_code == 302


def test_a_card_shows_status_customer_order_and_source(client, editor, teams):
    intake(customer=make_customer(customer_name="ลูกค้าใหม่"), order_id="7000010", ref="fixture:1")
    body = get(client, editor).content.decode()
    assert '<article class="work-card">' in body
    # The prototype regex /ASSIGNED/ also matches "UNASSIGNED", so this chip is green there too.
    assert '<span class="chip ok">GLOBAL_UNASSIGNED</span>' in body
    assert "<h3>ลูกค้าใหม่</h3>" in body
    assert "<p>Order: 7000010</p>" in body
    assert "<small>fixture:1</small>" in body


def test_a_missing_order_id_shows_a_dash(client, editor, teams):
    intake(order_id="")
    assert "<p>Order: —</p>" in get(client, editor).content.decode()


def test_one_button_per_active_team_and_no_inactive_ones(client, editor, teams):
    make_team("OLD_TEAM", active=False)
    intake()
    body = get(client, editor).content.decode()
    assert "ส่งเข้า CRM Team" in body and "ส่งเข้า Upsell Team" in body
    assert "OLD_TEAM" not in body
    # The first button is primary and the rest secondary, like the prototype's pair.
    buttons = re.findall(r'<button type="submit"( class="secondary")?>ส่งเข้า ([^<]*)</button>', body)
    assert [b[0] for b in buttons] == ["", ' class="secondary"']


def test_every_action_form_is_a_post_with_csrf_and_a_team_value(client, editor, teams):
    item = intake()
    body = get(client, editor).content.decode()
    action = reverse("leads:resolve_intake", args=[item.id])
    assert body.count(f'action="{action}"') == 2
    assert 'name="csrfmiddlewaretoken"' in body
    assert 'name="team" value="CRM_TEAM"' in body and 'name="team" value="UPSELL_TEAM"' in body


def test_posting_a_team_creates_a_real_lead_and_returns_with_a_toast(client, editor, teams):
    item = intake(customer=make_customer(customer_name="สมชาย"))
    login(client, editor)
    response = client.post(
        reverse("leads:resolve_intake", args=[item.id]),
        {"team": "UPSELL_TEAM", "next": reverse(URL)},
        follow=True,
    )
    body = response.content.decode()
    assert '<div class="toast">มอบหมายทีมแล้ว</div>' in body
    lead = Lead.objects.get(customer=item.customer)
    assert lead.team.code == "UPSELL_TEAM"
    item.refresh_from_db()
    assert item.status == "CONVERTED" and item.created_lead_id == lead.id
    assert "สมชาย" not in body  # the card is gone from the queue


def test_no_lead_is_created_for_an_unknown_team(client, editor, teams):
    item = intake()
    login(client, editor)
    response = client.post(reverse("leads:resolve_intake", args=[item.id]), {"team": "NOPE"})
    assert response.status_code == 400
    assert not Lead.objects.exists()
    assert Lead.objects.filter(team__isnull=True).count() == 0


@pytest.mark.parametrize("cursor", ["garbage", "e30", "W10", "WzEsMl0"])
def test_garbage_cursor_returns_the_first_page_200(client, editor, teams, cursor):
    item = intake(customer=make_customer(customer_name="แรก"))
    response = get(client, editor, cursor=cursor)
    assert response.status_code == 200
    assert "แรก" in response.content.decode() and item.id


def test_pager_on_a_long_queue(client, editor, teams):
    for i in range(27):
        intake(order_id=f"SO-{i:03d}")
    first = get(client, editor).content.decode()
    assert first.count('<article class="work-card">') == 25
    assert "หน้าถัดไป" in first and "หน้าแรก" not in first
    nxt = re.search(r'href="([^"]*)">หน้าถัดไป', first).group(1).replace("&amp;", "&")
    second = client.get(reverse(URL) + nxt).content.decode()
    assert second.count('<article class="work-card">') == 2 and "หน้าแรก" in second


def test_empty_queue_shows_the_state_box(client, editor, teams):
    body = get(client, editor).content.decode()
    assert '<div class="state-box">ยังไม่มีรายการในคิวนี้</div>' in body
    assert "work-card" not in body


def test_converted_intake_is_not_listed(client, editor, teams):
    intake(status="CONVERTED")
    assert "work-card" not in get(client, editor).content.decode()
