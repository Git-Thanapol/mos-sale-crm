"""Regressions for the final review's Important findings.

1. a conflict decision applied to every lead of the customer, not the row's lead;
2/3. confirm, commit and rollback could overlap on one batch;
4. rollback deleted audit rows the import never wrote;
5. an oversized CSV cell was an uncaught 500.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_review_fixes.py
"""

import psycopg
import pytest
from django.db import connection

from crm.importv2.services import upload
from crm.importv2.services.commit import commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview, recompute_preview_hash
from crm.importv2.services.rollback import rollback_batch
from crm.importv2.services.upload import UploadError
from crm.leads.models import Lead, LeadAuditEvent
from crm.orders.models import Order
from tests.db.factories import ROLE_EDITOR, ROLE_TELESELL, make_customer, make_lead, make_user
from tests.db.importv2_helpers import SELLER, TEAM, batch_from, crm_row

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


def test_a_reactivation_decision_only_reactivates_the_lead_of_the_rows_team(editor, seller):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678")
    crm = make_lead(customer=customer, team_code="CRM_TEAM", caretaker=seller, lifecycle_status="STOPPED")
    upsell = make_lead(customer=customer, team_code="UPSELL_TEAM", caretaker=seller, lifecycle_status="STOPPED")
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย", str(TEAM): "CRM Team"})])
    conflict = batch.conflicts.get(kind="REACTIVATION")
    conflict.status, conflict.resolution = "RESOLVED", "REACTIVATE"
    conflict.save()
    batch.preview_hash = recompute_preview_hash(batch)
    batch.save()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    crm.refresh_from_db()
    upsell.refresh_from_db()
    assert crm.lifecycle_status != "STOPPED"
    assert upsell.lifecycle_status == "STOPPED"


def _other_session():
    settings = connection.settings_dict
    return psycopg.connect(
        dbname=settings["NAME"], user=settings["USER"], password=settings["PASSWORD"],
        host=settings["HOST"], port=settings["PORT"], autocommit=True,
    )


def test_commit_and_rollback_refuse_to_overlap_with_a_running_operation(editor):
    batch = previewed(editor, [crm_row()])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    other = _other_session()
    try:
        other.execute("SELECT pg_advisory_lock(%s)", [7_000_000_000 + batch.pk])
        with pytest.raises(ValueError, match="ILLEGAL_STATE"):
            commit_batch(actor=editor, batch=batch, operation_key="m1")
        with pytest.raises(ValueError, match="ILLEGAL_STATE"):
            rollback_batch(actor=editor, batch=batch, reason="x", operation_key="r1")
        batch.refresh_from_db()
        assert batch.status == "COMMITTING" and not Order.objects.exists()
    finally:
        other.execute("SELECT pg_advisory_unlock(%s)", [7_000_000_000 + batch.pk])
        other.close()
    commit_batch(actor=editor, batch=batch, operation_key="m2")
    batch.refresh_from_db()
    assert batch.status == "COMMITTED"


def test_the_lock_is_released_after_a_failed_commit(editor, monkeypatch):
    from crm.importv2.services import commit as commit_module

    batch = previewed(editor, [crm_row()])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    monkeypatch.setattr(commit_module, "_apply_group", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        commit_batch(actor=editor, batch=batch, operation_key="m1")
    other = _other_session()
    try:
        got = other.execute("SELECT pg_try_advisory_lock(%s)", [7_000_000_000 + batch.pk]).fetchone()[0]
        assert got is True
        other.execute("SELECT pg_advisory_unlock(%s)", [7_000_000_000 + batch.pk])
    finally:
        other.close()


def test_rollback_does_not_delete_audit_rows_the_import_never_wrote(editor, seller):
    from tests.db.factories import make_team

    make_team("CRM_TEAM")
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    lead = Lead.objects.get()
    viewed = LeadAuditEvent.objects.create(
        lead=lead, customer=lead.customer, action="CUSTOMER_HISTORY_VIEW", actor=seller,
        payload={"kind": "ORDER_HISTORY_VIEW"},
    )
    result = rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r")
    assert result["status"] == "ROLLED_BACK" and not Lead.objects.exists()
    viewed.refresh_from_db()
    assert viewed.lead_id is None and viewed.action == "CUSTOMER_HISTORY_VIEW"


def test_an_oversized_csv_cell_is_a_refusal_not_a_crash():
    raw = b'a,b\n"' + b"x" * 200_000 + b'",1\n'
    with pytest.raises(UploadError) as raised:
        upload.read_table(raw, "f.csv")
    assert raised.value.code == "UNSUPPORTED_FILE"
