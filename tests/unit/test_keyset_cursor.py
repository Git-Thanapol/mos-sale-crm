"""Opaque keyset cursors.

A malformed cursor decodes to None rather than raising: these end up in URLs
that get hand-edited, shared and truncated, and a 500 on a mangled querystring
is worse than quietly starting from the first page.
"""

import pytest

from crm.core.pagination import decode_cursor, encode_cursor

pytestmark = pytest.mark.unit


def test_cursor_round_trips():
    assert decode_cursor(encode_cursor(("2026-11-01T00:00:00+00:00", 42))) == [
        "2026-11-01T00:00:00+00:00",
        42,
    ]


def test_a_null_first_component_round_trips():
    assert decode_cursor(encode_cursor((None, 7))) == [None, 7]


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-base64!!", "YWJj", "eyJhIjoxfQ==", None, "=" * 10],
)
def test_malformed_cursor_decodes_to_none_instead_of_raising(raw):
    assert decode_cursor(raw) is None


def test_a_truncated_cursor_decodes_to_none():
    good = encode_cursor(("2026-11-01T00:00:00+00:00", 42))
    assert decode_cursor(good[:-4]) is None


def test_the_cursor_is_url_safe():
    cursor = encode_cursor(("2026-11-01T00:00:00+00:00", 999999))
    assert "+" not in cursor
    assert "/" not in cursor
