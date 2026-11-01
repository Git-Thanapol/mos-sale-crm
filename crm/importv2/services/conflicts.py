"""Import conflicts: who sees them, who resolves them, and what resolving changes.

Authorization is the ported rule (crm.domain.conflict_scope.assert_resolver_scope):
an EDITOR resolves anything; a TEAM_LEAD resolves a conflict that belongs to
exactly one team inside their own scope and is not an IDENTITY conflict; nobody
else resolves. Resolution is optimistic: the caller names the version and hash
of the conflict it looked at, so two people cannot resolve the same row on stale
information.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.core.pagination import KeysetPage, decode_cursor_parts, encode_cursor
from crm.core.permissions import import_actor_role, team_scope
from crm.domain.conflict_scope import assert_resolver_scope, conflict_version_hash
from crm.domain.import_errors import ApplicationError
from crm.importv2.models import ImportBatch, ImportConflict
from crm.importv2.services.commit import _remember, _replay
from crm.importv2.services.preview import recompute_preview_hash
from crm.importv2.services.state import transition

DEFAULT_LIMIT = 25
MAX_LIMIT = 50


def conflict_record(conflict: ImportConflict) -> dict:
    """The conflict as the prototype's hashed document (camelCase)."""
    return {
        "conflictId": conflict.conflict_id,
        "batchId": str(conflict.batch.batch_id),
        "rowIndex": conflict.row_index,
        "kind": conflict.kind,
        "severity": conflict.severity,
        "status": conflict.status,
        "teamId": conflict.team.code if conflict.team_id else None,
        "affectedTeamIds": list(conflict.affected_team_codes or []),
        "summary": conflict.summary,
        "sourceRowRef": conflict.source_row_ref,
        "options": list(conflict.options or []),
        "version": conflict.version,
        "resolution": conflict.resolution or None,
    }


def version_hash(conflict: ImportConflict) -> str:
    return conflict_version_hash(conflict_record(conflict))


def _resolver(actor) -> dict | None:
    role = import_actor_role(actor)
    if role is None:
        return None
    return {"role": role, "team_ids": sorted(team_scope(actor))}


def visible_conflicts(user, qs=None):
    """The conflicts this user may see: all for an EDITOR; for a TEAM_LEAD only
    non-identity conflicts that belong to exactly one of their teams."""
    qs = ImportConflict.objects.select_related("batch", "team") if qs is None else qs
    resolver = _resolver(user)
    if resolver is None:
        return qs.none()
    if resolver["role"] == "ADMIN":
        return qs
    if resolver["role"] == "TEAM_LEAD" and resolver["team_ids"]:
        scoped = Q()
        for code in resolver["team_ids"]:
            scoped |= Q(affected_team_codes=[code]) | (Q(affected_team_codes=[]) & Q(team__code=code))
        return qs.exclude(kind="IDENTITY").filter(scoped)
    return qs.none()


def latest_batch_with_open_conflicts(user) -> ImportBatch | None:
    ids = visible_conflicts(user).filter(status="OPEN").order_by("-batch_id").values_list("batch_id", flat=True)[:1]
    return ImportBatch.objects.filter(pk__in=list(ids)).first()


def list_conflicts(user, *, batch: ImportBatch, after_cursor: str | None = None, limit: int = DEFAULT_LIMIT) -> KeysetPage:
    """Open conflicts of a batch, in row order, keyset-paged (no COUNT, no OFFSET)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    qs = visible_conflicts(user).filter(batch=batch, status="OPEN").order_by("row_index", "id")
    parts = decode_cursor_parts(after_cursor, ("int", "int"))
    if parts is not None:
        row_index, last_id = parts
        qs = qs.filter(Q(row_index__gt=row_index) | Q(row_index=row_index, id__gt=last_id))
    rows = list(qs[: limit + 1])
    items = rows[:limit]
    more = len(rows) > limit
    cursor = encode_cursor((items[-1].row_index, items[-1].id)) if more and items else None
    return KeysetPage(items=items, next_cursor=cursor, has_more=more)


def resolve_conflict(
    *,
    actor,
    conflict: ImportConflict,
    resolution: str,
    operation_key: str,
    expected_version: int,
    expected_hash: str,
    reason: str = "",
) -> dict:
    """Record a person's decision and re-derive the approval digest."""
    replay = _replay(conflict.batch, operation_key)
    if replay is not None:
        return replay
    resolver = _resolver(actor)
    if resolver is None:
        raise PermissionDenied("ไม่มีสิทธิ์แก้ Conflict")
    try:
        assert_resolver_scope(resolver, conflict_record(conflict))
    except ApplicationError as error:
        raise PermissionDenied(error.message) from error

    with transaction.atomic():
        locked = ImportConflict.objects.select_for_update(of=("self",)).select_related("batch", "team").get(pk=conflict.pk)
        batch = ImportBatch.objects.select_for_update().get(pk=locked.batch_id)
        if batch.status != "AWAITING_RESOLUTION":
            raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} has no resolvable conflicts")
        if locked.status != "OPEN":
            raise ValueError("ALREADY_RESOLVED")
        if resolution not in (locked.options or []):
            raise ValueError("INVALID_RESOLUTION")
        if locked.version != expected_version or version_hash(locked) != expected_hash:
            raise ValueError("CONCURRENT_MODIFICATION")

        locked.status = "RESOLVED"
        locked.resolution = resolution
        locked.version += 1
        locked.resolved_by = actor
        locked.resolved_at = timezone.now()
        locked.reason = (reason or "")[:255]
        locked.save()

        batch.preview_hash = recompute_preview_hash(batch)
        batch.save(update_fields=["preview_hash", "updated_at"])
        transition(batch, "AWAITING_RESOLUTION", actor=actor, reason=f"resolved {locked.conflict_id}: {resolution}")
        open_blocking = batch.conflicts.filter(severity="BLOCKING", status="OPEN").count()
        return _remember(
            batch,
            operation_key,
            "resolve",
            {
                "conflictId": locked.conflict_id,
                "version": locked.version,
                "previewHash": batch.preview_hash,
                "openBlockingConflicts": open_blocking,
            },
        )
