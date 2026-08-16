from django.urls import path

from crm.imports import views

app_name = "imports"

urlpatterns = [
    path("", views.import_excel, name="upload"),
    path("status/<int:job_id>/", views.import_status, name="status"),
]
