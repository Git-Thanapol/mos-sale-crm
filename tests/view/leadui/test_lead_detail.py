"""Lead detail page and its actions, as in the prototype's LeadDetail.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_lead_detail.py
"""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import Appointment, ContactAttempt, FollowupCycle, Note
from crm.followcycles.services import add_note, log_contact_attempt, open_cycle, set_appointment
from crm.leads.models import Lead
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    make_customer,
    make_lead,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def url(lead):
    return reverse("leadui:lead_detail", args=[lead.id])


def page(client, user, lead):
    login(client, user)
    return client.get(url(lead))


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001", staff_name="แอดมิน")


@pytest.fixture
def telesale():
    return make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001", staff_name="สมศรี")


@pytest.fixture
def lead(telesale):
    return make_lead(
        customer=make_customer(customer_name="ลูกค้าทดสอบ 003", staff_code="S0001"),
        caretaker=telesale,
        lifecycle_status="FOLLOWING",
        missed_channel_count=1,
    )


def test_titles_headings_and_dl_labels_verbatim(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    assert "<h1>รายละเอียด Lead</h1>" in body
    assert "<p>Notes / Contact Attempts / Appointments เขียนผ่าน server mutation</p>" in body
    for heading in ("เพิ่มโน้ต", "การติดต่อล่าสุด", "นัดหมาย"):
        assert f"<h2>{heading}</h2>" in body, heading
    assert "<h3>โน้ตล่าสุด</h3>" in body
    for label in ("เริ่มถือ Lead", "รอบติดตาม", "นัดหมายถัดไป", "ออเดอร์ตกหล่น"):
        assert f"<dt>{label}</dt>" in body, label
    assert "<dd>1 (กลุ่ม 1)</dd>" in body
    assert body.count('class="detail-card"') == 4
    assert '<div class="detail-layout">' in body


def test_heading_block_shows_name_team_caretaker_and_lifecycle_chip(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    assert '<div class="lead-heading">' in body
    assert "<h2>ลูกค้าทดสอบ 003</h2>" in body
    assert "<p>CRM Team · สมศรี</p>" in body
    assert '<span class="chip neutral">FOLLOWING</span>' in body


def test_an_unassigned_lead_says_so(client, editor):
    unassigned = make_lead(customer=make_customer(customer_name="ไม่มีเจ้าของ"))
    body = page(client, editor, unassigned).content.decode()
    assert "<p>CRM Team · ยังไม่มอบหมาย</p>" in body


def test_a_telesale_caretaker_sees_work_buttons_but_not_reassign_or_stop(client, telesale, lead):
    body = page(client, telesale, lead).content.decode()
    assert "บันทึกติดต่อไม่ได้" in body and "นัดพรุ่งนี้" in body
    assert 'name="body"' in body
    assert "เปลี่ยน caretaker" not in body
    assert ">STOP<" not in body and "Reactivate" not in body


def test_an_editor_sees_every_button_when_a_cycle_is_active(client, editor, lead):
    open_cycle(actor=editor, lead=lead)
    body = page(client, editor, lead).content.decode()
    for text in ("บันทึกติดต่อไม่ได้", "นัดพรุ่งนี้", "เปลี่ยน caretaker", "ปิดรอบติดตาม"):
        assert text in body, text
    assert '<button type="submit" class="danger-button">STOP</button>' in body
    assert "Reactivate" not in body


def test_no_active_cycle_hides_the_close_button(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    assert "ปิดรอบติดตาม" not in body


def test_a_stopped_lead_offers_reactivate_not_stop(client, editor, lead):
    lead.lifecycle_status = "STOPPED"
    lead.stopped_reason = "MANUAL_STOP"
    lead.save()
    body = page(client, editor, lead).content.decode()
    assert "Reactivate" in body and ">STOP<" not in body
    assert reverse("leads:reactivate", args=[lead.id]) in body


def test_a_viewer_in_the_lead_gets_no_page(client, lead):
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="V1")
    assert page(client, viewer, lead).status_code == 404


def test_an_out_of_scope_lead_is_404_and_leaks_no_name(client):
    # Review Focus 2.
    mine = make_user(email="mine@example.com", role=ROLE_TELESELL, staff_code="S0002")
    secret = make_lead(
        customer=make_customer(customer_name="ชื่อลับสุดยอด", staff_code="S0099"),
        caretaker=make_user(email="other@example.com", role=ROLE_TELESELL, staff_code="S0099"),
    )
    response = page(client, mine, secret)
    assert response.status_code == 404
    assert "ชื่อลับสุดยอด" not in response.content.decode()


def test_a_team_lead_sees_in_scope_leads_only(client):
    team_lead = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"])
    inside = make_lead(customer=make_customer(), team_code="TEAM_A")
    outside = make_lead(customer=make_customer(customer_name="ทีมอื่น"), team_code="TEAM_B")
    assert page(client, team_lead, inside).status_code == 200
    assert page(client, team_lead, inside).content.decode().count('data-v2-open="reassign-dialog"') == 1
    response = page(client, team_lead, outside)
    assert response.status_code == 404 and "ทีมอื่น" not in response.content.decode()


def test_anonymous_is_sent_to_login(client, lead):
    assert client.get(url(lead)).status_code == 302


def test_a_lead_with_no_history_renders_without_none(client, editor):
    bare = make_lead(customer=make_customer(customer_name="เปล่า"))
    body = page(client, editor, bare).content.decode()
    assert "None" not in body
    assert "<dd>—</dd>" in body  # no cycle, no appointment


def test_notes_attempts_and_appointments_are_listed(client, editor, lead):
    add_note(actor=editor, lead=lead, body="โน้ตทดสอบหนึ่ง")
    log_contact_attempt(actor=editor, lead=lead, result="NO_ANSWER", note="ไม่รับสาย (ข้อมูลทดสอบ)")
    set_appointment(actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=2))
    body = page(client, editor, lead).content.decode()
    assert '<div class="note">โน้ตทดสอบหนึ่ง<small>' in body
    assert '<div class="history-row"><span class="chip neutral">NO_ANSWER</span>' in body
    assert "ไม่รับสาย (ข้อมูลทดสอบ)" in body
    assert '<span class="chip neutral">SCHEDULED</span>' in body


def test_the_note_form_posts_back_to_the_detail_with_a_toast(client, editor, lead):
    login(client, editor)
    response = client.post(
        reverse("leads:add_note", args=[lead.id]), {"body": "คุยแล้ว", "next": url(lead)}, follow=True
    )
    body = response.content.decode()
    assert response.redirect_chain[-1][0] == url(lead)
    assert '<div class="toast">บันทึกโน้ตแล้ว</div>' in body
    assert "คุยแล้ว" in body
    assert Note.objects.filter(lead=lead, body="คุยแล้ว").exists()


def test_the_attempt_button_posts_no_answer_with_the_prototype_note(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    form = re.search(
        r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % re.escape(reverse("leads:log_attempt", args=[lead.id])),
        body,
        re.S,
    ).group(1)
    assert 'name="result" value="NO_ANSWER"' in form
    assert 'name="note" value="บันทึกจากหน้าจอ"' in form
    assert f'name="next" value="{url(lead)}"' in form
    client.post(
        reverse("leads:log_attempt", args=[lead.id]),
        {"result": "NO_ANSWER", "note": "บันทึกจากหน้าจอ", "next": url(lead)},
    )
    assert ContactAttempt.objects.filter(lead=lead, result="NO_ANSWER").exists()


def test_the_tomorrow_button_creates_an_appointment_about_24_hours_ahead(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    form = re.search(
        r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % re.escape(reverse("leads:set_appointment", args=[lead.id])),
        body,
        re.S,
    ).group(1)
    value = re.search(r'name="scheduled_at" value="([^"]+)"', form).group(1)
    client.post(reverse("leads:set_appointment", args=[lead.id]), {"scheduled_at": value, "next": url(lead)})
    appointment = Appointment.objects.get(lead=lead)
    delta = appointment.scheduled_at - timezone.now()
    assert timedelta(hours=23, minutes=50) < delta < timedelta(hours=24, minutes=10)


def test_the_reassign_dialog_lists_active_users_only(client, editor, lead):
    make_user(email="active@example.com", role=ROLE_TELESELL, staff_code="S5", staff_name="คนใช้งานอยู่")
    make_user(
        email="gone@example.com", role=ROLE_TELESELL, staff_code="S6", staff_name="คนลาออก", is_active=False
    )
    body = page(client, editor, lead).content.decode()
    assert "<dialog" in body and reverse("leads:reassign", args=[lead.id]) in body
    assert "คนใช้งานอยู่" in body and "คนลาออก" not in body


def test_stop_button_posts_manual_stop(client, editor, lead):
    body = page(client, editor, lead).content.decode()
    form = re.search(
        r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % re.escape(reverse("leads:stop", args=[lead.id])),
        body,
        re.S,
    ).group(1)
    assert 'name="reason" value="MANUAL_STOP"' in form


def test_close_cycle_ends_the_cycle_releases_the_lock_and_returns_with_a_toast(client, editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    cycle.lock_active = True
    cycle.save(update_fields=["lock_active"])
    login(client, editor)
    response = client.post(
        reverse("leads:close_cycle", args=[lead.id]), {"next": url(lead)}, follow=True
    )
    assert '<div class="toast">' in response.content.decode()
    cycle.refresh_from_db()
    lead.refresh_from_db()
    assert cycle.status == "ENDED" and cycle.ended_reason == "MANUAL_CLOSE"
    assert cycle.lock_active is False and lead.current_cycle_id is None


def test_close_cycle_is_403_for_a_user_who_cannot_edit(client, lead):
    open_cycle(actor=make_user(email="x@example.com", role=ROLE_EDITOR), lead=lead)
    viewer = make_user(email="v2@example.com", role=ROLE_VIEWER, staff_code="V2")
    login(client, viewer)
    assert client.post(reverse("leads:close_cycle", args=[lead.id])).status_code == 403
    assert FollowupCycle.objects.get(lead=lead).status == "ACTIVE"


def test_close_cycle_is_400_without_an_active_cycle(client, editor, lead):
    login(client, editor)
    assert client.post(reverse("leads:close_cycle", args=[lead.id])).status_code == 400


def test_close_cycle_requires_post(client, editor, lead):
    login(client, editor)
    assert client.get(reverse("leads:close_cycle", args=[lead.id])).status_code == 405


def test_close_cycle_is_404_for_an_unknown_lead(client, editor):
    login(client, editor)
    assert client.post(reverse("leads:close_cycle", args=[999999])).status_code == 404


def test_a_second_team_does_not_change_the_heading(client, editor):
    other_team = make_team("UPSELL_TEAM")
    other = make_lead(customer=make_customer(customer_name="อัปเซล"), team=other_team)
    assert "<p>Upsell Team · ยังไม่มอบหมาย</p>" in page(client, editor, other).content.decode()
    assert Lead.objects.count() == 1
