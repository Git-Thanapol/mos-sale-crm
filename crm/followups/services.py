from __future__ import annotations

from django.core.exceptions import PermissionDenied

from crm.core.identity import clean
from crm.core.permissions import can_edit_customer_lead
from crm.followups.models import Followup


def save_followup(user, followup: Followup, *, lead_status: str, status: str, priority: str,
                   next_followup_date, clear_date: bool, note: str) -> Followup:
    """The single write path for the follow-up edit form. Permission is
    re-checked here (not just at the route level) because this function
    is also the future entry point for the Customer 360 follow-up form
    and the Customers page follow-marker action — see
    docs/legacy/data-layer-report.md, all three legacy call sites shared
    this exact rule but re-implemented it inline each time.
    """
    if not can_edit_customer_lead(user, followup.customer):
        raise PermissionDenied("ไม่มีสิทธิ์แก้ไขรายการนี้")

    followup.lead_status = lead_status
    followup.status = status
    followup.priority = priority
    followup.next_followup_date = None if clear_date else next_followup_date
    followup.note = note
    followup.updated_by = clean(getattr(user, "email", ""))
    followup.save()  # Followup.save() recomputes priority_rank
    return followup
