"""The demo seed: DEBUG only, idempotent, and fully removable.

Run via: docker compose exec -T web python -m pytest tests/db/test_seed_leadui_demo.py
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from crm.customers.models import Customer
from crm.importv2.models import ImportBatch, MigrationDecision
from crm.intake.models import IntakeItem
from crm.leads.models import Lead
from crm.orders.models import Order

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def snapshot():
    return (
        Customer.objects.count(), Order.objects.count(), Lead.objects.count(), IntakeItem.objects.count(),
        ImportBatch.objects.count(), MigrationDecision.objects.count(),
    )


def test_it_refuses_to_run_when_debug_is_off():
    with override_settings(DEBUG=False):
        with pytest.raises(CommandError, match="DEBUG"):
            call_command("seed_leadui_demo")
        with pytest.raises(CommandError, match="DEBUG"):
            call_command("seed_leadui_demo", "--clean")
    assert snapshot() == (0, 0, 0, 0, 0, 0)


@override_settings(DEBUG=True)
def test_it_creates_every_kind_of_demo_record_through_the_real_services():
    call_command("seed_leadui_demo")
    assert Lead.objects.filter(customer__customer_name__startswith="ลูกค้าทดสอบ").count() >= 8
    assert IntakeItem.objects.filter(status="GLOBAL_UNASSIGNED").count() == 2
    committed = ImportBatch.objects.get(file_name="demo-committed.xlsx")
    waiting = ImportBatch.objects.get(file_name="demo-conflict.xlsx")
    assert committed.status == "COMMITTED" and waiting.status == "AWAITING_RESOLUTION"
    assert waiting.conflicts.filter(kind="IDENTITY", status="OPEN").exists()
    assert MigrationDecision.objects.filter(status="OPEN").count() == 5
    assert Lead.objects.filter(lifecycle_status="STOPPED").exists()


@override_settings(DEBUG=True)
def test_running_it_twice_changes_nothing_the_second_time():
    call_command("seed_leadui_demo")
    first = snapshot()
    call_command("seed_leadui_demo")
    assert snapshot() == first


@override_settings(DEBUG=True)
def test_clean_removes_everything_it_made_and_leaves_other_data_alone():
    other = Customer.objects.create(phone_key="0600000000", phone1="0600000000", customer_name="คนจริง")
    call_command("seed_leadui_demo")
    call_command("seed_leadui_demo", "--clean")
    assert snapshot() == (1, 0, 0, 0, 0, 0)
    assert Customer.objects.filter(pk=other.pk).exists()
