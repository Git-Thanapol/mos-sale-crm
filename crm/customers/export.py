"""Customer/order XLSX export — ported from
crm_streamlit/pages/customers.py build_customer_export_xlsx /
customer_export_row. One row per order line (legacy was one row per
order-shaped source record; our normalized schema's closest equivalent is
one row per OrderLine). Columns that have no normalized column
(ช่องทางขาย, วิธีการชำระ, ตำบล, พนักงานเปิดบิล, พนักงานอัพเซลล์) fall back
to the source StagingImportRow.raw_data — the exact reason that table was
kept alongside the normalized core (see docs/DECISIONS.md / plan schema
section).
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

CRM_EXPORT_HEADERS = [
    "วันที่สั่งซื้อ", "เลขคำสั่งซื้อ", "ช่องทางขาย", "SKU", "สินค้า", "จำนวน", "ราคา",
    "วิธีการชำระ", "ขนส่ง", "หมายเลขพัสดุ", "URL", "ชื่อลูกค้า", "เบอร์โทร", "เบอร์สำรอง",
    "ที่อยู่จัดส่ง", "ตำบล", "อำเภอ", "จังหวัด", "รหัสไปรษณีย์",
    "พนักงานเปิดบิล", "พนักงานอัพเซลล์", "พนักงานดูแล",
]


def _raw(order, key: str) -> str:
    row = order.source_import_row
    if not row or not isinstance(row.raw_data, dict):
        return ""
    return str(row.raw_data.get(key) or "")


def export_row(order, line) -> list:
    customer = order.customer
    return [
        order.order_date.isoformat() if order.order_date else "",
        order.order_no,
        _raw(order, "ช่องทางขาย"),
        line.sku,
        line.product_name,
        line.quantity,
        str(line.amount) if line.amount is not None else "",
        _raw(order, "วิธีการชำระ"),
        order.carrier,
        order.tracking_no,
        customer.url,
        customer.customer_name,
        customer.phone1,
        customer.phone2,
        customer.address,
        _raw(order, "ตำบล"),
        customer.city,
        customer.province,
        customer.postal_code,
        _raw(order, "พนักงานเปิดบิล"),
        _raw(order, "พนักงานอัพเซลล์"),
        customer.owner_display,
    ]


def build_export_workbook(customers_qs) -> bytes:
    """customers_qs: a Customer queryset (already filtered/scoped by the
    caller — see crm.customers.selectors.customer_page's filter logic,
    which this reuses via the same CustomerFilters).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "customers"
    ws.append(CRM_EXPORT_HEADERS)

    for customer in customers_qs.prefetch_related("orders__lines", "orders__source_import_row"):
        for order in customer.orders.all():
            lines = list(order.lines.all())
            if not lines:
                continue
            for line in lines:
                ws.append(export_row(order, line))

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
