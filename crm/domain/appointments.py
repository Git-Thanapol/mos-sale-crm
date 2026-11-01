"""Computed appointment state, in Bangkok calendar days.

02_LOCKED_BUSINESS_RULES.md fixes the timezone and the three computed states.
The comparison is between calendar DAYS, not instants: an appointment at 09:00
is still today's appointment at 17:00, and calling it OVERDUE would send a
salesperson chasing something they are about to do.

Computed state coexists with the lifecycle status; it never overwrites it.
"""

from __future__ import annotations

from datetime import datetime

from crm.domain.bangkok import epoch_day


def appointment_state(scheduled_at: datetime | None, now: datetime) -> str | None:
    """APPOINTMENT_TODAY / APPOINTMENT_FUTURE / OVERDUE, or None if unscheduled."""
    if scheduled_at is None:
        return None
    delta = epoch_day(scheduled_at) - epoch_day(now)
    if delta == 0:
        return "APPOINTMENT_TODAY"
    if delta > 0:
        return "APPOINTMENT_FUTURE"
    return "OVERDUE"
