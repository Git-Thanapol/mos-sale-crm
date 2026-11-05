"""Audit-fix regressions for the Lead write services.

Covers: missed-channel refresh on every ownership change, set_lifecycle
STOPPED rules, optimistic locking (Lead.version), manual reassign target and
reason constraints, STOPPED guards, intake resolution hardening, keyset cursor
robustness, ownership order eligibility and the N+1 closer lookup.
"""

from datetime import datetime, timedelta, timezone as dt_tz

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crm.core.pagination import encode_cursor
from crm.followcycles.models import ContactAttempt
from crm.followcycles.services import (
    add_note,
    complete_appointment,
    log_contact_attempt,
    open_cycle,
    set_appointment,
)
from crm.intake.services import resolve_intake_to_team, upsert_intake
from crm.leads.errors import InvalidCaretakerError, LeadStoppedError, StaleLeadError
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment
from crm.leads.recompute import recompute_lead_missed_channel
from crm.leads.selectors import (
    LeadQueueFilters,
    global_unassigned_queue,
    lead_queue,
    my_work_queue,
)
from crm.leads.services import (
    apply_order_to_lead,
    reactivate_lead,
    reassign_caretaker,
    reconcile_lead_access,
    set_lifecycle,
    stop_lead,
)
from crm.teams.models import TeamAssignment
from tests.db.factories import (
    ROLE_EDITOR,
    T0,
    make_customer,
    make_lead,
    make_order,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]

T1 = T0 + timedelta(days=1)


@pytest.fixture
def editor():
    return make_user(email="ed@example.com", role=ROLE_EDITOR, staff_code="E001")


def member(email, code, team="CRM_TEAM"):
    user = make_user(email=email, staff_code=code)
    TeamAssignment.objects.create(user=user, team_code=team, effective_from=T0 - timedelta(days=30))
    return user


# 1 -------------------------------------------------------------------------
def test_reassign_refreshes_missed_channel(editor):
    u1, u2 = member("a@example.com", "S1"), member("b@example.com", "S2")
    lead = make_lead(caretaker=u1)
    OwnershipSegment.objects.create(lead=lead, caretaker=u1, started_at=T0)
    make_order(customer=lead.customer, closer=None, ordered_at=T1)
    assert recompute_lead_missed_channel(lead=lead) == 1
    reassign_caretaker(actor=editor, lead=lead, caretaker=u2, reason="handover")
    lead.refresh_from_db()
    assert lead.missed_channel_count == 0


def test_reconcile_after_deactivation_refreshes_missed_channel(editor):
    u1 = member("a@example.com", "S1")
    lead = make_lead(caretaker=u1)
    OwnershipSegment.objects.create(lead=lead, caretaker=u1, started_at=T0)
    make_order(customer=lead.customer, closer=u1, ordered_at=T1)
    assert recompute_lead_missed_channel(lead=lead) == 0  # caretaker closed it
    u1.is_active = False
    u1.save()
    reconcile_lead_access(actor=editor, user=u1)
    lead.refresh_from_db()
    assert lead.caretaker_id is None
    # No current caretaker: the order now counts, and the stored value says so.
    assert lead.missed_channel_count == 1


# 3 -------------------------------------------------------------------------
def test_set_lifecycle_records_reason_and_defaults_it(editor):
    lead = make_lead()
    set_lifecycle(actor=editor, lead=lead, status="WAITING_CUSTOMER", reason="asked to wait")
    set_lifecycle(actor=editor, lead=lead, status="NEW")
    reasons = list(
        LeadAuditEvent.objects.filter(lead=lead, action="STATUS_CHANGE")
        .order_by("id")
        .values_list("reason", flat=True)
    )
    assert reasons == ["asked to wait", "manual status change"]


def test_set_lifecycle_cannot_leave_stopped_or_enter_it(editor):
    lead = make_lead()
    with pytest.raises(ValueError):
        set_lifecycle(actor=editor, lead=lead, status="STOPPED")
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    with pytest.raises(LeadStoppedError):
        set_lifecycle(actor=editor, lead=lead, status="NEW")
    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED"


