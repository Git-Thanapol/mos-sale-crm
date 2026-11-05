"""Audit-fix regressions at the HTTP surface: 404 vs 403 oracle, 409 on stale
version, reassign constraints, customer assign_owner via leads, history and
export audit, export cap."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.followcycles.models import Appointment
from crm.followcycles.services import set_appointment
from crm.leads.models import Lead, LeadAuditEvent
from crm.teams.models import TeamAssignment
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    T0,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]


def login(client, user):
    user.set_password("pw-123456-x")
    user.save()
    assert client.login(email=user.email, password="pw-123456-x")


@pytest.fixture
def editor():
    return make_user(email="ed@example.com", role=ROLE_EDITOR, staff_code="E001")


def member(email, code, team="CRM_TEAM"):
    user = make_user(email=email, staff_code=code)
    TeamAssignment.objects.create(user=user, team_code=team, effective_from=T0 - timedelta(days=30))
    return user


def test_out_of_scope_lead_posts_are_404_not_403(client):
    other_team = make_lead(customer=make_customer(_uid="1000001", staff_code="S0001"), team_code="UPSELL_TEAM")
    tl = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["CRM_TEAM"])
    login(client, tl)
    for name, data in (
        ("reassign", {"reason": "x"}),
        ("stop", {"reason": "MANUAL_STOP"}),
        ("reactivate", {}),
        ("log_attempt", {"result": "NO_ANSWER"}),
        ("set_appointment", {}),
        ("add_note", {"body": "x"}),
        ("set_lifecycle", {"lifecycle_status": "NEW"}),
        ("close_cycle", {}),
    ):
        assert client.post(reverse(f"leads:{name}", args=[other_team.id]), data).status_code == 404, name
    appt = set_appointment(actor=tl, lead=other_team, scheduled_at=timezone.now() + timedelta(days=1))
    r = client.post(reverse("leads:close_appointment", args=[appt.id]), {"status": "DONE"})
    assert r.status_code == 404
    assert Appointment.objects.get(pk=appt.pk).status == "SCHEDULED"


def test_in_scope_without_action_permission_is_still_403(client):
    ts = member("ts@example.com", "S0001")
    lead = make_lead(customer=make_customer(_uid="1000002", staff_code="S0001"), caretaker=ts)
    login(client, ts)
    assert client.post(reverse("leads:reassign", args=[lead.id]), {"reason": "x"}).status_code == 403


def test_reassign_requires_reason_and_valid_team_member(client, editor):
    lead = make_lead(customer=make_customer(_uid="1000003"))
    good = member("good@example.com", "S1")
    outsider = make_user(email="out@example.com", staff_code="S2")
    login(client, editor)
    url = reverse("leads:reassign", args=[lead.id])
    assert client.post(url, {"caretaker": good.id, "reason": ""}).status_code == 400
    assert client.post(url, {"caretaker": outsider.id, "reason": "r"}).status_code == 400
    lead.refresh_from_db()
    assert lead.caretaker_id is None
    assert client.post(url, {"caretaker": good.id, "reason": "r", "version": lead.version}).status_code == 302
    lead.refresh_from_db()
    assert lead.caretaker_id == good.id


def test_stale_version_gets_409_and_changes_nothing(client, editor):
    lead = make_lead(customer=make_customer(_uid="1000004"))
    login(client, editor)
    stale = lead.version
    assert client.post(reverse("leads:stop", args=[lead.id]), {"reason": "MANUAL_STOP", "version": stale}).status_code == 302
    r = client.post(reverse("leads:reactivate", args=[lead.id]), {"version": stale, "reason": "x"})
    assert r.status_code == 409
    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"
    assert client.post(reverse("leads:stop", args=[lead.id]), {"reason": "MANUAL_STOP", "version": "abc"}).status_code == 400


def test_set_lifecycle_cannot_unstop_via_dropdown(client, editor):
    lead = make_lead(customer=make_customer(_uid="1000005"))
    login(client, editor)
    client.post(reverse("leads:stop", args=[lead.id]), {"reason": "MANUAL_STOP"})
    r = client.post(reverse("leads:set_lifecycle", args=[lead.id]), {"lifecycle_status": "NEW"})
    assert r.status_code == 409
    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"


def test_stopped_lead_rejects_attempt_and_appointment_but_not_note(client, editor):
    lead = make_lead(customer=make_customer(_uid="1000006"))
    login(client, editor)
    client.post(reverse("leads:stop", args=[lead.id]), {"reason": "MANUAL_STOP"})
    assert client.post(reverse("leads:log_attempt", args=[lead.id]), {"result": "NO_ANSWER"}).status_code == 409
    when = (timezone.now() + timedelta(days=1)).isoformat()
    assert client.post(reverse("leads:set_appointment", args=[lead.id]), {"scheduled_at": when}).status_code == 409
    assert client.post(reverse("leads:add_note", args=[lead.id]), {"body": "n"}).status_code == 302


def test_resolve_intake_rejects_inactive_team(client, editor):
    from crm.intake.services import upsert_intake
    from tests.db.factories import make_team

    item, _ = upsert_intake(customer=make_customer(_uid="1000007"), source_order_id="S1", source_kind="LITE_DETAIL_ORDER")
    make_team("CRM_TEAM", active=False)
    login(client, editor)
    assert client.post(reverse("leads:resolve_intake", args=[item.id]), {"team": "CRM_TEAM"}).status_code == 400
    assert not Lead.objects.exists()


def test_assign_owner_routes_through_lead_reassign(client, editor):
    customer = make_customer(_uid="1000008", staff_code="S0009")
    lead = make_lead(customer=customer)
    target = member("t@example.com", "S0002")
    login(client, editor)
    url = reverse("customers:assign_owner", args=[customer.id])
    client.post(url, {"staff_code": "S0002", "owner_display": "T", "reason": ""})
    lead.refresh_from_db()
    assert lead.caretaker_id is None  # reason required
    client.post(url, {"staff_code": "S0002", "owner_display": "T", "reason": "handover"})
    lead.refresh_from_db()
    customer.refresh_from_db()
    assert lead.caretaker_id == target.id
    assert customer.staff_code == "S0002"
    ev = LeadAuditEvent.objects.get(lead=lead, action="OWNERSHIP_CHANGE")
    assert ev.reason == "handover" and ev.actor_id == editor.id


def test_assign_owner_fails_without_lead_or_with_inactive_target(client, editor):
    no_lead = make_customer(_uid="1000009", staff_code="S0009")
    member("t@example.com", "S0002")
    login(client, editor)
    client.post(reverse("customers:assign_owner", args=[no_lead.id]),
                {"staff_code": "S0002", "owner_display": "T", "reason": "r"})
    no_lead.refresh_from_db()
    assert no_lead.staff_code == "S0009"
    c2 = make_customer(_uid="1000010", staff_code="S0009")
    make_lead(customer=c2)
    ghost = member("g@example.com", "S0003")
    ghost.is_active = False
    ghost.save()
    client.post(reverse("customers:assign_owner", args=[c2.id]),
                {"staff_code": "S0003", "owner_display": "G", "reason": "r"})
    c2.refresh_from_db()
    assert c2.staff_code == "S0009"


def test_history_is_audited_on_every_page_and_without_lead(client, editor):
    from tests.db.factories import make_order

    customer = make_customer(_uid="1000011")  # no lead at all
    for i in range(30):
        make_order(customer=customer, ordered_at=T0 + timedelta(days=i))
    login(client, editor)
    url = reverse("leadui:customer_history", args=[customer.id])
    r1 = client.get(url)
    assert r1.status_code == 200
    import re

    m = re.search(r"cursor=([^\"&']+)", r1.content.decode())
    assert m
    assert client.get(url + "?cursor=" + m.group(1)).status_code == 200
    events = LeadAuditEvent.objects.filter(action="CUSTOMER_HISTORY_VIEW", customer=customer)
    assert events.count() == 2
    assert all(e.lead_id is None and e.payload["scope"] == "CUSTOMER" for e in events)


def test_export_is_audited_without_pii_and_capped(client, editor, monkeypatch):
    make_customer(_uid="1000012", customer_name="Secret Name")
    login(client, editor)
    r = client.get(reverse("customers:export"), {"keyword": "Secret"})
    assert r.status_code == 200
    ev = LeadAuditEvent.objects.get(action="EXPORT")
    assert ev.actor_id == editor.id and ev.payload["rows"] == 1 and ev.payload["filters"] == {"keyword": True}
    assert "Secret" not in str(ev.payload)

    monkeypatch.setattr("crm.customers.views.EXPORT_MAX_CUSTOMERS", 0)
    r = client.get(reverse("customers:export"))
    assert r.status_code == 302
    assert LeadAuditEvent.objects.filter(action="EXPORT", payload__refused=True).exists()


def test_reassign_candidate_list_is_team_scoped(client, editor):
    lead = make_lead(customer=make_customer(_uid="1000013"), team_code="CRM_TEAM")
    inside = member("in@example.com", "S1", "CRM_TEAM")
    inside.staff_name = "InsideTeam"
    inside.save()
    outside = member("out@example.com", "S2", "UPSELL_TEAM")
    outside.staff_name = "OutsideTeam"
    outside.save()
    login(client, editor)
    body = client.get(reverse("leadui:lead_detail", args=[lead.id])).content.decode()
    assert "InsideTeam" in body and "OutsideTeam" not in body
    assert f'name="version" value="{lead.version}"' in body
