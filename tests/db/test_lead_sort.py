"""Sort modes for the lead queues: WORK_PRIORITY, OLDEST_LAST_ORDER, NEWEST_LAST_ORDER.

The prototype ranks work as overdue, then today's appointment, then lifecycle
(WAITING_FOLLOWUP, NEW, WAITING_CUSTOMER), then everything else, and breaks ties
on next_follow_at then id (read-model/operational.ts workRank and the sort in
listMyWork). Every mode must page by keyset: a cursor taken from page N must
yield exactly the rows after it, with nothing repeated or skipped.

Run via: docker compose exec -T web python -m pytest tests/db/test_lead_sort.py
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.utils import timezone

from crm.leads.selectors import (
    NO_FILTERS,
    LeadQueueFilters,
    lead_queue,
    my_work_queue,
    team_unassigned_queue,
)
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]

SORTS = ("WORK_PRIORITY", "OLDEST_LAST_ORDER", "NEWEST_LAST_ORDER")


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


def _lead(**kwargs):
    return make_lead(customer=make_customer(), **kwargs)


def _ids(page):
    return [lead.id for lead in page.items]


def _all_pages(user, sort, limit):
    seen, cursor, guard = [], None, 0
    while True:
        page = lead_queue(
            user, filters=LeadQueueFilters(sort=sort), after_cursor=cursor, limit=limit
        )
        seen.extend(_ids(page))
        guard += 1
        assert guard < 100, "cursor never terminated"
        if not page.has_more:
            return seen
        cursor = page.next_cursor


def test_oldest_last_order_first_with_no_order_last(editor):
    now = timezone.now()
    a = _lead(last_order_at=now - timedelta(days=30))
    b = _lead(last_order_at=now - timedelta(days=10))
    c = _lead(last_order_at=now - timedelta(days=20))
    none = _lead(last_order_at=None)
    page = lead_queue(editor, filters=LeadQueueFilters(sort="OLDEST_LAST_ORDER"))
    assert _ids(page) == [a.id, c.id, b.id, none.id]


def test_newest_last_order_first_with_no_order_last(editor):
    now = timezone.now()
    a = _lead(last_order_at=now - timedelta(days=30))
    b = _lead(last_order_at=now - timedelta(days=10))
    c = _lead(last_order_at=now - timedelta(days=20))
    none = _lead(last_order_at=None)
    page = lead_queue(editor, filters=LeadQueueFilters(sort="NEWEST_LAST_ORDER"))
    assert _ids(page) == [b.id, c.id, a.id, none.id]


def test_work_priority_ranks_overdue_today_then_lifecycle(editor):
    now = timezone.now()
    following = _lead(lifecycle_status="FOLLOWING")
    waiting_customer = _lead(lifecycle_status="WAITING_CUSTOMER")
    new = _lead(lifecycle_status="NEW")
    waiting_followup = _lead(lifecycle_status="WAITING_FOLLOWUP")
    today = _lead(lifecycle_status="NEW", next_follow_at=now + timedelta(minutes=1))
    overdue = _lead(lifecycle_status="FOLLOWING", next_follow_at=now - timedelta(days=3))
    page = lead_queue(editor, filters=LeadQueueFilters(sort="WORK_PRIORITY"))
    assert _ids(page) == [
        overdue.id,
        today.id,
        waiting_followup.id,
        new.id,
        waiting_customer.id,
        following.id,
    ]


def test_work_priority_breaks_ties_on_next_follow_at_then_id(editor):
    now = timezone.now()
    later = _lead(lifecycle_status="FOLLOWING", next_follow_at=now - timedelta(days=1))
    earlier = _lead(lifecycle_status="FOLLOWING", next_follow_at=now - timedelta(days=5))
    same_a = _lead(lifecycle_status="FOLLOWING", next_follow_at=now - timedelta(days=9))
    same_b = _lead(lifecycle_status="FOLLOWING", next_follow_at=same_a.next_follow_at)
    page = lead_queue(editor, filters=LeadQueueFilters(sort="WORK_PRIORITY"))
    assert _ids(page) == [same_a.id, same_b.id, earlier.id, later.id]


def test_an_appointment_earlier_today_is_today_not_overdue(editor):
    # The prototype compares Bangkok calendar days: 00:01 today is still today.
    from crm.domain.bangkok import BANGKOK_TZ, day_start, epoch_day

    now = timezone.now()
    start_of_today = day_start(epoch_day(now)).astimezone(BANGKOK_TZ)
    today = _lead(lifecycle_status="FOLLOWING", next_follow_at=start_of_today + timedelta(minutes=1))
    overdue = _lead(lifecycle_status="FOLLOWING", next_follow_at=start_of_today - timedelta(minutes=1))
    page = lead_queue(editor, filters=LeadQueueFilters(sort="WORK_PRIORITY"))
    assert _ids(page).index(overdue.id) < _ids(page).index(today.id)
    assert page.items[0].id == overdue.id


def test_unknown_sort_falls_back_to_the_default():
    assert LeadQueueFilters.from_query({"age_sort": "zzz"}).sort == "OLDEST_LAST_ORDER"
    assert (
        LeadQueueFilters.from_query({"age_sort": "zzz"}, default_sort="WORK_PRIORITY").sort
        == "WORK_PRIORITY"
    )
    assert LeadQueueFilters.from_query({}, default_sort="WORK_PRIORITY").sort == "WORK_PRIORITY"
    assert LeadQueueFilters.from_query({"age_sort": "NEWEST_LAST_ORDER"}).sort == "NEWEST_LAST_ORDER"


@pytest.mark.parametrize("sort", SORTS)
def test_keyset_pages_are_stable_and_complete(editor, sort):
    now = timezone.now()
    statuses = ("NEW", "FOLLOWING", "WAITING_FOLLOWUP", "WAITING_CUSTOMER")
    for i in range(30):
        _lead(
            lifecycle_status=statuses[i % 4],
            last_order_at=None if i % 7 == 0 else now - timedelta(days=i % 9),
            next_follow_at=None if i % 3 == 0 else now + timedelta(days=(i % 5) - 2),
        )
    paged = _all_pages(editor, sort, limit=7)
    whole = _all_pages(editor, sort, limit=200)
    assert len(paged) == 30 and len(set(paged)) == 30
    assert paged == whole


@pytest.mark.parametrize("sort", SORTS)
def test_a_garbage_cursor_starts_from_the_first_page(editor, sort):
    for _ in range(3):
        _lead()
    first = lead_queue(editor, filters=LeadQueueFilters(sort=sort), limit=2)
    for junk in ("garbage", "e30", "W10", "WzEsMl0"):
        page = lead_queue(editor, filters=LeadQueueFilters(sort=sort), after_cursor=junk, limit=2)
        assert _ids(page) == _ids(first), junk


def test_a_cursor_from_another_sort_does_not_error(editor):
    for _ in range(4):
        _lead(last_order_at=timezone.now())
    work = lead_queue(editor, filters=LeadQueueFilters(sort="WORK_PRIORITY"), limit=2)
    page = lead_queue(
        editor,
        filters=LeadQueueFilters(sort="OLDEST_LAST_ORDER"),
        after_cursor=work.next_cursor,
        limit=2,
    )
    assert page.items is not None


def test_no_offset_reaches_the_sql(editor):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for _ in range(5):
        _lead()
    with CaptureQueriesContext(connection) as queries:
        lead_queue(editor, filters=LeadQueueFilters(sort="WORK_PRIORITY"), limit=2)
    sql = " ".join(q["sql"] for q in queries.captured_queries)
    assert "OFFSET" not in sql.upper()
    assert "COUNT(" not in sql.upper()


def test_my_work_applies_filters_but_pins_the_caretaker():
    me = make_user(email="me@example.com", role=ROLE_TELESELL, staff_code="S1")
    other = make_user(email="other@example.com", role=ROLE_TELESELL, staff_code="S2")
    mine = make_lead(customer=make_customer(customer_name="สมชาย", staff_code="S1"), caretaker=me)
    make_lead(customer=make_customer(customer_name="สมหญิง", staff_code="S1"), caretaker=me)
    make_lead(customer=make_customer(customer_name="สมชาย", staff_code="S2"), caretaker=other)

    filters = LeadQueueFilters(keyword="สมชาย", caretaker_user_id=str(other.pk))
    page = my_work_queue(me, filters=filters)
    assert _ids(page) == [mine.id]


def test_my_work_default_filters_are_unchanged():
    me = make_user(email="me2@example.com", role=ROLE_TELESELL, staff_code="S1")
    lead = make_lead(customer=make_customer(staff_code="S1"), caretaker=me)
    assert _ids(my_work_queue(me)) == [lead.id]


def test_team_unassigned_accepts_filters_and_sort(editor):
    now = timezone.now()
    old = _lead(last_order_at=now - timedelta(days=100))
    new = _lead(last_order_at=now - timedelta(days=1))
    page = team_unassigned_queue(
        editor, filters=replace(NO_FILTERS, sort="NEWEST_LAST_ORDER")
    )
    assert _ids(page) == [new.id, old.id]


def test_reallocation_review_filter_matches_the_review_queue_predicate(editor):
    now = timezone.now()
    caretaker = make_user(email="c@example.com", role=ROLE_TELESELL, staff_code="S1")
    old = dict(last_order_at=now - timedelta(days=200), caretaker=caretaker)
    candidate = _lead(**old)
    _lead(**old, next_follow_at=now + timedelta(days=1))  # protected by an appointment
    _lead(**old, lifecycle_status="STOPPED")
    _lead(last_order_at=now - timedelta(days=200))  # no caretaker to reallocate from
    _lead(last_order_at=now - timedelta(days=2), caretaker=caretaker)  # too recent
    page = lead_queue(editor, filters=LeadQueueFilters(reallocation_review=True))
    assert _ids(page) == [candidate.id]
    assert LeadQueueFilters.from_query({"reallocation_review": "on"}).reallocation_review is True
    assert LeadQueueFilters.from_query({}).reallocation_review is False


def test_my_work_for_an_editor_is_the_whole_scope_not_just_their_own_leads(editor):
    # Prototype pageMyWork: only TELESALE is pinned to its own caretaker; ADMIN
    # (EDITOR here) sees every lead and may filter by caretaker.
    other = make_user(email="o@example.com", role=ROLE_TELESELL, staff_code="S9")
    mine = _lead(caretaker=editor)
    theirs = _lead(caretaker=other)
    unassigned = _lead()
    assert set(_ids(my_work_queue(editor))) == {mine.id, theirs.id, unassigned.id}
    only_other = my_work_queue(editor, filters=LeadQueueFilters(caretaker_user_id=str(other.pk)))
    assert _ids(only_other) == [theirs.id]


def test_my_work_for_a_team_lead_is_their_team_only():
    lead_user = make_user(
        email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"]
    )
    in_scope = make_lead(customer=make_customer(), team_code="TEAM_A")
    make_lead(customer=make_customer(), team_code="TEAM_B")
    assert _ids(my_work_queue(lead_user)) == [in_scope.id]
