"""Loading the HX63 provisional dataset as orders, streamed a chunk at a time.

Run via: docker compose exec web python -m pytest tests/db
"""

import hashlib
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from crm.customers.models import Customer
from crm.importv2.management.commands import load_hx63_dataset as loader
from crm.orders.models import Order, OrderLine
from tests.db.factories import make_customer, make_order

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def _record(order_id: str, phone: str = "0812345678") -> dict:
    return {
        "rowId": f"row-{order_id}",
        "source": {"snapshotKey": order_id},
        "identity": {"status": "MISSING_IDENTITY"},
        "customerCandidate": {"name": "ลูกค้า", "phone": phone},
        "lead": {"team": "upsell", "caretaker": "ผู้ดูแล"},
        "order": {
            "orderId": order_id,
            "date": 1789900800000,
            "amount": 298,
            "status": "สำเร็จ",
            "items": [{"raw": "SP404", "qty": 2, "amt": 298, "product": "ผ้าเช็ดแว่นตา"}],
        },
    }


def _dataset(directory, records: list[dict]):
    canonical = directory / "canonical.jsonl"
    canonical.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    quality = directory / "data-quality.json"
    quality.write_text("{}", encoding="utf-8")
    manifest = {
        "status": "COMPLETE",
        "schemaVersion": loader.SCHEMA,
        "files": {
            "canonical.jsonl": {"count": len(records), "sha256": hashlib.sha256(canonical.read_bytes()).hexdigest()}
        },
        "qualitySha256": hashlib.sha256(quality.read_bytes()).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def _batch_orders():
    return Order.objects.filter(uploaded_by=loader.UPLOADED_BY)


def test_dry_run_writes_nothing(tmp_path):
    call_command("load_hx63_dataset", dir=str(_dataset(tmp_path, [_record("A1")])), dry_run=True)
    assert not _batch_orders().exists()


def test_writes_in_chunks_with_one_customer_per_order(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "CHUNK", 2)
    sizes = []
    real_write = loader.Command._write_chunk

    def spy(self, chunk, batch_id):
        sizes.append(len(chunk))
        return real_write(self, chunk, batch_id)

    monkeypatch.setattr(loader.Command, "_write_chunk", spy)
    records = [_record(f"A{n}") for n in range(5)]

    call_command("load_hx63_dataset", dir=str(_dataset(tmp_path, records)))

    assert sizes == [2, 2, 1]  # the trailing partial chunk must not be dropped
    assert _batch_orders().count() == 5
    assert OrderLine.objects.filter(order__in=_batch_orders()).count() == 5
    assert Customer.objects.filter(phone1="0812345678").count() == 5  # same phone, never merged


def test_rerun_is_idempotent(tmp_path):
    directory = _dataset(tmp_path, [_record("A1"), _record("A2")])
    call_command("load_hx63_dataset", dir=str(directory))
    call_command("load_hx63_dataset", dir=str(directory))
    assert _batch_orders().count() == 2


def test_existing_order_is_skipped_not_overwritten(tmp_path):
    existing = make_order(make_customer(), order_no="A1", total_amount=1)
    call_command("load_hx63_dataset", dir=str(_dataset(tmp_path, [_record("A1"), _record("A2")])))
    existing.refresh_from_db()
    assert existing.total_amount == 1
    assert existing.uploaded_by != loader.UPLOADED_BY
    assert list(_batch_orders().values_list("order_no", flat=True)) == ["A2"]


def test_duplicate_order_id_aborts_before_writing(tmp_path):
    with pytest.raises(CommandError, match="validation failed"):
        call_command("load_hx63_dataset", dir=str(_dataset(tmp_path, [_record("A1"), _record("A1")])))
    assert not _batch_orders().exists()


def test_tampered_file_aborts_before_writing(tmp_path):
    directory = _dataset(tmp_path, [_record("A1")])
    with (directory / "canonical.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_record("A2")) + "\n")
    with pytest.raises(CommandError, match="SHA-256 mismatch"):
        call_command("load_hx63_dataset", dir=str(directory))
    assert not _batch_orders().exists()
