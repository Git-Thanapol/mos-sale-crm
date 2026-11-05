"""งานของฉัน: toolbar, table, filters and paging, as in the prototype's MyWork.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_my_work.py
"""

import re
from datetime import timedelta

import pytest

from crm.leadui.labels_th import th_label
from django.urls import reverse
from django.utils import timezone

from crm.domain.last_order_age import REALLOCATION_REVIEW_DEFAULT_DAYS
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
URL = "leadui:my_work"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def get(client, user, **params):
    login(client, user)
    return client.get(reverse(URL), params)


def table_html(body):
    start = body.index("<table")
    return body[start : body.index("</table>") + len("</table>")]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def telesale():
    return make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001", staff_name="สมศรี")


def test_titles_and_subtitles_verbatim(client, editor):
    body = get(client, editor).content.decode()
    assert "<h1>งานของฉัน</h1>" in body
    assert "<p>เรียงงานเร่งด่วน: เกินกำหนด → นัดวันนี้ → ติดตาม → Lead ใหม่</p>" in body


CONTROLS = [
    "lead_keyword",
    "missed_channel",
    "age_preset",
    "age_min_days",
    "age_max_days",
    "age_sort",
    "appointment_protection",
    "followup_protection",
    "exclude_stopped",
    "reallocation_review",
]


def test_page_has_every_toolbar_control_in_prototype_order(client, editor):
    body = get(client, editor).content.decode()
    positions = [body.index(f'name="{name}"') for name in CONTROLS]
    assert positions == sorted(positions)
    assert '<form class="toolbar" method="get" data-v2-filters>' in body
    assert 'placeholder="เบอร์โทร / ชื่อ / ชื่อมาตรฐาน / Order ID"' in body
    for text in (
        "ทุกกลุ่มออเดอร์ตกหล่น",
        "ทุกช่วงออเดอร์ล่าสุด",
        "ขั้นต่ำ (วัน)",
        "สูงสุด (วัน)",
        "ลำดับงานปกติ",
        "ออเดอร์เก่าสุดก่อน",
        "ออเดอร์ใหม่สุดก่อน",
        "ทุกสถานะนัดหมาย",
        "มีนัดหมายทำงานอยู่",
        "ไม่มีนัดหมายทำงานอยู่",
        "ทุกรอบติดตาม",
        "มีรอบติดตาม",
        "ไม่มีรอบติดตาม",
        "ไม่รวม STOPPED",
        "หลีดที่ควรตรวจสอบการจัดสรรใหม่",
    ):
        assert text in body, text


def test_reallocation_checkbox_is_hidden_for_a_telesale(client, telesale):
    body = get(client, telesale).content.decode()
    assert 'name="reallocation_review"' not in body
    assert 'name="exclude_stopped"' in body


def test_reallocation_param_is_ignored_for_a_telesale(client, telesale):
    # Hiding the checkbox is not the control: a hand-built querystring must not
    # let a telesale run a review-queue filter.
    old = timezone.now() - timedelta(days=400)
    reviewable = make_lead(
        customer=make_customer(staff_code="S0001"), caretaker=telesale, last_order_at=old
    )
    recent = make_lead(
        customer=make_customer(staff_code="S0001"),
        caretaker=telesale,
        last_order_at=timezone.now(),
    )
    body = get(client, telesale, reallocation_review="on").content.decode()
    assert f"lead/{reviewable.id}/" in body and f"lead/{recent.id}/" in body


def test_hint_uses_the_configured_threshold(client, editor):
    body = get(client, editor).content.decode()
    assert (
        f'<span class="hint">เกณฑ์ตรวจสอบ: {REALLOCATION_REVIEW_DEFAULT_DAYS} วัน · '
        "เป็นคิวตรวจสอบเท่านั้น</span>"
    ) in body


def test_default_sort_is_work_priority_and_selected(client, editor):
    body = get(client, editor).content.decode()
    assert re.search(r'<option value="WORK_PRIORITY"\s+selected', body)


