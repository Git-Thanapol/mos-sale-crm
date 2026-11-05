"""The import screens: upload, preview, confirm/commit, conflicts and history.

Views stay thin: they authorize, call crm.importv2.services and turn the
service's refusals into toasts. A user who may not use a screen gets 404 (the
sidebar hides it); the services re-check authority on every write regardless.
"""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from crm.core.pagination import KeysetPage, decode_cursor_parts, encode_cursor
from crm.core.permissions import (
    can_import_v2,
    can_resolve_import_conflicts,
    can_view_import_history,
    import_actor_role,
)
from crm.domain.import_confirm import source_requires_batch_date
from crm.domain.import_errors import BatchBusinessDateError, ConfirmError
from crm.importv2.models import ImportBatch
from crm.importv2.services import conflicts as conflict_service
from crm.importv2.services.cancel import CANCELLABLE, cancel_batch, restage_batch
from crm.importv2.services.commit import StalePreviewError, StaleResolutionError, commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview, set_batch_business_date
from crm.importv2.services.rollback import retry_rollback, rollback_batch
from crm.importv2.services.upload import UploadError, create_batch
from crm.leadui.import_steps import STAGES, next_step
from crm.leadui.views import lead_page, pager_urls

PAGE_SIZE = 25
INOUT = "INOUT_MANAGE_MAIN"
LITE = "LITE_DETAIL_ORDER"
#: Normal for these exports (they carry columns nobody needs); listing them buries real warnings.
QUIET_WARNINGS = ("UNMAPPED_COLUMN", "DUPLICATE_HEADER")
# COMMITTING / ROLLING_BACK are included so an interrupted commit or rollback can be resumed.
HISTORY_ROLLBACK_STATUSES = ("COMMITTED", "PAUSED_QUOTA", "FAILED_RETRYABLE", "COMMITTING", "ROLLING_BACK")
# Admin-only retry after a human reconciled the rows a failed rollback listed.
HISTORY_RETRY_STATUSES = ("ROLLBACK_FAILED",)

#: A refusal code from a service, in words an operator can act on.
REFUSALS = {
    "ILLEGAL_STATE": "ขั้นตอนนี้ทำไม่ได้ในสถานะปัจจุบันของ Batch",
    "ALREADY_RESOLVED": "Conflict นี้ถูกแก้ไปแล้ว",
    "INVALID_RESOLUTION": "ตัวเลือกนี้ไม่ได้อยู่ในรายการของ Conflict",
    "INVALID_ARGUMENT": "ต้องระบุเหตุผล (อย่างน้อย 3 ตัวอักษร)",
    "CONCURRENT_MODIFICATION": "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน",
}


def _refusal(error: ValueError) -> str:
    text = str(error)
    for code, message in REFUSALS.items():
        if text.startswith(code):
            return f"{message} ({code})"
    return text


def _rollback_failed_text(result: dict) -> str:
    why = "; ".join(f"{d['entity']} #{d['id']}: {d['why']}" for d in (result.get("drift") or [])[:3])
    return f"Rollback หยุด: {why} — ต้องตรวจสอบด้วยมือ แล้วกด Retry rollback (ROLLBACK_FAILED)"


def _batch(batch_uuid) -> ImportBatch:
    batch = ImportBatch.objects.filter(batch_id=batch_uuid).first()
    if batch is None:
        raise Http404
    return batch


def _back(request, default: str) -> str:
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


def _import_url(batch: ImportBatch | None = None) -> str:
    base = reverse("leadui:import")
    return f"{base}?batch={batch.batch_id}" if batch else base


# --- นำเข้าข้อมูล ------------------------------------------------------------------------


