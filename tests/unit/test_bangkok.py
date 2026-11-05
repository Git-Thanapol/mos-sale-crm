"""Bangkok calendar-day arithmetic — Review Focus #1.

Every business bucket in this system is a calendar day in Asia/Bangkok, not a
24-hour window. Subtracting timestamps and dividing by 86400 gets the right
answer most of the day and the wrong one for anything in the 17:00-24:00 UTC
window, because that is already tomorrow in Bangkok. These tests pin the
calendar-day semantics so the last-order-age buckets cannot drift by a day.
"""

from datetime import datetime, timedelta, timezone

import pytest

from crm.domain.bangkok import BANGKOK_TZ, age_in_days, day_start, epoch_day

pytestmark = pytest.mark.unit


def test_late_evening_bangkok_order_is_zero_days_old():
    ordered = datetime(2026, 10, 13, 23, 30, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 10, 13, 23, 55, tzinfo=BANGKOK_TZ)
    assert age_in_days(ordered, now) == 0


def test_utc_evening_window_does_not_shift_the_bangkok_day():
    # 2026-10-13T18:00Z is already 2026-10-14 01:00 in Bangkok
    ordered = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
    assert age_in_days(ordered, now) == 0


def test_age_is_none_when_there_is_no_order():
    assert age_in_days(None, datetime(2026, 10, 13, tzinfo=BANGKOK_TZ)) is None


def test_one_minute_across_bangkok_midnight_is_one_day():
    ordered = datetime(2026, 10, 13, 23, 59, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 10, 14, 0, 1, tzinfo=BANGKOK_TZ)
    assert age_in_days(ordered, now) == 1


def test_a_full_24_hours_inside_one_bangkok_day_boundary_is_one_day():
    ordered = datetime(2026, 10, 13, 10, 0, tzinfo=BANGKOK_TZ)
    now = ordered + timedelta(hours=24)
    assert age_in_days(ordered, now) == 1


def test_age_is_measured_in_whole_calendar_days():
    ordered = datetime(2026, 10, 1, 12, 0, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=BANGKOK_TZ)
    assert age_in_days(ordered, now) == 31


def test_a_future_order_yields_a_negative_age():
    now = datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ)
    assert age_in_days(now + timedelta(days=2), now) == -2


def test_epoch_day_is_identical_for_two_instants_in_the_same_bangkok_day():
    early = datetime(2026, 10, 13, 0, 5, tzinfo=BANGKOK_TZ)
    late = datetime(2026, 10, 13, 23, 55, tzinfo=BANGKOK_TZ)
    assert epoch_day(early) == epoch_day(late)


def test_epoch_day_of_a_utc_instant_uses_the_bangkok_calendar():
    # 17:00Z on the 13th is 00:00 on the 14th in Bangkok
    assert epoch_day(datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)) == epoch_day(
        datetime(2026, 10, 14, 9, 0, tzinfo=BANGKOK_TZ)
    )


def test_consecutive_days_differ_by_exactly_one():
    first = datetime(2026, 10, 13, 8, 0, tzinfo=BANGKOK_TZ)
    assert epoch_day(first + timedelta(days=1)) - epoch_day(first) == 1


def test_day_start_is_bangkok_midnight_and_round_trips_through_epoch_day():
    moment = datetime(2026, 10, 13, 15, 42, tzinfo=BANGKOK_TZ)
    start = day_start(epoch_day(moment))
    assert start.tzinfo is not None
    assert start.astimezone(BANGKOK_TZ).hour == 0
    assert start.astimezone(BANGKOK_TZ).minute == 0
    assert start.astimezone(BANGKOK_TZ).date() == moment.date()
    assert epoch_day(start) == epoch_day(moment)


def test_day_start_of_the_next_day_is_24_hours_later():
    today = epoch_day(datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ))
    assert day_start(today + 1) - day_start(today) == timedelta(hours=24)


@pytest.mark.parametrize("naive_arg", ["last_order_at", "now"])
def test_a_naive_datetime_is_rejected_rather_than_assumed_to_be_utc(naive_arg):
    aware = datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ)
    naive = datetime(2026, 10, 13, 12, 0)
    kwargs = {"last_order_at": aware, "now": aware} | {naive_arg: naive}
    with pytest.raises(ValueError):
        age_in_days(**kwargs)
