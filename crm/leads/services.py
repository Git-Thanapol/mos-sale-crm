"""The only write paths for Lead ownership.

08_SECURITY_AND_AUTH_REQUIREMENTS.md requires a caretaker change to be
server-mediated and to update the ownership baseline, the authorization read
model, the audit trail and missed-channel semantics together. Each function
here does all of that inside one `transaction.atomic()`, so a partial
ownership change — a new segment with stale access arrays, or a moved baseline
with no audit row — cannot be committed.

`active_user_ids` is always derived here from the database, never accepted as
an argument: a caller passing a stale set could re-grant access to a disabled
account, which is exactly what 08_SECURITY forbids.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.core.permissions import MSG_LEAD_OUT_OF_SCOPE
from crm.domain.access import derive_lead_access
from crm.domain.ownership import (
    OwnershipDecision,
    OwnershipOrderFacts,
    derive_routing,
    resolve_ownership,
)
from crm.leads.errors import InvalidCaretakerError, LeadStoppedError, StaleLeadError
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment

__all__ = [
    "InvalidCaretakerError",
    "LeadStoppedError",
    "StaleLeadError",
    "apply_order_to_lead",
    "lock_lead",
    "order_facts",
    "reactivate_lead",
    "reassign_caretaker",
    "reconcile_lead_access",
    "set_lifecycle",
    "stop_lead",
    "validate_caretaker_target",
]


def _active_user_ids() -> frozenset[str]:
    """The authority on who may hold ownership, read fresh on every call."""
    return frozenset(str(pk) for pk in User.objects.filter(is_active=True).values_list("pk", flat=True))


def lock_lead(lead: Lead, expected_version: int | None = None) -> Lead:
    """Take the row lock on `lead`, reload it, and check the optimistic version.

    Must run inside a transaction. The caller's instance is refreshed in place
    so every later decision (current caretaker, lifecycle, streak) is made from
    committed state rather than from whatever the form was rendered against.

    `expected_version` is the Lead.version the user's form carried. None means
    an internal caller (sync / import) that wants the lock but has no form to
    compare; a stale form is rejected with StaleLeadError.
    """
    exists = Lead.objects.select_for_update().filter(pk=lead.pk).values_list("pk", flat=True).first()
    if exists is None:
        raise Lead.DoesNotExist(f"lead {lead.pk} no longer exists")
    lead.refresh_from_db()
    if expected_version is not None and lead.version != expected_version:
        raise StaleLeadError(
            f"lead {lead.pk} changed (form version {expected_version}, current {lead.version})"
        )
    return lead


def closer_user_id_for(staff_code: str | None) -> str | None:
    """Resolve an order's staff_code to a User id (one query)."""
    code = (staff_code or "").strip()
    if not code:
        return None
    pk = (
        User.objects.filter(staff_code=code)
        .order_by("-is_active", "pk")
        .values_list("pk", flat=True)
        .first()
    )
    return str(pk) if pk is not None else None


def validate_caretaker_target(caretaker: User, lead: Lead) -> User:
    """Can `caretaker` hold `lead`? Raises InvalidCaretakerError if not.

    Re-reads the user (a stale object must not smuggle in a disabled account)
    and requires a lead-holding role belonging to the lead's team: a telesale
    needs an open TeamAssignment for that team, a TEAM_LEAD needs the team in
    their scope. Any other role (EDITOR, ADMIN, read-only) does not hold leads.
    """
    from crm.core.permissions import current_team_codes, is_team_lead, is_telesell, team_scope

    fresh = User.objects.filter(pk=caretaker.pk, is_active=True).first()
    if fresh is None:
        raise InvalidCaretakerError("CARETAKER_INACTIVE")
    team_code = lead.team.code
    if is_telesell(fresh):
        if team_code not in current_team_codes(fresh):
            raise InvalidCaretakerError("CARETAKER_NOT_IN_TEAM")
    elif is_team_lead(fresh):
        if team_code not in team_scope(fresh):
            raise InvalidCaretakerError("CARETAKER_NOT_IN_TEAM")
    else:
        raise InvalidCaretakerError("CARETAKER_ROLE_CANNOT_HOLD_LEADS")
    return fresh


