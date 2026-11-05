"""What the import page tells the operator to do next, for one batch.

Pure: the page shows one primary action and one explanation, chosen here from
the batch's status. The services still decide what is allowed; this only decides
what to offer.
"""

from __future__ import annotations

STAGES = ("อัปโหลด", "Preview", "แก้ข้อขัดแย้ง", "Confirm", "Commit")
ALL_DONE = len(STAGES)

ACTION_PREVIEW = "preview"
ACTION_CONFLICTS = "conflicts"
ACTION_CONFIRM = "confirm"
ACTION_COMMIT = "commit"
ACTION_NEXT_FILE = "next_file"

_COMMIT_HELP = "บันทึกลงระบบจริง ขั้นนี้เท่านั้นที่ข้อมูลเปลี่ยน ย้อนกลับได้จากหน้าประวัตินำเข้า ถ้ายังไม่มีการแก้ข้อมูลต่อ"


def _step(stage: int, title: str, help_text: str, action: str | None = None, label: str = "") -> dict:
    return {"stage": stage, "title": title, "help": help_text, "action": action, "label": label}


def next_step(*, status: str | None, has_blocks: bool, open_blocking: int, is_inout: bool = False) -> dict:
    """`stage` is the index of the current stage in STAGES (ALL_DONE when finished)."""
    if status is None:
        return _step(0, "ขั้นต่อไป: เลือกไฟล์", "เลือกไฟล์ในช่องด้านบน ระบบจะตรวจไฟล์ให้ ยังไม่มีอะไรถูกบันทึก")
    if status in ("PREVIEW", "AWAITING_RESOLUTION") and has_blocks:
        return _step(
            1, "ขั้นต่อไป: แก้ไฟล์แล้วอัปโหลดใหม่",
            "ไฟล์นี้มีปัญหาตามรายการด้านล่าง นำเข้าไม่ได้จนกว่าจะแก้ ยังไม่มีอะไรถูกบันทึก",
        )
    if status == "PREVIEW":
        return _step(
            1, "ขั้นต่อไป: สร้าง Preview", "ตรวจไฟล์และดูว่าจะเกิดอะไรขึ้น ยังไม่แก้ข้อมูลจริง",
            ACTION_PREVIEW, "สร้าง Preview",
        )
    if status == "AWAITING_RESOLUTION" and open_blocking:
        return _step(
            2, f"ขั้นต่อไป: แก้ข้อขัดแย้ง {open_blocking:,} รายการ",
            "ระบบไม่เดาให้ ต้องให้คนเลือกทีละรายการ ยังไม่มีอะไรถูกบันทึก",
            ACTION_CONFLICTS, "ไปแก้ข้อขัดแย้ง",
        )
    if status == "AWAITING_RESOLUTION":
        return _step(
            3, "ขั้นต่อไป: Confirm", "ยืนยันว่าผล Preview ถูกต้อง ยังไม่แก้ข้อมูลจริง",
            ACTION_CONFIRM, "Confirm",
        )
    if status == "COMMITTING":
        return _step(4, "ขั้นต่อไป: Commit", _COMMIT_HELP, ACTION_COMMIT, "Commit")
    if status in ("FAILED_RETRYABLE", "PAUSED_QUOTA"):
        return _step(
            4, "การบันทึกหยุดกลางทาง",
            "กด Commit เพื่อทำต่อจากจุดเดิม ถ้ายังไม่ผ่าน ให้ยกเลิกแล้วสร้าง Batch ใหม่",
            ACTION_COMMIT, "Commit",
        )
    if status == "COMMITTED":
        if is_inout:
            return _step(
                ALL_DONE, "ไฟล์นี้นำเข้าแล้ว", "ต่อไปอัปโหลด LiteDetailOrder เพื่อเติมรายการสินค้า",
                ACTION_NEXT_FILE, "ไปไฟล์ถัดไป",
            )
        return _step(ALL_DONE, "ไฟล์นี้นำเข้าแล้ว", "ดูรายละเอียดหรือย้อนกลับได้จากหน้าประวัตินำเข้า")
    if status == "ROLLBACK_FAILED":
        return _step(0, "ย้อนกลับไม่สำเร็จ", "ต้องตรวจสอบข้อมูล แล้วทำต่อจากหน้าประวัตินำเข้า")
    if status == "ROLLING_BACK":
        return _step(0, "กำลังย้อนกลับ", "ทำต่อได้จากหน้าประวัตินำเข้า")
    return _step(0, "Batch นี้จบแล้ว", "ถ้าต้องการนำเข้าอีกครั้ง ให้อัปโหลดไฟล์ในช่องด้านบน")
