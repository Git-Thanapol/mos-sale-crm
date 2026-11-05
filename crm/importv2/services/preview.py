"""Preview: classify every staged order against current data.

Nothing here writes business data: it reads the file, resolves who and which
team each row names, decides identity, evaluates every order with the ported
rules (crm.domain.row_evaluation) and stores the outcome as inert plan rows and
conflicts. "No business write before Confirm" is structural: this module has no
code path that touches customers, orders, leads or intake.

Rulings (docs/DECISIONS.md, "CRM V2 import"):
 - the file is line-level (one row per SKU line); lines of one order id are
   grouped into one order and their line amounts summed into its total;
 - identity candidates come from the phone index (exact phone or secondary
   phone), so the "phone differs by 1-2 digits" path of the prototype is not
   reachable here;
 - an order whose rows have any mapping error is INVALID as a whole and blocks
   Confirm until the file is fixed and re-uploaded.
"""

from __future__ import annotations

from collections import OrderedDict

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.core.permissions import can_import_v2
from crm.customers.models import Customer
from crm.domain.conflict_scope import next_resolution_preview_hash
from crm.domain.identity import classify_identity
from crm.domain.import_confirm import compute_preview_hash
from crm.domain.import_errors import BatchBusinessDateError
from crm.domain.mapping import apply_mapping
from crm.domain.phone import normalize_phone
from crm.domain.row_evaluation import aggregate_preview, evaluate_row
from crm.domain.tele_date import confirm_batch_business_date
from crm.importv2.models import ImportBatch, ImportConflict, ImportRowPlan
from crm.importv2.services import adapters
from crm.importv2.services.state import transition
from crm.importv2.services.upload import get_profile, read_table
from crm.leads.models import Lead
from crm.orders.models import Order

PREVIEWABLE = ("PREVIEW", "AWAITING_RESOLUTION")

_ORDER_FIELDS = {
    "channel": "channel",
    "carrier": "carrier",
    "trackingNumber": "trackingNumber",
    "url": "url",
    "phone": "phone",
    "addressLine": "address.line",
    "subdistrict": "address.subdistrict",
    "district": "address.district",
    "province": "address.province",
    "postalCode": "address.postalCode",
}


def _first(rows: list[dict], key: str):
    return next((r["values"].get(key) for r in rows if r["values"].get(key) not in (None, "")), None)


def _group_rows(profile: dict, table: dict) -> list[dict]:
    """Group mapped rows by order id. Row numbers are the sheet's (header = 1)."""
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for offset, row in enumerate(table["rows"]):
        row_index = offset + 2
        mapped = apply_mapping(profile, row)
        key = mapped["values"].get("orderId") or f"__row{row_index}"
        groups.setdefault(key, []).append(
            {"row_index": row_index, "values": mapped["values"], "errors": mapped["errors"]}
        )
    return [{"order_id": "" if k.startswith("__row") else k, "rows": v} for k, v in groups.items()]


def _lines(rows: list[dict]) -> list[dict]:
    return adapters.merge_lines(
        [
            {
                "sku": r["values"].get("sku") or "",
                "productName": r["values"].get("productName") or "",
                "quantity": r["values"].get("quantity") or 1,
                "amount": r["values"].get("amount"),
            }
            for r in rows
        ]
    )


def _resolve_customer(values: dict) -> tuple[dict, Customer | None]:
    phones = [p for p in (values.get("phone"), values.get("phoneAlt")) if p]
    spellings = {p for p in phones} | {normalize_phone(p)["national"] for p in phones}
    spellings.discard("")
    query = Q()
    for spelling in spellings:
        query |= Q(phone1=spelling) | Q(phone2=spelling) | Q(phone_key=spelling)
    candidates = list(Customer.objects.filter(query).order_by("id")[:5]) if spellings else []
    identity = classify_identity(
        name=values.get("customerName") or "",
        phone=values.get("phone") or "",
        candidates=[
            {"customerId": str(c.pk), "name": c.customer_name, "phones": [c.phone1, c.phone2]}
            for c in candidates
        ],
    )
    matched = next((c for c in candidates if str(c.pk) == identity["candidateCustomerId"]), None)
    return identity, (matched if identity["kind"] == "AUTO_MATCH" else None), matched


