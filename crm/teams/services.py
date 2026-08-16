"""Write path for /team-sales: the team-assignment save/clear transaction.

Ported from crm_data/team_sales.py::_set_user_team_assignment. Same shape:
lock the current open row (if any) with SELECT ... FOR UPDATE, no-op on an
unchanged target, close the current row's effective_to, then either stop
(clear) or insert a fresh open row. The `effective_from + 1 microsecond`
nudge when clock_timestamp() would land exactly on (or before) the current
row's effective_from is preserved verbatim — it's what keeps the
ExclusionConstraint from ever firing on a legitimate same-instant edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from crm.teams.models import TeamAssignment


@dataclass(frozen=True)
class TeamAssignmentResult:
    changed: bool
    action: str  # "unchanged" | "created" | "changed" | "cleared"
    team_code: str | None


@transaction.atomic
def assign_team(*, actor, target_user, team_code: str | None) -> TeamAssignmentResult:
    if team_code is not None and team_code not in dict(TeamAssignment.TEAM_CHOICES):
        raise ValueError("team_code must be CRM_TEAM, UPSELL_TEAM, or None")

    current = (
        TeamAssignment.objects.select_for_update()
        .filter(user=target_user, effective_to__isnull=True)
        .order_by("-effective_from")
        .first()
    )

    if current is None and team_code is None:
        return TeamAssignmentResult(changed=False, action="unchanged", team_code=None)
    if current is not None and current.team_code == team_code:
        return TeamAssignmentResult(changed=False, action="unchanged", team_code=team_code)

    now = timezone.now()
    if current is not None and now <= current.effective_from:
        now = current.effective_from + timedelta(microseconds=1)

    actor_label = (getattr(actor, "email", "") or "").strip()

    if current is not None:
        current.effective_to = now
        current.updated_by = actor_label
        current.save(update_fields=["effective_to", "updated_by", "updated_at"])

    if team_code is None:
        return TeamAssignmentResult(changed=True, action="cleared", team_code=None)

    TeamAssignment.objects.create(
        user=target_user,
        team_code=team_code,
        effective_from=now,
        created_by=actor_label,
        updated_by=actor_label,
    )
    action = "created" if current is None else "changed"
    return TeamAssignmentResult(changed=True, action=action, team_code=team_code)
