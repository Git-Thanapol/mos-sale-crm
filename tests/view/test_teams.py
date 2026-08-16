from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from crm.accounts.models import User
from crm.core.permissions import MSG_TEAM_SALES_BLOCKED, can_view_team_sales
from crm.customers.models import Customer
from crm.orders.models import Order, OrderLine
from crm.teams import selectors, services
from crm.teams.models import TeamAssignment

pytestmark = [pytest.mark.view, pytest.mark.django_db]


@pytest.fixture
def editor(client):
    user = User.objects.create_user(email="editor@example.com", password="x", role="EDITOR")
    user.must_change_password = False
    user.save()
    client.force_login(user)
    return user


def _make_user(role: str, email: str) -> User:
    user = User.objects.create_user(email=email, password="x", role=role)
    user.must_change_password = False
    user.save()
    return user


def _manual_order(
    *, uploaded_by: str, sale_type: str, created_at, amount,
    sku="SP1", product_name="Widget", quantity=1, customer=None,
):
    seq = OrderLine.objects.count()
    customer = customer or Customer.objects.create(
        phone_key=f"0891{seq:06d}", phone1=f"0891{seq:06d}", customer_name="C"
    )
    order = Order.objects.create(
        customer=customer, order_no=f"O{Order.objects.count()}", sale_type=sale_type,
        source_type="manual", uploaded_by=uploaded_by,
    )
    Order.objects.filter(pk=order.pk).update(created_at=created_at)
    order.refresh_from_db()
    OrderLine.objects.create(
        order=order, sku=sku, product_name=product_name, quantity=quantity, amount=amount
    )
    return order


# --- permission gate ---

def test_can_view_team_sales_is_editor_only():
    assert can_view_team_sales(User(role="EDITOR"))
    # Deliberate asymmetry vs can_manage_all: ADMIN is blocked here.
    assert not can_view_team_sales(User(role="ADMIN"))
    assert not can_view_team_sales(User(role="พนักงาน"))
    assert not can_view_team_sales(User(role="ทั่วไป"))


@pytest.mark.parametrize("role", ["ADMIN", "พนักงาน", "ทั่วไป"])
def test_team_sales_page_blocks_non_editor(client, role):
    _make_user(role, f"{role.lower()}@example.com")
    client.force_login(User.objects.get(role=role))
    resp = client.get(reverse("teams:list"))
    assert resp.status_code == 403
    assert MSG_TEAM_SALES_BLOCKED in resp.content.decode()


def test_team_sales_page_renders_for_editor(client, editor):
    resp = client.get(reverse("teams:list"))
    assert resp.status_code == 200


# --- selectors: summary ---

def test_summary_only_counts_manual_orders(editor):
    bangkok_noon = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    assignee = _make_user("พนักงาน", "assignee@example.com")
    # Assignment must be effective *before* the order's created_at, or the
    # effective-dating join correctly treats the order as pre-dating the
    # assignment and buckets it as unassigned.
    with freeze_time(datetime(2026, 7, 1)):
        services.assign_team(actor=editor, target_user=assignee, team_code="CRM_TEAM")

    _manual_order(uploaded_by=assignee.email, sale_type="NEW_ORDER", created_at=bangkok_noon, amount=100)
    imported = Order.objects.create(
        customer=Customer.objects.create(phone_key="0892000001", phone1="0892000001", customer_name="D"),
        order_no="IMP1", sale_type="NEW_ORDER", source_type="import", uploaded_by=assignee.email,
    )
    Order.objects.filter(pk=imported.pk).update(created_at=bangkok_noon)
    OrderLine.objects.create(order=imported, sku="SPX", product_name="X", quantity=1, amount=99999)

    summary = selectors.team_sales_summary(bangkok_noon.date(), bangkok_noon.date())
    crm_team = next(t for t in summary["teams"] if t["team_code"] == "CRM_TEAM")
    assert crm_team["sales_amount"] == 100  # the import row's 99999 must not leak in


def test_summary_buckets_unassigned_uploader_separately(editor):
    ts = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    nobody = _make_user("พนักงาน", "nobody@example.com")  # never assigned to a team
    _manual_order(uploaded_by=nobody.email, sale_type="NEW_ORDER", created_at=ts, amount=250)

    summary = selectors.team_sales_summary(ts.date(), ts.date())
    assert summary["unassigned"]["sales_amount"] == 250
    assert summary["unassigned"]["row_count"] == 1
    assert summary["unassigned_count"] == 1
    for team in summary["teams"]:
        assert team["sales_amount"] == 0


