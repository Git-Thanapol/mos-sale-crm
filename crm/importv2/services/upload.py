"""Upload: validate a file by its CONTENT, pick the mapping profile, stage a batch.

08_SECURITY_AND_AUTH_REQUIREMENTS: the extension and the browser's MIME type are
operator conveniences, never a security boundary. The bytes decide: a `.xlsx`
must be a real workbook, a `.csv` must be UTF-8 text, and anything else is
refused before a batch exists. Nothing here writes business data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

from django.core.exceptions import PermissionDenied

from crm.core.permissions import can_import_v2
from crm.domain.mapping import normalize_header, validate_profile_against_sample
from crm.importv2.models import ImportBatch

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ROWS = 100_000
#: Rows sampled for the shape probes (prototype validateProfileAgainstSample).
SAMPLE_ROWS = 500
#: A file must match at least this share of a profile's header columns to be
#: treated as that format (a shifted or edited export still reaches validation).
MIN_HEADER_MATCH = 0.6

#: Source kinds the engine can stage, preview and commit. The others have a
#: profile (so they are recognised and validated) but need cross-file joins the
#: engine does not do yet; they are refused at Preview, never half-imported.
SUPPORTED_SOURCE_KINDS = ("CRM_CUSTOMERS",)

_PROFILES_PATH = Path(__file__).resolve().parents[1] / "profiles.json"


class UploadError(ValueError):
    """The file cannot become a batch. `code` is stable for the UI and tests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@lru_cache(maxsize=1)
def load_profiles() -> tuple[dict, ...]:
    return tuple(json.loads(_PROFILES_PATH.read_text(encoding="utf-8")))


def get_profile(profile_id: str, version: int | None = None) -> dict | None:
    for profile in load_profiles():
        if profile["profileId"] == profile_id and (version is None or profile["version"] == version):
            return profile
    return None


def _cell(value):
    """A spreadsheet cell as the mapping rules expect it: text, number or None.

    Date cells become `DD/MM/YYYY` (Gregorian), the form parse_thai_date reads;
    a Thai-year cell arrives as text and is left alone.
    """
    if isinstance(value, datetime):
        return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"
    if isinstance(value, date):
        return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"
    if isinstance(value, time):
        return f"{value.hour}:{value.minute:02d}"
    return value


def _trim_blank_tail(rows: list[list]) -> list[list]:
    while rows and all(v is None or (isinstance(v, str) and not v.strip()) for v in rows[-1]):
        rows.pop()
    return rows


def _read_xlsx(raw: bytes) -> dict:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as error:  # a zip that is not a workbook
        raise UploadError("UNSUPPORTED_FILE", "the file is not a readable .xlsx workbook") from error
    sheet = workbook.worksheets[0]
    rows: list[list] = []
    for index, values in enumerate(sheet.iter_rows(values_only=True)):
        if index > MAX_ROWS + 1:
            raise UploadError("TOO_MANY_ROWS", f"more than {MAX_ROWS} data rows")
        rows.append([_cell(v) for v in values])
    workbook.close()
    return _split_header(rows)


def _read_csv(raw: bytes) -> dict:
    if b"\x00" in raw[:4096]:
        raise UploadError("UNSUPPORTED_FILE", "the file is binary, not CSV text")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UploadError("UNSUPPORTED_FILE", "the CSV must be UTF-8") from error
    try:
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
    except csv.Error as error:
        raise UploadError("UNSUPPORTED_FILE", f"the CSV cannot be read: {error}") from error
    return _split_header(rows)


def _split_header(rows: list[list]) -> dict:
    rows = _trim_blank_tail(rows)
    if not rows:
        raise UploadError("EMPTY_FILE", "the file has no rows")
    headers = ["" if h is None else str(h) for h in rows[0]]
    data = rows[1:]
    if len(data) > MAX_ROWS:
        raise UploadError("TOO_MANY_ROWS", f"more than {MAX_ROWS} data rows")
    return {"headers": headers, "rows": data}


def read_table(raw: bytes, file_name: str) -> dict:
    """Parse the file into `{headers, rows}`, checking content against the name."""
    if not raw:
        raise UploadError("EMPTY_FILE", "the file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError("FILE_TOO_LARGE", f"the file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    extension = Path(file_name).suffix.lower()
    is_zip = raw[:4] == b"PK\x03\x04"
    if extension == ".xlsx":
        if not is_zip:
            raise UploadError("UNSUPPORTED_FILE", "the content is not an .xlsx workbook")
        return _read_xlsx(raw)
    if extension == ".csv":
        if is_zip:
            raise UploadError("UNSUPPORTED_FILE", "the content is a workbook, not CSV text")
        return _read_csv(raw)
    raise UploadError("UNSUPPORTED_FILE", "only .xlsx and .csv files are accepted")


def detect_profile(headers: list[str]) -> dict | None:
    """The profile whose header signature the file matches best, if close enough."""
    normalized = [normalize_header(h) for h in headers]
    best, best_score = None, 0.0
    for profile in load_profiles():
        signature = [normalize_header(h) for h in profile["headerSignature"]]
        hits = sum(1 for i, name in enumerate(signature) if i < len(normalized) and normalized[i] == name)
        score = hits / max(len(signature), len(normalized), 1)
        if score > best_score:
            best, best_score = profile, score
    return best if best_score >= MIN_HEADER_MATCH else None


def validation_for(profile: dict, table: dict) -> dict:
    result = validate_profile_against_sample(
        profile, {"headers": table["headers"], "rows": table["rows"][:SAMPLE_ROWS]}
    )
    if profile["sourceKind"] not in SUPPORTED_SOURCE_KINDS:
        result["issues"].append(
            {
                "severity": "BLOCK",
                "code": "SOURCE_NOT_SUPPORTED",
                "message": f"{profile['sourceKind']} files are recognised but cannot be imported yet: "
                "they need data from other files to be joined first.",
            }
        )
        result["ok"] = False
    return result


def create_batch(*, actor, file_name: str, raw: bytes) -> ImportBatch:
    """Stage a batch from an upload. EDITOR only; writes no business data."""
    if not can_import_v2(actor):
        raise PermissionDenied("เฉพาะ EDITOR เท่านั้นที่นำเข้าข้อมูลได้")
    table = read_table(raw, file_name)
    profile = detect_profile(table["headers"])
    if profile is None:
        raise UploadError(
            "UNKNOWN_FORMAT",
            "the columns do not match any known export format (crm-customers, litedetailorder, "
            "inoutmanagemain, tele-sales-report, cutover-order-sheet)",
        )
    safe_name = re.sub(r"[^\w.\- ()฀-๿]", "_", Path(file_name).name)[:255] or "upload"
    return ImportBatch.objects.create(
        file_name=safe_name,
        source_kind=profile["sourceKind"],
        mapping_profile_id=profile["profileId"],
        mapping_profile_version=profile["version"],
        file_sha256=hashlib.sha256(raw).hexdigest(),
        raw_bytes=raw,
        row_count=len(table["rows"]),
        validation=validation_for(profile, table),
        uploaded_by=actor,
    )
