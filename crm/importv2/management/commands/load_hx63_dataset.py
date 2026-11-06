"""Load the HX63 provisional dataset (canonical.jsonl) as orders.

    python manage.py load_hx63_dataset --dir v2-filtered-provisional-20261104 --dry-run
    python manage.py load_hx63_dataset --dir v2-filtered-provisional-20261104
    python manage.py load_hx63_dataset --dir v2-filtered-provisional-20261104 --rollback

Rules (from the dataset hand-over):
 - the manifest (status, counts, SHA-256 of every file) is verified first; any
   mismatch aborts before anything is written;
 - customers are NEVER merged by name or phone: every order gets its own customer
   whose phone_key is split as `<key>#<order id>` (crm_customer.phone_key is
   unique, and the importv2 engine already uses the same split);
 - no leads, intake items or follow-up cycles are created, and no staff is
   guessed: the caretaker name is kept in order.import_fields only;
 - an order number already present in the database is skipped and reported,
   never overwritten.

Idempotent: re-running skips orders this batch already wrote. The batch id is
derived from the SHA-256 of canonical.jsonl, so --rollback removes exactly it.

The file is read in two streaming passes (validate, then write CHUNK at a time):
holding all ~425k plans in memory gets this process OOM-killed on the 4 GB
production host.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crm.core.identity import clean, phone_key
from crm.customers.models import Customer
from crm.importv2.services.adapters import ms_to_bangkok_date
from crm.orders.models import Order, OrderLine

SCHEMA = "hx63-canonical-v1"
UPLOADED_BY = "hx63-dataset"
CHUNK = 2000
MAX_SKU = 64
MAX_PRODUCT = 255
MAX_NAME = 255
MAX_PHONE = 32


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def verify_manifest(directory: Path) -> dict:
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise CommandError(f"{manifest_path} not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE":
        raise CommandError(f"manifest status is {manifest.get('status')!r}, not COMPLETE")
    if manifest.get("schemaVersion") != SCHEMA:
        raise CommandError(f"manifest schemaVersion {manifest.get('schemaVersion')!r} != {SCHEMA!r}")
    entries = dict(manifest["files"])
    filt = manifest.get("filter", {})
    for key in ("reviewFile", "historyFile"):
        if filt.get(key):
            entries[filt[key]["name"]] = filt[key]
    entries["data-quality.json"] = {"sha256": manifest.get("qualitySha256")}
    for name, meta in entries.items():
        path = directory / name
        if not path.exists():
            raise CommandError(f"{name}: missing")
        actual = _sha256(path)
        if actual != meta["sha256"]:
            raise CommandError(f"{name}: SHA-256 mismatch (manifest {meta['sha256']}, actual {actual})")
        if "count" in meta and name.endswith(".jsonl"):
            lines = _count_lines(path)
            if lines != meta["count"]:
                raise CommandError(f"{name}: {lines} lines, manifest says {meta['count']}")
    return manifest


def _lines(items: list[dict]) -> list[dict]:
    """Merge items sharing (sku, product): crm_order_line is unique on (order, sku, product_name)."""
    merged: dict[tuple[str, str], dict] = {}
    for item in items or []:
        sku = clean(item.get("raw"))
        product = clean(item.get("product"))
        key = (sku, product)
        qty = int(item.get("qty") or 0)
        amount = Decimal(str(item.get("amt") or 0))
        if key in merged:
            merged[key]["quantity"] += qty
            merged[key]["amount"] += amount
        else:
            merged[key] = {"sku": sku, "product_name": product, "quantity": qty, "amount": amount}
    return list(merged.values())


def to_plan(record: dict) -> tuple[dict | None, list[str]]:
    """Return (plan, problems). Plan holds what to write for one order."""
    problems: list[str] = []
    order = record.get("order") or {}
    order_id = clean(order.get("orderId"))
    candidate = record.get("customerCandidate") or {}
    phone = clean(candidate.get("phone"))
    if not order_id:
        problems.append("blank order id")
    if not phone:
        problems.append("blank phone")
    if len(phone) > MAX_PHONE:
        problems.append("phone too long")
    if len(clean(candidate.get("name"))) > MAX_NAME:
        problems.append("name too long")
    lines = _lines(order.get("items"))
    for line in lines:
        if line["quantity"] < 1:
            problems.append("line quantity < 1")
        if len(line["sku"]) > MAX_SKU or len(line["product_name"]) > MAX_PRODUCT:
            problems.append("line text too long")
    if problems:
        return None, problems
    lead = record.get("lead") or {}
    return {
        "order_id": order_id,
        "phone": phone,
        "candidate": candidate,
        "date_ms": order.get("date"),
        "amount": order.get("amount"),
        "status": clean(order.get("status")),
        "caretaker": clean(lead.get("caretaker")),
        "lines": lines,
        "meta": {
            "hx63": {
                "rowId": record.get("rowId"),
                "snapshotKey": (record.get("source") or {}).get("snapshotKey"),
                "identityStatus": (record.get("identity") or {}).get("status"),
                "flags": record.get("flags") or [],
                "team": lead.get("team"),
                "caretaker": clean(lead.get("caretaker")),
                "provisional": True,
            }
        },
    }, []


def _iter_records(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            yield number, json.loads(line)


class Command(BaseCommand):
    help = "Load the HX63 provisional dataset as orders (one customer per order, never merged)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", required=True, help="dataset directory containing manifest.json")
        parser.add_argument("--dry-run", action="store_true", help="verify and validate, write nothing")
        parser.add_argument("--rollback", action="store_true", help="delete everything this dataset wrote")

    def handle(self, *args, **options):
        directory = Path(options["dir"])
        manifest = verify_manifest(directory)
        canonical = directory / "canonical.jsonl"
        expected = manifest["files"]["canonical.jsonl"]["count"]
        batch_id = uuid.uuid5(uuid.NAMESPACE_URL, "hx63:" + manifest["files"]["canonical.jsonl"]["sha256"])
        self.stdout.write(f"manifest OK: schema {SCHEMA}, {expected} orders, batch {batch_id}")

        if options["rollback"]:
            return self._rollback(batch_id)

        seen, bad = set(), defaultdict(list)
        for number, record in _iter_records(canonical):
            plan, problems = to_plan(record)
            if plan and plan["order_id"] in seen:
                plan, problems = None, ["duplicate order id"]
            if plan:
                seen.add(plan["order_id"])
            else:
                for problem in problems:
                    bad[problem].append(number)
        self.stdout.write(f"validated: {len(seen)} ok, {sum(len(v) for v in bad.values())} problems")
        for problem, rows in bad.items():
            self.stdout.write(self.style.ERROR(f"  {problem}: {len(rows)} (first lines {rows[:5]})"))
        if bad:
            raise CommandError("validation failed; nothing written")
        if len(seen) != expected:
            raise CommandError(f"{len(seen)} valid orders != manifest {expected}")

        done = set(Order.objects.filter(import_batch_id=batch_id).values_list("order_no", flat=True))
        taken = set()
        order_ids = list(seen)
        for start in range(0, len(order_ids), 10000):
            part = order_ids[start : start + 10000]
            taken.update(Order.objects.exclude(import_batch_id=batch_id).filter(order_no__in=part).values_list("order_no", flat=True))
        total = len(seen - done - taken)
        self.stdout.write(
            f"already loaded by this batch: {len(done)}; present from other sources (skipped): {len(taken)}; to write: {total}"
        )
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("dry run: nothing written"))
            return

        chunk, written = [], 0
        for _number, record in _iter_records(canonical):
            plan, _problems = to_plan(record)
            if plan["order_id"] in done or plan["order_id"] in taken:
                continue
            chunk.append(plan)
            if len(chunk) >= CHUNK:
                self._write_chunk(chunk, batch_id)
                written += len(chunk)
                chunk = []
                self.stdout.write(f"  wrote {written}/{total}")
        if chunk:
            self._write_chunk(chunk, batch_id)
            written += len(chunk)
            self.stdout.write(f"  wrote {written}/{total}")

        loaded = Order.objects.filter(import_batch_id=batch_id).count()
        self.stdout.write(f"orders with batch {batch_id}: {loaded} (expected {expected - len(taken)}, manifest {expected})")
        if loaded + len(taken) != expected:
            raise CommandError("post-import count does not reconcile")
        self.stdout.write(self.style.SUCCESS("import reconciled"))

    @transaction.atomic
    def _write_chunk(self, chunk: list[dict], batch_id) -> None:
        customers = []
        for p in chunk:
            c = p["candidate"]
            customers.append(
                Customer(
                    phone_key=f"{phone_key(p['phone'], '', p['order_id'])}#{p['order_id']}",
                    phone1=p["phone"],
                    customer_name=clean(c.get("name")),
                    address=clean(c.get("address")),
                    subdistrict=clean(c.get("subdistrict")),
                    city=clean(c.get("district")),
                    province=clean(c.get("province")),
                    postal_code=clean(c.get("postal")),
                    owner_display=p["caretaker"],
                )
            )
        Customer.objects.bulk_create(customers)
        orders = []
        for p, customer in zip(chunk, customers):
            amount = p["amount"]
            orders.append(
                Order(
                    customer=customer,
                    order_no=p["order_id"],
                    order_date=ms_to_bangkok_date(p["date_ms"]),
                    sale_type="NEW_ORDER",
                    order_status=p["status"],
                    total_amount=None if amount is None else Decimal(str(round(amount, 2))),
                    owner_display=p["caretaker"],
                    source_type="import",
                    uploaded_by=UPLOADED_BY,
                    import_batch_id=batch_id,
                    import_fields=p["meta"],
                )
            )
        Order.objects.bulk_create(orders)
        OrderLine.objects.bulk_create(
            [OrderLine(order=o, **line) for p, o in zip(chunk, orders) for line in p["lines"]]
        )
        for customer, order in zip(customers, orders):
            customer.last_order = order
            customer.last_order_date = order.order_date
            customer.order_count = 1
        Customer.objects.bulk_update(customers, ["last_order", "last_order_date", "order_count"])

    @transaction.atomic
    def _rollback(self, batch_id) -> None:
        customer_ids = list(Order.objects.filter(import_batch_id=batch_id).values_list("customer_id", flat=True))
        # Orders cascade from their customer; each customer here owns only its one order.
        extra = Order.objects.filter(customer_id__in=customer_ids).exclude(import_batch_id=batch_id).count()
        if extra:
            raise CommandError(f"{extra} orders from other sources hang on these customers; refusing to delete")
        Customer.objects.filter(pk__in=customer_ids).update(last_order=None)
        deleted, _ = Customer.objects.filter(pk__in=customer_ids).delete()
        self.stdout.write(self.style.SUCCESS(f"rolled back: {deleted} rows removed"))
