"""Display helpers for the Lead sections of the follow-up page.

Kept out of the templates so the Thai label rules and the computed states have
one definition each, and out of crm/domain/ because they are presentation.
"""

from __future__ import annotations

from django.utils import timezone

from crm.domain.appointments import appointment_state
from crm.domain.bangkok import age_in_days
from crm.domain.last_order_age import (
    LAST_ORDER_AGE_PRESETS,
    REALLOCATION_REVIEW_DEFAULT_DAYS,
    ProtectionSummary,
    is_reallocation_review_candidate,
)
from crm.domain.missed_channel import missed_channel_group

COMPUTED_STATUS_LABELS = {
    "APPOINTMENT_TODAY": "นัดวันนี้",
    "APPOINTMENT_FUTURE": "นัดล่วงหน้า",
    "OVERDUE": "เลยกำหนด",
}

LIFECYCLE_LABELS = {
    "NEW": "ใหม่",
    "FOLLOWING": "กำลังติดตาม",
    "WAITING_CUSTOMER": "รอลูกค้า",
    "WAITING_FOLLOWUP": "รอติดตาม",
    "STOPPED": "หยุดติดตาม",
}

ROUTING_LABELS = {
    "ASSIGNED": "มีผู้ดูแล",
    "TEAM_UNASSIGNED": "ยังไม่มีผู้ดูแลในทีม",
}

STOP_REASON_LABELS = {
    "CONTACT_LIMIT_REACHED": "ติดต่อครบจำนวนแล้ว",
    "MANUAL_STOP": "หยุดโดยผู้ใช้",
    "OTHER": "อื่น ๆ",
}

CONTACT_RESULT_LABELS = {
    "NO_ANSWER": "ไม่รับสาย",
    "BUSY": "สายไม่ว่าง",
    "CALLBACK_REQUESTED": "ขอให้โทรกลับ",
    "WRONG_NUMBER": "เบอร์ผิด",
    "REFUSED": "ปฏิเสธ",
    "ORDERED": "สั่งซื้อ",
    "OTHER": "อื่น ๆ",
}


def last_order_age_label(last_order_at, now=None) -> str:
    """'ออเดอร์ล่าสุด … / N วันที่แล้ว', or a no-order marker."""
    if last_order_at is None:
        return "ยังไม่มีออเดอร์"
    age = age_in_days(last_order_at, now or timezone.now())
    return f"{age} วันที่แล้ว"


def decorate(lead, *, now=None, threshold_days: int = REALLOCATION_REVIEW_DEFAULT_DAYS) -> dict:
    """Everything the row template needs, computed once per lead."""
    now = now or timezone.now()
    protection = ProtectionSummary(
        active_appointment=lead.next_follow_at is not None,
        active_followup_cycle=lead.current_cycle_id is not None,
        ownership_lock_active=False,
        next_follow_at=lead.next_follow_at,
    )
    computed = appointment_state(lead.next_follow_at, now)
    return {
        "lead": lead,
        "missed_channel_group": missed_channel_group(lead.missed_channel_count),
        "last_order_age_label": last_order_age_label(lead.last_order_at, now),
        "computed_status": computed,
        "computed_status_label": COMPUTED_STATUS_LABELS.get(computed, ""),
        "lifecycle_label": LIFECYCLE_LABELS.get(lead.lifecycle_status, lead.lifecycle_status),
        "routing_label": ROUTING_LABELS.get(lead.routing_state, lead.routing_state),
        "stop_reason_label": STOP_REASON_LABELS.get(lead.stopped_reason, ""),
        "reallocation_candidate": is_reallocation_review_candidate(
            last_order_at=lead.last_order_at,
            caretaker_user_id=str(lead.caretaker_id) if lead.caretaker_id else None,
            lifecycle_status=lead.lifecycle_status,
            protection=protection,
            now=now,
            threshold_days=threshold_days,
        ),
    }


def age_preset_options() -> list[tuple[str, str]]:
    return [(preset.id, preset.label) for preset in LAST_ORDER_AGE_PRESETS]
