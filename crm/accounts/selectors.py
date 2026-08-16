"""Read queries for /accounts/users/: the owner-mapping dropdown source
and the visibility tester.

visibility_summary reuses Customer.objects.for_user() — the exact same
scoping code path the rest of the app runs through — instead of
reimplementing the scope predicate a second time. Legacy's equivalent
(_followup_staff_scope, hand-copied into the tester) is why its "mapping
notice" copy could silently drift from the real rule (see docs/legacy
exploration: the legacy notice claims a staff_name/owner_alias fallback
that was never actually implemented). Testing through for_user() makes
that class of drift structurally impossible here.
"""

from __future__ import annotations

from crm.customers.models import Customer


def owner_options() -> list[str]:
    return list(
        Customer.objects.exclude(owner_display="")
        .order_by("owner_display")
        .values_list("owner_display", flat=True)
        .distinct()[:500]
    )


def visibility_summary(target_user) -> dict:
    qs = Customer.objects.for_user(target_user)
    total = qs.count()
    samples = list(
        qs.order_by("-updated_at").values(
            "customer_name", "phone1", "phone2", "owner_display", "staff_code"
        )[:10]
    )
    return {"total": total, "samples": samples}
