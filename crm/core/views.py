from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from crm.core.permissions import require_permission
from crm.reporting.selectors import dashboard_kpis


@login_required
def home(request):
    today = timezone.localdate()
    return render(request, "core/home.html", {"kpis": dashboard_kpis(request.user, today, today)})


@login_required
@require_permission("can_view_system_page")
def system_status(request):
    return render(request, "core/system_status.html")


@login_required
@require_permission("can_view_system_page")
def settings_page(request):
    return render(request, "core/settings.html")


@login_required
def coming_soon(request):
    """Placeholder for pages not yet built (Phase 3+). Keeps every
    NAV_GROUPS url_name reversible from Phase 1 onward so the sidebar never
    breaks while screens are being built out phase by phase.
    """
    return render(request, "core/coming_soon.html")
