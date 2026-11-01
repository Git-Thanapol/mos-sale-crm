"""Appointment state is a Bangkok calendar comparison, not a timestamp one.

02_LOCKED_BUSINESS_RULES.md fixes the timezone at Asia/Bangkok and the
computed states at TODAY / FUTURE / OVERDUE. The trap: comparing instants
makes an appointment at 09:00 "overdue" by lunchtime, when a salesperson
would plainly call it today's appointment.
"""

from datetime import datetime, timedelta

import pytest

from crm.domain.appointments import appointment_state
from crm.domain.bangkok import BANGKOK_TZ

pytestmark = pytest.mark.unit


def test_appointment_later_today_in_bangkok_is_today_not_future():
    assert (
        appointment_state(
            datetime(2026, 10, 13, 23, 0, tzinfo=BANGKOK_TZ),
            datetime(2026, 10, 13, 8, 0, tzinfo=BANGKOK_TZ),
        )
        == "APPOINTMENT_TODAY"
    )


def test_appointment_earlier_today_is_still_today_not_overdue():
    assert (
        appointment_state(
            datetime(2026, 10, 13, 8, 0, tzinfo=BANGKOK_TZ),
            datetime(2026, 10, 13, 17, 0, tzinfo=BANGKOK_TZ),
        )
        == "APPOINTMENT_TODAY"
    )


def test_yesterday_is_overdue_and_tomorrow_is_future():
    now = datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ)
    assert appointment_state(now - timedelta(days=1), now) == "OVERDUE"
    assert appointment_state(now + timedelta(days=1), now) == "APPOINTMENT_FUTURE"


def test_no_appointment_has_no_computed_state():
    assert appointment_state(None, datetime(2026, 10, 13, tzinfo=BANGKOK_TZ)) is None


def test_one_minute_past_bangkok_midnight_makes_yesterdays_appointment_overdue():
    assert (
        appointment_state(
            datetime(2026, 10, 13, 23, 59, tzinfo=BANGKOK_TZ),
            datetime(2026, 10, 14, 0, 1, tzinfo=BANGKOK_TZ),
        )
        == "OVERDUE"
    )


def test_the_computed_state_is_always_one_of_the_spec_vocabulary():
    from crm.domain.enums import COMPUTED_LEAD_STATUSES

    now = datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ)
    for offset in (-5, -1, 0, 1, 5):
        state = appointment_state(now + timedelta(days=offset), now)
        assert state in COMPUTED_LEAD_STATUSES
