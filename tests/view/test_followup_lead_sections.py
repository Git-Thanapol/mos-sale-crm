"""/followup keeps the legacy list; the lead POST routes still work from it.

The lead queue partials were removed: the V2 pages (crm/leadui) replace them.

Run via: docker compose exec web python -m pytest tests/view
"""


import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import ContactAttempt
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


def test_telesale_sees_no_review_or_intake_sections(client, telesell_s1):
    body = get_page(client, telesell_s1).content.decode()
    assert "Team Unassigned" not in body
    assert "STOPPED Review" not in body
    assert "Global Unassigned" not in body
    # Their own work is still reachable.
    assert "งานของฉัน" in body


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
