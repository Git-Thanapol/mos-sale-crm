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

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models


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
