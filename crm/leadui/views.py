"""Thin views for the CRM V2 lead pages: authorize, call selectors, render.

Business rules live in crm.leads / crm.followcycles / crm.intake; nothing here
decides who owns what. A user who may not use a page gets 404, not 403: the
sidebar hides it, and a 403 would confirm the route exists.
"""

from __future__ import annotations

from dataclasses import replace
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from crm.core.permissions import (
    can_resolve_intake,
    can_review_any_lead,
    can_use_lead_workspace,
)
from crm.domain.last_order_age import REALLOCATION_REVIEW_DEFAULT_DAYS
from crm.followcycles.models import FollowupCycle
from crm.teams.models import Team
from crm.leads import presenters
from crm.leads.selectors import (
    NO_FILTERS,
    LeadQueueFilters,
    global_unassigned_queue,
    my_work_queue,
    stopped_review_queue,
    team_unassigned_queue,
)

PAGE_SIZE = 25

#: Raw querystring keys the My Work form echoes back, so a value the server
#: ignored as nonsense (min > max, a negative number) is still shown to the user.
MY_WORK_FIELDS = (
    "lead_keyword",
    "missed_channel",
    "age_preset",
    "age_min_days",
    "age_max_days",
    "age_sort",
    "appointment_protection",
    "followup_protection",
)

MISSED_CHANNEL_OPTIONS = ("0", "1", "2", "3+")
SORT_OPTIONS = (
    ("WORK_PRIORITY", "ลำดับงานปกติ"),
    ("OLDEST_LAST_ORDER", "ออเดอร์เก่าสุดก่อน"),
    ("NEWEST_LAST_ORDER", "ออเดอร์ใหม่สุดก่อน"),
)
APPOINTMENT_OPTIONS = (
    ("ANY", "ทุกสถานะนัดหมาย"),
    ("ACTIVE", "มีนัดหมายทำงานอยู่"),
    ("NONE", "ไม่มีนัดหมายทำงานอยู่"),
)
FOLLOWUP_OPTIONS = (
    ("ANY", "ทุกรอบติดตาม"),
    ("ACTIVE", "มีรอบติดตาม"),
    ("NONE", "ไม่มีรอบติดตาม"),
)


def lead_page(predicate):
    """Anonymous: redirect to login. Authenticated but failing `predicate`: 404."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not predicate(request.user):
                raise Http404
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def decorate_rows(leads) -> list[dict]:
    """presenters.decorate for a page of leads, with the ownership-lock lookup
    done once for the page instead of once per row."""
    leads = list(leads)
    locked = set(
        FollowupCycle.objects.filter(
            lead_id__in=[lead.id for lead in leads], status="ACTIVE", lock_active=True
        ).values_list("lead_id", flat=True)
    )
    now = timezone.now()
    return [presenters.decorate(lead, now=now, lock_active=lead.id in locked) for lead in leads]


def url_with(request, **changes) -> str:
    """The current querystring with `changes` applied (None removes a key)."""
    query = request.GET.copy()
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    encoded = query.urlencode()
    return f"?{encoded}" if encoded else "?"


def pager_urls(request, page) -> dict:
    has_cursor = bool((request.GET.get("cursor") or "").strip())
    return {
        "first_url": url_with(request, cursor=None) if has_cursor else "",
        "next_url": url_with(request, cursor=page.next_cursor) if page.next_cursor else "",
    }


@lead_page(can_use_lead_workspace)
def my_work(request):
    filters = LeadQueueFilters.from_query(request.GET, default_sort="WORK_PRIORITY")
    may_review = can_review_any_lead(request.user)
    if not may_review:
        # Hiding the checkbox is a convenience; this is the control.
        filters = replace(filters, reallocation_review=False)
    page = my_work_queue(
        request.user,
        filters=filters,
        after_cursor=(request.GET.get("cursor") or None),
        limit=PAGE_SIZE,
    )
    return render(
        request,
        "leadui/my_work.html",
        {
            "rows": decorate_rows(page.items),
            "q": {name: (request.GET.get(name) or "").strip() for name in MY_WORK_FIELDS},
            "sort_value": filters.sort,
            "exclude_stopped": filters.exclude_stopped,
            "reallocation_review": filters.reallocation_review,
            "may_review": may_review,
            "reallocation_days": REALLOCATION_REVIEW_DEFAULT_DAYS,
            "missed_channel_options": MISSED_CHANNEL_OPTIONS,
            "age_preset_options": presenters.age_preset_options(),
            "sort_options": SORT_OPTIONS,
            "appointment_options": APPOINTMENT_OPTIONS,
            "followup_options": FOLLOWUP_OPTIONS,
            **pager_urls(request, page),
        },
    )


def _review_queue(request, *, kind):
    """Shared by Team Unassigned and STOPPED Review (prototype ReviewQueue).

    Only the age preset is a filter here, as in the prototype; every other
    querystring value is ignored rather than trusted.
    """
    cursor = request.GET.get("cursor") or None
    preset = (request.GET.get("age_preset") or "").strip()
    if kind == "TEAM":
        filters = replace(NO_FILTERS, age_preset=preset, sort="WORK_PRIORITY")
        page = team_unassigned_queue(
            request.user, filters=filters, after_cursor=cursor, limit=PAGE_SIZE
        )
        title, subtitle = "Team Unassigned", "Lead มี Team แล้ว แต่ยังไม่มี caretaker"
    else:
        filters = replace(NO_FILTERS, sort="WORK_PRIORITY")
        page = stopped_review_queue(
            request.user, filters=filters, after_cursor=cursor, limit=PAGE_SIZE
        )
        title, subtitle = "STOPPED Review", "STOPPED เป็น lifecycle state แยกจาก Unassigned"
    return render(
        request,
        "leadui/review_queue.html",
        {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "rows": decorate_rows(page.items),
            "age_preset": preset,
            "age_preset_options": presenters.age_preset_options(),
            **pager_urls(request, page),
        },
    )


@lead_page(can_review_any_lead)
def team_unassigned(request):
    return _review_queue(request, kind="TEAM")


@lead_page(can_review_any_lead)
def stopped_review(request):
    return _review_queue(request, kind="STOPPED")


def _not_built(request, *args, **kwargs):
    raise Http404


@lead_page(can_resolve_intake)
def global_unassigned(request):
    """D019 intake queue. The Team buttons post to leads:resolve_intake, which
    re-checks authorization and is the only thing that creates a Lead."""
    page = global_unassigned_queue(
        request.user, after_cursor=(request.GET.get("cursor") or None), limit=PAGE_SIZE
    )
    return render(
        request,
        "leadui/global_unassigned.html",
        {
            "items": page.items,
            # Closed set: a typed team code is a typo on an action that creates a Lead.
            "teams": list(Team.objects.filter(active=True).order_by("code")),
            "next": request.get_full_path(),
            **pager_urls(request, page),
        },
    )
lead_detail = customer_detail = customer_history = _not_built
