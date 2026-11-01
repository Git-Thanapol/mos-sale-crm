"""Shared pieces of the lead pages: wrapper, chip tones, date format, partials.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_shell.py
"""

from datetime import datetime, timezone

import pytest
from django.template import Context, Template
from django.urls import reverse

from tests.db.factories import ROLE_EDITOR, make_user

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"

#: Every leadui page route that can render without a pk; later tasks add theirs.
PAGE_ROUTES = ["leadui:my_work"]


def render(source, **context):
    return Template("{% load v2 %}" + source).render(Context(context))


def login(client, user):
    user.set_password(PASSWORD)
    user.save()
    assert client.login(email=user.email, password=PASSWORD)
    return user


@pytest.fixture
def editor():
    return make_user(email="editor@example.com", role=ROLE_EDITOR, staff_code="E001")


@pytest.mark.parametrize(
    ("value", "tone"),
    [
        ("OVERDUE", "danger"),
        ("STOPPED", "danger"),
        ("FAILED_RETRYABLE", "danger"),
        ("BLOCKING", "danger"),
        ("APPOINTMENT_TODAY", "warn"),
        ("PAUSED_QUOTA", "warn"),
        ("WARNING", "warn"),
        ("COMMITTED", "ok"),
        ("ASSIGNED", "ok"),
        ("DONE", "ok"),
        ("FOLLOWING", "neutral"),
        ("NEW", "neutral"),
        ("", "neutral"),
    ],
)
def test_chip_tone_matches_the_prototype(value, tone):
    # StatusChip, App.tsx:34-37: danger is tested first, then warn, then ok.
    assert render("{% v2_chip value %}", value=value) == (
        f'<span class="chip {tone}">{value}</span>'
    )


def test_chip_escapes_its_value():
    assert "&lt;script&gt;" in render("{% v2_chip value %}", value="<script>")


def test_bangkok_formats_like_the_prototype_th_TH_medium():
    moment = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    assert render("{{ moment|bangkok }}", moment=moment) == "26 ต.ค. 2569 10:00"


def test_bangkok_does_not_zero_pad_the_day():
    moment = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
    assert render("{{ moment|bangkok }}", moment=moment) == "5 พ.ย. 2569 10:00"


def test_bangkok_crosses_midnight_into_the_next_bangkok_day():
    moment = datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc)  # 01:30 on the 27th
    assert render("{{ moment|bangkok }}", moment=moment) == "27 ต.ค. 2569 01:30"


def test_bangkok_none_is_a_dash_never_the_word_None():
    assert render("{{ moment|bangkok }}", moment=None) == "—"


def test_bangkok_short_matches_the_age_column_format():
    moment = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)
    assert render("{{ moment|bangkok_short }}", moment=moment) == "10/10/69"


def test_page_title_partial():
    html = render('{% include "leadui/_page_title.html" with title="หัวข้อ" subtitle="คำอธิบาย" %}')
    assert '<header class="page-title"><div><h1>หัวข้อ</h1><p>คำอธิบาย</p></div></header>' in html


def test_state_partial_empty_default_and_error():
    empty = render('{% include "leadui/_state.html" with kind="empty" %}')
    assert 'class="state-box"' in empty and "ยังไม่มีรายการในคิวนี้" in empty
    error = render('{% include "leadui/_state.html" with kind="error" message="ล้มเหลว" %}')
    assert 'class="state-box error"' in error and "ล้มเหลว" in error


def test_pager_partial_renders_only_the_links_it_is_given():
    both = render('{% include "leadui/_pager.html" with first_url="?a=1" next_url="?b=2" %}')
    assert "หน้าแรก" in both and "หน้าถัดไป" in both
    only_next = render('{% include "leadui/_pager.html" with next_url="?b=2" %}')
    assert "หน้าถัดไป" in only_next and "หน้าแรก" not in only_next
    neither = render('{% include "leadui/_pager.html" %}')
    assert "หน้าแรก" not in neither and "หน้าถัดไป" not in neither


def test_a_page_requires_login(client):
    response = client.get(reverse("leadui:my_work"))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_base_v2_wraps_content_in_the_v2_div_and_loads_its_assets(client, editor):
    login(client, editor)
    body = client.get(reverse("leadui:my_work")).content.decode()
    assert '<div class="v2">' in body
    assert "crm/css/v2-followup.css" in body
    assert "crm/js/v2-filters.js" in body


@pytest.mark.parametrize("route", PAGE_ROUTES)
def test_no_template_comment_markers_leak_into_the_page(client, editor, route):
    # Django's {# #} is single-line only: a multi-line one renders verbatim.
    login(client, editor)
    body = client.get(reverse(route)).content.decode()
    for marker in ("{#", "#}", "{% comment %}", "{% endcomment %}"):
        assert marker not in body, f"{marker!r} leaked into {route}"


def test_messages_render_as_a_v2_toast_not_the_generic_card(client, editor):
    login(client, editor)
    session = client.session
    session.save()
    from django.contrib.messages import constants
    from django.contrib.messages.storage.cookie import CookieStorage
    from django.http import HttpRequest

    request = HttpRequest()
    request.COOKIES = {}
    storage = CookieStorage(request)
    storage.add(constants.SUCCESS, "บันทึกแล้ว")
    response = type("R", (), {"set_cookie": lambda self, k, v, **kw: client.cookies.__setitem__(k, v),
                              "delete_cookie": lambda self, *a, **k: None})()
    storage.update(response)
    body = client.get(reverse("leadui:my_work")).content.decode()
    assert '<div class="toast">' in body and "บันทึกแล้ว" in body
    assert 'class="crm-card" style="margin-bottom:16px;' not in body
