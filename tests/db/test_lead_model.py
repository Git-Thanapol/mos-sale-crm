"""Lead = Customer + Team, with a deterministic immutable key.

02_LOCKED_BUSINESS_RULES.md / 11_DO_NOT_CHANGE_CHECKLIST.md:
- Customer and Lead remain separate concepts.
- Lead remains Customer + Team; a Customer may hold several Leads in different
  Teams without collapsing.
- A Lead always has a Team. Team-unknown work is pre-Lead intake (D019), never
  a Lead with team_id NULL and never a fake GLOBAL_UNASSIGNED team.
- STOPPED is a lifecycle state and requires a reason.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from crm.customers.models import Customer
from crm.leads.models import Lead
from crm.teams.models import Team

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def make_customer(**kwargs) -> Customer:
    uid = kwargs.pop("_uid", "0000001")
    defaults = {
        "phone_key": f"09{uid}",
        "phone1": f"09{uid}",
        "customer_name": "ทดสอบ",
        "staff_code": "",
        "owner_display": "",
    }
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def make_team(code="CRM_TEAM", policy=None) -> Team:
    return Team.objects.get_or_create(
        code=code,
        defaults={
            "name": code,
            "display_name": code,
            "policy": policy or {"ownershipPolicy": "EXCLUSIVE"},
        },
    )[0]


def make_lead(customer=None, team_code="CRM_TEAM", **kwargs) -> Lead:
    customer = customer or make_customer()
    team = make_team(team_code)
    defaults = {
        "customer": customer,
        "team": team,
        "lifecycle_status": "NEW",
        "routing_state": "TEAM_UNASSIGNED",
        "ownership_policy": team.ownership_policy,
    }
    defaults.update(kwargs)
    return Lead.objects.create(**defaults)


def test_lead_key_is_team_code_colon_customer_uid():
    lead = make_lead(team_code="CRM_TEAM")
    assert lead.lead_key == f"CRM_TEAM:{lead.customer.customer_uid}"


def test_build_key_is_the_single_source_of_the_key_format():
    customer = make_customer()
    assert Lead.build_key("CRM_TEAM", customer.customer_uid) == f"CRM_TEAM:{customer.customer_uid}"


def test_one_customer_can_hold_a_lead_in_two_teams():
    customer = make_customer(_uid="0000001")
    make_lead(customer=customer, team_code="CRM_TEAM")
    make_lead(customer=customer, team_code="UPSELL_TEAM")
    assert Lead.objects.filter(customer=customer).count() == 2


def test_leads_for_the_same_customer_in_two_teams_have_different_keys():
    customer = make_customer(_uid="0000001")
    a = make_lead(customer=customer, team_code="CRM_TEAM")
    b = make_lead(customer=customer, team_code="UPSELL_TEAM")
    assert a.lead_key != b.lead_key


def test_duplicate_lead_for_the_same_team_and_customer_is_rejected():
    customer = make_customer(_uid="0000001")
    make_lead(customer=customer, team_code="CRM_TEAM")
    with pytest.raises(IntegrityError):
        make_lead(customer=customer, team_code="CRM_TEAM")


def test_lead_key_is_not_recomputed_for_an_existing_row():
    # Immutable identity: renaming would orphan audit rows that reference it.
    lead = make_lead()
    original = lead.lead_key
    lead.lifecycle_status = "FOLLOWING"
    lead.save()
    lead.refresh_from_db()
    assert lead.lead_key == original


def test_stopped_lead_requires_a_stop_reason():
    lead = make_lead(lifecycle_status="STOPPED", stopped_reason="")
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_stopped_lead_with_a_reason_validates():
    make_lead(lifecycle_status="STOPPED", stopped_reason="MANUAL_STOP").full_clean()


def test_unknown_lifecycle_status_is_rejected():
    lead = make_lead(lifecycle_status="LOITERING")
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_unknown_routing_state_is_rejected():
    lead = make_lead(routing_state="SOMEWHERE")
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_unknown_stop_reason_is_rejected():
    lead = make_lead(lifecycle_status="STOPPED", stopped_reason="BECAUSE_I_SAID")
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_lead_with_a_caretaker_always_has_a_team():
    # D019: Team-unknown work is pre-Lead intake, so team can never be NULL.
    assert Lead._meta.get_field("team").null is False


def test_lead_declares_both_scoping_paths():
    # STAFF_CODE_PATH drives the existing staff_code scoping; TEAM_PATH is read
    # by the TEAM_LEAD branch added in Task 14 (pre-flight CONFLICT 3).
    assert Lead.STAFF_CODE_PATH == "customer__staff_code"
    assert Lead.TEAM_PATH == "team__code"


def test_counters_default_to_zero_not_null():
    lead = make_lead()
    assert lead.missed_channel_count == 0
    assert lead.no_answer_streak == 0
    assert lead.total_orders == 0
    assert lead.total_spend == 0


def test_a_new_lead_has_no_order_history_and_no_appointment():
    lead = make_lead()
    assert lead.last_order_at is None
    assert lead.last_order_id is None
    assert lead.next_follow_at is None
    assert lead.current_cycle_id is None


def test_access_arrays_default_to_empty_lists():
    lead = make_lead()
    assert lead.access_team_ids == []
    assert lead.access_user_ids == []


def test_scoping_hides_another_staff_members_lead():
    # A telesale is scoped by staff_code AND the lead's team (see
    # tests/db/test_telesale_team_scope.py for why), so the user needs a real
    # row with an open TeamAssignment rather than a SimpleNamespace.
    from django.utils import timezone

    from crm.accounts.models import User
    from crm.teams.models import TeamAssignment

    mine = make_customer(_uid="0000001", staff_code="S0001")
    theirs = make_customer(_uid="0000002", staff_code="S0002")
    make_lead(customer=mine, team_code="CRM_TEAM")
    make_lead(customer=theirs, team_code="CRM_TEAM")

    telesell = User.objects.create(
        email="ts@example.com", role="พนักงาน", staff_code="S0001", must_change_password=False
    )
    TeamAssignment.objects.create(
        user=telesell, team_code="CRM_TEAM", effective_from=timezone.now()
    )

    visible = Lead.objects.for_user(telesell)
    assert [lead.customer_id for lead in visible] == [mine.id]


def test_scoping_fails_closed_for_staff_without_a_staff_code():
    from types import SimpleNamespace

    make_lead(customer=make_customer(_uid="0000001", staff_code="S0001"))
    nobody = SimpleNamespace(role="พนักงาน", staff_code="", pk=None, is_active=True)
    assert Lead.objects.for_user(nobody).count() == 0
