"""Write paths for /customers row actions: follow marker, owner
assignment, URL update. Ported from crm_streamlit/pages/customers.py
render_customer_actions, with the legacy's confusing 0/1/2/3/RESET marker
vocabulary retired in favor of the one canonical Followup.status enum
(docs/DECISIONS.md #10) — there is no second vocabulary left to reconcile.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction

from crm.core.identity import clean, phone_key as compute_phone_key
from crm.core.permissions import can_assign_customer_owner, can_edit_customer_lead
from crm.customers.models import Customer
from crm.followups.models import Followup


def find_or_create_customer(phone1: str, phone2: str, defaults: dict) -> tuple[Customer, bool]:
    """The one phone_key-based find-or-create, shared by the Excel importer
    (crm.imports.management.commands.import_xlsx) and the manual order
    form — both need "does a customer with this phone already exist"
    without duplicating the phone_key computation.
    """
    key = compute_phone_key(phone1, phone2, None)
    return get_or_create_by_phone_key(key, {"phone1": phone1, "phone2": phone2, **defaults})


def get_or_create_by_phone_key(key: str, defaults: dict) -> tuple[Customer, bool]:
    """phone_key is not unique, so `get_or_create` would raise
    MultipleObjectsReturned once two customers share a phone. Take the oldest."""
    customer = Customer.objects.filter(phone_key=key).order_by("id").first()
    if customer is not None:
        return customer, False
    return Customer.objects.create(phone_key=key, **defaults), True


def save_follow_marker(user, customer: Customer, status: str) -> Followup:
    if not can_edit_customer_lead(user, customer):
        raise PermissionDenied("ไม่มีสิทธิ์แก้ไขรายการนี้")

    followup, _ = Followup.objects.get_or_create(customer=customer)
    followup.status = status
    followup.updated_by = clean(getattr(user, "email", ""))
    followup.save()
    return followup


@transaction.atomic
def assign_owner(user, customer: Customer, owner_display: str, staff_code: str, reason: str = "") -> int:
    """Customer-level owner assignment, routed through the Lead ownership path.

    Ownership lives on the Lead (08_SECURITY: server-mediated, baseline +
    access + audit + missed-channel together), so this reassigns EVERY lead of
    the customer via crm.leads.services.reassign_caretaker (manual=True:
    active target, lead-holding role in the lead team, reason required, row
    lock, version bump, segment, audit). A customer with no lead is refused.
    All-or-nothing: any lead rejecting the target rolls the whole thing back.

    Customer.staff_code / owner_display and the denormalised Order/Followup
    copies (row scoping reads them) are then synced as a CONSEQUENCE, never as
    the source of truth.
    """
    from crm.accounts.models import User
    from crm.leads.services import reassign_caretaker

    if not can_assign_customer_owner(user):
        raise PermissionDenied("ไม่มีสิทธิ์มอบหมายผู้ดูแล")
    if not (reason or "").strip():
        raise ValueError("กรุณาระบุเหตุผลในการมอบหมายผู้ดูแล")

    target = User.objects.filter(staff_code=(staff_code or "").strip(), is_active=True).first()
    if not (staff_code or "").strip() or target is None:
        raise ValueError("ไม่พบผู้ดูแลที่ใช้งานอยู่ตาม staff_code ที่เลือก")

    leads = list(customer.leads.select_related("team").order_by("id"))
    if not leads:
        raise ValueError("ลูกค้ารายนี้ยังไม่มี Lead จึงมอบหมายผู้ดูแลไม่ได้")

    for lead in leads:
        reassign_caretaker(
            actor=user, lead=lead, caretaker=target, reason=reason.strip(), manual=True
        )

    owner_display = owner_display or target.staff_name or ""
    customer.owner_display = owner_display
    customer.staff_code = target.staff_code
    customer.save(update_fields=["owner_display", "staff_code", "updated_at"])

    updated = customer.orders.update(owner_display=owner_display, staff_code=target.staff_code)
    Followup.objects.filter(customer=customer).update(
        owner_display=owner_display, staff_code=target.staff_code
    )
    return updated + 1  # +1 for the customer row itself, matching the legacy "N rows updated" message


def assign_url(user, customer: Customer, url: str) -> Customer:
    if not can_assign_customer_owner(user):
        raise PermissionDenied("ไม่มีสิทธิ์อัปเดต URL")
    if not clean(url):
        raise ValueError("กรุณากรอก URL ก่อนบันทึก")

    customer.url = url
    customer.save(update_fields=["url", "updated_at"])
    return customer
