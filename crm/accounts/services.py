"""Write path for /accounts/users/: password issuance.

generate_password/issue_password are shared with `manage.py
issue_initial_passwords` (one implementation, two entry points, so the
CLI and the web button can't drift — same pattern as
crm.imports.services.import_workbook). User create/edit itself goes
through UserAdminForm directly (crm/accounts/forms.py) — no separate
upsert helper needed.
"""

from __future__ import annotations

import secrets
import string

from crm.accounts.models import User

_ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def issue_password(user: User) -> str:
    """Sets a fresh random password, forces a change on next login, and
    returns the plaintext once. The caller must display it exactly once
    (message banner, CSV, ...) — it is never stored anywhere in plaintext.
    """
    password = generate_password()
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password"])
    return password
