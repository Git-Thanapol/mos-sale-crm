"""Upsert logic for the two online-order exports. Two files can arrive in
either order, either can be re-uploaded later, and the result must
converge regardless — see feedback/Update_import_file/issue.md and the
merge-rule note below.

Batched (in_bulk / bulk_create / bulk_update), not per-row: the real
sample files are 13,878 and 14,243 orders (21,385 lines). A first,
straightforward per-row-save version took 284s on the smaller file alone
against RQ's 600s job timeout (config/settings/base.py RQ_QUEUES
DEFAULT_TIMEOUT) — too close to the edge, and the larger detail file's
per-order delete+bulk_create of lines would have been worse. This version
does a handful of batched round trips instead of one per row.

Never writes to crm.orders.Order/OrderLine, crm.customers rollups
(last_order_date/last_order/order_count — those come only from Order), or
crm.imports.StagingImportRow. This is a separate channel.
"""

from __future__ import annotations

from itertools import groupby

from django.db import transaction
from django.utils import timezone

from crm.core.identity import phone_key
from crm.customers.models import Customer
from crm.online_orders.importers import iter_detail_rows, iter_main_records
from crm.online_orders.models import OnlineOrder, OnlineOrderLine

BATCH_SIZE = 1000

EMPTY_MAIN_COUNTS = {"orders_created": 0, "orders_updated": 0, "customers_created": 0, "customers_linked": 0}
EMPTY_DETAIL_COUNTS = {"orders_created": 0, "orders_updated": 0, "lines_created": 0}

# Fields both files can supply. AllLiteDetailOrder (detail) is the richer
# order-detail export (5 distinct carriers vs. InoutManageMain's 2; totals
# agree on 13,875/13,878 sampled rows) so it is authoritative for these —
# it always overwrites. InoutManageMain (main) only fills them in when
# still blank, so uploading either file first converges to the same row.
SHARED_FIELDS = (
    "carrier", "tracking_no", "shop_name", "internal_order_no",
    "recipient_name", "province", "district", "subdistrict", "postal_code", "total_amount",
)
# Exclusive to InoutManageMain — the only file with a phone/address.
MAIN_ONLY_FIELDS = ("phone", "address", "customer_name")
# Exclusive to AllLiteDetailOrder.
DETAIL_ONLY_FIELDS = ("order_status", "ordered_at", "sales_staff", "payment_method")

MAIN_UPDATE_FIELDS = [*MAIN_ONLY_FIELDS, *SHARED_FIELDS, "shipping_imported_at", "import_batch_id"]
DETAIL_UPDATE_FIELDS = [*DETAIL_ONLY_FIELDS, *SHARED_FIELDS, "detail_imported_at", "import_batch_id"]


def _is_blank(value) -> bool:
    return value is None or value == ""


def _apply_owned(order: OnlineOrder, record: dict, fields) -> None:
    """Overwrite whenever the incoming value is non-blank."""
    for field in fields:
        value = record.get(field)
        if not _is_blank(value):
            setattr(order, field, value)


def _apply_fill_if_blank(order: OnlineOrder, record: dict, fields) -> None:
    """Write only when the incoming value is non-blank AND the stored
    value is still blank — so the non-authoritative file never clobbers
    a value the authoritative file already set.
    """
    for field in fields:
        value = record.get(field)
        if _is_blank(value):
            continue
        if _is_blank(getattr(order, field)):
            setattr(order, field, value)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


@transaction.atomic
def import_online_main(data_rows, batch_id, uploaded_by: str) -> dict:
    """InoutManageMain*.xlsx — one row per order, the shipping/phone side."""
    now = timezone.now()
    counts = dict(EMPTY_MAIN_COUNTS)

    records = list(iter_main_records(data_rows))
    if not records:
        return {"batch_id": batch_id, "format": "online_main", **counts}

    # Last row wins if the file has a duplicate online_order_no.
    by_key: dict[str, dict] = {}
    for record, _row_number in records:
        by_key[record["online_order_no"]] = record

    existing = OnlineOrder.objects.in_bulk(list(by_key.keys()), field_name="online_order_no")

    to_create: list[OnlineOrder] = []
    to_update: list[OnlineOrder] = []
    for online_order_no, record in by_key.items():
        order = existing.get(online_order_no)
        created = order is None
        if created:
            order = OnlineOrder(online_order_no=online_order_no)

        _apply_owned(order, record, MAIN_ONLY_FIELDS)
        _apply_fill_if_blank(order, record, SHARED_FIELDS)
        order.shipping_imported_at = now
        order.import_batch_id = batch_id

        (to_create if created else to_update).append(order)

    OnlineOrder.objects.bulk_create(to_create, batch_size=BATCH_SIZE)
    if to_update:
        OnlineOrder.objects.bulk_update(to_update, MAIN_UPDATE_FIELDS, batch_size=BATCH_SIZE)

    counts["orders_created"] = len(to_create)
    counts["orders_updated"] = len(to_update)

    customer_counts = _link_customers(to_create + to_update, by_key)
    counts["customers_created"] = customer_counts["created"]
    counts["customers_linked"] = customer_counts["linked"]

    return {"batch_id": batch_id, "format": "online_main", **counts}


