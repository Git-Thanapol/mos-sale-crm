"""Preview: classification of every staged order, and no business write.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_preview.py
"""

import pytest
from django.core.exceptions import PermissionDenied

from crm.customers.models import Customer
from crm.domain.import_errors import BatchBusinessDateError
from crm.importv2.models import ImportBatch
from crm.importv2.services.preview import build_preview, recompute_preview_hash, set_batch_business_date
from crm.intake.models import IntakeItem
from crm.leads.models import Lead
from crm.orders.models import Order, OrderLine
from tests.db.factories import (
    ROLE_EDITOR,
    ROLE_TELESELL,
    make_customer,
    make_lead,
    make_order,
    make_user,
)
from tests.db.importv2_helpers import (
    CARRIER,
    NAME,
    PRICE,
    SELLER,
    SKU,
    TEAM,
    TRACKING,
    batch_from,
    crm_row,
    xlsx,
)

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


@pytest.fixture
def seller():
    return make_user(email="seller@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี ขาย")


def counts():
    return (
        Customer.objects.count(),
        Order.objects.count(),
        OrderLine.objects.count(),
        Lead.objects.count(),
        IntakeItem.objects.count(),
    )


def plan_of(batch, order_id):
    return batch.plans.get(order_id=order_id)


def test_preview_writes_no_business_data(editor, seller):
    batch = batch_from(editor, [crm_row(), crm_row(order="SO-101", phone="0899999999")])
    before = counts()
    build_preview(actor=editor, batch=batch)
    assert counts() == before


def test_a_new_order_for_a_new_customer(editor):
    batch = batch_from(editor, [crm_row()])
    aggregate = build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    assert aggregate["rows"] == 1 and aggregate["newOrders"] == 1 and aggregate["newCustomers"] == 1
    assert plan_of(batch, "SO-100").classification == "NEW_ORDER"
    assert batch.status == "AWAITING_RESOLUTION" and len(batch.preview_hash) == 64
    assert batch.preview_aggregate == aggregate


def test_line_rows_of_one_order_become_one_order_with_a_summed_total(editor):
    rows = [
        crm_row(**{str(SKU): "A", str(PRICE): "300"}),
        crm_row(**{str(SKU): "B", str(PRICE): "700"}),
    ]
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    assert batch.plans.count() == 1
    facts = batch.plans.get().facts
    assert facts["fields"]["total"] == 1000
    assert [l["sku"] for l in facts["fields"]["items"]] == ["A", "B"]


def test_lines_with_the_same_sku_and_name_are_merged(editor):
    rows = [crm_row(**{str(PRICE): "100"}), crm_row(**{str(PRICE): "200"})]
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    items = batch.plans.get().facts["fields"]["items"]
    assert len(items) == 1 and items[0]["quantity"] == 4 and items[0]["amount"] == 300


