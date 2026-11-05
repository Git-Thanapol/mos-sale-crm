"""The three import screens, end to end over HTTP.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_import_pages.py
"""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from crm.importv2.models import ImportBatch, ImportConflict
from crm.importv2.services.preview import build_preview
from crm.orders.models import Order
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_customer, make_team, make_user
from tests.db.importv2_helpers import NAME, batch_from, crm_row, xlsx

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


@pytest.fixture
def team_lead():
    return make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"])


def upload(client, rows, name="crm_customers.xlsx", content=None):
    data = content if content is not None else xlsx(rows)
    return client.post(reverse("leadui:import_upload"), {"file": SimpleUploadedFile(name, data)}, follow=True)


def body_of(response):
    return response.content.decode()


# --- นำเข้าข้อมูล -------------------------------------------------------------------------


def test_the_import_page_matches_the_prototype_layout(client, editor):
    login(client, editor)
    body = body_of(client.get(reverse("leadui:import")))
    assert "<h1>นำเข้าข้อมูล</h1>" in body
    assert "<p>ไม่มี business write ก่อน Confirm; Batch Business Date ต้องยืนยันสำหรับ Tele</p>" in body
    steps = re.findall(r'<div class="step"><span>(\d)</span>([A-Za-z]+)</div>', body)
    assert steps == [("1", "Upload"), ("2", "Validate"), ("3", "Preview"), ("4", "Resolve"), ("5", "Confirm"), ("6", "Commit")]
    for label in ("ลูกค้าใหม่", "ออเดอร์ใหม่", "อัปเดต", "Conflict"):
        assert f"<span>{label}</span>" in body
    assert 'accept=".xlsx,.csv"' in body and 'aria-label="เลือกไฟล์นำเข้า"' in body
    assert "เลือกไฟล์นำเข้า (.xlsx / .csv)" in body
    assert "Batch Business Date (Asia/Bangkok)" in body and 'type="date"' in body
    assert '<div class="metric-grid">' in body and '<section class="detail-card">' in body
    for button in ("สร้าง Preview", "Confirm", "Commit"):
        assert f">{button}</button>" in body
    assert "ยังไม่ได้เลือกไฟล์" in body


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_only_an_editor_may_open_or_use_the_import_page(client, role):
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["TEAM_A"])
    login(client, user)
    assert client.get(reverse("leadui:import")).status_code == 404
    assert client.post(reverse("leadui:import_upload"), {}).status_code == 404


def test_anonymous_is_sent_to_login(client):
    for name in ("leadui:import", "leadui:import_conflicts", "leadui:import_history"):
        response = client.get(reverse(name))
        assert response.status_code == 302 and "login" in response["Location"]


def test_uploading_a_file_stages_a_batch_and_shows_it(client, editor):
    login(client, editor)
    response = upload(client, [crm_row(), crm_row(order="SO-101", phone="0899999999")])
    body = body_of(response)
    batch = ImportBatch.objects.get()
    assert response.redirect_chain[-1][0].endswith(f"?batch={batch.batch_id}")
    assert '<div class="toast">' in body and "2 แถว" in body
    assert "<h2>crm_customers.xlsx</h2>" in body
    assert "crm-customers@1 · 2 rows · อัปโหลดโดย editor@example.com" in body
    assert '<span class="chip neutral">PREVIEW</span>' in body
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [("evil.xlsx", b"MZ\x90\x00" + b"\x00" * 32), ("notes.txt", b"hello"), ("empty.csv", b"")],
)
def test_a_bad_file_is_refused_with_a_message_and_no_batch(client, editor, name, content):
    login(client, editor)
    body = body_of(upload(client, [], name=name, content=content))
    assert not ImportBatch.objects.exists()
    assert "นำเข้าไฟล์ไม่ได้" in body


def test_posting_without_a_file_is_a_message_not_an_error(client, editor):
    login(client, editor)
    response = client.post(reverse("leadui:import_upload"), {}, follow=True)
    assert response.status_code == 200 and "กรุณาเลือกไฟล์นำเข้า" in body_of(response)