def _link_customers(orders: list[OnlineOrder], records_by_key: dict[str, dict]) -> dict:
    """Batched version of: for each order's phone, find-or-create a
    Customer by phone_key and point the order at it. Only called from the
    main pass — the only file with a phone. A blank phone is left
    unlinked rather than minting a synthetic phone_key(row:<id>) customer
    (crm.core.identity.phone_key's fallback for two blank phones), since
    there is nothing identity-bearing about an online order with no phone.
    """
    counts = {"created": 0, "linked": 0}

    # order.id -> phone_key, and one representative record per key (for
    # the Customer defaults if it needs creating). Single pass, O(n).
    phone_by_order: dict[int, str] = {}
    record_by_phone_key: dict[str, dict] = {}
    for order in orders:
        record = records_by_key[order.online_order_no]
        phone = record["phone"]
        if not phone:
            continue
        key = phone_key(phone, "", None)
        phone_by_order[order.id] = key
        record_by_phone_key.setdefault(key, record)

    if not phone_by_order:
        return counts

    keys = sorted(record_by_phone_key.keys())
    existing_customers = Customer.objects.in_bulk(keys, field_name="phone_key")

    new_customers = [
        Customer(
            phone_key=key,
            phone1=record["phone"],
            customer_name=record.get("customer_name") or record.get("recipient_name") or "",
            province=record.get("province", ""),
            city=record.get("district", ""),
            subdistrict=record.get("subdistrict", ""),
            postal_code=record.get("postal_code", ""),
            address=record.get("address", ""),
        )
        for key, record in record_by_phone_key.items()
        if key not in existing_customers
    ]
    if new_customers:
        Customer.objects.bulk_create(new_customers, batch_size=BATCH_SIZE)
        for customer in new_customers:
            existing_customers[customer.phone_key] = customer
        counts["created"] = len(new_customers)

    new_customer_keys = {c.phone_key for c in new_customers}
    to_relink = []
    for order in orders:
        key = phone_by_order.get(order.id)
        if key is None:
            continue
        customer = existing_customers[key]
        if order.customer_id != customer.id:
            order.customer_id = customer.id
            to_relink.append(order)
            if key not in new_customer_keys:
                counts["linked"] += 1

    if to_relink:
        OnlineOrder.objects.bulk_update(to_relink, ["customer"], batch_size=BATCH_SIZE)

    return counts


@transaction.atomic
def import_online_detail(data_rows, batch_id, uploaded_by: str) -> dict:
    """AllLiteDetailOrder*.xlsx — one row per order LINE. Lines are
    replaced wholesale per order on every import (one batched delete +
    one batched bulk_create across the whole file, not per order), so
    re-uploading a newer export correctly drops removed lines and adds
    new ones (line_no is a pure function of the file's row order).
    """
    now = timezone.now()
    counts = dict(EMPTY_DETAIL_COUNTS)

    rows = list(iter_detail_rows(data_rows))
    if not rows:
        return {"batch_id": batch_id, "format": "online_detail", **counts}
    rows.sort(key=lambda pair: pair[0]["online_order_no"])

    groups: dict[str, list[dict]] = {}
    for online_order_no, group in groupby(rows, key=lambda pair: pair[0]["online_order_no"]):
        groups[online_order_no] = [record for record, _row_number in group]

    existing = OnlineOrder.objects.in_bulk(list(groups.keys()), field_name="online_order_no")

    to_create: list[OnlineOrder] = []
    to_update: list[OnlineOrder] = []
    for online_order_no, group_records in groups.items():
        header = group_records[0]
        order = existing.get(online_order_no)
        created = order is None
        if created:
            order = OnlineOrder(online_order_no=online_order_no)

        _apply_owned(order, header, DETAIL_ONLY_FIELDS)
        _apply_owned(order, header, SHARED_FIELDS)
        order.detail_imported_at = now
        order.import_batch_id = batch_id

        (to_create if created else to_update).append(order)

    OnlineOrder.objects.bulk_create(to_create, batch_size=BATCH_SIZE)
    if to_update:
        OnlineOrder.objects.bulk_update(to_update, DETAIL_UPDATE_FIELDS, batch_size=BATCH_SIZE)

    counts["orders_created"] = len(to_create)
    counts["orders_updated"] = len(to_update)

    all_orders = to_create + to_update
    all_order_ids = [o.id for o in all_orders]
    order_id_by_no = {o.online_order_no: o.id for o in all_orders}

    for chunk in _chunks(all_order_ids, BATCH_SIZE):
        OnlineOrderLine.objects.filter(order_id__in=chunk).delete()

    lines = []
    for online_order_no, group_records in groups.items():
        order_id = order_id_by_no[online_order_no]
        for i, rec in enumerate(group_records, start=1):
            lines.append(
                OnlineOrderLine(
                    order_id=order_id,
                    line_no=i,
                    sku=rec["sku"],
                    product_name=rec["product_name"],
                    quantity=rec["quantity"] or 0,
                    upsell_staff=rec["upsell_staff"],
                    upsell_amount=rec["upsell_amount"],
                )
            )
    OnlineOrderLine.objects.bulk_create(lines, batch_size=BATCH_SIZE)
    counts["lines_created"] = len(lines)

    return {"batch_id": batch_id, "format": "online_detail", **counts}
