"""One running operation per import batch.

Commit and rollback run in many transactions, so a row lock cannot cover them.
A session-level Postgres advisory lock does: it is held for the whole operation,
released on completion, failure, or when the connection dies (a crashed worker
does not wedge the batch). A second operation on the same batch is refused
instead of racing the first.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db import connection

_KEY_BASE = 7_000_000_000


@contextmanager
def batch_running(batch):
    key = _KEY_BASE + batch.pk
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise ValueError("ILLEGAL_STATE: another operation is already running on this batch")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
