from django.urls import path

from crm.catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.index, name="list"),
    path("create/", views.create, name="create"),
    path("<int:product_id>/save/", views.save_row, name="save_row"),
    path("<int:product_id>/deactivate/", views.deactivate, name="deactivate"),
    path("bulk/", views.bulk_action, name="bulk_action"),
    path("import/", views.import_view, name="import"),
    path("options/", views.options, name="options"),
]
