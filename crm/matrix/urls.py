from django.urls import path

from crm.matrix import views

app_name = "matrix"

urlpatterns = [
    path("", views.index, name="index"),
    path("holiday/save/", views.save_holiday, name="save_holiday"),
    path("holiday/<int:holiday_id>/delete/", views.remove_holiday, name="delete_holiday"),
]
