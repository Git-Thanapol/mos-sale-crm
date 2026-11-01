"""Parity of the import rules with the CRM V2 prototype.

Each case's `expected` was produced by EXECUTING the prototype's own functions
(scripts/export_crmv2_import_fixtures.mts), so a green run here is evidence the
Python port behaves like the reference, not like a second opinion about it.

Never hand-edit tests/fixtures/crmv2/import_*.json; regenerate them.

Run via: docker compose exec -T web python -m pytest tests/unit/test_import_parity.py
"""

import json
import re
from pathlib import Path

import pytest

from crm.domain import conflict_scope as scope
from crm.domain import import_confirm as confirm
from crm.domain import import_hashing as hashing
from crm.domain import import_state as state
from crm.domain import tele_date as tele
from crm.domain.import_errors import (
    ApplicationError,
    BatchBusinessDateError,
    ConfirmError,
    ImportTransitionError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "crmv2"
DOMAIN_ERRORS = (ApplicationError, BatchBusinessDateError, ConfirmError, ImportTransitionError)

pytestmark = pytest.mark.unit


def load(name):
    cases = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [pytest.param(case, id=case["name"]) for case in cases]


def snake(value):
    """camelCase keys to snake_case, recursively. Only for documents whose keys
    are identifiers of the prototype's records, never for hashed payloads."""
    if isinstance(value, dict):
        return {re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), k): snake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snake(v) for v in value]
    return value


def camel(value):
    if isinstance(value, dict):
        return {re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k): camel(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camel(v) for v in value]
    return value


def check(expected, call, normalise=lambda v: v):
    """Assert `call()` produced `expected` (a {value} or {error} record)."""
    try:
        got = call()
    except DOMAIN_ERRORS as error:
        assert "error" in expected, f"unexpected {type(error).__name__}: {error}"
        want = expected["error"]
        assert type(error).__name__ == want["name"]
        assert getattr(error, "code", None) == want["code"]
        if "from" in want:
            assert error.from_status == want["from"] and error.to_status == want["to"]
        if "blockerCodes" in want:
            assert [b["code"] for b in error.blockers] == want["blockerCodes"]
        return
    assert "value" in expected, f"expected an error {expected['error']}, got {got!r}"
    assert normalise(got) == expected["value"]


# --- state machine -------------------------------------------------------------


@pytest.mark.parametrize("case", load("import_state"))
def test_state_machine_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]
    fn = spec["fn"]
    if fn == "canTransition":
        assert state.can_transition(spec["from"], spec["to"]) is expected["value"]
    elif fn == "isTerminal":
        assert state.is_terminal(spec["status"]) is expected["value"]
    elif fn == "canRetryCommit":
        assert state.can_retry_commit(spec["status"]) is expected["value"]
    elif fn == "needsRollback":
        assert state.needs_rollback(spec["status"]) is expected["value"]
    elif fn == "assertTransition":
        check(
            expected,
            lambda: state.assert_transition(spec["from"], spec["to"], snake(spec["context"])) or True,
        )
    elif fn == "applyTransition":
        check(
            expected,
            lambda: state.apply_transition(snake(spec["batch"]), spec["to"], snake(spec["context"])),
            camel,
        )
    else:
        raise AssertionError(fn)


def test_status_list_and_transition_table_have_no_unknown_states():
    assert set(state.IMPORT_TRANSITIONS) == set(state.IMPORT_BATCH_STATUSES)
    for targets in state.IMPORT_TRANSITIONS.values():
        assert set(targets) <= set(state.IMPORT_BATCH_STATUSES)


# --- hashing ---------------------------------------------------------------------


