"""The Lead surface rendered into /followup.

The customer's request was to put this on the customer-follow-up page with the
permissions the source code defines, so the legacy Followup list must keep
working while the Lead sections appear alongside it.

Sections a user cannot act on are omitted rather than drawn disabled. The nav
keeps its show-but-gray behaviour (crm/core/nav.py) because that tells a user
a page exists; a review queue a telesale can never use is just noise.

Run via: docker compose exec web python -m pytest tests/view
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import ContactAttempt
from crm.intake.services import upsert_intake
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    make_customer,
    make_lead,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"
NOW = timezone.now()


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def telesell_s1():
    return make_user(email="ts1@example.com", role=ROLE_TELESELL, staff_code="S0001")


def _put_in_team(user, team_code):
    """A telesale is scoped by staff_code AND the lead's team, so acting on a
    lead needs a real membership row. See tests/db/test_telesale_team_scope.py.
    """
    from crm.teams.models import TeamAssignment

    make_team(team_code)
    TeamAssignment.objects.create(
        user=user, team_code=team_code, effective_from=timezone.now()
    )


def get_page(client, user, **params):
    login(client, user)
    return client.get(reverse("followups:list"), params)


def test_follow_up_page_still_renders_the_legacy_list(client, editor):
    body = get_page(client, editor).content.decode()
    assert "ติดตามลูกค้า" in body
    assert "ความสำคัญ" in body


def test_follow_up_page_shows_the_lead_sections_for_an_editor(client, editor):
    body = get_page(client, editor).content.decode()
    for label in ("จัดการ Lead", "งานของฉัน", "Team Unassigned", "STOPPED Review", "Global Unassigned"):
        assert label in body


def test_the_age_bucket_labels_are_rendered(client, editor):
    body = get_page(client, editor).content.decode()
    for label in ("0–7 วัน", "ไม่มีออเดอร์เลย", "มากกว่า 90 วัน"):
        assert label in body


def test_telesale_sees_no_review_or_intake_sections(client, telesell_s1):
    body = get_page(client, telesell_s1).content.decode()
    assert "Team Unassigned" not in body
    assert "STOPPED Review" not in body
    assert "Global Unassigned" not in body
    # Their own work is still reachable.
    assert "งานของฉัน" in body


def test_lead_row_shows_missed_channel_group_and_last_order_age(client, editor):
    make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"),
        missed_channel_count=3,
        last_order_at=NOW - timedelta(days=100),
    )
    body = get_page(client, editor).content.decode()
    assert "3+" in body
    assert "100 วันที่แล้ว" in body


def test_a_lead_with_no_order_renders_a_no_order_marker(client, editor):
    make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), last_order_at=None)
    assert "ยังไม่มีออเดอร์" in get_page(client, editor).content.decode()


def test_a_stale_lead_is_flagged_for_review_without_being_reassigned(client, editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    lead = make_lead(
        customer=make_customer(_uid="0000003", staff_code="S0001"),
        caretaker=caretaker,
        last_order_at=NOW - timedelta(days=200),
    )
    body = get_page(client, editor, lead_section="reallocation").content.decode()
    assert "ควรทบทวน" in body or "ทบทวนการมอบหมาย" in body
    lead.refresh_from_db()
    assert lead.caretaker_id == caretaker.id


def test_the_reallocation_section_says_it_does_not_reassign(client, editor):
    body = get_page(client, editor, lead_section="reallocation").content.decode()
    assert "ไม่เปลี่ยนผู้ดูแลให้อัตโนมัติ" in body


def test_a_stopped_lead_shows_its_reason(client, editor):
    make_lead(
        customer=make_customer(_uid="0000004", staff_code="S0001"),
        lifecycle_status="STOPPED",
        stopped_reason="MANUAL_STOP",
        caretaker=make_user(email="c2@example.com", staff_code="S0001"),
    )
    body = get_page(client, editor, lead_section="stopped").content.decode()
    assert "หยุดโดยผู้ใช้" in body


def test_a_telesale_requesting_a_review_section_falls_back_to_their_queue(client, telesell_s1):
    # Not a 403: the section simply is not theirs, so the page renders the
    # default queue rather than failing.
    response = get_page(client, telesell_s1, lead_section="stopped")
    assert response.status_code == 200
    assert "STOPPED Review" not in response.content.decode()


def test_logging_a_contact_attempt_from_the_page_does_not_change_lifecycle(client, telesell_s1):
    lead = make_lead(customer=make_customer(_uid="0000005", staff_code="S0001"))
    _put_in_team(telesell_s1, "CRM_TEAM")
    login(client, telesell_s1)
    response = client.post(
        reverse("leads:log_attempt", args=[lead.id]), {"result": "NO_ANSWER", "note": ""}
    )
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"
    assert ContactAttempt.objects.filter(lead=lead).count() == 1


def test_adding_a_note_from_the_page_updates_the_cached_copy(client, telesell_s1):
    lead = make_lead(customer=make_customer(_uid="0000006", staff_code="S0001"))
    _put_in_team(telesell_s1, "CRM_TEAM")
    login(client, telesell_s1)
    client.post(reverse("leads:add_note", args=[lead.id]), {"body": "โทรแล้ว"})
    lead.refresh_from_db()
    assert lead.latest_note == "โทรแล้ว"


def test_the_intake_section_lists_unresolved_work_for_an_editor(client, editor):
    customer = make_customer(_uid="0000007", staff_code="S0001", customer_name="ลูกค้ารอทีม")
    upsert_intake(customer=customer, source_order_id="SO-9", source_kind="LITE_DETAIL_ORDER")
    make_team("CRM_TEAM")
    body = get_page(client, editor).content.decode()
    assert "ลูกค้ารอทีม" in body
    assert "SO-9" in body


def test_the_queue_offers_a_next_link_rather_than_page_numbers(client, editor):
    for i in range(60):
        make_lead(
            customer=make_customer(_uid=f"{i + 100:07d}", staff_code="S0001"),
            last_order_at=NOW - timedelta(days=i + 1),
        )
    body = get_page(client, editor).content.decode()
    assert "หน้าถัดไป" in body
    assert "lead_cursor=" in body


def test_an_editor_page_render_stays_within_a_query_budget(
    client, editor, django_assert_max_num_queries
):
    for i in range(10):
        make_lead(customer=make_customer(_uid=f"{i + 200:07d}", staff_code="S0001"))
    login(client, editor)
    # Legacy followup list + lead queue + intake queue + auth/session. The
    # ceiling is what stops a per-row query creeping into the row loop.
    with django_assert_max_num_queries(30):
        client.get(reverse("followups:list"))
