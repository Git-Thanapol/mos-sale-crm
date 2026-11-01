"""Keep Lead state current when an order is written.

Without this, every derived value on the Lead is frozen at backfill time:
`last_order_at`, `total_orders` and `missed_channel_count` drift as real orders
arrive, ownership never follows a LATEST_ORDER closer, and no follow-up cycle
is ever opened — so `current_cycle_id` stays NULL, the follow-up protection
filter is inert, and every note and attempt gets `cycle=None`. The page would
display authoritative-looking numbers with no refresh path, which is worse than
showing none.

The full import pipeline is Plan 2. This is the minimum that keeps the Plan 1
display honest for orders written through the paths that exist today.

Deliberately forgiving: a repo without Leads yet (before `backfill_leads` has
run) must keep saving orders exactly as before, so every entry point here is a
no-op when the customer has no Lead.
"""

from __future__ import annotations

import logging

from crm.leads.models import Lead

logger = logging.getLogger(__name__)


def sync_leads_for_order(*, actor, order, open_new_cycle: bool = True) -> int:
    """Apply one order to every Lead of its customer. Returns leads touched.

    A customer may hold a Lead per team, and an order is a fact about the
    customer, so each of their Leads re-resolves ownership and recomputes its
    own counter against its own baseline.

    Never raises into the order write path: a derived-state refresh failing
    must not lose the order itself, which is the authoritative record. Failures
    are logged for `reconcile_lead_access`-style repair instead.
    """
    from crm.followcycles.models import FollowupCycle
    from crm.followcycles.services import open_cycle
    from crm.leads.recompute import recompute_lead_missed_channel
    from crm.leads.services import apply_order_to_lead

    leads = list(
        Lead.objects.select_related("team", "caretaker").filter(customer_id=order.customer_id)
    )
    if not leads:
        return 0

    touched = 0
    for lead in leads:
        try:
            lock_active = FollowupCycle.objects.filter(
                lead=lead, status="ACTIVE", lock_active=True
            ).exists()
            apply_order_to_lead(actor=actor, lead=lead, order=order, lock_active=lock_active)
            _refresh_order_rollups(lead=lead, order=order)
            recompute_lead_missed_channel(lead=lead)
            if open_new_cycle and lead.lifecycle_status != "STOPPED":
                # A new order starts a new follow-up cycle; the previous one is
                # ended with NEW_ORDER rather than deleted.
                open_cycle(actor=actor, lead=lead, trigger_order=order)
            touched += 1
        except Exception:
            logger.exception(
                "lead %s: failed to sync derived state for order %s", lead.pk, order.pk
            )
    return touched


def _refresh_order_rollups(*, lead: Lead, order) -> None:
    """Recompute last_order_at / last_order_id / total_orders from the rows.

    Recomputed rather than incremented, for the same reason the missed-channel
    counter is: a re-saved or merged order must not inflate the count.
    """
    from datetime import datetime, time

    from django.db.models import Count, Max

    from crm.domain.bangkok import BANGKOK_TZ
    from crm.orders.models import Order

    stats = Order.objects.filter(customer_id=lead.customer_id).aggregate(
        newest=Max("order_date"), total=Count("pk")
    )
    newest = stats["newest"]
    lead.last_order_at = (
        datetime.combine(newest, time.min, tzinfo=BANGKOK_TZ) if newest is not None else None
    )
    lead.last_order_id = order.pk
    lead.total_orders = stats["total"] or 0
    lead.save(update_fields=["last_order_at", "last_order_id", "total_orders", "updated_at"])
