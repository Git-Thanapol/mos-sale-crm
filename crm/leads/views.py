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
from functools import wraps

from django.http import HttpResponse, HttpResponseBadRequest
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
from crm.domain.enums import STOP_REASONS
from crm.followcycles.models import Appointment, FollowupCycle
from crm.followcycles.services import (
    cancel_appointment,
    complete_appointment,
    end_cycle,
    log_contact_attempt,
    mark_appointment_missed,
    set_appointment,
)
from crm.followcycles.services import add_note as add_note_service
from crm.intake.models import IntakeItem
from crm.intake.services import resolve_intake_to_team
from crm.leads.errors import InvalidCaretakerError, LeadStoppedError, StaleLeadError
from crm.leads.models import Lead
from crm.leads.services import (
    reactivate_lead,
    reassign_caretaker,
    stop_lead,
)
from crm.leads.services import set_lifecycle as set_lifecycle_service
from crm.teams.models import Team

MSG_STALE = "ข้อมูล Lead เปลี่ยนไปแล้ว กรุณาโหลดหน้าใหม่แล้วลองอีกครั้ง (stale version)"
MSG_STOPPED = "Lead นี้ถูก STOPPED แล้ว ต้องเปิดการติดตามอีกครั้ง (reactivate) ก่อน"


class _BadVersion(Exception):
    pass


def _lead(user, lead_id: int) -> Lead:
    """Resolve through the user scope so an out-of-scope id is a 404, never a
    403: a 403 would confirm that the lead exists (an existence oracle).
    In-scope but lacking the ACTION permission is still 403 in each view."""
    return get_object_or_404(
        Lead.objects.select_related("customer", "team", "caretaker").for_user(user), pk=lead_id
    )


