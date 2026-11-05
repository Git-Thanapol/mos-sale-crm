"""Field-level Order upsert (port of src/domain/order-merge.ts).

A repeated order id never creates a second Order, and never blindly replaces the
document with the incoming row. Each field is classified and merged:

 - a blank incoming value is "no new information" and never erases data;
 - a stale source cannot overwrite a newer authoritative value;
 - status fields respect the transition policy, so a file cannot move state
   backwards.

Orders, patches and decisions are plain dicts keyed with the PROTOTYPE's names
(`orderStatus`, `address.line`, `fieldProvenance`, ...): they are the import
plan's wire format and are persisted and hashed as such. The persistence layer
owns the mapping onto this repo's columns.
"""

from __future__ import annotations

import json

from crm.domain.import_hashing import _collation_key
from crm.domain.order_status_transitions import (
    classify_order_status_transition,
    classify_payment_transition,
    classify_shipping_transition,
)

SCALAR_ORDER_FIELDS = (
    "orderedAt",
    "closerUserId",
    "closerTeamId",
    "caretakerAtOrderTime",
    "channel",
    "shop",
    "subtotal",
    "total",
    "trackingNumber",
    "carrier",
    "url",
    "recipientName",
    "phone",
)
STATUS_ORDER_FIELDS = ("orderStatus", "paymentStatus", "shippingStatus")
ADDRESS_FIELDS = ("line", "subdistrict", "district", "province", "postalCode")

_STATUS_CLASSIFIER = {
    "orderStatus": classify_order_status_transition,
    "paymentStatus": classify_payment_transition,
    "shippingStatus": classify_shipping_transition,
}


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _stable(value) -> str:
    if value is None or not isinstance(value, (dict, list, tuple)):
        if isinstance(value, float) and value == int(value):
            value = int(value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_stable(v) for v in value) + "]"
    items = sorted(value.items(), key=lambda kv: _collation_key(str(kv[0])))
    return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{_stable(v)}" for k, v in items) + "}"


def _equal(a, b) -> bool:
    return _stable(a) == _stable(b)


def _read_path(order: dict, field: str):
    if field.startswith("address."):
        return (order.get("address") or {}).get(field[len("address."):])
    return order.get(field)


def _verdict_kind(verdict: str, allow_reversal: bool) -> str:
    if verdict == "SAME":
        return "UNCHANGED"
    if verdict in ("FORWARD", "CANCELLATION"):
        return "SAFE_UPDATE"
    if verdict == "REVERSAL":
        return "SAFE_UPDATE" if allow_reversal else "EXPLICIT_REVERSAL_REQUIRED"
    return "CONFLICT"


