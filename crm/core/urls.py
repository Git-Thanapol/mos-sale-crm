from django.urls import path

from crm.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("system-status/", views.system_status, name="system_status"),
    path("settings/", views.settings_page, name="settings"),
]
