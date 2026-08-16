from pathlib import Path

import pytest

from crm.core.thai import (
    DEFAULT_FOLLOWUP_PRIORITY,
    FOLLOWUP_PRIORITY_OPTIONS,
    LEGACY_FOLLOWUP_PRIORITY_MAP,
    LEGACY_FOLLOWUP_STATUS_MAP,
    PRIORITY_RANK_UNKNOWN,
    normalize_followup_status,
    normalize_priority,
    priority_rank,
)

pytestmark = pytest.mark.unit


def test_priority_canonical_options_and_default():
    assert FOLLOWUP_PRIORITY_OPTIONS == ("Super VIP", "VIP", "Premium", "Economy", "NEW", "Dismiss")
    assert DEFAULT_FOLLOWUP_PRIORITY == "NEW"
    assert normalize_priority(None) == "NEW"
    assert normalize_priority("") == "NEW"
    assert normalize_priority("nonsense") == "NEW"


@pytest.mark.parametrize(
    "legacy,expected",
    [
        ("urgent", "Super VIP"),
        ("ด่วนมาก", "Super VIP"),
        ("high", "VIP"),
        ("สูง", "VIP"),
        ("normal", "NEW"),
        ("ปกติ", "NEW"),
        ("low", "Economy"),
        ("ต่ำ", "Economy"),
    ],
)
def test_legacy_priority_aliases_map(legacy, expected):
    assert LEGACY_FOLLOWUP_PRIORITY_MAP[legacy] == expected
    assert normalize_priority(legacy) == expected


def test_priority_rank_ordering():
    assert priority_rank("Super VIP") == 6
    assert priority_rank("VIP") == 5
    assert priority_rank("Premium") == 4
    assert priority_rank("Economy") == 3
    assert priority_rank("NEW") == 2
    assert priority_rank("Dismiss") == 0
    assert priority_rank("something-unrecognized-but-not-a-legacy-alias") == PRIORITY_RANK_UNKNOWN
    # legacy aliases resolve to the canonical tier they map to — this is
    # what fixes the CP874 mojibake bug where sort and filter used to disagree.
    assert priority_rank("สูง") == priority_rank("VIP") == 5
    assert priority_rank("ด่วนมาก") == priority_rank("Super VIP") == 6
    # blank/None mirrors the legacy coalesce(priority, 'NEW') that ran
    # before the SQL CASE — NOT the same code path as a genuinely
    # unrecognized non-empty string, which must stay PRIORITY_RANK_UNKNOWN.
    assert priority_rank(None) == priority_rank("NEW") == 2
    assert priority_rank("") == 2


def test_no_mojibake_in_priority_literals():
    """The legacy ORDER BY held CP874-corrupted UTF-8 bytes for ด่วนมาก/สูง/
    ต่ำ/ปกติ that could never match a stored value. Guard against that class
    of bug recurring anywhere in this module.
    """
    import crm.core.thai as thai_module

    source = Path(thai_module.__file__).read_text(encoding="utf-8")
    mojibake_markers = ("เธ", "เน")
    for marker in mojibake_markers:
        assert marker not in source, f"mojibake byte sequence {marker!r} found in crm/core/thai.py"


@pytest.mark.parametrize(
    "legacy,expected",
    [
        ("", "none"),
        ("none", "none"),
        ("scheduled", "scheduled"),
        ("round_1", "round_1"),
        ("round_2", "round_2"),
        ("round_3", "round_3"),
        ("round_4", "round_4"),
        ("done", "done"),
        ("missed", "missed"),
        ("0", "none"),
        ("1", "round_1"),
        ("2", "round_2"),
        ("3", "round_3"),
        ("RESET", "none"),
    ],
)
def test_legacy_followup_status_map_covers_both_vocabularies(legacy, expected):
    assert normalize_followup_status(legacy) == expected


def test_legacy_followup_status_map_has_no_gaps_for_known_legacy_values():
    known_legacy_values = {"", "none", "scheduled", "round_1", "round_2", "round_3", "round_4",
                            "done", "missed", "0", "1", "2", "3", "reset"}
    assert known_legacy_values <= set(LEGACY_FOLLOWUP_STATUS_MAP.keys())
