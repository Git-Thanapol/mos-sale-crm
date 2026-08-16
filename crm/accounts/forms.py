from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from crm.accounts.models import User

OWNER_PLACEHOLDER = "ไม่เลือก owner mapping"


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="อีเมล", widget=forms.EmailInput(attrs={"autofocus": True, "class": "crm-input"})
    )
    password = forms.CharField(label="รหัสผ่าน", widget=forms.PasswordInput(attrs={"class": "crm-input"}))


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
        fields = ["email", "role", "staff_code", "staff_name", "owner_alias", "is_active"]

    def __init__(self, *args, owners: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", OWNER_PLACEHOLDER)] + [(o, o) for o in (owners or [])]
        self.fields["owner_alias"] = forms.ChoiceField(choices=choices, required=False, label="Owner mapping")
