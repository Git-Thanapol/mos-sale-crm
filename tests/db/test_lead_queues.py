"""Operational queues: keyset only, scoped, and never acting on their own.

06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md section F: offset pagination count =
0 for large operational lists, no unbounded queries, no per-Lead Order scan,
stable cursor behaviour.

Run via: docker compose exec web python -m pytest tests/db
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.utils import timezone

from crm.intake.services import resolve_intake_to_team, upsert_intake
from crm.leads.models import OwnershipSegment
from crm.leads.selectors import (
    NO_FILTERS,
    _lead_queue_queryset,
    global_unassigned_queue,
    lead_queue,
    my_work_queue,
    reallocation_review_queue,
    stopped_review_queue,
    team_unassigned_queue,
)
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    make_customer,
    make_lead,
    make_order,
    make_team,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]

NOW = timezone.now()


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


def make_leads(count, *, staff_code="S0001", with_orders=False, uid_offset=0, **kwargs):
    """`uid_offset` keeps two calls in one test from colliding on phone_key,
    which is UNIQUE (and stays so in this plan).
    """
    leads = []
    for i in range(count):
        customer = make_customer(_uid=f"{uid_offset + i + 1:07d}", staff_code=staff_code)
        lead = make_lead(
            customer=customer,
            last_order_at=NOW - timedelta(days=i + 1),
            **kwargs,
        )
        if with_orders:
            for _ in range(5):
                make_order(customer=customer)
        leads.append(lead)
    return leads


def test_queue_walks_every_row_exactly_once_across_pages(editor):
    make_leads(25)
    seen, cursor = [], None
    for _ in range(10):
        page = lead_queue(editor, after_cursor=cursor, limit=10)
        seen.extend(lead.id for lead in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == 25
    assert len(set(seen)) == 25


@pytest.mark.parametrize(
    "builder",
    [
        lambda u: _lead_queue_queryset(u, NO_FILTERS, None, 10),
        lambda u: _lead_queue_queryset(u, replace(NO_FILTERS, age_preset="OVER_90"), None, 10),
    ],
)
def test_queue_sql_contains_no_offset(editor, builder):
    # acceptance criterion F
    assert "OFFSET" not in str(builder(editor).query).upper()


def test_a_lead_with_no_order_sorts_last_and_is_still_reachable(editor):
    make_leads(3)
    tail = make_lead(
        customer=make_customer(_uid="9999999", staff_code="S0001"), last_order_at=None
    )
    seen, cursor = [], None
    for _ in range(10):
        page = lead_queue(editor, after_cursor=cursor, limit=2)
        seen.extend(lead.id for lead in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert seen[-1] == tail.id
    assert len(set(seen)) == 4


def test_inserting_a_row_between_pages_does_not_repeat_a_row(editor):
    make_leads(20)
    first = lead_queue(editor, limit=10)
    make_lead(customer=make_customer(_uid="8888888", staff_code="S0001"), last_order_at=NOW)
    second = lead_queue(editor, after_cursor=first.next_cursor, limit=10)
    assert not {lead.id for lead in first.items} & {lead.id for lead in second.items}


def test_telesell_queue_is_scoped_to_their_own_staff_code():
    from crm.teams.models import TeamAssignment

    make_leads(2, staff_code="S0001")
    make_leads(2, staff_code="S0002", uid_offset=100)
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001")
    # Scoping is staff_code AND the lead's team, so membership is part of the
    # realistic setup now.
    TeamAssignment.objects.create(
        user=telesell, team_code="CRM_TEAM", effective_from=timezone.now()
    )
    page = lead_queue(telesell, limit=50)
    assert page.items
    assert all(lead.customer.staff_code == "S0001" for lead in page.items)


def test_a_staff_user_without_a_staff_code_sees_nothing():
    make_leads(3)
    nobody = make_user(email="nobody@example.com", role=ROLE_TELESELL, staff_code="")
    assert lead_queue(nobody, limit=50).items == []


def test_age_filter_uses_the_domain_cutoffs(editor):
    old = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"),
        last_order_at=NOW - timedelta(days=100),
    )
    make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0001"),
        last_order_at=NOW - timedelta(days=3),
    )
    page = lead_queue(editor, filters=replace(NO_FILTERS, age_preset="OVER_90"), limit=50)
    assert {lead.id for lead in page.items} == {old.id}


def test_no_order_bucket_returns_only_leads_without_an_order(editor):
    make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"), last_order_at=NOW)
    none = make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), last_order_at=None)
    page = lead_queue(editor, filters=replace(NO_FILTERS, age_preset="NO_ORDER"), limit=50)
    assert {lead.id for lead in page.items} == {none.id}


def test_my_work_ignores_a_querystring_caretaker(editor):
    telesell = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001")
    other = make_user(email="o@example.com", role=ROLE_TELESELL, staff_code="S0001")
    mine = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"), caretaker=telesell
    )
    make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), caretaker=other)
    page = my_work_queue(telesell, limit=50)
    assert {lead.id for lead in page.items} == {mine.id}


def test_team_unassigned_lists_real_leads_with_a_team_and_no_caretaker(editor):
    unassigned = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"), caretaker=None
    )
    assigned = make_lead(
        customer=make_customer(_uid="0000002", staff_code="S0001"),
        caretaker=make_user(email="c@example.com", staff_code="S0001"),
    )
    page = team_unassigned_queue(editor, limit=50)
    ids = {lead.id for lead in page.items}
    assert unassigned.id in ids
    assert assigned.id not in ids
    assert all(lead.team_id is not None for lead in page.items)


def test_stopped_review_queue_is_filtered_on_lifecycle_not_routing(editor):
    stopped = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"),
        lifecycle_status="STOPPED",
        stopped_reason="MANUAL_STOP",
        caretaker=make_user(email="c@example.com", staff_code="S0001"),
    )
    make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), caretaker=None)
    page = stopped_review_queue(editor, limit=50)
    assert {lead.id for lead in page.items} == {stopped.id}


def test_reallocation_queue_never_reassigns_anything(editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    lead = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"),
        caretaker=caretaker,
        last_order_at=NOW - timedelta(days=200),
    )
    before = lead.caretaker_id

    reallocation_review_queue(editor, threshold_days=45, limit=50)

    lead.refresh_from_db()
    assert lead.caretaker_id == before
    assert not OwnershipSegment.objects.filter(lead=lead).exists()


def test_reallocation_queue_includes_a_stale_unprotected_lead(editor):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    stale = make_lead(
        customer=make_customer(_uid="0000001", staff_code="S0001"),
        caretaker=caretaker,
        last_order_at=NOW - timedelta(days=200),
    )
    assert stale.id in {lead.id for lead in reallocation_review_queue(editor, limit=50).items}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lifecycle_status": "STOPPED", "stopped_reason": "MANUAL_STOP"},
        {"next_follow_at": NOW + timedelta(days=1)},
        {"caretaker": None},
        {"last_order_at": None},
    ],
)
def test_reallocation_queue_excludes_protected_stopped_unowned_and_orderless(editor, kwargs):
    caretaker = make_user(email="c@example.com", staff_code="S0001")
    defaults = {
        "caretaker": caretaker,
        "last_order_at": NOW - timedelta(days=200),
    }
    defaults.update(kwargs)
    lead = make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"), **defaults)
    assert lead.id not in {row.id for row in reallocation_review_queue(editor, limit=50).items}


def test_global_unassigned_queue_lists_only_unresolved_intake(editor):
    a = make_customer(_uid="0000001", staff_code="S0001")
    b = make_customer(_uid="0000002", staff_code="S0001")
    open_item, _ = upsert_intake(customer=a, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    converted, _ = upsert_intake(
        customer=b, source_order_id="SO-2", source_kind="LITE_DETAIL_ORDER"
    )
    resolve_intake_to_team(actor=editor, intake=converted, team=make_team("CRM_TEAM"))

    ids = {item.id for item in global_unassigned_queue(editor, limit=50).items}
    assert open_item.id in ids
    assert converted.id not in ids


def test_global_unassigned_queue_paginates_without_repeats(editor):
    for i in range(12):
        upsert_intake(
            customer=make_customer(_uid=f"{i + 1:07d}", staff_code="S0001"),
            source_order_id=f"SO-{i}",
            source_kind="LITE_DETAIL_ORDER",
        )
    seen, cursor = [], None
    for _ in range(10):
        page = global_unassigned_queue(editor, after_cursor=cursor, limit=5)
        seen.extend(item.id for item in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 12


def test_one_lead_list_page_does_not_scan_orders_per_lead(editor, django_assert_max_num_queries):
    make_leads(10, with_orders=True)
    with django_assert_max_num_queries(4):
        list(lead_queue(editor, limit=10).items)


def test_a_malformed_cursor_starts_from_the_first_page(editor):
    make_leads(5)
    first = lead_queue(editor, limit=3)
    assert [lead.id for lead in lead_queue(editor, after_cursor="garbage!!", limit=3).items] == [
        lead.id for lead in first.items
    ]


def test_the_limit_is_clamped_so_a_crafted_querystring_cannot_force_a_huge_page(editor):
    make_leads(3)
    assert len(lead_queue(editor, limit=10_000).items) == 3
    assert len(lead_queue(editor, limit=0).items) == 1


def test_keyword_filter_matches_name_and_phone(editor):
    target = make_lead(customer=make_customer(_uid="0000001", staff_code="S0001", customer_name="สมชาย"))
    make_lead(customer=make_customer(_uid="0000002", staff_code="S0001", customer_name="สมหญิง"))
    page = lead_queue(editor, filters=replace(NO_FILTERS, keyword="สมชาย"), limit=50)
    assert {lead.id for lead in page.items} == {target.id}


def test_missed_channel_group_filter_buckets_without_collapsing_the_integer(editor):
    make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"), missed_channel_count=0)
    two = make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"), missed_channel_count=2)
    many = make_lead(customer=make_customer(_uid="0000003", staff_code="S0001"), missed_channel_count=7)
    assert {lead.id for lead in lead_queue(editor, filters=replace(NO_FILTERS, missed_channel_group="2"), limit=50).items} == {two.id}
    assert {lead.id for lead in lead_queue(editor, filters=replace(NO_FILTERS, missed_channel_group="3+"), limit=50).items} == {many.id}
