"""Recompute a Lead's missed-channel counter from stored Order rows.

The persistence adapter around crm/domain/missed_channel.py. Its whole job is
the translation the pure function must not know about: map this repo's
free-text order_status onto machine codes, resolve the closer from staff_code,
and choose the baseline.

Baseline precedence, highest first:
1. `hx63_baseline`, when the caller is recomputing a migrated lead (D023).
2. The start of the lead's open ownership segment — a caretaker change moves
   this, which is how a reassignment resets the counter without anything
   decrementing it.
3. The lead's creation time. A lead with no ownership history must not count
   its customer's entire order history (Review Focus #5).
"""

from __future__ import annotations

import logging
from datetime import datetime, time

from crm.accounts.models import User
from crm.domain.bangkok import BANGKOK_TZ
from crm.domain.missed_channel import MissedChannelOrder, recompute_missed_channel
from crm.leads.models import Lead, OwnershipSegment
from crm.leads.order_status import to_machine_status
from crm.orders.models import Order

logger = logging.getLogger(__name__)


def _ordered_at(order: Order) -> datetime | None:
    """The business time of the order.

    DATE-ONLY DECISION (audit item 4): Order.order_date is a DateField, so the
    true time of day is unknown and is NOT invented. Anchoring at Bangkok
    midnight gives a deterministic, conservative comparison against a baseline
    or segment-start instant: an order dated the same Bangkok day as a baseline
    that falls after midnight (e.g. HX63 19:31 Bangkok, or a mid-day
    reassignment) is treated as BEFORE it and not counted, because we cannot
    prove it happened after. Orders on later days count. A baseline that is
    itself a Bangkok day start (the backfill's) includes that whole day. Revisit
    only if Order gains a real datetime.

    11_DO_NOT_CHANGE_CHECKLIST: orderedAt remains the business-time authority,
    so import time and created_at must never be substituted. order_date is a
    DateField, so it is anchored at Bangkok midnight to become an instant.
    """
    if order.order_date is None:
        return None
    return datetime.combine(order.order_date, time.min, tzinfo=BANGKOK_TZ)


def _baseline_for(lead: Lead, hx63_baseline: datetime | None) -> datetime:
    """Counting start instant.

    D023 must survive every later recompute, so the durable
    `Lead.migrated_baseline_at` is consulted, not just the transient argument.
    A migrated lead's open segment is the BACKFILL segment opened at cutover
    (later than the HX63 snapshot), so for it the snapshot baseline wins:
    orders between the snapshot and the cutover still count. Once a real
    ownership change opens a newer segment, that segment's start wins when it is
    later (a reassignment resets the counter), i.e. max(baseline, segment start).
    """
    if hx63_baseline is not None:
        return hx63_baseline
    open_segment = (
        OwnershipSegment.objects.filter(lead=lead, ended_at__isnull=True)
        .order_by("-started_at")
        .values_list("started_at", "reason")
        .first()
    )
    migrated = lead.migrated_baseline_at
    if open_segment is not None:
        started_at, reason = open_segment
        if migrated is not None:
            if reason == "BACKFILL":
                return migrated
            return max(migrated, started_at)
        return started_at
    if migrated is not None:
        return migrated
    return lead.ownership_started_at or lead.created_at


def _staff_code_to_user_id() -> dict[str, str]:
    """staff_code -> user id, preferring an active user when codes collide.

    The closer is held by a user, not by a code that may be reassigned. A
    staff_code matching nobody yields a null closer, which the policy treats
    as a marketplace order.
    """
    mapping: dict[str, str] = {}
    for pk, staff_code, is_active in (
        User.objects.exclude(staff_code="")
        .order_by("-is_active", "pk")
        .values_list("pk", "staff_code", "is_active")
    ):
        mapping.setdefault(staff_code.strip(), str(pk))
    return mapping


def recompute_lead_missed_channel(
    *,
    lead: Lead,
    hx63_baseline: datetime | None = None,
    closer_ids: dict[str, str] | None = None,
) -> int:
    """Recompute, persist and return the lead's missed-channel count.

    `closer_ids` lets a batch caller build the staff_code map once. Without it
    this scans the whole User table per call, which is one full scan per lead
    inside a loop.
    """
    baseline = _baseline_for(lead, hx63_baseline)
    closer_ids = closer_ids if closer_ids is not None else _staff_code_to_user_id()

    orders: list[MissedChannelOrder] = []
    unmapped: set[str] = set()
    rows = Order.objects.filter(customer_id=lead.customer_id).values_list(
        "pk", "order_date", "staff_code", "order_status"
    )
    for pk, order_date, staff_code, order_status in rows:
        machine_status = to_machine_status(order_status)
        if machine_status is None:
            # Deferred, not guessed: excluded from the count and surfaced.
            unmapped.add((order_status or "").strip())
            continue
        if order_date is None:
            # 07_MIGRATION_AND_DATA_REQUIREMENTS: do not manufacture orderedAt.
            continue
        orders.append(
            MissedChannelOrder(
                order_id=str(pk),
                ordered_at=datetime.combine(order_date, time.min, tzinfo=BANGKOK_TZ),  # see _ordered_at: date-only
                closer_user_id=closer_ids.get((staff_code or "").strip()),
                order_status=machine_status,
            )
        )

    if unmapped:
        logger.warning(
            "lead %s: %d order status value(s) unmapped and excluded from missed-channel: %s",
            lead.pk,
            len(unmapped),
            sorted(unmapped),
        )

    result = recompute_missed_channel(
        baseline_started_at=baseline,
        caretaker_user_id=str(lead.caretaker_id) if lead.caretaker_id else None,
        orders=orders,
        policy=lead.team.missed_channel_policy,
    )

    if lead.missed_channel_count != result.count:
        lead.missed_channel_count = result.count
        lead.save(update_fields=["missed_channel_count", "updated_at"])
    return result.count
