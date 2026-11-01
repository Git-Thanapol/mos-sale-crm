"""Read queries for follow-up cycles.

Scoped through Lead, which carries the StaffScopedQuerySet manager, so cycle
history cannot be read for a lead the user cannot see.
"""

from __future__ import annotations

from crm.followcycles.models import FollowupCycle, Note
from crm.leads.models import Lead


def cycle_history(user, lead: Lead) -> list[FollowupCycle]:
    """Every cycle for the lead, newest first.

    Deliberately unfiltered by status: historical cycles may be hidden from
    the default daily work view, but they must stay retrievable
    (11_DO_NOT_CHANGE_CHECKLIST: historical follow-up cycles are not deleted).
    """
    if not Lead.objects.for_user(user).filter(pk=lead.pk).exists():
        return []
    return list(
        FollowupCycle.objects.filter(lead=lead)
        .select_related("trigger_order")
        .order_by("-started_at", "-id")
    )


def notes_for_lead(user, lead: Lead, *, limit: int = 100) -> list[Note]:
    if not Lead.objects.for_user(user).filter(pk=lead.pk).exists():
        return []
    return list(
        Note.objects.filter(lead=lead).select_related("created_by").order_by("-created_at", "-id")[:limit]
    )
