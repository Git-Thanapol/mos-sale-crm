from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("crm.accounts.urls")),
    path("", include("crm.core.urls")),
    path("customers/", include("crm.customers.urls")),
    path("followup/", include("crm.followups.urls")),
    path("orders/", include("crm.orders.urls")),
    path("orders/import/", include("crm.imports.urls")),
    path("products/", include("crm.catalog.urls")),
    path("team-sales/", include("crm.teams.urls")),
    path("daily-sales-matrix/", include("crm.matrix.urls")),
    path("dashboard/", include("crm.reporting.urls")),
]
