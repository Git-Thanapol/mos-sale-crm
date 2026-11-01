"""Ported CRM V2 business rules — framework-free.

One module per prototype module, mirroring its src/domain, src/read-model and
src/security layers. Nothing here may import Django: these are pure functions
over plain values, so they are testable without a database and provable
against the prototype's own case tables (see tests/unit/test_parity_fixtures.py).

Reference implementation: CRM_V2 checkpoint
f97f955874f6bea0b43cd7723039b6ca50fe398c.
"""
