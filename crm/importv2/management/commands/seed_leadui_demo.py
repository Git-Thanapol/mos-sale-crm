"""Demo data for the CRM V2 pages, for looking at them and comparing screenshots.

    python manage.py seed_leadui_demo           # create (idempotent)
    python manage.py seed_leadui_demo --clean   # remove everything it made

Refuses to run unless DEBUG is on. Everything it creates is recognisable and
removable: customers are named "ลูกค้าทดสอบ ...", users are demo-*@example.com
with no usable password, import files are demo-*.xlsx and decision subjects are
demo-*. Import batches go through the real services (upload, preview, confirm,
commit), so the history and conflict screens show genuine records.
"""

from __future__ import annotations

import io
from datetime import timedelta

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.customers.models import Customer
from crm.domain.migration_decisions import decision_id_for, evidence_hash_of
from crm.followcycles.services import add_note, log_contact_attempt, open_cycle, set_appointment
from crm.importv2.models import ImportBatch, MigrationDecision
from crm.importv2.services.commit import commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview
from crm.importv2.services.upload import create_batch, get_profile
from crm.intake.services import upsert_intake
from crm.leads.models import Lead
from crm.leads.services import stop_lead
from crm.leads.sync import sync_leads_for_order
from crm.orders.models import Order, OrderLine
from crm.teams.models import Team

NAME_PREFIX = "ลูกค้าทดสอบ"
EMAIL_SUFFIX = "@example.com"


def _workbook(rows: list[list]) -> bytes:
    profile = get_profile("crm-customers")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(profile["headerSignature"])
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _row(order, name, phone, team="CRM Team", seller="", price="1,000"):
    return [
        "26/10/2569", order, "LINE", "SKU-DEMO", "สินค้าตัวอย่าง", 1, price, "COD", "Kerry", "TH0001", "",
        name, phone, "", "1 ถนนตัวอย่าง", "ในเมือง", "เมือง", "กรุงเทพมหานคร", "10100", seller, "", "", team,
        "สูง", "", "",
    ]


