from django.urls import path

from crm.followups import views

app_name = "followups"

urlpatterns = [
    path("", views.index, name="list"),
    path("<int:followup_id>/save/", views.save_followup_view, name="save"),
    path("<int:followup_id>/add-order/", views.add_order_view, name="add_order"),
]
