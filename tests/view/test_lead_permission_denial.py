"""Server-side denial, not hidden buttons.

06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md section B: "Must prove server-side
denial, not only hidden UI." Every test here POSTs straight to the endpoint,
bypassing whatever a template would or would not render, and asserts both the
status code and that nothing changed in the database.

Review Focus #4 is here: one customer holding leads in two teams must not leak
across them.

Run via: docker compose exec web python -m pytest tests/view
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.intake.services import upsert_intake
from crm.leads.models import Lead
from tests.db.factories import (
    ROLE_ADMIN,
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


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def telesell_s1():
    return make_user(email="ts1@example.com", role=ROLE_TELESELL, staff_code="S0001")


@pytest.fixture
def u2():
    return make_user(email="u2@example.com", role=ROLE_TELESELL, staff_code="S0002")


def test_telesale_posting_a_caretaker_change_gets_403(client, telesell_s1, u2):
    lead = make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))
    login(client, telesell_s1)
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code == 403
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_team_lead_cannot_post_to_another_teams_lead(client, u2):
    upsell = make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0001"), team_code="UPSELL_TEAM"
    )
    team_lead = make_user(
        email="tl@example.com", role="TEAM_LEAD", staff_code="L001", team_scope=["CRM_TEAM"]
    )
    login(client, team_lead)
    response = client.post(reverse("leads:reassign", args=[upsell.id]), {"caretaker": u2.id})
    assert response.status_code == 403
    upsell.refresh_from_db()
    assert upsell.caretaker_id is None


def test_team_lead_may_post_within_their_own_team(client, u2):
    lead = make_lead(
        customer=make_customer(_uid="0000003", staff_code="S0001"), team_code="CRM_TEAM"
    )
    team_lead = make_user(
        email="tl2@example.com", role="TEAM_LEAD", staff_code="L002", team_scope=["CRM_TEAM"]
    )
    login(client, team_lead)
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.caretaker_id == u2.id


def test_client_supplied_actor_and_role_are_ignored(client, telesell_s1, u2, editor):
    lead = make_lead(customer=make_customer(_uid="0000004", staff_code="S0001"))
    login(client, telesell_s1)
    response = client.post(
        reverse("leads:reassign", args=[lead.id]),
        {
            "caretaker": u2.id,
            "role": "EDITOR",
            "actor_id": str(editor.id),
            "team_id": "CRM_TEAM",
            "team_scope": "CRM_TEAM",
        },
    )
    assert response.status_code == 403
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_this_repos_admin_cannot_reassign(client, u2):
    lead = make_lead(customer=make_customer(_uid="0000005", staff_code="S0001"))
    admin = make_user(email="admin@example.com", role=ROLE_ADMIN, staff_code="A001")
    login(client, admin)
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code == 403


def test_a_viewer_cannot_add_a_note(client):
    lead = make_lead(customer=make_customer(_uid="0000006", staff_code="S0001"))
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="")
    login(client, viewer)
    assert client.post(reverse("leads:add_note", args=[lead.id]), {"body": "x"}).status_code == 403


def test_team_lead_in_team_a_cannot_reach_the_same_customers_team_b_lead(client):
    # Review Focus #4 for TEAM_LEAD. The telesale case -- the role the plan's
    # Review Focus #4 actually names -- is covered by
    # tests/db/test_telesale_team_scope.py and by the view test below. This
    # test was previously named for the telesale while building a TEAM_LEAD,
    # so it reported green over a path it never exercised.
    customer = make_customer(_uid="0000007", staff_code="S0001")
    team_a_lead = make_lead(customer=customer, team_code="CRM_TEAM")
    team_b_lead = make_lead(customer=customer, team_code="UPSELL_TEAM")
    team_lead_a = make_user(
        email="tla@example.com", role="TEAM_LEAD", staff_code="L003", team_scope=["CRM_TEAM"]
    )
    login(client, team_lead_a)

    visible = set(Lead.objects.for_user(team_lead_a).values_list("pk", flat=True))
    assert team_a_lead.id in visible
    assert team_b_lead.id not in visible

    response = client.post(reverse("leads:add_note", args=[team_b_lead.id]), {"body": "x"})
    assert response.status_code == 403


def test_telesale_cannot_post_to_the_same_customers_other_team_lead(client, telesell_s1):
    # Review Focus #4, the role it names, through the HTTP surface.
    from django.utils import timezone

    from crm.teams.models import TeamAssignment

    customer = make_customer(_uid="0000020", staff_code="S0001")
    make_team("CRM_TEAM")
    make_team("UPSELL_TEAM")
    own = make_lead(customer=customer, team_code="CRM_TEAM")
    other = make_lead(customer=customer, team_code="UPSELL_TEAM")
    TeamAssignment.objects.create(
        user=telesell_s1, team_code="CRM_TEAM", effective_from=timezone.now()
    )
    login(client, telesell_s1)

    assert client.post(reverse("leads:add_note", args=[own.id]), {"body": "ok"}).status_code in (
        302,
        303,
    )
    assert (
        client.post(reverse("leads:add_note", args=[other.id]), {"body": "leak"}).status_code == 403
    )
    assert (
        client.post(
            reverse("leads:log_attempt", args=[other.id]), {"result": "NO_ANSWER"}
        ).status_code
        == 403
    )


def test_telesale_cannot_resolve_intake(client, telesell_s1):
    customer = make_customer(_uid="0000008", staff_code="S0001")
    intake, _ = upsert_intake(
        customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER"
    )
    make_team("CRM_TEAM")
    login(client, telesell_s1)
    response = client.post(reverse("leads:resolve_intake", args=[intake.id]), {"team": "CRM_TEAM"})
    assert response.status_code == 403
    intake.refresh_from_db()
    assert intake.status == "GLOBAL_UNASSIGNED"
    assert not Lead.objects.filter(customer=customer).exists()


def test_a_team_lead_cannot_resolve_intake(client):
    intake, _ = upsert_intake(
        customer=make_customer(_uid="0000009", staff_code="S0001"),
        source_order_id="SO-2",
        source_kind="LITE_DETAIL_ORDER",
    )
    make_team("CRM_TEAM")
    team_lead = make_user(
        email="tl3@example.com", role="TEAM_LEAD", staff_code="L004", team_scope=["CRM_TEAM"]
    )
    login(client, team_lead)
    response = client.post(reverse("leads:resolve_intake", args=[intake.id]), {"team": "CRM_TEAM"})
    assert response.status_code == 403
    intake.refresh_from_db()
    assert intake.status == "GLOBAL_UNASSIGNED"


def test_editor_can_resolve_intake(client, editor):
    customer = make_customer(_uid="0000010", staff_code="S0001")
    intake, _ = upsert_intake(
        customer=customer, source_order_id="SO-3", source_kind="LITE_DETAIL_ORDER"
    )
    make_team("CRM_TEAM")
    login(client, editor)
    response = client.post(reverse("leads:resolve_intake", args=[intake.id]), {"team": "CRM_TEAM"})
    assert response.status_code in (302, 303)
    intake.refresh_from_db()
    assert intake.status == "CONVERTED"


def test_stopping_a_lead_requires_a_reason(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000011", staff_code="S0001"))
    login(client, editor)
    assert client.post(reverse("leads:stop", args=[lead.id]), {"reason": ""}).status_code == 400
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_stopping_with_a_valid_reason_works(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000012", staff_code="S0001"))
    login(client, editor)
    response = client.post(reverse("leads:stop", args=[lead.id]), {"reason": "MANUAL_STOP"})
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"
    assert lead.stopped_reason == "MANUAL_STOP"


def test_a_get_request_cannot_mutate(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000013", staff_code="S0001"))
    login(client, editor)
    assert client.get(reverse("leads:reassign", args=[lead.id])).status_code == 405
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_an_anonymous_post_cannot_mutate(client, u2):
    lead = make_lead(customer=make_customer(_uid="0000014", staff_code="S0001"))
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code in (302, 403)
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_disabled_user_cannot_mutate(client, editor, u2):
    lead = make_lead(customer=make_customer(_uid="0000015", staff_code="S0001"))
    login(client, editor)
    editor.is_active = False
    editor.save()
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code in (302, 403)
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_an_appointment_in_the_past_is_still_recorded_for_review(client, editor):
    # OVERDUE is a real computed state, so a past time is recorded and shows
    # as overdue rather than being rejected.
    lead = make_lead(customer=make_customer(_uid="0000016", staff_code="S0001"))
    login(client, editor)
    when = (timezone.now() - timezone.timedelta(days=1)).isoformat()
    response = client.post(reverse("leads:set_appointment", args=[lead.id]), {"scheduled_at": when})
    assert response.status_code in (302, 303)
    lead.refresh_from_db()
    assert lead.next_follow_at is not None
