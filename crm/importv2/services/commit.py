"""Confirm and commit.

Confirm is the only door between "we looked at it" and "we changed business
data" (crm.domain.import_confirm.evaluate_confirm decides). Commit then writes
in chunks, each chunk one transaction, journalling every change so Rollback can
undo exactly this batch. Both are idempotent per operation key: replaying a key
returns the stored result and writes nothing.

Commit rulings (docs/DECISIONS.md, "CRM V2 import"):
 - imports never open follow-up cycles: a cycle opens when someone works a lead;
 - a customer whose identity was resolved NEW_CUSTOMER despite an exact phone
   match is created with a split phone_key (`<key>#<order id>`), because
   crm_customer.phone_key is still unique;
 - an order whose customer has no lead and whose row names no known team goes to
   Global Unassigned (D019/D024), never to a fake team.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.core.permissions import import_actor_role
from crm.customers.models import Customer
from crm.domain.access import derive_lead_access
from crm.domain.import_confirm import evaluate_confirm, source_requires_batch_date
from crm.domain.import_errors import ConfirmError
from crm.domain.order_merge import merge_order
from crm.importv2.models import ImportBatch, ImportConflict, ImportOperation, ImportRowPlan
from crm.importv2.services import adapters
from crm.importv2.services.journal import Journal, LeadTracker, snapshot
from crm.importv2.services.preview import recompute_preview_hash
from crm.importv2.services.state import transition
from crm.importv2.services.upload import get_profile
from crm.intake.services import upsert_intake
from crm.leads.models import Lead
from crm.leads.services import apply_order_to_lead, reactivate_lead, reassign_caretaker
from crm.leads.sync import _refresh_order_rollups
from crm.leads.recompute import recompute_lead_missed_channel
from crm.orders.models import Order
from crm.teams.models import Team

DEFAULT_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 200
IMPORT_SOURCE_ID = "import"


class StalePreviewError(Exception):
    code = "STALE_PREVIEW"


def _require_admin(actor) -> None:
    if import_actor_role(actor) != "ADMIN":
        raise PermissionDenied("เฉพาะ EDITOR เท่านั้นที่ยืนยันหรือ Commit การนำเข้าได้")


def _replay(batch: ImportBatch, operation_key: str) -> dict | None:
    existing = ImportOperation.objects.filter(batch=batch, operation_key=operation_key).first()
    return existing.result if existing else None


def _remember(batch: ImportBatch, operation_key: str, action: str, result: dict) -> dict:
    ImportOperation.objects.create(batch=batch, operation_key=operation_key, action=action, result=result)
    return result


# --- confirm ---------------------------------------------------------------------


def confirm_batch(*, actor, batch: ImportBatch, preview_hash: str, operation_key: str) -> dict:
    """Run the confirm gate and move the batch to COMMITTING.

    `preview_hash` is the digest the operator saw. Raises ConfirmError carrying
    every blocker, so the page can list them all at once.
    """
    _require_admin(actor)
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay

    profile = get_profile(batch.mapping_profile_id)
    open_blocking = batch.conflicts.filter(severity="BLOCKING", status="OPEN").count()
    stored_hash = batch.preview_hash
    staged_hash = recompute_preview_hash(batch)
    staged_file = hashlib.sha256(bytes(batch.raw_bytes or b"")).hexdigest()

    decision = evaluate_confirm(
        batch={
            "status": batch.status,
            "mapping_profile_id": batch.mapping_profile_id,
            "mapping_profile_version": batch.mapping_profile_version,
            "file_sha256": batch.file_sha256,
            "preview_hash": stored_hash,
        },
        actor={"role": import_actor_role(actor)},
        profile={
            "profile_id": profile["profileId"],
            "version": profile["version"],
            "status": profile["status"],
        }
        if profile
        else None,
        validation=batch.validation,
        conflicts=[],
        conflict_summary={"open_blocking_conflicts": open_blocking},
        staged={
            "file_sha256": staged_file,
            "mapping_profile_id": batch.mapping_profile_id,
            "mapping_profile_version": batch.mapping_profile_version,
            "preview_hash": staged_hash,
        },
        requires_batch_business_date=source_requires_batch_date(batch.source_kind),
        batch_business_date=batch.batch_date_meta or None,
    )
    blockers = list(decision["blockers"])
    if preview_hash != stored_hash:
        blockers.append(
            {
                "code": "PREVIEW_HASH_MISMATCH",
                "message": "the preview changed after you looked at it; reload it before confirming",
            }
        )
    if blockers:
        raise ConfirmError(blockers)

    with transaction.atomic():
        transition(
            batch, "COMMITTING", actor=actor, reason="confirmed",
            blocking_conflicts_open=open_blocking, confirmed_by=actor,
        )
        return _remember(batch, operation_key, "confirm", {"status": batch.status})


# --- commit ----------------------------------------------------------------------


def _resolutions(batch: ImportBatch) -> dict[int, dict[str, str]]:
    """row_index -> {conflict kind: resolution} from the resolved conflicts."""
    out: dict[int, dict[str, str]] = {}
    for conflict in batch.conflicts.exclude(resolution=""):
        out.setdefault(conflict.row_index, {})[conflict.kind] = conflict.resolution
    return out


def _closer(directory: adapters.Directory, facts: dict) -> User | None:
    return directory.users.get(facts["fields"].get("closerUserId") or "")


def _team(facts: dict) -> Team | None:
    code = facts["fields"].get("closerTeamId")
    return Team.objects.filter(code=code).first() if code else None


def _create_customer(*, facts: dict, closer: User | None, journal: Journal, split: bool) -> Customer:
    info = facts["customer"]
    key = adapters.new_customer_phone_key(info["phone1"], info["phone2"], facts["orderId"])
    if split:
        key = f"{key}#{facts['orderId']}"
    existing = Customer.objects.filter(phone_key=key).first()
    if existing:
        return existing  # an earlier order of this file already created this person
    customer = Customer.objects.create(
        phone_key=key,
        phone1=info["phone1"],
        phone2=info["phone2"],
        customer_name=info["name"],
        address=info["address"],
        subdistrict=info["subdistrict"],
        city=info["district"],
        province=info["province"],
        postal_code=info["postalCode"],
        owner_display=closer.staff_name if closer else "",
        staff_code=closer.staff_code if closer else "",
    )
    journal.created("CUSTOMER", customer)
    return customer


def _order_extra(facts: dict) -> dict:
    fields = facts["fields"]
    address = {k.split(".", 1)[1]: v for k, v in fields.items() if k.startswith("address.") and v}
    extra = {
        "closerUserId": fields.get("closerUserId"),
        "closerTeamId": fields.get("closerTeamId"),
        "channel": fields.get("channel"),
        "url": fields.get("url"),
        "phone": fields.get("phone"),
        "address": address,
    }
    return {k: v for k, v in extra.items() if v not in (None, "", {})}


def _write_lines(order: Order, lines: list[dict], journal: Journal) -> None:
    for line in list(order.lines.all()):
        journal.deleted("ORDER_LINE", snapshot(line), line.pk)
    adapters.replace_order_lines(order, lines)
    for line in order.lines.order_by("id"):
        journal.created("ORDER_LINE", line)


def _stamp(facts: dict) -> dict:
    stamp = {"sourceId": IMPORT_SOURCE_ID, "sourceUpdatedAt": facts.get("orderedAt"), "precedence": 10}
    return {k: dict(stamp) for k, v in facts["fields"].items() if v not in (None, "", [], {}) and k != "items"}


def _create_order(*, actor, batch, customer, facts, closer, journal) -> Order:
    fields = facts["fields"]
    order = Order.objects.create(
        customer=customer,
        order_no=facts["orderId"],
        order_date=adapters.ms_to_bangkok_date(facts.get("orderedAt")),
        sale_type="NEW_ORDER",
        carrier=fields.get("carrier") or "",
        tracking_no=fields.get("trackingNumber") or "",
        total_amount=None if fields.get("total") is None else Decimal(str(round(fields["total"], 2))),
        owner_display=closer.staff_name if closer else "",
        staff_code=closer.staff_code if closer else "",
        source_type="import",
        uploaded_by=actor.email or "",
        import_batch_id=batch.batch_id,
        import_fields=_order_extra(facts),
        field_provenance=_stamp(facts),
    )
    journal.created("ORDER", order)
    _write_lines(order, fields.get("items") or [], journal)
    return order


def _update_order(*, order: Order, facts: dict, resolution: dict, journal: Journal) -> bool:
    """Merge the file's values into an existing order. Returns True if it changed."""
    fields = dict(facts["fields"])
    patch = {
        "orderId": facts["orderId"],
        "fields": fields,
        "source": {"sourceId": IMPORT_SOURCE_ID, "sourceUpdatedAt": facts.get("orderedAt"), "precedence": 10},
    }
    existing = adapters.order_to_prototype(order)
    allow_reversal = resolution.get("REVERSAL") == "APPROVE_REVERSAL"
    result = merge_order(existing, patch, allow_reversal=allow_reversal)
    updates = dict(result["updates"])
    # A blocking field decision was resolved by a person: KEEP_EXISTING drops
    # the incoming value, ACCEPT_INCOMING / APPROVE_REVERSAL applies it.
    for decision in result["blocking"]:
        kind = "REVERSAL" if decision["kind"] == "EXPLICIT_REVERSAL_REQUIRED" else "STALE"
        choice = resolution.get(kind)
        if choice in ("ACCEPT_INCOMING", "APPROVE_REVERSAL") and decision["field"] != "customerId":
            updates[decision["field"]] = decision["after"]
    if not updates:
        return False
    before = snapshot(order)
    adapters.apply_order_updates(order, updates)
    order.save()
    journal.updated("ORDER", before, order)
    if "items" in updates:
        _write_lines(order, updates["items"], journal)
    return True


