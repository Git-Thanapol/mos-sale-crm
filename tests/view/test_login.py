"""Phase 7 hardening: login/logout/session had zero test coverage before
this file — every other view test force-logs-in directly and never
exercises CrmLoginView, ForcePasswordChangeMiddleware, or Django's
built-in is_active rejection (docs/DECISIONS.md: deactivated users must
be rejected outright, not silently downgraded like legacy).
"""

import pytest
from django.urls import reverse

from crm.accounts.models import User

pytestmark = [pytest.mark.view, pytest.mark.django_db]


def _make_user(email="editor@example.com", password="Sup3rSecret!23", **kwargs):
    user = User.objects.create_user(email=email, password=password, role="EDITOR", **kwargs)
    return user


def test_login_page_renders(client):
    resp = client.get(reverse("accounts:login"))
    assert resp.status_code == 200


def test_login_success_redirects_to_dashboard(client):
    user = _make_user()
    user.must_change_password = False
    user.save()
    resp = client.post(reverse("accounts:login"), {"username": user.email, "password": "Sup3rSecret!23"})
    assert resp.status_code == 302
    assert resp.url == reverse("reporting:dashboard")


def test_login_wrong_password_shows_error_not_crash(client):
    user = _make_user()
    user.must_change_password = False
    user.save()
    resp = client.post(reverse("accounts:login"), {"username": user.email, "password": "wrong"})
    assert resp.status_code == 200  # re-renders form, no 500
    assert not resp.wsgi_request.user.is_authenticated


def test_login_rejects_deactivated_user(client):
    """Deliberate behavior change vs legacy (docs/DECISIONS.md): legacy
    silently downgraded a deactivated user to viewer role; Django's
    ModelBackend rejects is_active=False outright at authenticate() time.
    """
    _make_user(email="off@example.com", is_active=False)
    payload = {"username": "off@example.com", "password": "Sup3rSecret!23"}
    resp = client.post(reverse("accounts:login"), payload)
    assert resp.status_code == 200
    assert not resp.wsgi_request.user.is_authenticated


def test_login_with_must_change_password_redirects_there_on_next_request(client):
    user = _make_user()  # must_change_password=True by model default
    client.post(reverse("accounts:login"), {"username": user.email, "password": "Sup3rSecret!23"})
    resp = client.get(reverse("reporting:dashboard"))
    assert resp.status_code == 302
    assert resp.url == reverse("accounts:password_change")


def test_password_change_page_itself_is_exempt_from_the_redirect_loop(client):
    user = _make_user()
    client.post(reverse("accounts:login"), {"username": user.email, "password": "Sup3rSecret!23"})
    resp = client.get(reverse("accounts:password_change"))
    assert resp.status_code == 200  # not redirected back to itself


def test_logout_clears_session(client):
    user = _make_user()
    user.must_change_password = False
    user.save()
    client.post(reverse("accounts:login"), {"username": user.email, "password": "Sup3rSecret!23"})
    client.post(reverse("accounts:logout"))
    resp = client.get(reverse("reporting:dashboard"))
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("accounts:login"))


def test_protected_page_requires_login(client):
    resp = client.get(reverse("reporting:dashboard"))
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("accounts:login"))
