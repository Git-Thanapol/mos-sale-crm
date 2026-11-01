"""Confirm gate and chunked, journalled, idempotent commit.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_commit.py
"""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from crm.customers.models import Customer
from crm.domain.import_errors import ConfirmError
from crm.followcycles.models import FollowupCycle
from crm.importv2.models import ImportBatch, ImportJournalEntry
from crm.importv2.services import commit as commit_module
from crm.importv2.services.commit import StalePreviewError, commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview, recompute_preview_hash
from crm.intake.models import IntakeItem
from crm.leads.models import Lead
from crm.orders.models import Order, OrderLine
from tests.db.factories import ROLE_EDITOR, ROLE_TELESELL, make_customer, make_lead, make_order, make_team, make_user
from tests.db.importv2_helpers import NAME, PRICE, SELLER, SKU, TEAM, TRACKING, batch_from, crm_row

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


@pytest.fixture
def seller():
    return make_user(email="seller@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี ขาย")


def previewed(editor, rows):
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    return batch


def confirm(editor, batch, key="confirm-1"):
    return confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key=key)


def run(editor, rows, **kwargs):
    batch = previewed(editor, rows)
    confirm(editor, batch)
    result = commit_batch(actor=editor, batch=batch, operation_key="commit-1", **kwargs)
    batch.refresh_from_db()
    return batch, result


def resolve(batch, kind, choice):
    """What the conflict service does: record the decision and re-derive the digest."""
    conflict = batch.conflicts.get(kind=kind)
    conflict.status, conflict.resolution, conflict.resolved_at = "RESOLVED", choice, timezone.now()
    conflict.version += 1
    conflict.save()
    batch.preview_hash = recompute_preview_hash(batch)
    batch.save(update_fields=["preview_hash"])


# --- confirm ---------------------------------------------------------------------


def test_confirm_moves_the_batch_to_committing_and_records_who(editor):
    batch = previewed(editor, [crm_row()])
    confirm(editor, batch)
    batch.refresh_from_db()
    assert batch.status == "COMMITTING" and batch.attempt_count == 1
    assert batch.confirmed_by_id == editor.id and batch.confirmed_at is not None
    assert [h["to"] for h in batch.transition_history][-1] == "COMMITTING"


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL])
def test_only_an_editor_may_confirm_or_commit(editor, role):
    batch = previewed(editor, [crm_row()])
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["CRM_TEAM"])
    with pytest.raises(PermissionDenied):
        confirm_batch(actor=user, batch=batch, preview_hash=batch.preview_hash, operation_key="k")
    confirm(editor, batch)
    with pytest.raises(PermissionDenied):
        commit_batch(actor=user, batch=batch, operation_key="k2")


def test_confirm_is_blocked_while_a_blocking_conflict_is_open(editor):
    make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = previewed(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    with pytest.raises(ConfirmError) as raised:
        confirm(editor, batch)
    assert "BLOCKING_CONFLICTS_OPEN" in [b["code"] for b in raised.value.blockers]
    batch.refresh_from_db()
    assert batch.status == "AWAITING_RESOLUTION"


def test_confirm_refuses_a_hash_the_operator_did_not_see(editor):
    batch = previewed(editor, [crm_row()])
    with pytest.raises(ConfirmError) as raised:
        confirm_batch(actor=editor, batch=batch, preview_hash="0" * 64, operation_key="k")
    assert "PREVIEW_HASH_MISMATCH" in [b["code"] for b in raised.value.blockers]


def test_confirm_is_blocked_for_a_batch_still_in_preview(editor):
    batch = batch_from(editor, [crm_row(**{"0": "not a date"})])
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    with pytest.raises(ConfirmError) as raised:
        confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="k")
    codes = [b["code"] for b in raised.value.blockers]
    assert "ILLEGAL_STATE" in codes and "VALIDATION_BLOCKED" in codes


def test_confirm_replays_by_operation_key(editor):
    batch = previewed(editor, [crm_row()])
    first = confirm(editor, batch, key="same")
    again = confirm(editor, batch, key="same")
    assert first == again
    batch.refresh_from_db()
    assert batch.attempt_count == 1


# --- commit: what gets written -------------------------------------------------------