@lead_page(can_import_v2)
def import_page(request):
    wanted = (request.GET.get("batch") or "").strip()
    batch = None
    if wanted:
        try:
            batch = ImportBatch.objects.filter(batch_id=uuid.UUID(wanted)).first()
        except ValueError:
            batch = None
    if batch is None:
        batch = ImportBatch.objects.exclude(status="CANCELLED").order_by("-id").first()
    aggregate = (batch.preview_aggregate if batch else None) or {}
    issues = (batch.validation or {}).get("issues", []) if batch else []
    blocks = [i for i in issues if i["severity"] == "BLOCK"]
    step = next_step(
        status=batch.status if batch else None,
        has_blocks=bool(blocks),
        open_blocking=batch.conflicts.filter(severity="BLOCKING", status="OPEN").count() if batch else 0,
        is_inout=bool(batch and batch.source_kind == INOUT),
    )
    return render(
        request,
        "leadui/import.html",
        {
            "batch": batch,
            "aggregate": aggregate,
            "blocks": blocks,
            "warnings": [i for i in issues if i["severity"] == "WARNING" and i["code"] not in QUIET_WARNINGS],
            "slots": _slots(batch),
            "other_file": bool(batch and batch.source_kind not in (INOUT, LITE)),
            "stages": [
                {"number": number, "label": label, "state": _stage_state(number - 1, step["stage"])}
                for number, label in enumerate(STAGES, start=1)
            ],
            "step": step,
            "can_repreview": bool(batch and batch.status == "AWAITING_RESOLUTION" and not blocks),
            "needs_batch_date": bool(batch and source_requires_batch_date(batch.source_kind)),
            "operation_key": uuid.uuid4().hex,
            "can_cancel": bool(batch and batch.status in CANCELLABLE and import_actor_role(request.user) == "ADMIN"),
            "batch_date": batch.batch_business_date.isoformat() if batch and batch.batch_business_date else "",
        },
    )


def _stage_state(index: int, current: int) -> str:
    return "done" if index < current else "now" if index == current else ""


def _slots(selected: ImportBatch | None) -> list[dict]:
    """The two main upload slots, each with its kind's latest batch that was not cancelled."""
    latest = {
        kind: ImportBatch.objects.filter(source_kind=kind).exclude(status="CANCELLED").order_by("-id").first()
        for kind in (INOUT, LITE)
    }
    inout_done = bool(latest[INOUT] and latest[INOUT].status == "COMMITTED")
    return [
        {
            "number": number,
            "kind": kind,
            "title": title,
            "provides": provides,
            "batch": latest[kind],
            "selected": bool(selected and latest[kind] and selected.pk == latest[kind].pk),
            # A reminder only: Lite can be uploaded at any time, including again later.
            "hint": "รอไฟล์ 1 เสร็จก่อน" if kind == LITE and not inout_done else "",
        }
        for number, kind, title, provides in (
            (1, INOUT, "InoutManageMain", "ให้เบอร์โทร ชื่อลูกค้า ที่อยู่ และยอดรวม"),
            (2, LITE, "LiteDetailOrder", "ให้รายการสินค้า จำนวน ราคา และช่องทางขาย"),
        )
    ]


@lead_page(can_import_v2)
@require_POST
def import_upload(request):
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "กรุณาเลือกไฟล์นำเข้า")
        return redirect(_import_url())
    slot = request.POST.get("slot")
    try:
        batch = create_batch(
            actor=request.user, file_name=upload.name, raw=upload.read(),
            expected_source_kind=slot if slot in (INOUT, LITE) else None,
        )
    except UploadError as error:
        messages.error(request, f"นำเข้าไฟล์ไม่ได้: {error.message} ({error.code})")
        return redirect(_import_url())
    messages.success(request, f"อัปโหลด {batch.file_name} แล้ว ({batch.row_count} แถว) — กด สร้าง Preview")
    return redirect(_import_url(batch))


