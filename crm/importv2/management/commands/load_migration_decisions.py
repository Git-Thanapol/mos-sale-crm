"""Load migration decisions produced by the run-once HX63 planner.

    python manage.py load_migration_decisions --from decisions.json [--dry-run]

The file is a JSON array of the prototype's MigrationDecision documents. Loading
is idempotent and never destroys a person's work:

 - a decision already RESOLVED is left exactly as it is;
 - an unresolved decision with the same evidence hash is left alone;
 - an unresolved decision whose evidence hash CHANGED is refused and reported
   ("source drift requires review"), never silently overwritten;
 - a row whose id or evidence hash does not match its own content is refused.

Exit status is non-zero when any row was refused.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from crm.domain.migration_decisions import (
    DECISION_CATEGORIES,
    DECISION_STATUSES,
    decision_id_for,
    evidence_hash_of,
)
from crm.importv2.models import MigrationDecision

REQUIRED = ("decisionId", "category", "status", "reasonCode", "subjectKey", "sourceSnapshotId", "evidence", "evidenceHash")


class Command(BaseCommand):
    help = "Load HX63 migration decisions (idempotent, never overwrites a resolution)."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="source", required=True, help="path to the decisions JSON file")
        parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")

    def handle(self, *args, source, dry_run=False, **options):
        path = Path(source)
        if not path.is_file():
            raise CommandError(f"{path} does not exist")
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CommandError(f"{path} is not valid JSON: {error}") from error
        if not isinstance(rows, list) or not rows:
            raise CommandError("the file must be a non-empty JSON array of decisions")

        counts = {"created": 0, "unchanged": 0, "kept_resolved": 0, "drifted": 0, "invalid": 0}
        for index, row in enumerate(rows, start=1):
            problem = self._problem(row)
            if problem:
                counts["invalid"] += 1
                self.stderr.write(f"row {index}: refused: {problem}")
                continue
            existing = MigrationDecision.objects.filter(decision_id=row["decisionId"]).first()
            if existing is None:
                counts["created"] += 1
                if not dry_run:
                    MigrationDecision.objects.create(
                        decision_id=row["decisionId"], category=row["category"],
                        status="OPEN" if row["status"] != "DEFERRED" else "DEFERRED",
                        reason_code=row["reasonCode"], subject_key=row["subjectKey"],
                        source_snapshot_id=row["sourceSnapshotId"], evidence=row["evidence"],
                        evidence_hash=row["evidenceHash"], revision=1, resolution=None,
                    )
            elif existing.status == "RESOLVED":
                counts["kept_resolved"] += 1
            elif existing.evidence_hash == row["evidenceHash"]:
                counts["unchanged"] += 1
            else:
                counts["drifted"] += 1
                self.stderr.write(
                    f"row {index}: {row['decisionId']} evidence changed since it was loaded; "
                    "source drift requires review, not overwritten"
                )
        prefix = "DRY RUN: " if dry_run else ""
        self.stdout.write(
            prefix + ", ".join(f"{name} {value}" for name, value in counts.items())
        )
        if counts["drifted"] or counts["invalid"]:
            raise CommandError(f"{counts['drifted']} drifted and {counts['invalid']} invalid row(s) were refused")

    @staticmethod
    def _problem(row) -> str | None:
        if not isinstance(row, dict) or any(k not in row for k in REQUIRED):
            return "missing a required field"
        if row["category"] not in DECISION_CATEGORIES or row["status"] not in DECISION_STATUSES:
            return "unknown category or status"
        if row["status"] == "RESOLVED":
            return "the loader creates unresolved decisions only; resolve them in the UI"
        if row["decisionId"] != decision_id_for(row["category"], row["sourceSnapshotId"], row["subjectKey"]):
            return "decisionId does not match its category, snapshot and subject"
        if row["evidenceHash"] != evidence_hash_of(row["category"], row["subjectKey"], row["evidence"]):
            return "evidenceHash does not match the evidence"
        return None