# 4 -------------------------------------------------------------------------
def test_stale_version_is_rejected_and_version_bumps(editor):
    u1 = member("a@example.com", "S1")
    lead = make_lead()
    v = lead.version
    reassign_caretaker(actor=editor, lead=lead, caretaker=u1, reason="r", expected_version=v)
    lead.refresh_from_db()
    assert lead.version == v + 1
    with pytest.raises(StaleLeadError):
        stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP", expected_version=v)
    with pytest.raises(StaleLeadError):
        reactivate_lead(actor=editor, lead=lead, reason="x", expected_version=v)
    with pytest.raises(StaleLeadError):
        set_lifecycle(actor=editor, lead=lead, status="NEW", expected_version=v)
    lead.refresh_from_db()
    assert lead.lifecycle_status == "NEW"


def test_internal_callers_may_omit_version(editor):
    lead = make_lead()
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    assert lead.version == 2


def test_service_works_from_committed_state_not_the_stale_instance(editor):
    u1 = member("a@example.com", "S1")
    lead = make_lead()
    stale = Lead.objects.get(pk=lead.pk)
    reassign_caretaker(actor=editor, lead=lead, caretaker=u1, reason="r")
    # `stale` still says no caretaker; the lock refresh must see u1.
    stop_lead(actor=editor, lead=stale, reason="MANUAL_STOP")
    assert stale.caretaker_id == u1.id


# 5 -------------------------------------------------------------------------
def test_streak_counts_from_the_locked_row_not_a_stale_instance(editor):
    ts = make_user(email="t@example.com", staff_code="S9")
    lead = make_lead()
    stale = Lead.objects.get(pk=lead.pk)
    log_contact_attempt(actor=ts, lead=lead, result="NO_ANSWER")
    out = log_contact_attempt(actor=ts, lead=stale, result="NO_ANSWER")
    assert out.no_answer_streak == 2
    lead.refresh_from_db()
    assert lead.no_answer_streak == 2


# 7 -------------------------------------------------------------------------
def test_manual_reassign_requires_a_reason(editor):
    u1 = member("a@example.com", "S1")
    lead = make_lead()
    with pytest.raises(ValueError):
        reassign_caretaker(actor=editor, lead=lead, caretaker=u1, reason="  ", manual=True)


@pytest.mark.parametrize("kind", ["inactive", "other_team", "no_assignment", "editor_role"])
def test_manual_reassign_rejects_bad_targets(editor, kind):
    lead = make_lead(team_code="CRM_TEAM")
    if kind == "inactive":
        target = member("a@example.com", "S1")
        target.is_active = False
        target.save()
    elif kind == "other_team":
        make_team("UPSELL_TEAM")
        target = member("a@example.com", "S1", team="UPSELL_TEAM")
    elif kind == "no_assignment":
        target = make_user(email="a@example.com", staff_code="S1")
    else:
        target = make_user(email="a@example.com", role=ROLE_EDITOR, staff_code="S1")
    with pytest.raises(InvalidCaretakerError):
        reassign_caretaker(actor=editor, lead=lead, caretaker=target, reason="r", manual=True)
    lead.refresh_from_db()
    assert lead.caretaker_id is None


def test_manual_reassign_accepts_a_team_member_and_a_scoped_team_lead(editor):
    lead = make_lead(team_code="CRM_TEAM")
    reassign_caretaker(
        actor=editor, lead=lead, caretaker=member("a@example.com", "S1"), reason="r", manual=True
    )
    tl = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["CRM_TEAM"])
    reassign_caretaker(actor=editor, lead=lead, caretaker=tl, reason="r", manual=True)
    assert lead.caretaker_id == tl.id


