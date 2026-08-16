from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from crm.accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Django /admin only — for support access, not the CRM-facing /users page.
    See crm.accounts.views.user_list for the CRM role-management screen.
    """

    ordering = ("email",)
    list_display = ("email", "role", "staff_code", "staff_name", "is_active", "is_staff")
    search_fields = ("email", "staff_code", "staff_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("CRM role", {"fields": ("role", "staff_code", "staff_name", "owner_alias")}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "must_change_password")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role", "staff_code")}),
    )