def order_facts(order, closer_user_id: str | None = None) -> OwnershipOrderFacts:
    """Project a crm_order row onto the pure ownership input.

    Callers applying one order to several leads pass `closer_user_id` (resolved
    once via closer_user_id_for) or the finished facts to apply_order_to_lead;
    resolving per lead was an N+1 User query per order.

    The closer is identified by `staff_code`, which is how this schema records
    who closed an order; it is resolved to a User id because ownership is held
    by a user, not by a code that may be reassigned. An order whose staff_code
    matches no active user yields a null closer, which the policies treat as a
    marketplace order — it never clears an existing caretaker.

    `ordered_at` uses order_date, the business date. 11_DO_NOT_CHANGE_CHECKLIST
    requires orderedAt to stay the business-time authority, so import or
    created_at timestamps must not be substituted here.
    """
    closer_id = closer_user_id if closer_user_id is not None else closer_user_id_for(order.staff_code)
    closer_team_code = None
    ordered_at = order.order_date
    if ordered_at is not None and not hasattr(ordered_at, "tzinfo"):
        # order_date is a DateField; anchor it at Bangkok midnight so the
        # ownership baseline is a real instant rather than a naive date.
        from crm.domain.bangkok import BANGKOK_TZ
        from datetime import datetime, time

        ordered_at = datetime.combine(ordered_at, time.min, tzinfo=BANGKOK_TZ)
    return OwnershipOrderFacts(
        order_id=str(order.pk),
        ordered_at=ordered_at or timezone.now(),
        closer_user_id=closer_id,
        closer_team_code=closer_team_code,
    )


#: Backwards-compatible private alias.
_order_facts = order_facts


def _apply_caretaker(
    *,
    actor,
    lead: Lead,
    caretaker: User | None,
    started_at,
    reason: str,
    audit_reason: str,
    active_user_ids: frozenset[str],
    extra_payload: dict | None = None,
) -> Lead:
    """Close the open segment, open a new one, recompute access, audit.

    Shared by the order-driven and the manual path so there is exactly one
    implementation of "ownership changed".
    """
    previous_caretaker_id = lead.caretaker_id
    now = timezone.now()

    # ONE boundary instant for both sides of the handover: the old segment ends
    # exactly where the new one begins. Computing `now` twice left a sub-
    # millisecond gap in which the lead had two overlapping segments, which
    # makes "the current ownership baseline" ambiguous and the missed-channel
    # count non-deterministic.
    boundary = started_at or now

    # An ownership segment cannot begin before the segment it replaces. It can
    # try to: `order_date` is a DateField, so an order's business time is
    # anchored at Bangkok midnight and may precede the start of the ownership
    # it supersedes. Clamping keeps the history monotonic without inventing a
    # time the order does not have.
    previous_open = OwnershipSegment.objects.filter(lead=lead, ended_at__isnull=True).first()
    if previous_open is not None and boundary < previous_open.started_at:
        boundary = previous_open.started_at

    OwnershipSegment.objects.filter(lead=lead, ended_at__isnull=True).update(ended_at=boundary)

    caretaker_active = caretaker is not None and str(caretaker.pk) in active_user_ids
    effective_caretaker = caretaker if caretaker_active else None

    OwnershipSegment.objects.create(
        lead=lead,
        caretaker=effective_caretaker,
        started_at=boundary,
        reason=reason,
        created_by=actor if isinstance(actor, User) else None,
    )

    routing = derive_routing(
        team_code=lead.team.code,
        caretaker_user_id=str(effective_caretaker.pk) if effective_caretaker else None,
        caretaker_active=caretaker_active,
        team_lead_configured=lead.team.team_lead_configured,
    )
    access = derive_lead_access(
        lead.team.code,
        str(effective_caretaker.pk) if effective_caretaker else None,
        active_user_ids,
    )

    lead.caretaker = effective_caretaker
    lead.routing_state = routing.routing_state
    lead.ownership_started_at = boundary
    lead.access_team_ids = access["access_team_ids"]
    lead.access_user_ids = access["access_user_ids"]
    lead.version = (lead.version or 0) + 1
    lead.save(
        update_fields=[
            "caretaker",
            "routing_state",
            "ownership_started_at",
            "access_team_ids",
            "access_user_ids",
            "version",
            "updated_at",
        ]
    )
    # The baseline just moved (new open segment), so the stored missed-channel
    # count is stale until recomputed. Same transaction: reassign,
    # reconcile_lead_access and order-driven changes all keep it correct.
    from crm.leads.recompute import recompute_lead_missed_channel

    recompute_lead_missed_channel(lead=lead)

    LeadAuditEvent.objects.create(
        lead=lead,
        action="OWNERSHIP_CHANGE",
        actor=actor if isinstance(actor, User) else None,
        reason=audit_reason,
        payload={
            "from": str(previous_caretaker_id) if previous_caretaker_id else None,
            "to": str(effective_caretaker.pk) if effective_caretaker else None,
            "reason": reason,
            "routing_state": routing.routing_state,
            "queue": routing.queue,
            "escalated_to_admin": routing.escalated_to_admin,
            **(extra_payload or {}),
        },
    )
    return lead