def test_the_preview_button_builds_the_preview_and_fills_the_metrics(client, editor):
    login(client, editor)
    upload(client, [crm_row(), crm_row(order="SO-101", phone="0899999999")])
    batch = ImportBatch.objects.get()
    response = client.post(reverse("leadui:import_preview", args=[batch.batch_id]), follow=True)
    body = body_of(response)
    batch.refresh_from_db()
    assert batch.status == "AWAITING_RESOLUTION"
    assert "<strong>2</strong><span>ลูกค้าใหม่</span>" in body and "<strong>2</strong><span>ออเดอร์ใหม่</span>" in body
    assert '<span class="chip neutral">AWAITING_RESOLUTION</span>' in body
    assert "สร้าง Preview แล้ว" in body


def test_validation_problems_are_listed_and_the_preview_stays_blocked(client, editor):
    login(client, editor)
    upload(client, [crm_row(**{"0": "not a date"})])
    batch = ImportBatch.objects.get()
    body = body_of(client.post(reverse("leadui:import_preview", args=[batch.batch_id]), follow=True))
    assert "INVALID_ROWS" in body and 'class="state-box error"' in body
    batch.refresh_from_db()
    assert batch.status == "PREVIEW"


def test_the_batch_date_input_validates_and_sticks(client, editor):
    login(client, editor)
    upload(client, [crm_row()])
    batch = ImportBatch.objects.get()
    url = reverse("leadui:import_date", args=[batch.batch_id])
    bad = body_of(client.post(url, {"batch_business_date": "2026-02-30"}, follow=True))
    assert "not a calendar date" in bad
    good = body_of(client.post(url, {"batch_business_date": "2026-10-26"}, follow=True))
    assert 'value="2026-10-26"' in good and "ยืนยัน Batch Business Date แล้ว" in good


def test_confirm_is_refused_with_every_reason_while_a_conflict_is_open(client, editor):
    make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    login(client, editor)
    upload(client, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    batch = ImportBatch.objects.get()
    client.post(reverse("leadui:import_preview", args=[batch.batch_id]))
    batch.refresh_from_db()
    body = body_of(
        client.post(
            reverse("leadui:import_confirm", args=[batch.batch_id]),
            {"preview_hash": batch.preview_hash, "operation_key": "k"},
            follow=True,
        )
    )
    assert "Confirm ไม่ได้" in body and "blocking conflict(s) are still open" in body
    batch.refresh_from_db()
    assert batch.status == "AWAITING_RESOLUTION"


def test_the_whole_flow_over_http_upload_preview_resolve_confirm_commit_rollback(client, editor):
    make_team("CRM_TEAM")
    existing = make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    login(client, editor)
    upload(client, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    batch = ImportBatch.objects.get()
    client.post(reverse("leadui:import_preview", args=[batch.batch_id]))

    conflicts_body = body_of(client.get(reverse("leadui:import_conflicts")))
    conflict = ImportConflict.objects.get()
    form = re.search(r'action="/followup/import/conflicts/%d/resolve/"[^>]*>(.*?)</form>' % conflict.id, conflicts_body, re.S)
    assert form, "the AUTO_MATCH button form is on the page"
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form.group(1)))
    assert fields["resolution"] == "AUTO_MATCH"
    resolved = client.post(reverse("leadui:conflict_resolve", args=[conflict.id]), fields, follow=True)
    assert "บันทึกการตัดสิน: AUTO_MATCH" in body_of(resolved)

    batch.refresh_from_db()
    client.post(reverse("leadui:import_confirm", args=[batch.batch_id]), {"preview_hash": batch.preview_hash, "operation_key": "c"})
    batch.refresh_from_db()
    assert batch.status == "COMMITTING"
    done = client.post(reverse("leadui:import_commit", args=[batch.batch_id]), {"operation_key": "m"}, follow=True)
    assert "Commit เสร็จ" in body_of(done)
    assert Order.objects.get(order_no="SO-100").customer_id == existing.id

    history = body_of(client.get(reverse("leadui:import_history")))
    assert '<span class="chip ok">COMMITTED</span>' in history and "Rollback" in history
    undone = client.post(reverse("leadui:history_rollback", args=[batch.batch_id]), {"operation_key": "r", "reason": "x"}, follow=True)
    assert "Rollback เสร็จ" in body_of(undone)
    assert not Order.objects.filter(order_no="SO-100").exists()


def test_commit_before_confirm_is_a_message_not_a_crash(client, editor):
    login(client, editor)
    upload(client, [crm_row()])
    batch = ImportBatch.objects.get()
    body = body_of(client.post(reverse("leadui:import_commit", args=[batch.batch_id]), {"operation_key": "m"}, follow=True))
    assert "ILLEGAL_STATE" in body and not Order.objects.exists()


