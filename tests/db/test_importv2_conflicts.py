"""Import conflicts: scoped visibility, scoped resolution, optimistic concurrency.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_conflicts.py
"""

import pytest
from django.core.exceptions import PermissionDenied

from crm.importv2.models import ImportBatch, ImportConflict
from crm.importv2.services.commit import commit_batch, confirm_batch
from crm.importv2.services.conflicts import (
    latest_batch_with_open_conflicts,
    list_conflicts,
    resolve_conflict,
    version_hash,
)
from crm.importv2.services.preview import build_preview
from crm.customers.models import Customer
from crm.orders.models import Order
from tests.db.factories import ROLE_ADMIN, ROLE_EDITOR, ROLE_TELESELL, ROLE_VIEWER, make_customer, make_team, make_user
from tests.db.importv2_helpers import NAME, batch_from, crm_row

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E1", staff_name="แอดมิน")


@pytest.fixture
def team_lead():
    return make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"])


def bare_batch(editor, status="AWAITING_RESOLUTION"):
    batch = batch_from(editor, [crm_row()])
    ImportBatch.objects.filter(pk=batch.pk).update(status=status)
    batch.refresh_from_db()
    return batch


def conflict(batch, row=1, kind="OWNERSHIP", team="TEAM_A", affected=None, options=("KEEP_CURRENT", "REASSIGN"), severity="BLOCKING"):
    team_obj = make_team(team) if team else None
    return ImportConflict.objects.create(
        batch=batch, conflict_id=f"{row}:{kind}:1", row_index=row, kind=kind, severity=severity,
        team=team_obj, affected_team_codes=[team] if affected is None and team else (affected or []),
        summary="สรุป", source_row_ref=f"row:{row}", options=list(options),
    )


def resolve(actor, c, choice="KEEP_CURRENT", key="k1", version=None, digest=None):
    c.refresh_from_db()
    return resolve_conflict(
        actor=actor, conflict=c, resolution=choice, operation_key=key,
        expected_version=c.version if version is None else version,
        expected_hash=version_hash(c) if digest is None else digest,
    )


# --- who sees what -------------------------------------------------------------------


def test_an_editor_sees_every_open_conflict(editor):
    batch = bare_batch(editor)
    conflict(batch, row=1, team="TEAM_A")
    conflict(batch, row=2, team="TEAM_B")
    conflict(batch, row=3, kind="IDENTITY", team="TEAM_A", options=("AUTO_MATCH", "NEW_CUSTOMER"))
    assert [c.row_index for c in list_conflicts(editor, batch=batch).items] == [1, 2, 3]


def test_a_team_lead_sees_only_own_team_non_identity_single_team_conflicts(editor, team_lead):
    batch = bare_batch(editor)
    mine = conflict(batch, row=1, team="TEAM_A")
    conflict(batch, row=2, team="TEAM_B")
    conflict(batch, row=3, kind="IDENTITY", team="TEAM_A", options=("AUTO_MATCH", "NEW_CUSTOMER"))
    conflict(batch, row=4, team="TEAM_A", affected=["TEAM_A", "TEAM_B"])
    conflict(batch, row=5, team=None, affected=[])
    seen = list_conflicts(team_lead, batch=batch).items
    assert [c.pk for c in seen] == [mine.pk]


