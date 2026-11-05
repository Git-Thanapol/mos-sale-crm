from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from crm.accounts.models import User

OWNER_PLACEHOLDER = "ไม่เลือก owner mapping"


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="อีเมล",
        widget=forms.EmailInput(attrs={"autofocus": True, "class": "crm-input", "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="รหัสผ่าน",
        widget=forms.PasswordInput(attrs={"class": "crm-input", "autocomplete": "current-password"}),
    )


class ForcePasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="รหัสผ่านเดิม",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "autofocus": True}),
    )
    new_password1 = forms.CharField(
        label="รหัสผ่านใหม่",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="อย่างน้อย 12 ตัวอักษร ไม่ใช่รหัสที่คาดเดาง่ายหรือเป็นตัวเลขล้วน",
    )
    new_password2 = forms.CharField(
        label="ยืนยันรหัสผ่านใหม่",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "crm-input")

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        user.must_change_password = False
        if commit:
            user.save()
        return user


class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "email",
            "role",
            "staff_code",
            "staff_name",
            "owner_alias",
            "team_scope",
            "is_active",
        ]

    def __init__(self, *args, owners: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", OWNER_PLACEHOLDER)] + [(o, o) for o in (owners or [])]
        self.fields["owner_alias"] = forms.ChoiceField(choices=choices, required=False, label="Owner mapping")

        # team_scope is the TEAM_LEAD authorization scope. Without it on the
        # form there is no way to provision that role outside a shell, which
        # made the whole TEAM_LEAD path dead in production.
        from crm.teams.models import Team

        self.fields["team_scope"] = forms.MultipleChoiceField(
            choices=[(t.code, t.display_name or t.code) for t in Team.objects.order_by("code")],
            required=False,
            label="ทีมที่ดูแล (TEAM_LEAD)",
            help_text="ใช้กับบทบาท TEAM_LEAD เท่านั้น",
        )

    def clean_team_scope(self):
        # Only a TEAM_LEAD carries a scope; storing one on another role would
        # be inert state that a future predicate might start honouring.
        scope = self.cleaned_data.get("team_scope") or []
        role = (self.data.get("role") or self.initial.get("role") or "").strip()
        return list(scope) if role == "TEAM_LEAD" else []
