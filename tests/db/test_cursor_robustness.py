"""A structurally valid cursor whose timestamp will not parse must not 500.

decode_cursor's contract is that it never raises: cursors live in URLs that get
truncated, shared and hand-edited, so degrading to the first page is the
correct behaviour. Shape validation alone was not enough -- base64 of
["not-a-date", 5] passed the shape check and then blew up in fromisoformat.
"""

import pytest

from crm.core.pagination import decode_cursor, encode_cursor
from crm.leads.selectors import global_unassigned_queue, lead_queue
from tests.db.factories import ROLE_EDITOR, make_customer, make_lead, make_user

pytestmark = [pytest.mark.db, pytest.mark.django_db]


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


def test_a_valid_shape_with_an_unparseable_timestamp_decodes_to_none():
    assert decode_cursor(encode_cursor(("not-a-date", 5))) is None


def test_the_lead_queue_survives_an_unparseable_cursor(editor):
    make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))
    bad = encode_cursor(("not-a-date", 5))
    page = lead_queue(editor, after_cursor=bad, limit=10)
    assert len(page.items) == 1


def test_the_intake_queue_survives_an_unparseable_cursor(editor):
    bad = encode_cursor(("2026-13-45T99:99:99", 5))
    assert global_unassigned_queue(editor, after_cursor=bad, limit=10).items == []


def test_a_real_cursor_still_works(editor):
    for i in range(4):
        make_lead(customer=make_customer(_uid=f"{i + 1:07d}", staff_code="S0001"))
    first = lead_queue(editor, limit=2)
    assert first.has_more
    assert len(lead_queue(editor, after_cursor=first.next_cursor, limit=2).items) == 2
