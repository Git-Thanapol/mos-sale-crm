from django.urls import path

from crm.reporting import views

app_name = "reporting"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("delete/", views.delete_sales, name="delete_sales"),
]
