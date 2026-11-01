"""Denormalised authorization arrays, ported from $PROTO/src/security/access-arrays.ts.

These are read models only. Lead / Team / caretaker plus active-user state are
the authority; every server mutation recomputes the arrays, and callers never
supply them as trusted input (08_SECURITY_AND_AUTH_REQUIREMENTS.md: "Never
trust client-provided actorId, role, Team scope, caretaker scope").

The deactivation rule is the one that matters: a disabled or resigned user
drops out of `access_user_ids` immediately, even while the Lead still records
them as caretaker for audit. A cached array that keeps them is a grant that
outlives the account.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def _unique_sorted(values: Iterable[str]) -> list[str]:
    """Deduplicate, drop blanks, sort.

    Blanks are dropped because an empty team code or caretaker id stored in an
    access array would match a blank column and quietly widen the grant.
    Sorting makes the stored value a function of the inputs alone, so an
    unchanged authority never shows up as a write.
    """
    return sorted({value for value in values if value})


def derive_lead_access(
    team_code: str,
    caretaker_user_id: str | None,
    active_user_ids: frozenset[str],
) -> dict[str, list[str]]:
    """Access for a single Lead: its team, plus its caretaker when still active."""
    return {
        "access_team_ids": _unique_sorted([team_code]),
        "access_user_ids": (
            [caretaker_user_id]
            if caretaker_user_id and caretaker_user_id in active_user_ids
            else []
        ),
    }


def derive_customer_access(
    leads: Sequence[tuple[str, str | None]],
    active_user_ids: frozenset[str],
) -> dict[str, list[str]]:
    """Access for a Customer: the union over every current Lead.

    `leads` is a sequence of `(team_code, caretaker_user_id)` pairs covering
    EVERY current Lead for the customer, not only the one being written — a
    partial list would silently revoke access the other Leads confer.

    Because this is a union across teams, it must never be used to scope a
    single Lead: a telesale in team A would match a customer whose array
    includes team B. Lead-level checks read the Lead's own array.
    """
    teams: list[str] = []
    users: list[str] = []
    for team_code, caretaker_user_id in leads:
        access = derive_lead_access(team_code, caretaker_user_id, active_user_ids)
        teams.extend(access["access_team_ids"])
        users.extend(access["access_user_ids"])
    return {"access_team_ids": _unique_sorted(teams), "access_user_ids": _unique_sorted(users)}