@pytest.mark.parametrize("case", load("import_hashing"))
def test_hashing_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "stableStringify":
        text = hashing.stable_stringify(spec["value"])
        assert text == expected["value"] if isinstance(expected, dict) else text == expected
        assert hashing.sha256_hex(text) == case["expected"]["sha256"]
    elif fn == "hashFields":
        assert hashing.hash_fields(spec["data"], spec["fields"]) == expected
    elif fn == "computePreviewHash":
        v = spec["value"]
        assert (
            confirm.compute_preview_hash(
                file_sha256=v["fileSha256"],
                mapping_profile_id=v["mappingProfileId"],
                mapping_profile_version=v["mappingProfileVersion"],
                batch_business_date=v["batchBusinessDate"],
                rows=v["rows"],
                decisions=v.get("decisions"),
            )
            == expected
        )
    elif fn == "nextResolutionPreviewHash":
        assert scope.next_resolution_preview_hash(spec["previous"], spec["decision"]) == expected
    elif fn == "conflictVersionHash":
        assert scope.conflict_version_hash(spec["conflict"]) == expected
    else:
        raise AssertionError(fn)


# --- confirm gate ----------------------------------------------------------------


@pytest.mark.parametrize("case", load("import_confirm"))
def test_confirm_gate_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "evaluateConfirm":
        decision = confirm.evaluate_confirm(**snake(spec["value"]))
        assert camel(decision) == expected
    elif fn == "sourceRequiresBatchDate":
        assert confirm.source_requires_batch_date(spec["kind"]) is expected
    elif fn == "IMPORT_CONFIRM_ROLES":
        assert sorted(confirm.IMPORT_CONFIRM_ROLES) == expected
    else:
        raise AssertionError(fn)


def test_assert_confirmable_raises_with_every_blocker_code():
    case = next(c for c in json.loads((FIXTURES / "import_confirm.json").read_text("utf-8")) if c["name"] == "confirm/everything-wrong")
    with pytest.raises(ConfirmError) as raised:
        confirm.assert_confirmable(**snake(case["input"]["value"]))
    assert [b["code"] for b in raised.value.blockers] == [
        b["code"] for b in case["expected"]["value"]["blockers"]
    ]


# --- batch business date -----------------------------------------------------------


@pytest.mark.parametrize("case", load("import_tele_date"))
def test_batch_business_date_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]
    fn = spec["fn"]
    if fn == "parseFilenameDate":
        assert camel(tele.parse_filename_date(spec["fileName"])) == expected["value"]
    elif fn == "proposeBatchBusinessDate":
        got = tele.propose_batch_business_date(spec["fileName"], spec.get("sourceEvidenceDayKeys"))
        assert camel(got) == expected["value"]
    elif fn == "confirmBatchBusinessDate":
        check(
            expected,
            lambda: tele.confirm_batch_business_date(
                spec["fileName"],
                spec["actor"],
                spec["at"],
                operator_day_key=spec.get("operatorDayKey"),
                source_evidence_day_keys=spec.get("sourceEvidenceDayKeys"),
            ),
            camel,
        )
    elif fn == "normalizeDayKey":
        assert tele.normalize_day_key(spec["text"]) == expected["value"]
    elif fn == "normalizeYear":
        assert tele.normalize_year(spec["year"], spec["digits"]) == expected["value"]
    elif fn == "dayKeyOf":
        assert tele.day_key_of(spec["year"], spec["month"], spec["day"]) == expected["value"]
    elif fn == "buildOrderedAt":
        check(expected, lambda: tele.build_ordered_at(spec["dayKey"], spec["timeText"]))
    elif fn == "assertConfirmedBatchDate":
        check(
            expected,
            lambda: tele.assert_confirmed_batch_date(snake(spec["value"]) if spec["value"] else None),
            camel,
        )
    else:
        raise AssertionError(fn)


# --- conflict resolver scope ---------------------------------------------------------


@pytest.mark.parametrize("case", load("import_conflict_scope"))
def test_resolver_scope_matches_the_prototype(case):
    spec = case["input"]
    actor = {"role": spec["actor"]["role"], "team_ids": spec["actor"]["teamIds"]}
    check(case["expected"], lambda: scope.assert_resolver_scope(actor, spec["conflict"]) or True)