def test_commit_creates_customer_order_lines_lead_and_ownership(editor, seller):
    make_team("CRM_TEAM")
    batch, result = run(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    assert result["status"] == "COMMITTED" and result["created"] == 1
    assert batch.status == "COMMITTED" and batch.committed_at is not None

    customer = Customer.objects.get(phone1="0812345678")
    assert customer.staff_code == "S1" and customer.owner_display == "สมศรี ขาย"
    order = Order.objects.get(order_no="SO-100")
    assert order.customer_id == customer.id and order.order_date == date(2026, 10, 26)
    assert float(order.total_amount) == 1000 and order.source_type == "import"
    assert order.import_batch_id == batch.batch_id and order.staff_code == "S1"
    assert order.field_provenance["carrier"]["sourceId"] == "import"
    line = OrderLine.objects.get(order=order)
    assert (line.sku, line.quantity, float(line.amount)) == ("SKU1", 2, 1000)

    lead = Lead.objects.get(customer=customer, team__code="CRM_TEAM")
    assert lead.caretaker_id == seller.id and lead.total_orders == 1
    assert not IntakeItem.objects.exists()


def test_an_order_with_no_known_team_goes_to_global_unassigned_not_to_a_fake_team(editor):
    batch, _ = run(editor, [crm_row(**{str(TEAM): "ทีมไม่มีจริง"})])
    customer = Customer.objects.get(phone1="0812345678")
    assert not Lead.objects.filter(customer=customer).exists()
    item = IntakeItem.objects.get(customer=customer)
    assert item.source_order_id == "SO-100" and item.status == "GLOBAL_UNASSIGNED"


def test_a_second_run_of_the_same_unresolved_order_does_not_duplicate_intake(editor):
    run(editor, [crm_row(**{str(TEAM): "ทีมไม่มีจริง"})])
    batch = previewed(editor, [crm_row(**{str(TEAM): "ทีมไม่มีจริง"})])
    confirm(editor, batch, key="c2")
    commit_batch(actor=editor, batch=batch, operation_key="m2")
    assert IntakeItem.objects.count() == 1 and Order.objects.count() == 1


def test_imports_never_open_follow_up_cycles(editor, seller):
    run(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    assert not FollowupCycle.objects.exists()


def test_two_orders_for_the_same_new_phone_make_one_customer(editor):
    run(editor, [crm_row(order="SO-1"), crm_row(order="SO-2")])
    assert Customer.objects.count() == 1 and Order.objects.count() == 2


def test_a_known_customer_gets_the_order_not_a_duplicate_customer(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    run(editor, [crm_row()])
    assert Customer.objects.count() == 1
    assert Order.objects.get(order_no="SO-100").customer_id == customer.id


def test_an_existing_order_is_updated_field_by_field_and_lines_replaced(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    order = make_order(customer=customer, order_no="SO-100", with_line=True)
    order.tracking_no = ""
    order.save()
    batch, result = run(editor, [crm_row(**{str(TRACKING): "TH999", str(SKU): "NEW"})])
    order.refresh_from_db()
    assert result["updated"] == 1 and order.tracking_no == "TH999"
    assert [l.sku for l in order.lines.all()] == ["NEW"]
    assert Order.objects.count() == 1
    assert not order.import_batch_id  # an order this batch did not create keeps no batch tag


def test_an_unchanged_order_writes_nothing(editor):
    batch, _ = run(editor, [crm_row()])
    again = previewed(editor, [crm_row()])
    confirm(editor, again, key="c2")
    entries_before = ImportJournalEntry.objects.count()
    result = commit_batch(actor=editor, batch=again, operation_key="m2")
    assert result["unchanged"] == 1 and result["created"] == 0
    assert ImportJournalEntry.objects.filter(batch=again).count() == 0
    assert ImportJournalEntry.objects.count() == entries_before


# --- commit: conflict decisions ------------------------------------------------------


def test_identity_resolved_auto_match_merges_into_the_existing_customer(editor):
    existing = make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = previewed(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    resolve(batch, "IDENTITY", "AUTO_MATCH")
    confirm(editor, batch)
    commit_batch(actor=editor, batch=batch, operation_key="m")
    assert Customer.objects.count() == 1
    assert Order.objects.get(order_no="SO-100").customer_id == existing.id


def test_identity_resolved_new_customer_creates_a_split_customer(editor):
    existing = make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = previewed(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    resolve(batch, "IDENTITY", "NEW_CUSTOMER")
    confirm(editor, batch)
    commit_batch(actor=editor, batch=batch, operation_key="m")
    assert Customer.objects.count() == 2
    created = Customer.objects.exclude(pk=existing.pk).get()
    assert created.customer_name == "สมชาย ใจดี" and created.phone_key.endswith("#SO-100")
    assert Order.objects.get(order_no="SO-100").customer_id == created.id


def test_a_stopped_lead_is_reactivated_only_when_a_person_chose_it(editor, seller):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", caretaker=seller, lifecycle_status="STOPPED")
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    resolve(batch, "REACTIVATION", "REACTIVATE")
    confirm(editor, batch)
    commit_batch(actor=editor, batch=batch, operation_key="m")
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_keep_stopped_leaves_the_lead_stopped_but_still_records_the_order(editor, seller):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", caretaker=seller, lifecycle_status="STOPPED")
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    resolve(batch, "REACTIVATION", "KEEP_STOPPED")
    confirm(editor, batch)
    commit_batch(actor=editor, batch=batch, operation_key="m")
    lead.refresh_from_db()
    assert lead.lifecycle_status == "STOPPED" and Order.objects.filter(order_no="SO-100").exists()


def test_an_exclusive_lead_keeps_its_caretaker_unless_reassign_was_chosen(editor, seller):
    holder = make_user(email="holder@example.com", role=ROLE_TELESELL, staff_code="S2", staff_name="ผู้ดูแลเดิม")
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", caretaker=holder)
    run(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    lead.refresh_from_db()
    assert lead.caretaker_id == holder.id


def test_reassign_moves_an_exclusive_lead_to_the_closer(editor, seller):
    holder = make_user(email="holder2@example.com", role=ROLE_TELESELL, staff_code="S2", staff_name="ผู้ดูแลเดิม")
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", caretaker=holder)
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    resolve(batch, "OWNERSHIP", "REASSIGN")
    confirm(editor, batch)
    commit_batch(actor=editor, batch=batch, operation_key="m")
    lead.refresh_from_db()
    assert lead.caretaker_id == seller.id


# --- commit: chunking, idempotency, failure ----------------------------------------------


def test_commit_is_chunked(editor):
    rows = [crm_row(order=f"SO-{i:04d}", phone=f"08{i:08d}") for i in range(250)]
    batch, result = run(editor, rows, chunk_size=100)
    assert result["chunks"] == 3 and Order.objects.count() == 250


def test_chunk_size_is_clamped():
    from crm.importv2.services.commit import DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE

    assert (DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE) == (100, 200)


def test_replaying_a_commit_key_creates_no_duplicates(editor):
    batch, first = run(editor, [crm_row()])
    again = commit_batch(actor=editor, batch=batch, operation_key="commit-1")
    assert again == first
    assert Order.objects.count() == 1 and OrderLine.objects.count() == 1


def test_a_new_key_on_a_committed_batch_is_refused(editor):
    batch, _ = run(editor, [crm_row()])
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        commit_batch(actor=editor, batch=batch, operation_key="another")


def test_a_stale_preview_aborts_before_any_write(editor):
    batch = previewed(editor, [crm_row()])
    confirm(editor, batch)
    plan = batch.plans.get()
    plan.facts["fields"]["total"] = 123456  # someone changed the staged data after approval
    plan.save()
    with pytest.raises(StalePreviewError):
        commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    assert batch.status == "FAILED_RETRYABLE"
    assert not Order.objects.exists() and not Customer.objects.exists()


def test_a_failure_in_a_later_chunk_keeps_the_earlier_chunk_and_journals_it(editor, monkeypatch):
    rows = [crm_row(order=f"SO-{i:04d}", phone=f"08{i:08d}") for i in range(150)]
    batch = previewed(editor, rows)
    confirm(editor, batch)
    real = commit_module._apply_group
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 120:
            raise RuntimeError("database went away")
        return real(**kwargs)

    monkeypatch.setattr(commit_module, "_apply_group", flaky)
    with pytest.raises(RuntimeError):
        commit_batch(actor=editor, batch=batch, operation_key="m", chunk_size=100)
    batch.refresh_from_db()
    assert batch.status == "FAILED_RETRYABLE"
    assert Order.objects.count() == 100  # chunk one stays, chunk two left nothing behind
    journalled = ImportJournalEntry.objects.filter(batch=batch, entity="ORDER").count()
    assert journalled == 100


def test_the_journal_records_every_kind_of_write(editor, seller):
    make_team("CRM_TEAM")
    batch, _ = run(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    entities = set(ImportJournalEntry.objects.filter(batch=batch).values_list("entity", flat=True))
    assert {"CUSTOMER", "ORDER", "ORDER_LINE", "LEAD", "OWNERSHIP_SEGMENT", "LEAD_AUDIT"} <= entities
    seqs = list(ImportJournalEntry.objects.filter(batch=batch).order_by("seq").values_list("seq", flat=True))
    assert seqs == list(range(1, len(seqs) + 1))


def test_invalid_orders_are_never_written(editor):
    # An invalid order blocks Confirm, but guard the commit too: it skips INVALID plans.
    batch = previewed(editor, [crm_row(), crm_row(order="SO-200", phone="0899999999")])
    ImportBatch.objects.filter(pk=batch.pk).update(status="COMMITTING", attempt_count=1)
    batch.refresh_from_db()
    plan = batch.plans.get(order_id="SO-200")
    plan.classification, plan.facts = "INVALID", {}
    plan.save()
    batch.preview_hash = recompute_preview_hash(batch)
    batch.save()
    commit_batch(actor=editor, batch=batch, operation_key="m")
    assert list(Order.objects.values_list("order_no", flat=True)) == ["SO-100"]


def test_the_customer_order_rollups_are_kept_current(editor):
    run(editor, [crm_row(order="SO-1"), crm_row(order="SO-2", **{"0": "27/10/2569"})])
    customer = Customer.objects.get(phone1="0812345678")
    assert customer.order_count == 2
    assert customer.last_order_date == date(2026, 10, 27)
    assert customer.last_order.order_no == "SO-2"
