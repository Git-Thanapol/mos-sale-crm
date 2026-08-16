import django_rq
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from crm.core.permissions import require_permission
from crm.imports.forms import ExcelUploadForm
from crm.imports.models import ImportJob
from crm.imports.tasks import run_import_job


@login_required
@require_permission("can_import_excel")
def import_excel(request):
    """Single-step upload -> commit (not a full multi-step wizard with
    sheet-selection/column-mapping/preview — the fixed templates
    established in Phase 2/4 make those steps unnecessary; see
    crm.imports.services' module docstring for the header contracts).

    Processing runs in the background (crm.imports.tasks.run_import_job on
    the RQ worker) rather than inline in this view — large workbooks were
    taking long enough to trip nginx's/gunicorn's request timeout (504).
    The view just persists the upload and redirects to a status page that
    polls until the job finishes. Every row is still validated per-row;
    invalid rows are recorded in staging with import_status='invalid' and
    skipped for the normalized write, never silently dropped.
    """
    if request.method == "POST":
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["file"]
            job = ImportJob.objects.create(
                file=upload, original_filename=upload.name, uploaded_by=request.user.email,
            )
            # RQ_QUEUES only defines "crm-default" (the only queue the
            # worker container listens on, see compose.yaml) — plain
            # django_rq.enqueue() would enqueue onto "default", which has
            # no worker.
            django_rq.get_queue("crm-default").enqueue(run_import_job, job.pk)
            return redirect(reverse("imports:status", args=[job.pk]))
    else:
        form = ExcelUploadForm()

    return render(request, "imports/upload.html", {"form": form})


@login_required
@require_permission("can_import_excel")
def import_status(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    template = "imports/_status_card.html" if request.htmx else "imports/status.html"
    return render(request, template, {"job": job})