@lead_page(can_import_v2)
@require_POST
def import_preview(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        aggregate = build_preview(actor=request.user, batch=batch)
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        batch.refresh_from_db()
        if batch.status == "PREVIEW":
            messages.error(request, "Preview ยังไม่ผ่านการตรวจสอบ — ดูรายการปัญหาด้านล่าง")
        else:
            messages.success(
                request,
                f"สร้าง Preview แล้ว: ออเดอร์ใหม่ {aggregate['newOrders']} · อัปเดต {aggregate['safeUpdates']} "
                f"· Conflict {aggregate['conflicts']}",
            )
    return redirect(_import_url(batch))


@lead_page(can_import_v2)
@require_POST
def import_date(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        set_batch_business_date(actor=request.user, batch=batch, text=request.POST.get("batch_business_date", ""))
    except (BatchBusinessDateError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "ยืนยัน Batch Business Date แล้ว")
    return redirect(_import_url(batch))


@lead_page(can_import_v2)
@require_POST
def import_confirm(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        confirm_batch(
            actor=request.user, batch=batch,
            preview_hash=request.POST.get("preview_hash", ""),
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
        )
    except ConfirmError as error:
        messages.error(request, "Confirm ไม่ได้: " + " · ".join(b["message"] for b in error.blockers))
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        messages.success(request, "ยืนยันแล้ว — กด Commit เพื่อเขียนข้อมูลจริง")
    return redirect(_import_url(batch))


@lead_page(can_import_v2)
@require_POST
def import_commit(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        result = commit_batch(
            actor=request.user, batch=batch, operation_key=request.POST.get("operation_key") or uuid.uuid4().hex
        )
    except StalePreviewError:
        messages.error(request, "Preview เปลี่ยนไปหลังการอนุมัติ ไม่มีการเขียนข้อมูล — ยกเลิกแล้วอัปโหลดใหม่ (STALE_PREVIEW)")
    except StaleResolutionError as error:
        messages.error(
            request,
            "การตัดสิน Ownership/Reactivation แถว " + ", ".join(map(str, error.rows[:10])) + " ไม่ตรงกับ Lead ปัจจุบัน "
            "ไม่มีการเขียนข้อมูล — Cancel/Restage แล้วสร้าง Preview ใหม่ (STALE_RESOLUTION)",
        )
    except ValueError as error:
        messages.error(request, _refusal(error))
    except Exception as error:  # the batch is FAILED_RETRYABLE and journalled; say so
        messages.error(request, f"Commit ล้มเหลวระหว่างทาง: {type(error).__name__} — Batch อยู่ในสถานะ FAILED_RETRYABLE")
    else:
        messages.success(request, f"Commit เสร็จ: สร้าง {result['created']} · อัปเดต {result['updated']}")
    return redirect(_import_url(batch))


@lead_page(can_import_v2)
@require_POST
def import_cancel(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        restage = request.POST.get("restage") == "1"
        action = restage_batch if restage else cancel_batch
        result = action(
            actor=request.user, batch=batch, reason=(request.POST.get("reason") or "").strip(),
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
        )
    except PermissionDenied:
        raise
    except ValueError as error:
        messages.error(request, _refusal(error))
        return redirect(_import_url(batch))
    if result.get("newBatchId"):
        messages.success(request, "ยกเลิก Batch เดิมแล้ว และสร้าง Batch ใหม่จากไฟล์เดิม — กด สร้าง Preview")
        return redirect(reverse("leadui:import") + "?batch=" + result["newBatchId"])
    messages.success(request, "ยกเลิก Batch แล้ว (ไม่มีการเขียนข้อมูลธุรกิจ)")
    return redirect(_back(request, _import_url(batch)))


# --- Import Conflict Resolution ----------------------------------------------------------


@lead_page(can_resolve_import_conflicts)
def conflicts_page(request):
    wanted = (request.GET.get("batch") or "").strip()
    batch = None
    if wanted:
        try:
            batch = ImportBatch.objects.filter(batch_id=uuid.UUID(wanted)).first()
        except ValueError:
            batch = None
    if batch is None:
        batch = conflict_service.latest_batch_with_open_conflicts(request.user)
    page = (
        conflict_service.list_conflicts(
            request.user, batch=batch, after_cursor=(request.GET.get("cursor") or None), limit=PAGE_SIZE
        )
        if batch
        else KeysetPage(items=[], next_cursor=None, has_more=False)
    )
    cards = [
        {"conflict": c, "hash": conflict_service.version_hash(c), "operation_key": uuid.uuid4().hex}
        for c in page.items
    ]
    return render(
        request,
        "leadui/import_conflicts.html",
        {"cards": cards, "batch": batch, **pager_urls(request, page)},
    )


@lead_page(can_resolve_import_conflicts)
@require_POST
def conflict_resolve(request, conflict_id: int):
    # Looked up through the visibility filter: a team lead cannot even probe a
    # conflict that is not theirs (404, not 403).
    conflict = conflict_service.visible_conflicts(request.user).filter(pk=conflict_id).first()
    if conflict is None:
        raise Http404
    try:
        conflict_service.resolve_conflict(
            actor=request.user,
            conflict=conflict,
            resolution=request.POST.get("resolution", ""),
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
            expected_version=int(request.POST.get("expected_version") or 0),
            expected_hash=request.POST.get("expected_hash", ""),
            reason=request.POST.get("reason", ""),
        )
    except PermissionDenied:
        raise
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        messages.success(request, f"บันทึกการตัดสิน: {request.POST.get('resolution')}")
    return redirect(_back(request, reverse("leadui:import_conflicts")))


# --- ประวัตินำเข้า ------------------------------------------------------------------------


def _history_page(user, after_cursor) -> KeysetPage:
    qs = ImportBatch.objects.order_by("-id")
    if import_actor_role(user) != "ADMIN":
        qs = qs.filter(pk__in=conflict_service.visible_conflicts(user).values("batch_id"))
    parts = decode_cursor_parts(after_cursor, ("int",))
    if parts is not None:
        qs = qs.filter(id__lt=parts[0])
    rows = list(qs[: PAGE_SIZE + 1])
    items = rows[:PAGE_SIZE]
    more = len(rows) > PAGE_SIZE
    return KeysetPage(items=items, next_cursor=encode_cursor((items[-1].id,)) if more and items else None, has_more=more)


@lead_page(can_view_import_history)
def history_page(request):
    page = _history_page(request.user, request.GET.get("cursor") or None)
    admin = can_import_v2(request.user)
    rows = [
        {
            "batch": b,
            "can_rollback": admin and b.status in HISTORY_ROLLBACK_STATUSES,
            "can_retry": admin and b.status in HISTORY_RETRY_STATUSES,
            "can_cancel": admin and b.status in CANCELLABLE and not b.journal.exists(),
            "operation_key": uuid.uuid4().hex,
        }
        for b in page.items
    ]
    return render(request, "leadui/import_history.html", {"rows": rows, **pager_urls(request, page)})


@lead_page(can_import_v2)
@require_POST
def history_rollback(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        result = rollback_batch(
            actor=request.user, batch=batch,
            reason=(request.POST.get("reason") or "").strip(),
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
        )
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        if result["status"] == "ROLLED_BACK":
            messages.success(request, f"Rollback เสร็จ: ลบ {result['deleted']} · คืนค่า {result['restored']}")
        else:
            messages.error(request, _rollback_failed_text(result))
    return redirect(_back(request, reverse("leadui:import_history")))


@lead_page(can_import_v2)
@require_POST
def history_retry_rollback(request, batch_uuid):
    batch = _batch(batch_uuid)
    try:
        result = retry_rollback(
            actor=request.user, batch=batch, reason=(request.POST.get("reason") or "").strip(),
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
        )
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        if result["status"] == "ROLLED_BACK":
            messages.success(request, f"Rollback เสร็จ: ลบ {result['deleted']} · คืนค่า {result['restored']}")
        else:
            messages.error(request, _rollback_failed_text(result))
    return redirect(_back(request, reverse("leadui:import_history")))


# --- Migration Decisions ----------------------------------------------------------------

ACTION_LABELS = {
    "MAP_TO_USER": "ผูกกับผู้ใช้ V2",
    "HISTORICAL_ONLY": "เก็บเป็นประวัติเท่านั้น",
    "DEFER": "เลื่อนการตัดสิน",
    "MATCH_EXISTING": "รวมกับลูกค้าที่มีอยู่",
    "CREATE_NEW_CUSTOMER": "สร้างลูกค้าใหม่ 1 ราย",
    "KEEP_SEPARATE": "แยกตามชื่อ (ไม่รวม)",
    "MAP_TO_STATUS": "กำหนดสถานะมาตรฐาน",
    "ARCHIVE_ONLY": "เก็บเป็นประวัติ (ค่าเริ่มต้นเดิม)",
    "SET_AUTHORITATIVE_DATE": "ใส่วันที่ที่ยืนยันแล้ว",
    "KEEP_BLOCKED": "คงสถานะถูกกัน",
}
CATEGORY_LABELS = {
    "STAFF_ALIAS": "ชื่อพนักงานเดิม",
    "IDENTITY_REVIEW": "ตรวจตัวตนลูกค้า",
    "UNKNOWN_STATUS": "สถานะที่ไม่รู้จัก",
    "NULL_DATE_ORDER": "ออเดอร์ไม่มีวันที่",
}
DECISION_PAGE_SIZE = 25
STATUS_PAYLOAD_KEYS = ("orderStatus", "paymentStatus", "shippingStatus")


def mask_phone_value(national: str) -> str:
    from crm.domain.migration_decisions import mask_phone

    return mask_phone(national) if national else "ไม่มีเบอร์"


def bangkok_input_to_millis(value: str) -> int | None:
    """`YYYY-MM-DDTHH:mm` Bangkok wall clock to epoch millis, or None. No guessing."""
    import re
    from datetime import datetime

    from crm.domain.bangkok import BANGKOK_TZ

    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", (value or "").strip())
    if not match:
        return None
    try:
        local = datetime(*(int(g) for g in match.groups()), tzinfo=BANGKOK_TZ)
    except ValueError:
        return None
    return round(local.timestamp() * 1000)


def _decision_view(decision):
    from crm.importv2.services.decisions import offered_actions

    evidence = decision.evidence or {}
    resolution = decision.resolution or {}
    return {
        "decision": decision,
        "evidence": evidence,
        "kind": evidence.get("kind"),
        "masked_phone": mask_phone_value(evidence.get("normalizedPhone", "")) if evidence.get("kind") == "IDENTITY_REVIEW" else "",
        "source_context": " · ".join(
            f"{team or '(ว่าง)'} {count}" for team, count in (evidence.get("sourceContext") or {}).items()
        ),
        "variants": (evidence.get("nameVariants") or [])[:6],
        "similarities": [
            {"a": s["a"] + 1, "b": s["b"] + 1, "pct": round(s["score"] * 100)}
            for s in (evidence.get("similarities") or [])[:4]
        ],
        "actions": [(a, ACTION_LABELS.get(a, a)) for a in offered_actions(decision)],
        "resolution": resolution,
        "resolution_label": ACTION_LABELS.get(resolution.get("action"), resolution.get("action")),
        "resolved_by": (resolution.get("resolvedBy") or ""),
    }


@lead_page(can_import_v2)
def decisions_page(request):
    from crm.domain.migration_decisions import DECISION_CATEGORIES, DECISION_REASON_FILTERS, DECISION_STATUSES
    from crm.importv2.services import decisions as decision_service

    category = request.GET.get("category") if request.GET.get("category") in DECISION_CATEGORIES else "STAFF_ALIAS"
    status = request.GET.get("status") if request.GET.get("status") in DECISION_STATUSES else "OPEN"
    reason = request.GET.get("reason") if request.GET.get("reason") in DECISION_REASON_FILTERS[category] else ""
    trail = [c for c in (request.GET.get("trail") or "").split(",") if c][-20:]
    cursor = request.GET.get("cursor") or None
    page = decision_service.list_decisions(
        request.user, category=category, status=status, reason_code=reason or None,
        after_cursor=cursor, limit=DECISION_PAGE_SIZE,
    )
    counts = decision_service.summary(request.user)

    def url(**changes):
        query = request.GET.copy()
        for key, value in changes.items():
            if value in (None, ""):
                query.pop(key, None)
            else:
                query[key] = value
        encoded = query.urlencode()
        return f"?{encoded}" if encoded else "?"

    previous_url = ""
    if cursor is not None or trail:
        previous_url = url(cursor=trail[-1] if trail else None, trail=",".join(trail[:-1]) or None)
    next_url = ""
    if page.next_cursor:
        next_url = url(cursor=page.next_cursor, trail=",".join([*trail, cursor] if cursor else trail) or None)
    return render(
        request,
        "leadui/migration_decisions.html",
        {
            "category": category,
            "status": status,
            "reason": reason,
            "tabs": [(c, CATEGORY_LABELS[c], counts[c]) for c in DECISION_CATEGORIES],
            "statuses": DECISION_STATUSES,
            "reasons": DECISION_REASON_FILTERS[category],
            "unresolved_here": counts[category],
            "page_size": DECISION_PAGE_SIZE,
            "cards": [_decision_view(d) for d in page.items],
            "previous_url": previous_url,
            "next_url": next_url,
            "next": request.get_full_path(),
            "order_statuses": ("PENDING_CONFIRMATION", "WAITING_SHIPMENT", "SHIPPED", "DELIVERED", "CANCELLED", "COD_RETURNED"),
            "payment_statuses": ("UNPAID", "PENDING_VERIFICATION", "PAID", "COD_PENDING", "COD_COLLECTED", "REFUNDED"),
            "shipping_statuses": ("NOT_SHIPPED", "LABEL_CREATED", "PICKED_UP", "IN_TRANSIT", "DELIVERED", "FAILED", "RETURNING", "RETURNED"),
        },
    )


def _decision_payload(request, action: str) -> dict:
    post = request.POST
    if action == "MAP_TO_USER":
        return {"userId": (post.get("userId") or post.get("exact_user") or "").strip()}
    if action == "MATCH_EXISTING":
        return {"customerId": (post.get("customerId") or "").strip()}
    if action == "MAP_TO_STATUS":
        return {key: post.get(key, "") for key in STATUS_PAYLOAD_KEYS}
    if action == "SET_AUTHORITATIVE_DATE":
        return {
            "orderedAt": bangkok_input_to_millis(post.get("orderedAt", "")),
            "evidenceRef": (post.get("evidenceRef") or "").strip(),
        }
    return {}


def _decision_refusal(error: ValueError) -> str:
    text = str(error)
    if text.startswith("CONCURRENT_MODIFICATION"):
        return "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน"
    if text.startswith("NOT_FOUND"):
        return "ไม่พบรายการนี้"
    return text


@lead_page(can_import_v2)
@require_POST
def decision_resolve(request, decision_id: str):
    from crm.importv2.services import decisions as decision_service

    action = request.POST.get("action", "")
    try:
        decision_service.resolve_decision(
            actor=request.user, decision_id=decision_id,
            revision=int(request.POST.get("revision") or 0),
            evidence_hash=request.POST.get("evidence_hash", ""),
            action=action, payload=_decision_payload(request, action), reason=request.POST.get("reason", ""),
        )
    except ValueError as error:
        messages.error(request, _decision_refusal(error))
    else:
        messages.success(request, f"บันทึกแล้ว: {ACTION_LABELS.get(action, action)}")
    return redirect(_back(request, reverse("leadui:migration_decisions")))


@lead_page(can_import_v2)
@require_POST
def decision_reopen(request, decision_id: str):
    from crm.importv2.services import decisions as decision_service

    try:
        decision_service.reopen_decision(
            actor=request.user, decision_id=decision_id,
            revision=int(request.POST.get("revision") or 0), reason=request.POST.get("reason", ""),
        )
    except ValueError as error:
        messages.error(request, _decision_refusal(error))
    else:
        messages.success(request, "เปิดการตัดสินใหม่แล้ว")
    return redirect(_back(request, reverse("leadui:migration_decisions")))
