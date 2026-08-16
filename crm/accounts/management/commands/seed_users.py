import csv

from django.core.management.base import BaseCommand, CommandError

from crm.accounts.models import User


class Command(BaseCommand):
    help = (
        "Create/update accounts.User rows from a CSV with columns: "
        "email, role, staff_code, staff_name, owner_alias. "
        "New users get set_unusable_password() and must_change_password=True — "
        "run issue_initial_passwords afterward to hand out real passwords. "
        "Existing users (matched by lowercased email) are updated in place; "
        "their password is left untouched."
    )

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=True, help="Path to the users CSV (see seed/users.csv)")

    def handle(self, *args, **options):
        path = options["csv"]
        try:
            fh = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"cannot open {path}: {exc}") from exc

        created, updated = 0, 0
        with fh:
            reader = csv.DictReader(fh)
            required = {"email", "role", "staff_code", "staff_name", "owner_alias"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV missing columns: {sorted(missing)}")

            for row in reader:
                email = row["email"].strip().lower()
                if not email:
                    continue
                defaults = {
                    "role": row["role"].strip(),
                    "staff_code": row["staff_code"].strip(),
                    "staff_name": row["staff_name"].strip(),
                    "owner_alias": row["owner_alias"].strip(),
                }
                user, was_created = User.objects.get_or_create(
                    email=email,
                    defaults={**defaults, "must_change_password": True},
                )
                if was_created:
                    user.set_unusable_password()
                    user.save(update_fields=["password"])
                    created += 1
                    self.stdout.write(f"created  {email} ({defaults['role']})")
                else:
                    for field, value in defaults.items():
                        setattr(user, field, value)
                    user.save(update_fields=list(defaults))
                    updated += 1
                    self.stdout.write(f"updated  {email} ({defaults['role']})")

        self.stdout.write(self.style.SUCCESS(f"done: {created} created, {updated} updated"))
        if created:
            self.stdout.write(
                self.style.WARNING(
                    "New users have no usable password yet. "
                    "Run: python manage.py issue_initial_passwords --csv out.csv"
                )
            )
