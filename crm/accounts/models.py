from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

from crm.accounts.managers import UserManager
from crm.core.permissions import ROLE_OPTIONS, ROLE_VIEWER


class User(AbstractBaseUser, PermissionsMixin):
    """Replaces crm_user_roles. `role` is deliberately a free-text CharField
    with `choices` for UI purposes but no DB CHECK constraint — an unknown
    role string must degrade to "no permissions" via
    crm.core.permissions.normalize_role, exactly like the legacy app, rather
    than fail at the database layer. See docs/DECISIONS.md.

    is_staff/is_superuser are Django /admin gates only. They are NOT part of
    the CRM permission matrix (crm.core.permissions) — do not check them in
    application views.
    """

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=64, choices=[(r, r) for r in ROLE_OPTIONS], default=ROLE_VIEWER)
    staff_code = models.CharField(max_length=32, blank=True, db_index=True)
    staff_name = models.CharField(max_length=128, blank=True)
    owner_alias = models.CharField(max_length=128, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        constraints = [
            models.UniqueConstraint(Lower("email"), name="ux_user_email_lower"),
        ]
        indexes = [
            models.Index(fields=["staff_code"], name="ix_user_staff_code_active", condition=models.Q(is_active=True)),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        super().save(*args, **kwargs)
