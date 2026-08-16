"""Row-level visibility. The mechanical fix for a rule the legacy app
duplicated four times with two different SQL shapes
(_followup_staff_scope, _fetch_dashboard_kpis, _sales_report_where,
can_view_customer_detail) — see docs/legacy/data-layer-report.md §4.

Every scoped model gets StaffScopedQuerySet as its manager and declares
STAFF_CODE_PATH, the ORM lookup path from that model to a staff_code column:

    class Customer(models.Model):
        STAFF_CODE_PATH = "staff_code"
        objects = StaffScopedQuerySet.as_manager()

    class Followup(models.Model):
        STAFF_CODE_PATH = "customer__staff_code"
        objects = StaffScopedQuerySet.as_manager()

Then every selector calls .for_user(user) explicitly:

    Customer.objects.for_user(user).filter(...)

Fail-closed is load-bearing: a non-manager with no staff_code gets .none(),
never "everything" and never an unfiltered queryset. See
tests/db/test_scoping.py::test_staff_without_staff_code_sees_nothing.
"""

from __future__ import annotations

from django.db import models

from crm.core.permissions import can_manage_all, clean


class StaffScopedQuerySet(models.QuerySet):
    def for_user(self, user) -> "StaffScopedQuerySet":
        if can_manage_all(user):
            return self

        code = clean(getattr(user, "staff_code", "")) if user else ""
        if not code:
            return self.none()

        path = getattr(self.model, "STAFF_CODE_PATH", None)
        if not path:
            raise NotImplementedError(
                f"{self.model.__name__} uses StaffScopedQuerySet but has no STAFF_CODE_PATH"
            )
        return self.filter(**{path: code})