@pytest.mark.parametrize("role", [ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_other_roles_see_nothing(editor, role):
    batch = bare_batch(editor)
    conflict(batch)
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X")
    assert list_conflicts(user, batch=batch).items == []


def test_a_team_lead_without_a_scope_sees_nothing(editor):
    batch = bare_batch(editor)
    conflict(batch)
    user = make_user(email="tl0@example.com", role="TEAM_LEAD", staff_code="L0", team_scope=[])
    assert list_conflicts(user, batch=batch).items == []


def test_only_open_conflicts_are_listed(editor):
    batch = bare_batch(editor)
    done = conflict(batch, row=1)
    conflict(batch, row=2)
    resolve(editor, done)
    assert [c.row_index for c in list_conflicts(editor, batch=batch).items] == [2]


def test_conflicts_page_by_keyset_and_a_garbage_cursor_restarts(editor):
    batch = bare_batch(editor)
    for row in range(1, 31):
        conflict(batch, row=row)
    first = list_conflicts(editor, batch=batch, limit=25)
    assert len(first.items) == 25 and first.has_more
    second = list_conflicts(editor, batch=batch, limit=25, after_cursor=first.next_cursor)
    assert [c.row_index for c in second.items] == [26, 27, 28, 29, 30] and not second.has_more
    for junk in ("garbage", "e30", "W10", "WzEsMl0"):
        assert [c.pk for c in list_conflicts(editor, batch=batch, limit=25, after_cursor=junk).items] == [
            c.pk for c in first.items
        ]


def test_the_latest_batch_with_open_conflicts_for_a_user(editor, team_lead):
    old = bare_batch(editor)
    conflict(old, team="TEAM_A")
    newer = bare_batch(editor)
    conflict(newer, team="TEAM_B")
    assert latest_batch_with_open_conflicts(editor) == newer
    assert latest_batch_with_open_conflicts(team_lead) == old  # the newer one holds only TEAM_B conflicts


# --- who resolves --------------------------------------------------------------------


def test_a_team_lead_resolves_a_conflict_in_their_own_team(editor, team_lead):
    batch = bare_batch(editor)
    c = conflict(batch, team="TEAM_A")
    result = resolve(team_lead, c)
    c.refresh_from_db()
    assert result["conflictId"] == c.conflict_id and c.status == "RESOLVED" and c.resolved_by_id == team_lead.id


@pytest.mark.parametrize(
    "build",
    [
        lambda b: conflict(b, row=1, kind="IDENTITY", team="TEAM_A", options=("AUTO_MATCH", "NEW_CUSTOMER")),
        lambda b: conflict(b, row=2, team="TEAM_B"),
        lambda b: conflict(b, row=3, team="TEAM_A", affected=["TEAM_A", "TEAM_B"]),
        lambda b: conflict(b, row=4, team=None, affected=[]),
    ],
    ids=["identity", "other-team", "multi-team", "no-team"],
)
def test_a_team_lead_cannot_resolve_outside_their_scope(editor, team_lead, build):
    batch = bare_batch(editor)
    c = build(batch)
    with pytest.raises(PermissionDenied):
        resolve(team_lead, c)
    c.refresh_from_db()
    assert c.status == "OPEN"


@pytest.mark.parametrize("role", [ROLE_TELESELL, ROLE_ADMIN, ROLE_VIEWER])
def test_other_roles_cannot_resolve(editor, role):
    batch = bare_batch(editor)
    c = conflict(batch)
    user = make_user(email=f"{role}@example.com", role=role, staff_code="X")
    with pytest.raises(PermissionDenied):
        resolve(user, c)


def test_an_editor_resolves_an_identity_conflict(editor):
    batch = bare_batch(editor)
    c = conflict(batch, kind="IDENTITY", options=("AUTO_MATCH", "NEW_CUSTOMER"))
    resolve(editor, c, choice="NEW_CUSTOMER")
    c.refresh_from_db()
    assert c.resolution == "NEW_CUSTOMER"


# --- what resolving does ------------------------------------------------------------------


def test_an_option_the_conflict_does_not_offer_is_refused(editor):
    batch = bare_batch(editor)
    c = conflict(batch)
    with pytest.raises(ValueError, match="INVALID_RESOLUTION"):
        resolve(editor, c, choice="APPROVE_REVERSAL")


def test_resolving_bumps_the_version_changes_the_digest_and_logs_the_event(editor):
    batch = bare_batch(editor, status="PREVIEW")
    ImportBatch.objects.filter(pk=batch.pk).update(status="AWAITING_RESOLUTION")
    batch.refresh_from_db()
    c = conflict(batch)
    version_before, hash_before = c.version, batch.preview_hash
    resolve(editor, c)
    c.refresh_from_db()
    batch.refresh_from_db()
    assert c.version == version_before + 1 and c.resolved_at is not None
    assert batch.preview_hash != hash_before
    assert batch.transition_history[-1]["to"] == "AWAITING_RESOLUTION" and "resolved" in batch.transition_history[-1]["reason"]


def test_a_stale_version_or_hash_is_refused(editor):
    batch = bare_batch(editor)
    c = conflict(batch)
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        resolve(editor, c, version=99)
    with pytest.raises(ValueError, match="CONCURRENT_MODIFICATION"):
        resolve(editor, c, digest="0" * 64)
    c.refresh_from_db()
    assert c.status == "OPEN"


def test_replaying_a_resolution_key_is_idempotent_and_a_new_key_is_refused(editor):
    batch = bare_batch(editor)
    c = conflict(batch)
    first = resolve(editor, c, key="same")
    again = resolve_conflict(
        actor=editor, conflict=c, resolution="KEEP_CURRENT", operation_key="same",
        expected_version=1, expected_hash="irrelevant on replay",
    )
    assert again == first
    with pytest.raises(ValueError, match="ALREADY_RESOLVED"):
        resolve(editor, c, key="other")


@pytest.mark.parametrize("status", ["PREVIEW", "COMMITTING", "COMMITTED", "CANCELLED"])
def test_a_batch_outside_resolution_has_nothing_to_resolve(editor, status):
    batch = bare_batch(editor, status=status)
    c = conflict(batch)
    with pytest.raises(ValueError, match="ILLEGAL_STATE"):
        resolve(editor, c)


def test_resolving_the_last_blocking_conflict_lets_the_batch_be_confirmed_and_committed(editor):
    existing = make_customer(customer_name="ประเสริฐ ศรีสุข", phone1="0812345678")
    batch = batch_from(editor, [crm_row(**{str(NAME): "สมชาย ใจดี"})])
    build_preview(actor=editor, batch=batch)
    batch.refresh_from_db()
    blocking = batch.conflicts.get()
    from crm.domain.import_errors import ConfirmError

    with pytest.raises(ConfirmError):
        confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c0")
    result = resolve(editor, blocking, choice="AUTO_MATCH")
    assert result["openBlockingConflicts"] == 0
    batch.refresh_from_db()
    confirm_batch(actor=editor, batch=batch, preview_hash=batch.preview_hash, operation_key="c1")
    commit_batch(actor=editor, batch=batch, operation_key="m1")
    assert Customer.objects.count() == 1
    assert Order.objects.get(order_no="SO-100").customer_id == existing.id