def _expected_version(request) -> int | None:
    """The Lead.version the submitted form was rendered against, if sent.

    Absent means the caller did not send one (scripted POST): the write still
    runs under the row lock, it just cannot detect a stale form."""
    raw = (request.POST.get("version") or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise _BadVersion
    return int(raw)


def _conflict(message: str) -> HttpResponse:
    return HttpResponse(message, status=409, content_type="text/plain; charset=utf-8")


def handle_conflicts(view):
    """Map the optimistic-lock failure to 409 and a malformed version to 400."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except StaleLeadError:
            return _conflict(MSG_STALE)
        except _BadVersion:
            return HttpResponseBadRequest("invalid version")

    return wrapped


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
@handle_conflicts
def reassign(request, lead_id: int):
    lead = _lead(request.user, lead_id)
    if not can_reassign_caretaker(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    version = _expected_version(request)

    raw = (request.POST.get("caretaker") or "").strip()
    caretaker = None
    if raw:
        from crm.accounts.models import User

        # isdigit first: a non-numeric pk reaches the ORM and raises
        # ValueError("Field 'id' expected a number") before .first() can
        # return, which is a 500 rather than the bad request below.
        if not raw.isdigit():
            return HttpResponseBadRequest("unknown caretaker")
        # is_active is enforced HERE and again in the service (active, holds
        # leads, belongs to the lead team): a rendered dropdown is not a control.
        caretaker = User.objects.filter(pk=raw, is_active=True).first()
        if caretaker is None:
            return HttpResponseBadRequest("unknown caretaker")

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        return HttpResponseBadRequest("reason required")
    try:
        reassign_caretaker(
            actor=request.user,
            lead=lead,
            caretaker=caretaker,
            reason=reason,
            expected_version=version,
            manual=True,
        )
    except InvalidCaretakerError as exc:
        return HttpResponseBadRequest(f"invalid caretaker: {exc}")
    messages.success(request, "อัปเดตผู้ดูแลแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def stop(request, lead_id: int):
    lead = _lead(request.user, lead_id)
    if not can_review_stopped(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    version = _expected_version(request)

    reason = (request.POST.get("reason") or "").strip()
    if reason not in STOP_REASONS:
        # STOPPED always carries a reason; a blank one is a bad request, not a
        # silently defaulted OTHER.
        return HttpResponseBadRequest("stop reason required")

    # The service ends the cycle, releases the lock and writes STATUS_CHANGE.
    stop_lead(actor=request.user, lead=lead, reason=reason, expected_version=version)
    messages.success(request, "หยุดติดตามแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def reactivate(request, lead_id: int):
    lead = _lead(request.user, lead_id)
    if not can_review_stopped(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    version = _expected_version(request)

    try:
        reactivate_lead(
            actor=request.user,
            lead=lead,
            reason=(request.POST.get("reason") or "").strip() or "reactivated",
            expected_version=version,
        )
    except ValueError:
        return HttpResponseBadRequest("lead is not stopped")
    messages.success(request, "เปิดการติดตามอีกครั้ง")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def log_attempt(request, lead_id: int):
    lead = _lead(request.user, lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    try:
        log_contact_attempt(
            actor=request.user,
            lead=lead,
            result=(request.POST.get("result") or "").strip(),
            note=(request.POST.get("note") or "").strip(),
        )
    except LeadStoppedError:
        return _conflict(MSG_STOPPED)
    except ValueError:
        return HttpResponseBadRequest("invalid contact result")
    messages.success(request, "บันทึกผลการติดต่อแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def set_appointment_view(request, lead_id: int):
    lead = _lead(request.user, lead_id)
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

    try:
        set_appointment(
            actor=request.user, lead=lead, scheduled_at=parsed, note=(request.POST.get("note") or "")
        )
    except LeadStoppedError:
        return _conflict(MSG_STOPPED)
    messages.success(request, "บันทึกนัดติดตามแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def add_note(request, lead_id: int):
    lead = _lead(request.user, lead_id)
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
@handle_conflicts
def close_cycle(request, lead_id: int):
    """End the lead's active follow-up cycle (prototype "ปิดรอบติดตาม").

    Releases the ownership lock through the service; the lead's own lifecycle
    status is untouched, unlike STOP.
    """
    lead = _lead(request.user, lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)

    cycle = None
    if lead.current_cycle_id:
        cycle = FollowupCycle.objects.filter(pk=lead.current_cycle_id, status="ACTIVE").first()
    if cycle is None:
        return HttpResponseBadRequest("no active follow-up cycle")

    try:
        end_cycle(actor=request.user, cycle=cycle, reason="MANUAL_CLOSE")
    except ValueError:
        return HttpResponseBadRequest("no active follow-up cycle")
    messages.success(request, "ปิดรอบติดตามแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def set_lifecycle(request, lead_id: int):
    """Set the user-editable lifecycle status.

    STOPPED is deliberately NOT reachable here: it carries a reason and ends
    the follow-up cycle, so it goes through leads:stop and its service.
    """
    lead = _lead(request.user, lead_id)
    if not can_edit_lead(request.user, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    version = _expected_version(request)

    status = (request.POST.get("lifecycle_status") or "").strip()
    try:
        set_lifecycle_service(
            actor=request.user,
            lead=lead,
            status=status,
            reason=(request.POST.get("reason") or "").strip(),
            expected_version=version,
        )
    except LeadStoppedError:
        # Leaving STOPPED is a review decision, not a dropdown change.
        return _conflict(MSG_STOPPED)
    except ValueError:
        return HttpResponseBadRequest("invalid lifecycle status")
    messages.success(request, "บันทึกสถานะแล้ว")
    return redirect(_back(request))


@login_required
@require_POST
@handle_conflicts
def close_appointment(request, appointment_id: int):
    """Mark a scheduled appointment DONE / MISSED / CANCELLED.

    Without this an appointment could never leave SCHEDULED, so
    Lead.next_follow_at was permanent -- and because active_appointment is
    derived from it, one appointment ever set excluded the lead from
    reallocation review forever.
    """
    # Scoped through the user visible leads: out-of-scope is a 404 (no oracle).
    appointment = get_object_or_404(
        Appointment.objects.select_related("lead", "lead__team", "lead__customer").filter(
            lead__in=Lead.objects.for_user(request.user).values("pk")
        ),
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
@handle_conflicts
def resolve_intake(request, intake_id: int):
    if not can_resolve_intake(request.user):
        raise PermissionDenied("หน้านี้เป็นระบบหลังบ้าน เฉพาะ EDITOR เท่านั้น")

    intake = get_object_or_404(IntakeItem.objects.select_related("customer"), pk=intake_id)
    team = Team.objects.filter(code=(request.POST.get("team") or "").strip()).first()
    if team is None:
        return HttpResponseBadRequest("unknown team")
    try:
        resolve_intake_to_team(actor=request.user, intake=intake, team=team)
    except ValueError as exc:
        return HttpResponseBadRequest(
            "team inactive" if str(exc).startswith("TEAM_INACTIVE") else "intake already resolved"
        )
    messages.success(request, "มอบหมายทีมแล้ว")
    return redirect(_back(request))
