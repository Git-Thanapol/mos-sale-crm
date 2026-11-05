"""Team assignment — effective-dated, at most one open row per user.

Ported from crm_streamlit's crm_user_team_assignments table (see
docs/legacy/data-layer-report.md §2.3). Key differences from the legacy
schema, both deliberate simplifications the normalized Django models allow:

- `user` is a real FK to accounts.User, not a bare `user_email` text column
  matched by `lower(btrim(...))` at query time. The email is still what
  Order.uploaded_by carries, so selectors still join on a normalized email
  comparison — but the assignment table itself no longer needs its own
  email-normalization CHECK constraint.
- `team_name` is not a GENERATED column here; TEAM_CHOICES is the single
  source of the code->label mapping, read at the Python layer.

The exclusion constraint is the same one the legacy migration used
(`EXCLUDE USING gist (user_email WITH =, tstzrange(...) WITH &&)`), just
expressed over the FK instead of the raw email, and it's the reason
crm.core.migrations.0001_extensions installs btree_gist up front.
"""

from __future__ import annotations

from copy import deepcopy

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models

from crm.domain.enums import OWNERSHIP_POLICIES

#: Mirrors $PROTO/src/domain/entities.ts TeamPolicySchema defaults. Every
#: accessor below falls back to these, so a Team row written before a policy
#: key existed keeps behaving instead of raising KeyError.
TEAM_POLICY_DEFAULTS: dict = {
    "maxNoAnswerAttempts": 3,
    "autoStop": False,
    "missedChannel": {
        "enabled": True,
        "countNullCloser": True,
        "resetOnCaretakerClose": True,
    },
    "routing": {"teamLeadConfigured": True},
}


class Team(models.Model):
    """A configurable team. Ownership behaviour is data, not a code branch.

    02_LOCKED_BUSINESS_RULES.md requires that team names never reach business
    logic: `policy["ownershipPolicy"]` selects the resolver in
    crm/domain/ownership.py, so a fourth policy is a new resolver rather than
    a new `if team.code == ...`.

    Separate from TeamAssignment, which stays what it was: an effective-dated
    user->team membership read by /team-sales. This model is the team itself,
    and it is what a Lead points at.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    display_name = models.CharField(max_length=128)
    active = models.BooleanField(default=True)
    policy = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_team"
        indexes = [
            models.Index(fields=["code"], name="ix_team_code_active", condition=models.Q(active=True)),
        ]

    def __str__(self) -> str:
        return self.display_name or self.code

    def clean(self):
        super().clean()
        policy = self.policy or {}
        if not isinstance(policy, dict):
            raise ValidationError({"policy": "policy must be an object"})
        ownership = policy.get("ownershipPolicy")
        if ownership not in OWNERSHIP_POLICIES:
            raise ValidationError(
                {
                    "policy": (
                        f"ownershipPolicy must be one of {', '.join(OWNERSHIP_POLICIES)}; "
                        f"got {ownership!r}"
                    )
                }
            )
        attempts = policy.get("maxNoAnswerAttempts", TEAM_POLICY_DEFAULTS["maxNoAnswerAttempts"])
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise ValidationError({"policy": "maxNoAnswerAttempts must be an integer >= 1"})

    @property
    def ownership_policy(self) -> str:
        return self.policy.get("ownershipPolicy", "")

    @property
    def missed_channel_policy(self) -> dict:
        merged = deepcopy(TEAM_POLICY_DEFAULTS["missedChannel"])
        merged.update(self.policy.get("missedChannel") or {})
        return merged

    @property
    def team_lead_configured(self) -> bool:
        routing = self.policy.get("routing") or {}
        return bool(routing.get("teamLeadConfigured", TEAM_POLICY_DEFAULTS["routing"]["teamLeadConfigured"]))

    @property
    def max_no_answer_attempts(self) -> int:
        return int(self.policy.get("maxNoAnswerAttempts", TEAM_POLICY_DEFAULTS["maxNoAnswerAttempts"]))

    @property
    def auto_stop(self) -> bool:
        """False by default: the no-answer threshold warns, never stops.

        02_LOCKED_BUSINESS_RULES.md treats STOPPED as a human decision, so an
        operator must opt a team into automatic stopping.
        """
        return bool(self.policy.get("autoStop", TEAM_POLICY_DEFAULTS["autoStop"]))


class TeamAssignment(models.Model):
    TEAM_CRM = "CRM_TEAM"
    TEAM_UPSELL = "UPSELL_TEAM"
    TEAM_CHOICES = [
        (TEAM_CRM, "CRM Team"),
        (TEAM_UPSELL, "Upsell Team"),
    ]

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="team_assignments")
    team_code = models.CharField(max_length=16, choices=TEAM_CHOICES)

    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)  # NULL = currently open

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "crm_team_assignment"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(effective_to__isnull=True)
                    | models.Q(effective_to__gt=models.F("effective_from"))
                ),
                name="chk_team_assignment_period",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(effective_to__isnull=True),
                name="ux_team_assignment_current_user",
            ),
            ExclusionConstraint(
                name="ex_team_assignment_period",
                expressions=[
                    ("user", RangeOperators.EQUAL),
                    (
                        models.Func("effective_from", "effective_to", function="tstzrange"),
                        RangeOperators.OVERLAPS,
                    ),
                ],
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-effective_from"], name="ix_team_assignment_user_period"),
            models.Index(
                fields=["team_code", "effective_from", "effective_to"], name="ix_team_assignment_team_period"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.team_code}"
