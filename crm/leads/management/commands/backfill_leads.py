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
from crm.domain.bangkok import day_start, epoch_day
from crm.domain.ownership import derive_routing
from crm.intake.services import upsert_intake
from crm.leads.models import Lead, OwnershipSegment
from crm.leads.order_status import HX63_SNAPSHOT_BASELINE
from crm.leads.recompute import recompute_lead_missed_channel
from crm.teams.models import Team, TeamAssignment

CHUNK_SIZE = 1000


class Command(BaseCommand):
    help = "Create a Lead (or Global Unassigned intake) for every existing Customer."

    def add_arguments(self, parser):
        parser.add_argument(
            "--team-code",
            dest="team_code",
            default=None,
            help=(
                "Fallback team for a caretaker with no open TeamAssignment. "
                "Defaults to the first seeded team."
            ),
        )
        parser.add_argument(
            "--resolve-to",
            dest="resolve_to",
            default=None,
            help=(
                "Team code for customers with no staff_code, instead of queueing "
                "them as Global Unassigned intake. On this database that is the "
                "difference between a 216k-row hand-resolve queue and one command."
            ),
        )
        parser.add_argument(
            "--hx63-baseline",
            dest="hx63_baseline",
            default=None,
            help=(
                "ISO instant to count missed-channel from instead of the backfill "
                "baseline (D023 uses the HX63 snapshot)."
            ),
        )
        parser.add_argument(
            "--no-migrated-baseline",
            dest="no_migrated_baseline",
            action="store_true",
            help=(
                "Do not stamp Lead.migrated_baseline_at. By default every "
                "backfilled lead gets the HX63 snapshot baseline (D023) so "
                "all later recomputes keep counting from it."
            ),
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

        self.resolve_to = None
        if options.get("resolve_to"):
            self.resolve_to = Team.objects.filter(code=options["resolve_to"]).first()
            if self.resolve_to is None:
                raise CommandError(f"unknown --resolve-to team {options['resolve_to']!r}")

        self.hx63_baseline = None
        self.stamp_baseline = not options.get("no_migrated_baseline")
        if options.get("hx63_baseline"):
            from datetime import datetime

            self.hx63_baseline = datetime.fromisoformat(options["hx63_baseline"])
            if self.hx63_baseline.tzinfo is None:
                raise CommandError("--hx63-baseline must carry a timezone offset")

        if team_code is None:
            # Resolved here rather than as an argparse default: a default that
            # queries the DB makes --help run SELECT ... FROM crm_team and
            # crash with ProgrammingError before migrate has created it.
            team_code = Team.objects.values_list("code", flat=True).order_by("code").first()
        fallback_team = Team.objects.filter(code=team_code).first() if team_code else None
        if fallback_team is None:
            # Checked before any write, so a typo cannot half-migrate the table.
            raise CommandError(f"unknown team code {team_code!r}; run seed_teams first")

        # setdefault, not a comprehension: ordering by -is_active puts the
        # active holder first, and a dict comprehension lets the LAST row win,
        # selecting the INACTIVE user when two share a staff_code and then
        # routing the lead to TEAM_UNASSIGNED although an active holder of that
        # code exists. crm/leads/recompute.py already does this correctly.
        caretakers: dict[str, User] = {}
        for candidate in User.objects.exclude(staff_code="").order_by("-is_active", "pk"):
            code = (candidate.staff_code or "").strip()
            if code:
                caretakers.setdefault(code, candidate)
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
        if created_intake > 1000 and self.resolve_to is None:
            # An operator deserves to know BEFORE the real run that they are
            # being handed a queue they must clear one POST at a time.
            self.stdout.write(
                self.style.WARNING(
                    f"{prefix}{created_intake} customers have no staff_code and are queued as "
                    "Global Unassigned. Clearing that queue by hand is one form submission "
                    "per row -- re-run with --resolve-to TEAM_CODE to assign them directly."
                )
            )

    def _process(
        self, batch, caretakers, team_by_user, teams_by_code, fallback_team, dry_run
    ) -> tuple[int, int, int, int]:
        from crm.customers.models import Customer

        created_leads = skipped_leads = created_intake = skipped_intake = 0
        intake_batch: list = []
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
                # D019: team unknown, so pre-Lead intake, never a Lead -- unless
                # the operator named a team up front with --resolve-to.
                if self.resolve_to is None:
                    if dry_run:
                        created_intake += 1
                    else:
                        intake_batch.append(customer)
                    continue
                if pk in existing_leads:
                    # The staff_code branch checks this; without the same check
                    # here a dry run over an already-backfilled table reports
                    # every resolved-to customer as new, so the number an
                    # operator decides on is wrong on a re-run.
                    skipped_leads += 1
                    continue
                if dry_run:
                    created_leads += 1
                    continue
                caretaker = None
                team = self.resolve_to

            else:
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
            # Bangkok DAY START, not the instant. Order business time is
            # datetime.combine(order_date, time.min, BANGKOK_TZ), so a baseline
            # of timezone.now() puts every order dated TODAY before it and the
            # backfill would silently discard cutover-day orders.
            now = day_start(epoch_day(timezone.now()))
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
                        "migrated_baseline_at": (
                            (self.hx63_baseline or HX63_SNAPSHOT_BASELINE)
                            if self.stamp_baseline
                            else None
                        ),
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
                # Recompute only when counting from an explicit earlier
                # baseline. From the backfill's own baseline the answer is 0
                # for every lead by construction, so calling it otherwise is a
                # full order-history read per lead to store a zero.
                if lead.migrated_baseline_at is not None and (rollup.get("order_total") or 0) > 0:
                    recompute_lead_missed_channel(lead=lead)
            created_leads += 1

        if intake_batch:
            # One statement per chunk instead of one transaction + get_or_create
            # per row: at 216k unowned customers that is the difference between
            # ~650k round trips and ~220. ignore_conflicts makes it idempotent
            # against the (customer, source_order_id) unique constraint.
            from crm.intake.models import IntakeItem

            with transaction.atomic():
                before = IntakeItem.objects.filter(
                    customer_id__in=[c.pk for c in intake_batch]
                ).count()
                IntakeItem.objects.bulk_create(
                    [
                        IntakeItem(
                            customer=customer,
                            source_order_id="",
                            source_kind="CRM_CUSTOMERS",
                            source_ref="backfill_leads",
                            status="GLOBAL_UNASSIGNED",
                        )
                        for customer in intake_batch
                    ],
                    ignore_conflicts=True,
                    batch_size=500,
                )
                after = IntakeItem.objects.filter(
                    customer_id__in=[c.pk for c in intake_batch]
                ).count()
            created_intake += after - before
            skipped_intake += len(intake_batch) - (after - before)

        return created_leads, skipped_leads, created_intake, skipped_intake
