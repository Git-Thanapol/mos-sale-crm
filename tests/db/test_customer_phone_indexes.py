"""The import preview's phone lookup must not scan crm_customer.

Run via: docker compose exec web python -m pytest tests/db
"""

import pytest
from django.db import connection

from crm.customers.models import Customer

pytestmark = [pytest.mark.db, pytest.mark.django_db]


def test_phone_columns_have_valid_indexes():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = 'crm_customer'::regclass AND i.indisvalid
            """
        )
        names = {row[0] for row in cursor.fetchall()}
    assert {"ix_customer_phone1", "ix_customer_phone2"} <= names


def test_phone_lookup_uses_the_indexes_instead_of_scanning():
    Customer.objects.bulk_create(
        Customer(
            phone_key=f"09{n:08d}",
            phone1=f"08{n:08d}",
            phone2=f"06{n:08d}",
            customer_name=f"customer {n}",
        )
        for n in range(5000)
    )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE crm_customer")
        # The same query shape as preview._resolve_customer.
        cursor.execute(
            "EXPLAIN SELECT * FROM crm_customer "
            "WHERE phone1 = %s OR phone2 = %s OR phone_key = %s ORDER BY id LIMIT 5",
            ["0800000001", "0800000001", "0800000001"],
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert "ix_customer_phone1" in plan
    assert "ix_customer_phone2" in plan
    assert "Seq Scan on crm_customer" not in plan