def test_summary_attribution_is_effective_dated(editor):
    """A row is credited to whichever team was open at the order's
    created_at — not whichever team is current now. Reassigning someone
    must not retroactively change history."""
    staffer = _make_user("พนักงาน", "mover@example.com")
    day1 = timezone.make_aware(datetime(2026, 7, 1, 9, 0))
    day2 = timezone.make_aware(datetime(2026, 7, 15, 9, 0))

    with freeze_time(datetime(2026, 6, 20)):
        services.assign_team(actor=editor, target_user=staffer, team_code="CRM_TEAM")
    _manual_order(uploaded_by=staffer.email, sale_type="NEW_ORDER", created_at=day1, amount=111)

    # Move them to Upsell Team partway through the month.
    with freeze_time(datetime(2026, 7, 10)):
        services.assign_team(actor=editor, target_user=staffer, team_code="UPSELL_TEAM")
    _manual_order(uploaded_by=staffer.email, sale_type="NEW_ORDER", created_at=day2, amount=222)

    summary = selectors.team_sales_summary(day1.date(), day2.date())
    crm_team = next(t for t in summary["teams"] if t["team_code"] == "CRM_TEAM")
    upsell_team = next(t for t in summary["teams"] if t["team_code"] == "UPSELL_TEAM")
    assert crm_team["sales_amount"] == 111
    assert upsell_team["sales_amount"] == 222


def test_summary_excludes_follow_sale_type_even_unfiltered(editor):
    staffer = _make_user("พนักงาน", "follow@example.com")
    services.assign_team(actor=editor, target_user=staffer, team_code="CRM_TEAM")
    ts = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    _manual_order(uploaded_by=staffer.email, sale_type="FOLLOW", created_at=ts, amount=999999)

    summary = selectors.team_sales_summary(ts.date(), ts.date())
    assert all(t["sales_amount"] == 0 for t in summary["teams"])
    assert summary["unassigned"]["sales_amount"] == 0


def test_summary_sale_type_filter_narrows_to_one_type(editor):
    staffer = _make_user("พนักงาน", "filt@example.com")
    with freeze_time(datetime(2026, 7, 1)):
        services.assign_team(actor=editor, target_user=staffer, team_code="CRM_TEAM")
    ts = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    _manual_order(uploaded_by=staffer.email, sale_type="NEW_ORDER", created_at=ts, amount=100)
    _manual_order(uploaded_by=staffer.email, sale_type="UPSELL", created_at=ts, amount=50)

    summary = selectors.team_sales_summary(ts.date(), ts.date(), sale_type_filter="UPSELL")
    crm_team = next(t for t in summary["teams"] if t["team_code"] == "CRM_TEAM")
    assert crm_team["sales_amount"] == 50
    assert crm_team["order_count"] == 1


def test_summary_invalid_sale_type_raises():
    with pytest.raises(ValueError):
        selectors.team_sales_summary(timezone.localdate(), timezone.localdate(), sale_type_filter="BOGUS")


# --- selectors: top products ---

def test_top_products_excludes_unassigned_rows(editor):
    nobody = _make_user("พนักงาน", "unassigned2@example.com")
    ts = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    _manual_order(
        uploaded_by=nobody.email, sale_type="NEW_ORDER", created_at=ts, amount=100,
        sku="SPU", product_name="Unassigned Product",
    )

    rows = selectors.team_top_products(ts.date(), ts.date())
    assert all(r["sku"] != "SPU" for r in rows)


def test_top_products_orders_by_quantity_desc_then_name(editor):
    staffer = _make_user("พนักงาน", "topprod@example.com")
    with freeze_time(datetime(2026, 7, 1)):
        services.assign_team(actor=editor, target_user=staffer, team_code="CRM_TEAM")
    ts = timezone.make_aware(datetime(2026, 7, 10, 12, 0))
    _manual_order(
        uploaded_by=staffer.email, sale_type="NEW_ORDER", created_at=ts, amount=10,
        sku="S1", product_name="Alpha", quantity=5,
    )
    _manual_order(
        uploaded_by=staffer.email, sale_type="NEW_ORDER", created_at=ts, amount=10,
        sku="S2", product_name="Beta", quantity=9,
    )

    rows = selectors.team_top_products(ts.date(), ts.date(), limit=10)
    assert [r["sku"] for r in rows[:2]] == ["S2", "S1"]  # 9 before 5


