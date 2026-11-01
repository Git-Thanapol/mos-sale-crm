"""Parity of Thai time parsing, the status map and mapping profiles with the
CRM V2 prototype (fixtures from scripts/export_crmv2_mapping_fixtures.mts).

The profiles under test are crm/importv2/profiles.json, which that same script
generated from the prototype's own MAPPING_PROFILES.

Run via: docker compose exec -T web python -m pytest tests/unit/test_import_mapping_parity.py
"""

import json
import math
from pathlib import Path

import pytest

from crm.domain import mapping, status_map, thai_time

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "crmv2"
PROFILES = {p["profileId"]: p for p in json.loads((ROOT / "crm" / "importv2" / "profiles.json").read_text("utf-8"))}

pytestmark = pytest.mark.unit


def load(name):
    cases = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [pytest.param(case, id=case["name"]) for case in cases]


@pytest.mark.parametrize("case", load("import_thai_time"))
def test_thai_time_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "parseThaiDate":
        assert thai_time.parse_thai_date(spec["text"]) == expected
    elif fn == "excelSerialToMillis":
        serial = spec["serial"]
        serial = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}.get(serial, serial)
        assert thai_time.excel_serial_to_millis(serial) == expected
    elif fn == "bangkokTimeOnDay":
        assert thai_time.bangkok_time_on_day(spec["dayKey"], spec["hhmm"]) == expected
    elif fn == "bangkokDayKey":
        assert thai_time.bangkok_day_key(spec["ts"]) == expected
    elif fn == "startOfBangkokDay":
        assert thai_time.start_of_bangkok_day(spec["ts"]) == expected
    else:
        raise AssertionError(fn)


@pytest.mark.parametrize("case", load("import_status_map"))
def test_status_map_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "mapSourceStatus":
        assert status_map.map_source_status(spec["raw"]) == expected
    elif fn == "mapPaymentMethod":
        assert status_map.map_payment_method(spec["raw"]) == expected
    elif fn == "mapShippingText":
        assert status_map.map_shipping_text(spec["raw"]) == expected
    elif fn == "knownSourceStatuses":
        assert status_map.known_source_statuses() == expected
    else:
        raise AssertionError(fn)


@pytest.mark.parametrize("case", load("import_mapping"))
def test_mapping_matches_the_prototype(case):
    spec, expected = case["input"], case["expected"]["value"]
    fn = spec["fn"]
    if fn == "normalizeHeader":
        assert mapping.normalize_header(spec["header"]) == expected
    elif fn == "validateProfileAgainstSample":
        options = spec["options"] or {}
        got = mapping.validate_profile_against_sample(
            PROFILES[spec["profileId"]], spec["table"], min_samples=options.get("minSamples", 3)
        )
        assert got == expected
    elif fn == "applyMapping":
        assert mapping.apply_mapping(PROFILES[spec["profileId"]], spec["row"]) == expected
    else:
        raise AssertionError(fn)


def test_the_five_profiles_are_loaded():
    assert sorted(PROFILES) == [
        "crm-customers",
        "cutover-order-sheet",
        "inoutmanagemain",
        "litedetailorder",
        "tele-sales-report",
    ]
