"""Parity of identity, status transitions, order merge and row evaluation with
the CRM V2 prototype (fixtures from scripts/export_crmv2_merge_fixtures.mts,
whose `expected` values come from EXECUTING the prototype).

Run via: docker compose exec -T web python -m pytest tests/unit/test_import_merge_parity.py
"""

import json
import math
from pathlib import Path

import pytest

from crm.domain import identity, order_merge, order_status_transitions as transitions, phone, row_evaluation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "crmv2"
pytestmark = pytest.mark.unit


def load(name):
    cases = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [pytest.param(case, id=case["name"]) for case in cases]


def roundtrip(value):
    """What the prototype's JSON.stringify keeps: no infinities, no NaN."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=lambda x: "Infinity" if x == math.inf else x))


@pytest.mark.parametrize("case", load("import_phone"))
def test_phone_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "normalizePhone":
        assert phone.normalize_phone(spec["raw"]) == expected
    elif fn == "digitEditDistance":
        got = phone.digit_edit_distance(spec["a"], spec["b"])
        assert ("Infinity" if got == math.inf else got) == expected
    elif fn == "looksLikePhone":
        assert phone.looks_like_phone(spec["value"]) is expected
    else:
        raise AssertionError(fn)


@pytest.mark.parametrize("case", load("import_identity"))
def test_identity_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "normalizeName":
        assert identity.normalize_name(spec["raw"]) == expected
    elif fn == "nameSimilarity":
        assert identity.name_similarity(spec["a"], spec["b"]) == pytest.approx(expected, abs=1e-12)
    elif fn == "classifyIdentity":
        got = identity.classify_identity(**spec["value"])
        assert got["nameScore"] == pytest.approx(expected["nameScore"], abs=1e-12)
        assert {**got, "nameScore": 0} == {**expected, "nameScore": 0}
    elif fn == "identityBlocksCommit":
        assert identity.identity_blocks_commit(spec["kind"]) is expected
    else:
        raise AssertionError(fn)


@pytest.mark.parametrize("case", load("import_status_transitions"))
def test_status_transitions_match_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "order":
        got = transitions.classify_order_status_transition(spec["current"], spec["incoming"], spec["allowReversal"])
    elif fn == "payment":
        got = transitions.classify_payment_transition(spec["current"], spec["incoming"], spec["allowReversal"])
    elif fn == "shipping":
        got = transitions.classify_shipping_transition(spec["current"], spec["incoming"], spec["allowReversal"])
    elif fn == "statusChangeAllowed":
        got = transitions.status_change_allowed(spec["verdict"], spec["allowReversal"])
    else:
        raise AssertionError(fn)
    assert got == expected


@pytest.mark.parametrize("case", load("import_order_merge"))
def test_order_merge_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    if spec["fn"] == "mergeOrder":
        got = order_merge.merge_order(spec["existing"], spec["incoming"], spec["allowReversal"])
        assert roundtrip(got) == expected
    else:
        assert order_merge.summarize_decisions(spec["decisions"]) == expected


@pytest.mark.parametrize("case", load("import_row_evaluation"))
def test_row_evaluation_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    if spec["fn"] == "evaluateRow":
        got = row_evaluation.evaluate_row(spec["facts"], spec["context"])
        assert roundtrip(got) == expected
    else:
        got = row_evaluation.aggregate_preview(spec["evaluations"], spec["newCustomers"])
        assert got == expected
