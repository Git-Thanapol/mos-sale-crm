from django.urls import path

from crm.customers import views

app_name = "customers"

urlpatterns = [
    path("", views.index, name="list"),
    path("export.xlsx", views.export_xlsx, name="export"),
    path("<int:customer_id>/", views.detail, name="detail"),
    path("<int:customer_id>/follow-marker/", views.save_follow_marker_view, name="follow_marker"),
    path("<int:customer_id>/owner/", views.assign_owner_view, name="assign_owner"),
    path("<int:customer_id>/url/", views.assign_url_view, name="assign_url"),
]
