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
from crm.domain.import_errors import BatchBusinessDateError, ConfirmError
from crm.importv2.models import ImportBatch
from crm.importv2.services import conflicts as conflict_service
from crm.importv2.services.commit import StalePreviewError, commit_batch, confirm_batch
from crm.importv2.services.preview import build_preview, set_batch_business_date
from crm.importv2.services.rollback import rollback_batch
from crm.importv2.services.upload import UploadError, create_batch
from crm.leadui.views import lead_page, pager_urls

PAGE_SIZE = 25
HISTORY_ROLLBACK_STATUSES = ("COMMITTED", "PAUSED_QUOTA", "FAILED_RETRYABLE")

#: A refusal code from a service, in words an operator can act on.
REFUSALS = {
    "ILLEGAL_STATE": "ขั้นตอนนี้ทำไม่ได้ในสถานะปัจจุบันของ Batch",
    "ALREADY_RESOLVED": "Conflict นี้ถูกแก้ไปแล้ว",
    "INVALID_RESOLUTION": "ตัวเลือกนี้ไม่ได้อยู่ในรายการของ Conflict",
    "CONCURRENT_MODIFICATION": "ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน",
}


def _refusal(error: ValueError) -> str:
    text = str(error)
    for code, message in REFUSALS.items():
        if text.startswith(code):
            return f"{message} ({code})"
    return text


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
        batch = ImportBatch.objects.order_by("-id").first()
    aggregate = (batch.preview_aggregate if batch else None) or {}
    issues = (batch.validation or {}).get("issues", []) if batch else []
    return render(
        request,
        "leadui/import.html",
        {
            "batch": batch,
            "aggregate": aggregate,
            "blocks": [i for i in issues if i["severity"] == "BLOCK"],
            "warnings": [i for i in issues if i["severity"] == "WARNING"],
            "steps": ("Upload", "Validate", "Preview", "Resolve", "Confirm", "Commit"),
            "operation_key": uuid.uuid4().hex,
            "batch_date": batch.batch_business_date.isoformat() if batch and batch.batch_business_date else "",
        },
    )


@lead_page(can_import_v2)
@require_POST
def import_upload(request):
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "กรุณาเลือกไฟล์นำเข้า")
        return redirect(_import_url())
    try:
        batch = create_batch(actor=request.user, file_name=upload.name, raw=upload.read())
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
    except ValueError as error:
        messages.error(request, _refusal(error))
    except Exception as error:  # the batch is FAILED_RETRYABLE and journalled; say so
        messages.error(request, f"Commit ล้มเหลวระหว่างทาง: {type(error).__name__} — Batch อยู่ในสถานะ FAILED_RETRYABLE")
    else:
        messages.success(request, f"Commit เสร็จ: สร้าง {result['created']} · อัปเดต {result['updated']}")
    return redirect(_import_url(batch))


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
        {"batch": b, "can_rollback": admin and b.status in HISTORY_ROLLBACK_STATUSES, "operation_key": uuid.uuid4().hex}
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
            reason=request.POST.get("reason", "") or "Admin confirmed in UI",
            operation_key=request.POST.get("operation_key") or uuid.uuid4().hex,
        )
    except ValueError as error:
        messages.error(request, _refusal(error))
    else:
        if result["status"] == "ROLLED_BACK":
            messages.success(request, f"Rollback เสร็จ: ลบ {result['deleted']} · คืนค่า {result['restored']}")
        else:
            messages.error(request, "Rollback ไม่ได้: มีข้อมูลที่ถูกแก้ไขหลังนำเข้า ไม่มีการเปลี่ยนแปลงใดๆ (ROLLBACK_FAILED)")
    return redirect(_back(request, reverse("leadui:import_history")))
