"""Migration Decision Center rules (port of the pure parts of
src/migration/decisions.ts).

Every migration input that cannot be decided by an exact, documented rule
becomes a decision item. Nothing here guesses: a decision changes migration
behaviour only after an Admin records an explicit resolution, and every
resolution is bound to the exact evidence the Admin saw (`evidenceHash`) and the
item `revision`. Stale decisions fail.

Categories: STAFF_ALIAS (historical caretaker text without an approved alias),
IDENTITY_REVIEW (phone groups whose names disagree, or orders without phone),
UNKNOWN_STATUS (source status outside the known vocabulary) and NULL_DATE_ORDER
(orders without a usable date: orderedAt is never invented).

Decision and evidence dicts use the prototype's camelCase keys (evidence is
hashed as stored).
"""

from __future__ import annotations

import math

from crm.domain.enums import ORDER_STATUSES, PAYMENT_STATUSES, SHIPPING_STATUSES
from crm.domain.import_hashing import sha256_hex, stable_stringify

DECISION_CATEGORIES = ("STAFF_ALIAS", "IDENTITY_REVIEW", "UNKNOWN_STATUS", "NULL_DATE_ORDER")
DECISION_STATUSES = ("OPEN", "DEFERRED", "RESOLVED")
#: DEFERRED is still unresolved: it never counts toward readiness.
UNRESOLVED_DECISION_STATUSES = ("OPEN", "DEFERRED")

