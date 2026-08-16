from django.urls import path

from crm.teams import views

app_name = "teams"

urlpatterns = [
    path("", views.index, name="list"),
    path("assign/<int:user_id>/", views.save_assignment, name="assign"),
]
