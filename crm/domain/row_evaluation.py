"""Preview / classification of one import row (port of src/import/preview.ts).

Nothing here writes business data. It turns one incoming row plus the current
state into a classification with explicit conflicts, so that Confirm can be a
decision informed by counts rather than a leap of faith.

Rows, contexts and results are dicts with the prototype's camelCase keys (the
import plan's wire format). Times are epoch milliseconds. Ownership comes from
crm.domain.ownership, which already ports the policies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from crm.domain.identity import identity_blocks_commit
from crm.domain.order_merge import merge_order, summarize_decisions
from crm.domain.ownership import (
    OwnershipOrderFacts,
    evaluate_stopped_reactivation,
    resolve_ownership,
)


def _dt(millis):
    return None if millis is None else datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _ms(moment):
    return None if moment is None else round(moment.timestamp() * 1000)


def _ownership_dict(decision) -> dict:
    conflict = decision.conflict
    return {
        "caretakerUserId": decision.caretaker_user_id,
        "changed": decision.changed,
        "newSegmentStartedAt": _ms(decision.new_segment_started_at),
        "reason": decision.reason,
        "conflict": None
        if conflict is None
        else {
            "kind": conflict.kind,
            "blocking": conflict.blocking,
            "summary": conflict.summary,
            "options": list(conflict.options),
        },
    }


def evaluate_row(facts: dict, context: dict) -> dict:
    """Classify one row. `context` = {identity, existingOrder, existingLead, activeUserIds}."""
    conflicts: list[dict] = []
    reasons: list[str] = []
    existing_lead = context.get("existingLead")
    existing_order = context.get("existingOrder")
    team_id = facts.get("closerTeamId") or (existing_lead["teamId"] if existing_lead else None)
    identity = context["identity"]
    active = frozenset(context["activeUserIds"])

    if existing_order is None and existing_lead is None and identity_blocks_commit(identity["kind"]):
        detail = "; ".join(identity["reasons"]) or "similar name without exact phone"
        conflicts.append(
            {
                "kind": "IDENTITY",
                "severity": "BLOCKING",
                "summary": f"Customer identity is ambiguous ({detail}).",
                "teamId": team_id,
                "rowIndex": facts["rowIndex"],
                "options": ["AUTO_MATCH", "NEW_CUSTOMER"],
            }
        )
        reasons.append("identity review required before a new lead can be created")

    merge_summary = None
    merge_blocking = False
    if existing_order is not None:
        patch = {
            "orderId": facts["orderId"],
            "fields": facts["fields"],
            "source": {"sourceId": "preview", "sourceUpdatedAt": facts["orderedAt"], "precedence": 10},
        }
        result = merge_order(existing_order, patch, allow_reversal=facts.get("allowReversal") is True)
        merge_summary = summarize_decisions(result["decisions"])
        merge_blocking = result["hasBlocking"]
        for decision in result["blocking"]:
            reversal = decision["kind"] == "EXPLICIT_REVERSAL_REQUIRED"
            conflicts.append(
                {
                    "kind": "REVERSAL" if reversal else "STALE",
                    "severity": "BLOCKING",
                    "summary": f"{decision['field']}: {decision['reason']}",
                    "teamId": team_id,
                    "rowIndex": facts["rowIndex"],
                    "options": ["KEEP_EXISTING", "APPROVE_REVERSAL"]
                    if reversal
                    else ["KEEP_EXISTING", "ACCEPT_INCOMING"],
                }
            )

    order_facts = OwnershipOrderFacts(
        order_id=facts["orderId"],
        ordered_at=_dt(facts["orderedAt"]),
        closer_user_id=facts.get("closerUserId"),
        closer_team_code=facts.get("closerTeamId"),
    )

    ownership = None
    if existing_lead is not None:
        decision = resolve_ownership(
            policy=existing_lead["ownershipPolicy"],
            team_code=existing_lead["teamId"],
            current_caretaker_user_id=existing_lead["caretakerUserId"],
            ownership_started_at=_dt(existing_lead.get("ownershipStartedAt")),
            lifecycle_status=existing_lead["lifecycleStatus"],
            lock_active=existing_lead["lockActive"],
            order=order_facts,
            active_user_ids=active,
        )
        ownership = _ownership_dict(decision)
        if ownership["conflict"]:
            conflicts.append(
                {
                    "kind": "OWNERSHIP",
                    "severity": "BLOCKING" if ownership["conflict"]["blocking"] else "WARNING",
                    "summary": ownership["conflict"]["summary"],
                    "teamId": team_id,
                    "rowIndex": facts["rowIndex"],
                    "options": ownership["conflict"]["options"],
                }
            )

    reactivation = None
    if existing_lead is not None and existing_lead["lifecycleStatus"] == "STOPPED":
        outcome = evaluate_stopped_reactivation(
            lifecycle_status=existing_lead["lifecycleStatus"],
            prior_caretaker_user_id=existing_lead["caretakerUserId"],
            previous_stopped_at=None,
            order=order_facts,
            active_user_ids=active,
        )
        reactivation = {
            "requiresReview": outcome.requires_review,
            "proposedAction": outcome.proposed_action,
            "sameCaretakerAsBefore": outcome.same_caretaker_as_before,
            "teamId": outcome.team_code,
        }
        conflicts.append(
            {
                "kind": "REACTIVATION",
                "severity": "BLOCKING",
                "summary": "A stopped lead received a new order from the same caretaker: choose REACTIVATE or KEEP_STOPPED."
                if outcome.same_caretaker_as_before
                else "A stopped lead received a new order: choose a reactivation action.",
                "teamId": team_id,
                "rowIndex": facts["rowIndex"],
                "options": ["REACTIVATE", "KEEP_STOPPED"],
            }
        )

    if existing_order is None and existing_lead is None and facts.get("closerUserId") is None:
        reasons.append("no usable team/caretaker: routes to unassigned review rather than a guessed owner")

    blocking = any(c["severity"] == "BLOCKING" for c in conflicts) or merge_blocking
    if blocking:
        classification = "CONFLICT"
    elif existing_order is None:
        classification = "NEW_ORDER"
    elif merge_summary and merge_summary["safeUpdate"] > 0:
        classification = "SAFE_UPDATE"
    elif merge_summary and merge_summary["staleInput"] > 0:
        classification = "STALE_INPUT"
    else:
        classification = "UNCHANGED"

    return {
        "rowIndex": facts["rowIndex"],
        "orderId": facts["orderId"],
        "classification": classification,
        "blocking": blocking,
        "merge": merge_summary,
        "ownership": ownership,
        "reactivation": reactivation,
        "conflicts": conflicts,
        "reasons": reasons,
    }


def aggregate_preview(evaluations: list[dict], new_customers: int) -> dict:
    """Counts shown on the import page. Each evaluation needs `classification`
    and `blocking`."""
    aggregate = {
        "rows": len(evaluations),
        "newOrders": 0,
        "newCustomers": new_customers,
        "safeUpdates": 0,
        "staleInputs": 0,
        "unchanged": 0,
        "conflicts": 0,
        "blockingConflicts": 0,
        "invalidRows": 0,
    }
    key = {
        "NEW_ORDER": "newOrders",
        "SAFE_UPDATE": "safeUpdates",
        "STALE_INPUT": "staleInputs",
        "UNCHANGED": "unchanged",
        "CONFLICT": "conflicts",
        "INVALID": "invalidRows",
    }
    for evaluation in evaluations:
        if evaluation["classification"] in key:
            aggregate[key[evaluation["classification"]]] += 1
        if evaluation["blocking"]:
            aggregate["blockingConflicts"] += 1
    return aggregate
