import pytest
from django.urls import reverse

from crm.accounts.models import User
from crm.customers.models import Customer

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def telesell_factory(client):
    def _make(email: str, staff_code: str):
        user = User.objects.create_user(email=email, password="x", role="พนักงาน", staff_code=staff_code)
        user.must_change_password = False
        user.save()
        return user

    return _make


def test_customers_list_renders_and_is_unscoped(client, telesell_factory):
    """docs/DECISIONS.md #11: Customers list is a deliberate carve-out —
    every logged-in role sees every customer, unlike /followup.
    """
    user = telesell_factory("staff1@example.com", "S0001")
    Customer.objects.create(phone_key="0899999911", phone1="0899999911", customer_name="A", staff_code="S0001")
    Customer.objects.create(phone_key="0899999912", phone1="0899999912", customer_name="B", staff_code="S0002")
    client.force_login(user)

    resp = client.get(reverse("customers:list"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "A" in body
    assert "B" in body  # visible despite belonging to a different staff_code


def test_customer_360_direct_url_denied_for_other_owner(client, telesell_factory):
    """Invariant 5: direct-URL access to another owner's Customer 360 must
    be denied (403), not merely hidden from a list.
    """
    viewer = telesell_factory("staff1@example.com", "S0001")
    other_customer = Customer.objects.create(
        phone_key="0899999913", phone1="0899999913", customer_name="Other", staff_code="S0002"
    )
    client.force_login(viewer)

    resp = client.get(reverse("customers:detail", args=[other_customer.id]))
    assert resp.status_code == 403


def test_customer_360_allows_own_staff_code(client, telesell_factory):
    viewer = telesell_factory("staff1@example.com", "S0001")
    own_customer = Customer.objects.create(
        phone_key="0899999914", phone1="0899999914", customer_name="Mine", staff_code="S0001"
    )
    client.force_login(viewer)

    resp = client.get(reverse("customers:detail", args=[own_customer.id]))
    assert resp.status_code == 200


def test_customer_360_editor_sees_everyone(client):
    editor = User.objects.create_user(email="editor2@example.com", password="x", role="EDITOR")
    editor.must_change_password = False
    editor.save()
    other_customer = Customer.objects.create(
        phone_key="0899999915", phone1="0899999915", customer_name="AnyOne", staff_code="S0009"
    )
    client.force_login(editor)

    resp = client.get(reverse("customers:detail", args=[other_customer.id]))
    assert resp.status_code == 200
