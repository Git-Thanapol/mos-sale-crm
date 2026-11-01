"""Migration decisions: Admin-only, revision/evidence-bound, audited; and the loader.

Run via: docker compose exec -T web python -m pytest tests/db/test_migration_decisions.py
"""

import json

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError

from crm.domain.migration_decisions import decision_id_for, evidence_hash_of
from crm.importv2.models import MigrationDecision, MigrationDecisionAudit
from crm.importv2.services.decisions import list_decisions, offered_actions, reopen_decision, resolve_decision, summary
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]

STAFF = {
    "kind": "STAFF_ALIAS", "historicalDisplayValue": "พนักงานเดิม 5", "normalizedValue": "พนักงานเดิม 5", "frequency": 200,
    "sourceContext": {"crm": 150}, "exactCandidates": [],
}
IDENTITY = {
    "kind": "IDENTITY_REVIEW", "normalizedPhone": "0812345678", "nameVariants": [], "nameVariantTotal": 0, "similarities": [],
    "sourceOrderCount": 2, "sampleOrderIds": ["SO-1"], "candidates": [{"customerId": "c1", "displayName": "ก", "similarity": 0.9}],
}
NO_PHONE = {**IDENTITY, "normalizedPhone": "", "candidates": []}


def make(category="STAFF_ALIAS", reason="NO_EXACT_CANDIDATE", evidence=STAFF, subject="s1", status="OPEN"):
    return MigrationDecision.objects.create(
        decision_id=decision_id_for(category, "snap", subject), category=category, status=status, reason_code=reason,
        subject_key=subject, source_snapshot_id="snap", evidence=evidence,
        evidence_hash=evidence_hash_of(category, subject, evidence),
    )


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1")


@pytest.fixture
def worker():
    return make_user(email="worker@example.com", role=ROLE_TELESELL, staff_code="S1", staff_name="สมศรี")


def resolve(actor, d, action="HISTORICAL_ONLY", payload=None, reason="ตรวจแล้ว", revision=None, digest=None):
    d.refresh_from_db()
    return resolve_decision(
        actor=actor, decision_id=d.decision_id, revision=d.revision if revision is None else revision,
        evidence_hash=d.evidence_hash if digest is None else digest, action=action, payload=payload, reason=reason,
    )


