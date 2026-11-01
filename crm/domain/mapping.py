"""Versioned mapping profiles (port of src/import/mapping.ts).

Header names alone are not trusted. A profile records the header signature it
was built against *and* the expected value shape of each mapped column, so a
shifted export (order ids that suddenly look like phone numbers) is caught
before any preview is produced.

Profiles are the prototype's own dicts (generated into crm/importv2/profiles.json
from its MAPPING_PROFILES): `profileId`, `version`, `headerSignature` and
`columns[{columnIndex, header, canonicalField, transform, expect, required}]`.

Cell values arrive from a spreadsheet reader as str, int, float or None. Where
the prototype uses JavaScript's `Number()` and `String()`, the helpers here
reproduce them (including `Number("0x10") === 16` and `Number("1_0")` being NaN),
because the fixtures were produced by that behaviour.
"""

from __future__ import annotations

import math
import re
import unicodedata

from crm.domain.phone import looks_like_phone, normalize_phone
from crm.domain.thai_time import excel_serial_to_millis, parse_thai_date

_DECIMAL = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$")
_RADIX = re.compile(r"^0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$")
_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHORT_ID = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
_HH_MM = re.compile(r"^[0-9]{1,2}:[0-9]{2}$")
_JS_SPACE = "\u0009\u000a\u000b\u000c\u000d                  　﻿"


def js_string(value) -> str:
    """JavaScript String(value) for the cell types a spreadsheet yields."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == int(value) and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    return str(value)


def js_number(value) -> float:
    """JavaScript Number(value): NaN (math.nan) when it is not a number."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    text = str(value).strip(_JS_SPACE)
    if text == "":
        return 0.0
    if text in ("Infinity", "+Infinity"):
        return math.inf
    if text == "-Infinity":
        return -math.inf
    radix = _RADIX.match(text)
    if radix:
        return float(int(text, 0))
    if _DECIMAL.match(text):
        return float(text)
    return math.nan


def _is_finite(number: float) -> bool:
    return not (math.isnan(number) or math.isinf(number))


def _is_integer(number: float) -> bool:
    return _is_finite(number) and number == math.floor(number)


def _plain_number(number: float):
    """JSON-friendly: whole numbers as int, as JSON.stringify would write them."""
    return int(number) if number == int(number) and abs(number) < 1e21 else number


def normalize_header(header: str) -> str:
    text = unicodedata.normalize("NFC", header)
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub("[​-‍﻿]", "", text)
    return text.strip(_JS_SPACE)


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip(_JS_SPACE) == "")


def _matches_shape(expect: str, value) -> bool:
    text = js_string(value).strip(_JS_SPACE)
    if expect == "PHONE":
        return normalize_phone(text)["plausible"]
    if expect == "ORDER_ID":
        return bool(_ORDER_ID.match(text)) and not looks_like_phone(text)
    if expect == "DATE":
        return parse_thai_date(text) is not None or excel_serial_to_millis(js_number(text)) is not None
    if expect in ("NUMBER", "MONEY"):
        return _is_finite(js_number(text.replace(",", "")))
    if expect == "INTEGER":
        return _is_integer(js_number(text.replace(",", "")))
    if expect == "ENUM":
        return 0 < len(text) < 80
    return len(text) > 0


