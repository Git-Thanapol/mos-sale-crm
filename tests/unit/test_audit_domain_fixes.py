from datetime import datetime, timedelta, timezone

import pytest

from crm.domain.identity import classify_identity_guarded
from crm.domain.last_order_age import DATE_ANOMALY_BUCKET, age_bucket_id, matches_last_order_age
from crm.domain.order_merge import merge_order
from crm.domain.order_status_transitions import (
    classify_order_status_transition,
    classify_payment_transition,
    classify_shipping_transition,
    status_change_allowed,
)
from crm.domain.ownership import (
    OwnershipOrderFacts,
    filter_ownership_orders,
    is_order_eligible_for_ownership,
)

T = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)


def test_ownership_skips_cancelled_returned_unknown_and_stale():
    assert is_order_eligible_for_ownership("DELIVERED", T, T)
    assert not is_order_eligible_for_ownership("CANCELLED", T, None)
    assert not is_order_eligible_for_ownership("COD_RETURNED", T, None)
    assert not is_order_eligible_for_ownership(None, T, None)
    assert not is_order_eligible_for_ownership("DELIVERED", T - timedelta(seconds=1), T)


def test_filter_ownership_orders_sorts_and_filters():
    a = OwnershipOrderFacts("a", T + timedelta(days=2), "u", None)
    b = OwnershipOrderFacts("b", T + timedelta(days=1), "u", None)
    c = OwnershipOrderFacts("c", T - timedelta(days=1), "u", None)
    out = filter_ownership_orders([(a, "DELIVERED"), (b, "CANCELLED"), (c, "DELIVERED")], T)
    assert [o.order_id for o in out] == ["a"]


def test_two_plausible_candidates_force_human_review():
    cands = [
        {"customerId": "c1", "name": "สมชาย ใจดี", "phones": ["0812345678"]},
        {"customerId": "c2", "name": "สมชาย ใจดี", "phones": ["0812345678"]},
    ]
    got = classify_identity_guarded(name="สมชาย ใจดี", phone="0812345678", candidates=cands)
    assert got["kind"] == "HUMAN_REVIEW"
    assert got["candidateCustomerId"] is None
    assert got["candidateCustomerIds"] == ["c1", "c2"]


def test_single_candidate_unchanged():
    cands = [{"customerId": "c1", "name": "สมชาย ใจดี", "phones": ["0812345678"]}]
    assert classify_identity_guarded(name="สมชาย ใจดี", phone="0812345678", candidates=cands)["kind"] == "AUTO_MATCH"


def test_unknown_status_values_do_not_raise():
    assert classify_order_status_transition("DELIVERED", "WEIRD") == "INVALID"
    assert classify_order_status_transition("WEIRD", "DELIVERED") == "INVALID"
    assert classify_payment_transition("PAID", "WEIRD") == "INVALID"
    assert classify_shipping_transition("WEIRD", "DELIVERED") == "INVALID"
    assert not status_change_allowed("INVALID")


def test_stale_status_is_rejected_by_merge():
    existing = {
        "orderStatus": "PENDING_CONFIRMATION",
        "fieldProvenance": {"orderStatus": {"sourceId": "a", "sourceUpdatedAt": 2000, "precedence": 10}},
    }
    incoming = {
        "orderId": "o",
        "fields": {"orderStatus": "DELIVERED"},
        "source": {"sourceId": "b", "sourceUpdatedAt": 1000, "precedence": 10},
    }
    result = merge_order(existing, incoming)
    assert result["decisions"][0]["kind"] == "STALE_INPUT"
    assert "orderStatus" not in result["updates"]


def test_future_dated_order_is_an_anomaly_not_zero_to_seven():
    now = datetime(2026, 5, 10, 5, tzinfo=timezone.utc)
    future = now + timedelta(days=3)
    assert age_bucket_id(future, now) == DATE_ANOMALY_BUCKET
    assert not matches_last_order_age(future, "0_7", None, None, now)
    assert age_bucket_id(now, now) == "0_7"
    assert age_bucket_id(None, now) == "NO_ORDER"
    assert age_bucket_id(now - timedelta(days=100), now) == "OVER_90"