def merge_order(existing: dict, incoming: dict, allow_reversal: bool = False) -> dict:
    """Classify and merge `incoming` (`{orderId, fields, source}`) into `existing`.

    Returns `{orderId, updates, decisions, blocking, hasBlocking}`.
    """
    decisions: list[dict] = []
    updates: dict = {}
    provenance_updates: dict = {}
    source = incoming["source"]
    existing_address = existing.get("address") or {}
    existing_provenance = existing.get("fieldProvenance") or {}

    def record(field, kind, before, after, reason):
        decisions.append({"field": field, "kind": kind, "before": before, "after": after, "reason": reason})
        if kind == "SAFE_UPDATE":
            if field.startswith("address."):
                key = field[len("address."):]
                merged = {**(updates.get("address") or {}), key: after}
                updates["address"] = {**existing_address, **merged}
            elif field == "items":
                updates["items"] = after
            else:
                updates[field] = after
            provenance_updates[field] = source

    for field, after in incoming["fields"].items():
        before = _read_path(existing, field)

        if field == "customerId":
            # Identity re-pointing is never an import side effect.
            if not _is_blank(after) and not _equal(before, after):
                record(
                    field, "CONFLICT", before, after,
                    "customerId changes require the identity merge flow, not an order upsert",
                )
            else:
                record(field, "UNCHANGED", before, after, "same or blank customer id")
            continue

        if _is_blank(after):
            record(field, "UNCHANGED", before, after, "blank incoming value carries no new information")
            continue
        if _equal(before, after):
            record(field, "UNCHANGED", before, after, "identical value")
            continue
        if _is_blank(before):
            record(field, "SAFE_UPDATE", before, after, "fills an empty field")
            continue

        if field == "items":
            record(
                field, "SAFE_UPDATE", before, after,
                "source-derived line items replaced as a unit; totals remain field-merged",
            )
            continue

        if field in STATUS_ORDER_FIELDS:
            # Stale check first: a status from an older source must never
            # overwrite a newer one, even when it looks like a forward move.
            status_incoming_at = source.get("sourceUpdatedAt")
            status_existing_at = (existing_provenance.get(field) or {}).get("sourceUpdatedAt")
            if (
                status_incoming_at is not None
                and status_existing_at is not None
                and status_incoming_at < status_existing_at
            ):
                record(field, "STALE_INPUT", before, after,
                       "incoming source timestamp is older than the stored field provenance")
                continue
            verdict = _STATUS_CLASSIFIER[field](before, after, allow_reversal)
            record(
                field, _verdict_kind(verdict, allow_reversal), before, after,
                f"status transition {before} -> {after} classified {verdict}",
            )
            continue

        existing_prov = existing_provenance.get(field)
        incoming_at = source.get("sourceUpdatedAt")
        existing_at = (existing_prov or {}).get("sourceUpdatedAt")

        if incoming_at is not None and existing_at is not None:
            if incoming_at < existing_at:
                record(field, "STALE_INPUT", before, after,
                       "incoming source timestamp is older than the stored field provenance")
                continue
            if incoming_at == existing_at:
                record(field, "CONFLICT", before, after, "same source version reports a different value")
                continue
            record(field, "SAFE_UPDATE", before, after, "newer source version")
            continue

        if (
            incoming_at is None
            and existing_at is not None
            and source["precedence"] <= ((existing_prov or {}).get("precedence") or 0)
        ):
            record(field, "STALE_INPUT", before, after,
                   "undated source with no precedence advantage cannot overwrite a dated value")
            continue

        if existing_prov and source["precedence"] < existing_prov["precedence"]:
            record(field, "CONFLICT", before, after, "conflicting value from a lower-precedence source")
            continue

        record(field, "SAFE_UPDATE", before, after, "no newer provenance stored; accepted")

    if provenance_updates:
        merged = {**existing_provenance}
        for field, stamp in provenance_updates.items():
            merged[field] = {
                "sourceId": stamp["sourceId"],
                "sourceUpdatedAt": stamp.get("sourceUpdatedAt"),
                "precedence": stamp["precedence"],
            }
        updates["fieldProvenance"] = merged

    blocking = [d for d in decisions if d["kind"] in ("EXPLICIT_REVERSAL_REQUIRED", "CONFLICT")]
    return {
        "orderId": incoming["orderId"],
        "updates": updates,
        "decisions": decisions,
        "blocking": blocking,
        "hasBlocking": len(blocking) > 0,
    }


def summarize_decisions(decisions: list[dict]) -> dict:
    """Aggregate field decisions into the counts shown in the preview."""
    summary = {
        "considered": len(decisions),
        "unchanged": 0,
        "safeUpdate": 0,
        "staleInput": 0,
        "conflict": 0,
        "reversalRequired": 0,
    }
    key = {
        "UNCHANGED": "unchanged",
        "SAFE_UPDATE": "safeUpdate",
        "STALE_INPUT": "staleInput",
        "CONFLICT": "conflict",
        "EXPLICIT_REVERSAL_REQUIRED": "reversalRequired",
    }
    for decision in decisions:
        if decision["kind"] in key:
            summary[key[decision["kind"]]] += 1
    return summary
