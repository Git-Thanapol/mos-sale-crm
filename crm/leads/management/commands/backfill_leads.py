"""Turn the existing Customer rows into Leads and Global Unassigned intake.

Ownership in this schema is a `staff_code` string on Customer. This command
reads that and produces the Lead-shaped world the CRM V2 rules need:

- A customer WITH a staff_code becomes a Lead with an open ownership segment.
  The team comes from that caretaker's open TeamAssignment when there is one,
  otherwise from --team-code.
- A customer WITHOUT a staff_code becomes an IntakeItem. That is exactly D019:
  the customer exists, the team is unknown, so no Lead exists yet. It is NOT
  given a placeholder team.

Idempotent and restartable. The live table has ~216k rows, so work happens in
bounded chunks inside per-chunk transactions rather than one long-running
transaction holding locks across the whole table -- a backfill that cannot be
re-run safely is one nobody dares run twice.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from crm.accounts.models import User
from crm.domain.access import derive_lead_access
from crm.domain.ownership import derive_routing
from crm.intake.services import upsert_intake
from crm.leads.models import Lead, OwnershipSegment
from crm.leads.recompute import recompute_lead_missed_channel
from crm.teams.models import Team, TeamAssignment

CHUNK_SIZE = 1000


class Command(BaseCommand):
    help = "Create a Lead (or Global Unassigned intake) for every existing Customer."

    def add_arguments(self, parser):
        parser.add_argument(
            "--team-code",
            dest="team_code",
            default=Team.objects.values_list("code", flat=True).first() or "CRM_TEAM",
            help="Fallback team for a caretaker with no open TeamAssignment.",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Report what would be written without writing it.",
        )

    def handle(self, *args, **options):
        from crm.customers.models import Customer

        team_code = options["team_code"]
        dry_run = options["dry_run"]

        fallback_team = Team.objects.filter(code=team_code).first()
        if fallback_team is None:
            # Checked before any write, so a typo cannot half-migrate the table.
            raise CommandError(f"unknown team code {team_code!r}; run seed_teams first")

        caretakers = {
            code.strip(): user
            for user, code in (
                (user, user.staff_code)
                for user in User.objects.exclude(staff_code="").order_by("-is_active", "pk")
            )
            if code.strip()
        }
        team_by_user = {
            user_id: code
            for user_id, code in TeamAssignment.objects.filter(effective_to__isnull=True).values_list(
                "user_id", "team_code"
            )
        }
        teams_by_code = {team.code: team for team in Team.objects.all()}

        created_leads = skipped_leads = created_intake = skipped_intake = 0

        pending = Customer.objects.order_by("pk").values_list(
            "pk", "staff_code"
        )
        batch: list[tuple[int, str]] = []
        for row in pending.iterator(chunk_size=CHUNK_SIZE):
            batch.append(row)
            if len(batch) >= CHUNK_SIZE:
                counts = self._process(
                    batch, caretakers, team_by_user, teams_by_code, fallback_team, dry_run
                )
                created_leads += counts[0]
                skipped_leads += counts[1]
                created_intake += counts[2]
                skipped_intake += counts[3]
                batch = []
        if batch:
            counts = self._process(
                batch, caretakers, team_by_user, teams_by_code, fallback_team, dry_run
            )
            created_leads += counts[0]
            skipped_leads += counts[1]
            created_intake += counts[2]
            skipped_intake += counts[3]

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}leads created {created_leads}, already present {skipped_leads}; "
                f"intake created {created_intake}, already present {skipped_intake}"
            )
        )

    def _process(
        self, batch, caretakers, team_by_user, teams_by_code, fallback_team, dry_run
    ) -> tuple[int, int, int, int]:
        from crm.customers.models import Customer

        created_leads = skipped_leads = created_intake = skipped_intake = 0
        customer_ids = [pk for pk, _ in batch]
        customers = {
            customer.pk: customer for customer in Customer.objects.filter(pk__in=customer_ids)
        }
        existing_leads = set(
            Lead.objects.filter(customer_id__in=customer_ids).values_list("customer_id", flat=True)
        )
        rollups = {
            row["pk"]: row
            for row in Customer.objects.filter(pk__in=customer_ids)
            .annotate(
                order_total=Count("orders", distinct=True),
                newest_order=Max("orders__order_date"),
            )
            .values("pk", "order_total", "newest_order")
        }

        for pk, staff_code in batch:
            customer = customers.get(pk)
            if customer is None:
                continue
            code = (staff_code or "").strip()

            if not code:
                # D019: team unknown, so pre-Lead intake, never a Lead.
                if dry_run:
                    created_intake += 1
                    continue
                with transaction.atomic():
                    _, created = upsert_intake(
                        customer=customer,
                        source_order_id="",
                        source_kind="CRM_CUSTOMERS",
                        source_ref="backfill_leads",
                    )
                created_intake += 1 if created else 0
                skipped_intake += 0 if created else 1
                continue

            if pk in existing_leads:
                skipped_leads += 1
                continue
            if dry_run:
                created_leads += 1
                continue

            caretaker = caretakers.get(code)
            team = fallback_team
            if caretaker is not None:
                assigned_code = team_by_user.get(caretaker.pk)
                if assigned_code and assigned_code in teams_by_code:
                    team = teams_by_code[assigned_code]

            caretaker_active = caretaker is not None and caretaker.is_active
            effective = caretaker if caretaker_active else None
            now = timezone.now()
            rollup = rollups.get(pk) or {}
            newest = rollup.get("newest_order")
            last_order_at = None
            if newest is not None:
                from datetime import datetime, time

                from crm.domain.bangkok import BANGKOK_TZ

                last_order_at = datetime.combine(newest, time.min, tzinfo=BANGKOK_TZ)

            routing = derive_routing(
                team_code=team.code,
                caretaker_user_id=str(effective.pk) if effective else None,
                caretaker_active=caretaker_active,
                team_lead_configured=team.team_lead_configured,
            )
            access = derive_lead_access(
                team.code,
                str(effective.pk) if effective else None,
                frozenset({str(effective.pk)}) if effective else frozenset(),
            )

            with transaction.atomic():
                lead, created = Lead.objects.get_or_create(
                    team=team,
                    customer=customer,
                    defaults={
                        "caretaker": effective,
                        "lifecycle_status": "NEW",
                        "routing_state": routing.routing_state,
                        "ownership_policy": team.ownership_policy,
                        "ownership_started_at": now,
                        "last_order_at": last_order_at,
                        "total_orders": rollup.get("order_total", 0) or 0,
                        "access_team_ids": access["access_team_ids"],
                        "access_user_ids": access["access_user_ids"],
                    },
                )
                if not created:
                    skipped_leads += 1
                    continue
                OwnershipSegment.objects.create(
                    lead=lead,
                    caretaker=effective,
                    started_at=now,
                    reason="BACKFILL",
                )
                recompute_lead_missed_channel(lead=lead)
            created_leads += 1

        return created_leads, skipped_leads, created_intake, skipped_intake