def _ensure_lead_or_intake(*, batch, customer, facts, team, journal) -> None:
    """Team known and no lead: create it. Team unknown and no lead: Global Unassigned."""
    has_leads = Lead.objects.filter(customer=customer).exists()
    if team is not None:
        if not Lead.objects.filter(customer=customer, team=team).exists():
            lead = Lead.objects.create(
                team=team, customer=customer, lifecycle_status="NEW",
                routing_state="TEAM_UNASSIGNED", ownership_policy=team.ownership_policy,
            )
            access = derive_lead_access(team.code, None, frozenset())
            lead.access_team_ids = access["access_team_ids"]
            lead.access_user_ids = access["access_user_ids"]
            lead.save(update_fields=["access_team_ids", "access_user_ids", "updated_at"])
    elif not has_leads:
        item, created = upsert_intake(
            customer=customer, source_order_id=facts["orderId"], source_kind=batch.source_kind,
            source_ref=f"batch:{batch.batch_id}",
        )
        if created:
            journal.created("INTAKE", item)


def _apply_lead_resolutions(*, actor, customer, closer, resolution, order) -> None:
    for lead in Lead.objects.select_related("team", "caretaker").filter(customer=customer):
        if resolution.get("REACTIVATION") == "REACTIVATE" and lead.lifecycle_status == "STOPPED":
            reactivate_lead(actor=actor, lead=lead, reason=f"import order {order.order_no}")
        if resolution.get("OWNERSHIP") == "REASSIGN" and closer is not None:
            reassign_caretaker(actor=actor, lead=lead, caretaker=closer, reason=f"import order {order.order_no}")


