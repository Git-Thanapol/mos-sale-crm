"""Journal-based rollback: exactly the batch's own writes are undone.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_rollback.py
"""

import pytest
from django.core.exceptions import PermissionDenied

from crm.customers.models import Customer
from crm.importv2.models import ImportBatch, ImportJournalEntry
from crm.importv2.services import commit as commit_module
from crm.importv2.services import rollback as rollback_module
from crm.importv2.services.commit import commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview
from crm.importv2.services.rollback import find_drift, rollback_batch
from crm.intake.models import IntakeItem
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment
from crm.orders.models import Order, OrderLine
from tests.db.factories import ROLE_EDITOR, ROLE_TELESELL, make_customer, make_lead, make_order, make_team, make_user
from tests.db.importv2_helpers import SELLER, SKU, TEAM, TRACKING, batch_from, crm_row

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


@pytest.fixture
def seller():
    return make_user(email="seller@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี ขาย")


def committed(editor, rows, key="1"):
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key=f"c{key}")
    commit_batch(actor=editor, batch=batch, operation_key=f"m{key}")
    batch.refresh_from_db()
    return batch


def state():
    return {
        "customers": Customer.objects.count(),
        "orders": Order.objects.count(),
        "lines": OrderLine.objects.count(),
        "leads": Lead.objects.count(),
        "segments": OwnershipSegment.objects.count(),
        "audits": LeadAuditEvent.objects.count(),
        "intake": IntakeItem.objects.count(),
    }


def test_rollback_removes_everything_a_new_import_created(editor, seller):
    make_team("CRM_TEAM")
    before = state()
    batch = committed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"}), crm_row(order="SO-2", phone="0899999999", **{str(TEAM): "ทีมไม่มีจริง"})])
    assert state() != before
    result = rollback_batch(actor=editor, batch=batch, reason="wrong file", operation_key="r1")
    batch.refresh_from_db()
    assert result["status"] == "ROLLED_BACK" and result["drift"] == []
    assert state() == before
    assert batch.status == "ROLLED_BACK" and batch.rolled_back_at is not None
    assert [h["to"] for h in batch.transition_history][-2:] == ["ROLLING_BACK", "ROLLED_BACK"]
    assert not ImportJournalEntry.objects.filter(batch=batch, rolled_back_at__isnull=True).exists()


def test_rollback_restores_rows_an_import_only_updated(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    order = make_order(customer=customer, order_no="SO-100", with_line=True)
    order.tracking_no = ""
    order.save()
    customer.refresh_from_db()
    original = (customer.order_count, customer.last_order_date)
    lines_before = list(OrderLine.objects.filter(order=order).values_list("id", "sku", "quantity"))
    batch = committed(editor, [crm_row(**{str(TRACKING): "TH999", str(SKU): "NEW"})])
    order.refresh_from_db()
    assert order.tracking_no == "TH999"
    rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r1")
    order.refresh_from_db()
    customer.refresh_from_db()
    assert order.tracking_no == ""
    assert list(OrderLine.objects.filter(order=order).values_list("id", "sku", "quantity")) == lines_before
    assert (customer.order_count, customer.last_order_date) == original


def test_rollback_restores_lead_state_and_ownership_history(editor, seller):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", policy={"ownershipPolicy": "LATEST_ORDER"})
    lead_before = (lead.caretaker_id, lead.total_orders, lead.routing_state)
    segments_before = OwnershipSegment.objects.filter(lead=lead).count()
    batch = committed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    lead.refresh_from_db()
    assert lead.caretaker_id == seller.id and lead.total_orders == 1
    rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r1")
    lead.refresh_from_db()
    assert (lead.caretaker_id, lead.total_orders, lead.routing_state) == lead_before
    assert OwnershipSegment.objects.filter(lead=lead).count() == segments_before


def test_rollback_leaves_later_work_on_other_rows_alone(editor):
    batch = committed(editor, [crm_row()])
    other = make_customer(customer_name="คนอื่น", phone1="0600000000")
    kept = make_order(customer=other, order_no="LATER-1")
    rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r1")
    assert Order.objects.filter(pk=kept.pk).exists() and Customer.objects.filter(pk=other.pk).exists()
    assert not Order.objects.filter(order_no="SO-100").exists()


def test_rollback_replays_by_key_and_a_new_key_on_a_rolled_back_batch_is_refused(editor):
    batch = committed(editor, [crm_row()])
    first = rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r1")
    assert rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r1") == first
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r2")


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL])
def test_only_an_editor_may_roll_back(editor, role):
    batch = committed(editor, [crm_row()])
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["CRM_TEAM"])
    with pytest.raises(PermissionDenied):
        rollback_batch(actor=user, batch=batch, reason="x", operation_key="r")
    assert Order.objects.filter(order_no="SO-100").exists()


