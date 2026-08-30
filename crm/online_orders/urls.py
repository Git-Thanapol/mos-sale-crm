from django.urls import path

from crm.online_orders import views

app_name = "online_orders"

urlpatterns = [
    path("", views.index, name="list"),
]
