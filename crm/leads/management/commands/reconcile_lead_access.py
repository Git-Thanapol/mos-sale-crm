"""Re-route leads whose caretaker is no longer active.

A drift repair. The deactivate view calls reconcile_lead_access directly, but a
user disabled by any other route (a shell, a fixture, a future admin action)
leaves leads pointing at an account that cannot act -- and those leads never
reach the Team Unassigned queue, so nobody picks them up.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from crm.accounts.models import User
from crm.leads.models import Lead
from crm.leads.services import reconcile_lead_access


class Command(BaseCommand):
    help = "Re-route every Lead whose caretaker is inactive."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", dest="dry_run", action="store_true")

    def handle(self, *args, **options):
        stale = (
            Lead.objects.filter(caretaker__isnull=False, caretaker__is_active=False)
            .values_list("caretaker_id", flat=True)
            .distinct()
        )
        user_ids = list(stale)
        if options["dry_run"]:
            count = Lead.objects.filter(caretaker_id__in=user_ids).count()
            self.stdout.write(f"[dry-run] would re-route {count} lead(s) across {len(user_ids)} user(s)")
            return

        total = 0
        for user in User.objects.filter(pk__in=user_ids):
            affected = reconcile_lead_access(user=user)
            total += affected
            self.stdout.write(f"re-routed {affected} lead(s) from inactive user {user.email}")
        self.stdout.write(self.style.SUCCESS(f"reconcile_lead_access: {total} lead(s) re-routed"))
