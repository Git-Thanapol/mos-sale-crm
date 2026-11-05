"""Last-order-age buckets and reallocation-review candidacy.

Ported from $PROTO/src/read-model/last-order-age.ts.

The rule 02_LOCKED_BUSINESS_RULES.md is emphatic about: "Lead should be
reviewed for reallocation" is a review/filter queue ONLY. Nothing here
reassigns anything — `is_reallocation_review_candidate` answers a question, and
the only thing that may act on the answer is a human through
crm.leads.services.reassign_caretaker.

Ages are whole Bangkok calendar days (crm/domain/bangkok.py), so the cutoffs
are day boundaries rather than offsets from "now". Computing them from a raw
timestamp is the Review Focus #1 off-by-one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from crm.domain.bangkok import age_in_days, day_start, epoch_day

REALLOCATION_REVIEW_DEFAULT_DAYS = 45

_MAX_DAYS = 36_500


@dataclass(frozen=True)
class AgePreset:
    id: str
    label: str
    min_days: int | None
    max_days: int | None
    no_order: bool = False


#: Order is the contract: this is the option list the UI renders.
LAST_ORDER_AGE_PRESETS: tuple[AgePreset, ...] = (
    AgePreset("0_7", "0–7 วัน", 0, 7),
    AgePreset("8_15", "8–15 วัน", 8, 15),
    AgePreset("16_30", "16–30 วัน", 16, 30),
    AgePreset("31_45", "31–45 วัน", 31, 45),
    AgePreset("46_60", "46–60 วัน", 46, 60),
    AgePreset("61_90", "61–90 วัน", 61, 90),
    AgePreset("OVER_90", "มากกว่า 90 วัน", 91, None),
    AgePreset("NO_ORDER", "ไม่มีออเดอร์เลย", None, None, no_order=True),
)

_PRESETS_BY_ID = {preset.id: preset for preset in LAST_ORDER_AGE_PRESETS}


@dataclass(frozen=True)
class AgeCutoffs:
    no_order: bool
    #: Inclusive lower bound for last_order_at.
    ordered_at_from: datetime | None
    #: EXCLUSIVE upper bound for last_order_at. The asymmetry is deliberate and
    #: the SQL layer depends on it: a bucket covers whole days, so the upper
    #: edge is the start of the day after the youngest matching day.
    ordered_at_before: datetime | None
    min_days: int | None
    max_days: int | None


@dataclass(frozen=True)
class ProtectionSummary:
    active_appointment: bool
    active_followup_cycle: bool
    ownership_lock_active: bool
    next_follow_at: datetime | None


def _assert_days(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    # bool is an int subclass; True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_DAYS:
        raise ValueError(f"{name} must be an integer between 0 and {_MAX_DAYS}")
    return value


def resolve_last_order_age_filter(
    preset: str | None, min_days: int | None, max_days: int | None
) -> AgeCutoffs | None:
    """Normalise a filter request, or None when nothing was asked for."""
    if preset is None and min_days is None and max_days is None:
        return None

    resolved_preset = None
    if preset is not None:
        resolved_preset = _PRESETS_BY_ID.get(preset)
        if resolved_preset is None:
            raise ValueError(f"UNKNOWN_LAST_ORDER_AGE_PRESET:{preset}")

    if resolved_preset is not None and resolved_preset.no_order:
        if min_days is not None or max_days is not None:
            raise ValueError("NO_ORDER cannot be combined with a numeric age range")
        return AgeCutoffs(
            no_order=True,
            ordered_at_from=None,
            ordered_at_before=None,
            min_days=None,
            max_days=None,
        )

    preset_min = resolved_preset.min_days if resolved_preset else None
    preset_max = resolved_preset.max_days if resolved_preset else None
    effective_min = _assert_days(min_days if min_days is not None else preset_min, "minimum days") or 0
    effective_max = _assert_days(max_days if max_days is not None else preset_max, "maximum days")
    if effective_max is not None and effective_min > effective_max:
        raise ValueError("minimum days cannot exceed maximum days")

    return AgeCutoffs(
        no_order=False,
        ordered_at_from=None,
        ordered_at_before=None,
        min_days=effective_min,
        max_days=effective_max,
    )


def last_order_age_cutoffs(
    preset: str | None, min_days: int | None, max_days: int | None, now: datetime
) -> AgeCutoffs | None:
    """Turn a day range into Bangkok day-boundary timestamps for SQL."""
    resolved = resolve_last_order_age_filter(preset, min_days, max_days)
    if resolved is None or resolved.no_order:
        return resolved
    today = epoch_day(now)
    return AgeCutoffs(
        no_order=False,
        ordered_at_from=(
            None if resolved.max_days is None else day_start(today - resolved.max_days)
        ),
        ordered_at_before=day_start(today - (resolved.min_days or 0) + 1),
        min_days=resolved.min_days,
        max_days=resolved.max_days,
    )


def matches_last_order_age(
    last_order_at: datetime | None,
    preset: str | None,
    min_days: int | None,
    max_days: int | None,
    now: datetime,
) -> bool:
    resolved = resolve_last_order_age_filter(preset, min_days, max_days)
    if resolved is None:
        return True
    if resolved.no_order:
        return last_order_at is None
    age = age_in_days(last_order_at, now)
    if age is None or age < (resolved.min_days or 0):
        return False
    return resolved.max_days is None or age <= resolved.max_days


def is_reallocation_review_candidate(
    *,
    last_order_at: datetime | None,
    caretaker_user_id: str | None,
    lifecycle_status: str,
    protection: ProtectionSummary,
    now: datetime,
    threshold_days: int,
) -> bool:
    """Should a human LOOK at reallocating this lead?

    Answers a question; reassigns nothing. A lead is a candidate only when it
    has a caretaker to reallocate away from, is not STOPPED, has an order old
    enough to pass the threshold, and is not protected by an active
    appointment, follow-up cycle or ownership lock.
    """
    _assert_days(threshold_days, "reallocation threshold")
    age = age_in_days(last_order_at, now)
    return (
        age is not None
        and age >= threshold_days
        and caretaker_user_id is not None
        and lifecycle_status != "STOPPED"
        and not protection.active_appointment
        and not protection.active_followup_cycle
        and not protection.ownership_lock_active
    )
