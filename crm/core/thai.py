"""Thai vocabulary: priority tiers and follow-up status, canonicalized.

This is where two legacy defects die (see docs/DECISIONS.md #9, #10):

- FOLLOWUP_PRIORITY_RANK replaces the CP874-mojibake Thai literals that sat
  in the legacy ORDER BY (data-layer-report.md §"Bug worth carrying...").
  No Thai literal is ever compared or sorted directly again — always resolve
  through PRIORITY_RANK first.
- FOLLOWUP_STATUS_CANONICAL is the ONE vocabulary the new schema stores.
  The legacy dual vocabulary (none/scheduled/round_1..4/done/missed vs
  0/1/2/3/RESET) is mapped down to this at write time by the ETL/importer;
  nothing downstream needs to know the old value ever existed except via
  the audit field that keeps it.
"""

from __future__ import annotations

FOLLOWUP_PRIORITY_OPTIONS: tuple[str, ...] = ("Super VIP", "VIP", "Premium", "Economy", "NEW", "Dismiss")
DEFAULT_FOLLOWUP_PRIORITY = "NEW"

# Exact port of LEGACY_FOLLOWUP_PRIORITY_MAP, neon_utils.py:31-50
LEGACY_FOLLOWUP_PRIORITY_MAP: dict[str, str] = {
    "urgent": "Super VIP",
    "ด่วนมาก": "Super VIP",
    "high": "VIP",
    "สูง": "VIP",
    "normal": "NEW",
    "ปกติ": "NEW",
    "low": "Economy",
    "ต่ำ": "Economy",
}

# Stored as crm_followup.priority_rank (smallint). Higher sorts first.
# Values match the legacy ORDER BY's intended (but mojibake-broken) weights.
PRIORITY_RANK: dict[str, int] = {
    "Super VIP": 6,
    "VIP": 5,
    "Premium": 4,
    "Economy": 3,
    "NEW": 2,
    "Dismiss": 0,
}
PRIORITY_RANK_UNKNOWN = 1


def normalize_priority(value: str | None) -> str:
    text = (value or "").strip()
    if text in FOLLOWUP_PRIORITY_OPTIONS:
        return text
    mapped = LEGACY_FOLLOWUP_PRIORITY_MAP.get(text)
    return mapped or DEFAULT_FOLLOWUP_PRIORITY


def priority_rank(value: str | None) -> int:
    """Sort weight for a priority value. NOT routed through
    normalize_priority(): that function collapses any unrecognized string to
    the NEW default, which would make PRIORITY_RANK_UNKNOWN unreachable and
    erase the legacy SQL's `else 1` fallback for a genuinely garbage stored
    value. Blank/None still defaults to NEW's rank, matching the legacy
    `coalesce(priority, 'NEW')` that ran before its CASE expression.
    """
    text = (value or "").strip()
    if text in PRIORITY_RANK:
        return PRIORITY_RANK[text]
    mapped = LEGACY_FOLLOWUP_PRIORITY_MAP.get(text)
    if mapped:
        return PRIORITY_RANK[mapped]
    if not text:
        return PRIORITY_RANK[DEFAULT_FOLLOWUP_PRIORITY]
    return PRIORITY_RANK_UNKNOWN


# Canonical follow-up status vocabulary (crm_followup.status). See
# docs/DECISIONS.md #10 for the two legacy vocabularies this replaces.
FOLLOWUP_STATUS_OPTIONS: tuple[str, ...] = (
    "none",
    "scheduled",
    "round_1",
    "round_2",
    "round_3",
    "round_4",
    "done",
    "missed",
)
DEFAULT_FOLLOWUP_STATUS = "none"

# Legacy value -> canonical, covering BOTH vocabularies that wrote
# crm_lead_followups.followup_status in the legacy schema.
LEGACY_FOLLOWUP_STATUS_MAP: dict[str, str] = {
    "": "none",
    "none": "none",
    "scheduled": "scheduled",
    "round_1": "round_1",
    "round_2": "round_2",
    "round_3": "round_3",
    "round_4": "round_4",
    "done": "done",
    "missed": "missed",
    # pages/customers.py's numeric/RESET vocabulary
    "0": "none",
    "1": "round_1",
    "2": "round_2",
    "3": "round_3",
    "reset": "none",
}


def normalize_followup_status(value: str | None) -> str:
    text = (value or "").strip().lower()
    return LEGACY_FOLLOWUP_STATUS_MAP.get(text, DEFAULT_FOLLOWUP_STATUS)


LEAD_STATUS_OPTIONS: tuple[str, ...] = ("new", "contacted", "interested", "follow_up", "won", "lost", "dormant")
DEFAULT_LEAD_STATUS = "new"

# Thai display labels, verbatim from crm_streamlit/pages/followup.py
LEAD_STATUS_LABELS: dict[str, str] = {
    "new": "ลูกค้าใหม่",
    "contacted": "ติดต่อแล้ว",
    "interested": "สนใจ",
    "follow_up": "ต้องติดตาม",
    "won": "ปิดการขายแล้ว",
    "lost": "ไม่สนใจ/หลุด",
    "dormant": "ลูกค้าเงียบ",
}
FOLLOWUP_STATUS_LABELS: dict[str, str] = {
    "none": "ยังไม่ตั้งติดตาม",
    "scheduled": "นัดติดตาม",
    "round_1": "ติดตามรอบ 1",
    "round_2": "ติดตามรอบ 2",
    "round_3": "ติดตามรอบ 3",
    "round_4": "ติดตามรอบ 4",
    "done": "ติดตามแล้ว",
    "missed": "เลยกำหนด",
}
PRIORITY_TONE: dict[str, str] = {
    "Super VIP": "red",
    "VIP": "orange",
    "Premium": "blue",
    "Economy": "gray",
    "NEW": "yellow",
    "Dismiss": "gray",
}

UNASSIGNED_OWNER_LABEL = "ยังไม่มอบหมาย"
ALL_OPTION_LABEL = "ทั้งหมด"
