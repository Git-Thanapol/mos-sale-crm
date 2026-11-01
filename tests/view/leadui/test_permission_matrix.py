"""Every role against every lead page, over HTTP.

The sidebar hides what a role may not open, but a hidden link is a convenience:
this matrix pins the real answer for each (role, route) pair, including ids in
the URL that belong to someone else's team or customer. 404 means "not yours /
does not exist" and is deliberately indistinguishable from an unknown id.

Run via: docker compose exec -T web python -m pytest tests/view/leadui/test_permission_matrix.py
"""

import pytest
from django.urls import reverse

from tests.db.factories import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_TELESELL,
    ROLE_VIEWER,
    make_customer,
    make_lead,
    make_user,
)

pytestmark = [pytest.mark.view, pytest.mark.django_db]

PASSWORD = "test-pass-12345"

EDITOR, TEAM_LEAD, TELESALE, ADMIN, VIEWER = "editor", "team_lead", "telesale", "admin", "viewer"
ROLES = [EDITOR, TEAM_LEAD, TELESALE, ADMIN, VIEWER]

#: Pages without an id: role -> expected status.
LIST_ROUTES = {
    "leadui:my_work": {EDITOR: 200, TEAM_LEAD: 200, TELESALE: 200, ADMIN: 404, VIEWER: 404},
    "leadui:global_unassigned": {EDITOR: 200, TEAM_LEAD: 404, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    "leadui:team_unassigned": {EDITOR: 200, TEAM_LEAD: 200, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    "leadui:stopped_review": {EDITOR: 200, TEAM_LEAD: 200, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    # CRM V2 import screens: upload and decisions are EDITOR only; conflicts and
    # history are also open to a team lead who holds a team scope.
    "leadui:import": {EDITOR: 200, TEAM_LEAD: 404, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    "leadui:import_conflicts": {EDITOR: 200, TEAM_LEAD: 200, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    "leadui:import_history": {EDITOR: 200, TEAM_LEAD: 200, TELESALE: 404, ADMIN: 404, VIEWER: 404},
    "leadui:migration_decisions": {EDITOR: 200, TEAM_LEAD: 404, TELESALE: 404, ADMIN: 404, VIEWER: 404},
}

#: Pages for one lead / customer. Keys of the inner dict are the fixtures below:
#: mine (the telesale is caretaker, team A), in_a (team A, not theirs), in_b (team B).
ROW_EXPECTATIONS = {
    EDITOR: {"mine": 200, "in_a": 200, "in_b": 200},
    TEAM_LEAD: {"mine": 200, "in_a": 200, "in_b": 404},
    TELESALE: {"mine": 200, "in_a": 404, "in_b": 404},
    ADMIN: {"mine": 404, "in_a": 404, "in_b": 404},
    VIEWER: {"mine": 404, "in_a": 404, "in_b": 404},
}

#: Sidebar children each role should be offered.
SIDEBAR = {
    EDITOR: set(LIST_ROUTES),
    TEAM_LEAD: {
        "leadui:my_work",
        "leadui:team_unassigned",
        "leadui:stopped_review",
        "leadui:import_conflicts",
        "leadui:import_history",
    },
    TELESALE: {"leadui:my_work"},
    ADMIN: set(),
    VIEWER: set(),
}


@pytest.fixture
def world():
    telesale = make_user(email="ts@example.com", role=ROLE_TELESELL, staff_code="S0001", staff_name="สมศรี")
    users = {
        EDITOR: make_user(email="e@example.com", role=ROLE_EDITOR, staff_code="E1"),
        TEAM_LEAD: make_user(email="tl@example.com", role="TEAM_LEAD", staff_code="L1", team_scope=["TEAM_A"]),
        TELESALE: telesale,
        ADMIN: make_user(email="a@example.com", role=ROLE_ADMIN, staff_code="A1"),
        VIEWER: make_user(email="v@example.com", role=ROLE_VIEWER, staff_code="V1"),
    }
    customers = {
        "mine": make_customer(customer_name="ลูกค้าของฉัน", staff_code="S0001"),
        "in_a": make_customer(customer_name="ลูกค้าทีมเอ", staff_code="S0009"),
        "in_b": make_customer(customer_name="ลูกค้าทีมบี", staff_code="S0009"),
    }
    leads = {
        "mine": make_lead(customer=customers["mine"], team_code="TEAM_A", caretaker=telesale),
        "in_a": make_lead(customer=customers["in_a"], team_code="TEAM_A"),
        "in_b": make_lead(customer=customers["in_b"], team_code="TEAM_B"),
    }
    for user in users.values():
        user.set_password(PASSWORD)
        user.save()
    return {"users": users, "customers": customers, "leads": leads}


def as_role(client, world, role):
    assert client.login(email=world["users"][role].email, password=PASSWORD)


def url_for(kind, world, key):
    if kind == "lead":
        return reverse("leadui:lead_detail", args=[world["leads"][key].id])
    if kind == "customer":
        return reverse("leadui:customer_detail", args=[world["customers"][key].id])
    return reverse("leadui:customer_history", args=[world["customers"][key].id])


@pytest.mark.parametrize("route", list(LIST_ROUTES))
@pytest.mark.parametrize("role", ROLES)
def test_list_pages_by_role(client, world, route, role):
    as_role(client, world, role)
    assert client.get(reverse(route)).status_code == LIST_ROUTES[route][role]


@pytest.mark.parametrize("kind", ["lead", "customer", "history"])
@pytest.mark.parametrize("key", ["mine", "in_a", "in_b"])
@pytest.mark.parametrize("role", ROLES)
def test_row_pages_by_role_and_ownership(client, world, kind, key, role):
    as_role(client, world, role)
    assert client.get(url_for(kind, world, key)).status_code == ROW_EXPECTATIONS[role][key]


@pytest.mark.parametrize("kind", ["lead", "customer", "history"])
@pytest.mark.parametrize("role", [TELESALE, TEAM_LEAD])
def test_a_denied_row_page_leaks_no_customer_name(client, world, kind, role):
    as_role(client, world, role)
    denied = "in_b"
    body = client.get(url_for(kind, world, denied)).content.decode()
    assert "ลูกค้าทีมบี" not in body


@pytest.mark.parametrize("kind", ["lead", "customer", "history"])
@pytest.mark.parametrize("role", [EDITOR, TEAM_LEAD, TELESALE])
def test_an_unknown_id_looks_the_same_as_a_denied_one(client, world, kind, role):
    as_role(client, world, role)
    name = {"lead": "lead_detail", "customer": "customer_detail", "history": "customer_history"}[kind]
    assert client.get(reverse(f"leadui:{name}", args=[99999999])).status_code == 404


@pytest.mark.parametrize("route", list(LIST_ROUTES))
def test_anonymous_list_pages_redirect_to_login(client, route):
    response = client.get(reverse(route))
    assert response.status_code == 302 and "login" in response["Location"]


@pytest.mark.parametrize("kind", ["lead", "customer", "history"])
def test_anonymous_row_pages_redirect_to_login(client, world, kind):
    response = client.get(url_for(kind, world, "mine"))
    assert response.status_code == 302 and "login" in response["Location"]


@pytest.mark.parametrize("role", ROLES)
def test_the_sidebar_offers_exactly_the_pages_the_role_may_open(client, world, role):
    as_role(client, world, role)
    body = client.get(reverse("reporting:dashboard")).content.decode()
    every = {route: reverse(route) for route in LIST_ROUTES}
    offered = {route for route, path in every.items() if f'href="{path}"' in body}
    assert offered == SIDEBAR[role]


@pytest.mark.parametrize("role", ROLES)
def test_every_sidebar_link_a_role_sees_actually_opens(client, world, role):
    # The menu and the gate must agree: nothing offered may 404.
    as_role(client, world, role)
    for route in SIDEBAR[role]:
        assert client.get(reverse(route)).status_code == 200, route
