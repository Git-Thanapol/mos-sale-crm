"""The Migration Decisions screen: Admin only, masked evidence, stale-safe forms.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_migration_decisions_page.py
"""

import re
from datetime import datetime

import pytest
from django.urls import reverse

from crm.domain.bangkok import BANGKOK_TZ
from crm.domain.migration_decisions import decision_id_for, evidence_hash_of
from crm.importv2.models import MigrationDecision
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_user

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"
URL = "leadui:migration_decisions"

STAFF = {
    "kind": "STAFF_ALIAS", "historicalDisplayValue": "พนักงานเดิม 5", "normalizedValue": "พนักงานเดิม 5", "frequency": 8613,
    "sourceContext": {"crm": 150, "upsell": 50}, "exactCandidates": [],
}
IDENTITY = {
    "kind": "IDENTITY_REVIEW", "normalizedPhone": "0812345678",
    "nameVariants": [{"name": "สมชาย", "normalizedName": "สมชาย", "orderCount": 3, "firstOrderAt": 1_728_000_000_000, "lastOrderAt": 1_729_000_000_000}],
    "nameVariantTotal": 1, "similarities": [{"a": 0, "b": 1, "score": 0.873}], "sourceOrderCount": 3, "sampleOrderIds": ["SO-1"],
    "candidates": [{"customerId": "c1", "displayName": "ก", "similarity": 0.9}],
}
STATUS = {
    "kind": "UNKNOWN_STATUS", "rawValue": "แปลก", "count": 3, "sourceKind": "HX63_HISTORICAL", "sampleOrderIds": ["SO-1"],
    "fallback": {"orderStatus": "PENDING_CONFIRMATION", "paymentStatus": "UNPAID", "shippingStatus": "NOT_SHIPPED"},
}
NULL_DATE = {"kind": "NULL_DATE_ORDER", "sourceOrderId": "SO-9", "sourceKind": "HX63_HISTORICAL", "hasPhone": True, "itemCount": 2, "total": 100, "rawStatus": "สำเร็จ"}


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


def make(category="STAFF_ALIAS", reason="NO_EXACT_CANDIDATE", evidence=STAFF, subject="s1", status="OPEN"):
    return MigrationDecision.objects.create(
        decision_id=decision_id_for(category, "snap", subject), category=category, status=status, reason_code=reason,
        subject_key=subject, source_snapshot_id="snap", evidence=evidence, evidence_hash=evidence_hash_of(category, subject, evidence),
    )


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1")


def get(client, user, **params):
    login(client, user)
    return client.get(reverse(URL), params).content.decode()


def post_resolve(client, d, **fields):
    data = {"revision": d.revision, "evidence_hash": d.evidence_hash, "reason": "ตรวจสอบแล้ว", **fields}
    return client.post(reverse("leadui:decision_resolve", args=[d.decision_id]), data, follow=True)


def test_title_subtitle_and_tabs_with_counts(client, editor):
    make(subject="a")
    make("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", STATUS, subject="b")
    body = get(client, editor)
    assert "<h1>Migration Decisions</h1>" in body
    assert "<p>ตัดสินโดย Admin เท่านั้น · ทุกการตัดสินมีเหตุผลและประวัติ · ไม่มีการจับคู่อัตโนมัติ</p>" in body
    assert '<section class="decision-center">' in body
    tabs = re.findall(r'<button type="submit" name="category" value="(\w+)"( class="active")?>([^<]+)<span class="count">([^<]+)</span>', body)
    assert [(t[0], t[2], t[3]) for t in tabs] == [
        ("STAFF_ALIAS", "ชื่อพนักงานเดิม", "1"),
        ("IDENTITY_REVIEW", "ตรวจตัวตนลูกค้า", "0"),
        ("UNKNOWN_STATUS", "สถานะที่ไม่รู้จัก", "1"),
        ("NULL_DATE_ORDER", "ออเดอร์ไม่มีวันที่", "0"),
    ]
    assert [t[0] for t in tabs if t[1]] == ["STAFF_ALIAS"]


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_only_an_editor_may_open_or_post(client, role):
    d = make()
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["A"])
    login(client, user)
    assert client.get(reverse(URL)).status_code == 404
    assert client.post(reverse("leadui:decision_resolve", args=[d.decision_id]), {}).status_code == 404
    assert client.post(reverse("leadui:decision_reopen", args=[d.decision_id]), {}).status_code == 404
    d.refresh_from_db()
    assert d.status == "OPEN"