def _lead_for(customer: Customer | None, team) -> Lead | None:
    if customer is None:
        return None
    leads = list(Lead.objects.select_related("team", "caretaker").filter(customer=customer))
    if team is not None:
        return next((l for l in leads if l.team_id == team.pk), None)
    return leads[0] if len(leads) == 1 else None


def recompute_preview_hash(batch: ImportBatch) -> str:
    """The digest the operator approves: file, profile, batch date, every staged
    order's merge fields, then each resolved conflict in resolution order."""
    plans = batch.plans.exclude(classification="INVALID").order_by("row_index")
    digest = compute_preview_hash(
        file_sha256=batch.file_sha256,
        mapping_profile_id=batch.mapping_profile_id,
        mapping_profile_version=batch.mapping_profile_version,
        batch_business_date=batch.batch_business_date.isoformat() if batch.batch_business_date else None,
        rows=[
            {"rowIndex": p.row_index, "orderId": p.order_id, "changes": p.facts.get("fields", {})}
            for p in plans
        ],
        decisions=None,
    )
    for conflict in batch.conflicts.exclude(resolution="").order_by("resolved_at", "id"):
        digest = next_resolution_preview_hash(
            digest,
            {"conflictId": conflict.conflict_id, "version": conflict.version, "resolution": conflict.resolution},
        )
    return digest


def _set_invalid_rows_issue(batch: ImportBatch, invalid_orders: int) -> None:
    issues = [i for i in batch.validation.get("issues", []) if i.get("code") != "INVALID_ROWS"]
    if invalid_orders:
        issues.append(
            {
                "severity": "BLOCK",
                "code": "INVALID_ROWS",
                "message": f"{invalid_orders} order(s) have rows that failed mapping. Fix the file and "
                "upload it again; nothing is imported from a partly invalid order.",
            }
        )
    batch.validation = {"ok": not any(i["severity"] == "BLOCK" for i in issues), "issues": issues}