@pytest.mark.parametrize("status", ["PREVIEW", "AWAITING_RESOLUTION", "CANCELLED", "ROLLED_BACK", "FAILED"])
def test_a_batch_that_wrote_nothing_cannot_be_rolled_back(editor, status):
    batch = batch_from(editor, [crm_row()])
    ImportBatch.objects.filter(pk=batch.pk).update(status=status)
    batch.refresh_from_db()
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r")


def test_a_partly_committed_batch_can_be_rolled_back(editor, monkeypatch):
    rows = [crm_row(order=f"SO-{i:04d}", phone=f"08{i:08d}") for i in range(150)]
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    real = commit_module._apply_group
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 120:
            raise RuntimeError("down")
        return real(**kwargs)

    monkeypatch.setattr(commit_module, "_apply_group", flaky)
    with pytest.raises(RuntimeError):
        commit_batch(actor=editor, batch=batch, operation_key="m", chunk_size=100)
    batch.refresh_from_db()
    assert batch.status == "FAILED_RETRYABLE" and Order.objects.count() == 100
    rollback_batch(actor=editor, batch=batch, reason="give up", operation_key="r")
    assert Order.objects.count() == 0 and Customer.objects.count() == 0


def test_a_row_edited_after_the_import_stops_rollback_before_anything_changes(editor):
    batch = committed(editor, [crm_row(), crm_row(order="SO-2", phone="0899999999")])
    edited = Order.objects.get(order_no="SO-100")
    edited.tracking_no = "EDITED-BY-A-HUMAN"
    edited.save()
    assert find_drift(batch)
    before = state()
    result = rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r")
    batch.refresh_from_db()
    assert result["status"] == "ROLLBACK_FAILED" and result["drift"]
    assert batch.status == "ROLLBACK_FAILED"
    assert state() == before  # nothing was undone, not even the untouched order
    assert Order.objects.get(order_no="SO-100").tracking_no == "EDITED-BY-A-HUMAN"


def test_an_interrupted_rollback_resumes_where_it_stopped(editor, monkeypatch):
    rows = [crm_row(order=f"SO-{i}", phone=f"08{i:08d}") for i in range(30)]
    batch = committed(editor, rows)
    real = rollback_module._undo
    calls = {"n": 0}

    def flaky(entry, totals, creates):
        calls["n"] += 1
        if calls["n"] == 25:
            raise RuntimeError("crash")
        return real(entry, totals, creates)

    monkeypatch.setattr(rollback_module, "_undo", flaky)
    with pytest.raises(RuntimeError):
        rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r1", chunk_size=10)
    batch.refresh_from_db()
    assert batch.status == "ROLLING_BACK"
    assert ImportJournalEntry.objects.filter(batch=batch, rolled_back_at__isnull=False).count() == 20  # two whole chunks
    monkeypatch.setattr(rollback_module, "_undo", real)
    result = rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r2", chunk_size=10)
    batch.refresh_from_db()
    assert result["status"] == "ROLLED_BACK" and batch.status == "ROLLED_BACK"
    assert Order.objects.count() == 0 and Customer.objects.count() == 0
