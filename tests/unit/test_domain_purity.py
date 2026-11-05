"""crm/domain/ holds ported business rules and must stay framework-free.

A Django import there means persistence leaked into business logic — the
exact mistake 03_INFRASTRUCTURE_REPLACEMENT_GUIDE.md warns against ("replace
adapters, not business semantics"). Enforced here rather than by convention,
because the prototype's own src/domain, src/import, src/read-model and
src/security layers have zero firebase imports and that purity is what made
this port cheap.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "crm" / "domain"


def test_domain_package_never_imports_django():
    offenders = []
    for path in DOMAIN_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "django" for n in names):
                offenders.append(str(path))
    assert offenders == []
