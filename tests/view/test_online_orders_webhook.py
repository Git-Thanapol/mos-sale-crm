"""POST /online-orders/webhook/trackflow/ -- the live status push from
TRACKFLOW (crm.online_orders.views.trackflow_webhook /
crm.online_orders.services.apply_trackflow_status). A machine caller
(shared-secret header, not a staff session), so these tests hit the URL
directly with Django's test client rather than going through login_user
like the rest of tests/view/test_online_orders.py.
"""

import json

import pytest
from django.test import override_settings
from django.urls import reverse

from crm.online_orders.models import OnlineOrder

pytestmark = [pytest.mark.view, pytest.mark.django_db]

TOKEN = "test-shared-secret"
URL_NAME = "online_orders:trackflow_webhook"


def _post(client, body, token=TOKEN):
    return client.post(
        reverse(URL_NAME),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_TRACKFLOW_TOKEN=token,
    )


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_updates_every_online_order_sharing_the_tracking_no(client):
    order1 = OnlineOrder.objects.create(online_order_no="OO-1", tracking_no="TH123", order_status="เก่า")
    order2 = OnlineOrder.objects.create(online_order_no="OO-2", tracking_no="TH123", order_status="เก่า")
    other = OnlineOrder.objects.create(online_order_no="OO-3", tracking_no="TH999", order_status="เก่า")

    response = _post(client, {"tracking_no": "TH123", "status": "เรียบร้อย"})

    assert response.status_code == 200
    assert response.json() == {"updated": 2}

    order1.refresh_from_db()
    order2.refresh_from_db()
    other.refresh_from_db()
    assert order1.order_status == "เรียบร้อย"
    assert order2.order_status == "เรียบร้อย"
    assert order1.trackflow_synced_at is not None
    assert other.order_status == "เก่า"  # different tracking_no, untouched


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
@pytest.mark.parametrize(
    "trackflow_status,expected_crm_status",
    [
        ("จัดส่งแล้ว", "จัดส่งแล้ว"),
        ("เรียบร้อย", "เรียบร้อย"),
        ("รอตีกลับ", "รอตีกลับ"),
        ("ตีกลับเข้าคลัง", "ตีกลับแล้ว"),
        ("ตีกลับเรียบร้อยแล้ว", "ตีกลับแล้ว"),
        ("แจ้งเคลม", "รอแจ้งเคลม"),
        ("แจ้งเคลมสำเร็จ", "แจ้งเคลมเรียบร้อย"),
        ("เคลมไม่สำเร็จ", "เคลมไม่สำเร็จ"),
    ],
)
def test_maps_every_trackflow_status_into_the_crms_vocabulary(client, trackflow_status, expected_crm_status):
    OnlineOrder.objects.create(online_order_no="OO-1", tracking_no="TH123")

    response = _post(client, {"tracking_no": "TH123", "status": trackflow_status})

    assert response.status_code == 200
    order = OnlineOrder.objects.get(online_order_no="OO-1")
    assert order.order_status == expected_crm_status


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_no_matching_tracking_no_returns_404_and_writes_nothing(client):
    response = _post(client, {"tracking_no": "NOPE", "status": "เรียบร้อย"})
    assert response.status_code == 404
    assert not OnlineOrder.objects.filter(tracking_no="NOPE").exists()


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_wrong_token_is_rejected_and_writes_nothing(client):
    OnlineOrder.objects.create(online_order_no="OO-1", tracking_no="TH123", order_status="เก่า")

    response = _post(client, {"tracking_no": "TH123", "status": "เรียบร้อย"}, token="wrong-token")

    assert response.status_code == 401
    order = OnlineOrder.objects.get(online_order_no="OO-1")
    assert order.order_status == "เก่า"


@override_settings(TRACKFLOW_WEBHOOK_TOKEN="")
def test_blank_configured_token_rejects_every_call(client):
    """The webhook must default to closed, not open, if an operator
    deploys without setting TRACKFLOW_WEBHOOK_TOKEN."""
    response = _post(client, {"tracking_no": "TH123", "status": "เรียบร้อย"}, token="")
    assert response.status_code == 401


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_missing_status_is_rejected_as_bad_request(client):
    response = _post(client, {"tracking_no": "TH123"})
    assert response.status_code == 400


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_missing_tracking_no_is_rejected_as_bad_request(client):
    response = _post(client, {"status": "เรียบร้อย"})
    assert response.status_code == 400


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_malformed_json_body_is_rejected_as_bad_request(client):
    response = client.post(
        reverse(URL_NAME),
        data="not json",
        content_type="application/json",
        HTTP_X_TRACKFLOW_TOKEN=TOKEN,
    )
    assert response.status_code == 400


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_get_is_not_allowed(client):
    response = client.get(reverse(URL_NAME), HTTP_X_TRACKFLOW_TOKEN=TOKEN)
    assert response.status_code == 405


@override_settings(TRACKFLOW_WEBHOOK_TOKEN=TOKEN)
def test_call_is_idempotent(client):
    """A retried delivery (TRACKFLOW's own outbox retries on any non-2xx,
    and could in principle also redeliver after a 200 it never saw the
    response to) must be safe to apply twice."""
    OnlineOrder.objects.create(online_order_no="OO-1", tracking_no="TH123", order_status="เก่า")

    first = _post(client, {"tracking_no": "TH123", "status": "เรียบร้อย"})
    second = _post(client, {"tracking_no": "TH123", "status": "เรียบร้อย"})

    assert first.status_code == second.status_code == 200
    order = OnlineOrder.objects.get(online_order_no="OO-1")
    assert order.order_status == "เรียบร้อย"