def _order_is_eligible(order, facts: OwnershipOrderFacts, lead: Lead) -> bool:
    from crm.domain.enums import ORDER_STATUSES
    from crm.domain.ownership import is_order_eligible_for_ownership
    from crm.leads.order_status import to_machine_status

    raw = (getattr(order, "order_status", "") or "").strip()
    status = to_machine_status(raw)
    if status is None and raw in ORDER_STATUSES:
        status = raw  # already a machine code
    # order_date is a DateField anchored at Bangkok midnight, so a same-day order
    # always sorts before the (instant-valued) segment start. Staleness is
    # therefore judged on the Bangkok DAY of the segment start.
    started = lead.ownership_started_at
    if started is not None:
        from crm.domain.bangkok import day_start, epoch_day

        started = day_start(epoch_day(started))
    return is_order_eligible_for_ownership(status, facts.ordered_at, started)


@transaction.atomic
def apply_order_to_lead(
    *, actor, lead: Lead, order, lock_active: bool, facts: OwnershipOrderFacts | None = None
) -> OwnershipDecision:
    """Resolve ownership for one order and persist the result.

    Orders that cannot move ownership (cancelled, COD-returned, unmapped status,
    or dated before the current ownership segment) are dropped BEFORE
    resolve_ownership via is_order_eligible_for_ownership; the missed-channel
    rule applies its own exclusions separately (crm/domain/missed_channel.py).
    """
    lock_lead(lead)
    active_user_ids = _active_user_ids()
    order_input = facts if facts is not None else order_facts(order)
    if not _order_is_eligible(order, order_input, lead):
        # Cancelled / COD-returned / unmapped / stale orders never move
        # ownership. Nothing is persisted: no segment, no audit row.
        return OwnershipDecision(
            caretaker_user_id=str(lead.caretaker_id) if lead.caretaker_id else None,
            changed=False,
            new_segment_started_at=None,
            reason="ORDER_NOT_ELIGIBLE",
            conflict=None,
        )
    decision = resolve_ownership(
        policy=lead.team.ownership_policy,
        team_code=lead.team.code,
        current_caretaker_user_id=str(lead.caretaker_id) if lead.caretaker_id else None,
        ownership_started_at=lead.ownership_started_at,
        lifecycle_status=lead.lifecycle_status,
        lock_active=lock_active,
        order=order_input,
        active_user_ids=active_user_ids,
    )

    if not decision.changed:
        # Nothing to persist. A no-op must not write a segment or an audit row,
        # or every re-import would inflate the ownership history.
        return decision

    caretaker = (
        User.objects.filter(pk=decision.caretaker_user_id).first()
        if decision.caretaker_user_id
        else None
    )
    _apply_caretaker(
        actor=actor,
        lead=lead,
        caretaker=caretaker,
        started_at=decision.new_segment_started_at,
        reason=decision.reason,
        audit_reason=f"order {order.pk}",
        active_user_ids=active_user_ids,
        extra_payload={"order_id": str(order.pk), "policy": lead.team.ownership_policy},
    )
    return decision


