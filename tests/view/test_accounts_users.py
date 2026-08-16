import pytest
from django.urls import reverse

from crm.accounts import selectors, services
from crm.accounts.models import User
from crm.customers.models import Customer

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client):
    user = User.objects.create_user(email="viewer@example.com", password="x", role="ทั่วไป")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def _customer(phone: str, name: str, staff_code: str) -> Customer:
    return Customer.objects.create(phone_key=phone, phone1=phone, customer_name=name, staff_code=staff_code)


def _payload(**overrides):
    base = {
        "email": "x@example.com", "role": "ทั่วไป", "staff_code": "", "staff_name": "",
        "owner_alias": "", "is_active": "on",
    }
    base.update(overrides)
    return base


# --- index view ---

def test_index_renders_for_any_logged_in_user(client, viewer):
    resp = client.get(reverse("accounts:user_list"))
    assert resp.status_code == 200
    assert resp.context["can_manage"] is False
    assert resp.context["create_form"] is None


def test_index_shows_manage_controls_for_editor(client, editor):
    resp = client.get(reverse("accounts:user_list"))
    assert resp.status_code == 200
    assert resp.context["can_manage"] is True
    assert resp.context["create_form"] is not None


# --- visibility tester (selectors.visibility_summary via for_user) ---

def test_tester_reuses_real_scoping_for_telesell(client, editor):
    telesell = User.objects.create_user(
        email="staff@example.com", password="x", role="พนักงาน", staff_code="S0001"
    )
    _customer("0891111111", "Own", "S0001")
    _customer("0891111112", "Other", "S0002")

    resp = client.get(reverse("accounts:user_list"), {"test_email": telesell.email})
    assert resp.status_code == 200
    assert resp.context["tester_result"]["total"] == 1


def test_tester_fails_closed_for_staff_without_staff_code(client, editor):
    telesell = User.objects.create_user(email="nocod@example.com", password="x", role="พนักงาน", staff_code="")
    _customer("0891111113", "Any", "S0001")

    resp = client.get(reverse("accounts:user_list"), {"test_email": telesell.email})
    assert resp.context["tester_result"]["total"] == 0


def test_tester_editor_sees_everything(client, editor):
    other_editor = User.objects.create_user(email="ed2@example.com", password="x", role="EDITOR")
    _customer("0891111114", "A", "S0001")
    _customer("0891111115", "B", "S0002")

    resp = client.get(reverse("accounts:user_list"), {"test_email": other_editor.email})
    assert resp.context["tester_result"]["total"] == 2


def test_tester_unknown_email_shows_error(client, editor):
    resp = client.get(reverse("accounts:user_list"), {"test_email": "nobody@example.com"}, follow=True)
    assert resp.status_code == 200
    assert resp.context["tester_result"] is None


def test_visibility_summary_matches_dashboard_scoping_directly():
    telesell = User.objects.create_user(
        email="direct@example.com", password="x", role="พนักงาน", staff_code="S0001"
    )
    _customer("0891111116", "Mine", "S0001")
    result = selectors.visibility_summary(telesell)
    assert result["total"] == 1
    assert result["samples"][0]["customer_name"] == "Mine"


# --- create_user ---

def test_create_user_new_email_gets_unusable_password(client, editor):
    resp = client.post(
        reverse("accounts:create_user"),
        _payload(email="NEW@Example.com", role="พนักงาน", staff_code="S0009", staff_name="New"),
        follow=True,
    )
    assert resp.status_code == 200
    created = User.objects.get(email="new@example.com")  # normalized lowercase
    assert created.has_usable_password() is False
    assert created.staff_code == "S0009"


def test_create_user_existing_email_updates_in_place(client, editor):
    existing = User.objects.create_user(email="dup@example.com", password="x", role="ทั่วไป")
    resp = client.post(
        reverse("accounts:create_user"),
        _payload(email="dup@example.com", role="EDITOR"),
        follow=True,
    )
    assert resp.status_code == 200
    assert User.objects.count() == 2  # existing + editor fixture, no duplicate row
    existing.refresh_from_db()
    assert existing.role == "EDITOR"
    assert existing.has_usable_password() is True  # untouched, not reset to unusable


