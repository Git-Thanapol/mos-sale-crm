"""Thai display labels for the machine codes the import and lead screens show.

Display only. Stored values, form values, plan hashes and the parity-pinned
domain strings stay in English; the templates translate at the last moment.
An unknown code is shown as-is rather than hidden or guessed.
"""

from __future__ import annotations

LABELS: dict[str, str] = {
    # import batch status
    "PREVIEW": "รอ Preview",
    "AWAITING_RESOLUTION": "รอดำเนินการต่อ",
    "COMMITTING": "กำลังบันทึก",
    "COMMITTED": "นำเข้าสำเร็จ",
    "ROLLING_BACK": "กำลังย้อนกลับ",
    "ROLLED_BACK": "ย้อนกลับแล้ว",
    "CANCELLED": "ยกเลิกแล้ว",
    "FAILED": "ล้มเหลว",
    "ROLLBACK_FAILED": "ย้อนกลับไม่สำเร็จ",
    "FAILED_RETRYABLE": "ล้มเหลว (ลองใหม่ได้)",
    "PAUSED_QUOTA": "หยุดชั่วคราว (เกินโควตา)",
    # conflict kind / severity / status
    "OWNERSHIP": "ความเป็นเจ้าของ",
    "IDENTITY": "ตัวตนลูกค้า",
    "REACTIVATION": "เปิด Lead ใหม่",
    "MAPPING": "การแมปข้อมูล",
    "STALE": "ข้อมูลเก่ากว่า",
    "REVERSAL": "ย้อนสถานะ",
    "STOPPED_TARGET": "Lead ที่หยุดแล้ว",
    "BLOCKING": "ต้องแก้ก่อนยืนยัน",
    "WARNING": "คำเตือน",
    "OPEN": "รอแก้ไข",
    "RESOLVED": "แก้ไขแล้ว",
    "AUTO_RESOLVED": "แก้ไขอัตโนมัติ",
    # conflict resolution options
    "AUTO_MATCH": "ใช้ลูกค้าเดิม",
    "NEW_CUSTOMER": "สร้างลูกค้าใหม่",
    "KEEP_CURRENT": "คงผู้ดูแลเดิม",
    "REASSIGN": "เปลี่ยนผู้ดูแล",
    "REACTIVATE": "เปิด Lead ใหม่",
    "KEEP_STOPPED": "คงสถานะหยุด",
    "KEEP_EXISTING": "ใช้ข้อมูลเดิม",
    "ACCEPT_INCOMING": "ใช้ข้อมูลในไฟล์",
    "APPROVE_REVERSAL": "อนุมัติการย้อนสถานะ",
    # preview row classification
    "NEW_ORDER": "ออเดอร์ใหม่",
    "SAFE_UPDATE": "อัปเดตได้",
    "UNCHANGED": "ไม่เปลี่ยนแปลง",
    "STALE_INPUT": "ข้อมูลในไฟล์เก่ากว่า",
    "CONFLICT": "ข้อขัดแย้ง",
    "INVALID": "ข้อมูลไม่ถูกต้อง",
    # lead lifecycle / computed / intake
    "NEW": "ใหม่",
    "FOLLOWING": "กำลังติดตาม",
    "WAITING_CUSTOMER": "รอลูกค้า",
    "WAITING_FOLLOWUP": "รอติดตาม",
    "STOPPED": "หยุดติดตาม",
    "APPOINTMENT_TODAY": "นัดวันนี้",
    "APPOINTMENT_FUTURE": "นัดล่วงหน้า",
    "OVERDUE": "เลยกำหนด",
    "GLOBAL_UNASSIGNED": "ยังไม่มีผู้ดูแล",
    "CONVERTED": "แปลงเป็น Lead แล้ว",
    "ARCHIVED": "เก็บถาวร",
    "ASSIGNED": "มอบหมายแล้ว",
    "DONE": "เสร็จสิ้น",
    # order status (mapped machine codes)
    "PENDING_CONFIRMATION": "รอยืนยัน",
    "WAITING_SHIPMENT": "รอจัดส่ง",
    "SHIPPED": "จัดส่งแล้ว",
    "DELIVERED": "ส่งสำเร็จ",
    "COD_RETURNED": "COD ตีกลับ",
    "UNKNOWN": "ไม่ทราบสถานะ",
    "OWNERSHIP_LOCK": "ล็อกผู้ดูแล",
    "ACTIVE_FOLLOWUP": "มีรอบติดตาม",
    "ACTIVE_APPOINTMENT": "มีนัดหมาย",
    # source kinds
    "CRM_CUSTOMERS": "CRM Customers",
    "LITE_DETAIL_ORDER": "LiteDetailOrder",
    "INOUT_MANAGE_MAIN": "InoutManageMain",
    "TELE_SALES_REPORT": "Tele Sales Report",
    "CUTOVER_ORDER_SHEET": "Cutover Order Sheet",
    # validation issue codes
    "INVALID_ROWS": "มีแถวที่แมปไม่ผ่าน",
    "SOURCE_NOT_SUPPORTED": "ยังไม่รองรับไฟล์ประเภทนี้",
    "NO_CUSTOMER_SOURCE": "ไม่พบลูกค้าของออเดอร์",
    "HEADER_COUNT_MISMATCH": "จำนวนคอลัมน์ไม่ตรงกับรูปแบบไฟล์",
    "HEADER_SIGNATURE_MISMATCH": "ชื่อคอลัมน์ไม่ตรงกับรูปแบบไฟล์",
    "DUPLICATE_HEADER": "ชื่อคอลัมน์ซ้ำ",
    "UNMAPPED_COLUMN": "คอลัมน์ที่ไม่ได้ใช้",
    "SHAPE_MISMATCH": "ข้อมูลในคอลัมน์ไม่ตรงชนิด",
    "SHAPE_PARTIAL": "ข้อมูลบางแถวไม่ตรงชนิด",
    "SHIFTED_COLUMNS": "คอลัมน์อาจเลื่อนตำแหน่ง",
    "REQUIRED_FIELD_UNMAPPED": "ไม่มีคอลัมน์ที่จำเป็น",
    "MAPPING_INDEX_OUT_OF_RANGE": "ตำแหน่งคอลัมน์เกินขอบเขตไฟล์",
}