@transaction.atomic
def reassign_caretaker(
    *,
    actor,
    lead: Lead,
    caretaker: User | None,
    reason: str,
    expected_version: int | None = None,
    manual: bool = False,
) -> Lead:
    """The only manual ownership write path.

    A caretaker who is not active is stored as no caretaker at all, so the lead
    routes to TEAM_UNASSIGNED instead of pointing at an account that cannot
    act (08_SECURITY: a disabled user must not retain caretaker authority).

    `manual=True` is the human path (reassign view, customer owner assignment):
    a non-empty reason is required and the target must be an active user with
    a lead-holding role in the lead's team. System callers (import commit)
    leave it False.
    """
    lock_lead(lead, expected_version)
    if manual:
        if not (reason or "").strip():
            raise ValueError("REASON_REQUIRED")
        if caretaker is not None:
            caretaker = validate_caretaker_target(caretaker, lead)
    return _apply_caretaker(
        actor=actor,
        lead=lead,
        caretaker=caretaker,
        started_at=timezone.now(),
        reason="MANUAL_REASSIGN",
        audit_reason=(reason or "").strip() or "manual reassignment",
        active_user_ids=_active_user_ids(),
    )


@transaction.atomic
def stop_lead(
    *,
    actor,
    lead: Lead,
    reason: str,
    enforce_permission: bool = True,
    expected_version: int | None = None,
) -> Lead:
    """Move a lead to STOPPED, audited, with its cycle and lock released.

    STOPPED is a lifecycle state that always carries a reason
    (02_LOCKED_BUSINESS_RULES.md). Permission is re-checked here and not only
    at the route, per the plan's Global Constraints.

    The cycle is ended with reason STOPPED and its lock cleared: a stopped
    lead holding an ACTIVE cycle with lock_active would keep ownership pinned
    under LATEST_ORDER_WITH_LOCK on work nobody is doing.

    `enforce_permission=False` is for the ONE system-initiated caller: the
    autoStop branch of log_contact_attempt. There the authorization is the
    team's own policy (an operator set autoStop and maxNoAnswerAttempts), and
    the actor is merely whichever telesale happened to log the attempt that
    crossed the threshold -- a telesale has no stop authority, so enforcing it
    there would make the policy unreachable. Every human path keeps the check.
    """
    from crm.core.permissions import can_review_stopped
    from crm.domain.enums import STOP_REASONS

    lock_lead(lead, expected_version)
    if enforce_permission and not can_review_stopped(actor, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    if reason not in STOP_REASONS:
        raise ValueError(f"UNKNOWN_STOP_REASON:{reason}")

    previous = lead.lifecycle_status
    now = timezone.now()

    # Imported here: crm.followcycles imports crm.leads.models, so a
    # module-level import would be circular.
    from crm.followcycles.models import FollowupCycle

    for cycle in FollowupCycle.objects.select_for_update().filter(lead=lead, status="ACTIVE"):
        cycle.status = "ENDED"
        cycle.ended_at = now
        cycle.ended_reason = "STOPPED"
        cycle.lock_active = False
        cycle.save(update_fields=["status", "ended_at", "ended_reason", "lock_active"])

    lead.lifecycle_status = "STOPPED"
    lead.stopped_reason = reason
    lead.stopped_at = now
    lead.current_cycle_id = None
    lead.version = (lead.version or 0) + 1
    lead.save(
        update_fields=[
            "lifecycle_status",
            "stopped_reason",
            "stopped_at",
            "current_cycle_id",
            "version",
            "updated_at",
        ]
    )

    LeadAuditEvent.objects.create(
        lead=lead,
        action="STATUS_CHANGE",
        actor=actor if isinstance(actor, User) else None,
        reason=reason,
        payload={"from": previous, "to": "STOPPED", "stopped_reason": reason},
    )
    return lead


@transaction.atomic
def reactivate_lead(
    *, actor, lead: Lead, reason: str, expected_version: int | None = None
) -> Lead:
    """Return a STOPPED lead to FOLLOWING, audited.

    Reactivation is an authorized human decision: a STOPPED lead is never
    silently revived (see crm.domain.ownership.evaluate_stopped_reactivation,
    which is what the order path consults).
    """
    from crm.core.permissions import can_review_stopped

    lock_lead(lead, expected_version)
    if not can_review_stopped(actor, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    if lead.lifecycle_status != "STOPPED":
        raise ValueError("LEAD_NOT_STOPPED")

    previous = lead.lifecycle_status
    lead.lifecycle_status = "FOLLOWING"
    lead.stopped_reason = ""
    lead.stopped_at = None
    lead.version = (lead.version or 0) + 1
    lead.save(
        update_fields=["lifecycle_status", "stopped_reason", "stopped_at", "version", "updated_at"]
    )

    LeadAuditEvent.objects.create(
        lead=lead,
        action="STATUS_CHANGE",
        actor=actor if isinstance(actor, User) else None,
        reason=reason,
        payload={"from": previous, "to": "FOLLOWING"},
    )
    return lead


@transaction.atomic
def set_lifecycle(
    *,
    actor,
    lead: Lead,
    status: str,
    reason: str = "",
    expected_version: int | None = None,
) -> Lead:
    """Set a user-editable lifecycle status, audited with a recorded reason.

    STOPPED is neither a legal target (it carries a stop reason and ends the
    cycle: use stop_lead) nor a legal source: leaving STOPPED is an authorized
    review decision, so it must go through reactivate_lead, which re-checks
    can_review_stopped. Otherwise a plain editor could un-stop a lead through
    the generic status dropdown. `reason` defaults to "manual status change".
    """
    from crm.core.permissions import can_edit_lead
    from crm.domain.enums import LEAD_LIFECYCLE_STATUSES

    lock_lead(lead, expected_version)
    if not can_edit_lead(actor, lead):
        raise PermissionDenied(MSG_LEAD_OUT_OF_SCOPE)
    if status not in LEAD_LIFECYCLE_STATUSES or status == "STOPPED":
        raise ValueError("INVALID_LIFECYCLE_STATUS")
    if lead.lifecycle_status == "STOPPED":
        raise LeadStoppedError("LEAD_STOPPED_USE_REACTIVATE")

    previous = lead.lifecycle_status
    if previous == status:
        return lead
    lead.lifecycle_status = status
    lead.version = (lead.version or 0) + 1
    lead.save(update_fields=["lifecycle_status", "version", "updated_at"])
    LeadAuditEvent.objects.create(
        lead=lead,
        action="STATUS_CHANGE",
        actor=actor if isinstance(actor, User) else None,
        reason=(reason or "").strip() or "manual status change",
        payload={"from": previous, "to": status},
    )
    return lead


@transaction.atomic
def reconcile_lead_access(*, actor=None, user: User) -> int:
    """Re-route and re-scope every lead this user caretakes.

    Called when a user is deactivated. 08_SECURITY requires a disabled or
    resigned user to lose caretaker authority immediately; without this, the
    lead keeps routing_state=ASSIGNED and the resigned person's work never
    reaches the Team Unassigned queue, so nobody picks it up.
    """
    affected = 0
    for lead in Lead.objects.select_related("team").filter(caretaker=user):
        lock_lead(lead)
        if lead.caretaker_id != user.pk:
            continue  # reassigned by someone else while this was queued
        _apply_caretaker(
            actor=actor,
            lead=lead,
            caretaker=user,
            started_at=lead.ownership_started_at or timezone.now(),
            reason="USER_DEACTIVATED",
            audit_reason=f"user {user.pk} deactivated",
            active_user_ids=_active_user_ids(),
        )
        affected += 1
    return affected
