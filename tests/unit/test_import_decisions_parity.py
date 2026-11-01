"""Parity of the Migration Decision rules with the CRM V2 prototype
(scripts/export_crmv2_decision_fixtures.mts; expected values come from
EXECUTING the prototype).

Run via: docker compose exec -T web python -m pytest tests/unit/test_import_decisions_parity.py
"""

import json
from pathlib import Path

import pytest

from crm.domain import migration_decisions as md

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "crmv2"
pytestmark = pytest.mark.unit

CASES = json.loads((FIXTURES / "import_decisions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", [pytest.param(c, id=c["name"]) for c in CASES])
def test_decision_rules_match_the_prototype(case):
    spec, expected = case["input"], case["expected"]
    fn = spec["fn"]
    if fn == "allowedActions":
        assert list(md.allowed_actions(spec["category"], spec["reasonCode"])) == expected["value"]
    elif fn == "nextStatusFor":
        assert md.next_status_for(spec["action"]) == expected["value"]
    elif fn == "maskPhone":
        assert md.mask_phone(spec["value"]) == expected["value"]
    elif fn == "evidenceHashOf":
        assert md.evidence_hash_of(spec["category"], spec["subjectKey"], spec["evidence"]) == expected["value"]
    elif fn == "decisionIdFor":
        assert md.decision_id_for(spec["category"], spec["snapshot"], spec["subjectKey"]) == expected["value"]
    elif fn == "validateResolution":
        users = spec["users"]
        lookup = lambda user_id: users.get(user_id)  # noqa: E731
        try:
            got = md.validate_resolution(spec["decision"], spec["request"], lookup)
        except md.DecisionValidationError as error:
            assert "error" in expected, f"unexpected {error.code}"
            assert error.code == expected["error"]["code"]
        else:
            assert "value" in expected, f"expected {expected['error']}, got {got!r}"
            assert got == expected["value"]
    else:
        raise AssertionError(fn)


def test_every_decision_action_belongs_to_exactly_the_prototype_categories():
    assert set(md.DECISION_ACTIONS) == set(md.DECISION_CATEGORIES)
    assert md.UNRESOLVED_DECISION_STATUSES == ("OPEN", "DEFERRED")
