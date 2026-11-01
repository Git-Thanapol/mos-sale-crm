"""Regression tests for the second review's 11 findings.

Two of these (#7, #8) are holes the C1 fix itself opened: making `caretaker` a
short-circuit path in can_view_lead let it bypass the role check behind it.
Closing one leak and opening another is worth a named test each.

Run via: docker compose exec web python -m pytest tests/view
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.core.permissions import (
    ROLE_OPTIONS,
    can_edit_lead,
    can_view_followup,
    can_view_lead,
)
from crm.leads.models import Lead, OwnershipSegment
from crm.teams.models import TeamAssignment
from tests.db.factories import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    T0,
    make_customer,
    make_lead,
    make_order,
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


# --- F1: TEAM_LEAD could not open the page its own sections live on ---------


def test_a_team_lead_may_view_the_followup_page():
    team_lead = make_user(
        email="tl@example.com", role="TEAM_LEAD", staff_code="L001", team_scope=["CRM_TEAM"]
    )
    assert can_view_followup(team_lead) is True


def test_a_team_lead_reaches_the_followup_page_over_http(client):
    team_lead = make_user(
        email="tl2@example.com", role="TEAM_LEAD", staff_code="L002", team_scope=["CRM_TEAM"]
    )
    make_team("CRM_TEAM")
    login(client, team_lead)
    response = client.get(reverse("followups:list"))
    assert response.status_code == 200
    # The sections gated on can_review_any_lead must actually render for them.
    body = response.content.decode()
    assert "Team Unassigned" in body
    assert "STOPPED Review" in body


# --- F2: the role could not be provisioned at all --------------------------


def test_team_lead_is_a_selectable_role():
    assert "TEAM_LEAD" in ROLE_OPTIONS


def test_the_user_form_can_set_the_role_and_its_team_scope():
    from crm.accounts.forms import UserAdminForm

    assert "role" in UserAdminForm.Meta.fields
    assert "team_scope" in UserAdminForm.Meta.fields


def test_the_user_form_only_stores_a_team_scope_for_a_team_lead():
    from crm.accounts.forms import UserAdminForm

    make_team("CRM_TEAM")
    telesale = UserAdminForm(
        data={
            "email": "x@example.com",
            "role": "พนักงาน",
            "staff_code": "S1",
            "staff_name": "x",
            "team_scope": ["CRM_TEAM"],
            "is_active": "on",
        }
    )
    assert telesale.is_valid(), telesale.errors
    assert telesale.cleaned_data["team_scope"] == []

    team_lead = UserAdminForm(
        data={
            "email": "y@example.com",
            "role": "TEAM_LEAD",
            "staff_code": "L1",
            "staff_name": "y",
            "team_scope": ["CRM_TEAM"],
            "is_active": "on",
        }
    )
    assert team_lead.is_valid(), team_lead.errors
    assert team_lead.cleaned_data["team_scope"] == ["CRM_TEAM"]


# --- F3: a hand-edited age filter was an uncaught 500 ----------------------


@pytest.mark.parametrize(
    "params",
    [
        {"age_preset": "foo"},
        {"age_min_days": "10", "age_max_days": "5"},
        {"age_min_days": "-1"},
        {"age_preset": "NO_ORDER", "age_min_days": "10", "age_max_days": "20"},
        {"age_min_days": "999999"},
    ],
)
def test_a_nonsensical_age_filter_does_not_500(client, editor, params):
    make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))
    login(client, editor)
    assert client.get(reverse("followups:list"), params).status_code == 200


# --- F4: a non-numeric caretaker was a 500, and was unvalidated ------------


def test_a_non_numeric_caretaker_is_a_bad_request_not_a_500(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"))
    login(client, editor)
    assert client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": "abc"}).status_code == 400


def test_an_inactive_user_cannot_be_assigned_as_caretaker(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000003", staff_code="S0001"))
    gone = make_user(email="gone@example.com", staff_code="S0009", is_active=False)
    login(client, editor)
    assert (
        client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": gone.id}).status_code
        == 400
    )
    lead.refresh_from_db()
    assert lead.caretaker_id is None


# --- F5: a failed derived-state refresh could lose the order ---------------


def test_a_failing_lead_sync_does_not_lose_the_order(client, editor, monkeypatch):
    from crm.leads import sync
    from crm.orders.models import Order
    from crm.orders.services import LineItem, save_order_for_customer

    customer = make_customer(_uid="0000004", staff_code="S0001", owner_display="หนึ่ง")
    make_lead(customer=customer)

    def boom(**kwargs):
        raise RuntimeError("derived state exploded")

    monkeypatch.setattr(sync, "_refresh_order_rollups", boom)

    save_order_for_customer(
        actor=editor,
        customer=customer,
        order_no="SO-KEEP-1",
        order_date=timezone.localdate(),
        sale_type="NEW_ORDER",
        url="",
        address="",
        items=[LineItem(sku="SKU-1", product_name="สินค้า", qty=1, amount=100)],
        bypass_owner_conflict=True,
    )

    # The order is the authoritative record and must survive.
    assert Order.objects.filter(customer=customer, order_no="SO-KEEP-1").exists()


# --- F6: autoStop left the cycle active and the lock set -------------------


def test_auto_stop_ends_the_cycle_releases_the_lock_and_audits():
    from crm.followcycles.models import FollowupCycle
    from crm.followcycles.services import log_contact_attempt, open_cycle
    from crm.leads.models import LeadAuditEvent

    editor = make_user(email="e2@example.com", role=ROLE_EDITOR, staff_code="E002")
    team = make_team(
        "CRM_TEAM",
        policy={
            "ownershipPolicy": "LATEST_ORDER_WITH_LOCK",
            "maxNoAnswerAttempts": 1,
            "autoStop": True,
        },
    )
    lead = make_lead(customer=make_customer(_uid="0000005", staff_code="S0001"), team=team)
    cycle = open_cycle(actor=editor, lead=lead)
    cycle.lock_active = True
    cycle.save(update_fields=["lock_active"])

    log_contact_attempt(actor=editor, lead=lead, result="NO_ANSWER")

    lead.refresh_from_db()
    cycle.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"
    assert lead.current_cycle_id is None
    assert cycle.status == "ENDED"
    assert cycle.ended_reason == "STOPPED"
    assert cycle.lock_active is False
    assert LeadAuditEvent.objects.filter(lead=lead, action="STATUS_CHANGE").exists()


# --- F7: the caretaker shortcut skipped the role check ---------------------


def test_a_viewer_who_is_caretaker_still_cannot_work_the_lead():
    viewer = make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="")
    lead = make_lead(
        customer=make_customer(_uid="0000006", staff_code="S0001"), caretaker=viewer
    )
    assert can_view_lead(viewer, lead) is False
    assert can_edit_lead(viewer, lead) is False


def test_this_repos_admin_who_is_caretaker_still_cannot_work_the_lead():
    # ADMIN is deliberately weaker than EDITOR for follow-up.
    admin = make_user(email="a@example.com", role=ROLE_ADMIN, staff_code="A001")
    lead = make_lead(customer=make_customer(_uid="0000007", staff_code="S0001"), caretaker=admin)
    assert can_view_lead(admin, lead) is False


def test_a_viewer_who_is_caretaker_cannot_post_either(client):
    viewer = make_user(email="v2@example.com", role=ROLE_VIEWER, staff_code="")
    lead = make_lead(
        customer=make_customer(_uid="0000008", staff_code="S0001"), caretaker=viewer
    )
    login(client, viewer)
    assert client.post(reverse("leads:add_note", args=[lead.id]), {"body": "x"}).status_code == 403


def test_a_telesale_caretaker_is_still_allowed():
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001")
    lead = make_lead(
        customer=make_customer(_uid="0000009", staff_code="S0001"), caretaker=telesell
    )
    assert can_view_lead(telesell, lead) is True


# --- F8: the queryset did not mirror the predicate for a blank staff_code --


def test_a_caretaker_with_a_blank_staff_code_sees_their_own_lead():
    telesell = make_user(email="ts3@example.com", role=ROLE_TELESELL, staff_code="")
    lead = make_lead(
        customer=make_customer(_uid="0000010", staff_code="S0002"), caretaker=telesell
    )
    assert can_view_lead(telesell, lead) is True
    assert lead.id in set(Lead.objects.for_user(telesell).values_list("pk", flat=True))


def test_a_non_caretaker_with_a_blank_staff_code_still_sees_nothing():
    telesell = make_user(email="ts4@example.com", role=ROLE_TELESELL, staff_code="")
    make_lead(customer=make_customer(_uid="0000011", staff_code="S0002"))
    assert Lead.objects.for_user(telesell).count() == 0


# --- F9: the badge disagreed with the queue it points at -------------------


def test_a_locked_lead_is_not_badged_for_reallocation_review(editor):
    from crm.followcycles.services import open_cycle
    from crm.leads import presenters

    caretaker = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S0001")
    lead = make_lead(
        customer=make_customer(_uid="0000012", staff_code="S0001"),
        caretaker=caretaker,
        last_order_at=timezone.now() - timezone.timedelta(days=200),
    )
    cycle = open_cycle(actor=editor, lead=lead)
    cycle.lock_active = True
    cycle.save(update_fields=["lock_active"])

    lead.refresh_from_db()
    assert presenters.decorate(lead)["reallocation_candidate"] is False


# --- F10: last_order_at and last_order_id described different orders -------


def test_the_rollup_pair_always_describes_the_same_order(editor):
    from crm.leads.sync import sync_leads_for_order

    customer = make_customer(_uid="0000013", staff_code="S0001")
    lead = make_lead(customer=customer)
    newest = make_order(customer=customer, closer=None, ordered_at=timezone.now())
    sync_leads_for_order(actor=editor, order=newest)

    backdated = make_order(
        customer=customer, closer=None, ordered_at=timezone.now() - timezone.timedelta(days=30)
    )
    sync_leads_for_order(actor=editor, order=backdated)

    lead.refresh_from_db()
    assert lead.last_order_id == newest.pk
    # last_order_at is anchored at BANGKOK midnight, so its UTC .date() is the
    # previous day. Compare in Bangkok or the assertion is off by one.
    from crm.domain.bangkok import BANGKOK_TZ

    assert lead.last_order_at.astimezone(BANGKOK_TZ).date() == newest.order_date


# --- F11: the dry run over-reported on a re-run ----------------------------


def test_a_resolve_to_dry_run_does_not_recount_existing_leads(capsys):
    from django.core.management import call_command

    make_team("CRM_TEAM")
    customer = make_customer(_uid="0000014", staff_code="")
    make_lead(customer=customer, team_code="CRM_TEAM")

    call_command("backfill_leads", team_code="CRM_TEAM", resolve_to="CRM_TEAM", dry_run=True)
    out = capsys.readouterr().out
    assert "leads created 0" in out
    assert "already present 1" in out
