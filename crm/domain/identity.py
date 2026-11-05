"""Customer identity resolution (port of src/domain/identity.ts).

Deliberately conservative: only an exact normalised phone *together with* a
sufficiently similar name is auto-matched. Everything ambiguous becomes a human
review item, and nothing is ever auto-merged.
"""

from __future__ import annotations

import re
import unicodedata

from crm.domain.phone import digit_edit_distance, normalize_phone

_TITLES = [
    "นาย", "นาง", "นางสาว", "น.ส.", "ด.ช.", "ด.ญ.", "คุณ", "คุณหญิง", "ท่าน",
    "mr", "mrs", "ms", "miss", "dr", "khun",
]
# Longest first, so นางสาว is removed before นาง.
_TITLE_PATTERNS = [
    re.compile(r"^" + re.escape(title) + r"\.?\s*", re.IGNORECASE)
    for title in sorted(_TITLES, key=len, reverse=True)
]
_PUNCTUATION = re.compile(r"[()\[\]{}.,/\\|_\-–—\"'’“”+*#@!?<>:;]")
_SPACES = re.compile(r"\s+")

NAME_SIMILAR_THRESHOLD = 0.82
NAME_WEAK_THRESHOLD = 0.55
NAME_STRONG_THRESHOLD = 0.85


def normalize_name(raw) -> str:
    if not raw:
        return ""
    value = unicodedata.normalize("NFC", raw).lower().strip()
    # Titles are stripped before punctuation so dotted forms (น.ส., Mr.) match.
    for pattern in _TITLE_PATTERNS:
        value = pattern.sub("", value, count=1)
    value = _PUNCTUATION.sub(" ", value)
    return _SPACES.sub(" ", value).strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current.append(min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost))
        previous = current
    return previous[len(b)]


def name_similarity(a: str, b: str) -> float:
    """0..1 name similarity blending character distance and shared tokens."""
    left = normalize_name(a)
    right = normalize_name(b)
    if not left or not right:
        return 0
    if left == right:
        return 1
    max_len = max(len(left), len(right))
    char_score = 1 - _levenshtein(left, right) / max_len
    left_tokens = {t for t in left.split(" ") if t}
    right_tokens = {t for t in right.split(" ") if t}
    shared = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens) or 1
    token_score = shared / union
    return max(0, min(1, 0.6 * char_score + 0.4 * token_score))


def classify_identity(*, name: str, phone: str, candidates: list[dict]) -> dict:
    """Classify the incoming record against candidate customers.

    NEW_CUSTOMER only when there is no meaningful identity signal at all.
    Candidates are `{customerId, name, phones}`; the result uses the prototype's
    keys (kind, candidateCustomerId, nameScore, phoneExact, phoneNear, reasons).
    """
    incoming = normalize_phone(phone)
    best = {
        "kind": "NEW_CUSTOMER",
        "candidateCustomerId": None,
        "nameScore": 0,
        "phoneExact": False,
        "phoneNear": False,
        "reasons": [],
    }
    best_score = -1.0

    for candidate in candidates:
        phones = [p for p in (normalize_phone(x)["national"] for x in candidate["phones"]) if p]
        phone_exact = len(incoming["national"]) > 0 and incoming["national"] in phones
        distances = sorted(
            d for d in (digit_edit_distance(p, incoming["national"]) for p in phones) if d != float("inf")
        )
        nearest = distances[0] if distances else None
        phone_near = (not phone_exact) and nearest is not None and nearest <= 2 and len(phone.strip()) > 0
        name_score = name_similarity(candidate["name"], name)

        kind = "NEW_CUSTOMER"
        reasons: list[str] = []
        if phone_exact and name_score >= NAME_SIMILAR_THRESHOLD:
            kind = "AUTO_MATCH"
            reasons.append("exact normalized phone + similar name")
        elif phone_exact:
            kind = "HUMAN_REVIEW"
            reasons.append("exact normalized phone but name differs")
        elif name_score >= NAME_STRONG_THRESHOLD:
            kind = "HUMAN_REVIEW"
            reasons.append("strong name similarity without exact phone")
        elif phone_near and name_score >= NAME_SIMILAR_THRESHOLD:
            kind = "HUMAN_REVIEW"
            reasons.append("phone differs by 1-2 digits with similar name")

        # Prefer AUTO_MATCH, then the highest name score among review candidates.
        weight = (10 if kind == "AUTO_MATCH" else 1 if kind == "HUMAN_REVIEW" else 0) + name_score
        if weight > best_score and kind != "NEW_CUSTOMER":
            best_score = weight
            best = {
                "kind": kind,
                "candidateCustomerId": candidate["customerId"],
                "nameScore": name_score,
                "phoneExact": phone_exact,
                "phoneNear": phone_near,
                "reasons": reasons,
            }
    return best


def identity_blocks_commit(kind: str) -> bool:
    """Block commit when a row would create or re-point identity ambiguously."""
    return kind == "HUMAN_REVIEW"