def build_preview(*, actor, batch: ImportBatch) -> dict:
    """(Re)build the preview. Resolutions made on an earlier preview are reset."""
    if not can_import_v2(actor):
        raise PermissionDenied("เฉพาะ EDITOR เท่านั้นที่สร้าง Preview ได้")
    if batch.status not in PREVIEWABLE:
        raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} cannot be previewed")

    profile = get_profile(batch.mapping_profile_id, batch.mapping_profile_version)
    blocked_before = [i for i in batch.validation.get("issues", []) if i["severity"] == "BLOCK" and i["code"] != "INVALID_ROWS"]

    with transaction.atomic():
        batch.plans.all().delete()
        batch.conflicts.all().delete()
        if blocked_before or profile is None:
            batch.preview_aggregate = aggregate_preview([], 0)
            batch.preview_hash = ""
            batch.save(update_fields=["preview_aggregate", "preview_hash", "updated_at"])
            return batch.preview_aggregate

        table = read_table(bytes(batch.raw_bytes), batch.file_name)
        directory = adapters.Directory()
        evaluations: list[dict] = []
        new_customer_keys: set[str] = set()
        invalid_orders = 0

        for group in _group_rows(profile, table):
            rows = group["rows"]
            first_index = rows[0]["row_index"]
            errors = [
                {"rowIndex": r["row_index"], **e} for r in rows for e in r["errors"]
            ]
            if errors or not group["order_id"]:
                invalid_orders += 1
                ImportRowPlan.objects.create(
                    batch=batch, row_index=first_index, order_id=group["order_id"],
                    classification="INVALID", errors=errors, facts={},
                )
                evaluations.append({"classification": "INVALID", "blocking": False})
                continue

            values = {k: _first(rows, k) for k in rows[0]["values"]}
            lines = _lines(rows)
            amounts = [l["amount"] for l in lines if l["amount"] is not None]
            closer = directory.user_for(values.get("sellerName"))
            team = directory.team_for(values.get("teamName"))
            fields = {order_key: values.get(src) for src, order_key in _ORDER_FIELDS.items()}
            fields.update(
                {
                    "orderedAt": values.get("orderedAt"),
                    "closerUserId": str(closer.pk) if closer else None,
                    "closerTeamId": team.code if team else None,
                    "total": sum(amounts) if amounts else None,
                    "items": lines,
                }
            )
            identity, customer, candidate = _resolve_customer(values)
            existing_order = (
                Order.objects.filter(customer=customer, order_no=group["order_id"]).first()
                if customer
                else None
            )
            lead = _lead_for(customer, team)
            facts = {
                "rowIndex": first_index,
                "orderId": group["order_id"],
                "orderedAt": values.get("orderedAt"),
                "closerUserId": fields["closerUserId"],
                "closerTeamId": fields["closerTeamId"],
                "customerName": values.get("customerName") or "",
                "phone": values.get("phone") or "",
                "fields": fields,
            }
            evaluation = evaluate_row(
                facts,
                {
                    "identity": identity,
                    "existingOrder": adapters.order_to_prototype(existing_order) if existing_order else None,
                    "existingLead": adapters.lead_snapshot(lead) if lead else None,
                    "activeUserIds": sorted(directory.active_user_ids),
                },
            )
            facts["customer"] = {
                "name": values.get("customerName") or "",
                "phone1": values.get("phone") or "",
                "phone2": values.get("phoneAlt") or "",
                "address": values.get("addressLine") or "",
                "subdistrict": values.get("subdistrict") or "",
                "district": values.get("district") or "",
                "province": values.get("province") or "",
                "postalCode": values.get("postalCode") or "",
                "caretakerName": values.get("caretakerName") or "",
                "sellerName": values.get("sellerName") or "",
                "teamName": values.get("teamName") or "",
            }
            facts["identity"] = {**identity, "candidateCustomerId": identity["candidateCustomerId"]}
            if identity["kind"] == "NEW_CUSTOMER":
                new_customer_keys.add(
                    adapters.new_customer_phone_key(values.get("phone") or "", values.get("phoneAlt") or "", group["order_id"])
                )
            ImportRowPlan.objects.create(
                batch=batch, row_index=first_index, order_id=group["order_id"], facts=facts,
                classification=evaluation["classification"], evaluation=evaluation,
                identity_kind=identity["kind"], customer=customer or candidate if identity["kind"] != "HUMAN_REVIEW" else None,
            )
            for number, conflict in enumerate(evaluation["conflicts"], start=1):
                ImportConflict.objects.create(
                    batch=batch,
                    conflict_id=f"{first_index}:{conflict['kind']}:{number}",
                    row_index=first_index,
                    kind=conflict["kind"],
                    severity=conflict["severity"],
                    team=team or (lead.team if lead else None),
                    affected_team_codes=[conflict["teamId"]] if conflict["teamId"] else [],
                    summary=conflict["summary"],
                    source_row_ref=f"row:{first_index}",
                    options=conflict["options"],
                )
            evaluations.append(evaluation)

        aggregate = aggregate_preview(evaluations, len(new_customer_keys))
        batch.row_count = len(table["rows"])
        batch.preview_aggregate = aggregate
        _set_invalid_rows_issue(batch, invalid_orders)
        batch.preview_hash = recompute_preview_hash(batch)
        batch.save()
        if batch.validation["ok"]:
            transition(batch, "AWAITING_RESOLUTION", actor=actor, reason="preview built")
        return aggregate


def set_batch_business_date(*, actor, batch: ImportBatch, text: str) -> None:
    """Operator-confirm the batch business date (D3) and re-derive the digest.

    Raises BatchBusinessDateError for anything that is not a real calendar day.
    """
    if not can_import_v2(actor):
        raise PermissionDenied("เฉพาะ EDITOR เท่านั้นที่ยืนยันวันที่ของ Batch ได้")
    if batch.status not in PREVIEWABLE:
        raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} cannot change its date")
    confirmed = confirm_batch_business_date(
        batch.file_name,
        actor=actor.email or str(actor.pk),
        at=round(timezone.now().timestamp() * 1000),
        operator_day_key=text,
    )
    from datetime import date

    batch.batch_business_date = date.fromisoformat(confirmed["day_key"])
    batch.batch_date_meta = confirmed
    batch.preview_hash = recompute_preview_hash(batch)
    batch.save(update_fields=["batch_business_date", "batch_date_meta", "preview_hash", "updated_at"])


__all__ = ["build_preview", "set_batch_business_date", "recompute_preview_hash", "BatchBusinessDateError"]