def test_anonymous_is_sent_to_login(client):
    response = client.get(reverse(URL))
    assert response.status_code == 302 and "login" in response["Location"]


def test_an_identity_phone_is_masked_even_for_an_editor(client, editor):
    make("IDENTITY_REVIEW", "NAME_CONFLICT", IDENTITY, subject="p1")
    body = get(client, editor, category="IDENTITY_REVIEW")
    assert "0812345678" not in body and "<strong>******5678</strong>" in body
    assert "3 ออเดอร์ · 1 ชื่อที่ต่างกัน" in body
    assert "ความใกล้เคียงของชื่อ: 1↔2 87%" in body
    assert '<span class="chip">c1</span>' in body and "ลูกค้าเดิมเบอร์เดียวกัน" in body
    assert "04/10/2567–15/10/2567" in body  # 1728000000000 / 1729000000000 ms, Bangkok date, Buddhist year


def test_an_identity_with_no_phone_says_so(client, editor):
    make("IDENTITY_REVIEW", "NO_PHONE", {**IDENTITY, "normalizedPhone": "", "candidates": []}, subject="p2")
    assert "<strong>ไม่มีเบอร์</strong>" in get(client, editor, category="IDENTITY_REVIEW")


def test_a_staff_alias_card_shows_its_evidence_and_never_guesses(client, editor):
    make()
    body = get(client, editor)
    assert "<strong>พนักงานเดิม 5</strong><small>พบ 8,613 ออเดอร์</small>" in body
    assert "ทีมเดิม: crm 150 · upsell 50" in body
    assert "ไม่มีผู้ใช้ที่ชื่อตรงทุกตัว — ระบบไม่เดาให้" in body
    assert '<span class="chip">NO_EXACT_CANDIDATE</span><span class="chip">OPEN</span><small>rev 1</small>' in body


def test_a_staff_alias_with_exact_candidates_lists_them(client, editor):
    evidence = {**STAFF, "exactCandidates": [{"userId": "u1", "active": False, "basis": "EXACT_LEGACY_ALIAS"}]}
    make(evidence=evidence)
    body = get(client, editor)
    assert "u1 (ปิดใช้งาน) · ชื่อเดิมตรงทุกตัว" in body and "ไม่มีผู้ใช้ที่ชื่อตรง" not in body


def test_unknown_status_and_null_date_cards(client, editor):
    make("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", STATUS, subject="b")
    make("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", NULL_DATE, subject="c")
    status_body = get(client, editor, category="UNKNOWN_STATUS")
    assert "<strong>แปลก</strong><small>3 ออเดอร์</small>" in status_body
    assert "ถ้าไม่ตัดสิน: PENDING_CONFIRMATION / UNPAID / NOT_SHIPPED" in status_body
    date_body = get(client, editor, category="NULL_DATE_ORDER")
    assert "<strong>ออเดอร์ SO-9</strong><small>ไม่มีวันที่ในแหล่งข้อมูล</small>" in date_body
    assert "2 รายการ · ยอด 100 · มีเบอร์ · สถานะเดิม สำเร็จ" in date_body


