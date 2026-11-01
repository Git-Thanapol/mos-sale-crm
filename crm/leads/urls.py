from django.urls import path

from crm.leads import views

app_name = "leads"

urlpatterns = [
    path("<int:lead_id>/reassign/", views.reassign, name="reassign"),
    path("<int:lead_id>/stop/", views.stop, name="stop"),
    path("<int:lead_id>/reactivate/", views.reactivate, name="reactivate"),
    path("<int:lead_id>/log-attempt/", views.log_attempt, name="log_attempt"),
    path("<int:lead_id>/appointment/", views.set_appointment_view, name="set_appointment"),
    path("<int:lead_id>/note/", views.add_note, name="add_note"),
    path("<int:lead_id>/lifecycle/", views.set_lifecycle, name="set_lifecycle"),
    path(
        "appointment/<int:appointment_id>/close/",
        views.close_appointment,
        name="close_appointment",
    ),
    path("intake/<int:intake_id>/resolve/", views.resolve_intake, name="resolve_intake"),
]