def test_table_headers_match_the_prototype(client, editor):
    make_lead(customer=make_customer(), caretaker=editor)
    html = table_html(get(client, editor).content.decode())
    headers = re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", html)
    assert headers == [
        "ลูกค้า",
        "Team / ผู้ดูแล",
        "สถานะ",
        "นัดหมาย",
        "ระยะห่างจากออเดอร์ล่าสุด",
        "ออเดอร์ตกหล่น",
        "",
    ]
    assert '<div class="table-shell">' in get(client, editor).content.decode()


def test_row_cells_follow_the_prototype(client, editor):
    caretaker = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="พนักงาน หนึ่ง")
    customer = make_customer(customer_name="ลูกค้าทดสอบ 003", phone1="0810000012")
    now = timezone.now()
    lead = make_lead(
        customer=customer,
        caretaker=caretaker,
        lifecycle_status="FOLLOWING",
        next_follow_at=now - timedelta(days=3),
        last_order_at=now - timedelta(days=22),
        missed_channel_count=2,
    )
    body = get(client, editor).content.decode()
    html = table_html(body)
    assert f'href="{reverse("leadui:customer_detail", args=[customer.id])}"' in html
    assert "ลูกค้าทดสอบ 003" in html and "<small>0810000012</small>" in html
    assert '<span class="team-badge">CRM Team</span>' in html
    assert "<small>พนักงาน หนึ่ง</small>" in html
    assert '<span class="chip neutral">กำลังติดตาม</span>' in html
    assert '<span class="chip danger">เลยกำหนด</span>' in html
    assert "ออเดอร์ล่าสุด: " in html and "<small>22 วันที่แล้ว</small>" in html
    assert '<span class="metric">2</span><small>กลุ่ม 2</small>' in html
    assert f'href="{reverse("leadui:lead_detail", args=[lead.id])}"' in html
    assert "เปิด Lead" in html


def test_unassigned_lead_and_no_order_text(client, editor):
    make_lead(customer=make_customer(), last_order_at=None)
    html = table_html(get(client, editor).content.decode())
    assert "<small>ยังไม่มอบหมาย</small>" in html
    assert "ยังไม่มีออเดอร์" in html


def test_protection_chips_show_cycle_appointment_and_lock(client, editor):
    from crm.followcycles.services import open_cycle

    lead = make_lead(
        customer=make_customer(),
        caretaker=editor,
        next_follow_at=timezone.now() + timedelta(days=2),
    )
    cycle = open_cycle(actor=editor, lead=lead)
    cycle.lock_active = True
    cycle.save(update_fields=["lock_active"])
    html = table_html(get(client, editor).content.decode())
    for code in ("ACTIVE_FOLLOWUP", "ACTIVE_APPOINTMENT", "OWNERSHIP_LOCK", "APPOINTMENT_FUTURE"):
        assert f">{th_label(code)}</span>" in html, code


def test_blank_name_and_phone_render_no_phone_text_not_None(client, editor):
    # Review Focus 4.
    lead = make_lead(
        customer=make_customer(customer_name="", phone1="", phone2=""), caretaker=editor
    )
    html = table_html(get(client, editor).content.decode())
    assert "<small>ไม่มีเบอร์</small>" in html
    assert "None" not in html
    assert f'href="{reverse("leadui:lead_detail", args=[lead.id])}"' in html


@pytest.mark.parametrize("cursor", ["garbage", "e30", "W10", "WzEsMl0", "%%%"])
def test_garbage_cursor_returns_the_first_page_200(client, editor, cursor):
    # Review Focus 1.
    lead = make_lead(customer=make_customer(), caretaker=editor)
    response = get(client, editor, cursor=cursor)
    assert response.status_code == 200
    assert f"lead/{lead.id}/" in response.content.decode()


@pytest.mark.parametrize(
    "params",
    [
        {"age_min_days": "10", "age_max_days": "5"},
        {"age_min_days": "-1"},
        {"age_preset": "foo"},
        {"age_sort": "zzz"},
        {"age_min_days": "999999999999999999999"},
        {"age_preset": "NO_ORDER", "age_min_days": "10", "age_max_days": "20"},
        {"missed_channel": "9"},
        {"appointment_protection": "BOGUS"},
    ],
)
def test_nonsense_filters_return_200(client, editor, params):
    # Review Focus 5.
    make_lead(customer=make_customer(), caretaker=editor)
    assert get(client, editor, **params).status_code == 200


