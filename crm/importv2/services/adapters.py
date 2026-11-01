"""Translate between this repo's rows and the prototype's import dicts.

The rules in crm.domain speak the prototype's vocabulary (orderStatus,
address.line, caretakerUserId, epoch milliseconds). The tables here do not.
Every conversion between the two lives in this module so the mapping is
reviewable in one place.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from crm.accounts.models import User
from crm.core.identity import phone_key
from crm.domain.bangkok import BANGKOK_TZ
from crm.followcycles.models import FollowupCycle
from crm.leads.models import Lead
from crm.leads.order_status import to_machine_status
from crm.orders.models import Order, OrderLine
from crm.teams.models import Team


def date_to_ms(day: date | None) -> int | None:
    """Bangkok midnight of a calendar day, as epoch milliseconds."""
    if day is None:
        return None
    midnight = datetime.combine(day, time.min, tzinfo=BANGKOK_TZ)
    return round(midnight.timestamp() * 1000)


def ms_to_bangkok_date(ms: int | None) -> date | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(BANGKOK_TZ).date()


def datetime_to_ms(moment: datetime | None) -> int | None:
    return None if moment is None else round(moment.timestamp() * 1000)


def ms_to_datetime(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


class Directory:
    """Who and which team a file's names refer to, loaded once per preview.

    A staff name resolves only when exactly one ACTIVE user carries it: an
    ambiguous or unknown name is None, which routes the order to review rather
    than guessing an owner (the prototype's "no usable team/caretaker" rule).
    """

    def __init__(self) -> None:
        active = list(User.objects.filter(is_active=True))
        self.active_user_ids = frozenset(str(u.pk) for u in active)
        self._users_by_name: dict[str, list[User]] = {}
        for user in active:
            for label in {user.staff_name, user.staff_code}:
                if label:
                    self._users_by_name.setdefault(_norm(label), []).append(user)
        self.users = {str(u.pk): u for u in active}
        self._teams: dict[str, Team] = {}
        for team in Team.objects.filter(active=True):
            for label in (team.code, team.name, team.display_name):
                if label:
                    self._teams.setdefault(_norm(label), team)

    def user_for(self, name: str | None) -> User | None:
        matches = {u.pk: u for u in self._users_by_name.get(_norm(name or ""), [])}
        return next(iter(matches.values())) if len(matches) == 1 else None

    def team_for(self, name: str | None) -> Team | None:
        return self._teams.get(_norm(name or ""))


def order_to_prototype(order: Order) -> dict:
    """An existing Order as the prototype's merge input (camelCase, ms times)."""
    extra = order.import_fields or {}
    lines = [
        {"sku": l.sku, "productName": l.product_name, "quantity": l.quantity,
         "amount": float(l.amount) if l.amount is not None else None}
        for l in order.lines.order_by("id")
    ]
    status = to_machine_status(order.order_status)
    return {
        "orderId": order.order_no,
        "customerId": str(order.customer_id),
        "orderedAt": date_to_ms(order.order_date),
        "closerUserId": extra.get("closerUserId"),
        "closerTeamId": extra.get("closerTeamId"),
        "caretakerAtOrderTime": extra.get("caretakerAtOrderTime"),
        "channel": extra.get("channel"),
        "shop": extra.get("shop"),
        "subtotal": extra.get("subtotal"),
        "total": float(order.total_amount) if order.total_amount is not None else None,
        "trackingNumber": order.tracking_no or None,
        "carrier": order.carrier or None,
        "url": extra.get("url"),
        "recipientName": extra.get("recipientName"),
        "phone": extra.get("phone"),
        "orderStatus": status,
        "paymentStatus": extra.get("paymentStatus"),
        "shippingStatus": extra.get("shippingStatus"),
        "items": lines,
        "address": extra.get("address") or {},
        "fieldProvenance": order.field_provenance or {},
    }


#: Merge field -> (Order column) for the fields that have one; everything else
#: is kept in Order.import_fields under the prototype's name.
_COLUMN_FIELDS = {"trackingNumber": "tracking_no", "carrier": "carrier"}


def apply_order_updates(order: Order, updates: dict) -> None:
    """Write a merge result's `updates` onto the row (caller saves)."""
    extra = dict(order.import_fields or {})
    for field, value in updates.items():
        if field == "fieldProvenance":
            order.field_provenance = value
        elif field == "orderedAt":
            order.order_date = ms_to_bangkok_date(value)
        elif field == "total":
            order.total_amount = None if value is None else round(float(value), 2)
        elif field in _COLUMN_FIELDS:
            setattr(order, _COLUMN_FIELDS[field], str(value))
        elif field == "items":
            continue  # lines are replaced by the commit service as a unit
        elif field == "address":
            extra["address"] = value
        elif field == "orderStatus":
            continue  # the raw status text is owned by the source file, never inferred
        else:
            extra[field] = value
    order.import_fields = extra


def merge_lines(lines: list[dict]) -> list[dict]:
    """Merge lines sharing (sku, product name): the schema allows one per pair."""
    merged: dict[tuple[str, str], dict] = {}
    for line in lines:
        key = (line.get("sku") or "", line.get("productName") or "")
        if key in merged:
            merged[key]["quantity"] += line.get("quantity") or 0
            if line.get("amount") is not None:
                merged[key]["amount"] = (merged[key]["amount"] or 0) + line["amount"]
        else:
            merged[key] = {
                "sku": key[0],
                "productName": key[1],
                "quantity": line.get("quantity") or 0,
                "amount": line.get("amount"),
            }
    return list(merged.values())


def replace_order_lines(order: Order, lines: list[dict]) -> None:
    OrderLine.objects.filter(order=order).delete()
    OrderLine.objects.bulk_create(
        OrderLine(
            order=order,
            sku=l["sku"],
            product_name=l["productName"],
            quantity=max(int(l["quantity"] or 0), 1),
            amount=None if l["amount"] is None else round(float(l["amount"]), 2),
        )
        for l in merge_lines(lines)
    )


def lead_snapshot(lead: Lead) -> dict:
    lock = FollowupCycle.objects.filter(lead=lead, status="ACTIVE", lock_active=True).exists()
    return {
        "leadId": str(lead.pk),
        "teamId": lead.team.code,
        "lifecycleStatus": lead.lifecycle_status,
        "caretakerUserId": str(lead.caretaker_id) if lead.caretaker_id else None,
        "ownershipStartedAt": datetime_to_ms(lead.ownership_started_at),
        "ownershipPolicy": lead.ownership_policy,
        "lockActive": lock,
    }


def new_customer_phone_key(phone1: str, phone2: str, order_id: str) -> str:
    return phone_key(phone1, phone2, order_id)


def day_after(day: date) -> date:
    return day + timedelta(days=1)
