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

from crm.core.permissions import (
    can_manage_all,
    clean,
    current_team_codes,
    is_team_lead,
    is_telesell,
    team_scope,
)


class StaffScopedQuerySet(models.QuerySet):
    def for_user(self, user) -> "StaffScopedQuerySet":
        if can_manage_all(user):
            return self

        # A TEAM_LEAD is scoped by authorized team, not by staff_code: they
        # work their whole team's leads, not only customers assigned to them.
        # TEAM_PATH names the row's own team, never a customer-wide union,
        # which spans teams and would leak team B to a team-A lead.
        # Fail-closed: no TEAM_PATH or an empty scope sees nothing.
        if is_team_lead(user):
            team_path = getattr(self.model, "TEAM_PATH", None)
            scope = team_scope(user)
            if not team_path or not scope:
                return self.none()
            return self.filter(**{f"{team_path}__in": sorted(scope)})

        code = clean(getattr(user, "staff_code", "")) if user else ""
        path = getattr(self.model, "STAFF_CODE_PATH", None)
        team_path = getattr(self.model, "TEAM_PATH", None)

        # A model declaring TEAM_PATH is team-dimensioned (today: Lead). It
        # mirrors crm.core.permissions.can_view_lead exactly -- caretaker OR
        # (own customer AND the lead's team is one of mine) -- because a
        # queryset that admits a row the predicate rejects, or the reverse, is
        # how a leak gets in.
        #
        # The caretaker leg comes BEFORE the staff_code gate: a caretaker with
        # a blank staff_code would otherwise be able to mutate a lead by URL
        # (the predicate allows it) while seeing it in no queue at all.
        if team_path and is_telesell(user) and getattr(user, "pk", None) is not None:
            if not path:
                raise NotImplementedError(
                    f"{self.model.__name__} declares TEAM_PATH but no STAFF_CODE_PATH"
                )
            visible = models.Q(caretaker_id=user.pk)
            if code:
                visible |= models.Q(
                    **{path: code, f"{team_path}__in": sorted(current_team_codes(user))}
                )
            return self.filter(visible)

        if not code:
            return self.none()

        if not path:
            raise NotImplementedError(
                f"{self.model.__name__} uses StaffScopedQuerySet but has no STAFF_CODE_PATH"
            )

        return self.filter(**{path: code})
