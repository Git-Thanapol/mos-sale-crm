"""Team is configurable data, not a hard-coded name.

02_LOCKED_BUSINESS_RULES.md: "Team names must not be hard-coded into business
logic." Ownership behaviour therefore lives in Team.policy, and adding a
fourth policy means adding a resolver in crm/domain/ownership.py -- never a
`if team.code == "CRM_TEAM"` branch. These tests pin the policy accessors and
their defaults, so a row seeded before a policy key existed keeps behaving
rather than raising.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.utils import IntegrityError

from crm.domain.enums import OWNERSHIP_POLICIES
from crm.teams.models import Team, TeamAssignment

pytestmark = [pytest.mark.db, pytest.mark.django_db]

PROTOTYPE_MISSED_CHANNEL_DEFAULTS = {
    "enabled": True,
    "countNullCloser": True,
    "resetOnCaretakerClose": True,
}


def make_team(**kwargs) -> Team:
    defaults = {
        "code": "CRM_TEAM",
        "name": "CRM",
        "display_name": "CRM Team",
        "policy": {"ownershipPolicy": "EXCLUSIVE"},
    }
    defaults.update(kwargs)
    return Team.objects.create(**defaults)


def test_policy_defaults_match_the_prototype_team_policy_schema():
    team = make_team()
    assert team.ownership_policy == "EXCLUSIVE"
    assert team.missed_channel_policy == PROTOTYPE_MISSED_CHANNEL_DEFAULTS
    assert team.team_lead_configured is True
    assert team.max_no_answer_attempts == 3
    assert team.auto_stop is False


def test_a_policy_key_the_row_omits_falls_back_instead_of_raising():
    # A team seeded before a policy key existed must keep working.
    team = make_team(policy={"ownershipPolicy": "LATEST_ORDER"})
    assert team.missed_channel_policy == PROTOTYPE_MISSED_CHANNEL_DEFAULTS
    assert team.max_no_answer_attempts == 3
    assert team.auto_stop is False
    assert team.team_lead_configured is True


def test_stored_policy_values_override_the_defaults():
    team = make_team(
        policy={
            "ownershipPolicy": "LATEST_ORDER_WITH_LOCK",
            "maxNoAnswerAttempts": 5,
            "autoStop": True,
            "missedChannel": {"enabled": False, "countNullCloser": False, "resetOnCaretakerClose": False},
            "routing": {"teamLeadConfigured": False},
        }
    )
    assert team.ownership_policy == "LATEST_ORDER_WITH_LOCK"
    assert team.max_no_answer_attempts == 5
    assert team.auto_stop is True
    assert team.missed_channel_policy == {
        "enabled": False,
        "countNullCloser": False,
        "resetOnCaretakerClose": False,
    }
    assert team.team_lead_configured is False


def test_a_partial_missed_channel_policy_merges_over_the_defaults():
    team = make_team(policy={"ownershipPolicy": "EXCLUSIVE", "missedChannel": {"countNullCloser": False}})
    assert team.missed_channel_policy == {
        "enabled": True,
        "countNullCloser": False,
        "resetOnCaretakerClose": True,
    }


@pytest.mark.parametrize("policy", OWNERSHIP_POLICIES)
def test_every_locked_ownership_policy_validates(policy):
    team = Team(code=f"T_{policy}", name=policy, display_name=policy, policy={"ownershipPolicy": policy})
    team.full_clean()


def test_unknown_ownership_policy_is_rejected_at_the_model_layer():
    team = Team(code="X", name="X", display_name="X", policy={"ownershipPolicy": "WHATEVER"})
    with pytest.raises(ValidationError):
        team.full_clean()


def test_policy_without_an_ownership_policy_is_rejected():
    team = Team(code="X", name="X", display_name="X", policy={})
    with pytest.raises(ValidationError):
        team.full_clean()


def test_team_code_is_unique():
    make_team(code="CRM_TEAM")
    with pytest.raises(IntegrityError):
        make_team(code="CRM_TEAM")


def test_seed_teams_creates_the_existing_team_codes():
    call_command("seed_teams")
    assert set(Team.objects.values_list("code", flat=True)) == set(dict(TeamAssignment.TEAM_CHOICES))


def test_seed_teams_is_idempotent():
    call_command("seed_teams")
    call_command("seed_teams")
    assert Team.objects.filter(code__in=["CRM_TEAM", "UPSELL_TEAM"]).count() == 2


def test_seed_teams_does_not_overwrite_an_operator_edited_policy():
    call_command("seed_teams")
    team = Team.objects.get(code="CRM_TEAM")
    team.policy = {"ownershipPolicy": "LATEST_ORDER", "maxNoAnswerAttempts": 9}
    team.save()
    call_command("seed_teams")
    team.refresh_from_db()
    assert team.ownership_policy == "LATEST_ORDER"
    assert team.max_no_answer_attempts == 9


def test_seeded_teams_carry_the_display_names_the_legacy_choices_used():
    call_command("seed_teams")
    assert Team.objects.get(code="CRM_TEAM").display_name == "CRM Team"
    assert Team.objects.get(code="UPSELL_TEAM").display_name == "Upsell Team"


def test_team_assignment_team_code_is_not_converted_to_a_foreign_key():
    # Task 3 deliberately leaves the existing /team-sales queries alone.
    field = TeamAssignment._meta.get_field("team_code")
    assert field.get_internal_type() == "CharField"
