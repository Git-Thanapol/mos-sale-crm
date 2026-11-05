from django.urls import path

from crm.leadui import import_views as imports
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
    # CRM V2 import: upload, preview, confirm/commit, conflicts, history.
    path("import/", imports.import_page, name="import"),
    path("import/upload/", imports.import_upload, name="import_upload"),
    path("import/decisions/", imports.decisions_page, name="migration_decisions"),
    path("import/decisions/<str:decision_id>/resolve/", imports.decision_resolve, name="decision_resolve"),
    path("import/decisions/<str:decision_id>/reopen/", imports.decision_reopen, name="decision_reopen"),
    path("import/conflicts/", imports.conflicts_page, name="import_conflicts"),
    path("import/conflicts/<int:conflict_id>/resolve/", imports.conflict_resolve, name="conflict_resolve"),
    path("import/history/", imports.history_page, name="import_history"),
    path("import/<uuid:batch_uuid>/preview/", imports.import_preview, name="import_preview"),
    path("import/<uuid:batch_uuid>/date/", imports.import_date, name="import_date"),
    path("import/<uuid:batch_uuid>/confirm/", imports.import_confirm, name="import_confirm"),
    path("import/<uuid:batch_uuid>/commit/", imports.import_commit, name="import_commit"),
    path("import/<uuid:batch_uuid>/rollback/", imports.history_rollback, name="history_rollback"),
]
