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

from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.domain.access import derive_lead_access
from crm.domain.ownership import (
    OwnershipDecision,
    OwnershipOrderFacts,
    derive_routing,
    resolve_ownership,
)
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment


def _active_user_ids() -> frozenset[str]:
    """The authority on who may hold ownership, read fresh on every call."""
    return frozenset(str(pk) for pk in User.objects.filter(is_active=True).values_list("pk", flat=True))


def _order_facts(order) -> OwnershipOrderFacts:
    """Project a crm_order row onto the pure ownership input.

    The closer is identified by `staff_code`, which is how this schema records
    who closed an order; it is resolved to a User id because ownership is held
    by a user, not by a code that may be reassigned. An order whose staff_code
    matches no active user yields a null closer, which the policies treat as a
    marketplace order — it never clears an existing caretaker.

    `ordered_at` uses order_date, the business date. 11_DO_NOT_CHANGE_CHECKLIST
    requires orderedAt to stay the business-time authority, so import or
    created_at timestamps must not be substituted here.
    """
    closer_id = None
    closer_team_code = None
    staff_code = (order.staff_code or "").strip()
    if staff_code:
        closer = User.objects.filter(staff_code=staff_code).order_by("-is_active", "pk").first()
        if closer is not None:
            closer_id = str(closer.pk)
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
    lead.save(
        update_fields=[
            "caretaker",
            "routing_state",
            "ownership_started_at",
            "access_team_ids",
            "access_user_ids",
            "updated_at",
        ]
    )

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


@transaction.atomic
def apply_order_to_lead(*, actor, lead: Lead, order, lock_active: bool) -> OwnershipDecision:
    """Resolve ownership for one order and persist the result.

    Deliberately does NOT filter by order status: the reference
    implementation's resolveOwnership does not, and excluding cancelled or
    COD-returned orders is the missed-channel rule
    (crm/domain/missed_channel.py). Applying it here too would double-count the
    exclusion and lose the fact that the order did move ownership when closed.
    """
    active_user_ids = _active_user_ids()
    decision = resolve_ownership(
        policy=lead.team.ownership_policy,
        team_code=lead.team.code,
        current_caretaker_user_id=str(lead.caretaker_id) if lead.caretaker_id else None,
        ownership_started_at=lead.ownership_started_at,
        lifecycle_status=lead.lifecycle_status,
        lock_active=lock_active,
        order=_order_facts(order),
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
def reassign_caretaker(*, actor, lead: Lead, caretaker: User | None, reason: str) -> Lead:
    """The only manual ownership write path.

    A caretaker who is not active is stored as no caretaker at all, so the lead
    routes to TEAM_UNASSIGNED instead of pointing at an account that cannot
    act (08_SECURITY: a disabled user must not retain caretaker authority).
    """
    return _apply_caretaker(
        actor=actor,
        lead=lead,
        caretaker=caretaker,
        started_at=timezone.now(),
        reason="MANUAL_REASSIGN",
        audit_reason=reason,
        active_user_ids=_active_user_ids(),
    )
