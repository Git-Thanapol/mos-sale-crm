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
    return Customer.objects.get_or_create(
        phone_key=key, defaults={"phone1": phone1, "phone2": phone2, **defaults}
    )


def save_follow_marker(user, customer: Customer, status: str) -> Followup:
    if not can_edit_customer_lead(user, customer):
        raise PermissionDenied("ไม่มีสิทธิ์แก้ไขรายการนี้")

    followup, _ = Followup.objects.get_or_create(customer=customer)
    followup.status = status
    followup.updated_by = clean(getattr(user, "email", ""))
    followup.save()
    return followup


@transaction.atomic
def assign_owner(user, customer: Customer, owner_display: str, staff_code: str) -> int:
    """Owner and staff_code are always written together (invariant 4) —
    and cascaded to every existing Order/Followup row for this customer so
    row-level scoping (which reads Order's OWN staff_code, not a join
    through customer) stays consistent after reassignment. The legacy
    equivalent (assign_owner_to_order_record) updated every crm_data_imports
    row sharing this customer's phone number for the same reason.
    """
    if not can_assign_customer_owner(user):
        raise PermissionDenied("ไม่มีสิทธิ์มอบหมายผู้ดูแล")

    customer.owner_display = owner_display
    customer.staff_code = staff_code
    customer.save(update_fields=["owner_display", "staff_code", "updated_at"])

    updated = customer.orders.update(owner_display=owner_display, staff_code=staff_code)
    Followup.objects.filter(customer=customer).update(owner_display=owner_display, staff_code=staff_code)
    return updated + 1  # +1 for the customer row itself, matching the legacy "N rows updated" message


def assign_url(user, customer: Customer, url: str) -> Customer:
    if not can_assign_customer_owner(user):
        raise PermissionDenied("ไม่มีสิทธิ์อัปเดต URL")
    if not clean(url):
        raise ValueError("กรุณากรอก URL ก่อนบันทึก")

    customer.url = url
    customer.save(update_fields=["url", "updated_at"])
    return customer
