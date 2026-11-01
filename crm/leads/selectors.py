"""Read queries for the Lead operational queues.

Every queue is keyset-paginated on `(last_order_at NULLS LAST, id)` and never
calls COUNT(*) on the full set — 06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md
requires offset pagination count = 0 for operational lists and forbids
unbounded queries. The offset pager in crm/core/pagination.py stays in use for
the legacy follow-up list; see docs/DECISIONS.md.

Every function takes `user` first and scopes through
`Lead.objects.for_user()`, matching the convention in
crm/followups/selectors.py.

The list page reads the maintained columns on Lead (`last_order_at`,
`total_orders`, `missed_channel_count`) rather than aggregating crm_order per
row: 03_INFRASTRUCTURE_REPLACEMENT_GUIDE.md's "do not scan all Orders every
time a Lead list is opened".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Q, QuerySet
from django.utils import timezone

from crm.core.pagination import KeysetPage, decode_cursor, encode_cursor
from crm.domain.last_order_age import (
    REALLOCATION_REVIEW_DEFAULT_DAYS,
    last_order_age_cutoffs,
)
from crm.intake.models import IntakeItem
from crm.leads.models import Lead

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: Tri-state protection filter, as in the prototype's ProtectionFilter.
PROTECTION_FILTERS = ("ANY", "ACTIVE", "NONE")


@dataclass(frozen=True)
class LeadQueueFilters:
    team_code: str = ""
    caretaker_user_id: str = ""
    lifecycle_status: str = ""
    age_preset: str = ""
    age_min_days: int | None = None
    age_max_days: int | None = None
    #: Named appointment_PROTECTION, not appointment_state: the latter is the
    #: computed status vocabulary (APPOINTMENT_TODAY / ... / OVERDUE) in
    #: crm/domain/appointments.py. One name for two value spaces guarantees a
    #: caller eventually passes the wrong one.
    appointment_protection: str = "ANY"
    followup_protection: str = "ANY"
    ownership_lock_protection: str = "ANY"
    exclude_stopped: bool = False
    missed_channel_group: str = ""
    keyword: str = ""

    @classmethod
    def from_query(cls, query) -> "LeadQueueFilters":
        def _int(name):
            raw = (query.get(name) or "").strip()
            try:
                return int(raw) if raw else None
            except (TypeError, ValueError):
                return None

        def _protection(name):
            value = (query.get(name) or "ANY").strip().upper()
            return value if value in PROTECTION_FILTERS else "ANY"

        return cls(
            team_code=(query.get("team") or "").strip(),
            caretaker_user_id=(query.get("caretaker") or "").strip(),
            lifecycle_status=(query.get("lifecycle") or "").strip(),
            age_preset=(query.get("age_preset") or "").strip(),
            age_min_days=_int("age_min_days"),
            age_max_days=_int("age_max_days"),
            appointment_protection=_protection("appointment_protection"),
            followup_protection=_protection("followup_protection"),
            ownership_lock_protection=_protection("ownership_lock_protection"),
            exclude_stopped=(query.get("exclude_stopped") or "") in ("1", "true", "on"),
            missed_channel_group=(query.get("missed_channel") or "").strip(),
            keyword=(query.get("lead_keyword") or "").strip(),
        )


NO_FILTERS = LeadQueueFilters()


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _apply_filters(qs: QuerySet, filters: LeadQueueFilters, now: datetime) -> QuerySet:
    if filters.team_code:
        qs = qs.filter(team__code=filters.team_code)
    if filters.caretaker_user_id:
        qs = qs.filter(caretaker_id=filters.caretaker_user_id)
    if filters.lifecycle_status:
        qs = qs.filter(lifecycle_status=filters.lifecycle_status)
    if filters.exclude_stopped:
        qs = qs.exclude(lifecycle_status="STOPPED")

    cutoffs = last_order_age_cutoffs(
        filters.age_preset or None, filters.age_min_days, filters.age_max_days, now
    )
    if cutoffs is not None:
        if cutoffs.no_order:
            qs = qs.filter(last_order_at__isnull=True)
        else:
            qs = qs.filter(last_order_at__isnull=False)
            if cutoffs.ordered_at_from is not None:
                qs = qs.filter(last_order_at__gte=cutoffs.ordered_at_from)
            # Exclusive upper bound: the bucket covers whole Bangkok days.
            qs = qs.filter(last_order_at__lt=cutoffs.ordered_at_before)

    if filters.appointment_protection == "ACTIVE":
        qs = qs.filter(next_follow_at__isnull=False)
    elif filters.appointment_protection == "NONE":
        qs = qs.filter(next_follow_at__isnull=True)

    if filters.followup_protection == "ACTIVE":
        qs = qs.filter(current_cycle_id__isnull=False)
    elif filters.followup_protection == "NONE":
        qs = qs.filter(current_cycle_id__isnull=True)

    if filters.ownership_lock_protection in ("ACTIVE", "NONE"):
        locked_ids = Lead.objects.filter(cycles__status="ACTIVE", cycles__lock_active=True).values(
            "pk"
        )
        if filters.ownership_lock_protection == "ACTIVE":
            qs = qs.filter(pk__in=locked_ids)
        else:
            qs = qs.exclude(pk__in=locked_ids)

    group = filters.missed_channel_group
    if group == "0":
        qs = qs.filter(missed_channel_count=0)
    elif group in ("1", "2"):
        qs = qs.filter(missed_channel_count=int(group))
    elif group == "3+":
        qs = qs.filter(missed_channel_count__gte=3)

    if filters.keyword:
        keyword = filters.keyword
        qs = qs.filter(
            Q(customer__customer_name__icontains=keyword)
            | Q(customer__phone1__icontains=keyword)
            | Q(customer__phone2__icontains=keyword)
            | Q(lead_key__icontains=keyword)
        )
    return qs


def _seek(qs: QuerySet, after_cursor: str | None) -> QuerySet:
    """Apply the keyset predicate for `(last_order_at NULLS LAST, id)`.

    NULLS LAST makes the predicate asymmetric: from a row with a timestamp the
    remaining rows are later timestamps, the same timestamp with a higher id,
    or any null; from a null row only higher-id nulls remain.
    """
    cursor = decode_cursor(after_cursor)
    if cursor is None:
        return qs
    raw_value, last_id = cursor
    if raw_value is None:
        return qs.filter(last_order_at__isnull=True, id__gt=last_id)
    parsed = datetime.fromisoformat(raw_value) if isinstance(raw_value, str) else raw_value
    return qs.filter(
        Q(last_order_at__gt=parsed)
        | Q(last_order_at=parsed, id__gt=last_id)
        | Q(last_order_at__isnull=True)
    )


def _page(qs: QuerySet, limit: int, *, key=lambda row: row.last_order_at) -> KeysetPage:
    """Fetch limit+1 rows to learn whether more exist, without a COUNT(*)."""
    limit = _clamp_limit(limit)
    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        value = key(last)
        next_cursor = encode_cursor((value.isoformat() if value else None, last.id))
    return KeysetPage(items=items, next_cursor=next_cursor, has_more=has_more)


def _lead_queue_queryset(
    user, filters: LeadQueueFilters, after_cursor: str | None, limit: int, *, now=None
) -> QuerySet:
    """The seek query, exposed so tests can assert no OFFSET reaches the SQL."""
    qs = (
        Lead.objects.select_related("customer", "team", "caretaker")
        .for_user(user)
    )
    qs = _apply_filters(qs, filters, now or timezone.now())
    qs = _seek(qs, after_cursor)
    return qs.order_by("last_order_at", "id")


def lead_queue(
    user,
    *,
    filters: LeadQueueFilters = NO_FILTERS,
    after_cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> KeysetPage:
    return _page(_lead_queue_queryset(user, filters, after_cursor, limit), limit)


def my_work_queue(
    user, *, after_cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> KeysetPage:
    """Acceptance item A "My Work".

    The caretaker is pinned to `user` HERE, server-side, and any caretaker
    arriving from a querystring is discarded — otherwise a telesale could read
    another caretaker's work by editing the URL.
    """
    filters = replace(NO_FILTERS, caretaker_user_id=str(getattr(user, "pk", "") or ""))
    return _page(_lead_queue_queryset(user, filters, after_cursor, limit), limit)


def team_unassigned_queue(
    user, *, team=None, after_cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> KeysetPage:
    """Real Leads whose Team is known and whose caretaker is empty.

    Distinct from Global Unassigned, which is pre-Lead intake.
    """
    filters = replace(
        NO_FILTERS,
        team_code=getattr(team, "code", "") or "",
        exclude_stopped=True,
    )
    qs = _lead_queue_queryset(user, filters, after_cursor, limit).filter(
        routing_state="TEAM_UNASSIGNED"
    )
    return _page(qs, limit)


def stopped_review_queue(
    user, *, after_cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> KeysetPage:
    """STOPPED leads awaiting authorized reactivation or reassignment.

    STOPPED is a lifecycle state, not an unassigned one, so this queue is
    filtered on lifecycle_status and never on routing_state.
    """
    filters = replace(NO_FILTERS, lifecycle_status="STOPPED")
    return _page(_lead_queue_queryset(user, filters, after_cursor, limit), limit)


def reallocation_review_queue(
    user,
    *,
    threshold_days: int = REALLOCATION_REVIEW_DEFAULT_DAYS,
    after_cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> KeysetPage:
    """Leads a human may want to look at reallocating.

    A review queue and nothing more: it reassigns nothing, which is what
    02_LOCKED_BUSINESS_RULES.md requires of lastOrderAge. The predicate mirrors
    crm.domain.last_order_age.is_reallocation_review_candidate — a caretaker to
    reallocate away from, not STOPPED, old enough, and unprotected.
    """
    now = timezone.now()
    cutoffs = last_order_age_cutoffs(None, threshold_days, None, now)
    filters = replace(
        NO_FILTERS,
        exclude_stopped=True,
        appointment_protection="NONE",
        followup_protection="NONE",
        ownership_lock_protection="NONE",
    )
    qs = _lead_queue_queryset(user, filters, after_cursor, limit, now=now).filter(
        caretaker__isnull=False,
        last_order_at__isnull=False,
        last_order_at__lt=cutoffs.ordered_at_before,
    )
    return _page(qs, limit)


def global_unassigned_queue(
    user, *, after_cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> KeysetPage:
    """D019 pre-Lead intake still awaiting a Team decision.

    Keyed on (created_at, id): intake has no last_order_at, and oldest-first is
    the order the work should be picked up in.
    """
    qs = IntakeItem.objects.select_related("customer").filter(status="GLOBAL_UNASSIGNED")
    cursor = decode_cursor(after_cursor)
    if cursor is not None:
        raw_value, last_id = cursor
        if raw_value is not None:
            parsed = datetime.fromisoformat(raw_value)
            qs = qs.filter(Q(created_at__gt=parsed) | Q(created_at=parsed, id__gt=last_id))
    qs = qs.order_by("created_at", "id")
    return _page(qs, limit, key=lambda row: row.created_at)
