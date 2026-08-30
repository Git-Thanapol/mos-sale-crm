from django.contrib.auth.decorators import login_required

from django.shortcuts import render

from crm.core.permissions import require_permission
from crm.online_orders.selectors import (
    DEFAULT_PAGE_SIZE,
    OnlineOrderFilters,
    online_order_filter_options,
    online_order_page,
)


@login_required
@require_permission("can_view_followup")
def index(request):
    filters = OnlineOrderFilters.from_query(request.GET)
    page_number = request.GET.get("page", 1)
    page_size = request.GET.get("page_size", DEFAULT_PAGE_SIZE)

    page = online_order_page(filters, page_number, page_size)
    options = online_order_filter_options()

    qs = request.GET.copy()
    qs.pop("page", None)
    qs.pop("id", None)
    base_qs = qs.urlencode()

    detail = None
    detail_id = request.GET.get("id")
    if detail_id:
        detail = next((o for o in page.items if str(o.id) == detail_id), None)

    context = {
        "page": page,
        "filters": filters,
        "options": options,
        "base_qs": base_qs,
        "detail": detail,
    }
    return render(request, "online_orders/index.html", context)
