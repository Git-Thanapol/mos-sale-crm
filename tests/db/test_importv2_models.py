"""Import persistence: constraints that the rules depend on.

Run via: docker compose exec -T web python -m pytest tests/db/test_importv2_models.py
"""

import pytest
from django.db import IntegrityError, transaction

from crm.domain.import_state import IMPORT_BATCH_STATUSES
from crm.importv2.models import (
    ImportBatch,
    ImportConflict,
    ImportJournalEntry,
    ImportOperation,
    ImportRowPlan,
)
from tests.db.factories import make_order

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def batch(**kwargs):
    defaults = dict(
        file_name="crm_customers.xlsx",
        source_kind="CRM_CUSTOMERS",
        mapping_profile_id="crm-customers",
        file_sha256="f" * 64,
    )
    defaults.update(kwargs)
    return ImportBatch.objects.create(**defaults)


def test_a_batch_starts_in_preview_like_the_prototype():
    created = batch()
    assert created.status == "PREVIEW" and created.attempt_count == 0
    assert created.transition_history == [] and created.batch_id is not None


def test_status_choices_are_exactly_the_state_machine_states():
    field = ImportBatch._meta.get_field("status")
    assert [value for value, _ in field.choices] == list(IMPORT_BATCH_STATUSES)


def test_operation_key_is_unique_per_batch_but_reusable_across_batches():
    first, second = batch(), batch()
    ImportOperation.objects.create(batch=first, operation_key="k1", action="commit")
    ImportOperation.objects.create(batch=second, operation_key="k1", action="commit")
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportOperation.objects.create(batch=first, operation_key="k1", action="commit")


def test_journal_sequence_is_unique_per_attempt():
    created = batch()
    kwargs = dict(batch=created, attempt=1, entity="ORDER", entity_id=1, op="CREATE")
    ImportJournalEntry.objects.create(seq=1, **kwargs)
    ImportJournalEntry.objects.create(seq=2, **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportJournalEntry.objects.create(seq=1, **kwargs)
    ImportJournalEntry.objects.create(seq=1, **{**kwargs, "attempt": 2})


def test_a_staged_row_index_is_unique_within_a_batch():
    created = batch()
    ImportRowPlan.objects.create(batch=created, row_index=2, order_id="SO-1", classification="NEW_ORDER")
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRowPlan.objects.create(batch=created, row_index=2, order_id="SO-2", classification="NEW_ORDER")


def test_a_conflict_id_is_unique_within_a_batch():
    created = batch()
    kwargs = dict(batch=created, row_index=3, kind="OWNERSHIP", severity="BLOCKING", summary="x", options=["KEEP_CURRENT"])
    ImportConflict.objects.create(conflict_id="3:OWNERSHIP:1", **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportConflict.objects.create(conflict_id="3:OWNERSHIP:1", **kwargs)


def test_deleting_a_batch_removes_its_children():
    created = batch()
    ImportRowPlan.objects.create(batch=created, row_index=2, order_id="SO-1", classification="NEW_ORDER")
    ImportOperation.objects.create(batch=created, operation_key="k", action="commit")
    created.delete()
    assert not ImportRowPlan.objects.exists() and not ImportOperation.objects.exists()


def test_order_gets_import_json_columns_with_empty_defaults():
    order = make_order()
    order.refresh_from_db()
    assert order.import_fields == {} and order.field_provenance == {}
