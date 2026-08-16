from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from crm.core.permissions import require_permission
from crm.imports.forms import ExcelUploadForm
from crm.imports.services import WorkbookFormatError, import_workbook


@login_required
@require_permission("can_import_excel")
def import_excel(request):
    """Single-step upload -> commit (not a full multi-step wizard with
    sheet-selection/column-mapping/preview — the fixed template
    established in Phase 2 makes those steps unnecessary; see the
    import_xlsx management command's docstring for the header contract).
    Every row is validated per-row; invalid rows are recorded in staging
    with import_status='invalid' and skipped for the normalized write,
    never silently dropped.
    """
    if request.method == "POST":
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                result = import_workbook(form.cleaned_data["file"], request.user.email)
            except WorkbookFormatError as exc:
                messages.error(request, f"นำเข้าไม่สำเร็จ: {exc}")
            else:
                messages.success(
                    request,
                    f"นำเข้าสำเร็จ: {result['valid']} แถวถูกต้อง, {result['invalid']} แถวไม่ถูกต้อง "
                    f"(ลูกค้าใหม่ {result['customers_created']}, คำสั่งซื้อใหม่ {result['orders_created']}, "
                    f"รายการสินค้าใหม่ {result['lines_created']})",
                )
                return redirect(reverse("imports:upload"))
    else:
        form = ExcelUploadForm()

    return render(request, "imports/upload.html", {"form": form})
