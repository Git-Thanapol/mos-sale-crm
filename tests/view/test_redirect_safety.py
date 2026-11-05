"""The mutation views must not redirect off-site.

_back() honoured any next value starting with "/", and "//evil.com" is a
protocol-relative URL -- the browser leaves the site. HttpResponseRedirect only
rejects disallowed schemes, so Django does not catch it.
"""

import pytest
from django.urls import reverse

from tests.db.factories import ROLE_EDITOR, make_customer, make_lead, make_user

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"


@pytest.fixture
def editor():
    user = make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")
    user.set_password(PASSWORD)
    user.save()
    return user


BACKSLASH = chr(92)

# Two leading backslashes are the dangerous form: browsers normalise them to
# "//", making it protocol-relative and cross-origin. ONE leading backslash
# normalises to "/evil.com", which is same-origin and therefore fine -- so it
# is asserted as allowed rather than blocked.
HOSTILE = [
    "//evil.com",
    "https://evil.com/x",
    "////evil.com",
    BACKSLASH * 2 + "evil.com",
    "http://evil.com",
    "https:/" + BACKSLASH + "evil.com",
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_an_off_site_next_is_refused_and_falls_back_to_the_list(client, editor, hostile):
    lead = make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))
    assert client.login(email=editor.email, password=PASSWORD)
    response = client.post(
        reverse("leads:add_note", args=[lead.id]), {"body": "x", "next": hostile}
    )
    assert response.status_code in (302, 303)
    assert response["Location"] == reverse("followups:list"), f"leaked for {hostile!r}"


def test_a_single_backslash_path_is_same_origin_and_allowed(client, editor):
    # chr(92) + "evil.com" normalises to "/evil.com" in every browser, which
    # is a path on our own host. Blocking it would be harmless but wrong.
    lead = make_lead(customer=make_customer(_uid="0000003", staff_code="S0001"))
    assert client.login(email=editor.email, password=PASSWORD)
    response = client.post(
        reverse("leads:add_note", args=[lead.id]),
        {"body": "x", "next": BACKSLASH + "evil.com"},
    )
    assert response.status_code in (302, 303)


def test_a_same_site_next_is_honoured(client, editor):
    lead = make_lead(customer=make_customer(_uid="0000002", staff_code="S0001"))
    assert client.login(email=editor.email, password=PASSWORD)
    target = f"{reverse('followups:list')}?lead_section=my_work"
    response = client.post(
        reverse("leads:add_note", args=[lead.id]), {"body": "x", "next": target}
    )
    assert response["Location"] == target
