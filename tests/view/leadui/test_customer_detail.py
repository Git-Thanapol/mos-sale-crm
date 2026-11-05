"""Customer detail page and the lazy, audited order history.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_customer_detail.py
"""

import re

from crm.leadui.labels_th import th_label
from datetime import date, timedelta

import pytest
from django.urls import reverse

from crm.leads.models import LeadAuditEvent
from crm.orders.models import Order
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    T0,
    make_customer,
    make_lead,
    make_order,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def detail_url(customer):
    return reverse("leadui:customer_detail", args=[customer.id])


def history_url(customer, **params):
    base = reverse("leadui:customer_history", args=[customer.id])
    if params:
        from urllib.parse import urlencode

        return f"{base}?{urlencode(params)}"
    return base


def audits(customer):
    return LeadAuditEvent.objects.filter(action="CUSTOMER_HISTORY_VIEW", customer=customer)


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def customer():
    return make_customer(customer_name="ลูกค้าทดสอบ 001", phone1="0810000010", staff_code="S0001")


def orders(customer, count, amount="714.00", start=T0):
    made = []
    for i in range(count):
        made.append(
            make_order(
                customer=customer,
                ordered_at=start + timedelta(days=i),
                order_no=f"SO-{customer.id}-{i:03d}",
                total_amount=amount,
            )
        )
    return made


def test_titles_and_summary_card(client, editor, customer):
    make_lead(customer=customer)
    orders(customer, 2)
    login(client, editor)
    body = client.get(detail_url(customer)).content.decode()
    assert "<h1>ข้อมูลลูกค้า</h1>" in body
    assert "<p>สรุปก่อน โหลดประวัติออเดอร์เมื่อผู้ใช้กดเท่านั้น</p>" in body
    assert "<h2>ลูกค้าทดสอบ 001</h2>" in body
    assert "<p>0810000010</p>" in body
    assert f"<dt>ออเดอร์ล่าสุด</dt><dd>SO-{customer.id}-001</dd>" in body
    assert "<dt>จำนวนออเดอร์</dt><dd>2</dd>" in body
    assert "<dt>ยอดซื้อรวม</dt><dd>฿1,428</dd>" in body
    assert "ดูประวัติเก่า" in body
    assert body.count('class="detail-card"') == 2 and '<div class="detail-layout">' in body
    assert "<h2>Leads ตาม Team</h2>" in body


def test_first_load_has_no_order_history_and_no_audit_event(client, editor, customer):
    orders(customer, 3)
    login(client, editor)
    body = client.get(detail_url(customer)).content.decode()
    assert 'class="history-row"' not in body and "<h2>ประวัติออเดอร์</h2>" not in body
    assert audits(customer).count() == 0
    assert f'data-v2-history="{history_url(customer)}"' in body


def test_a_customer_with_no_orders_or_phone_renders_cleanly(client, editor):
    bare = make_customer(customer_name="", phone1="", phone2="")
    login(client, editor)
    body = client.get(detail_url(bare)).content.decode()
    assert "<p>ไม่มีเบอร์หลัก</p>" in body
    assert "<dt>ออเดอร์ล่าสุด</dt><dd>—</dd>" in body
    assert "<dt>จำนวนออเดอร์</dt><dd>0</dd>" in body
    assert "<dt>ยอดซื้อรวม</dt><dd>฿0</dd>" in body
    assert "None" not in body


def test_a_customer_with_no_leads_renders_an_empty_leads_card(client, editor, customer):
    # Review Focus 3.
    login(client, editor)
    response = client.get(detail_url(customer))
    assert response.status_code == 200
    assert "list-button" not in response.content.decode()


def test_lead_links_are_one_per_visible_lead(client, editor, customer):
    first = make_lead(customer=customer, team_code="CRM_TEAM", lifecycle_status="FOLLOWING")
    second = make_lead(customer=customer, team_code="UPSELL_TEAM")
    login(client, editor)
    body = client.get(detail_url(customer)).content.decode()
    for lead, team, chip in ((first, "CRM Team", "FOLLOWING"), (second, "Upsell Team", "NEW")):
        assert re.search(
            r'<a class="list-button v2-link-row" href="%s"><span>%s</span><span class="chip neutral">%s</span></a>'
            % (re.escape(reverse("leadui:lead_detail", args=[lead.id])), team, th_label(chip)),
            body,
        ), team


def test_a_team_lead_only_gets_links_for_leads_in_their_scope(client, customer):
    team_lead = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"])
    mine = make_lead(customer=customer, team_code="TEAM_A")
    other = make_lead(customer=customer, team_code="TEAM_B")
    login(client, team_lead)
    body = client.get(detail_url(customer)).content.decode()
    assert f"lead/{mine.id}/" in body and f"lead/{other.id}/" not in body