def test_top_products_invalid_limit_raises():
    with pytest.raises(ValueError):
        selectors.team_top_products(timezone.localdate(), timezone.localdate(), limit=0)


def test_top_products_invalid_team_code_raises():
    with pytest.raises(ValueError):
        selectors.team_top_products(timezone.localdate(), timezone.localdate(), team_code="BOGUS")


# --- services: assign_team ---

def test_assign_team_creates_then_changes_then_clears(editor):
    target = _make_user("พนักงาน", "assign1@example.com")

    result = services.assign_team(actor=editor, target_user=target, team_code="CRM_TEAM")
    assert result.action == "created" and result.changed
    assert TeamAssignment.objects.filter(user=target, effective_to__isnull=True).count() == 1

    result = services.assign_team(actor=editor, target_user=target, team_code="UPSELL_TEAM")
    assert result.action == "changed" and result.changed
    assert TeamAssignment.objects.filter(user=target).count() == 2
    current = TeamAssignment.objects.get(user=target, effective_to__isnull=True)
    assert current.team_code == "UPSELL_TEAM"

    result = services.assign_team(actor=editor, target_user=target, team_code=None)
    assert result.action == "cleared" and result.changed
    assert TeamAssignment.objects.filter(user=target, effective_to__isnull=True).count() == 0


def test_assign_team_same_team_is_noop(editor):
    target = _make_user("พนักงาน", "assign2@example.com")
    services.assign_team(actor=editor, target_user=target, team_code="CRM_TEAM")
    before_count = TeamAssignment.objects.filter(user=target).count()

    result = services.assign_team(actor=editor, target_user=target, team_code="CRM_TEAM")
    assert not result.changed and result.action == "unchanged"
    assert TeamAssignment.objects.filter(user=target).count() == before_count


def test_assign_team_clear_with_no_existing_assignment_is_noop(editor):
    target = _make_user("พนักงาน", "assign3@example.com")
    result = services.assign_team(actor=editor, target_user=target, team_code=None)
    assert not result.changed and result.action == "unchanged"


def test_assign_team_invalid_code_raises(editor):
    target = _make_user("พนักงาน", "assign4@example.com")
    with pytest.raises(ValueError):
        services.assign_team(actor=editor, target_user=target, team_code="BOGUS")


def test_assign_team_rapid_reassignment_does_not_violate_exclusion_constraint(editor):
    """Regression: same-instant (or backward-clock) reassignment must nudge
    effective_from forward by 1us rather than let two rows with an
    identical instant hit the GiST exclusion constraint."""
    target = _make_user("พนักงาน", "assign5@example.com")
    frozen = datetime(2026, 7, 10, 12, 0, 0)
    with freeze_time(frozen):
        services.assign_team(actor=editor, target_user=target, team_code="CRM_TEAM")
        # Same frozen instant as the row just created -> now <= current.effective_from
        services.assign_team(actor=editor, target_user=target, team_code="UPSELL_TEAM")

    rows = list(TeamAssignment.objects.filter(user=target).order_by("effective_from"))
    assert len(rows) == 2
    assert rows[1].effective_from > rows[0].effective_from
    assert rows[0].effective_to == rows[1].effective_from


# --- view: save_assignment ---

def test_save_assignment_view_updates_team(client, editor):
    target = _make_user("พนักงาน", "viewassign@example.com")
    resp = client.post(
        reverse("teams:assign", args=[target.id]),
        {"team_choice": "CRM Team", "base_qs": ""},
        follow=True,
    )
    assert resp.status_code == 200
    assert TeamAssignment.objects.filter(
        user=target, team_code="CRM_TEAM", effective_to__isnull=True
    ).exists()


def test_save_assignment_view_blocks_non_editor(client):
    staffer = _make_user("พนักงาน", "blocked1@example.com")
    target = _make_user("พนักงาน", "blocked2@example.com")
    client.force_login(staffer)
    resp = client.post(reverse("teams:assign", args=[target.id]), {"team_choice": "CRM Team", "base_qs": ""})
    assert resp.status_code == 403
    assert not TeamAssignment.objects.filter(user=target).exists()