def test_an_unknown_batch_id_is_404(client, editor):
    login(client, editor)
    import uuid

    assert client.post(reverse("leadui:import_preview", args=[uuid.uuid4()])).status_code == 404


def test_a_garbage_batch_query_falls_back_to_the_latest(client, editor):
    login(client, editor)
    upload(client, [crm_row()])
    assert client.get(reverse("leadui:import"), {"batch": "not-a-uuid"}).status_code == 200


# --- Import Conflict Resolution -------------------------------------------------------------


def conflict_world(editor):
    batch = batch_from(editor, [crm_row()])
    ImportBatch.objects.filter(pk=batch.pk).update(status="AWAITING_RESOLUTION")
    batch.refresh_from_db()
    team_a, team_b = make_team("TEAM_A"), make_team("TEAM_B")
    made = {}
    for row, kind, team, options in (
        (1, "OWNERSHIP", team_a, ["KEEP_CURRENT", "REASSIGN"]),
        (2, "OWNERSHIP", team_b, ["KEEP_CURRENT", "REASSIGN"]),
        (3, "IDENTITY", team_a, ["AUTO_MATCH", "NEW_CUSTOMER"]),
    ):
        made[kind + str(row)] = ImportConflict.objects.create(
            batch=batch, conflict_id=f"{row}:{kind}:1", row_index=row, kind=kind, severity="BLOCKING", team=team,
            affected_team_codes=[team.code], summary=f"สรุป {kind} {row}", source_row_ref=f"row:{row}", options=options,
        )
    return batch, made


def test_the_conflicts_page_renders_cards_with_one_button_per_option(client, editor):
    conflict_world(editor)
    login(client, editor)
    body = body_of(client.get(reverse("leadui:import_conflicts")))
    assert "<h1>Import Conflict Resolution</h1>" in body
    assert "<p>Team Lead เห็นเฉพาะ own-Team; Identity และ multi-Team เป็น Admin เท่านั้น</p>" in body
    assert body.count('<article class="work-card">') == 3
    assert '<span class="chip neutral">OWNERSHIP</span> <span class="chip danger">BLOCKING</span>' in body
    assert "<h3>สรุป OWNERSHIP 1</h3>" in body and "<p>Team: TEAM_A</p>" in body and "<small>row:1</small>" in body
    for option in ("KEEP_CURRENT", "REASSIGN", "AUTO_MATCH", "NEW_CUSTOMER"):
        assert f">{option}</button>" in body


def test_a_team_lead_sees_and_resolves_only_their_own_team(client, editor, team_lead):
    batch, made = conflict_world(editor)
    login(client, team_lead)
    body = body_of(client.get(reverse("leadui:import_conflicts")))
    assert body.count('<article class="work-card">') == 1 and "สรุป OWNERSHIP 1" in body
    assert "สรุป IDENTITY 3" not in body and "สรุป OWNERSHIP 2" not in body
    for key in ("OWNERSHIP2", "IDENTITY3"):
        response = client.post(reverse("leadui:conflict_resolve", args=[made[key].id]), {"resolution": made[key].options[0]})
        assert response.status_code == 404
        made[key].refresh_from_db()
        assert made[key].status == "OPEN"