def test_create_user_blank_email_rejected(client, editor):
    before = User.objects.count()
    client.post(reverse("accounts:create_user"), {"email": "", "role": "EDITOR"}, follow=True)
    assert User.objects.count() == before


# --- save_user ---

def test_save_user_updates_fields(client, editor):
    target = User.objects.create_user(email="target@example.com", password="x", role="ทั่วไป")
    resp = client.post(
        reverse("accounts:save_user", args=[target.id]),
        _payload(email="target@example.com", role="พนักงาน", staff_code="S0005", staff_name="T"),
        follow=True,
    )
    assert resp.status_code == 200
    target.refresh_from_db()
    assert target.role == "พนักงาน"
    assert target.staff_code == "S0005"


def test_save_user_email_collision_reports_error_not_crash(client, editor):
    User.objects.create_user(email="taken@example.com", password="x", role="ทั่วไป")
    target = User.objects.create_user(email="target2@example.com", password="x", role="ทั่วไป")
    resp = client.post(
        reverse("accounts:save_user", args=[target.id]),
        _payload(email="taken@example.com"),
        follow=True,
    )
    assert resp.status_code == 200
    target.refresh_from_db()
    assert target.email == "target2@example.com"  # unchanged


# --- deactivate_user ---

def test_deactivate_user(client, editor):
    target = User.objects.create_user(email="off@example.com", password="x", role="ทั่วไป", is_active=True)
    client.post(reverse("accounts:deactivate_user", args=[target.id]), {}, follow=True)
    target.refresh_from_db()
    assert target.is_active is False


def test_deactivate_self_is_blocked(client, editor):
    resp = client.post(reverse("accounts:deactivate_user", args=[editor.id]), {})
    assert resp.status_code == 403
    editor.refresh_from_db()
    assert editor.is_active is True


def test_deactivate_already_inactive_is_noop(client, editor):
    target = User.objects.create_user(email="already@example.com", password="x", role="ทั่วไป", is_active=False)
    resp = client.post(reverse("accounts:deactivate_user", args=[target.id]), {}, follow=True)
    assert resp.status_code == 200
    target.refresh_from_db()
    assert target.is_active is False


# --- reset_password ---

def test_reset_password_issues_new_usable_password(client, editor):
    target = User.objects.create_user(email="reset@example.com", password="x", role="ทั่วไป")
    target.set_unusable_password()
    target.save()
    resp = client.post(reverse("accounts:reset_password", args=[target.id]), {}, follow=True)
    assert resp.status_code == 200
    target.refresh_from_db()
    assert target.has_usable_password() is True
    assert target.must_change_password is True


def test_issue_password_returns_plaintext_once(db):
    target = User.objects.create_user(email="svc@example.com", password="x", role="ทั่วไป")
    password = services.issue_password(target)
    assert len(password) == 16
    target.refresh_from_db()
    assert target.check_password(password)
    assert target.must_change_password is True


# --- permission gate ---

@pytest.mark.parametrize("url_name,args", [
    ("accounts:create_user", []),
])
def test_create_blocks_non_editor(client, viewer, url_name, args):
    resp = client.post(reverse(url_name, args=args), {})
    assert resp.status_code == 403


def test_save_user_blocks_non_editor(client, viewer):
    target = User.objects.create_user(email="t@example.com", password="x", role="ทั่วไป")
    resp = client.post(reverse("accounts:save_user", args=[target.id]), {})
    assert resp.status_code == 403


def test_deactivate_blocks_non_editor(client, viewer):
    target = User.objects.create_user(email="t2@example.com", password="x", role="ทั่วไป")
    resp = client.post(reverse("accounts:deactivate_user", args=[target.id]), {})
    assert resp.status_code == 403


def test_reset_password_blocks_non_editor(client, viewer):
    target = User.objects.create_user(email="t3@example.com", password="x", role="ทั่วไป")
    resp = client.post(reverse("accounts:reset_password", args=[target.id]), {})
    assert resp.status_code == 403
