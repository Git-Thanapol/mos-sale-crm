"""Confirm gate (port of src/import/confirm.ts).

Confirm is the only door between "we looked at it" and "we changed business
data". Everything it checks is a real precondition rather than UI politeness:

 - the caller may import at all;
 - the mapping profile used is the active, pinned version;
 - validation and every blocking conflict are resolved;
 - the preview the operator saw is still the preview we would execute (hash);
 - a source without its own date has an operator-confirmed batch business date.

Any change to staged input or decisions invalidates the hash and forces a
re-preview instead of committing something nobody approved.

Roles here are the PROTOTYPE's (ADMIN, TEAM_LEAD, TELESALE). The service layer
maps this repo's roles onto them (EDITOR is the prototype's ADMIN).
"""

from __future__ import annotations

from crm.domain.import_errors import BatchBusinessDateError, ConfirmError
from crm.domain.import_hashing import sha256_hex, stable_stringify
from crm.domain.tele_date import assert_confirmed_batch_date

CONFIRMABLE_STATES = frozenset({"AWAITING_RESOLUTION", "PAUSED_QUOTA", "FAILED_RETRYABLE"})

#: Master Spec section 17 gives upload, mapping edits and (therefore) commit to
#: Admin. A Team Lead resolves conflicts inside their own team; they do not
#: commit a batch.
IMPORT_CONFIRM_ROLES = frozenset({"ADMIN"})


def source_requires_batch_date(source_kind: str) -> bool:
    """Sources that cannot supply their own date (the Tele sales report)."""
    return source_kind == "TELE_SALES_REPORT"


def evaluate_confirm(
    *,
    batch: dict,
    actor: dict,
    profile: dict | None,
    validation: dict,
    conflicts: list,
    staged: dict | None,
    requires_batch_business_date: bool,
    batch_business_date: dict | None,
    conflict_summary: dict | None = None,
) -> dict:
    """Return `{"ok", "blockers": [{"code", "message"}], "open_blocking_conflicts"}`.

    Dict shapes mirror the prototype's ConfirmInput with snake_case keys:
    batch{status, mapping_profile_id, mapping_profile_version, file_sha256,
    preview_hash}, actor{role}, profile{profile_id, version, status},
    validation{ok, issues[{severity}]}, conflicts[{severity, status}],
    staged{file_sha256, mapping_profile_id, mapping_profile_version, preview_hash}.
    """
    blockers: list[dict] = []

    if actor["role"] not in IMPORT_CONFIRM_ROLES:
        blockers.append(
            {
                "code": "NOT_AUTHORIZED",
                "message": f"{actor['role']} may not confirm an import; only Admin may.",
            }
        )
    if batch["status"] not in CONFIRMABLE_STATES:
        blockers.append(
            {"code": "ILLEGAL_STATE", "message": f"a batch in {batch['status']} cannot be confirmed"}
        )
    if not profile:
        blockers.append(
            {"code": "PROFILE_MISSING", "message": "the mapping profile used by this batch no longer exists"}
        )
    else:
        if profile["status"] != "ACTIVE":
            blockers.append(
                {
                    "code": "PROFILE_NOT_ACTIVE",
                    "message": f"mapping profile {profile['profile_id']} is {profile['status']}; "
                    "only an ACTIVE version may commit",
                }
            )
        if (
            profile["profile_id"] != batch["mapping_profile_id"]
            or profile["version"] != batch["mapping_profile_version"]
        ):
            blockers.append(
                {
                    "code": "PROFILE_VERSION_MISMATCH",
                    "message": f"batch is pinned to {batch['mapping_profile_id']}@{batch['mapping_profile_version']} "
                    f"but {profile['profile_id']}@{profile['version']} was supplied",
                }
            )

    block_issues = [i for i in (validation.get("issues") or []) if i.get("severity") == "BLOCK"]
    if not validation.get("ok") or block_issues:
        blockers.append(
            {
                "code": "VALIDATION_BLOCKED",
                "message": f"{len(block_issues)} validation error(s) must be fixed first"
                if block_issues
                else "validation did not pass",
            }
        )

    if conflict_summary is not None and conflict_summary.get("open_blocking_conflicts") is not None:
        open_blocking = conflict_summary["open_blocking_conflicts"]
    else:
        open_blocking = len(
            [c for c in conflicts if c["severity"] == "BLOCKING" and c["status"] == "OPEN"]
        )
    if open_blocking > 0:
        blockers.append(
            {
                "code": "BLOCKING_CONFLICTS_OPEN",
                "message": f"{open_blocking} blocking conflict(s) are still open",
            }
        )

    if not staged:
        blockers.append({"code": "PREVIEW_MISSING", "message": "no preview exists for this batch"})
    else:
        if staged["file_sha256"] != batch["file_sha256"]:
            blockers.append(
                {
                    "code": "STAGED_FILE_CHANGED",
                    "message": "the staged file differs from the batch file; re-upload and re-preview",
                }
            )
        if not batch.get("preview_hash") or staged["preview_hash"] != batch["preview_hash"]:
            blockers.append(
                {
                    "code": "PREVIEW_HASH_MISMATCH",
                    "message": "the decisions changed after the preview; re-preview before committing",
                }
            )
        if (
            staged["mapping_profile_id"] != batch["mapping_profile_id"]
            or staged["mapping_profile_version"] != batch["mapping_profile_version"]
        ):
            blockers.append(
                {
                    "code": "PROFILE_VERSION_MISMATCH",
                    "message": "the staged mapping profile is not the version pinned on the batch",
                }
            )

    if requires_batch_business_date:
        try:
            assert_confirmed_batch_date(batch_business_date)
        except BatchBusinessDateError as error:
            blockers.append({"code": "BATCH_DATE_REQUIRED", "message": str(error)})

    return {"ok": not blockers, "blockers": blockers, "open_blocking_conflicts": open_blocking}


def assert_confirmable(**kwargs) -> dict:
    decision = evaluate_confirm(**kwargs)
    if not decision["ok"]:
        raise ConfirmError(decision["blockers"])
    return decision


def compute_preview_hash(
    *,
    file_sha256: str,
    mapping_profile_id: str,
    mapping_profile_version: int,
    batch_business_date: str | None,
    rows: list[dict],
    decisions: list[dict] | None = None,
) -> str:
    """Digest of everything Confirm is approving, recomputed on every resolution
    so a decision made after the preview cannot ride along unnoticed.

    `rows` items carry `rowIndex`, `orderId`, `changes`; `decisions` items carry
    `conflictId`, `choice`. These are the prototype's own key names because they
    are part of the hashed document.
    """
    return sha256_hex(
        stable_stringify(
            {
                "fileSha256": file_sha256,
                "mappingProfileId": mapping_profile_id,
                "mappingProfileVersion": mapping_profile_version,
                "batchBusinessDate": batch_business_date,
                "rows": [
                    {"rowIndex": r["rowIndex"], "orderId": r["orderId"], "changes": r["changes"]}
                    for r in rows
                ],
                "decisions": [
                    {"conflictId": d["conflictId"], "choice": d["choice"]} for d in (decisions or [])
                ],
            }
        )
    )
