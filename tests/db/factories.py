"""Shared builders for the Lead-surface DB and view tests.

The repo's older tests each define their own `make_customer`, which was fine
when one file needed one row. The Lead surface needs Team + User + Lead +
Order together in ten files, and ten copies of that drift apart — the first
copy to gain a `customer_uid` argument makes the others silently build
different objects. One module, one definition.

Not factory_boy: these are plain functions so a test reads as the rows it
needs, and nothing is generated implicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.leads.models import Lead
from crm.orders.models import Order, OrderLine
from crm.teams.models import Team

#: Matches the prototype's test epoch (tests/support/factories.ts T0).
T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)

ROLE_EDITOR = "EDITOR"
ROLE_ADMIN = "ADMIN"
ROLE_TELESELL = "พนักงาน"
ROLE_VIEWER = "ทั่วไป"


def make_user(
    email: str | None = None,
    role: str = ROLE_TELESELL,
    staff_code: str = "",
    staff_name: str = "",
    is_active: bool = True,
    team_scope: list[str] | None = None,
) -> User:
    """A real accounts.User row.

    `team_scope` is accepted from the start so Task 14's tests do not have to
    change this signature; it is only applied once the field exists.
    """
    email = email or f"user{User.objects.count() + 1}@example.com"
    user = User(
        email=email,
        role=role,
        staff_code=staff_code,
        staff_name=staff_name or staff_code or email,
        is_active=is_active,
        # Onboarded. The model defaults this True, and the password-change
        # middleware then redirects every request before the view runs, so a
        # permission test would measure the redirect instead of the gate.
        must_change_password=False,
    )
    if team_scope is not None and hasattr(user, "team_scope"):
        user.team_scope = team_scope
    user.set_unusable_password()
    user.save()
    return user


def make_customer(**kwargs) -> Customer:
    uid = kwargs.pop("_uid", None) or f"{Customer.objects.count() + 1:07d}"
    defaults = {
        "phone_key": f"09{uid}",
        "phone1": f"09{uid}",
        "customer_name": "ทดสอบ",
        "staff_code": "",
        "owner_display": "",
    }
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def make_team(code: str = "CRM_TEAM", policy: dict | None = None, **kwargs) -> Team:
    """Idempotent by code, so several leads can share a team in one test."""
    defaults = {
        "name": code,
        "display_name": {"CRM_TEAM": "CRM Team", "UPSELL_TEAM": "Upsell Team"}.get(code, code),
        "policy": policy or {"ownershipPolicy": "EXCLUSIVE"},
    }
    defaults.update(kwargs)
    team, created = Team.objects.get_or_create(code=code, defaults=defaults)
    if not created and policy is not None and team.policy != policy:
        team.policy = policy
        team.save(update_fields=["policy", "updated_at"])
    return team


def make_lead(
    customer: Customer | None = None,
    team_code: str = "CRM_TEAM",
    team: Team | None = None,
    caretaker: User | None = None,
    policy: dict | None = None,
    **kwargs,
) -> Lead:
    customer = customer if customer is not None else make_customer()
    team = team or make_team(team_code, policy=policy)
    defaults = {
        "customer": customer,
        "team": team,
        "caretaker": caretaker,
        "lifecycle_status": "NEW",
        "routing_state": "ASSIGNED" if caretaker else "TEAM_UNASSIGNED",
        "ownership_policy": team.ownership_policy,
        "ownership_started_at": T0 if caretaker else None,
    }
    defaults.update(kwargs)
    return Lead.objects.create(**defaults)


def make_order(
    customer: Customer | None = None,
    closer: User | None = None,
    ordered_at: datetime | None = None,
    # This repo's real vocabulary, not the prototype's machine codes: the live
    # crm_order.order_status holds only 'สำเร็จ' and blank. Defaulting to
    # DELIVERED here would make every test exercise a value the adapter's
    # status map correctly refuses to recognise.
    order_status: str = "สำเร็จ",
    order_no: str | None = None,
    sale_type: str = "NEW_ORDER",
    total_amount: str | Decimal | None = None,
    with_line: bool = False,
) -> Order:
    """An Order row.

    `closer` is written to both staff_code and owner_display, which is how the
    existing schema records who closed an order; the Lead surface reads the
    staff_code side.
    """
    customer = customer if customer is not None else make_customer()
    ordered_at = ordered_at or T0
    order = Order.objects.create(
        customer=customer,
        order_no=order_no or f"SO-{Order.objects.count() + 1:05d}",
        order_date=ordered_at.date(),
        sale_type=sale_type,
        order_status=order_status,
        staff_code=closer.staff_code if closer else "",
        owner_display=closer.staff_name if closer else "",
        source_type="manual",
        total_amount=Decimal(total_amount) if total_amount is not None else None,
    )
    if with_line:
        OrderLine.objects.create(
            order=order, sku="SKU-1", product_name="สินค้า", quantity=1, amount=Decimal("100.00")
        )
    return order
