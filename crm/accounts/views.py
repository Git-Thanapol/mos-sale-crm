from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from crm.accounts import selectors, services
from crm.accounts.forms import ForcePasswordChangeForm, LoginForm, UserAdminForm
from crm.accounts.models import User
from crm.core.permissions import can_edit_users, require_permission


class CrmLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class CrmLogoutView(LogoutView):
    next_page = "accounts:login"


class ForcePasswordChangeView(PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = ForcePasswordChangeForm
    success_url = reverse_lazy("core:home")


@login_required
def user_list(request):
    # Legacy: everyone logged in can view + use the visibility tester;
    # only can_edit_users may create/edit/deactivate/reset a password —
    # enforced on the write views below, not here.
    can_manage = can_edit_users(request.user)
    owners = selectors.owner_options()
    users = list(User.objects.order_by("-is_active", "email"))

    # create_form's field.choices doubles as the shared role/owner dropdown
    # source for every per-user edit form in the template — those choice
    # sets don't vary per user, so there's no need for one ModelForm
    # instance per row.
    create_form = UserAdminForm(owners=owners) if can_manage else None

    tester_email = (request.GET.get("test_email") or "").strip().lower()
    tester_result = None
    if tester_email:
        target = User.objects.filter(email=tester_email).first()
        if target is None:
            messages.error(request, "ไม่พบ user นี้ใน accounts.User")
        else:
            tester_result = {
                "email": target.email,
                "role": target.role,
                "staff_code": target.staff_code,
                "staff_name": target.staff_name,
                "owner_alias": target.owner_alias,
                **selectors.visibility_summary(target),
            }

    context = {
        "users": users,
        "can_manage": can_manage,
        "create_form": create_form,
        "tester_email": tester_email,
        "tester_result": tester_result,
        "all_emails": [u.email for u in users],
    }
    return render(request, "accounts/user_list.html", context)


@login_required
@require_permission("can_edit_users")
def create_user(request):
    if request.method != "POST":
        return redirect("accounts:user_list")

    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        messages.error(request, "กรุณากรอก email")
        return redirect("accounts:user_list")

    existing = User.objects.filter(email=email).first()
    form = UserAdminForm(request.POST, instance=existing, owners=selectors.owner_options())
    if not form.is_valid():
        messages.error(request, "ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบฟอร์ม")
        return redirect("accounts:user_list")

    user = form.save(commit=False)
    if existing is None:
        user.set_unusable_password()  # matches manage.py seed_users; issue a password separately
    user.save()
    messages.success(request, "บันทึก User / Role แล้ว")
    return redirect("accounts:user_list")


@login_required
@require_permission("can_edit_users")
def save_user(request, user_id: int):
    if request.method != "POST":
        return redirect("accounts:user_list")
    user = get_object_or_404(User, pk=user_id)

    form = UserAdminForm(request.POST, instance=user, owners=selectors.owner_options())
    if not form.is_valid():
        messages.error(request, "ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบฟอร์ม")
        return redirect("accounts:user_list")
    try:
        form.save()
    except IntegrityError:
        messages.error(request, "อีเมลนี้ถูกใช้งานโดยผู้ใช้อื่นแล้ว")
        return redirect("accounts:user_list")

    messages.success(request, "บันทึก User / Role แล้ว")
    return redirect("accounts:user_list")


@login_required
@require_permission("can_edit_users")
def deactivate_user(request, user_id: int):
    if request.method != "POST":
        return redirect("accounts:user_list")
    user = get_object_or_404(User, pk=user_id)

    # Not a legacy behavior — legacy has no self-deactivation guard at all
    # (docs/legacy exploration flagged this as an open question). Blocking
    # it here is a deliberate improvement: there is no self-service reset
    # and no SMTP, so a lone EDITOR deactivating their own account would
    # be an unrecoverable lockout.
    if user_id == request.user.id:
        raise PermissionDenied("ไม่สามารถปิดใช้งานบัญชีของตัวเองได้")

    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "ปิดใช้งาน user แล้ว")
    return redirect("accounts:user_list")


@login_required
@require_permission("can_edit_users")
def reset_password(request, user_id: int):
    if request.method != "POST":
        return redirect("accounts:user_list")
    user = get_object_or_404(User, pk=user_id)
    password = services.issue_password(user)
    messages.success(
        request,
        f"ออกรหัสผ่านใหม่ให้ {user.email} แล้ว: {password} "
        "(แสดงครั้งนี้ครั้งเดียว โปรดคัดลอกและส่งให้ผู้ใช้ทันที)",
    )
    return redirect("accounts:user_list")