def _apply_group(*, actor, batch, plan, directory, journal, resolutions) -> str:
    facts = plan.facts
    resolution = resolutions.get(plan.row_index, {})
    closer = _closer(directory, facts)
    team = _team(facts)

    customer = plan.customer
    if customer is None:
        if plan.identity_kind == "HUMAN_REVIEW":
            choice = resolution.get("IDENTITY")
            if choice == "AUTO_MATCH":
                customer = Customer.objects.get(pk=int(facts["identity"]["candidateCustomerId"]))
            elif choice == "NEW_CUSTOMER":
                customer = _create_customer(facts=facts, closer=closer, journal=journal, split=True)
            else:
                raise ValueError(f"UNRESOLVED_IDENTITY: row {plan.row_index}")
        else:
            customer = _create_customer(facts=facts, closer=closer, journal=journal, split=False)

    order = Order.objects.filter(customer=customer, order_no=facts["orderId"]).first()
    tracker = LeadTracker(journal, customer)
    if order is None:
        order = _create_order(actor=actor, batch=batch, customer=customer, facts=facts, closer=closer, journal=journal)
        outcome = "created"
    else:
        outcome = "updated" if _update_order(order=order, facts=facts, resolution=resolution, journal=journal) else "unchanged"

    if outcome != "unchanged":
        _ensure_lead_or_intake(batch=batch, customer=customer, facts=facts, team=team, journal=journal)
        for lead in Lead.objects.select_related("team", "caretaker").filter(customer=customer):
            lock = lead.cycles.filter(status="ACTIVE", lock_active=True).exists()
            apply_order_to_lead(actor=actor, lead=lead, order=order, lock_active=lock)
            _refresh_order_rollups(lead=lead, order=order)
            recompute_lead_missed_channel(lead=lead)
        _apply_lead_resolutions(actor=actor, customer=customer, closer=closer, resolution=resolution, order=order)
    tracker.finish()
    return outcome


def commit_batch(*, actor, batch: ImportBatch, operation_key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    """Write the confirmed batch in chunks. Returns counts per outcome."""
    _require_admin(actor)
    replay = _replay(batch, operation_key)
    if replay is not None:
        return replay
    if batch.status != "COMMITTING":
        raise ValueError(f"ILLEGAL_STATE: a batch in {batch.status} cannot be committed")
    chunk_size = max(1, min(int(chunk_size), MAX_CHUNK_SIZE))

    if recompute_preview_hash(batch) != batch.preview_hash:
        transition(batch, "FAILED_RETRYABLE", actor=actor, reason="STALE_PREVIEW before any write")
        raise StalePreviewError("the decisions changed after the preview was approved")

    directory = adapters.Directory()
    resolutions = _resolutions(batch)
    journal = Journal(batch, attempt=batch.attempt_count)
    plans = list(batch.plans.exclude(classification__in=["INVALID"]).order_by("row_index").select_related("customer"))
    totals = {"created": 0, "updated": 0, "unchanged": 0, "chunks": 0}
    try:
        for start in range(0, len(plans), chunk_size):
            with transaction.atomic():
                for plan in plans[start : start + chunk_size]:
                    totals[_apply_group(
                        actor=actor, batch=batch, plan=plan, directory=directory,
                        journal=journal, resolutions=resolutions,
                    )] += 1
            totals["chunks"] += 1
    except Exception as error:
        # Earlier chunks are committed and journalled; the batch can be retried
        # or rolled back. Nothing from the failing chunk survived.
        transition(batch, "FAILED_RETRYABLE", actor=actor, reason=f"{type(error).__name__}: {error}"[:200])
        raise

    with transaction.atomic():
        transition(batch, "COMMITTED", actor=actor, reason="commit complete")
        return _remember(batch, operation_key, "commit", {**totals, "status": "COMMITTED"})
