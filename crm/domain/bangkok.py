"""Calendar-day arithmetic in Asia/Bangkok.

Ported from the date helpers in $PROTO/src/read-model/last-order-age.ts, which
convert to the Bangkok calendar and compare whole days rather than dividing a
millisecond difference by 86_400_000. Every business bucket in this system is a
calendar day, so "2 days ago" means two Bangkok dates back, not 48 hours.

Thailand has no DST and has held UTC+07:00 since 1920, so a fixed offset would
work today — but reading the zone from the tz database keeps that an
observation rather than an assumption baked into arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def _require_aware(moment: datetime, name: str) -> datetime:
    """A naive datetime has no instant, so its Bangkok date is a guess.

    Django runs with USE_TZ=True and hands aware datetimes out of the ORM; a
    naive one here means a caller built it by hand and probably meant local
    time. Guessing UTC would silently shift the day for anything before 07:00.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"NAIVE_DATETIME:{name}")
    return moment


def epoch_day(moment: datetime) -> int:
    """The Bangkok calendar day of `moment`, as a day ordinal.

    Two instants in the same Bangkok day return the same value regardless of
    the tzinfo they were expressed in.
    """
    return _require_aware(moment, "moment").astimezone(BANGKOK_TZ).date().toordinal()


def day_start(epoch_day_value: int) -> datetime:
    """Bangkok midnight that opens the given day ordinal.

    The inclusive lower bound of a day; `day_start(n + 1)` is the exclusive
    upper bound, which is how the last-order-age cutoffs are built.
    """
    return datetime.combine(date.fromordinal(epoch_day_value), time.min, tzinfo=BANGKOK_TZ)


def age_in_days(last_order_at: datetime | None, now: datetime) -> int | None:
    """Whole Bangkok calendar days between `last_order_at` and `now`.

    None when there is no order at all — distinct from 0, which means an order
    earlier today. A future timestamp yields a negative age rather than being
    clamped, so a clock-skewed or mis-imported date is visible instead of
    looking like a fresh order.
    """
    _require_aware(now, "now")
    if last_order_at is None:
        return None
    return epoch_day(now) - epoch_day(_require_aware(last_order_at, "last_order_at"))
