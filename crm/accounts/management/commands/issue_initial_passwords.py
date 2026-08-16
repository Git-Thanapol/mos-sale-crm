import csv

from django.core.management.base import BaseCommand, CommandError

from crm.accounts.models import User
from crm.accounts.services import issue_password


class Command(BaseCommand):
    help = (
        "Generate a fresh 16-char password for each user with an unusable "
        "password (or --all / --emails), write email+password to a CSV once, "
        "and set must_change_password=True. Hand-deliver the CSV out of band "
        "and delete it afterward — this is the only place the plaintext "
        "password exists."
    )

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Path to write the generated email,password CSV")
        parser.add_argument("--emails", nargs="*", help="Only these emails (default: every user with no usable password)")
        parser.add_argument("--all", action="store_true", help="Reissue for every active user, even ones with a working password")

    def handle(self, *args, **options):
        qs = User.objects.filter(is_active=True)
        if options["emails"]:
            qs = qs.filter(email__in=[e.strip().lower() for e in options["emails"]])
        elif not options["all"]:
            qs = [u for u in qs if not u.has_usable_password()]

        users = list(qs)
        if not users:
            self.stdout.write(self.style.WARNING("no matching users"))
            return

        try:
            fh = open(options["out"], "w", newline="", encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"cannot write {options['out']}: {exc}") from exc

        with fh:
            writer = csv.writer(fh)
            writer.writerow(["email", "password"])
            for user in users:
                password = issue_password(user)
                writer.writerow([user.email, password])

        self.stdout.write(self.style.SUCCESS(f"issued {len(users)} password(s) to {options['out']}"))
        self.stdout.write(
            self.style.WARNING("Deliver this file out of band and delete it once handed off.")
        )
