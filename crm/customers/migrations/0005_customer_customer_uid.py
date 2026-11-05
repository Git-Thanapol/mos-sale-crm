"""Add the immutable customer_uid in three steps.

A single AddField with `default=uuid4` cannot work here: Django resolves a
callable default ONCE and writes that one value into every pre-existing row,
so the subsequent UNIQUE constraint fails on any table with more than one row
(this one has ~216k). The column therefore arrives nullable, is backfilled
row-by-row, and only then becomes NOT NULL UNIQUE.

The backfill runs in bounded chunks rather than one UPDATE so it does not hold
a single long write lock across the whole table.
"""

from uuid import uuid4

from django.db import migrations, models

CHUNK_SIZE = 2000


def assign_uids(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    while True:
        pks = list(
            Customer.objects.filter(customer_uid__isnull=True).values_list("pk", flat=True)[:CHUNK_SIZE]
        )
        if not pks:
            break
        rows = list(Customer.objects.filter(pk__in=pks))
        for row in rows:
            row.customer_uid = uuid4()
        Customer.objects.bulk_update(rows, ["customer_uid"])


def drop_uids(apps, schema_editor):
    """Reverse leg: the column is dropped by the AddField reversal, so there is
    nothing to undo here. Present so the migration is reversible rather than
    irreversible by omission.
    """
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0004_remove_customer_ix_customer_recent_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="customer_uid",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(assign_uids, drop_uids),
        migrations.AlterField(
            model_name="customer",
            name="customer_uid",
            field=models.UUIDField(default=uuid4, editable=False, unique=True),
        ),
    ]