def test_the_form_echoes_the_raw_values_the_user_typed(client, editor):
    body = get(
        client, editor, lead_keyword="สมชาย", age_min_days="10", age_max_days="5"
    ).content.decode()
    assert 'name="age_min_days"' in body
    assert re.search(r'name="age_min_days"[^>]*value="10"', body)
    assert re.search(r'name="age_max_days"[^>]*value="5"', body)
    assert re.search(r'name="lead_keyword"[^>]*value="สมชาย"', body)


def test_keyword_and_exclude_stopped_filters_apply(client, editor):
    keep = make_lead(customer=make_customer(customer_name="สมชาย ใจดี"), caretaker=editor)
    make_lead(customer=make_customer(customer_name="สมหญิง"), caretaker=editor)
    stopped = make_lead(
        customer=make_customer(customer_name="สมชาย หยุด"),
        caretaker=editor,
        lifecycle_status="STOPPED",
    )
    html = table_html(get(client, editor, lead_keyword="สมชาย", exclude_stopped="1").content.decode())
    assert f"lead/{keep.id}/" in html and f"lead/{stopped.id}/" not in html
    assert "สมหญิง" not in html


def test_sort_option_orders_the_rows(client, editor):
    now = timezone.now()
    old = make_lead(customer=make_customer(), caretaker=editor, last_order_at=now - timedelta(days=50))
    new = make_lead(customer=make_customer(), caretaker=editor, last_order_at=now - timedelta(days=2))
    body = get(client, editor, age_sort="NEWEST_LAST_ORDER").content.decode()
    assert body.index(f"lead/{new.id}/") < body.index(f"lead/{old.id}/")
    body = get(client, editor, age_sort="OLDEST_LAST_ORDER").content.decode()
    assert body.index(f"lead/{old.id}/") < body.index(f"lead/{new.id}/")


def test_first_and_next_pager_links(client, editor):
    for _ in range(27):
        make_lead(customer=make_customer(), caretaker=editor)
    first = get(client, editor, lead_keyword="").content.decode()
    assert "หน้าถัดไป" in first and "หน้าแรก" not in first
    nxt = re.search(r'href="([^"]*)">หน้าถัดไป', first).group(1).replace("&amp;", "&")
    assert "cursor=" in nxt
    second = client.get(reverse(URL) + nxt).content.decode()
    assert "หน้าแรก" in second and "หน้าถัดไป" not in second
    first_url = re.search(r'href="([^"]*)">หน้าแรก', second).group(1)
    assert "cursor=" not in first_url
    # 25 rows on page one, the remaining 2 on page two, none repeated.
    assert first.count("เปิด Lead") == 25 and second.count("เปิด Lead") == 2


def test_pager_links_keep_the_active_filters(client, editor):
    for i in range(27):
        make_lead(customer=make_customer(customer_name=f"สมชาย {i}"), caretaker=editor)
    body = get(client, editor, lead_keyword="สมชาย", age_sort="NEWEST_LAST_ORDER").content.decode()
    href = re.search(r'href="([^"]*)">หน้าถัดไป', body).group(1)
    assert "age_sort=NEWEST_LAST_ORDER" in href and "lead_keyword=" in href


def test_a_telesale_sees_only_their_own_leads_even_with_a_caretaker_param(client, telesale):
    other = make_user(email="o@example.com", role=ROLE_TELESELL, staff_code="S0002")
    mine = make_lead(customer=make_customer(staff_code="S0001"), caretaker=telesale)
    theirs = make_lead(customer=make_customer(staff_code="S0002"), caretaker=other)
    body = get(client, telesale, caretaker=str(other.pk)).content.decode()
    assert f"lead/{mine.id}/" in body and f"lead/{theirs.id}/" not in body


def test_an_empty_queue_shows_the_state_box(client, editor):
    body = get(client, editor).content.decode()
    assert '<div class="state-box">ยังไม่มีรายการในคิวนี้</div>' in body
    assert "<table" not in body


@pytest.mark.parametrize("role", [ROLE_ADMIN, ROLE_VIEWER])
def test_roles_outside_the_lead_workspace_get_404(client, role):
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X1")
    assert get(client, user).status_code == 404