def test_total_spend_ignores_unmapped_status_but_the_count_matches_the_lead_rollup(client, editor, customer):
    # The repo has no cancelled/COD-returned vocabulary (crm/leads/order_status.py):
    # only a mapped status counts toward money, an unknown one is never guessed.
    make_order(customer=customer, order_no="OK-1", total_amount="1000")
    make_order(customer=customer, order_no="OK-2", total_amount="500", order_status="")
    make_order(customer=customer, order_no="ODD", total_amount="9999", order_status="ยกเลิก")
    login(client, editor)
    body = client.get(detail_url(customer)).content.decode()
    assert "<dt>จำนวนออเดอร์</dt><dd>3</dd>" in body
    assert "<dt>ยอดซื้อรวม</dt><dd>฿1,500</dd>" in body


def test_an_out_of_scope_customer_is_404_and_leaks_no_name(client):
    # Review Focus 2.
    mine = make_user(email="mine@example.com", role=ROLE_TELESELL, staff_code="S0002")
    secret = make_customer(customer_name="ชื่อลับสุดยอด", staff_code="S0099")
    make_lead(customer=secret, caretaker=make_user(email="o@example.com", role=ROLE_TELESELL, staff_code="S0099"))
    login(client, mine)
    response = client.get(detail_url(secret))
    assert response.status_code == 404 and "ชื่อลับสุดยอด" not in response.content.decode()
    history = client.get(history_url(secret))
    assert history.status_code == 404
    assert audits(secret).count() == 0


def test_a_caretaker_may_open_a_customer_that_is_not_staff_coded_to_them(client):
    carer = make_user(email="carer@example.com", role=ROLE_TELESELL, staff_code="S0003")
    customer = make_customer(customer_name="ผ่านลีด", staff_code="S0777")
    make_lead(customer=customer, caretaker=carer)
    login(client, carer)
    assert client.get(detail_url(customer)).status_code == 200


def test_a_viewer_gets_404(client, customer):
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="V1")
    login(client, viewer)
    assert client.get(detail_url(customer)).status_code == 404
    assert client.get(history_url(customer)).status_code == 404


def test_anonymous_is_sent_to_login(client, customer):
    assert client.get(detail_url(customer)).status_code == 302
    assert client.get(history_url(customer)).status_code == 302


def test_history_returns_a_section_of_rows_and_audits_once(client, editor, customer):
    orders(customer, 3)
    login(client, editor)
    response = client.get(history_url(customer))
    assert response.status_code == 200
    body = response.content.decode()
    assert '<section class="detail-card history">' in body and "<h2>ประวัติออเดอร์</h2>" in body
    assert body.count('class="history-row"') == 3
    assert f"<strong>SO-{customer.id}-002</strong>" in body  # newest first
    assert body.index(f"SO-{customer.id}-002") < body.index(f"SO-{customer.id}-000")
    assert "฿714" in body and "<span>manual</span>" in body
    event = audits(customer).get()
    assert event.actor_id == editor.id and event.payload["kind"] == "ORDER_HISTORY_VIEW"


def test_history_pages_by_keyset_and_audits_every_request(client, editor, customer):
    orders(customer, 30)
    login(client, editor)
    first = client.get(history_url(customer)).content.decode()
    assert first.count('class="history-row"') == 25
    more = re.search(r'data-v2-history="([^"]+)"[^>]*>โหลดเพิ่ม', first).group(1).replace("&amp;", "&")
    assert "cursor=" in more
    second = client.get(more).content.decode()
    assert second.count('class="history-row"') == 5
    assert "โหลดเพิ่ม" not in second and "<section" not in second
    assert audits(customer).count() == 2  # every page is audited
    seen = re.findall(r"<strong>(SO-[^<]+)</strong>", first + second)
    assert len(seen) == 30 and len(set(seen)) == 30


@pytest.mark.parametrize("cursor", ["garbage", "e30", "W10", "WzEsMl0"])
def test_history_garbage_cursor_returns_the_first_page(client, editor, customer, cursor):
    # Review Focus 1.
    orders(customer, 2)
    login(client, editor)
    response = client.get(history_url(customer, cursor=cursor))
    assert response.status_code == 200
    assert response.content.decode().count('class="history-row"') == 2


def test_history_is_get_only(client, editor, customer):
    login(client, editor)
    assert client.post(history_url(customer)).status_code == 405


def test_history_for_a_customer_without_orders_is_an_empty_state(client, editor, customer):
    login(client, editor)
    body = client.get(history_url(customer)).content.decode()
    assert "history-row" not in body and "ยังไม่มีรายการ" in body


def test_a_null_total_shows_a_dash_after_the_baht_sign(client, editor, customer):
    Order.objects.create(customer=customer, order_no="NOAMT", order_date=date(2026, 10, 1), total_amount=None)
    login(client, editor)
    assert "฿—" in client.get(history_url(customer)).content.decode()
