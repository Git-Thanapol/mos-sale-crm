"""Lead mutation endpoints.

Every one of these is the security surface 08_SECURITY_AND_AUTH_REQUIREMENTS.md
cares about: "Authorization must be enforced server-side. Hidden buttons are
not security controls." So each view re-derives the actor from request.user,
checks an object-level predicate, and ignores any actor/role/team the client
posted.

Rendering the follow-up page is Task 15; these are the writes.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from crm.core.permissions import (
    MSG_LEAD_OUT_OF_SCOPE,
    can_edit_lead,
    can_reassign_caretaker,
    can_resolve_intake,
    can_review_stopped,
)
from crm.domain.enums import LEAD_LIFECYCLE_STATUSES, STOP_REASONS
from crm.followcycles.models import Appointment
from crm.followcycles.services import (
    cancel_appointment,
    complete_appointment,
    log_contact_attempt,
    mark_appointment_missed,
    set_appointment,
)
from crm.followcycles.services import add_note as add_note_service
from crm.intake.models import IntakeItem
from crm.intake.services import resolve_intake_to_team
from crm.leads.models import Lead, LeadAuditEvent
from crm.leads.services import reactivate_lead, reassign_caretaker, stop_lead
from crm.teams.models import Team


def _lead(lead_id: int) -> Lead:
    return get_object_or_404(Lead.objects.select_related("customer", "team", "caretaker"), pk=lead_id)


def _back(request) -> str:
    """Return to the follow-up list, preserving the caller's filters.

    startswith("/") is not a safe test: "//evil.com" is a protocol-relative
    URL and the browser leaves the site. HttpResponseRedirect only rejects
    disallowed schemes, so Django does not catch it either.
    """
    nxt = request.POST.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return reverse("followups:list")


@login_required
@require_POST
def reassign(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_reassign_caretaker(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    raw = (request.POST.get("caretaker") or "").strip()
    caretaker = None
    if raw:
        from crm.accounts.models import User

        caretaker = User.objects.filter(pk=raw).first()
        if caretaker is None:
            return HttpResponseBadRequest("unknown caretaker")

    reassign_caretaker(
        actor=request.user,
        lead=lead,
        caretaker=caretaker,
        reason=(request.POST.get("reason") or "").strip() or "manual reassignment",
    )
    messages.success(request, "อัปเดตผู้ดูแลแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def stop(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_review_stopped(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    reason = (request.POST.get("reason") or "").strip()
    if reason not in STOP_REASONS:
        # STOPPED always carries a reason; a blank one is a bad request, not a
        # silently defaulted OTHER.
        return HttpResponseBadRequest("stop reason required")

    # The service ends the cycle, releases the lock and writes STATUS_CHANGE.
    stop_lead(actor=request.user, lead=lead, reason=reason)
    messages.success(request, "หยุดติดตามแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def reactivate(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_review_stopped(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    try:
        reactivate_lead(
            actor=request.user,
            lead=lead,
            reason=(request.POST.get("reason") or "").strip() or "reactivated",
        )
    except ValueError:
        return HttpResponseBadRequest("lead is not stopped")
    messages.success(request, "เปิดการติดตามอีกครั้ง")
    return redirect(_back(request))


@login_required
@require_POST
def log_attempt(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    try:
        log_contact_attempt(
            actor=request.user,
            lead=lead,
            result=(request.POST.get("result") or "").strip(),
            note=(request.POST.get("note") or "").strip(),
        )
    except ValueError:
        return HttpResponseBadRequest("invalid contact result")
    messages.success(request, "บันทึกผลการติดต่อแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def set_appointment_view(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    raw = (request.POST.get("scheduled_at") or "").strip()
    parsed = None
    if raw:
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(raw)
    if parsed is None:
        return HttpResponseBadRequest("scheduled_at required")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)

    set_appointment(
        actor=request.user, lead=lead, scheduled_at=parsed, note=(request.POST.get("note") or "")
    )
    messages.success(request, "บันทึกนัดติดตามแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def add_note(request, lead_id: int):
    lead = _lead(lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    try:
        add_note_service(actor=request.user, lead=lead, body=request.POST.get("body") or "")
    except ValueError:
        return HttpResponseBadRequest("note cannot be empty")
    messages.success(request, "บันทึกโน้ตแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def set_lifecycle(request, lead_id: int):
    """Set the user-editable lifecycle status.

    STOPPED is deliberately NOT reachable here: it carries a reason and ends
    the follow-up cycle, so it goes through leads:stop and its service.
    """
    lead = _lead(lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    status = (request.POST.get("lifecycle_status") or "").strip()
    if status not in LEAD_LIFECYCLE_STATUSES or status == "STOPPED":
        return HttpResponseBadRequest("invalid lifecycle status")

    previous = lead.lifecycle_status
    if previous != status:
        lead.lifecycle_status = status
        lead.save(update_fields=["lifecycle_status", "updated_at"])
        LeadAuditEvent.objects.create(
            lead=lead,
            action="STATUS_CHANGE",
            actor=request.user,
            payload={"from": previous, "to": status},
        )
    messages.success(request, "บันทึกสถานะแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def close_appointment(request, appointment_id: int):
    """Mark a scheduled appointment DONE / MISSED / CANCELLED.

    Without this an appointment could never leave SCHEDULED, so
    Lead.next_follow_at was permanent -- and because active_appointment is
    derived from it, one appointment ever set excluded the lead from
    reallocation review forever.
    """
    appointment = get_object_or_404(
        Appointment.objects.select_related("lead", "lead__team", "lead__customer"),
        pk=appointment_id,
    )
    if not can_edit_lead(request.user, appointment.lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    status = (request.POST.get("status") or "").strip()
    handler = {
        "DONE": complete_appointment,
        "CANCELLED": cancel_appointment,
        "MISSED": mark_appointment_missed,
    }.get(status)
    if handler is None:
        return HttpResponseBadRequest("invalid appointment status")
    try:
        handler(actor=request.user, appointment=appointment)
    except ValueError:
        return HttpResponseBadRequest("appointment already closed")
    messages.success(request, "อัปเดตนัดแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
def resolve_intake(request, intake_id: int):
    if not can_resolve_intake(request.user):
        raise PermissionDenied("หน้านี้เป็นระบบหลังบ้าน เฉพาะ EDITOR เท่านั้น")

    intake = get_object_or_404(IntakeItem.objects.select_related("customer"), pk=intake_id)
    team = Team.objects.filter(code=(request.POST.get("team") or "").strip()).first()
    if team is None:
        return HttpResponseBadRequest("unknown team")
    try:
        resolve_intake_to_team(actor=request.user, intake=intake, team=team)
    except ValueError:
        return HttpResponseBadRequest("intake already resolved")
    messages.success(request, "มอบหมายทีมแล้ว")
    return redirect(_back(request))
