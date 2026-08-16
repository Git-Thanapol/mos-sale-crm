from django.urls import path

from crm.orders import views

app_name = "orders"

urlpatterns = [
    path("new/", views.new_order, name="new"),
]
