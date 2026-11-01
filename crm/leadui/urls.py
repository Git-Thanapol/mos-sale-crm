from django.urls import path

from crm.leadui import views

app_name = "leadui"

urlpatterns = [
    path("work/", views.my_work, name="my_work"),
    path("global-unassigned/", views.global_unassigned, name="global_unassigned"),
    path("team-unassigned/", views.team_unassigned, name="team_unassigned"),
    path("stopped/", views.stopped_review, name="stopped_review"),
    path("lead/<int:lead_id>/", views.lead_detail, name="lead_detail"),
    path("customer/<int:customer_id>/", views.customer_detail, name="customer_detail"),
    path(
        "customer/<int:customer_id>/history/",
        views.customer_history,
        name="customer_history",
    ),
]