@pytest.mark.parametrize("role", [ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_other_roles_get_404_on_the_conflicts_page(client, editor, role):
    conflict_world(editor)
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X")
    login(client, user)
    assert client.get(reverse("leadui:import_conflicts")).status_code == 404


def test_a_stale_resolution_shows_the_reload_message(client, editor):
    batch, made = conflict_world(editor)
    login(client, editor)
    body = body_of(
        client.post(
            reverse("leadui:conflict_resolve", args=[made["OWNERSHIP1"].id]),
            {"resolution": "KEEP_CURRENT", "expected_version": "9", "expected_hash": "0" * 64, "operation_key": "k"},
            follow=True,
        )
    )
    assert "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน" in body
    made["OWNERSHIP1"].refresh_from_db()
    assert made["OWNERSHIP1"].status == "OPEN"


def test_an_empty_conflict_queue_shows_the_state_box(client, editor):
    login(client, editor)
    body = body_of(client.get(reverse("leadui:import_conflicts")))
    assert '<div class="state-box">ยังไม่มีรายการในคิวนี้</div>' in body and "work-card" not in body


@pytest.mark.parametrize("cursor", ["garbage", "e30", "W10", "WzEsMl0"])
def test_garbage_cursor_is_a_first_page_on_conflicts_and_history(client, editor, cursor):
    conflict_world(editor)
    login(client, editor)
    assert client.get(reverse("leadui:import_conflicts"), {"cursor": cursor}).status_code == 200
    assert client.get(reverse("leadui:import_history"), {"cursor": cursor}).status_code == 200


# --- ประวัตินำเข้า ----------------------------------------------------------------------------


def history_batches(editor):
    out = {}
    for status in ("COMMITTED", "PAUSED_QUOTA", "FAILED_RETRYABLE", "AWAITING_RESOLUTION", "ROLLED_BACK", "CANCELLED"):
        batch = batch_from(editor, [crm_row(order=f"SO-{status}")], name=f"{status.lower()}.xlsx")
        ImportBatch.objects.filter(pk=batch.pk).update(status=status)
        out[status] = ImportBatch.objects.get(pk=batch.pk)
    return out


def test_the_history_table_matches_the_prototype(client, editor):
    history_batches(editor)
    login(client, editor)
    body = body_of(client.get(reverse("leadui:import_history")))
    assert "<h1>ประวัตินำเข้า</h1>" in body and "<p>สถานะจริงเท่านั้น — แสดงสำเร็จเมื่อ COMMITTED</p>" in body
    html = body[body.index("<table") : body.index("</table>")]
    assert re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", html) == ["Batch", "ไฟล์", "สถานะ", "จำนวน", ""]
    assert '<div class="table-shell">' in body
    assert "committed.xlsx<small>CRM_CUSTOMERS</small>" in body
    assert '<span class="chip ok">COMMITTED</span>' in body and '<span class="chip warn">PAUSED_QUOTA</span>' in body


def test_rollback_is_offered_only_for_committed_paused_and_failed_retryable_batches(client, editor):
    batches = history_batches(editor)
    login(client, editor)
    body = body_of(client.get(reverse("leadui:import_history")))
    offered = set(re.findall(r'data-v2-open="rollback-(\d+)"', body))
    expected = {str(batches[s].id) for s in ("COMMITTED", "PAUSED_QUOTA", "FAILED_RETRYABLE")}
    assert offered == expected
    assert body.count("<dialog") == 3 and 'class="danger-button"' in body


def test_a_team_lead_sees_history_of_their_batches_but_never_a_rollback_button(client, editor, team_lead):
    batches = history_batches(editor)
    for batch in batches.values():
        pass
    scoped = batches["COMMITTED"]
    ImportConflict.objects.create(
        batch=scoped, conflict_id="1:OWNERSHIP:1", row_index=1, kind="OWNERSHIP", severity="BLOCKING",
        team=make_team("TEAM_A"), affected_team_codes=["TEAM_A"], summary="s", options=["KEEP_CURRENT"],
    )
    login(client, team_lead)
    body = body_of(client.get(reverse("leadui:import_history")))
    assert "committed.xlsx" in body and "cancelled.xlsx" not in body
    assert "Rollback" not in body and "<dialog" not in body
    assert client.post(reverse("leadui:history_rollback", args=[scoped.batch_id]), {}).status_code == 404


@pytest.mark.parametrize("role", [ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_other_roles_get_404_on_the_history_page(client, editor, role):
    history_batches(editor)
    login(client, make_user(email=f"{role}@example.com", role=role, staff_code="X"))
    assert client.get(reverse("leadui:import_history")).status_code == 404


def test_history_pages_by_keyset(client, editor):
    for i in range(27):
        batch_from(editor, [crm_row(order=f"SO-{i}")], name=f"f{i}.xlsx")
    login(client, editor)
    first = body_of(client.get(reverse("leadui:import_history")))
    assert first.count("<tr>") == 26  # header row + 25
    nxt = re.search(r'href="([^"]*)">หน้าถัดไป', first).group(1).replace("&amp;", "&")
    second = body_of(client.get(reverse("leadui:import_history") + nxt))
    assert second.count("<tr>") == 3 and "หน้าแรก" in second


def test_an_empty_history_shows_the_state_box(client, editor):
    login(client, editor)
    assert '<div class="state-box">ยังไม่มีรายการในคิวนี้</div>' in body_of(client.get(reverse("leadui:import_history")))
