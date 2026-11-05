"""Follow-up cycles are preserved, and notes belong to the Lead.

11_DO_NOT_CHANGE_CHECKLIST.md:
- "Notes remain Lead/Cycle-owned, not Order-owned."
- "Historical follow-up cycles are not deleted."

A new Order starts a new cycle; the old one ends with a reason and stays
queryable. Deleting an Order must not take its notes with it — a note may
reference an order but never belongs to one.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.db.utils import IntegrityError

from crm.followcycles.models import FollowupCycle, Note
from crm.followcycles.selectors import cycle_history
from crm.followcycles.services import add_note, end_cycle, open_cycle
from tests.db.factories import ROLE_EDITOR, make_lead, make_order, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.fixture
def lead():
    return make_lead()


def test_opening_a_new_cycle_ends_the_previous_one_with_a_reason(editor, lead):
    first = open_cycle(actor=editor, lead=lead)
    trigger = make_order(customer=lead.customer)
    second = open_cycle(actor=editor, lead=lead, trigger_order=trigger)

    first.refresh_from_db()
    assert first.status == "ENDED"
    assert first.ended_reason == "NEW_ORDER"
    assert first.ended_at is not None
    assert second.status == "ACTIVE"


def test_opening_a_cycle_without_a_trigger_order_closes_the_old_one_manually(editor, lead):
    first = open_cycle(actor=editor, lead=lead)
    open_cycle(actor=editor, lead=lead)
    first.refresh_from_db()
    assert first.ended_reason == "MANUAL_CLOSE"


def test_historical_cycles_are_never_deleted(editor, lead):
    open_cycle(actor=editor, lead=lead)
    open_cycle(actor=editor, lead=lead, trigger_order=make_order(customer=lead.customer))
    assert FollowupCycle.objects.filter(lead=lead).count() == 2
    assert len(cycle_history(editor, lead)) == 2


def test_cycle_history_returns_newest_first_and_does_not_filter_by_status(editor, lead):
    first = open_cycle(actor=editor, lead=lead)
    second = open_cycle(actor=editor, lead=lead)
    history = cycle_history(editor, lead)
    assert [c.id for c in history] == [second.id, first.id]
    assert {c.status for c in history} == {"ACTIVE", "ENDED"}


def test_a_lead_never_has_two_active_cycles(editor, lead):
    open_cycle(actor=editor, lead=lead)
    with pytest.raises(IntegrityError):
        FollowupCycle.objects.create(lead=lead, status="ACTIVE", started_at="2026-10-01T03:00:00Z")


def test_opening_a_cycle_points_the_lead_at_it(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    lead.refresh_from_db()
    assert lead.current_cycle_id == cycle.id


def test_ending_the_active_cycle_clears_the_lead_pointer(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    end_cycle(actor=editor, cycle=cycle, reason="CUSTOMER_ORDERED")
    lead.refresh_from_db()
    assert lead.current_cycle_id is None


def test_ending_a_cycle_requires_a_known_reason(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    with pytest.raises(ValueError):
        end_cycle(actor=editor, cycle=cycle, reason="BECAUSE")


def test_ending_an_already_ended_cycle_is_rejected(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    end_cycle(actor=editor, cycle=cycle, reason="MANUAL_CLOSE")
    with pytest.raises(ValueError):
        end_cycle(actor=editor, cycle=cycle, reason="MANUAL_CLOSE")


def test_deleting_an_order_keeps_the_note_and_nulls_the_reference(editor, lead):
    order = make_order(customer=lead.customer)
    note = add_note(actor=editor, lead=lead, body="คุยแล้ว", order=order)
    order.delete()
    note.refresh_from_db()
    assert note.lead_id == lead.id
    assert note.order_id is None
    assert note.body == "คุยแล้ว"


def test_note_is_attached_to_the_active_cycle_when_one_exists(editor, lead):
    cycle = open_cycle(actor=editor, lead=lead)
    assert add_note(actor=editor, lead=lead, body="x").cycle_id == cycle.id


def test_note_without_an_active_cycle_still_belongs_to_the_lead(editor, lead):
    note = add_note(actor=editor, lead=lead, body="ยังไม่เปิดรอบ")
    assert note.cycle_id is None
    assert note.lead_id == lead.id


def test_adding_a_note_refreshes_the_cached_copy_on_the_lead(editor, lead):
    add_note(actor=editor, lead=lead, body="โน้ตแรก")
    add_note(actor=editor, lead=lead, body="โน้ตล่าสุด")
    lead.refresh_from_db()
    assert lead.latest_note == "โน้ตล่าสุด"
    assert lead.latest_note_updated_at is not None
    # The rows stay authoritative; the cache is only the newest.
    assert Note.objects.filter(lead=lead).count() == 2


def test_an_empty_note_is_rejected(editor, lead):
    with pytest.raises(ValueError):
        add_note(actor=editor, lead=lead, body="   ")


def test_deleting_a_lead_removes_its_cycles_and_notes(editor, lead):
    open_cycle(actor=editor, lead=lead)
    add_note(actor=editor, lead=lead, body="x")
    lead_id = lead.id
    lead.delete()
    assert not FollowupCycle.objects.filter(lead_id=lead_id).exists()
    assert not Note.objects.filter(lead_id=lead_id).exists()


def test_a_note_records_who_wrote_it(editor, lead):
    assert add_note(actor=editor, lead=lead, body="x").created_by_id == editor.id


def test_cycle_lock_defaults_to_inactive(editor, lead):
    # LATEST_ORDER_WITH_LOCK only holds ownership while a lock is active, so
    # the default must not silently protect every new cycle.
    assert open_cycle(actor=editor, lead=lead).lock_active is False
