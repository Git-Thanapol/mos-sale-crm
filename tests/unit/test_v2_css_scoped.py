"""The ported prototype stylesheet must not restyle anything outside `.v2`.

The prototype's styles.css styles bare `button`, `input`, `table`, `body` and
its own app shell. Dropped into this site unscoped it would restyle every
page. These tests pin: every selector is under `.v2`, no shell rule leaks, and
the declarations that give the prototype its look survived the port.

Run via: docker compose exec -T web python -m pytest tests/unit/test_v2_css_scoped.py
"""

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "crm" / "css" / "v2-followup.css"


def _css() -> str:
    text = CSS_PATH.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _rules(text: str):
    """Yield (selector_list, declarations) for every style rule, descending
    into @media and skipping @keyframes bodies."""
    i = 0
    n = len(text)
    while i < n:
        j = text.find("{", i)
        if j == -1:
            return
        prelude = text[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        body = text[j + 1 : k - 1]
        if prelude.startswith("@keyframes"):
            pass
        elif prelude.startswith("@"):
            yield from _rules(body)
        else:
            yield [s.strip() for s in prelude.split(",")], body.strip()
        i = k


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def test_every_selector_is_scoped_under_v2():
    rules = list(_rules(_css()))
    assert rules, "stylesheet is empty"
    offenders = [
        sel
        for selectors, _ in rules
        for sel in selectors
        if not (sel == ".v2" or sel.startswith(".v2 ") or sel.startswith(".v2:"))
    ]
    assert offenders == []


def test_no_prototype_shell_rules_leak():
    css = _css()
    for shell in (".app-shell", "aside", ".brand", ".role-box", ".topbar", ".uat-banner"):
        assert shell not in css, f"prototype shell rule leaked: {shell}"


PRESERVED = [
    (".v2 .chip.danger", "background: #fee2e2; color: #a42323;"),
    (".v2 .chip.warn", "background: #fff2c9; color: #7d5d0d;"),
    (".v2 .chip.ok", "background: #d8f3e9; color: #176b5b;"),
    (".v2 .table-shell", "border-radius: 10px;"),
    (".v2 .toolbar", "padding: 13px;"),
    (".v2 button", "background: #176b5b;"),
    (".v2 button.secondary", "background: #e8efed; color: #28554d;"),
    (".v2 button.danger-button", "background: #b63b3b;"),
    (".v2 .page-title h1", "font-size: 25px;"),
    (".v2 .detail-layout", "grid-template-columns: repeat(2, minmax(0, 1fr));"),
    (".v2 .card-grid", "grid-template-columns: repeat(3, minmax(0, 1fr));"),
    (".v2 .stepper", "grid-template-columns: repeat(6, 1fr);"),
    (".v2 .decision-card", "border-radius: 10px;"),
    (".v2 .toast", "background: #d8f3e9; color: #176b5b;"),
]


@pytest.mark.parametrize(("selector", "declaration"), PRESERVED)
def test_prototype_declarations_preserved(selector, declaration):
    found = [
        _norm(body)
        for selectors, body in _rules(_css())
        if selector in selectors
    ]
    assert found, f"no rule for {selector}"
    assert any(_norm(declaration) in body for body in found), (selector, found)


def test_responsive_rules_do_not_touch_the_host_page():
    css = _css()
    # The prototype's media query also resized body and its app shell.
    assert "body" not in css
    assert "@media (max-width: 1180px)" in css
