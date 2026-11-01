"""D3 Batch Business Date (port of src/import/tele-date.ts).

The Tele sales report carries only a time of day (`HH:mm`), never a date. A file
named `Tele 26.10.69.xlsx` therefore *suggests* 2026-10-26, but the filename is
not authoritative: an operator must see the batch business date and explicitly
confirm or correct it before a time can become `ordered_at`.

 - a filename date is only a proposal, never silently accepted;
 - an ambiguous or unusable filename leaves the batch blocked until an
   operator supplies the date;
 - evidence inside the sheet can veto a filename date;
 - `ordered_at` is built from the confirmed day plus the source time in
   Asia/Bangkok; upload time, file mtime and sheet-entry time are never used.

Times are epoch milliseconds, as in the prototype. Day keys are `YYYY-MM-DD`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone

from crm.domain.import_errors import BatchBusinessDateError

_THAI_YEAR_OFFSET = 543
_BANGKOK_OFFSET_MS = 7 * 60 * 60 * 1000

# JavaScript's \d is ASCII-only; Python's would also match Thai digits.
_ISO = re.compile(r"(?<![0-9])([0-9]{4})[-_.]([0-9]{1,2})[-_.]([0-9]{1,2})(?![0-9])")
_DMY = re.compile(r"(?<![0-9])([0-9]{1,2})[./_-]([0-9]{1,2})[./_-]([0-9]{2}|[0-9]{4})(?![0-9])")
_DAY_KEY = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_TIME = re.compile(r"^([0-9]{1,2}):([0-9]{2})$")


def day_key_of(year, month, day) -> str | None:
    """Gregorian day key, or None when the triple is not a real calendar date."""
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (year, month, day)):
        # JS Number.isInteger accepts 2026.0; the fixtures only pass real ints.
        if not all(isinstance(v, (int, float)) and float(v).is_integer() for v in (year, month, day)):
            return None
        year, month, day = int(year), int(month), int(day)
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_year(year: int, digits: int) -> int | None:
    """Two-digit years: above 50 are Buddhist-era (69 -> 2569 -> 2026), the rest
    Gregorian 2000s (26 -> 2026). Four-digit Buddhist years (> 2400) are
    converted; four-digit Gregorian years are kept."""
    if digits == 4:
        if year > 2400:
            gregorian = year - _THAI_YEAR_OFFSET
            return gregorian if 1900 <= gregorian <= 2200 else None
        return year if 1900 <= year <= 2200 else None
    if year < 0 or year > 99:
        return None
    gregorian = 2500 + year - _THAI_YEAR_OFFSET if year > 50 else 2000 + year
    return gregorian if 2000 <= gregorian <= 2099 else None


def _find_dates(text: str) -> list[dict]:
    found: list[dict] = []
    text = unicodedata.normalize("NFC", text)
    # ISO first, so `2026-10-26` is not also read as day/month/year.
    for match in _ISO.finditer(text):
        year = normalize_year(int(match.group(1)), 4)
        key = None if year is None else day_key_of(year, int(match.group(2)), int(match.group(3)))
        if key:
            found.append({"day_key": key, "raw": match.group(0)})
    for match in _DMY.finditer(text):
        digits = 4 if len(match.group(3)) == 4 else 2
        year = normalize_year(int(match.group(3)), digits)
        key = None if year is None else day_key_of(year, int(match.group(2)), int(match.group(1)))
        if key:
            found.append({"day_key": key, "raw": match.group(0)})
    return found


def parse_filename_date(file_name: str) -> dict:
    """Propose a business date from a filename. Never returns an accepted value."""
    base = re.sub(r"^.*[\\/]", "", file_name)
    dates = _find_dates(base)
    if not dates:
        return {"day_key": None, "ambiguous": False, "raw": None, "reason": "the filename contains no date"}
    distinct = list(dict.fromkeys(d["day_key"] for d in dates))
    if len(distinct) > 1:
        return {
            "day_key": None,
            "ambiguous": True,
            "raw": dates[0]["raw"],
            "reason": f"the filename contains conflicting dates ({', '.join(distinct)})",
        }
    return {"day_key": dates[0]["day_key"], "ambiguous": False, "raw": dates[0]["raw"],
            "reason": "date parsed from the filename"}


def normalize_day_key(text: str) -> str | None:
    """Accept `YYYY-MM-DD` and validate that it is a real day."""
    match = _DAY_KEY.match(text.strip())
    if not match:
        return None
    year = normalize_year(int(match.group(1)), 4)
    if year is None:
        return None
    return day_key_of(year, int(match.group(2)), int(match.group(3)))


def propose_batch_business_date(file_name: str, source_evidence_day_keys=None) -> dict:
    """Build an *unconfirmed* proposal. Blockers explain why the operator must
    supply a date instead of accepting the suggestion."""
    parsed = parse_filename_date(file_name)
    blockers: list[str] = []
    if not parsed["day_key"]:
        blockers.append(
            f"AMBIGUOUS_FILENAME_DATE: {parsed['reason']}" if parsed["ambiguous"] else "NO_DATE_IN_FILENAME"
        )
    if parsed["day_key"]:
        evidence = [k for k in (source_evidence_day_keys or []) if k]
        if evidence and parsed["day_key"] not in evidence:
            blockers.append(
                f"FILENAME_DATE_CONFLICTS_WITH_SOURCE: filename suggests {parsed['day_key']} "
                f"but the file contains {', '.join(dict.fromkeys(evidence))}"
            )
    candidate = None
    if parsed["day_key"]:
        candidate = {
            "day_key": parsed["day_key"],
            "timezone": "Asia/Bangkok",
            "source": "FILENAME",
            "confirmed_by": None,
            "confirmed_at": None,
            "filename_candidate": parsed["day_key"],
            "filename_ambiguous": False,
        }
    return {"candidate": candidate, "blockers": blockers}


def confirm_batch_business_date(
    file_name: str,
    actor: str,
    at: int,
    operator_day_key: str | None = None,
    source_evidence_day_keys=None,
) -> dict:
    """Require an explicit operator confirmation.

    Passing `operator_day_key` overrides a filename suggestion (and rescues an
    unusable filename), but the result is always attributed to a named operator.
    """
    proposal = propose_batch_business_date(file_name, source_evidence_day_keys)
    chosen = (operator_day_key or "").strip() or None
    if not chosen:
        if proposal["blockers"]:
            raise BatchBusinessDateError(
                "BATCH_DATE_REQUIRED",
                f"the batch business date must be supplied by an operator ({'; '.join(proposal['blockers'])})",
            )
        raise BatchBusinessDateError(
            "BATCH_DATE_NOT_CONFIRMED",
            "the filename date is only a suggestion; an operator must confirm it explicitly",
        )
    day_key = normalize_day_key(chosen)
    if not day_key:
        raise BatchBusinessDateError("INVALID_BATCH_DATE", f"not a calendar date: {chosen}")

    # An operator may correct a bad filename, but not contradict the sheet itself.
    evidence = [k for k in (source_evidence_day_keys or []) if k]
    if evidence and day_key not in evidence:
        raise BatchBusinessDateError(
            "BATCH_DATE_CONFLICTS_WITH_SOURCE",
            f"the file contains {', '.join(dict.fromkeys(evidence))}, so {day_key} cannot be "
            "confirmed without resolving the conflict",
        )
    candidate = proposal["candidate"]
    return {
        "day_key": day_key,
        "timezone": "Asia/Bangkok",
        "source": "FILENAME" if candidate and candidate["day_key"] == day_key else "OPERATOR",
        "confirmed_by": actor,
        "confirmed_at": at,
        "filename_candidate": candidate["day_key"] if candidate else None,
        "filename_ambiguous": any(b.startswith("AMBIGUOUS_FILENAME_DATE") for b in proposal["blockers"]),
    }


def assert_confirmed_batch_date(value: dict | None) -> dict:
    """A batch may only commit with a confirmed date; raises otherwise."""
    if not value:
        raise BatchBusinessDateError("BATCH_DATE_REQUIRED", "this source cannot supply orderedAt on its own")
    if not value.get("confirmed_by") or value.get("confirmed_at") is None:
        raise BatchBusinessDateError(
            "BATCH_DATE_NOT_CONFIRMED", "an operator has not confirmed the batch business date"
        )
    if not normalize_day_key(value["day_key"]):
        raise BatchBusinessDateError("INVALID_BATCH_DATE", f"not a calendar date: {value['day_key']}")
    return value


def build_ordered_at(day_key: str, time_text: str) -> int:
    """Combine the confirmed day with a source time (epoch ms). `HH:mm` and
    `H:mm` are accepted; anything else is rejected rather than guessed."""
    day = normalize_day_key(day_key)
    if not day:
        raise BatchBusinessDateError("INVALID_BATCH_DATE", f"not a calendar date: {day_key}")
    match = _TIME.match(time_text.strip())
    if not match:
        raise BatchBusinessDateError("INVALID_TIME", f'not HH:mm: "{time_text}"')
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        raise BatchBusinessDateError("INVALID_TIME", f'not a time of day: "{time_text}"')
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000) + (hours * 60 + minutes) * 60 * 1000 - _BANGKOK_OFFSET_MS
