"""Write paths for follow-up cycles and notes.

Each function is the single place its invariant lives, so a view, an importer
and a backfill cannot each end a cycle slightly differently.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.domain.enums import CONTACT_ATTEMPT_RESULTS, CYCLE_END_REASONS
from crm.followcycles.models import (
    NO_ANSWER_RESULTS,
    Appointment,
    ContactAttempt,
    FollowupCycle,
    Note,
)
from crm.leads.errors import LeadStoppedError
from crm.leads.models import Lead, LeadAuditEvent


def _lock(lead: Lead) -> Lead:
    # Imported lazily: crm.leads.services is the owner of the lock/version
    # protocol and must not be a module-level dependency of this package.
    from crm.leads.services import lock_lead

    return lock_lead(lead)


def _reject_if_stopped(lead: Lead, what: str) -> None:
    """STOPPED guard (locked rule: STOPPED is a lifecycle state a human
    reviews). Contact attempts and appointments are work on an open lead, so
    they are refused until it is reactivated. Notes stay allowed: a reviewer
    needs to record why a lead is stopped or why it should come back."""
    if lead.lifecycle_status == "STOPPED":
        raise LeadStoppedError(f"LEAD_STOPPED:{what}")


@transaction.atomic
def open_cycle(*, actor, lead: Lead, trigger_order=None) -> FollowupCycle:
    """Start a cycle, ending the active one first.

    The old cycle is ENDED, never deleted: 11_DO_NOT_CHANGE_CHECKLIST requires
    historical follow-up state to survive. `NEW_ORDER` when an order triggered
    the new cycle, `MANUAL_CLOSE` otherwise — the reason is what later explains
    why a cycle stopped.
    """
    _lock(lead)
    now = timezone.now()
    active = FollowupCycle.objects.select_for_update().filter(lead=lead, status="ACTIVE").first()
    if active is not None:
        active.status = "ENDED"
        active.ended_at = now
        active.ended_reason = "NEW_ORDER" if trigger_order is not None else "MANUAL_CLOSE"
        active.lock_active = False
        active.save(update_fields=["status", "ended_at", "ended_reason", "lock_active"])

    cycle = FollowupCycle.objects.create(
        lead=lead,
        trigger_order=trigger_order,
        status="ACTIVE",
        started_at=now,
        created_by=actor if isinstance(actor, User) else None,
    )
    lead.current_cycle_id = cycle.id
    lead.save(update_fields=["current_cycle_id", "updated_at"])
    return cycle


@transaction.atomic
def end_cycle(*, actor, cycle: FollowupCycle, reason: str) -> FollowupCycle:
    if reason not in CYCLE_END_REASONS:
        raise ValueError(f"UNKNOWN_CYCLE_END_REASON:{reason}")
    # Lead first, then the cycle: the same order stop_lead takes its locks in.
    _lock(cycle.lead)
    FollowupCycle.objects.select_for_update().filter(pk=cycle.pk).first()
    cycle.refresh_from_db()
    if cycle.status != "ACTIVE":
        raise ValueError("CYCLE_ALREADY_ENDED")

    cycle.status = "ENDED"
    cycle.ended_at = timezone.now()
    cycle.ended_reason = reason
    # A lock cannot outlive its cycle, or LATEST_ORDER_WITH_LOCK would hold
    # ownership forever on a cycle nobody is working.
    cycle.lock_active = False
    cycle.save(update_fields=["status", "ended_at", "ended_reason", "lock_active"])

    lead = cycle.lead
    lead.refresh_from_db()
    if lead.current_cycle_id == cycle.id:
        lead.current_cycle_id = None
        lead.save(update_fields=["current_cycle_id", "updated_at"])
    return cycle


@transaction.atomic
def add_note(*, actor, lead: Lead, body: str, order=None) -> Note:
    """Attach a note to the Lead, and to the active cycle when there is one.

    `Lead.latest_note` is refreshed as a cached operational copy; the Note rows
    remain authoritative, which is why the cache is written from the note and
    never the other way round.
    """
    text = (body or "").strip()
    if not text:
        raise ValueError("EMPTY_NOTE")
    _lock(lead)

    active = FollowupCycle.objects.filter(lead=lead, status="ACTIVE").first()
    note = Note.objects.create(
        lead=lead,
        cycle=active,
        order=order,
        body=text,
        created_by=actor if isinstance(actor, User) else None,
    )

    lead.latest_note = text
    lead.latest_note_updated_at = note.created_at
    lead.save(update_fields=["latest_note", "latest_note_updated_at", "updated_at"])

    LeadAuditEvent.objects.create(
        lead=lead,
        action="NOTE_ADDED",
        actor=actor if isinstance(actor, User) else None,
        payload={"note_id": note.id, "cycle_id": active.id if active else None},
    )
    return note


@dataclass(frozen=True)
class ContactAttemptOutcome:
    attempt: ContactAttempt
    no_answer_streak: int
    #: True once the streak reaches the team's threshold. A recommendation, not
    #: an action: only a team with autoStop enabled gets an automatic stop.
    stop_recommended: bool


@transaction.atomic
def log_contact_attempt(
    *, actor, lead: Lead, result: str, note: str = "", attempted_at=None
) -> ContactAttemptOutcome:
    """Record one contact attempt.

    "ติดต่อไม่ได้" is a contact RESULT, not a lifecycle status
    (02_LOCKED_BUSINESS_RULES.md), so this never writes lifecycle_status unless
    the team explicitly opted into autoStop. Reaching the threshold otherwise
    only sets stop_recommended, leaving the decision to a human.
    """
    if result not in CONTACT_ATTEMPT_RESULTS:
        raise ValueError(f"UNKNOWN_CONTACT_ATTEMPT_RESULT:{result}")

    when = attempted_at or timezone.now()
    if when.tzinfo is None:
        raise ValueError("NAIVE_DATETIME:attempted_at")

    # Row lock BEFORE reading the streak: two concurrent NO_ANSWERs would
    # otherwise both read N and both write N+1, losing a count and delaying
    # the stop threshold.
    _lock(lead)
    _reject_if_stopped(lead, "contact attempt")

    active = FollowupCycle.objects.filter(lead=lead, status="ACTIVE").first()
    attempt = ContactAttempt.objects.create(
        lead=lead,
        cycle=active,
        user=actor if isinstance(actor, User) else None,
        attempted_at=when,
        result=result,
        note=(note or "").strip(),
    )

    streak = lead.no_answer_streak + 1 if result in NO_ANSWER_RESULTS else 0
    threshold = lead.team.max_no_answer_attempts
    stop_recommended = streak >= threshold

    lead.no_answer_streak = streak
    lead.save(update_fields=["no_answer_streak", "updated_at"])

    auto_stopped = False
    if stop_recommended and lead.team.auto_stop and lead.lifecycle_status != "STOPPED":
        # Through stop_lead, not an inline field assignment: that service also
        # ends the ACTIVE cycle with reason STOPPED, clears lock_active and
        # writes STATUS_CHANGE. Setting the fields here left a stopped lead
        # holding an active locked cycle, which pins ownership under
        # LATEST_ORDER_WITH_LOCK and keeps the lead out of reallocation
        # review because current_cycle_id stays non-null.
        from crm.leads.services import stop_lead

        # System-initiated: the team's autoStop policy is the authorization,
        # not the telesale who logged the attempt (they have no stop
        # authority). See stop_lead's enforce_permission docstring.
        stop_lead(
            actor=actor,
            lead=lead,
            reason="CONTACT_LIMIT_REACHED",
            enforce_permission=False,
        )
        auto_stopped = True

    LeadAuditEvent.objects.create(
        lead=lead,
        action="CONTACT_ATTEMPT_ADDED",
        actor=actor if isinstance(actor, User) else None,
        payload={
            "result": result,
            "no_answer_streak": streak,
            "threshold": threshold,
            "stop_recommended": stop_recommended,
            "auto_stopped": auto_stopped,
        },
    )
    return ContactAttemptOutcome(
        attempt=attempt, no_answer_streak=streak, stop_recommended=stop_recommended
    )


@transaction.atomic
def set_appointment(*, actor, lead: Lead, scheduled_at, note: str = "") -> Appointment:
    """Schedule a follow-up and refresh the lead's next_follow_at.

    next_follow_at tracks the SOONEST still-scheduled appointment, not the most
    recently created one: the work queue needs what is coming up next, and
    booking a later appointment must not push a nearer one out of view.
    """
    if scheduled_at.tzinfo is None:
        raise ValueError("NAIVE_DATETIME:scheduled_at")
    _lock(lead)
    _reject_if_stopped(lead, "appointment")

    active = FollowupCycle.objects.filter(lead=lead, status="ACTIVE").first()
    appointment = Appointment.objects.create(
        lead=lead,
        cycle=active,
        scheduled_at=scheduled_at,
        status="SCHEDULED",
        note=(note or "").strip(),
        created_by=actor if isinstance(actor, User) else None,
    )

    soonest = (
        Appointment.objects.filter(lead=lead, status="SCHEDULED")
        .order_by("scheduled_at")
        .values_list("scheduled_at", flat=True)
        .first()
    )
    lead.next_follow_at = soonest
    lead.save(update_fields=["next_follow_at", "updated_at"])

    LeadAuditEvent.objects.create(
        lead=lead,
        action="APPOINTMENT_SET",
        actor=actor if isinstance(actor, User) else None,
        payload={"appointment_id": appointment.id, "scheduled_at": scheduled_at.isoformat()},
    )
    return appointment


def _refresh_next_follow_at(lead: Lead) -> None:
    """Point the lead at its soonest still-SCHEDULED appointment, or nothing.

    Called after every appointment state change. Without it next_follow_at is
    sticky forever, and because ProtectionSummary.active_appointment is derived
    from it, one appointment ever set would exclude the lead from reallocation
    review permanently.
    """
    soonest = (
        Appointment.objects.filter(lead=lead, status="SCHEDULED")
        .order_by("scheduled_at")
        .values_list("scheduled_at", flat=True)
        .first()
    )
    lead.next_follow_at = soonest
    lead.save(update_fields=["next_follow_at", "updated_at"])


def _close_appointment(*, actor, appointment: Appointment, status: str) -> Appointment:
    # Lead lock first (it owns next_follow_at), then the appointment row, so two
    # closers serialize and the second sees the first's status.
    _lock(appointment.lead)
    Appointment.objects.select_for_update().filter(pk=appointment.pk).first()
    appointment.refresh_from_db()
    if appointment.status != "SCHEDULED":
        raise ValueError(f"APPOINTMENT_ALREADY_CLOSED:{appointment.status}")
    appointment.status = status
    appointment.save(update_fields=["status"])
    _refresh_next_follow_at(appointment.lead)
    LeadAuditEvent.objects.create(
        lead=appointment.lead,
        action="APPOINTMENT_SET",
        actor=actor if isinstance(actor, User) else None,
        payload={"appointment_id": appointment.id, "status": status},
    )
    return appointment


@transaction.atomic
def complete_appointment(*, actor, appointment: Appointment) -> Appointment:
    return _close_appointment(actor=actor, appointment=appointment, status="DONE")


@transaction.atomic
def cancel_appointment(*, actor, appointment: Appointment) -> Appointment:
    return _close_appointment(actor=actor, appointment=appointment, status="CANCELLED")


@transaction.atomic
def mark_appointment_missed(*, actor, appointment: Appointment) -> Appointment:
    return _close_appointment(actor=actor, appointment=appointment, status="MISSED")
