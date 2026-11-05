"""Write paths for the Global Unassigned queue."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from crm.accounts.models import User
from crm.domain.access import derive_lead_access
from crm.domain.enums import SOURCE_KINDS
from crm.intake.models import IntakeItem
from crm.leads.models import Lead, LeadAuditEvent, OwnershipSegment
from crm.teams.models import Team


def upsert_intake(
    *, customer, source_order_id: str, source_kind: str, source_ref: str = ""
) -> tuple[IntakeItem, bool]:
    """Queue one unresolved source Order, exactly once (D024).

    Identity is (customer, source_order_id) and deliberately excludes the
    import batch, so re-importing the same unresolved order reuses the same
    intake. Returns (item, created).
    """
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"UNKNOWN_SOURCE_KIND:{source_kind}")

    item, created = IntakeItem.objects.get_or_create(
        customer=customer,
        source_order_id=(source_order_id or "").strip(),
        defaults={"source_kind": source_kind, "source_ref": source_ref},
    )
    if not created and source_ref and item.source_ref != source_ref:
        # Provenance is refreshed; identity is not touched.
        item.source_ref = source_ref
        item.save(update_fields=["source_ref", "updated_at"])
    return item, created


@transaction.atomic
def resolve_intake_to_team(*, actor, intake: IntakeItem, team: Team) -> Lead:
    """Convert a queued intake into a Lead for the chosen Team.

    Reuses the team's existing Lead for that customer when there is one, so two
    intakes for the same customer converge on one Lead rather than creating a
    second. Creates it otherwise.
    """
    # Lock the intake row and re-read its status INSIDE the transaction: two
    # editors resolving the same intake concurrently must not both pass the
    # check on a stale in-memory object and create two audit trails.
    locked = IntakeItem.objects.select_for_update().select_related("customer").get(pk=intake.pk)
    intake.refresh_from_db()
    if locked.status != "GLOBAL_UNASSIGNED":
        raise ValueError(f"INTAKE_ALREADY_RESOLVED:{locked.status}")
    if not team.active:
        raise ValueError(f"TEAM_INACTIVE:{team.code}")

    lead, created = Lead.objects.get_or_create(
        team=team,
        customer=intake.customer,
        defaults={
            "lifecycle_status": "NEW",
            "routing_state": "TEAM_UNASSIGNED",
            "ownership_policy": team.ownership_policy,
        },
    )
    if created:
        # No caretaker yet: the team owns it until someone is assigned. A new
        # Lead also gets its initial open OwnershipSegment (caretaker null) and
        # ownership_started_at, as backfill does: the missed-channel baseline
        # is "the open segment", and a lead with none falls back to created_at.
        now = timezone.now()
        access = derive_lead_access(team.code, None, frozenset())
        lead.access_team_ids = access["access_team_ids"]
        lead.access_user_ids = access["access_user_ids"]
        lead.ownership_started_at = now
        lead.save(
            update_fields=["access_team_ids", "access_user_ids", "ownership_started_at", "updated_at"]
        )
        OwnershipSegment.objects.create(
            lead=lead,
            caretaker=None,
            started_at=now,
            reason="INTAKE_CONVERTED",
            created_by=actor if isinstance(actor, User) else None,
        )

    intake.status = "CONVERTED"
    intake.assigned_team = team
    intake.created_lead = lead
    intake.resolved_by = actor if isinstance(actor, User) else None
    intake.resolved_at = timezone.now()
    intake.save(
        update_fields=[
            "status",
            "assigned_team",
            "created_lead",
            "resolved_by",
            "resolved_at",
            "updated_at",
        ]
    )

    LeadAuditEvent.objects.create(
        lead=lead,
        action="INTAKE_CONVERTED",
        actor=actor if isinstance(actor, User) else None,
        payload={
            "intake_id": intake.id,
            "team": team.code,
            "lead_created": created,
            "source_kind": intake.source_kind,
            "source_order_id": intake.source_order_id,
        },
    )
    return lead
