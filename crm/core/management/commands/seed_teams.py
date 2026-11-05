"""Seed the Team rows for the team codes this deployment already uses.

TeamAssignment.TEAM_CHOICES was the only place a team existed before Lead
needed to point at one, so it is the source for this seed rather than a new
hard-coded list here. Upserts by `code` and never overwrites a policy an
operator has edited -- re-running it must not silently revert a team from
LATEST_ORDER back to the seeded EXCLUSIVE.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.teams.models import Team, TeamAssignment

#: Conservative default: EXCLUSIVE never lets another closer take a lead
#: silently, so a wrong guess here cannot move ownership on its own.
DEFAULT_POLICY = {"ownershipPolicy": "EXCLUSIVE"}


class Command(BaseCommand):
    help = "Create or update a Team row for every team code in TeamAssignment.TEAM_CHOICES."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        kept_count = 0
        for code, display_name in TeamAssignment.TEAM_CHOICES:
            team, created = Team.objects.get_or_create(
                code=code,
                defaults={
                    "name": display_name,
                    "display_name": display_name,
                    "active": True,
                    "policy": dict(DEFAULT_POLICY),
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"created team {code} ({display_name}) policy={team.policy}")
                continue
            kept_count += 1
            # Only fill a policy that is missing or unusable; an edited one is
            # the operator's and is left exactly as found.
            if not team.policy.get("ownershipPolicy"):
                team.policy = {**dict(DEFAULT_POLICY), **(team.policy or {})}
                team.save(update_fields=["policy", "updated_at"])
                self.stdout.write(f"backfilled missing ownershipPolicy on {code}")
            else:
                self.stdout.write(f"kept team {code} policy={team.policy}")
        self.stdout.write(self.style.SUCCESS(f"seed_teams: {created_count} created, {kept_count} already present"))
