"""customer_uid is the immutable identity Leads and ownership reference.

02_LOCKED_BUSINESS_RULES.md: "customerId is immutable internal identity.
Phone is indexed and normalized, but is **not** the primary key." Today
Customer.phone_key is UNIQUE, so phone *is* the identity -- and phone_key is
derived from phone1/phone2 (crm.core.identity.phone_key), which means it
changes when a phone changes. These tests pin a separate identity that does
not.

phone_key keeps its UNIQUE constraint in this plan; dropping it lands with the
import engine that needs it (Plan 2). Nothing new keys off it.

Run via: docker compose exec web python -m pytest tests/db
"""

import uuid

import pytest

from crm.core.identity import phone_key
from crm.customers.models import Customer

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def make_customer(**kwargs) -> Customer:
    uid = kwargs.pop("_uid", "0000001")
    defaults = {
        "phone_key": f"09{uid}",
        "phone1": f"09{uid}",
        "customer_name": "ทดสอบ",
        "staff_code": "",
        "owner_display": "",
    }
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def test_customer_uid_is_assigned_and_unique():
    a, b = make_customer(_uid="0000001"), make_customer(_uid="0000002")
    assert a.customer_uid and b.customer_uid
    assert a.customer_uid != b.customer_uid


def test_customer_uid_is_a_uuid():
    assert isinstance(make_customer(_uid="0000001").customer_uid, uuid.UUID)


def test_customer_uid_survives_a_phone_change():
    customer = make_customer(_uid="0000001")
    original = customer.customer_uid
    customer.phone1 = "0999999999"
    customer.phone_key = phone_key("0999999999", "", customer.pk)
    customer.save()
    customer.refresh_from_db()
    assert customer.customer_uid == original


def test_customer_uid_survives_a_name_and_owner_change():
    customer = make_customer(_uid="0000001")
    original = customer.customer_uid
    customer.customer_name = "ชื่อใหม่"
    customer.staff_code = "S0009"
    customer.owner_display = "พนักงานใหม่"
    customer.save()
    customer.refresh_from_db()
    assert customer.customer_uid == original


def test_customer_uid_is_not_editable_through_forms():
    # editable=False keeps it out of ModelForms and the admin, so no UI can
    # rewrite an identity other rows point at.
    assert Customer._meta.get_field("customer_uid").editable is False


def test_customer_uid_is_unique_at_the_database_level():
    assert Customer._meta.get_field("customer_uid").unique is True


def test_two_customers_sharing_no_phone_still_get_distinct_uids():
    uids = {make_customer(_uid=f"000000{i}").customer_uid for i in range(1, 6)}
    assert len(uids) == 5


def test_backfill_assigns_a_uid_to_every_existing_row():
    make_customer(_uid="0000001")
    assert not Customer.objects.filter(customer_uid__isnull=True).exists()


def test_phone_key_remains_unique_in_this_plan():
    # Plan 1 does not relax this; Plan 2 (import engine) does.
    assert Customer._meta.get_field("phone_key").unique is True
