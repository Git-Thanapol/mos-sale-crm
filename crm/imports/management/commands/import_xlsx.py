"""Minimum-viable Excel importer (Phase 2), now delegating to
crm.imports.services (Phase 4 extracted the row-processing logic so the
CLI and the /orders/import web view share one implementation instead of
drifting like the legacy ui/manual_order_ui.py / pages/followup.py
near-duplicates did).

Expected header row (first row of the first sheet, exact Thai labels match
the legacy import template so docs/parity/thai_strings.json stays the
source of truth for what a real workbook looks like):

    เลขคำสั่งซื้อ | ชื่อลูกค้า | เบอร์โทร | เบอร์สำรอง | SKU | ชื่อสินค้า |
    จำนวน | ราคา | วันที่สั่งซื้อ | ประเภทการขาย | จังหวัด | อำเภอ |
    รหัสไปรษณีย์ | ที่อยู่ | ผู้ดูแล | รหัสพนักงาน | เลขพัสดุ | ขนส่ง | สถานะ

Required: ชื่อลูกค้า, and at least one of เบอร์โทร/เบอร์สำรอง.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from crm.imports.services import WorkbookFormatError, import_workbook


class Command(BaseCommand):
    help = "Import a fixed-template Excel workbook into staging + the normalized core."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the .xlsx file")
        parser.add_argument("--uploaded-by", default="", help="Email of the importing user")

    def handle(self, *args, **options):
        try:
            with open(options["path"], "rb") as fh:
                result = import_workbook(fh, options["uploaded_by"])
        except WorkbookFormatError as exc:
            raise CommandError(str(exc)) from exc
        except OSError as exc:
            raise CommandError(f"cannot open {options['path']}: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(
            f"batch {result['batch_id']}: {result['valid']} valid rows, {result['invalid']} invalid, "
            f"{result['customers_created']} customers created, {result['orders_created']} orders created, "
            f"{result['lines_created']} lines created"
        ))
