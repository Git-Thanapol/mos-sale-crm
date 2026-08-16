"""RQ job run by the worker container (see compose.yaml's `worker` service:
`manage.py rqworker crm-default`). Split out from the synchronous view path
because large workbooks (many thousands of rows, each a handful of writes
in import_row) were taking long enough to trip nginx's/gunicorn's request
timeout — see crm.imports.views.import_excel.
"""

from __future__ import annotations

from django.utils import timezone

from crm.imports.models import ImportJob
from crm.imports.services import WorkbookFormatError, import_workbook


def run_import_job(job_id: int) -> None:
    job = ImportJob.objects.get(pk=job_id)
    job.status = ImportJob.STATUS_RUNNING
    job.save(update_fields=["status"])

    try:
        with job.file.open("rb") as fh:
            result = import_workbook(fh, job.uploaded_by)
    except WorkbookFormatError as exc:
        job.status = ImportJob.STATUS_FAILED
        job.error_message = str(exc)
    except Exception as exc:  # never leave the status page stuck on an unattended worker
        job.status = ImportJob.STATUS_FAILED
        job.error_message = f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {exc}"
    else:
        job.status = ImportJob.STATUS_DONE
        job.result = {**result, "batch_id": str(result["batch_id"])}

    job.finished_at = timezone.now()

    # The workbook has already been fully read into staging/normalized rows
    # at this point (or the read failed) — no need to keep the raw upload
    # around either way; it may contain customer PII, see
    # IMPORT_UPLOAD_ROOT's definition. delete(save=False): a single save()
    # below persists both this and the status/result fields together.
    job.file.delete(save=False)

    job.save(update_fields=["status", "result", "error_message", "finished_at", "file"])