@pytest.mark.parametrize("role", ["TEAM_LEAD", ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_only_an_editor_may_use_the_decision_service(editor, role):
    d = make()
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X", team_scope=["A"])
    with pytest.raises(PermissionDenied):
        summary(user)
    with pytest.raises(PermissionDenied):
        list_decisions(user, category="STAFF_ALIAS")
    with pytest.raises(PermissionDenied):
        resolve(user, d)
    with pytest.raises(PermissionDenied):
        reopen_decision(actor=user, decision_id=d.decision_id, revision=1, reason="เหตุผล")
    d.refresh_from_db()
    assert d.status == "OPEN" and d.revision == 1


def test_summary_counts_open_and_deferred_but_not_resolved(editor):
    make(subject="a")
    make(subject="b", status="DEFERRED")
    make(subject="c", status="RESOLVED")
    make("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", {"kind": "UNKNOWN_STATUS"}, subject="d")
    counts = summary(editor)
    assert counts == {"STAFF_ALIAS": 2, "IDENTITY_REVIEW": 0, "UNKNOWN_STATUS": 1, "NULL_DATE_ORDER": 0}


def test_the_queue_filters_by_category_status_and_reason_and_pages_by_keyset(editor):
    for i in range(30):
        make(subject=f"s{i}", reason="NO_EXACT_CANDIDATE" if i % 2 else "EXACT_CANDIDATE_AVAILABLE")
    make(subject="done", status="RESOLVED")
    first = list_decisions(editor, category="STAFF_ALIAS", limit=25)
    assert len(first.items) == 25 and first.has_more
    second = list_decisions(editor, category="STAFF_ALIAS", limit=25, after_cursor=first.next_cursor)
    assert len(second.items) == 5 and not second.has_more
    assert {d.subject_key for d in first.items + second.items}.isdisjoint({"done"})
    only = list_decisions(editor, category="STAFF_ALIAS", reason_code="NO_EXACT_CANDIDATE", limit=50)
    assert {d.reason_code for d in only.items} == {"NO_EXACT_CANDIDATE"} and len(only.items) == 15
    assert [d.subject_key for d in list_decisions(editor, category="STAFF_ALIAS", status="RESOLVED").items] == ["done"]
    for junk in ("garbage", "e30", "W10"):
        assert len(list_decisions(editor, category="STAFF_ALIAS", after_cursor=junk, limit=25).items) == 25


@pytest.mark.parametrize("kwargs", [{"category": "NOPE"}, {"category": "STAFF_ALIAS", "status": "NOPE"}])
def test_a_bad_filter_is_refused(editor, kwargs):
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        list_decisions(editor, **kwargs)


def test_resolving_records_the_resolution_bumps_the_revision_and_audits(editor):
    d = make()
    resolved = resolve(editor, d, reason="  ชื่อเก่า ไม่ใช้แล้ว  ")
    assert resolved.status == "RESOLVED" and resolved.revision == 2
    assert resolved.resolution["action"] == "HISTORICAL_ONLY" and resolved.resolution["reason"] == "ชื่อเก่า ไม่ใช้แล้ว"
    assert resolved.resolution["resolvedBy"] == str(editor.pk) and resolved.resolution["mappingVersion"] == "phase8b-decision-v1"
    audit = MigrationDecisionAudit.objects.get(decision=d)
    assert (audit.from_status, audit.to_status, audit.revision_before, audit.revision_after) == ("OPEN", "RESOLVED", 1, 2)
    assert audit.resolved_by_id == editor.id and audit.evidence_hash == d.evidence_hash


def test_defer_keeps_it_unresolved(editor):
    d = make()
    resolved = resolve(editor, d, action="DEFER")
    assert resolved.status == "DEFERRED"
    assert summary(editor)["STAFF_ALIAS"] == 1


def test_a_stale_revision_or_evidence_hash_is_refused(editor):
    d = make()
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        resolve(editor, d, revision=5)
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        resolve(editor, d, digest="0" * 64)
    d.refresh_from_db()
    assert d.status == "OPEN" and not MigrationDecisionAudit.objects.exists()


def test_a_resolved_decision_cannot_be_resolved_again_until_reopened(editor):
    d = make()
    resolve(editor, d)
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        resolve(editor, d, action="DEFER")


def test_reopen_returns_it_to_open_with_a_new_revision_and_an_audit_row(editor):
    d = make()
    resolve(editor, d)
    d.refresh_from_db()
    reopened = reopen_decision(actor=editor, decision_id=d.decision_id, revision=d.revision, reason="ตัดสินผิด")
    assert reopened.status == "OPEN" and reopened.revision == 3 and reopened.resolution is None
    assert list(MigrationDecisionAudit.objects.filter(decision=d).order_by("id").values_list("action", flat=True)) == ["HISTORICAL_ONLY", "REOPEN"]
    resolve(editor, d, action="DEFER")  # needs the new revision, and works with it


@pytest.mark.parametrize("revision", [1, 99])
def test_only_the_current_resolved_revision_can_be_reopened(editor, revision):
    d = make()
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        reopen_decision(actor=editor, decision_id=d.decision_id, revision=revision, reason="เหตุผล")  # still OPEN
    resolve(editor, d)
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        reopen_decision(actor=editor, decision_id=d.decision_id, revision=revision, reason="เหตุผล")


def test_reopen_needs_a_reason(editor):
    d = make()
    resolve(editor, d)
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        reopen_decision(actor=editor, decision_id=d.decision_id, revision=2, reason="ab")


def test_an_unknown_decision_is_not_found(editor):
    with pytest.raises(ValueError, match="NOT_FOUND"):
        resolve_decision(actor=editor, decision_id="dec_x_00", revision=1, evidence_hash="0" * 64, action="DEFER", payload={}, reason="เหตุผล")


def test_resolution_rules_are_enforced_by_the_service(editor, worker):
    d = make()
    with pytest.raises(ValueError, match="REASON_REQUIRED"):
        resolve(editor, d, reason="ab")
    with pytest.raises(ValueError, match="ACTION_NOT_ALLOWED"):
        resolve(editor, d, action="MAP_TO_STATUS", payload={})
    with pytest.raises(ValueError, match="USER_NOT_FOUND"):
        resolve(editor, d, action="MAP_TO_USER", payload={"userId": "99999"})
    with pytest.raises(ValueError, match="USER_NOT_CARETAKER"):
        resolve(editor, d, action="MAP_TO_USER", payload={"userId": str(editor.pk)})
    mapped = resolve(editor, d, action="MAP_TO_USER", payload={"userId": str(worker.pk)})
    assert mapped.resolution["payload"] == {"userId": str(worker.pk), "userActiveAtApproval": True}


def test_a_disabled_user_is_mapped_for_history_only(editor):
    gone = make_user(email="gone@example.com", role=ROLE_TELESELL, staff_code="S9", is_active=False)
    d = make()
    resolved = resolve(editor, d, action="MAP_TO_USER", payload={"userId": str(gone.pk)})
    assert resolved.resolution["payload"]["userActiveAtApproval"] is False


def test_match_existing_needs_an_exact_phone_candidate(editor):
    d = make("IDENTITY_REVIEW", "NAME_CONFLICT", IDENTITY, subject="p1")
    with pytest.raises(ValueError, match="CANDIDATE_REQUIRED"):
        resolve(editor, d, action="MATCH_EXISTING", payload={"customerId": "stranger"})
    assert resolve(editor, d, action="MATCH_EXISTING", payload={"customerId": "c1"}).resolution["payload"] == {"customerId": "c1"}


def test_offered_actions_hide_match_existing_without_a_candidate_and_for_no_phone(editor):
    with_candidate = make("IDENTITY_REVIEW", "NAME_CONFLICT", IDENTITY, subject="p1")
    without = make("IDENTITY_REVIEW", "NAME_CONFLICT", {**IDENTITY, "candidates": []}, subject="p2")
    no_phone = make("IDENTITY_REVIEW", "NO_PHONE", NO_PHONE, subject="p3")
    assert offered_actions(with_candidate) == ["MATCH_EXISTING", "CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "DEFER"]
    assert offered_actions(without) == ["CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "DEFER"]
    assert offered_actions(no_phone) == ["KEEP_SEPARATE", "DEFER"]


# --- loader ---------------------------------------------------------------------------


def doc(subject="s1", evidence=STAFF, status="OPEN", category="STAFF_ALIAS"):
    return {
        "decisionId": decision_id_for(category, "snap", subject), "category": category, "status": status,
        "reasonCode": "NO_EXACT_CANDIDATE", "subjectKey": subject, "sourceSnapshotId": "snap", "evidence": evidence,
        "evidenceHash": evidence_hash_of(category, subject, evidence), "revision": 1, "resolution": None,
    }


def load(tmp_path, rows, *args):
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    call_command("load_migration_decisions", "--from", str(path), *args)


def test_the_loader_creates_open_decisions_and_is_idempotent(tmp_path, capsys):
    load(tmp_path, [doc("a"), doc("b")])
    assert MigrationDecision.objects.count() == 2 and "created 2" in capsys.readouterr().out
    load(tmp_path, [doc("a"), doc("b")])
    assert MigrationDecision.objects.count() == 2 and "unchanged 2" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    load(tmp_path, [doc("a")], "--dry-run")
    assert not MigrationDecision.objects.exists() and "DRY RUN: created 1" in capsys.readouterr().out


def test_the_loader_never_overwrites_a_resolved_decision(tmp_path, editor, capsys):
    load(tmp_path, [doc("a")])
    resolve(editor, MigrationDecision.objects.get())
    load(tmp_path, [doc("a")])
    kept = MigrationDecision.objects.get()
    assert kept.status == "RESOLVED" and "kept_resolved 1" in capsys.readouterr().out


def test_changed_evidence_for_an_unresolved_decision_is_refused_not_overwritten(tmp_path):
    load(tmp_path, [doc("a")])
    changed = {**STAFF, "frequency": 999}
    with pytest.raises(CommandError, match="drifted"):
        load(tmp_path, [doc("a", evidence=changed)])
    assert MigrationDecision.objects.get().evidence["frequency"] == 200


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(evidenceHash="0" * 64),
        lambda d: d.update(decisionId="dec_staff_alias_" + "0" * 32),
        lambda d: d.update(category="NOPE"),
        lambda d: d.update(status="RESOLVED"),
        lambda d: d.pop("subjectKey"),
    ],
    ids=["bad-hash", "bad-id", "bad-category", "resolved", "missing-field"],
)
def test_a_malformed_row_is_refused_and_nothing_from_it_is_written(tmp_path, mutate):
    row = doc("a")
    mutate(row)
    with pytest.raises(CommandError, match="invalid"):
        load(tmp_path, [row])
    assert not MigrationDecision.objects.exists()


@pytest.mark.parametrize("content", ["[]", "{}", "not json"])
def test_an_unusable_file_is_refused(tmp_path, content):
    path = tmp_path / "x.json"
    path.write_text(content)
    with pytest.raises(CommandError):
        call_command("load_migration_decisions", "--from", str(path))


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(CommandError, match="does not exist"):
        call_command("load_migration_decisions", "--from", str(tmp_path / "nope.json"))
