from django.urls import path

from crm.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.CrmLoginView.as_view(), name="login"),
    path("logout/", views.CrmLogoutView.as_view(), name="logout"),
    path("password-change/", views.ForcePasswordChangeView.as_view(), name="password_change"),
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.create_user, name="create_user"),
    path("users/<int:user_id>/save/", views.save_user, name="save_user"),
    path("users/<int:user_id>/deactivate/", views.deactivate_user, name="deactivate_user"),
    path("users/<int:user_id>/reset-password/", views.reset_password, name="reset_password"),
]
