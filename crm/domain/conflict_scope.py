"""Who may resolve an import conflict, and the digests that guard resolution
(port of the pure parts of src/import/conflict-resolution.ts).

The conflict is a mapping with the prototype's own field names (conflictId,
teamId, affectedTeamIds, ...) because it is hashed as such.
"""

from __future__ import annotations

from crm.domain.import_errors import ApplicationError
from crm.domain.import_hashing import hash_fields, sha256_hex, stable_stringify

CONFLICT_HASH_FIELDS = (
    "conflictId",
    "batchId",
    "rowIndex",
    "kind",
    "severity",
    "status",
    "teamId",
    "affectedTeamIds",
    "summary",
    "sourceRowRef",
    "options",
    "version",
    "resolution",
)


def conflict_version_hash(conflict: dict) -> str:
    return hash_fields(conflict, CONFLICT_HASH_FIELDS)


def next_resolution_preview_hash(previous_preview_hash: str | None, decision: dict) -> str:
    """Change the approval digest without reading an unbounded conflict
    collection. The prior digest is already bound to file, mapping, date, rows
    and earlier decisions; optimistic compare-and-set makes this append
    deterministic and race-safe. `decision` = {conflictId, version, resolution}.
    """
    return sha256_hex(
        stable_stringify({"previousPreviewHash": previous_preview_hash, "decision": decision})
    )


def assert_resolver_scope(actor: dict, conflict: dict) -> None:
    """`actor` = {role, team_ids}; roles are the prototype's."""
    role = actor["role"]
    if role == "ADMIN":
        return
    if role != "TEAM_LEAD":
        raise ApplicationError("FORBIDDEN", "Telesale users cannot resolve import conflicts.")
    if conflict["kind"] == "IDENTITY":
        raise ApplicationError("FORBIDDEN", "Global identity conflicts require Admin resolution.")
    affected = conflict.get("affectedTeamIds") or []
    if affected:
        scope = list(dict.fromkeys(affected))
    elif conflict.get("teamId"):
        scope = [conflict["teamId"]]
    else:
        scope = []
    if len(scope) != 1 or scope[0] not in actor["team_ids"]:
        raise ApplicationError("FORBIDDEN", "This conflict is outside the Team Lead scope.")