# 9 -------------------------------------------------------------------------
def test_resolve_creates_open_segment_and_started_at(editor):
    customer = make_customer()
    item, _ = upsert_intake(customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    lead = resolve_intake_to_team(actor=editor, intake=item, team=make_team("CRM_TEAM"))
    lead.refresh_from_db()
    assert lead.ownership_started_at is not None
    seg = OwnershipSegment.objects.get(lead=lead, ended_at__isnull=True)
    assert seg.caretaker_id is None


def test_resolve_rejects_inactive_team_and_stale_intake_object(editor):
    customer = make_customer()
    item, _ = upsert_intake(customer=customer, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    off = make_team("UPSELL_TEAM", active=False)
    with pytest.raises(ValueError):
        resolve_intake_to_team(actor=editor, intake=item, team=off)
    team = make_team("CRM_TEAM")
    stale = type(item).objects.get(pk=item.pk)
    resolve_intake_to_team(actor=editor, intake=item, team=team)
    with pytest.raises(ValueError):
        resolve_intake_to_team(actor=editor, intake=stale, team=team)  # stale says GLOBAL_UNASSIGNED
    assert LeadAuditEvent.objects.filter(action="INTAKE_CONVERTED").count() == 1


# 10 ------------------------------------------------------------------------
def test_stopped_lead_blocks_attempts_and_appointments_but_allows_notes(editor):
    lead = make_lead()
    stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")
    with pytest.raises(LeadStoppedError):
        log_contact_attempt(actor=editor, lead=lead, result="NO_ANSWER")
    with pytest.raises(LeadStoppedError):
        set_appointment(actor=editor, lead=lead, scheduled_at=timezone.now())
    assert not ContactAttempt.objects.filter(lead=lead).exists()
    assert add_note(actor=editor, lead=lead, body="why stopped").body == "why stopped"


def test_closing_an_appointment_twice_is_rejected_from_a_stale_object(editor):
    from crm.followcycles.models import Appointment

    lead = make_lead()
    appt = set_appointment(actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=1))
    stale = Appointment.objects.get(pk=appt.pk)
    complete_appointment(actor=editor, appointment=appt)
    with pytest.raises(ValueError):
        complete_appointment(actor=editor, appointment=stale)


# 11 ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cursor",
    [
        encode_cursor((1, 2, 3)),
        encode_cursor(("2026-01-01T00:00:00+00:00", 1, 9)),
        encode_cursor(("x", "y")),
        encode_cursor((None, None)),
    ],
)
def test_global_unassigned_bad_cursor_is_first_page(editor, cursor):
    upsert_intake(customer=make_customer(), source_order_id="A", source_kind="LITE_DETAIL_ORDER")
    assert len(global_unassigned_queue(editor, after_cursor=cursor, limit=5).items) == 1


def test_work_priority_cursor_binds_the_snapshot_instant(editor):
    for i in range(5):
        make_lead(customer=make_customer(), next_follow_at=T0 + timedelta(days=i))
    f = LeadQueueFilters(sort="WORK_PRIORITY")
    first = lead_queue(editor, filters=f, limit=2)
    from crm.core.pagination import decode_cursor_parts

    parts = decode_cursor_parts(first.next_cursor, ("int", "datetime?", "int", "datetime?"))
    assert parts is not None and parts[3] is not None
    # A 3-element (pre-snapshot) or oldest-sort 2-element cursor restarts safely.
    assert len(lead_queue(editor, filters=f, after_cursor=encode_cursor((0, None, 1)), limit=2).items) == 2
    seen = [lead.id for lead in first.items]
    cursor = first.next_cursor
    while cursor:
        page = lead_queue(editor, filters=f, after_cursor=cursor, limit=2)
        seen += [lead.id for lead in page.items]
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 5


# order eligibility + 12 ----------------------------------------------------
def test_cancelled_and_stale_orders_do_not_move_ownership(editor):
    u1, u2 = member("a@example.com", "S1"), member("b@example.com", "S2")
    lead = make_lead(caretaker=u1, policy={"ownershipPolicy": "LATEST_ORDER"})
    OwnershipSegment.objects.create(lead=lead, caretaker=u1, started_at=T0)
    cancelled = make_order(customer=lead.customer, closer=u2, ordered_at=T1, order_status="CANCELLED")
    d = apply_order_to_lead(actor=editor, lead=lead, order=cancelled, lock_active=False)
    assert d.changed is False and lead.caretaker_id == u1.id
    stale = make_order(customer=lead.customer, closer=u2, ordered_at=T0 - timedelta(days=5))
    d = apply_order_to_lead(actor=editor, lead=lead, order=stale, lock_active=False)
    assert d.changed is False and lead.caretaker_id == u1.id
    ok = make_order(customer=lead.customer, closer=u2, ordered_at=T1)
    d = apply_order_to_lead(actor=editor, lead=lead, order=ok, lock_active=False)
    assert d.changed is True and lead.caretaker_id == u2.id


def test_sync_resolves_the_closer_once_per_order(editor):
    from crm.leads.sync import sync_leads_for_order

    customer = make_customer()
    for code in ("CRM_TEAM", "UPSELL_TEAM"):
        make_lead(customer=customer, team_code=code)
    closer = make_user(email="c@example.com", staff_code="S77")
    order = make_order(customer=customer, closer=closer, ordered_at=T1)
    with CaptureQueriesContext(connection) as ctx:
        sync_leads_for_order(actor=editor, order=order)
    closer_lookups = [q for q in ctx.captured_queries if "staff_code\" = 'S77'" in q["sql"]]
    assert len(closer_lookups) == 1
