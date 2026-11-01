"""Canonical machine-code vocabularies, ported from $PROTO/src/domain/enums.ts.

Transcribed in source order. Order is part of the contract — these tuples feed
UI option lists, so sorting them changes what a user sees. Values are stable
machine codes; Thai labels live in templates, so renaming a label never
rewrites stored data.

The import/conflict/mapping vocabularies (FIELD_CHANGE_KINDS,
IMPORT_BATCH_STATUSES, CONFLICT_*, EXPORT_FORMATS) are deliberately absent:
they belong to the import engine, which is Plan 2. SOURCE_KINDS is here
because the Global Unassigned intake needs it.
"""

from __future__ import annotations

ROLES: tuple[str, ...] = ("ADMIN", "TEAM_LEAD", "TELESALE")

#: Per-team configuration, never a hard-coded team name.
OWNERSHIP_POLICIES: tuple[str, ...] = ("EXCLUSIVE", "LATEST_ORDER", "LATEST_ORDER_WITH_LOCK")

#: User-editable. Never overwritten by a computed status.
LEAD_LIFECYCLE_STATUSES: tuple[str, ...] = (
    "NEW",
    "FOLLOWING",
    "WAITING_CUSTOMER",
    "WAITING_FOLLOWUP",
    "STOPPED",
)

#: Derived at read time. Coexists with the lifecycle status rather than replacing it.
COMPUTED_LEAD_STATUSES: tuple[str, ...] = ("APPOINTMENT_TODAY", "APPOINTMENT_FUTURE", "OVERDUE")

STOP_REASONS: tuple[str, ...] = ("CONTACT_LIMIT_REACHED", "MANUAL_STOP", "OTHER")

#: Routing is separate from lifecycle: a STOPPED lead keeps its owner and its
#: stopped state and is never treated as unassigned.
ROUTING_STATES: tuple[str, ...] = ("ASSIGNED", "TEAM_UNASSIGNED")

#: D019 — global-unassigned work is a pre-Lead intake, never a fake/null-Team Lead.
INTAKE_STATUSES: tuple[str, ...] = ("GLOBAL_UNASSIGNED", "CONVERTED", "ARCHIVED")

ORDER_STATUSES: tuple[str, ...] = (
    "PENDING_CONFIRMATION",
    "WAITING_SHIPMENT",
    "SHIPPED",
    "DELIVERED",
    "CANCELLED",
    "COD_RETURNED",
)

PAYMENT_STATUSES: tuple[str, ...] = (
    "UNPAID",
    "PENDING_VERIFICATION",
    "PAID",
    "COD_PENDING",
    "COD_COLLECTED",
    "REFUNDED",
)

SHIPPING_STATUSES: tuple[str, ...] = (
    "NOT_SHIPPED",
    "LABEL_CREATED",
    "PICKED_UP",
    "IN_TRANSIT",
    "DELIVERED",
    "FAILED",
    "RETURNING",
    "RETURNED",
)

CONTACT_ATTEMPT_RESULTS: tuple[str, ...] = (
    "NO_ANSWER",
    "BUSY",
    "CALLBACK_REQUESTED",
    "WRONG_NUMBER",
    "REFUSED",
    "ORDERED",
    "OTHER",
)

APPOINTMENT_STATUSES: tuple[str, ...] = ("SCHEDULED", "DONE", "MISSED", "CANCELLED")

CYCLE_END_REASONS: tuple[str, ...] = (
    "NEW_ORDER",
    "CUSTOMER_ORDERED",
    "MANUAL_CLOSE",
    "STOPPED",
    "TEAM_CHANGED",
)

IDENTIFIER_TYPES: tuple[str, ...] = ("PHONE", "CHANNEL_ID", "LEGACY_ID")

#: Identity resolution outcome. An ambiguous case is never auto-merged.
IDENTITY_MATCH_KINDS: tuple[str, ...] = ("AUTO_MATCH", "HUMAN_REVIEW", "NEW_CUSTOMER")

SOURCE_KINDS: tuple[str, ...] = (
    "CRM_CUSTOMERS",
    "LITE_DETAIL_ORDER",
    "INOUT_MANAGE_MAIN",
    "TELE_SALES_REPORT",
    "CUTOVER_ORDER_SHEET",
    "HX63_HISTORICAL",
    "PENDING_SOURCE_SAMPLE",
)

AUDIT_ACTIONS: tuple[str, ...] = (
    "FIELD_UPDATE",
    "OWNERSHIP_CHANGE",
    "IDENTITY_MERGE",
    "STATUS_CHANGE",
    "EXPORT",
    "CUSTOMER_HISTORY_VIEW",
    "INTAKE_CONVERTED",
    "NOTE_ADDED",
    "CONTACT_ATTEMPT_ADDED",
    "APPOINTMENT_SET",
)

#: A cancelled or COD-returned order never counts toward sales or missed-channel.
_TERMINAL_NEGATIVE_ORDER_STATUSES = frozenset({"CANCELLED", "COD_RETURNED"})


def _require_order_status(status: str) -> str:
    """TypeScript rejected an unknown OrderStatus at compile time; Python has to
    do it here. Returning a default instead would let the migration's
    unmapped status (1 value affecting 120 Orders, per
    07_MIGRATION_AND_DATA_REQUIREMENTS.md) silently count as a valid sale,
    which is the outcome that document forbids.
    """
    if status not in ORDER_STATUSES:
        raise ValueError(f"UNKNOWN_ORDER_STATUS:{status!r}")
    return status


def is_order_terminal_negative(status: str) -> bool:
    return _require_order_status(status) in _TERMINAL_NEGATIVE_ORDER_STATUSES


def is_order_valid_for_sales(status: str) -> bool:
    return not is_order_terminal_negative(status)