def validate_profile_against_sample(profile: dict, table: dict, min_samples: int = 3) -> dict:
    """Validate a profile against a concrete sample before it is used.

    `table` = {headers: [str], rows: [[cell]]}. BLOCK stops the import; WARNING is
    surfaced in the preview. Returns `{ok, issues}`.
    """
    issues: list[dict] = []
    headers = table["headers"]
    rows = table["rows"]
    signature = profile["headerSignature"]
    pid, version = profile["profileId"], profile["version"]

    if len(headers) != len(signature):
        issues.append(
            {
                "severity": "BLOCK",
                "code": "HEADER_COUNT_MISMATCH",
                "message": f"Sample has {len(headers)} columns but profile {pid}@{version} expects {len(signature)}.",
            }
        )
    else:
        for i, expected_raw in enumerate(signature):
            expected = normalize_header(expected_raw)
            actual = normalize_header(headers[i])
            if expected != actual:
                issues.append(
                    {
                        "severity": "BLOCK",
                        "code": "HEADER_SIGNATURE_MISMATCH",
                        "message": f'Column {i} is "{actual}" but the profile was built for "{expected}".',
                        "columnIndex": i,
                    }
                )

    # Real exports repeat header names; index-based mapping keeps that
    # unambiguous, but the duplication is surfaced so a name-based edit cannot
    # slip through.
    seen: dict[str, list[int]] = {}
    for i, header in enumerate(headers):
        seen.setdefault(normalize_header(header), []).append(i)
    for header, indexes in seen.items():
        if len(indexes) > 1:
            issues.append(
                {
                    "severity": "WARNING",
                    "code": "DUPLICATE_HEADER",
                    "message": f'Header "{header}" appears {len(indexes)} times (columns '
                    f"{', '.join(str(i) for i in indexes)}). Mapping is index-based, not name-based.",
                    "columnIndex": indexes[0],
                }
            )

    mapped = {c["columnIndex"] for c in profile["columns"]}
    for column in profile["columns"]:
        if column.get("required") and _is_blank(column.get("header")):
            issues.append(
                {
                    "severity": "BLOCK",
                    "code": "REQUIRED_FIELD_UNMAPPED",
                    "message": f'Required field "{column["canonicalField"]}" has no source column.',
                }
            )
        if column["columnIndex"] >= len(headers):
            issues.append(
                {
                    "severity": "BLOCK",
                    "code": "MAPPING_INDEX_OUT_OF_RANGE",
                    "message": f'Field "{column["canonicalField"]}" points at column '
                    f"{column['columnIndex']}, which the sample does not have.",
                    "columnIndex": column["columnIndex"],
                }
            )

    for i, header in enumerate(headers):
        if i not in mapped and len(normalize_header(header)) > 0:
            issues.append(
                {
                    "severity": "WARNING",
                    "code": "UNMAPPED_COLUMN",
                    "message": f'Column {i} ("{normalize_header(header)}") is not mapped to any canonical field.',
                    "columnIndex": i,
                }
            )

    # Data-shape probes: this is what actually catches shifted columns.
    for column in profile["columns"]:
        if column["expect"] in ("TEXT", "ENUM"):
            continue
        values = [
            row[column["columnIndex"]] if column["columnIndex"] < len(row) else None for row in rows
        ]
        values = [v for v in values if not _is_blank(v)][:500]
        if len(values) < min_samples:
            continue
        good = len([v for v in values if _matches_shape(column["expect"], v)])
        ratio = good / len(values)
        if ratio < 0.5:
            issues.append(
                {
                    "severity": "BLOCK",
                    "code": "SHAPE_MISMATCH",
                    "message": f'Column {column["columnIndex"]} ("{column["header"]}") is mapped as '
                    f'{column["canonicalField"]} ({column["expect"]}) but only {good}/{len(values)} '
                    "sampled values fit. The export may have shifted columns.",
                    "columnIndex": column["columnIndex"],
                }
            )
        elif ratio < 0.9:
            issues.append(
                {
                    "severity": "WARNING",
                    "code": "SHAPE_PARTIAL",
                    "message": f'Column {column["columnIndex"]} ("{column["header"]}") has '
                    f'{len(values) - good} sampled values that do not fit {column["expect"]}.',
                    "columnIndex": column["columnIndex"],
                }
            )

    id_column = next((c for c in profile["columns"] if c["canonicalField"] == "orderId"), None)
    phone_column = next((c for c in profile["columns"] if c["canonicalField"] == "phone"), None)
    if id_column and phone_column:

        def cell(row, column):
            return row[column["columnIndex"]] if column["columnIndex"] < len(row) else None

        id_values = [cell(r, id_column) for r in rows if not _is_blank(cell(r, id_column))][:200]
        phone_values = [cell(r, phone_column) for r in rows if not _is_blank(cell(r, phone_column))][:200]
        id_looks_phone = len([v for v in id_values if looks_like_phone(js_string(v))])
        phone_looks_id = len(
            [
                v
                for v in phone_values
                if _SHORT_ID.match(js_string(v).strip(_JS_SPACE)) and not looks_like_phone(js_string(v))
            ]
        )
        if id_looks_phone > 0 and phone_looks_id > 0:
            issues.append(
                {
                    "severity": "BLOCK",
                    "code": "SHIFTED_COLUMNS",
                    "message": "The orderId column contains phone-like values while the phone column "
                    "contains id-like values: the source columns are probably shifted.",
                }
            )

    return {"ok": not any(i["severity"] == "BLOCK" for i in issues), "issues": issues}


def apply_mapping(profile: dict, row: list) -> dict:
    """Apply the profile's transforms to one raw row. Never coerces silently.

    Returns `{values: {canonicalField: value}, errors: [{canonicalField, message}]}`.
    A field whose conversion failed is absent from `values`.
    """
    values: dict = {}
    errors: list[dict] = []
    for column in profile["columns"]:
        field = column["canonicalField"]
        raw = row[column["columnIndex"]] if column["columnIndex"] < len(row) else None
        if _is_blank(raw):
            values[field] = None
            continue
        text = js_string(raw).strip(_JS_SPACE)
        transform = column.get("transform", "IDENTITY")
        if transform == "THAI_DATE":
            parsed = parse_thai_date(text)
            if parsed is None:
                errors.append({"canonicalField": field, "message": f'not a Thai date: "{text}"'})
            else:
                values[field] = parsed
        elif transform == "EXCEL_SERIAL":
            parsed = excel_serial_to_millis(js_number(raw))
            if parsed is None:
                parsed = parse_thai_date(text)
            if parsed is None:
                errors.append({"canonicalField": field, "message": f'not an Excel serial: "{text}"'})
            else:
                values[field] = parsed
        elif transform == "PHONE":
            values[field] = text
            if not normalize_phone(text)["plausible"]:
                errors.append({"canonicalField": field, "message": f'not a plausible phone: "{text}"'})
        elif transform in ("NUMBER", "MONEY"):
            number = js_number(text.replace(",", ""))
            if not _is_finite(number):
                errors.append({"canonicalField": field, "message": f'not a number: "{text}"'})
            else:
                values[field] = _plain_number(number)
        elif transform == "INTEGER":
            number = js_number(text.replace(",", ""))
            if not _is_integer(number):
                errors.append({"canonicalField": field, "message": f'not an integer: "{text}"'})
            else:
                values[field] = _plain_number(number)
        elif transform == "HH_MM":
            values[field] = text
            if not _HH_MM.match(text):
                errors.append({"canonicalField": field, "message": f'not HH:mm: "{text}"'})
        else:
            values[field] = text
    for column in profile["columns"]:
        if column.get("required") and _is_blank(values.get(column["canonicalField"])):
            errors.append({"canonicalField": column["canonicalField"], "message": "required value is blank"})
    return {"values": values, "errors": errors}
