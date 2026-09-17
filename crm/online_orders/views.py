import hmac
import json

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crm.core.permissions import require_permission
from crm.online_orders.selectors import (
    DEFAULT_PAGE_SIZE,
    OnlineOrderFilters,
    online_order_filter_options,
    online_order_page,
)
from crm.online_orders.services import apply_trackflow_status


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


@csrf_exempt
@require_POST
def trackflow_webhook(request):
    """Receives TRACKFLOW's status-push outbox events (one per shipment
    status transition -- see TRACKFLOW's tracking/crm.py). A machine
    caller, not a staff browser session, so this is token-authenticated
    (a shared secret in a header) rather than @login_required, and CSRF-
    exempt (there is no session/cookie here for Django's CSRF protection
    to apply to in the first place).

    Expected JSON body -- the shape TRACKFLOW's own CRM_FIELD_MAP/
    CRM_STATUS_MAP produce on their side (see TRACKFLOW's .env.example
    for the exact map this contract assumes):

        {"tracking_no": "<courier tracking number>", "status": "<TRACKFLOW's Thai status string>"}

    Status codes:
      200  matched >=1 OnlineOrder row and updated it/them
      400  malformed JSON or missing tracking_no/status
      401  missing/wrong X-TRACKFLOW-TOKEN header
      404  well-formed request, but no OnlineOrder has that tracking_no
           yet (e.g. today's shipping-platform export hasn't been
           imported here yet) -- TRACKFLOW's own outbox retries with
           backoff on any non-2xx, so this self-heals once the import
           lands, with no special "pending" state needed on this side.
    """
    token = request.headers.get("X-TRACKFLOW-TOKEN", "")
    expected = settings.TRACKFLOW_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token, expected):
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    tracking_no = (body.get("tracking_no") or "").strip()
    status = (body.get("status") or "").strip()
    if not tracking_no or not status:
        return JsonResponse({"error": "tracking_no and status are required"}, status=400)

    updated = apply_trackflow_status(tracking_no=tracking_no, status=status)
    if not updated:
        return JsonResponse({"error": "no matching online order for this tracking_no"}, status=404)

    return JsonResponse({"updated": updated}, status=200)