class Command(BaseCommand):
    help = "Create (or --clean) demo data for the CRM V2 pages. DEBUG only."

    def add_arguments(self, parser):
        parser.add_argument("--clean", action="store_true", help="remove the demo data instead")

    def handle(self, *args, clean=False, **options):
        if not settings.DEBUG:
            raise CommandError("refusing to touch demo data when DEBUG is false")
        if clean:
            self._clean()
            self.stdout.write("demo data removed")
            return
        self._seed()
        self.stdout.write("demo data ready")

    # -- clean --------------------------------------------------------------------------

    @transaction.atomic
    def _clean(self):
        ImportBatch.objects.filter(file_name__startswith="demo-").delete()
        MigrationDecision.objects.filter(subject_key__startswith="demo-").delete()
        customers = Customer.objects.filter(customer_name__startswith=NAME_PREFIX)
        Lead.objects.filter(customer__in=customers).delete()
        Order.objects.filter(customer__in=customers).delete()
        customers.delete()
        User.objects.filter(email__startswith="demo-", email__endswith=EMAIL_SUFFIX).delete()

    # -- seed ---------------------------------------------------------------------------

    def _user(self, email, role, code, name):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"role": role, "staff_code": code, "staff_name": name, "must_change_password": False},
        )
        if created:
            user.set_unusable_password()
            user.save()
        return user

    @transaction.atomic
    def _seed(self):
        crm = Team.objects.filter(code="CRM_TEAM").first() or Team.objects.create(
            code="CRM_TEAM", name="CRM_TEAM", display_name="CRM Team", policy={"ownershipPolicy": "EXCLUSIVE"}
        )
        upsell = Team.objects.filter(code="UPSELL_TEAM").first() or Team.objects.create(
            code="UPSELL_TEAM", name="UPSELL_TEAM", display_name="Upsell Team",
            policy={"ownershipPolicy": "LATEST_ORDER"},
        )
        editor = self._user("demo-admin@example.com", "EDITOR", "DEMO-E", "แอดมิน (ทดสอบ)")
        crm_rep = self._user("demo-ts-crm@example.com", "พนักงาน", "DEMO-C1", "พนักงานขาย CRM 1 (ทดสอบ)")
        up_rep = self._user("demo-ts-up@example.com", "พนักงาน", "DEMO-U1", "พนักงานขาย Upsell 1 (ทดสอบ)")
        today = timezone.localdate()

        spec = [  # name, team, rep, days since last order, lifecycle, appointment offset (days)
            ("003", crm, crm_rep, 22, "FOLLOWING", -3),
            ("007", crm, crm_rep, 18, "STOPPED", -3),
            ("011", crm, None, 14, "FOLLOWING", -3),
            ("015", crm, crm_rep, 10, "FOLLOWING", -3),
            ("002", upsell, up_rep, 23, "FOLLOWING", -3),
            ("004", upsell, up_rep, 21, "NEW", None),
            ("006", upsell, None, 19, "FOLLOWING", 0),
            ("001", crm, crm_rep, 24, "NEW", 3),
        ]
        for number, team, rep, age, lifecycle, appointment in spec:
            name = f"{NAME_PREFIX} {number}"
            customer, created = Customer.objects.get_or_create(
                phone_key=f"0881000{number}",
                defaults={
                    "customer_name": name, "phone1": f"0881000{number}", "staff_code": rep.staff_code if rep else "",
                    "owner_display": rep.staff_name if rep else "",
                },
            )
            if not created:
                continue
            for index in range(2):
                order = Order.objects.create(
                    customer=customer, order_no=f"DEMO-{number}-{index}", order_date=today - timedelta(days=age + index * 9),
                    sale_type="NEW_ORDER", total_amount=714 + index, staff_code=rep.staff_code if rep else "",
                    owner_display=rep.staff_name if rep else "", source_type="manual",
                )
                OrderLine.objects.create(order=order, sku="SKU-DEMO", product_name="สินค้าตัวอย่าง", quantity=1, amount=714 + index)
            lead = Lead.objects.create(
                team=team, customer=customer, lifecycle_status="NEW", routing_state="TEAM_UNASSIGNED",
                ownership_policy=team.ownership_policy,
            )
            for order in customer.orders.order_by("id"):
                sync_leads_for_order(actor=editor, order=order, open_new_cycle=False)
            lead.refresh_from_db()
            if lifecycle != "NEW":
                lead.lifecycle_status = lifecycle if lifecycle != "STOPPED" else "FOLLOWING"
                lead.save(update_fields=["lifecycle_status", "updated_at"])
            if appointment is not None:
                open_cycle(actor=editor, lead=lead)
                set_appointment(actor=editor, lead=lead, scheduled_at=timezone.now() + timedelta(days=appointment))
                add_note(actor=editor, lead=lead, body="โน้ตทดสอบ")
                log_contact_attempt(actor=editor, lead=lead, result="NO_ANSWER", note="ไม่รับสาย (ข้อมูลทดสอบ)")
            if lifecycle == "STOPPED":
                stop_lead(actor=editor, lead=lead, reason="MANUAL_STOP")

        for number in ("901", "902"):
            customer, _ = Customer.objects.get_or_create(
                phone_key=f"0882000{number}",
                defaults={"customer_name": f"{NAME_PREFIX} {number}", "phone1": f"0882000{number}"},
            )
            upsert_intake(customer=customer, source_order_id=f"DEMO-{number}", source_kind="CRM_CUSTOMERS", source_ref="fixture:demo")

        self._imports(editor)
        self._decisions()

    def _imports(self, editor):
        if ImportBatch.objects.filter(file_name__startswith="demo-").exists():
            return
        committed = create_batch(
            actor=editor, file_name="demo-committed.xlsx",
            raw=_workbook([_row("DEMO-IMP-1", f"{NAME_PREFIX} นำเข้า 1", "0883000001"), _row("DEMO-IMP-2", f"{NAME_PREFIX} นำเข้า 2", "0883000002")]),
        )
        build_preview(actor=editor, batch=committed)
        committed.refresh_from_db()
        confirm_batch(actor=editor, batch=committed, preview_hash=committed.preview_hash, operation_key=f"demo-c-{committed.pk}")
        commit_batch(actor=editor, batch=committed, operation_key=f"demo-m-{committed.pk}")
        # The same phone as a demo customer under a different name: a blocking identity conflict.
        waiting = create_batch(
            actor=editor, file_name="demo-conflict.xlsx",
            raw=_workbook([_row("DEMO-IMP-3", f"{NAME_PREFIX} ชื่ออื่นโดยสิ้นเชิง", "0881000003"), _row("DEMO-IMP-4", f"{NAME_PREFIX} นำเข้า 4", "0883000004")]),
        )
        build_preview(actor=editor, batch=waiting)

    def _decisions(self):
        staff = {
            "kind": "STAFF_ALIAS", "historicalDisplayValue": "พนักงานเดิมทดสอบ 5", "normalizedValue": "พนักงานเดิมทดสอบ 5",
            "frequency": 200, "sourceContext": {"crm": 150, "upsell": 50}, "exactCandidates": [],
        }
        identity = {
            "kind": "IDENTITY_REVIEW", "normalizedPhone": "0812345678",
            "nameVariants": [{"name": "สมชาย", "normalizedName": "สมชาย", "orderCount": 3, "firstOrderAt": 1_728_000_000_000, "lastOrderAt": 1_729_000_000_000}],
            "nameVariantTotal": 1, "similarities": [], "sourceOrderCount": 3, "sampleOrderIds": ["SO-1"],
            "candidates": [{"customerId": "1", "displayName": "ทดสอบ", "similarity": 0.9}],
        }
        status = {
            "kind": "UNKNOWN_STATUS", "rawValue": "สถานะทดสอบ", "count": 3, "sourceKind": "HX63_HISTORICAL", "sampleOrderIds": ["SO-1"],
            "fallback": {"orderStatus": "PENDING_CONFIRMATION", "paymentStatus": "UNPAID", "shippingStatus": "NOT_SHIPPED"},
        }
        dated = {"kind": "NULL_DATE_ORDER", "sourceOrderId": "DEMO-9", "sourceKind": "HX63_HISTORICAL", "hasPhone": True, "itemCount": 2, "total": 100, "rawStatus": "สำเร็จ"}
        items = [
            ("STAFF_ALIAS", "NO_EXACT_CANDIDATE", "demo-staff-1", staff),
            ("STAFF_ALIAS", "NO_EXACT_CANDIDATE", "demo-staff-2", {**staff, "historicalDisplayValue": "พนักงานเดิมทดสอบ 6"}),
            ("IDENTITY_REVIEW", "NAME_CONFLICT", "demo-identity-1", identity),
            ("UNKNOWN_STATUS", "UNRECOGNISED_VALUE", "demo-status-1", status),
            ("NULL_DATE_ORDER", "SOURCE_DATE_MISSING", "demo-date-1", dated),
        ]
        for category, reason, subject, evidence in items:
            MigrationDecision.objects.get_or_create(
                decision_id=decision_id_for(category, "demo-snapshot", subject),
                defaults={
                    "category": category, "reason_code": reason, "subject_key": subject,
                    "source_snapshot_id": "demo-snapshot", "evidence": evidence,
                    "evidence_hash": evidence_hash_of(category, subject, evidence),
                },
            )