def test_the_action_menu_follows_the_rules_for_each_card(client, editor):
    make("IDENTITY_REVIEW", "NAME_CONFLICT", IDENTITY, subject="with")
    make("IDENTITY_REVIEW", "NAME_CONFLICT", {**IDENTITY, "candidates": []}, subject="without")
    make("IDENTITY_REVIEW", "NO_PHONE", {**IDENTITY, "normalizedPhone": "", "candidates": []}, subject="nophone")
    body = get(client, editor, category="IDENTITY_REVIEW")
    menus = [re.findall(r'<option value="(\w+)">', m) for m in re.findall(r'<select name="action"[^>]*>(.*?)</select>', body, re.S)]
    assert menus == [
        ["MATCH_EXISTING", "CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "DEFER"],
        ["CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "DEFER"],
        ["KEEP_SEPARATE", "DEFER"],
    ]
    for label in ("รวมกับลูกค้าที่มีอยู่", "สร้างลูกค้าใหม่ 1 ราย", "แยกตามชื่อ (ไม่รวม)", "เลื่อนการตัดสิน"):
        assert label in body
    assert 'placeholder="เหตุผล (บังคับ)"' in body and ">บันทึกการตัดสิน</button>" in body


def test_status_and_reason_filters(client, editor):
    make(subject="open", reason="NO_EXACT_CANDIDATE")
    make(subject="other-reason", reason="EXACT_CANDIDATE_AVAILABLE")
    make(subject="done", status="RESOLVED")
    assert get(client, editor).count('<article class="decision-card">') == 2
    only = get(client, editor, reason="EXACT_CANDIDATE_AVAILABLE")
    assert only.count('<article class="decision-card">') == 1 and "EXACT_CANDIDATE_AVAILABLE" in only
    resolved = get(client, editor, status="RESOLVED")
    assert resolved.count('<article class="decision-card">') == 1


def test_an_unknown_category_or_reason_falls_back_instead_of_failing(client, editor):
    make()
    for params in ({"category": "NOPE"}, {"status": "NOPE"}, {"reason": "NOPE"}, {"cursor": "garbage"}, {"cursor": "e30"}):
        login(client, editor)
        assert client.get(reverse(URL), params).status_code == 200


def test_a_resolved_card_shows_the_resolution_and_a_reopen_control(client, editor):
    d = make()
    post_resolve(client_logged := login_client(client, editor), d, action="HISTORICAL_ONLY")
    body = client.get(reverse(URL), {"status": "RESOLVED"}).content.decode()
    assert '<div class="resolved">' in body and "เก็บเป็นประวัติเท่านั้น" in body and "ตรวจสอบแล้ว" in body
    assert "เปิดใหม่" in body and "<dialog" in body and "decision-form" not in body
    assert client_logged is client


def login_client(client, user):
    login(client, user)
    return client


def test_resolving_through_the_form_records_the_decision_and_shows_a_toast(client, editor):
    d = make()
    login(client, editor)
    response = post_resolve(client, d, action="HISTORICAL_ONLY")
    assert "บันทึกแล้ว: เก็บเป็นประวัติเท่านั้น" in response.content.decode()
    d.refresh_from_db()
    assert d.status == "RESOLVED" and d.revision == 2


def test_a_stale_form_shows_the_reload_message_and_changes_nothing(client, editor):
    d = make()
    login(client, editor)
    body = post_resolve(client, d, action="HISTORICAL_ONLY", revision=9).content.decode()
    assert "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน" in body
    d.refresh_from_db()
    assert d.status == "OPEN" and d.revision == 1


def test_a_reason_that_is_too_short_is_refused_with_its_code(client, editor):
    d = make()
    login(client, editor)
    body = post_resolve(client, d, action="HISTORICAL_ONLY", reason="ab").content.decode()
    assert "REASON_REQUIRED" in body
    d.refresh_from_db()
    assert d.status == "OPEN"


def test_map_to_status_collects_the_three_axes(client, editor):
    d = make("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", STATUS, subject="b")
    login(client, editor)
    body = post_resolve(
        client, d, action="MAP_TO_STATUS", orderStatus="SHIPPED", paymentStatus="PAID", shippingStatus="IN_TRANSIT"
    ).content.decode()
    d.refresh_from_db()
    assert "บันทึกแล้ว: กำหนดสถานะมาตรฐาน" in body
    assert d.resolution["payload"] == {"orderStatus": "SHIPPED", "paymentStatus": "PAID", "shippingStatus": "IN_TRANSIT"}
    other = make("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", {**STATUS, "rawValue": "อื่น"}, subject="c")
    assert "STATUS_REQUIRED" in post_resolve(client, other, action="MAP_TO_STATUS", orderStatus="SHIPPED").content.decode()


def test_the_authoritative_date_is_read_as_bangkok_wall_clock(client, editor):
    d = make("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", NULL_DATE, subject="c")
    login(client, editor)
    post_resolve(client, d, action="SET_AUTHORITATIVE_DATE", orderedAt="2026-10-26T10:00", evidenceRef="ใบส่งของ 123")
    d.refresh_from_db()
    expected = round(datetime(2026, 10, 26, 10, 0, tzinfo=BANGKOK_TZ).timestamp() * 1000)
    assert d.resolution["payload"] == {"orderedAt": expected, "evidenceRef": "ใบส่งของ 123"}
    bad = make("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", {**NULL_DATE, "sourceOrderId": "SO-8"}, subject="d")
    assert "AUTHORITATIVE_DATE_REQUIRED" in post_resolve(client, bad, action="SET_AUTHORITATIVE_DATE", orderedAt="not a date", evidenceRef="x" * 5).content.decode()


def test_map_to_user_accepts_a_typed_or_chosen_user_id(client, editor):
    worker = make_user(email="w@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี")
    evidence = {**STAFF, "exactCandidates": [{"userId": str(worker.pk), "active": True, "basis": "EXACT_DISPLAY_NAME"}]}
    d = make(evidence=evidence)
    login(client, editor)
    post_resolve(client, d, action="MAP_TO_USER", exact_user=str(worker.pk))
    d.refresh_from_db()
    assert d.resolution["payload"] == {"userId": str(worker.pk), "userActiveAtApproval": True}


def test_reopening_returns_a_resolved_decision_to_open(client, editor):
    d = make()
    login(client, editor)
    post_resolve(client, d, action="HISTORICAL_ONLY")
    d.refresh_from_db()
    response = client.post(
        reverse("leadui:decision_reopen", args=[d.decision_id]), {"revision": d.revision, "reason": "ตัดสินผิด"}, follow=True
    )
    assert "เปิดการตัดสินใหม่แล้ว" in response.content.decode()
    d.refresh_from_db()
    assert d.status == "OPEN" and d.revision == 3
    stale = client.post(reverse("leadui:decision_reopen", args=[d.decision_id]), {"revision": 1, "reason": "ตัดสินผิด"}, follow=True)
    assert "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน" in stale.content.decode()


def test_an_unknown_decision_id_is_a_message_not_a_crash(client, editor):
    login(client, editor)
    response = client.post(
        reverse("leadui:decision_resolve", args=["dec_none_00"]), {"revision": 1, "evidence_hash": "0" * 64, "action": "DEFER", "reason": "เหตุผล"}, follow=True
    )
    assert response.status_code == 200 and "ไม่พบรายการนี้" in response.content.decode()


def test_the_queue_pages_forward_and_back(client, editor):
    for i in range(30):
        make(subject=f"s{i:02d}")
    login(client, editor)
    first = client.get(reverse(URL)).content.decode()
    assert first.count('<article class="decision-card">') == 25
    assert "หน้าถัดไป" in first and "หน้าก่อน" not in first
    nxt = re.search(r'href="([^"]*)">หน้าถัดไป', first).group(1).replace("&amp;", "&")
    second = client.get(reverse(URL) + nxt).content.decode()
    assert second.count('<article class="decision-card">') == 5 and "หน้าก่อน" in second and "หน้าถัดไป" not in second
    back = re.search(r'href="([^"]*)">หน้าก่อน', second).group(1).replace("&amp;", "&")
    again = client.get(reverse(URL) + back).content.decode()
    assert again.count('<article class="decision-card">') == 25 and "หน้าก่อน" not in again


def test_an_empty_queue_shows_the_state_box(client, editor):
    body = get(client, editor)
    assert '<div class="state-box">ไม่มีรายการในคิวนี้</div>' in body and "decision-card" not in body
