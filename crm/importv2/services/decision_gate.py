"""Consume Migration Decisions during preview (spec 07: "Do not guess").

A MigrationDecision is a human answer to an input no exact rule can decide. The
preview looks every staged order up against them:

 - RESOLVED decisions are APPLIED:
     STAFF_ALIAS   MAP_TO_USER      the named active user becomes the closer;
                   HISTORICAL_ONLY  no user is assigned, the name stays as text;
     IDENTITY      MATCH_EXISTING   the order belongs to the named customer;
                   CREATE_NEW_CUSTOMER / KEEP_SEPARATE
                                    a separate customer (split phone key) is made;
     UNKNOWN_STATUS ARCHIVE_ONLY    the order is excluded from the import
                   MAP_TO_STATUS    accepted; the status columns are not wired
                                    into the import yet (known gap), nothing is
                                    inferred;
     NULL_DATE     SET_AUTHORITATIVE_DATE  the human-supplied date is used;
                   ARCHIVE_ONLY     the order is excluded from the import;
 - OPEN / DEFERRED decisions, and KEEP_BLOCKED, make the order a BLOCKING
   conflict (kind MAPPING, no options): Confirm stays shut until the decision is
   resolved on the Migration Decisions page and the preview is rebuilt.

A staff name that exactly matches one active user never consults an alias
decision (the exact rule wins; fuzzy matching is forbidden).

Every decision a row consumed is recorded on the plan (`facts["decisions"]`) with
its revision, and the preview hash covers those revisions, so reopening or
re-resolving a decision after the preview makes the preview stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crm.domain.phone import normalize_phone
from crm.importv2.models import MigrationDecision
from crm.importv2.services.adapters import Directory, _norm


@dataclass
class RowVerdict:
    closer_set: bool = False
    closer: object = None  # User or None, meaningful only when closer_set
    ordered_at: int | None = None
    identity_override: dict | None = None
    customer_id: int | None = None
    separate_customer: bool = False
    excluded: str = ""
    blockers: list[str] = field(default_factory=list)
    used: list[dict] = field(default_factory=list)


def _used(decision: MigrationDecision, action: str) -> dict:
    return {"decisionId": decision.decision_id, "revision": decision.revision, "status": decision.status, "action": action}


def _action(decision: MigrationDecision) -> tuple[str, dict]:
    resolution = decision.resolution or {}
    return resolution.get("action", ""), resolution.get("payload") or {}


class DecisionIndex:
    def __init__(self) -> None:
        self.staff: dict[str, MigrationDecision] = {}
        self.identity_by_phone: dict[str, MigrationDecision] = {}
        self.identity_by_order: dict[str, MigrationDecision] = {}
        self.status: dict[str, MigrationDecision] = {}
        self.null_date: dict[str, MigrationDecision] = {}
        for d in MigrationDecision.objects.all().order_by("id"):
            ev = d.evidence or {}
            if d.category == "STAFF_ALIAS":
                key = _norm(ev.get("normalizedValue") or ev.get("historicalDisplayValue") or "")
                if key:
                    self.staff[key] = d
            elif d.category == "IDENTITY_REVIEW":
                phone = ev.get("normalizedPhone") or ""
                if phone:
                    self.identity_by_phone[phone] = d
                else:
                    for order_id in ev.get("sampleOrderIds") or []:
                        self.identity_by_order[str(order_id)] = d
            elif d.category == "UNKNOWN_STATUS":
                key = _norm(ev.get("rawValue") or "")
                if key:
                    self.status[key] = d
            elif d.category == "NULL_DATE_ORDER":
                if ev.get("sourceOrderId"):
                    self.null_date[str(ev["sourceOrderId"])] = d

    # -- per category ---------------------------------------------------------------

    def _staff(self, verdict: RowVerdict, directory: Directory, name: str | None, is_closer: bool) -> None:
        key = _norm(name or "")
        if not key or directory.user_for(name) is not None:
            return
        decision = self.staff.get(key)
        if decision is None:
            return
        action, payload = _action(decision)
        if decision.status != "RESOLVED":
            verdict.blockers.append(
                f"Staff alias '{name}' has an unresolved decision ({decision.status}); resolve it on the Migration Decisions page"
            )
            verdict.used.append(_used(decision, decision.status))
            return
        verdict.used.append(_used(decision, action))
        if not is_closer:
            return
        verdict.closer_set = True
        if action == "MAP_TO_USER":
            verdict.closer = directory.users.get(str(payload.get("userId")))  # None: inactive now -> history only
        else:  # HISTORICAL_ONLY: the name stays on the customer as text, no user
            verdict.closer = None

    def _identity(self, verdict: RowVerdict, values: dict, order_id: str) -> None:
        national = normalize_phone(values.get("phone") or "")["national"]
        decision = self.identity_by_phone.get(national) if national else self.identity_by_order.get(order_id)
        if decision is None:
            return
        action, payload = _action(decision)
        if decision.status != "RESOLVED":
            verdict.blockers.append(
                f"Customer identity for this order has an unresolved decision ({decision.status}); "
                "resolve it on the Migration Decisions page"
            )
            verdict.used.append(_used(decision, decision.status))
            return
        verdict.used.append(_used(decision, action))
        if action == "MATCH_EXISTING":
            customer_id = str(payload.get("customerId") or "")
            if not customer_id.isdigit():
                verdict.blockers.append("The identity decision names a customer that cannot be used; reopen it")
                return
            verdict.customer_id = int(customer_id)
            verdict.identity_override = {
                "kind": "AUTO_MATCH", "candidateCustomerId": customer_id, "nameScore": 1,
                "phoneExact": True, "phoneNear": False, "reasons": ["resolved by migration decision"],
            }
        else:  # CREATE_NEW_CUSTOMER / KEEP_SEPARATE: never merge, never guess
            verdict.separate_customer = True
            verdict.identity_override = {
                "kind": "NEW_CUSTOMER", "candidateCustomerId": None, "nameScore": 0,
                "phoneExact": False, "phoneNear": False, "reasons": ["kept separate by migration decision"],
            }

    def _status(self, verdict: RowVerdict, values: dict) -> None:
        raw = values.get("orderStatusText")
        decision = self.status.get(_norm(str(raw))) if raw not in (None, "") else None
        if decision is None:
            return
        action, _ = _action(decision)
        if decision.status != "RESOLVED":
            verdict.blockers.append(f"Status '{raw}' has an unresolved decision ({decision.status})")
        elif action == "ARCHIVE_ONLY":
            verdict.excluded = f"status '{raw}' archived by decision"
        verdict.used.append(_used(decision, decision.status if decision.status != "RESOLVED" else action))

    def _date(self, verdict: RowVerdict, values: dict, order_id: str) -> None:
        if values.get("orderedAt") is not None:
            return
        decision = self.null_date.get(order_id)
        if decision is None:
            return
        action, payload = _action(decision)
        if decision.status == "RESOLVED" and action == "SET_AUTHORITATIVE_DATE":
            verdict.ordered_at = int(payload["orderedAt"])
        elif decision.status == "RESOLVED" and action == "ARCHIVE_ONLY":
            verdict.excluded = "order without a date archived by decision"
        else:
            verdict.blockers.append("This order has no date and no authoritative date decision (never invented)")
        verdict.used.append(_used(decision, decision.status if decision.status != "RESOLVED" else action))

    # -- entry point ------------------------------------------------------------------

    def verdict(self, values: dict, order_id: str, directory: Directory) -> RowVerdict:
        verdict = RowVerdict()
        self._staff(verdict, directory, values.get("sellerName"), is_closer=True)
        self._staff(verdict, directory, values.get("caretakerName"), is_closer=False)
        self._identity(verdict, values, order_id)
        self._status(verdict, values)
        self._date(verdict, values, order_id)
        return verdict


def live_decision_state(decision_ids: set[str]) -> list[dict]:
    """The current (status, revision) of the decisions a batch consumed, in a
    stable order, for the preview hash."""
    rows = MigrationDecision.objects.filter(decision_id__in=decision_ids).order_by("decision_id")
    return [
        {"conflictId": f"decision:{d.decision_id}", "choice": f"{d.status}:{d.revision}"}
        for d in rows
    ]