DECISION_ACTIONS = {
    "STAFF_ALIAS": ("MAP_TO_USER", "HISTORICAL_ONLY", "DEFER"),
    "IDENTITY_REVIEW": ("MATCH_EXISTING", "CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "DEFER"),
    "UNKNOWN_STATUS": ("MAP_TO_STATUS", "ARCHIVE_ONLY", "DEFER"),
    "NULL_DATE_ORDER": ("SET_AUTHORITATIVE_DATE", "KEEP_BLOCKED", "ARCHIVE_ONLY", "DEFER"),
}
IDENTITY_REVIEW_REASONS = ("NAME_CONFLICT", "BLANK_NAME", "NO_PHONE")

#: Version of the mapping table a resolution was recorded under (prototype DECISION_MAPPING_VERSION).
DECISION_MAPPING_VERSION = "phase8b-decision-v1"

#: Reason filters offered per category (a UI menu; the server validates).
DECISION_REASON_FILTERS = {
    "STAFF_ALIAS": ("NO_EXACT_CANDIDATE", "EXACT_CANDIDATE_AVAILABLE"),
    "IDENTITY_REVIEW": ("NAME_CONFLICT", "BLANK_NAME", "NO_PHONE"),
    "UNKNOWN_STATUS": ("BLANK_VALUE", "UNRECOGNISED_VALUE"),
    "NULL_DATE_ORDER": ("SOURCE_DATE_MISSING",),
}

#: Actions forbidden for a specific reason even though the category allows them.
#: Orders without any phone share no identity at all: one Customer for the whole
#: group would merge unrelated people, and there is no candidate to match.
REASON_FORBIDDEN = {"IDENTITY_REVIEW:NO_PHONE": ("CREATE_NEW_CUSTOMER", "MATCH_EXISTING")}

_NO_PAYLOAD = ("DEFER", "HISTORICAL_ONLY", "CREATE_NEW_CUSTOMER", "KEEP_SEPARATE", "KEEP_BLOCKED", "ARCHIVE_ONLY")


class DecisionValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def decision_id_for(category: str, source_snapshot_id: str, subject_key: str) -> str:
    return f"dec_{category.lower()}_{sha256_hex(f'{source_snapshot_id}|{category}|{subject_key}')[:32]}"


def evidence_hash_of(category: str, subject_key: str, evidence: dict) -> str:
    return sha256_hex(stable_stringify({"category": category, "subjectKey": subject_key, "evidence": evidence}))


def allowed_actions(category: str, reason_code: str) -> tuple[str, ...]:
    forbidden = REASON_FORBIDDEN.get(f"{category}:{reason_code}", ())
    return tuple(a for a in DECISION_ACTIONS[category] if a not in forbidden)


def next_status_for(action: str) -> str:
    return "DEFERRED" if action == "DEFER" else "RESOLVED"


def mask_phone(national: str) -> str:
    """All but the last four characters replaced by `*`, even for an Admin."""
    if not national:
        return ""
    return "*" * len(national) if len(national) <= 4 else "*" * (len(national) - 4) + national[-4:]


def _is_whole(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value == int(value)


def validate_resolution(decision: dict, request: dict, lookup_user) -> dict:
    """Validate an explicit human resolution against the decision's evidence and
    return the normalized payload to store.

    `decision` = {category, reasonCode, evidence}; `request` = {action, payload,
    reason}; `lookup_user(user_id)` returns `{role, active}` or None (the caller
    loads users from the server directory, never the client).
    """
    action = request["action"]
    if action not in allowed_actions(decision["category"], decision["reasonCode"]):
        raise DecisionValidationError(
            "ACTION_NOT_ALLOWED",
            f"{action} is not allowed for {decision['category']}/{decision['reasonCode']}",
        )
    if len((request.get("reason") or "").strip()) < 3:
        raise DecisionValidationError("REASON_REQUIRED", "A reason of at least 3 characters is required.")
    payload = request.get("payload") or {}
    evidence = decision["evidence"]

    if action in _NO_PAYLOAD:
        if payload:
            raise DecisionValidationError("PAYLOAD_NOT_ALLOWED", f"{action} takes no payload.")
        return {}

    if action == "MAP_TO_USER":
        user_id = payload.get("userId")
        if not isinstance(user_id, str) or not user_id:
            raise DecisionValidationError("USER_REQUIRED", "MAP_TO_USER requires userId.")
        user = lookup_user(user_id)
        if not user:
            raise DecisionValidationError("USER_NOT_FOUND", "Target user does not exist.")
        if user["role"] == "ADMIN":
            raise DecisionValidationError("USER_NOT_CARETAKER", "An Admin account cannot be a historical caretaker.")
        # The decision records the user's state when approved. A disabled user is
        # mapped for history only and can never become an active caretaker.
        return {"userId": user_id, "userActiveAtApproval": user["active"]}

    if action == "MATCH_EXISTING":
        customer_id = payload.get("customerId")
        if evidence.get("kind") != "IDENTITY_REVIEW":
            raise DecisionValidationError("INVALID_EVIDENCE", "Identity evidence required.")
        if not isinstance(customer_id, str) or not any(
            c["customerId"] == customer_id for c in evidence.get("candidates", [])
        ):
            raise DecisionValidationError(
                "CANDIDATE_REQUIRED", "MATCH_EXISTING must name one of the exact-phone candidates."
            )
        return {"customerId": customer_id}

    if action == "MAP_TO_STATUS":
        keys = {"orderStatus": ORDER_STATUSES, "paymentStatus": PAYMENT_STATUSES, "shippingStatus": SHIPPING_STATUSES}
        if set(payload) != set(keys) or any(payload[k] not in values for k, values in keys.items()):
            raise DecisionValidationError(
                "STATUS_REQUIRED",
                "MAP_TO_STATUS requires exact canonical orderStatus, paymentStatus and shippingStatus.",
            )
        return {k: payload[k] for k in keys}

    if action == "SET_AUTHORITATIVE_DATE":
        ordered_at = payload.get("orderedAt")
        ref = payload.get("evidenceRef")
        ref_ok = isinstance(ref, str) and 3 <= len(ref.strip()) <= 300
        if (
            set(payload) != {"orderedAt", "evidenceRef"}
            or not _is_whole(ordered_at)
            or ordered_at < 0
            or not ref_ok
        ):
            raise DecisionValidationError(
                "AUTHORITATIVE_DATE_REQUIRED",
                "A corrected date needs orderedAt and an independent evidence reference.",
            )
        return {"orderedAt": int(ordered_at), "evidenceRef": ref.strip()}

    raise DecisionValidationError("ACTION_NOT_ALLOWED", f"{action} is not supported.")
