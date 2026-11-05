"""Migration decisions: queue, resolve, reopen (port of src/migration/decision-service.ts).

Admin only (the repo's EDITOR). A resolution is bound to the revision and the
evidence hash the admin reviewed: if either moved, the decision is stale and
nothing is written. Every resolve and reopen leaves an audit row.
"""

from __future__ import annotations

import time

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count

from crm.accounts.models import User
from crm.core.pagination import KeysetPage, decode_cursor_parts, encode_cursor
from crm.core.permissions import can_import_v2, is_team_lead, is_telesell
from crm.domain.migration_decisions import (
    DECISION_CATEGORIES,
    DECISION_MAPPING_VERSION,
    DECISION_STATUSES,
    UNRESOLVED_DECISION_STATUSES,
    DecisionValidationError,
    allowed_actions,
    next_status_for,
    validate_resolution,
)
from crm.importv2.models import MigrationDecision, MigrationDecisionAudit

DEFAULT_LIMIT = 25
MAX_LIMIT = 50


def _require_admin(actor) -> None:
    if not can_import_v2(actor):
        raise PermissionDenied("Migration decisions are Admin-only.")


def _caretaker_role(user: User) -> str:
    """A user's role as `validate_resolution` understands it. Anything that is
    not a working salesperson or team lead counts as an admin-type account,
    which can never be a historical caretaker."""
    if is_telesell(user):
        return "TELESALE"
    if is_team_lead(user):
        return "TEAM_LEAD"
    return "ADMIN"


def _lookup(user_id: str) -> dict | None:
    if not user_id.isdigit():
        return None
    user = User.objects.filter(pk=int(user_id)).first()
    return None if user is None else {"role": _caretaker_role(user), "active": bool(user.is_active)}


def summary(actor) -> dict[str, int]:
    """Unresolved (OPEN + DEFERRED) decisions per category."""
    _require_admin(actor)
    counts = {c: 0 for c in DECISION_CATEGORIES}
    rows = (
        MigrationDecision.objects.filter(status__in=UNRESOLVED_DECISION_STATUSES)
        .values("category")
        .annotate(n=Count("id"))
    )
    for row in rows:
        counts[row["category"]] = row["n"]
    return counts


def list_decisions(
    actor,
    *,
    category: str,
    status: str = "OPEN",
    reason_code: str | None = None,
    after_cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> KeysetPage:
    """One bounded keyset page of a queue. Bad filters are a ValueError."""
    _require_admin(actor)
    if category not in DECISION_CATEGORIES or status not in DECISION_STATUSES:
        raise ValueError("INVALID_ARGUMENT: decision filter is invalid")
    limit = max(1, min(int(limit), MAX_LIMIT))
    qs = MigrationDecision.objects.filter(category=category, status=status).order_by("id")
    if reason_code:
        qs = qs.filter(reason_code=reason_code)
    parts = decode_cursor_parts(after_cursor, ("int",))
    if parts is not None:
        qs = qs.filter(id__gt=parts[0])
    rows = list(qs[: limit + 1])
    items = rows[:limit]
    more = len(rows) > limit
    return KeysetPage(items=items, next_cursor=encode_cursor((items[-1].id,)) if more and items else None, has_more=more)


def offered_actions(decision: MigrationDecision) -> list[str]:
    """What the form may offer: the rule's actions, minus MATCH_EXISTING when the
    evidence names no exact-phone candidate to match."""
    actions = list(allowed_actions(decision.category, decision.reason_code))
    candidates = (decision.evidence or {}).get("candidates") or []
    if decision.category == "IDENTITY_REVIEW" and not candidates:
        actions = [a for a in actions if a != "MATCH_EXISTING"]
    return actions


def _record(decision, locked, actor, *, to_status, action, payload, reason, revision_after):
    MigrationDecisionAudit.objects.create(
        decision=locked, from_status=decision["from"], to_status=to_status, action=action,
        payload=payload, reason=reason, resolved_by=actor, mapping_version=DECISION_MAPPING_VERSION,
        evidence_hash=locked.evidence_hash, revision_before=decision["revision"], revision_after=revision_after,
    )


def resolve_decision(
    *, actor, decision_id: str, revision: int, evidence_hash: str, action: str, payload: dict | None, reason: str
) -> MigrationDecision:
    _require_admin(actor)
    with transaction.atomic():
        locked = MigrationDecision.objects.select_for_update().filter(decision_id=decision_id).first()
        if locked is None:
            raise ValueError("NOT_FOUND: decision was not found")
        if locked.status == "RESOLVED":
            raise ValueError("CONCURRENT_MODIFICATION: decision is already resolved; reopen it before deciding again")
        if revision != locked.revision or evidence_hash != locked.evidence_hash:
            raise ValueError(
                "CONCURRENT_MODIFICATION: decision is stale: revision or evidence changed since it was reviewed"
            )
        try:
            normalized = validate_resolution(
                {"category": locked.category, "reasonCode": locked.reason_code, "evidence": locked.evidence},
                {"action": action, "payload": payload or {}, "reason": reason},
                _lookup,
            )
        except DecisionValidationError as error:
            raise ValueError(f"INVALID_ARGUMENT: {error.message} ({error.code})") from error
        before = {"from": locked.status, "revision": locked.revision}
        to_status = next_status_for(action)
        locked.status = to_status
        locked.revision += 1
        locked.resolution = {
            "action": action,
            "payload": normalized,
            "reason": reason.strip(),
            "resolvedBy": str(actor.pk),
            "resolvedAt": round(time.time() * 1000),
            "mappingVersion": DECISION_MAPPING_VERSION,
        }
        locked.save()
        _record(before, locked, actor, to_status=to_status, action=action, payload=normalized,
                reason=reason.strip(), revision_after=locked.revision)
        return locked


def reopen_decision(*, actor, decision_id: str, revision: int, reason: str) -> MigrationDecision:
    """Explicit, audited reopen of a RESOLVED decision; the next resolution needs
    the new revision."""
    _require_admin(actor)
    if len((reason or "").strip()) < 3:
        raise ValueError("INVALID_ARGUMENT: a reason is required")
    with transaction.atomic():
        locked = MigrationDecision.objects.select_for_update().filter(decision_id=decision_id).first()
        if locked is None:
            raise ValueError("NOT_FOUND: decision was not found")
        if locked.status != "RESOLVED" or locked.revision != revision:
            raise ValueError("CONCURRENT_MODIFICATION: only the current RESOLVED revision can be reopened")
        before = {"from": "RESOLVED", "revision": locked.revision}
        locked.status = "OPEN"
        locked.revision += 1
        locked.resolution = None
        locked.save()
        _record(before, locked, actor, to_status="OPEN", action="REOPEN", payload={},
                reason=reason.strip(), revision_after=locked.revision)
        return locked


__all__ = ["summary", "list_decisions", "offered_actions", "resolve_decision", "reopen_decision"]