#: Field names that appear in merge conflict summaries ("channel: ...").
FIELD_LABELS: dict[str, str] = {
    "channel": "ช่องทางขาย",
    "carrier": "ขนส่ง",
    "trackingNumber": "เลขพัสดุ",
    "url": "URL",
    "phone": "เบอร์โทร",
    "total": "ยอดรวม",
    "items": "รายการสินค้า",
    "orderedAt": "วันที่สั่งซื้อ",
    "orderStatus": "สถานะออเดอร์",
    "status": "สถานะ",
    "customerId": "ลูกค้า",
    "closerUserId": "ผู้ปิดการขาย",
    "closerTeamId": "ทีม",
    "address.line": "ที่อยู่",
    "address.subdistrict": "ตำบล",
    "address.district": "อำเภอ",
    "address.province": "จังหวัด",
    "address.postalCode": "รหัสไปรษณีย์",
}

#: Sentences produced by the domain rules (parity-pinned, so translated here).
PHRASES: tuple[tuple[str, str], ...] = (
    ("Customer identity is ambiguous", "ยืนยันตัวตนลูกค้าไม่ได้"),
    ("exact normalized phone + similar name", "เบอร์โทรตรงกัน และชื่อใกล้เคียง"),
    ("exact normalized phone but name differs", "เบอร์โทรตรงกัน แต่ชื่อต่างกัน"),
    ("strong name similarity without exact phone", "ชื่อคล้ายมาก แต่เบอร์โทรไม่ตรง"),
    ("phone differs by 1-2 digits with similar name", "เบอร์โทรต่างกัน 1-2 หลัก และชื่อใกล้เคียง"),
    ("similar name without exact phone", "ชื่อคล้ายแต่เบอร์โทรไม่ตรง"),
    ("candidate customers qualify; not auto-matching", "รายที่เข้าข่ายตรงกัน ระบบไม่จับคู่อัตโนมัติ"),
    ("Candidates:", "ลูกค้าที่เข้าข่าย:"),
    ("same source version reports a different value", "ไฟล์ชุดเดียวกันให้ค่าที่ต่างจากที่บันทึกไว้"),
    ("incoming source timestamp is older than the stored field provenance", "เวลาของข้อมูลในไฟล์เก่ากว่าข้อมูลที่บันทึกไว้"),
    ("undated source with no precedence advantage cannot overwrite a dated value", "ข้อมูลไม่มีวันที่และไม่มีลำดับความสำคัญสูงกว่า จึงทับค่าที่มีวันที่ไม่ได้"),
    ("conflicting value from a lower-precedence source", "ค่าขัดแย้งจากแหล่งข้อมูลที่มีลำดับความสำคัญต่ำกว่า"),
    ("customerId changes require the identity merge flow, not an order upsert", "การเปลี่ยนลูกค้าของออเดอร์ต้องทำผ่านขั้นตอนรวมตัวตน ไม่ใช่การนำเข้า"),
    ("source-derived line items replaced as a unit; totals remain field-merged", "รายการสินค้าจากไฟล์แทนที่ทั้งชุด"),
    (
        "Order closed by a different user while the lead holds an active exclusive caretaker. Ownership was kept.",
        "ออเดอร์ถูกปิดโดยคนอื่นขณะที่ Lead มีผู้ดูแลแบบเฉพาะบุคคล ระบบคงผู้ดูแลเดิมไว้",
    ),
    (
        "A stopped lead received a new order from the same caretaker: choose REACTIVATE or KEEP_STOPPED.",
        "Lead ที่หยุดแล้วได้ออเดอร์ใหม่จากผู้ดูแลคนเดิม: เลือก เปิด Lead ใหม่ หรือ คงสถานะหยุด",
    ),
    ("The customer named by the identity decision no longer exists", "ลูกค้าที่ระบุในการตัดสินใจไม่มีอยู่แล้ว"),
)


def th_label(value) -> str:
    """Thai label for a machine code; the code itself when there is none."""
    text = "" if value is None else str(value)
    return LABELS.get(text, text)


def th_summary(summary) -> str:
    """Thai rendering of a conflict summary: field names and known sentences translated."""
    text = "" if summary is None else str(summary)
    field, sep, rest = text.partition(": ")
    if sep and field in FIELD_LABELS:
        text = f"{FIELD_LABELS[field]}: {rest}"
    for english, thai in PHRASES:
        text = text.replace(english, thai)
    return text
