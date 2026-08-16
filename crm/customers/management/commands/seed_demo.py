"""Synthetic Thai-ish demo data — for eyeballing Phase 3 screens and driving
tests/perf/. NOT a substitute for importing the customer's real workbook
(that's manage.py import_xlsx); this data has no relationship to any real
customer.
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.core.identity import phone_key
from crm.core.thai import FOLLOWUP_PRIORITY_OPTIONS
from crm.customers.models import Customer
from crm.followups.models import Followup
from crm.orders.models import Order, OrderLine

FIRST_NAMES = ["สมชาย", "สมหญิง", "วิชัย", "มาลี", "ประยุทธ์", "กัญญา", "อนุชา", "พรทิพย์", "ธนกร", "สุนีย์"]
LAST_NAMES = ["ใจดี", "รักไทย", "สายฝน", "แสงทอง", "ทองคำ", "บุญมี", "ศรีสุข", "วงศ์ทอง"]
PROVINCES = ["กรุงเทพมหานคร", "เชียงใหม่", "ขอนแก่น", "ชลบุรี", "นครราชสีมา"]
STAFF_CODES = ["S0001", "S0002", "S0003", ""]  # blank = unassigned cohort
SALE_TYPES = ["NEW_ORDER", "UPSELL", "FOLLOW"]
SKUS = [("SP001", "ครีมบำรุงผิว"), ("SP002", "วิตามินซี"), ("SP003", "แชมพูสมุนไพร"), ("SP004", "น้ำมันนวด")]


class Command(BaseCommand):
    help = "Seed synthetic demo data for screen review and the perf test suite."

    def add_arguments(self, parser):
        parser.add_argument("--customers", type=int, default=200)

    def handle(self, *args, **options):
        n = options["customers"]
        rng = random.Random(42)
        today = timezone.localdate()

        created_customers = 0
        for i in range(n):
            phone1 = f"08{rng.randint(10000000, 99999999)}"
            has_phone2 = rng.random() < 0.3
            phone2 = f"09{rng.randint(10000000, 99999999)}" if has_phone2 else ""
            key = phone_key(phone1, phone2, i)
            if Customer.objects.filter(phone_key=key).exists():
                continue

            with transaction.atomic():
                staff_code = rng.choice(STAFF_CODES)
                customer = Customer.objects.create(
                    phone_key=key,
                    phone1=phone1,
                    phone2=phone2,
                    customer_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                    province=rng.choice(PROVINCES),
                    staff_code=staff_code,
                    # blank, not the literal placeholder string — "ยังไม่มอบหมาย" is a
                    # template display fallback (see templates' |default filter), not real data
                    owner_display="" if not staff_code else f"พนักงาน {staff_code}",
                )

                order_count = rng.randint(1, 4)
                last_date = None
                last_order = None
                for _ in range(order_count):
                    order_date = today - timedelta(days=rng.randint(0, 180))
                    order = Order.objects.create(
                        customer=customer,
                        order_no=f"ORD{rng.randint(100000, 999999)}",
                        order_date=order_date,
                        sale_type=rng.choice(SALE_TYPES),
                        staff_code=staff_code,
                        owner_display=customer.owner_display,
                        source_type="import",
                    )
                    sku, name = rng.choice(SKUS)
                    OrderLine.objects.create(
                        order=order, sku=sku, product_name=name,
                        quantity=rng.randint(1, 3), amount=rng.randint(200, 2000),
                    )
                    if last_date is None or order_date >= last_date:
                        last_date, last_order = order_date, order

                customer.last_order_date = last_date
                customer.last_order = last_order
                customer.order_count = order_count
                customer.save(update_fields=["last_order_date", "last_order", "order_count"])

                Followup.objects.create(
                    customer=customer,
                    priority=rng.choice(FOLLOWUP_PRIORITY_OPTIONS),
                    status=rng.choice(["none", "scheduled", "done"]),
                    staff_code=staff_code,
                    owner_display=customer.owner_display,
                    next_followup_date=today + timedelta(days=rng.randint(-5, 20)) if rng.random() < 0.6 else None,
                )
                created_customers += 1

        self.stdout.write(self.style.SUCCESS(f"seeded {created_customers} demo customer(s)"))
