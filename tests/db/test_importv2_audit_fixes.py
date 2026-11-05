"""Audit fixes for the import engine: rollback safety, cancel, migration
decisions, staleness, plan hash, PII-safe history.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_audit_fixes.py
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from crm.customers.models import Customer
from crm.domain.import_errors import ConfirmError
from crm.domain.migration_decisions import decision_id_for, evidence_hash_of
from crm.importv2.models import ImportBatch, MigrationDecision
from crm.importv2.services import commit as commit_module
from crm.importv2.services import preview as preview_module
from crm.importv2.services import rollback as rollback_module
from crm.importv2.services.cancel import cancel_batch, restage_batch
from crm.importv2.services.commit import QuotaExceededError, StaleResolutionError, commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview, recompute_preview_hash
from crm.importv2.services.rollback import find_drift, retry_rollback, rollback_batch
from crm.leadui import import_views
from crm.orders.models import Order
from tests.db.factories import ROLE_EDITOR, ROLE_TELESELL, make_customer, make_lead, make_order, make_team, make_user
from tests.db.importv2_helpers import CARETAKER, NAME, PHONE, SELLER, batch_from, crm_row

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


def previewed(editor, rows):
    batch = batch_from(editor, rows)
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    return batch


def committed(editor, rows):
    batch = previewed(editor, rows)
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    return batch


def decision(category, reason, evidence, subject, status="OPEN", action=None, payload=None):
    resolution = None
    if status == "RESOLVED" or action:
        resolution = {"action": action, "payload": payload or {}, "reason": "ok"}
    return MigrationDecision.objects.create(
        decision_id=decision_id_for(category, "snap", subject), category=category, status=status,
        reason_code=reason, subject_key=subject, source_snapshot_id="snap", evidence=evidence,
        evidence_hash=evidence_hash_of(category, subject, evidence), resolution=resolution,
    )


def staff_evidence(name):
    return {"kind": "STAFF_ALIAS", "historicalDisplayValue": name, "normalizedValue": name, "frequency": 1,
            "sourceContext": {}, "exactCandidates": []}


# --- rollback safety ------------------------------------------------------------------


def test_touching_updated_at_does_not_block_rollback(editor):
    batch = committed(editor, [crm_row()])
    Order.objects.update(updated_at=timezone.now())
    Customer.objects.update(updated_at=timezone.now())
    assert find_drift(batch) == []
    assert rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r")["status"] == "ROLLED_BACK"


def test_rollback_requires_a_reason(editor):
    batch = committed(editor, [crm_row()])
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        rollback_batch(actor=editor, batch=batch, reason="  ", operation_key="r")


def test_drift_that_appears_after_the_precheck_stops_the_chunk_untouched(editor, monkeypatch):
    batch = committed(editor, [crm_row(), crm_row(order="SO-2", phone="0899999999")])
    Order.objects.filter(order_no="SO-100").update(tracking_no="EDITED")
    monkeypatch.setattr(rollback_module, "find_drift", lambda b: [])  # the race: pre-check saw nothing
    orders, customers = Order.objects.count(), Customer.objects.count()
    result = rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r")
    batch.refresh_from_db()
    assert result["status"] == "ROLLBACK_FAILED" and batch.status == "ROLLBACK_FAILED"
    assert result["drift"][0]["why"] == "row was edited after the import"
    assert (Order.objects.count(), Customer.objects.count()) == (orders, customers)


def test_rollback_refuses_to_cascade_delete_newer_data(editor):
    batch = committed(editor, [crm_row()])
    customer = Customer.objects.get(phone1="0812345678")
    newer = make_order(customer=customer, order_no="LATER-1")  # not journalled by this batch
    result = rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r")
    assert result["status"] == "ROLLBACK_FAILED"
    assert "newer data depends" in result["drift"][0]["why"]
    assert Order.objects.filter(pk=newer.pk).exists() and Customer.objects.filter(pk=customer.pk).exists()


def test_retry_rollback_after_reconciliation_is_admin_only_and_resumes(editor):
    batch = committed(editor, [crm_row()])
    customer = Customer.objects.get(phone1="0812345678")
    newer = make_order(customer=customer, order_no="LATER-1")
    assert rollback_batch(actor=editor, batch=batch, reason="undo", operation_key="r1")["status"] == "ROLLBACK_FAILED"
    teller = make_user(email="t@example.com", role=ROLE_TELESELL, staff_code="T")
    with pytest.raises(PermissionDenied):
        retry_rollback(actor=teller, batch=batch, reason="x" * 5, operation_key="r2")
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        retry_rollback(actor=editor, batch=batch, reason="", operation_key="r2")
    newer.delete()  # the human reconciliation
    result = retry_rollback(actor=editor, batch=batch, reason="removed the later order", operation_key="r2")
    batch.refresh_from_db()
    assert result["status"] == "ROLLED_BACK" and batch.status == "ROLLED_BACK"
    assert [h["to"] for h in batch.transition_history][-3:] == ["ROLLBACK_FAILED", "ROLLING_BACK", "ROLLED_BACK"]
    assert Customer.objects.count() == 0


def test_retry_rollback_refuses_other_states(editor):
    batch = committed(editor, [crm_row()])
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        retry_rollback(actor=editor, batch=batch, reason="because", operation_key="r")


def test_history_offers_rollback_for_interrupted_states():
    assert {"COMMITTING", "ROLLING_BACK"} <= set(import_views.HISTORY_ROLLBACK_STATUSES)


# --- cancel / restage ------------------------------------------------------------------


def test_cancel_a_batch_that_wrote_nothing(editor):
    batch = previewed(editor, [crm_row()])
    result = cancel_batch(actor=editor, batch=batch, reason="wrong file", operation_key="x")
    batch.refresh_from_db()
    assert result["status"] == "CANCELLED" and batch.status == "CANCELLED"
    assert batch.transition_history[-1]["reason"] == "wrong file"
    assert cancel_batch(actor=editor, batch=batch, reason="wrong file", operation_key="x") == result


def test_cancel_is_admin_only_needs_a_reason_and_refuses_written_batches(editor):
    batch = previewed(editor, [crm_row()])
    with pytest.raises(PermissionDenied):
        cancel_batch(actor=make_user(email="t@example.com", role=ROLE_TELESELL, staff_code="T"), batch=batch, reason="xxx", operation_key="a")
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        cancel_batch(actor=editor, batch=batch, reason="", operation_key="b")
    done = committed(editor, [crm_row(order="SO-9", phone="0877777777")])
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        cancel_batch(actor=editor, batch=done, reason="late", operation_key="cancel-late")


def test_failed_retryable_with_a_journal_points_to_rollback(editor, monkeypatch):
    batch = previewed(editor, [crm_row(), crm_row(order="SO-2", phone="0899999999")])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    real = commit_module._apply_group
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("down")
        return real(**kw)

    monkeypatch.setattr(commit_module, "_apply_group", flaky)
    with pytest.raises(RuntimeError):
        commit_batch(actor=editor, batch=batch, operation_key="m", chunk_size=1)
    with pytest.raises(ValueError, match="Rollback"):
        cancel_batch(actor=editor, batch=batch, reason="give up", operation_key="x")


def test_stale_preview_dead_end_can_be_restaged(editor):
    batch = previewed(editor, [crm_row()])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    ImportBatch.objects.filter(pk=batch.pk).update(preview_hash="0" * 64)
    batch.refresh_from_db()
    with pytest.raises(commit_module.StalePreviewError):
        commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    assert batch.status == "FAILED_RETRYABLE"
    result = restage_batch(actor=editor, batch=batch, reason="re-preview", operation_key="rs")
    batch.refresh_from_db()
    fresh = ImportBatch.objects.get(batch_id=result["newBatchId"])
    assert batch.status == "CANCELLED" and fresh.status == "PREVIEW" and fresh.file_sha256 == batch.file_sha256


def test_quota_error_parks_the_batch_in_paused_quota(editor, monkeypatch):
    batch = previewed(editor, [crm_row()])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")

    def quota(**kw):
        raise QuotaExceededError("limit for 0812345678")

    monkeypatch.setattr(commit_module, "_apply_group", quota)
    with pytest.raises(QuotaExceededError):
        commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    assert batch.status == "PAUSED_QUOTA"
    assert "0812345678" not in str(batch.transition_history)


def test_failure_history_stores_class_and_code_never_the_message(editor, monkeypatch):
    batch = previewed(editor, [crm_row()])
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    monkeypatch.setattr(commit_module, "_apply_group", lambda **kw: (_ for _ in ()).throw(ValueError("สมชาย 0812345678")))
    with pytest.raises(ValueError):
        commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    text = str(batch.transition_history)
    assert "0812345678" not in text and "สมชาย" not in text and "ValueError" in text


def test_upload_is_recorded_with_actor(editor):
    batch = batch_from(editor, [crm_row()])
    assert batch.transition_history[0]["actor"] == "editor@example.com" and batch.transition_history[0]["reason"] == "upload"


# --- build_preview lock ------------------------------------------------------------------


def test_preview_rechecks_status_under_the_lock(editor):
    batch = previewed(editor, [crm_row()])
    ImportBatch.objects.filter(pk=batch.pk).update(status="COMMITTING")  # another request confirmed meanwhile
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        build_preview(actor=editor, batch=batch)  # `batch` in memory still says AWAITING_RESOLUTION
    assert batch.plans.count() == 1


# --- plan hash ---------------------------------------------------------------------------


def test_pipeline_version_is_part_of_the_hash(editor, monkeypatch):
    batch = previewed(editor, [crm_row()])
    same = recompute_preview_hash(batch)
    assert same == batch.preview_hash
    monkeypatch.setattr(preview_module, "IMPORT_PIPELINE_VERSION", "other")
    assert recompute_preview_hash(batch) != same


# --- migration decisions -------------------------------------------------------------------


def test_unresolved_staff_alias_blocks_confirm(editor):
    decision("STAFF_ALIAS", "NO_EXACT_CANDIDATE", staff_evidence("พนักงานเก่า"), "a1")
    batch = previewed(editor, [crm_row(**{str(SELLER): "พนักงานเก่า"})])
    conflict = batch.conflicts.get(kind="MAPPING")
    assert conflict.severity == "BLOCKING" and conflict.options == []
    with pytest.raises(ConfirmError):
        confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")


def test_deferred_alias_also_blocks(editor):
    decision("STAFF_ALIAS", "NO_EXACT_CANDIDATE", staff_evidence("พนักงานเก่า"), "a1", status="DEFERRED", action="DEFER")
    batch = previewed(editor, [crm_row(**{str(SELLER): "พนักงานเก่า"})])
    assert batch.conflicts.filter(kind="MAPPING", severity="BLOCKING").exists()


def test_alias_mapped_to_a_user_assigns_that_user(editor):
    make_team("CRM_TEAM")
    sale = make_user(email="s@example.com", role=ROLE_TELESELL, staff_code="S9", staff_name="คนจริง")
    decision("STAFF_ALIAS", "NO_EXACT_CANDIDATE", staff_evidence("พนักงานเก่า"), "a1", status="RESOLVED",
             action="MAP_TO_USER", payload={"userId": str(sale.pk)})
    batch = previewed(editor, [crm_row(**{str(SELLER): "พนักงานเก่า"})])
    assert not batch.conflicts.filter(kind="MAPPING").exists()
    assert batch.plans.get().facts["fields"]["closerUserId"] == str(sale.pk)


def test_historical_only_alias_keeps_the_name_and_assigns_nobody(editor):
    decision("STAFF_ALIAS", "NO_EXACT_CANDIDATE", staff_evidence("พนักงานเก่า"), "a1", status="RESOLVED", action="HISTORICAL_ONLY")
    batch = previewed(editor, [crm_row(**{str(SELLER): "พนักงานเก่า"})])
    plan = batch.plans.get()
    assert not batch.conflicts.filter(kind="MAPPING").exists()
    assert plan.facts["fields"]["closerUserId"] is None and plan.facts["customer"]["sellerName"] == "พนักงานเก่า"


def test_reopening_a_consumed_decision_makes_the_preview_stale(editor):
    d = decision("STAFF_ALIAS", "NO_EXACT_CANDIDATE", staff_evidence("พนักงานเก่า"), "a1", status="RESOLVED", action="HISTORICAL_ONLY")
    batch = previewed(editor, [crm_row(**{str(SELLER): "พนักงานเก่า"})])
    assert recompute_preview_hash(batch) == batch.preview_hash
    MigrationDecision.objects.filter(pk=d.pk).update(status="OPEN", revision=2, resolution=None)
    assert recompute_preview_hash(batch) != batch.preview_hash


def identity_evidence(phone):
    return {"kind": "IDENTITY_REVIEW", "normalizedPhone": phone, "nameVariants": [], "nameVariantTotal": 0,
            "similarities": [], "sourceOrderCount": 2, "sampleOrderIds": [], "candidates": []}


def test_identity_decision_create_new_makes_a_separate_customer(editor):
    make_customer(customer_name="คนละชื่อโดยสิ้นเชิง", phone1="0812345678", phone_key="0812345678")
    decision("IDENTITY_REVIEW", "NAME_CONFLICT", identity_evidence("0812345678"), "i1", status="RESOLVED", action="CREATE_NEW_CUSTOMER")
    batch = committed(editor, [crm_row()])
    assert batch.status == "COMMITTED"
    assert Customer.objects.filter(phone1="0812345678").count() == 2
    assert Customer.objects.filter(phone_key__contains="#SO-100").exists()


def test_open_identity_decision_blocks(editor):
    decision("IDENTITY_REVIEW", "NAME_CONFLICT", identity_evidence("0812345678"), "i1")
    batch = previewed(editor, [crm_row()])
    assert batch.conflicts.filter(kind="MAPPING", severity="BLOCKING").exists()


def test_null_date_decision_archive_excludes_the_order(editor):
    batch = previewed(editor, [crm_row()])
    # exercise the verdict directly: the CRM profile requires a date, so a null date is INVALID there
    from crm.importv2.services.adapters import Directory
    from crm.importv2.services.decision_gate import DecisionIndex

    decision("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", {"kind": "NULL_DATE_ORDER", "sourceOrderId": "SO-NODATE"}, "d1",
             status="RESOLVED", action="ARCHIVE_ONLY")
    verdict = DecisionIndex().verdict({"orderedAt": None}, "SO-NODATE", Directory())
    assert verdict.excluded
    decision("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", {"kind": "NULL_DATE_ORDER", "sourceOrderId": "SO-OPEN"}, "d2")
    assert DecisionIndex().verdict({"orderedAt": None}, "SO-OPEN", Directory()).blockers


# --- resolution staleness -------------------------------------------------------------------


def test_commit_refuses_a_resolution_made_against_a_changed_lead(editor):
    customer = make_customer(customer_name="สมชาย", phone1="0812345678", phone_key="0812345678")
    lead = make_lead(customer=customer, team_code="CRM_TEAM", policy={"ownershipPolicy": "LATEST_ORDER"})
    make_user(email="s@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี ขาย")
    batch = previewed(editor, [crm_row(**{str(SELLER): "สมศรี ขาย"})])
    plan = batch.plans.get()
    plan.facts["leadState"] = {**plan.facts["leadState"], "lifecycleStatus": "STOPPED"} if plan.facts.get("leadState") else {"x": 1}
    plan.save()
    ImportBatchConflictStub = batch.conflicts.create(
        conflict_id="x:OWNERSHIP:9", row_index=plan.row_index, kind="OWNERSHIP", severity="WARNING",
        status="RESOLVED", summary="s", options=["REASSIGN"], resolution="REASSIGN",
    )
    batch.preview_hash = recompute_preview_hash(batch)
    batch.save()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c")
    with pytest.raises(StaleResolutionError):
        commit_batch(actor=editor, batch=batch, operation_key="m")
    batch.refresh_from_db()
    assert batch.status == "FAILED_RETRYABLE" and not Order.objects.filter(order_no="SO-100").exists()
    assert ImportBatchConflictStub.pk


# --- multi-candidate identity ------------------------------------------------------------------


def test_several_qualifying_customers_leave_only_new_customer_as_a_choice(editor):
    make_customer(customer_name="สมชาย", phone1="0812345678", phone_key="0812345678")
    make_customer(customer_name="สมชาย", phone1="0812345678", phone_key="0812345678#2")
    batch = previewed(editor, [crm_row(name="สมชาย")])
    plan = batch.plans.get()
    assert plan.identity_kind == "HUMAN_REVIEW" and plan.facts["identity"]["candidateCustomerId"] is None
    assert len(plan.facts["identity"]["candidateCustomerIds"]) == 2
    conflict = batch.conflicts.get(kind="IDENTITY")
    assert conflict.options == ["NEW_CUSTOMER"] and "Candidates" in conflict.summary