def test_a_repeat_of_an_existing_order_matches_its_customer_and_fills_empty_fields(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    make_order(customer=customer, order_no="SO-100")
    batch = batch_from(editor, [crm_row()])
    build_preview(actor=editor, batch=batch)
    plan = plan_of(batch, "SO-100")
    assert plan.customer_id == customer.id and plan.identity_kind == "AUTO_MATCH"
    assert plan.classification == "SAFE_UPDATE"  # the file fills fields the order left empty


def test_a_new_tracking_number_on_an_existing_order_is_a_safe_update(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    order = make_order(customer=customer, order_no="SO-100")
    order.tracking_no = ""
    order.save()
    batch = batch_from(editor, [crm_row(**{str(TRACKING): "TH999"})])
    build_preview(actor=editor, batch=batch)
    plan = plan_of(batch, "SO-100")
    assert plan.classification == "SAFE_UPDATE"
    assert batch.preview_aggregate["safeUpdates"] == 1


def test_an_older_source_does_not_overwrite_a_newer_stored_value(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    order = make_order(customer=customer, order_no="SO-100")
    order.carrier = "Flash"
    order.field_provenance = {"carrier": {"sourceId": "other", "sourceUpdatedAt": 9_999_999_999_999, "precedence": 10}}
    order.save()
    batch = batch_from(editor, [crm_row(**{str(CARRIER): "Kerry"})])
    build_preview(actor=editor, batch=batch)
    plan = plan_of(batch, "SO-100")
    assert plan.evaluation["merge"]["staleInput"] == 1  # carrier: the stored value is newer


def test_the_same_phone_with_a_different_name_is_a_blocking_identity_conflict(editor):
    make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = batch_from(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    build_preview(actor=editor, batch=batch)
    conflict = batch.conflicts.get()
    assert conflict.kind == "IDENTITY" and conflict.severity == "BLOCKING" and conflict.status == "OPEN"
    assert conflict.options == ["AUTO_MATCH", "NEW_CUSTOMER"]
    assert plan_of(batch, "SO-100").classification == "CONFLICT"
    assert batch.preview_aggregate["blockingConflicts"] == 1
    assert plan_of(batch, "SO-100").customer_id is None


def test_a_different_closer_on_an_exclusive_lead_is_a_warning_not_a_block(editor, seller):
    holder = make_user(email="holder@example.com", role=ROLE_TELESELL, staff_code="S2", staff_name="ผู้ดูแลเดิม")
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    make_lead(customer=customer, team_code="CRM_TEAM", caretaker=holder)
    batch = batch_from(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    build_preview(actor=editor, batch=batch)
    conflict = batch.conflicts.get()
    assert conflict.kind == "OWNERSHIP" and conflict.severity == "WARNING"
    assert plan_of(batch, "SO-100").classification == "NEW_ORDER"


def test_an_order_for_a_stopped_lead_needs_a_reactivation_decision(editor, seller):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    make_lead(customer=customer, team_code="CRM_TEAM", caretaker=seller, lifecycle_status="STOPPED")
    batch = batch_from(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    build_preview(actor=editor, batch=batch)
    conflict = batch.conflicts.get()
    assert conflict.kind == "REACTIVATION" and conflict.severity == "BLOCKING"
    assert conflict.options == ["REACTIVATE", "KEEP_STOPPED"]


def test_an_unknown_closer_and_team_resolve_to_nobody_not_to_a_guess(editor):
    batch = batch_from(editor, [crm_row(**{str(SELLER): "ไม่มีคนนี้", str(TEAM): "ทีมไม่มีจริง"})])
    build_preview(actor=editor, batch=batch)
    fields = plan_of(batch, "SO-100").facts["fields"]
    assert fields["closerUserId"] is None and fields["closerTeamId"] is None


def test_an_ambiguous_staff_name_is_not_resolved(editor):
    make_user(email="a1@example.com", role=ROLE_TELESELL, staff_code="A1", staff_name="ชื่อซ้ำ")
    make_user(email="a2@example.com", role=ROLE_TELESELL, staff_code="A2", staff_name="ชื่อซ้ำ")
    batch = batch_from(editor, [crm_row(**{str(SELLER): "ชื่อซ้ำ"})])
    build_preview(actor=editor, batch=batch)
    assert plan_of(batch, "SO-100").facts["fields"]["closerUserId"] is None


def test_a_known_closer_and_team_are_resolved(editor, seller):
    make_lead(customer=make_customer(phone1="0899999999"), team_code="CRM_TEAM")
    batch = batch_from(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    build_preview(actor=editor, batch=batch)
    fields = plan_of(batch, "SO-100").facts["fields"]
    assert fields["closerUserId"] == str(seller.pk) and fields["closerTeamId"] == "CRM_TEAM"


def test_a_bad_row_makes_its_whole_order_invalid_and_blocks_confirm(editor):
    rows = [crm_row(), crm_row(**{str(SKU): "B", "0": "not a date"}), crm_row(order="SO-200", phone="0899999999")]
    batch = batch_from(editor, rows)
    aggregate = build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    assert plan_of(batch, "SO-100").classification == "INVALID"
    assert plan_of(batch, "SO-100").errors and plan_of(batch, "SO-200").classification == "NEW_ORDER"
    assert aggregate["invalidRows"] == 1
    assert batch.validation["ok"] is False
    assert "INVALID_ROWS" in [i["code"] for i in batch.validation["issues"]]
    assert batch.status == "PREVIEW"  # never reaches AWAITING_RESOLUTION, so it cannot be confirmed


def test_a_row_without_an_order_id_is_invalid(editor):
    batch = batch_from(editor, [crm_row(order=None)])
    build_preview(actor=editor, batch=batch)
    assert batch.plans.get().classification == "INVALID"


def test_an_unsupported_source_previews_to_nothing(editor):
    from tests.db.importv2_helpers import PROFILES
    from crm.importv2.services.upload import create_batch

    tele = PROFILES["tele-sales-report"]["headerSignature"]
    raw = xlsx([[1, "09:30", "ขาย", "SO-1", "ก", 1, 100, "ข"]], headers=tele)
    batch = create_batch(actor=editor, file_name="Tele 26.10.69.xlsx", raw=raw)
    aggregate = build_preview(actor=editor, batch=batch)
    assert aggregate["rows"] == 0 and not batch.plans.exists()
    batch.refresh_from_db()
    assert batch.status == "PREVIEW"


def test_only_an_editor_may_preview(editor):
    batch = batch_from(editor, [crm_row()])
    for role in ("TEAM_LEAD", ROLE_TELESELL):
        user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["CRM_TEAM"])
        with pytest.raises(PermissionDenied):
            build_preview(actor=user, batch=batch)
    assert not batch.plans.exists()


@pytest.mark.parametrize("status", ["COMMITTING", "COMMITTED", "ROLLED_BACK", "CANCELLED", "FAILED"])
def test_a_batch_past_preview_cannot_be_previewed_again(editor, status):
    batch = batch_from(editor, [crm_row()])
    ImportBatch.objects.filter(pk=batch.pk).update(status=status)
    batch.refresh_from_db()
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        build_preview(actor=editor, batch=batch)


def test_previewing_twice_rebuilds_without_duplicating(editor):
    make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = batch_from(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    build_preview(actor=editor, batch=batch)
    first_hash = ImportBatch.objects.get(pk=batch.pk).preview_hash
    batch.refresh_from_db()
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    assert batch.plans.count() == 1 and batch.conflicts.count() == 1
    assert batch.preview_hash == first_hash and batch.status == "AWAITING_RESOLUTION"


def test_the_preview_hash_changes_when_the_file_changes(editor):
    first = batch_from(editor, [crm_row()])
    second = batch_from(editor, [crm_row(**{str(PRICE): "999"})])
    build_preview(actor=editor, batch=first)
    build_preview(actor=editor, batch=second)
    assert first.preview_hash != second.preview_hash


def test_the_same_file_gives_the_same_hash_apart_from_the_file_digest(editor):
    batch = batch_from(editor, [crm_row()])
    build_preview(actor=editor, batch=batch)
    assert recompute_preview_hash(batch) == batch.preview_hash


def test_confirming_a_batch_date_changes_the_preview_hash(editor):
    batch = batch_from(editor, [crm_row()])
    build_preview(actor=editor, batch=batch)
    before = batch.preview_hash
    set_batch_business_date(actor=editor, batch=batch, text="2026-10-26")
    batch.refresh_from_db()
    assert batch.batch_business_date.isoformat() == "2026-10-26"
    assert batch.batch_date_meta["confirmed_by"] == editor.email
    assert batch.preview_hash != before


@pytest.mark.parametrize("text", ["", "not-a-date", "2026-02-30", "26/10/2026"])
def test_a_bad_batch_date_is_refused(editor, text):
    batch = batch_from(editor, [crm_row()])
    build_preview(actor=editor, batch=batch)
    with pytest.raises(BatchBusinessDateError):
        set_batch_business_date(actor=editor, batch=batch, text=text)
    batch.refresh_from_db()
    assert batch.batch_business_date is None


def test_only_an_editor_may_set_the_batch_date(editor):
    batch = batch_from(editor, [crm_row()])
    build_preview(actor=editor, batch=batch)
    user = make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L", team_scope=["CRM_TEAM"])
    with pytest.raises(PermissionDenied):
        set_batch_business_date(actor=user, batch=batch, text="2026-10-26")
