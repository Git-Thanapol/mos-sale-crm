# CRM V2 Follow-up / Lead Surface — Implementation Plan (Plan 1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the CRM V2 prototype's lead-management semantics — Lead = Customer + Team, ownership policies, missed-channel, last-order-age review, STOPPED lifecycle, follow-up cycles, contact attempts, appointments, Global Unassigned intake — into the existing Django `/followup` page with server-side permission enforcement, preserving the prototype's business behavior exactly.

**Architecture:** Ported pure business rules live in a new `crm/domain/` package with **no Django imports**, mirroring the prototype's `src/domain` + `src/read-model` + `src/security` layers one-to-one. They are proven identical to the prototype by a parity-fixture harness: the prototype's own test vectors are exported once to JSON and replayed against the Python ports. Django apps (`crm/leads`, `crm/followcycles`, `crm/intake`) hold persistence, services and views on top of those pure functions. The prototype's Firebase adapters (`src/adapters`, 360 LOC), transport (`src/server`) and React UI (`src/ui`) are discarded and replaced by the Django ORM, views and templates. The prototype's migration/backup/cutover tooling stays TypeScript and is out of scope for this plan.

**Tech Stack:** Python 3.12, Django >=5.1,<5.2, PostgreSQL 16, psycopg 3, pytest + pytest-django, django-htmx, Docker Compose. Parity fixtures exported with Node 22 + tsx from the prototype repo (one-time, committed as JSON).

**Spec:** `C:\Users\Thana\OneDrive\เดสก์ท็อป\customer-crm-v2-codex\customer-crm-v2-codex\CRM_V2_Developer_Handoff_Server_Data\` — in particular `02_LOCKED_BUSINESS_RULES.md`, `06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md`, `08_SECURITY_AND_AUTH_REQUIREMENTS.md`, `11_DO_NOT_CHANGE_CHECKLIST.md`. Reference implementation: the same repo's `src/` tree.

## Global Constraints

Every task's requirements implicitly include this section.

- **Reference implementation path** (call it `$PROTO` below): `C:\Users\Thana\OneDrive\เดสก์ท็อป\customer-crm-v2-codex\customer-crm-v2-codex`. Reviewed code checkpoint `f97f955874f6bea0b43cd7723039b6ca50fe398c`.
- **`crm/domain/` imports nothing from Django.** Enforced by a test, not convention. Those modules are ports; a Django import there means persistence leaked into business logic.
- **Business timezone is `Asia/Bangkok`** for every date bucket, appointment state and business-date computation. `settings.TIME_ZONE` is already `Asia/Bangkok` and `USE_TZ = True`; domain functions take an explicit `now` argument and never read the clock themselves.
- **D023 HX63 snapshot baseline:** `2026-10-13T12:31:55Z`. Missed-channel counting for HX63-migrated leads starts here; earlier orders are never retroactively counted.
- **Lead identity:** `lead_key = f"{team.code}:{customer.customer_uid}"`, deterministic and immutable once created.
- **Enums are stored as machine codes**, verbatim from `$PROTO/src/domain/enums.ts`. Thai labels live only in templates and in `crm/core/thai.py`-style label maps. Renaming a label must never rewrite stored data.
- **No team name may appear in business logic.** Ownership behavior is read from `Team.policy`. A new policy means a new branch in `crm/domain/ownership.py`, never a `if team.code == "CRM_TEAM"`.
- **Role mapping** (decided; see `docs/DECISIONS.md` addendum written in Task 14): prototype `ADMIN` → this repo's `EDITOR`; prototype `TELESALE` → `พนักงาน`; prototype `TEAM_LEAD` → new team-scoped predicates. `crm/core/permissions.py` remains the single source of truth. This repo's `ADMIN` keeps its documented weaker scope and is **not** the spec's Admin.
- **`Customer.phone_key` stays `UNIQUE` in this plan.** `customer_uid` is added as the immutable identity that Leads and ownership reference. Dropping phone uniqueness lands in Plan 2 (import engine), which is the code that needs it.
- **New lead queues use keyset pagination.** `crm/core/pagination.py`'s offset pager is left untouched for the existing follow-up list; acceptance criterion F ("offset pagination count = 0") applies to the new operational queues only, and the boundary is documented in Task 13.
- **Authorization is server-side.** Hidden UI is not a control. Every new mutation re-checks permission in its service function, not only at the route.
- **Never trust client-supplied** `actor_id`, `role`, `team_id`, `caretaker_id`, or any `access_*` array. All are derived from `request.user` server-side.
- **Commands.** Unit tests (no DB): `python -m pytest -m unit tests/unit/...`. DB and view tests: `docker compose exec web python -m pytest tests/db/...` / `tests/view/...` — they need the compose network.

## Review Focus

Five input classes the spec implies but that no task's happy path exercises. Each gets a test in the task that owns the code.

1. **Bangkok day-boundary off-by-one.** An order at `2026-10-13T23:30+07:00` and a "now" minutes later must land in bucket `0_7`, not `8_15`. Epoch-day arithmetic on a UTC timestamp silently shifts by one day for anything in the 17:00–24:00 UTC window. Owned by Task 11.
2. **Caretaker deactivated mid-cycle.** A disabled or resigned user must lose operational access and caretaker authority immediately (`08_SECURITY`), so the lead must route to `TEAM_UNASSIGNED` and `access_user_ids` must drop them — even though `Lead.caretaker_user_id` still names them for audit. A cached access array that keeps them is a real privilege leak. Owned by Task 5.
3. **Tied order timestamps.** Two valid orders with the identical `ordered_at` must produce one deterministic missed-channel count and one stable ownership outcome, regardless of import order. The prototype breaks the tie on `order_id`; a port that sorts on timestamp alone flip-flops between runs. Owned by Task 10.
4. **One customer, leads in two teams.** A `พนักงาน` in team A must not see the team-B lead for the same customer. The customer-level access array is a *union* over leads, so scoping that reads the customer array instead of the lead array leaks across teams. Owned by Task 14.
5. **Lead with no orders at all.** `last_order_at IS NULL` must yield the `NO_ORDER` bucket, must never be a reallocation-review candidate, and its missed-channel baseline is lead creation time rather than a null comparison that counts everything. Owned by Task 11.

---

## File Structure

**New pure-domain package** — ports, one file per prototype module, no Django:

| File | Responsibility | Ported from |
|---|---|---|
| `crm/domain/__init__.py` | empty marker | — |
| `crm/domain/enums.py` | every machine-code vocabulary | `$PROTO/src/domain/enums.ts` |
| `crm/domain/bangkok.py` | Bangkok epoch-day / day-start arithmetic | `$PROTO/src/read-model/last-order-age.ts` (helpers) |
| `crm/domain/ownership.py` | `resolve_ownership`, `derive_routing`, `evaluate_stopped_reactivation` | `$PROTO/src/domain/ownership.ts` |
| `crm/domain/missed_channel.py` | `recompute_missed_channel`, `missed_channel_group` | `$PROTO/src/domain/missed-channel.ts` |
| `crm/domain/last_order_age.py` | presets, cutoffs, matching, reallocation candidacy | `$PROTO/src/read-model/last-order-age.ts` |
| `crm/domain/access.py` | `derive_lead_access`, `derive_customer_access` | `$PROTO/src/security/access-arrays.ts` |

**New Django apps** — persistence, services, views:

| File | Responsibility |
|---|---|
| `crm/leads/models.py` | `Lead`, `OwnershipSegment` |
| `crm/leads/services.py` | the only write paths for ownership, lifecycle, STOPPED |
| `crm/leads/selectors.py` | keyset lead queues, last-order-age filter, review queue |
| `crm/followcycles/models.py` | `FollowupCycle`, `Note`, `ContactAttempt`, `Appointment` |
| `crm/followcycles/services.py` | cycle open/close, attempt logging, appointment writes |
| `crm/followcycles/selectors.py` | cycle history, appointment state |
| `crm/intake/models.py` | `IntakeItem` (Global Unassigned, pre-Lead) |
| `crm/intake/services.py` | deterministic intake upsert (D024), convert-to-team |

**Modified:**

| File | Change |
|---|---|
| `crm/teams/models.py` | add configurable `Team` with `policy` |
| `crm/customers/models.py` | add immutable `customer_uid` |
| `crm/core/permissions.py` | add `TEAM_LEAD` scope predicates |
| `crm/core/pagination.py` | add keyset cursor helpers alongside the offset pager |
| `crm/followups/views.py`, `templates/followups/*` | render the new sections |
| `config/settings/base.py` | register the three new apps |

**Parity fixtures** (committed, generated once): `tests/fixtures/crmv2/*.json`.

---

### Task 1: Parity fixture harness

The keystone: without this, every later "ported correctly" claim is an assertion rather than evidence.

**Files:**
- Create: `scripts/export_crmv2_fixtures.ts`
- Create: `tests/fixtures/crmv2/README.md`
- Create: `tests/unit/test_parity_fixtures.py`
- Create: `crm/domain/__init__.py`
- Create: `tests/unit/test_domain_purity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `tests/fixtures/crmv2/<module>.json`, each a list of `{"name": str, "input": {...}, "expected": {...}}` cases. `load_cases(module: str) -> list[dict]` in `tests/unit/test_parity_fixtures.py` is imported by every later parity test.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parity_fixtures.py
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "crmv2"


def load_cases(module: str) -> list[dict]:
    cases = json.loads((FIXTURE_DIR / f"{module}.json").read_text(encoding="utf-8"))
    assert cases, f"{module}.json is empty"
    for case in cases:
        assert set(case) >= {"name", "input", "expected"}
    return cases


def test_every_fixture_file_is_loadable():
    files = sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
    assert files == ["last_order_age", "missed_channel", "ownership"]
    for module in files:
        load_cases(module)
```

```python
# tests/unit/test_domain_purity.py
import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit


def test_domain_package_never_imports_django():
    offenders = []
    for path in pathlib.Path("crm/domain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "django" for n in names):
                offenders.append(str(path))
    assert offenders == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_parity_fixtures.py tests/unit/test_domain_purity.py -v`
Expected: `test_every_fixture_file_is_loadable` FAILS (fixture directory missing); `test_domain_package_never_imports_django` PASSES vacuously once `crm/domain/__init__.py` exists.

- [ ] **Step 3: Write `scripts/export_crmv2_fixtures.ts`**

A tsx script run **inside the prototype repo**, not this one. It imports `resolveOwnership`, `recomputeMissedChannel`, and the `last-order-age` functions, enumerates the case tables already present in `$PROTO/tests/unit/`, and writes one JSON file per module to a path given as `argv[2]`. Timestamps serialize as integer milliseconds, `ReadonlySet` as a sorted array, `null` stays `null`.

Produce at minimum: every ownership policy × (no caretaker / same closer / other closer / null closer / lock active / STOPPED), the missed-channel reason matrix including `BEFORE_BASELINE` and both exclusions, and every `LAST_ORDER_AGE_PRESETS` boundary at both ends.

- [ ] **Step 4: Generate and commit the fixtures**

```bash
cd "$PROTO" && npm ci && npx tsx scripts/export_crmv2_fixtures.ts \
  "C:/Laptop files/MOS/mos-sale-crm/tests/fixtures/crmv2"
```

Write `tests/fixtures/crmv2/README.md` recording the prototype commit `f97f955874f6bea0b43cd7723039b6ca50fe398c`, the exact command, and the rule that these files are regenerated, never hand-edited.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_parity_fixtures.py tests/unit/test_domain_purity.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/export_crmv2_fixtures.ts tests/fixtures/crmv2 tests/unit/test_parity_fixtures.py tests/unit/test_domain_purity.py crm/domain/__init__.py
git commit -m "test: add CRM V2 parity fixture harness and domain purity guard"
```

---

### Task 2: Enum and Bangkok-date ports

**Files:**
- Create: `crm/domain/enums.py`
- Create: `crm/domain/bangkok.py`
- Create: `tests/unit/test_domain_enums.py`
- Create: `tests/unit/test_bangkok.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `crm/domain/enums.py` — module-level tuples `ROLES`, `OWNERSHIP_POLICIES`, `LEAD_LIFECYCLE_STATUSES`, `COMPUTED_LEAD_STATUSES`, `STOP_REASONS`, `ROUTING_STATES`, `INTAKE_STATUSES`, `ORDER_STATUSES`, `PAYMENT_STATUSES`, `SHIPPING_STATUSES`, `CONTACT_ATTEMPT_RESULTS`, `APPOINTMENT_STATUSES`, `CYCLE_END_REASONS`, `IDENTIFIER_TYPES`, `IDENTITY_MATCH_KINDS`, `AUDIT_ACTIONS`, each a `tuple[str, ...]` in the prototype's order; plus `is_order_terminal_negative(status: str) -> bool` and `is_order_valid_for_sales(status: str) -> bool`.
  - `crm/domain/bangkok.py` — `BANGKOK_TZ: ZoneInfo`, `epoch_day(moment: datetime) -> int`, `day_start(epoch_day_value: int) -> datetime`, `age_in_days(last_order_at: datetime | None, now: datetime) -> int | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_domain_enums.py — assert the vocabularies verbatim
def test_ownership_policies_are_exactly_the_three_locked_policies():
    assert ENUMS.OWNERSHIP_POLICIES == ("EXCLUSIVE", "LATEST_ORDER", "LATEST_ORDER_WITH_LOCK")


def test_stopped_is_a_lifecycle_status_not_a_routing_state():
    assert "STOPPED" in ENUMS.LEAD_LIFECYCLE_STATUSES
    assert "STOPPED" not in ENUMS.ROUTING_STATES
    assert ENUMS.ROUTING_STATES == ("ASSIGNED", "TEAM_UNASSIGNED")


def test_cancelled_and_cod_returned_are_the_only_invalid_sales_statuses():
    invalid = [s for s in ENUMS.ORDER_STATUSES if not ENUMS.is_order_valid_for_sales(s)]
    assert invalid == ["CANCELLED", "COD_RETURNED"]
```

```python
# tests/unit/test_bangkok.py — Review Focus #1
def test_late_evening_bangkok_order_is_zero_days_old():
    ordered = datetime(2026, 10, 13, 23, 30, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 10, 13, 23, 55, tzinfo=BANGKOK_TZ)
    assert age_in_days(ordered, now) == 0


def test_utc_evening_window_does_not_shift_the_bangkok_day():
    # 2026-10-13T18:00Z is already 2026-10-14 01:00 in Bangkok
    ordered = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)
    assert age_in_days(ordered, now) == 0


def test_age_is_none_when_there_is_no_order():
    assert age_in_days(None, datetime(2026, 10, 13, tzinfo=BANGKOK_TZ)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_domain_enums.py tests/unit/test_bangkok.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.domain.enums'`

- [ ] **Step 3: Implement `crm/domain/enums.py` and `crm/domain/bangkok.py`**

Transcribe the vocabularies from `$PROTO/src/domain/enums.ts` in source order — do not sort, reorder or "tidy" them; order is part of the contract for UI option lists. `age_in_days` converts both arguments to `BANGKOK_TZ` and subtracts calendar day ordinals; it must not divide a timedelta by 86400.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_domain_enums.py tests/unit/test_bangkok.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/domain/enums.py crm/domain/bangkok.py tests/unit/test_domain_enums.py tests/unit/test_bangkok.py
git commit -m "feat: port CRM V2 enums and Bangkok date arithmetic"
```

---

### Task 3: Configurable Team with ownership policy

**Files:**
- Modify: `crm/teams/models.py`
- Create: `crm/teams/migrations/0002_team.py`
- Create: `crm/core/management/commands/seed_teams.py`
- Create: `tests/db/test_team_policy.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py` (`OWNERSHIP_POLICIES`).
- Produces: `crm.teams.models.Team` with fields `code: CharField(max_length=32, unique=True)`, `name`, `display_name`, `active: BooleanField(default=True)`, `policy: JSONField`, `created_at`, `updated_at`; and `Team.ownership_policy -> str`, `Team.missed_channel_policy -> dict`, `Team.team_lead_configured -> bool` reading `policy` with the prototype's defaults. `TeamAssignment.team_code` keeps its existing value space and is **not** converted to an FK in this task.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_team_policy.py
pytestmark = [pytest.mark.db, pytest.mark.django_db]


def test_policy_defaults_match_the_prototype_team_policy_schema():
    team = Team.objects.create(code="CRM_TEAM", name="CRM", display_name="CRM Team",
                               policy={"ownershipPolicy": "EXCLUSIVE"})
    assert team.ownership_policy == "EXCLUSIVE"
    assert team.missed_channel_policy == {
        "enabled": True, "countNullCloser": True, "resetOnCaretakerClose": True
    }
    assert team.team_lead_configured is True


def test_unknown_ownership_policy_is_rejected_at_the_model_layer():
    team = Team(code="X", name="X", display_name="X", policy={"ownershipPolicy": "WHATEVER"})
    with pytest.raises(ValidationError):
        team.full_clean()


def test_seed_teams_is_idempotent():
    call_command("seed_teams")
    call_command("seed_teams")
    assert Team.objects.filter(code__in=["CRM_TEAM", "UPSELL_TEAM"]).count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_team_policy.py -v`
Expected: FAIL with `ImportError: cannot import name 'Team'`

- [ ] **Step 3: Implement `Team` and `seed_teams`**

`policy` is validated in `Team.clean()` against `OWNERSHIP_POLICIES`; the three property accessors apply the prototype's defaults for any key the stored policy omits, so an older row never changes behavior when a new policy key is introduced. `seed_teams` upserts by `code` from `TeamAssignment.TEAM_CHOICES` with `ownershipPolicy: "EXCLUSIVE"`, and must not overwrite a `policy` an operator has already edited.

- [ ] **Step 4: Generate the migration and run the tests**

Run: `docker compose exec web python manage.py makemigrations teams && docker compose exec web python -m pytest tests/db/test_team_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/teams/models.py crm/teams/migrations/0002_team.py crm/core/management/commands/seed_teams.py tests/db/test_team_policy.py
git commit -m "feat: add configurable Team with per-team ownership policy"
```

---

### Task 4: Immutable `customer_uid`

**Files:**
- Modify: `crm/customers/models.py`
- Create: `crm/customers/migrations/00NN_customer_uid.py`
- Create: `tests/db/test_customer_uid.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Customer.customer_uid: UUIDField(unique=True, editable=False, default=uuid4)`. `phone_key` keeps its `UNIQUE` constraint. Every later task references customers by `customer_uid`, never by `phone_key` or `pk`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_customer_uid.py
def test_customer_uid_is_assigned_and_unique():
    a, b = make_customer(_uid="0000001"), make_customer(_uid="0000002")
    assert a.customer_uid and a.customer_uid != b.customer_uid


def test_customer_uid_survives_a_phone_change():
    customer = make_customer(_uid="0000001")
    original = customer.customer_uid
    customer.phone1 = "0999999999"
    customer.phone_key = "0999999999"
    customer.save()
    customer.refresh_from_db()
    assert customer.customer_uid == original


def test_backfill_assigns_a_uid_to_every_existing_row():
    assert not Customer.objects.filter(customer_uid__isnull=True).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_customer_uid.py -v`
Expected: FAIL with `FieldError: Cannot resolve keyword 'customer_uid'`

- [ ] **Step 3: Add the field and a three-step migration**

The migration adds the column nullable, backfills with one `uuid4()` per existing row in a `RunPython` forward operation, then alters it to `null=False, unique=True`. A single `AddField` with a callable default assigns the *same* UUID to every pre-existing row on some backends and then fails the unique constraint — do not collapse the steps.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec web python -m pytest tests/db/test_customer_uid.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/customers/models.py crm/customers/migrations tests/db/test_customer_uid.py
git commit -m "feat: add immutable customer_uid as the identity Leads reference"
```

---

### Task 5: Access arrays and the Lead model

Owns **Review Focus #2** (deactivated caretaker).

**Files:**
- Create: `crm/domain/access.py`
- Create: `crm/leads/__init__.py`, `crm/leads/apps.py`, `crm/leads/models.py`
- Create: `crm/leads/migrations/0001_initial.py`
- Modify: `config/settings/base.py` (register `crm.leads`)
- Create: `tests/unit/test_access_arrays.py`
- Create: `tests/db/test_lead_model.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py`, `crm.teams.models.Team`, `Customer.customer_uid`.
- Produces:
  - `crm/domain/access.py` — `derive_lead_access(team_code: str, caretaker_user_id: str | None, active_user_ids: frozenset[str]) -> dict[str, list[str]]` returning `{"access_team_ids": [...], "access_user_ids": [...]}`, sorted and de-duplicated; `derive_customer_access(leads: Sequence[tuple[str, str | None]], active_user_ids: frozenset[str]) -> dict[str, list[str]]`.
  - `crm.leads.models.Lead` with `lead_key: CharField(unique=True)`, FKs `customer`, `team`, `caretaker: FK(accounts.User, null=True)`, `lifecycle_status`, `routing_state`, `ownership_started_at`, `ownership_policy`, `current_cycle_id`, `last_order_at`, `last_order_id`, `total_orders`, `total_spend`, `latest_note`, `latest_note_updated_at`, `next_follow_at`, `missed_channel_count`, `no_answer_streak`, `stopped_reason`, `stopped_at`, `access_team_ids: ArrayField`, `access_user_ids: ArrayField`, `created_at`, `updated_at`; `STAFF_CODE_PATH = "customer__staff_code"`; `objects = StaffScopedQuerySet.as_manager()`; and `Lead.build_key(team_code: str, customer_uid) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_access_arrays.py — Review Focus #2
def test_inactive_caretaker_is_excluded_from_access_users():
    access = derive_lead_access("CRM_TEAM", "u1", frozenset())
    assert access == {"access_team_ids": ["CRM_TEAM"], "access_user_ids": []}


def test_active_caretaker_is_included():
    assert derive_lead_access("CRM_TEAM", "u1", frozenset({"u1"}))["access_user_ids"] == ["u1"]


def test_customer_access_unions_every_team_and_sorts():
    access = derive_customer_access(
        [("UPSELL_TEAM", "u2"), ("CRM_TEAM", "u1")], frozenset({"u1", "u2"})
    )
    assert access == {
        "access_team_ids": ["CRM_TEAM", "UPSELL_TEAM"],
        "access_user_ids": ["u1", "u2"],
    }
```

```python
# tests/db/test_lead_model.py
def test_lead_key_is_team_code_colon_customer_uid():
    lead = make_lead(team_code="CRM_TEAM")
    assert lead.lead_key == f"CRM_TEAM:{lead.customer.customer_uid}"


def test_one_customer_can_hold_a_lead_in_two_teams():
    customer = make_customer(_uid="0000001")
    make_lead(customer=customer, team_code="CRM_TEAM")
    make_lead(customer=customer, team_code="UPSELL_TEAM")
    assert Lead.objects.filter(customer=customer).count() == 2


def test_duplicate_lead_for_the_same_team_and_customer_is_rejected():
    customer = make_customer(_uid="0000001")
    make_lead(customer=customer, team_code="CRM_TEAM")
    with pytest.raises(IntegrityError):
        make_lead(customer=customer, team_code="CRM_TEAM")


def test_stopped_lead_requires_a_stop_reason():
    lead = make_lead(lifecycle_status="STOPPED", stopped_reason="")
    with pytest.raises(ValidationError):
        lead.full_clean()


def test_lead_with_a_caretaker_always_has_a_team():
    # teamId is non-null by FK; the guard is that caretaker without team is impossible
    assert Lead._meta.get_field("team").null is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_access_arrays.py -v` then `docker compose exec web python -m pytest tests/db/test_lead_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.domain.access'`

- [ ] **Step 3: Implement `crm/domain/access.py`, then `crm.leads.models.Lead`**

`Lead.save()` computes `lead_key` from `build_key` when absent and must never recompute it for an existing row. A `UniqueConstraint(fields=["team", "customer"])` backs the duplicate rejection. The `STOPPED`-requires-reason rule and `ownership_policy` validity live in `Lead.clean()`, mirroring the prototype's two `.refine()` guards. Index for the queues: `(team, routing_state, last_order_at, id)` and a partial index on `~Q(lifecycle_status="STOPPED")`.

- [ ] **Step 4: Generate the migration and run the tests**

Run: `docker compose exec web python manage.py makemigrations leads && docker compose exec web python -m pytest tests/unit/test_access_arrays.py tests/db/test_lead_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/domain/access.py crm/leads config/settings/base.py tests/unit/test_access_arrays.py tests/db/test_lead_model.py
git commit -m "feat: add Lead (Customer + Team) with derived access arrays"
```

---

### Task 6: Ownership resolution and routing (ported)

**Files:**
- Create: `crm/domain/ownership.py`
- Create: `tests/unit/test_ownership_parity.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py`, `load_cases` from Task 1.
- Produces:
  - `resolve_ownership(policy: str, team_code: str, current_caretaker_user_id: str | None, ownership_started_at: datetime | None, lifecycle_status: str, lock_active: bool, order: OwnershipOrderFacts, active_user_ids: frozenset[str]) -> OwnershipDecision`
  - `@dataclass(frozen=True) OwnershipOrderFacts(order_id: str, ordered_at: datetime, closer_user_id: str | None, closer_team_code: str | None)`
  - `@dataclass(frozen=True) OwnershipDecision(caretaker_user_id: str | None, changed: bool, new_segment_started_at: datetime | None, reason: str, conflict: OwnershipConflict | None)` where `reason ∈ {UNCHANGED, INITIAL_ASSIGNMENT, LATEST_ORDER, LOCKED, NO_ACTIVE_CLOSER, EXCLUSIVE_HELD}`
  - `@dataclass(frozen=True) OwnershipConflict(kind: str, blocking: bool, summary: str, options: tuple[str, ...])`
  - `derive_routing(team_code: str | None, caretaker_user_id: str | None, caretaker_active: bool, team_lead_configured: bool) -> RoutingDecision` with `RoutingDecision(routing_state: str, queue: str, escalated_to_admin: bool)`
  - `evaluate_stopped_reactivation(lifecycle_status: str, prior_caretaker_user_id: str | None, previous_stopped_at: datetime | None, order: OwnershipOrderFacts, active_user_ids: frozenset[str]) -> StoppedReactivationDecision`

- [ ] **Step 1: Write the failing parity test**

```python
# tests/unit/test_ownership_parity.py
@pytest.mark.parametrize("case", load_cases("ownership"), ids=lambda c: c["name"])
def test_ownership_matches_prototype(case):
    decision = resolve_ownership(**build_input(case["input"]))
    assert decision.caretaker_user_id == case["expected"]["caretakerUserId"]
    assert decision.changed is case["expected"]["changed"]
    assert decision.reason == case["expected"]["reason"]
    assert (decision.conflict is None) == (case["expected"]["conflict"] is None)


def test_exclusive_lead_is_never_stolen_by_another_closer():
    decision = resolve_ownership(policy="EXCLUSIVE", team_code="CRM_TEAM",
                                 current_caretaker_user_id="u1", ownership_started_at=T0,
                                 lifecycle_status="FOLLOWING", lock_active=False,
                                 order=facts(closer_user_id="u2"),
                                 active_user_ids=frozenset({"u1", "u2"}))
    assert decision.caretaker_user_id == "u1"
    assert decision.changed is False
    assert decision.reason == "EXCLUSIVE_HELD"
    assert decision.conflict.blocking is False
    assert decision.conflict.options == ("KEEP_CURRENT", "REASSIGN")


def test_marketplace_order_with_no_closer_never_clears_a_caretaker():
    decision = resolve_ownership(policy="LATEST_ORDER", team_code="CRM_TEAM",
                                 current_caretaker_user_id="u1", ownership_started_at=T0,
                                 lifecycle_status="FOLLOWING", lock_active=False,
                                 order=facts(closer_user_id=None),
                                 active_user_ids=frozenset({"u1"}))
    assert decision.caretaker_user_id == "u1"
    assert decision.reason == "NO_ACTIVE_CLOSER"


def test_active_lock_holds_ownership_under_latest_order_with_lock():
    decision = resolve_ownership(policy="LATEST_ORDER_WITH_LOCK", team_code="CRM_TEAM",
                                 current_caretaker_user_id="u1", ownership_started_at=T0,
                                 lifecycle_status="FOLLOWING", lock_active=True,
                                 order=facts(closer_user_id="u2"),
                                 active_user_ids=frozenset({"u1", "u2"}))
    assert decision.reason == "LOCKED"


def test_unknown_policy_raises_rather_than_falling_back():
    with pytest.raises(ValueError, match="UNSUPPORTED_OWNERSHIP_POLICY"):
        resolve_ownership(policy="SOMETHING_NEW", ...)


def test_stopped_lead_receiving_an_order_always_requires_review():
    decision = evaluate_stopped_reactivation(
        lifecycle_status="STOPPED", prior_caretaker_user_id="u1", previous_stopped_at=T0,
        order=facts(closer_user_id="u1"), active_user_ids=frozenset({"u1"}))
    assert decision.requires_review is True
    assert decision.proposed_action == "REACTIVATE"
    assert decision.same_caretaker_as_before is True


def test_routing_without_a_team_is_a_programming_error():
    with pytest.raises(ValueError, match="TEAM_REQUIRED_FOR_LEAD_ROUTING"):
        derive_routing(team_code=None, caretaker_user_id=None,
                       caretaker_active=False, team_lead_configured=True)


def test_team_without_a_lead_escalates_to_admin():
    decision = derive_routing("CRM_TEAM", None, False, team_lead_configured=False)
    assert decision.routing_state == "TEAM_UNASSIGNED"
    assert decision.queue == "ADMIN"
    assert decision.escalated_to_admin is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -m unit tests/unit/test_ownership_parity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.domain.ownership'`

- [ ] **Step 3: Implement `crm/domain/ownership.py` as a line-by-line port of `$PROTO/src/domain/ownership.ts`**

Keep the prototype's branch order; it is load-bearing for which `reason` a case reports. Keep the `EXCLUSIVE_HELD` conflict non-blocking — the order is still recorded, only ownership is held. Keep `derive_routing`'s raise on a missing team: routing a team-less lead is a bug, and `None` must never be silently treated as unassigned (that is what Global Unassigned intake exists for).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -m unit tests/unit/test_ownership_parity.py -v`
Expected: PASS, every parametrized fixture case included

- [ ] **Step 5: Commit**

```bash
git add crm/domain/ownership.py tests/unit/test_ownership_parity.py
git commit -m "feat: port ownership policies, routing and STOPPED reactivation"
```

---

### Task 7: Ownership segments, audit and the apply service

**Files:**
- Modify: `crm/leads/models.py` (add `OwnershipSegment`, `LeadAuditEvent`)
- Create: `crm/leads/services.py`
- Create: `crm/leads/migrations/0002_ownership_segment.py`
- Create: `tests/db/test_ownership_service.py`

**Interfaces:**
- Consumes: `crm/domain/ownership.py`, `crm/domain/access.py`, `Lead`, `Team`.
- Produces:
  - `crm.leads.models.OwnershipSegment` — `lead: FK`, `caretaker: FK(accounts.User, null=True)`, `started_at`, `ended_at: null=True`, `reason`, `created_by`, with `UniqueConstraint(fields=["lead"], condition=Q(ended_at__isnull=True), name="ux_ownership_segment_open")`.
  - `crm.leads.models.LeadAuditEvent` — `lead: FK`, `action` (from `AUDIT_ACTIONS`), `actor: FK(accounts.User, null=True)`, `reason`, `payload: JSONField`, `created_at`.
  - `apply_order_to_lead(*, actor, lead: Lead, order, lock_active: bool) -> OwnershipDecision` — resolves ownership, and on `changed` closes the open segment, opens a new one, recomputes both access arrays, and writes an `OWNERSHIP_CHANGE` audit event, all inside one `transaction.atomic()`.
  - `reassign_caretaker(*, actor, lead: Lead, caretaker, reason: str) -> Lead` — the only manual ownership write path.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_ownership_service.py
def test_ownership_change_closes_the_previous_segment_and_opens_one():
    lead = make_lead(caretaker=u1, policy="LATEST_ORDER")
    apply_order_to_lead(actor=editor, lead=lead, order=order(closer=u2), lock_active=False)
    segments = list(OwnershipSegment.objects.filter(lead=lead).order_by("started_at"))
    assert [s.caretaker_id for s in segments] == [u1.id, u2.id]
    assert segments[0].ended_at is not None
    assert segments[1].ended_at is None


def test_ownership_change_writes_an_audit_event_naming_the_actor():
    ...
    event = LeadAuditEvent.objects.get(lead=lead, action="OWNERSHIP_CHANGE")
    assert event.actor_id == editor.id
    assert event.payload["from"] == str(u1.id)
    assert event.payload["to"] == str(u2.id)


def test_access_arrays_are_recomputed_on_ownership_change():
    ...
    lead.refresh_from_db()
    assert lead.access_user_ids == [str(u2.id)]


def test_unchanged_ownership_writes_no_segment_and_no_audit_event():
    before = OwnershipSegment.objects.count()
    apply_order_to_lead(actor=editor, lead=lead, order=order(closer=u1), lock_active=False)
    assert OwnershipSegment.objects.count() == before
    assert not LeadAuditEvent.objects.filter(lead=lead, action="OWNERSHIP_CHANGE").exists()


def test_a_lead_never_has_two_open_segments():
    with pytest.raises(IntegrityError):
        OwnershipSegment.objects.create(lead=lead, caretaker=u2, started_at=now(), ended_at=None)


def test_deactivated_caretaker_routes_the_lead_to_team_unassigned():
    # Review Focus #2
    u1.is_active = False
    u1.save()
    reassign_caretaker(actor=editor, lead=lead, caretaker=u1, reason="test")
    lead.refresh_from_db()
    assert lead.routing_state == "TEAM_UNASSIGNED"
    assert lead.access_user_ids == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_ownership_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_order_to_lead'`

- [ ] **Step 3: Implement the two models and `crm/leads/services.py`**

`active_user_ids` is always derived from `accounts.User.objects.filter(is_active=True)` inside the service — never passed in by a caller, so a disabled user cannot be re-granted access by a stale argument. Both write paths are wrapped in `transaction.atomic()`; the segment close, segment open, access recompute, `Lead` update and audit insert must commit or fail together.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec web python -m pytest tests/db/test_ownership_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/leads/models.py crm/leads/services.py crm/leads/migrations tests/db/test_ownership_service.py
git commit -m "feat: add ownership segments, lead audit events and the ownership apply service"
```

---

### Task 8: Follow-up cycles and notes

**Files:**
- Create: `crm/followcycles/__init__.py`, `apps.py`, `models.py`, `services.py`, `selectors.py`
- Create: `crm/followcycles/migrations/0001_initial.py`
- Modify: `config/settings/base.py`
- Create: `tests/db/test_followcycles.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py` (`CYCLE_END_REASONS`), `Lead`.
- Produces:
  - `FollowupCycle` — `lead: FK`, `trigger_order: FK(orders.Order, null=True)`, `status` in `("ACTIVE", "ENDED")`, `started_at`, `ended_at: null=True`, `ended_reason: null=True`, `lock_active: BooleanField(default=False)`, with `UniqueConstraint(fields=["lead"], condition=Q(status="ACTIVE"), name="ux_cycle_one_active")`.
  - `Note` — `lead: FK`, `cycle: FK(null=True)`, `order: FK(orders.Order, null=True)`, `body`, `created_by`, `created_at`, `updated_at`. **No** `related_name` on `Order` that implies ownership; a note references an order and is never cascaded by one (`on_delete=SET_NULL`).
  - `open_cycle(*, actor, lead, trigger_order=None) -> FollowupCycle`
  - `end_cycle(*, actor, cycle, reason: str) -> FollowupCycle`
  - `add_note(*, actor, lead, body: str, order=None) -> Note`
  - `cycle_history(user, lead) -> list[FollowupCycle]` — newest first, every cycle, never filtered by status.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_followcycles.py
def test_opening_a_new_cycle_ends_the_previous_one_with_a_reason():
    first = open_cycle(actor=editor, lead=lead)
    second = open_cycle(actor=editor, lead=lead, trigger_order=new_order)
    first.refresh_from_db()
    assert first.status == "ENDED"
    assert first.ended_reason == "NEW_ORDER"
    assert second.status == "ACTIVE"


def test_historical_cycles_are_never_deleted():
    open_cycle(actor=editor, lead=lead)
    open_cycle(actor=editor, lead=lead, trigger_order=new_order)
    assert FollowupCycle.objects.filter(lead=lead).count() == 2
    assert len(cycle_history(editor, lead)) == 2


def test_a_lead_never_has_two_active_cycles():
    open_cycle(actor=editor, lead=lead)
    with pytest.raises(IntegrityError):
        FollowupCycle.objects.create(lead=lead, status="ACTIVE", started_at=now())


def test_deleting_an_order_keeps_the_note_and_nulls_the_reference():
    note = add_note(actor=editor, lead=lead, body="คุยแล้ว", order=order)
    order.delete()
    note.refresh_from_db()
    assert note.lead_id == lead.id
    assert note.order_id is None


def test_note_is_attached_to_the_active_cycle_when_one_exists():
    cycle = open_cycle(actor=editor, lead=lead)
    assert add_note(actor=editor, lead=lead, body="x").cycle_id == cycle.id


def test_ending_a_cycle_requires_a_known_reason():
    with pytest.raises(ValueError):
        end_cycle(actor=editor, cycle=open_cycle(actor=editor, lead=lead), reason="BECAUSE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_followcycles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.followcycles'`

- [ ] **Step 3: Implement the models, services and selector**

`open_cycle` ends any active cycle in the same transaction, with `NEW_ORDER` when a `trigger_order` is given and `MANUAL_CLOSE` otherwise. `add_note` also refreshes `Lead.latest_note` / `latest_note_updated_at` as a cached copy, with the `Note` rows remaining authoritative. `cycle_history` must not filter on `status`, so historical cycles stay retrievable even once they are hidden from the daily work view.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec web python -m pytest tests/db/test_followcycles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/followcycles config/settings/base.py tests/db/test_followcycles.py
git commit -m "feat: add follow-up cycles and lead-owned notes"
```

---

### Task 9: Contact attempts and appointments

**Files:**
- Create: `crm/domain/appointments.py`
- Modify: `crm/followcycles/models.py`, `services.py`, `selectors.py`
- Create: `crm/followcycles/migrations/0002_attempts_appointments.py`
- Create: `tests/db/test_contact_attempts.py`
- Create: `tests/unit/test_appointment_state.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py` (`CONTACT_ATTEMPT_RESULTS`, `APPOINTMENT_STATUSES`, `COMPUTED_LEAD_STATUSES`), `crm/domain/bangkok.py`, `Team.policy`.
- Produces:
  - `ContactAttempt` — `lead: FK`, `cycle: FK(null=True)`, `user: FK(accounts.User)`, `attempted_at`, `result`, `note`.
  - `Appointment` — `lead: FK`, `cycle: FK(null=True)`, `scheduled_at`, `status` (default `SCHEDULED`), `note`, `created_by`, `created_at`.
  - `log_contact_attempt(*, actor, lead, result: str, note: str = "", attempted_at=None) -> ContactAttemptOutcome` where `ContactAttemptOutcome(attempt: ContactAttempt, no_answer_streak: int, stop_recommended: bool)`.
  - `set_appointment(*, actor, lead, scheduled_at, note: str = "") -> Appointment`
  - `crm/domain/appointments.py::appointment_state(scheduled_at: datetime | None, now: datetime) -> str | None` returning `"APPOINTMENT_TODAY"`, `"APPOINTMENT_FUTURE"`, `"OVERDUE"` or `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_appointment_state.py
def test_appointment_later_today_in_bangkok_is_today_not_future():
    assert appointment_state(
        datetime(2026, 10, 13, 23, 0, tzinfo=BANGKOK_TZ),
        datetime(2026, 10, 13, 8, 0, tzinfo=BANGKOK_TZ)) == "APPOINTMENT_TODAY"


def test_appointment_earlier_today_is_still_today_not_overdue():
    assert appointment_state(
        datetime(2026, 10, 13, 8, 0, tzinfo=BANGKOK_TZ),
        datetime(2026, 10, 13, 17, 0, tzinfo=BANGKOK_TZ)) == "APPOINTMENT_TODAY"


def test_yesterday_is_overdue_and_tomorrow_is_future():
    now = datetime(2026, 10, 13, 12, 0, tzinfo=BANGKOK_TZ)
    assert appointment_state(now - timedelta(days=1), now) == "OVERDUE"
    assert appointment_state(now + timedelta(days=1), now) == "APPOINTMENT_FUTURE"


def test_no_appointment_has_no_computed_state():
    assert appointment_state(None, datetime(2026, 10, 13, tzinfo=BANGKOK_TZ)) is None
```

```python
# tests/db/test_contact_attempts.py
def test_no_answer_attempts_accumulate_a_streak():
    for _ in range(3):
        outcome = log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    assert outcome.no_answer_streak == 3


def test_a_reached_result_resets_the_streak():
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    outcome = log_contact_attempt(actor=telesell, lead=lead, result="CALLBACK_REQUESTED")
    assert outcome.no_answer_streak == 0


def test_reaching_the_threshold_recommends_but_never_performs_a_stop():
    lead.team.policy = {"ownershipPolicy": "EXCLUSIVE", "maxNoAnswerAttempts": 2}
    lead.team.save()
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    outcome = log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    assert outcome.stop_recommended is True
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_contact_attempt_does_not_change_lifecycle_status():
    before = lead.lifecycle_status
    log_contact_attempt(actor=telesell, lead=lead, result="NO_ANSWER")
    lead.refresh_from_db()
    assert lead.lifecycle_status == before


def test_attempt_records_actor_time_result_note_and_cycle():
    cycle = open_cycle(actor=editor, lead=lead)
    attempt = log_contact_attempt(actor=telesell, lead=lead, result="BUSY", note="สายไม่ว่าง").attempt
    assert (attempt.user_id, attempt.result, attempt.note, attempt.cycle_id) == (
        telesell.id, "BUSY", "สายไม่ว่าง", cycle.id)
    assert attempt.attempted_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_appointment_state.py -v` then `docker compose exec web python -m pytest tests/db/test_contact_attempts.py -v`
Expected: FAIL — `appointment_state` and `log_contact_attempt` undefined

- [ ] **Step 3: Implement the models, `appointment_state`, and the two services**

"ติดต่อไม่ได้" is a contact *result*, never a lifecycle status — `log_contact_attempt` updates `Lead.no_answer_streak` and returns `stop_recommended`, and writes `lifecycle_status` only when `Team.policy["autoStop"]` is explicitly `True` (default `False`). `appointment_state` compares Bangkok calendar days via `crm/domain/bangkok.py`, not timestamps, so an appointment earlier the same day is `APPOINTMENT_TODAY` rather than `OVERDUE`. `set_appointment` writes an `APPOINTMENT_SET` audit event and refreshes `Lead.next_follow_at`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_appointment_state.py -v && docker compose exec web python -m pytest tests/db/test_contact_attempts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/domain/appointments.py crm/followcycles tests/unit/test_appointment_state.py tests/db/test_contact_attempts.py
git commit -m "feat: add contact attempts with no-answer streak and Bangkok appointment states"
```

---

### Task 10: Missed-channel recomputation (ported)

Owns **Review Focus #3** (tied timestamps).

**Files:**
- Create: `crm/domain/missed_channel.py`
- Create: `crm/leads/recompute.py`
- Create: `tests/unit/test_missed_channel_parity.py`
- Create: `tests/db/test_missed_channel_recompute.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py`, `load_cases` from Task 1, `OwnershipSegment`.
- Produces:
  - `recompute_missed_channel(*, baseline_started_at: datetime, caretaker_user_id: str | None, orders: Sequence[MissedChannelOrder], policy: Mapping[str, bool] | None = None) -> MissedChannelResult`
  - `@dataclass(frozen=True) MissedChannelOrder(order_id: str, ordered_at: datetime, closer_user_id: str | None, order_status: str)`
  - `@dataclass(frozen=True) MissedChannelResult(count: int, explanations: tuple[MissedChannelExplanation, ...])`, `MissedChannelExplanation(order_id, ordered_at, closer_user_id, counted: bool, reason: str)` with `reason ∈ {COUNTED_NO_CLOSER, COUNTED_OTHER_CLOSER, RESET_BY_CARETAKER, EXCLUDED_CANCELLED, EXCLUDED_COD_RETURNED, BEFORE_BASELINE}`
  - `missed_channel_group(count: int) -> str` returning `"0" | "1" | "2" | "3+"`
  - `crm/leads/recompute.py::recompute_lead_missed_channel(*, lead: Lead, hx63_baseline: datetime | None = None) -> int` — reads the lead's open ownership segment start (or `hx63_baseline`, or lead creation) as the baseline, loads the customer's orders, writes `Lead.missed_channel_count`, returns it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_missed_channel_parity.py
@pytest.mark.parametrize("case", load_cases("missed_channel"), ids=lambda c: c["name"])
def test_missed_channel_matches_prototype(case):
    result = recompute_missed_channel(**build_input(case["input"]))
    assert result.count == case["expected"]["count"]
    assert [e.reason for e in result.explanations] == [
        e["reason"] for e in case["expected"]["explanations"]]


def test_tied_timestamps_break_on_order_id_deterministically():
    # Review Focus #3
    a = MissedChannelOrder("B", T1, "u2", "DELIVERED")
    b = MissedChannelOrder("A", T1, "u1", "DELIVERED")
    forward = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id="u1", orders=[a, b])
    reverse = recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id="u1", orders=[b, a])
    assert forward.count == reverse.count
    assert [e.order_id for e in forward.explanations] == ["A", "B"]


def test_reimporting_the_same_order_never_inflates_the_count():
    order = MissedChannelOrder("O1", T1, None, "DELIVERED")
    assert recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id="u1", orders=[order, order]).count == 1


def test_cancelled_and_cod_returned_never_count():
    orders = [MissedChannelOrder("O1", T1, None, "CANCELLED"),
              MissedChannelOrder("O2", T1, None, "COD_RETURNED")]
    result = recompute_missed_channel(baseline_started_at=T0, caretaker_user_id="u1", orders=orders)
    assert result.count == 0
    assert [e.reason for e in result.explanations] == ["EXCLUDED_CANCELLED", "EXCLUDED_COD_RETURNED"]


def test_caretaker_closing_a_valid_order_resets_the_count_to_zero():
    orders = [MissedChannelOrder("O1", T1, None, "DELIVERED"),
              MissedChannelOrder("O2", T2, "u1", "DELIVERED")]
    assert recompute_missed_channel(
        baseline_started_at=T0, caretaker_user_id="u1", orders=orders).count == 0


def test_orders_before_the_baseline_are_not_counted():
    order = MissedChannelOrder("O1", T0 - timedelta(days=1), None, "DELIVERED")
    result = recompute_missed_channel(baseline_started_at=T0, caretaker_user_id="u1", orders=[order])
    assert result.count == 0
    assert result.explanations[0].reason == "BEFORE_BASELINE"


def test_group_labels_do_not_collapse_the_stored_integer():
    assert [missed_channel_group(n) for n in (0, 1, 2, 7)] == ["0", "1", "2", "3+"]
```

```python
# tests/db/test_missed_channel_recompute.py
def test_recompute_is_idempotent():
    first = recompute_lead_missed_channel(lead=lead)
    assert recompute_lead_missed_channel(lead=lead) == first


def test_caretaker_change_resets_the_baseline_so_earlier_orders_stop_counting():
    make_order(customer=lead.customer, closer=None, ordered_at=T1, status="DELIVERED")
    assert recompute_lead_missed_channel(lead=lead) == 1
    reassign_caretaker(actor=editor, lead=lead, caretaker=u2, reason="handover")
    lead.refresh_from_db()
    assert recompute_lead_missed_channel(lead=lead) == 0


def test_hx63_baseline_suppresses_pre_snapshot_history():
    # D023
    make_order(customer=lead.customer, closer=None,
               ordered_at=datetime(2026, 10, 1, tzinfo=timezone.utc), status="DELIVERED")
    assert recompute_lead_missed_channel(
        lead=lead, hx63_baseline=datetime(2026, 10, 13, 12, 31, 55, tzinfo=timezone.utc)) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_missed_channel_parity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.domain.missed_channel'`

- [ ] **Step 3: Implement `crm/domain/missed_channel.py` and `crm/leads/recompute.py`**

Port `$PROTO/src/domain/missed-channel.ts` exactly, keeping the three stages in order: de-duplicate by `order_id` with last-occurrence-wins, sort by `(ordered_at, order_id)`, then fold. The explanation list is part of the contract — it is what makes the counter auditable, and dropping it would make a wrong count undiagnosable. `recompute_lead_missed_channel` never increments; it always recomputes from history, so repeated imports, rollbacks and out-of-order arrivals settle identically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_missed_channel_parity.py -v && docker compose exec web python -m pytest tests/db/test_missed_channel_recompute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/domain/missed_channel.py crm/leads/recompute.py tests/unit/test_missed_channel_parity.py tests/db/test_missed_channel_recompute.py
git commit -m "feat: port missed-channel recomputation from order and ownership history"
```

---

### Task 11: Last-order-age filter and reallocation review

Owns **Review Focus #1** and **#5**.

**Files:**
- Create: `crm/domain/last_order_age.py`
- Create: `tests/unit/test_last_order_age_parity.py`

**Interfaces:**
- Consumes: `crm/domain/bangkok.py`, `load_cases` from Task 1.
- Produces:
  - `LAST_ORDER_AGE_PRESETS: tuple[AgePreset, ...]` where `AgePreset(id: str, label: str, min_days: int | None, max_days: int | None, no_order: bool)`, ids exactly `("0_7", "8_15", "16_30", "31_45", "46_60", "61_90", "OVER_90", "NO_ORDER")` with the prototype's Thai labels verbatim.
  - `REALLOCATION_REVIEW_DEFAULT_DAYS = 45`
  - `resolve_last_order_age_filter(preset: str | None, min_days: int | None, max_days: int | None) -> AgeCutoffs | None`
  - `last_order_age_cutoffs(preset, min_days, max_days, now: datetime) -> AgeCutoffs | None` where `AgeCutoffs(no_order: bool, ordered_at_from: datetime | None, ordered_at_before: datetime | None, min_days: int | None, max_days: int | None)`
  - `matches_last_order_age(last_order_at: datetime | None, preset, min_days, max_days, now) -> bool`
  - `is_reallocation_review_candidate(*, last_order_at: datetime | None, caretaker_user_id: str | None, lifecycle_status: str, protection: ProtectionSummary, now: datetime, threshold_days: int) -> bool`
  - `@dataclass(frozen=True) ProtectionSummary(active_appointment: bool, active_followup_cycle: bool, ownership_lock_active: bool, next_follow_at: datetime | None)`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_last_order_age_parity.py
@pytest.mark.parametrize("case", load_cases("last_order_age"), ids=lambda c: c["name"])
def test_last_order_age_matches_prototype(case):
    assert matches_last_order_age(**build_input(case["input"])) is case["expected"]["matches"]


def test_preset_ids_and_labels_are_the_locked_set():
    assert [p.id for p in LAST_ORDER_AGE_PRESETS] == [
        "0_7", "8_15", "16_30", "31_45", "46_60", "61_90", "OVER_90", "NO_ORDER"]
    assert LAST_ORDER_AGE_PRESETS[-1].label == "ไม่มีออเดอร์เลย"


def test_bucket_boundaries_are_inclusive_at_both_ends():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=BANGKOK_TZ)
    for days, preset in ((7, "0_7"), (8, "8_15"), (15, "8_15"), (16, "16_30"), (91, "OVER_90")):
        ordered = now - timedelta(days=days)
        assert matches_last_order_age(ordered, preset, None, None, now) is True


def test_late_evening_order_lands_in_the_zero_to_seven_bucket():
    # Review Focus #1
    ordered = datetime(2026, 10, 31, 23, 45, tzinfo=BANGKOK_TZ)
    now = datetime(2026, 11, 1, 0, 15, tzinfo=BANGKOK_TZ)
    assert matches_last_order_age(ordered, "0_7", None, None, now) is True
    assert matches_last_order_age(ordered, "8_15", None, None, now) is False


def test_no_order_bucket_matches_only_a_null_last_order():
    # Review Focus #5
    now = datetime(2026, 11, 1, tzinfo=BANGKOK_TZ)
    assert matches_last_order_age(None, "NO_ORDER", None, None, now) is True
    assert matches_last_order_age(now - timedelta(days=5), "NO_ORDER", None, None, now) is False


def test_no_order_cannot_be_combined_with_a_numeric_range():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter("NO_ORDER", 10, 20)


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter("LAST_CENTURY", None, None)


def test_min_days_above_max_days_raises():
    with pytest.raises(ValueError):
        resolve_last_order_age_filter(None, 40, 10)


def test_lead_with_no_order_is_never_a_reallocation_candidate():
    # Review Focus #5
    assert is_reallocation_review_candidate(
        last_order_at=None, caretaker_user_id="u1", lifecycle_status="FOLLOWING",
        protection=no_protection, now=NOW, threshold_days=45) is False


def test_stopped_lead_is_never_a_reallocation_candidate():
    assert is_reallocation_review_candidate(
        last_order_at=NOW - timedelta(days=200), caretaker_user_id="u1",
        lifecycle_status="STOPPED", protection=no_protection,
        now=NOW, threshold_days=45) is False


@pytest.mark.parametrize("field", ["active_appointment", "active_followup_cycle", "ownership_lock_active"])
def test_any_active_protection_excludes_a_lead_from_review(field):
    protection = replace(no_protection, **{field: True})
    assert is_reallocation_review_candidate(
        last_order_at=NOW - timedelta(days=200), caretaker_user_id="u1",
        lifecycle_status="FOLLOWING", protection=protection,
        now=NOW, threshold_days=45) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -m unit tests/unit/test_last_order_age_parity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.domain.last_order_age'`

- [ ] **Step 3: Implement `crm/domain/last_order_age.py`**

`last_order_age_cutoffs` returns an inclusive `ordered_at_from` and an **exclusive** `ordered_at_before`, both snapped to Bangkok day starts via `crm/domain/bangkok.py` — the SQL layer in Task 12 relies on that asymmetry, and computing bounds from a raw timestamp instead of a day start is exactly the Review Focus #1 bug. Day counts are validated as integers in `[0, 36500]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -m unit tests/unit/test_last_order_age_parity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/domain/last_order_age.py tests/unit/test_last_order_age_parity.py
git commit -m "feat: port last-order-age buckets and reallocation review candidacy"
```

---

### Task 12: Global Unassigned intake (D019 / D024)

**Files:**
- Create: `crm/intake/__init__.py`, `apps.py`, `models.py`, `services.py`, `selectors.py`
- Create: `crm/intake/migrations/0001_initial.py`
- Modify: `config/settings/base.py`
- Create: `tests/db/test_intake.py`

**Interfaces:**
- Consumes: `crm/domain/enums.py` (`INTAKE_STATUSES`, `SOURCE_KINDS`), `Customer.customer_uid`, `Team`, `Lead`.
- Produces:
  - `IntakeItem` — `customer: FK`, `source_order_id: CharField(blank=True)`, `source_kind`, `source_ref`, `status` (default `GLOBAL_UNASSIGNED`), `assigned_team: FK(null=True)`, `created_lead: FK(leads.Lead, null=True)`, `resolved_by: FK(accounts.User, null=True)`, `resolved_at: null=True`, `created_at`, `updated_at`, with `UniqueConstraint(fields=["customer", "source_order_id"], name="ux_intake_customer_source_order")`.
  - `upsert_intake(*, customer, source_order_id: str, source_kind: str, source_ref: str = "") -> tuple[IntakeItem, bool]` — returns `(item, created)`; the deterministic D024 identity is `(customer, source_order_id)`, never the import batch.
  - `resolve_intake_to_team(*, actor, intake: IntakeItem, team: Team) -> Lead` — reuses the existing `team:customer_uid` Lead or creates it, marks the intake `CONVERTED`, writes an `INTAKE_CONVERTED` audit event.
  - The Global Unassigned **queue** selector is not in this task; it needs the keyset helpers and lands in Task 13 with the other queues.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_intake.py
def test_same_customer_and_source_order_across_two_batches_creates_one_intake():
    # D024 acceptance
    first, created_first = upsert_intake(customer=c, source_order_id="SO-1",
                                         source_kind="LITE_DETAIL_ORDER", source_ref="batch-A")
    second, created_second = upsert_intake(customer=c, source_order_id="SO-1",
                                           source_kind="LITE_DETAIL_ORDER", source_ref="batch-B")
    assert created_first is True and created_second is False
    assert first.id == second.id
    assert IntakeItem.objects.filter(customer=c).count() == 1


def test_a_different_source_order_creates_a_separate_intake():
    upsert_intake(customer=c, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    upsert_intake(customer=c, source_order_id="SO-2", source_kind="LITE_DETAIL_ORDER")
    assert IntakeItem.objects.filter(customer=c).count() == 2


def test_intake_never_creates_a_lead_before_it_is_resolved():
    upsert_intake(customer=c, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    assert not Lead.objects.filter(customer=c).exists()


def test_an_existing_lead_does_not_auto_route_a_team_unknown_order():
    make_lead(customer=c, team_code="CRM_TEAM")
    item, created = upsert_intake(customer=c, source_order_id="SO-1",
                                  source_kind="LITE_DETAIL_ORDER")
    assert created is True
    assert item.status == "GLOBAL_UNASSIGNED"
    assert item.assigned_team_id is None


def test_resolving_reuses_the_existing_lead_for_that_team():
    existing = make_lead(customer=c, team_code="CRM_TEAM")
    item, _ = upsert_intake(customer=c, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    assert resolve_intake_to_team(actor=editor, intake=item, team=existing.team).id == existing.id
    item.refresh_from_db()
    assert item.status == "CONVERTED"
    assert item.resolved_by_id == editor.id


def test_resolving_creates_the_lead_when_none_exists():
    item, _ = upsert_intake(customer=c, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    lead = resolve_intake_to_team(actor=editor, intake=item, team=crm_team)
    assert lead.lead_key == f"CRM_TEAM:{c.customer_uid}"


def test_no_team_named_global_unassigned_exists():
    # D019: Global Unassigned is a pre-Lead queue, never a fake Team
    assert not Team.objects.filter(code__icontains="GLOBAL_UNASSIGNED").exists()
    assert Lead._meta.get_field("team").null is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_intake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crm.intake'`

- [ ] **Step 3: Implement the model and the two services**

`upsert_intake` uses `get_or_create` on `(customer, source_order_id)` so a re-imported row reuses the same intake row regardless of batch; `source_ref` is updated on an existing row but the identity never changes. `resolve_intake_to_team` runs in one transaction: `Lead.objects.get_or_create` on `(team, customer)`, intake status to `CONVERTED`, `created_lead` set, access arrays computed, audit event written.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec web python -m pytest tests/db/test_intake.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/intake config/settings/base.py tests/db/test_intake.py
git commit -m "feat: add Global Unassigned pre-Lead intake with D024 deterministic identity"
```

---

### Task 13: Keyset pagination and the lead queue selectors

**Files:**
- Modify: `crm/core/pagination.py`
- Create: `crm/leads/selectors.py`
- Modify: `docs/DECISIONS.md` (record the offset/keyset boundary)
- Create: `tests/db/test_lead_queues.py`
- Create: `tests/unit/test_keyset_cursor.py`

**Interfaces:**
- Consumes: `crm/domain/last_order_age.py`, `Lead`, `IntakeItem`, `StaffScopedQuerySet`.
- Produces:
  - `crm/core/pagination.py` — `@dataclass(frozen=True) KeysetPage(items: list, next_cursor: str | None, has_more: bool)`; `encode_cursor(values: tuple) -> str`, `decode_cursor(raw: str | None) -> tuple | None` (URL-safe base64 of a compact JSON tuple; a malformed cursor returns `None` rather than raising, so a hand-edited URL degrades to page one).
  - `crm/leads/selectors.py` — `_lead_queue_queryset(user, filters: LeadQueueFilters, after_cursor: str | None, limit: int) -> QuerySet` (the seek query, exposed for the no-OFFSET assertion); `lead_queue(user, *, filters: LeadQueueFilters, after_cursor: str | None = None, limit: int = 50) -> KeysetPage`; `my_work_queue(user, *, after_cursor=None, limit=50) -> KeysetPage` (acceptance item A "My Work" — `lead_queue` with `caretaker_user_id` pinned to `user` server-side, never from the querystring); `team_unassigned_queue(user, *, team=None, after_cursor=None, limit=50) -> KeysetPage`; `stopped_review_queue(user, *, after_cursor=None, limit=50) -> KeysetPage`; `reallocation_review_queue(user, *, threshold_days: int = 45, after_cursor=None, limit=50) -> KeysetPage`; `global_unassigned_queue(user, *, after_cursor=None, limit=50) -> KeysetPage` (over `IntakeItem`, status `GLOBAL_UNASSIGNED`); `@dataclass(frozen=True) LeadQueueFilters(team_code, caretaker_user_id, lifecycle_status, age_preset, age_min_days, age_max_days, appointment_state, followup_state, ownership_lock_state, exclude_stopped, missed_channel_group, keyword)` with `from_query(query: dict)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_keyset_cursor.py
def test_cursor_round_trips():
    assert decode_cursor(encode_cursor((1730000000000, 42))) == [1730000000000, 42]


def test_malformed_cursor_decodes_to_none_instead_of_raising():
    for raw in ("", "not-base64!!", encode_cursor((1,))[:-2]):
        assert decode_cursor(raw) is None
```

```python
# tests/db/test_lead_queues.py
def test_queue_walks_every_row_exactly_once_across_pages():
    make_leads(25)
    seen, cursor = [], None
    while True:
        page = lead_queue(editor, filters=NO_FILTERS, after_cursor=cursor, limit=10)
        seen.extend(lead.id for lead in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 25


@pytest.mark.parametrize("builder", [
    lambda u: _lead_queue_queryset(u, NO_FILTERS, None, 10),
    lambda u: _lead_queue_queryset(u, replace(NO_FILTERS, age_preset="OVER_90"), None, 10),
])
def test_queue_sql_contains_no_offset(builder):
    # acceptance criterion F
    assert "OFFSET" not in str(builder(editor).query).upper()


def test_my_work_ignores_a_querystring_caretaker():
    page = my_work_queue(telesell_s1, limit=50)
    assert all(lead.caretaker_id == telesell_s1.id for lead in page.items)


def test_global_unassigned_queue_lists_only_unresolved_intake():
    upsert_intake(customer=c, source_order_id="SO-1", source_kind="LITE_DETAIL_ORDER")
    converted, _ = upsert_intake(customer=c2, source_order_id="SO-2",
                                 source_kind="LITE_DETAIL_ORDER")
    resolve_intake_to_team(actor=editor, intake=converted, team=crm_team)
    ids = {item.id for item in global_unassigned_queue(editor, limit=50).items}
    assert converted.id not in ids


def test_inserting_a_row_between_pages_does_not_skip_or_repeat():
    make_leads(20)
    first = lead_queue(editor, filters=NO_FILTERS, limit=10)
    make_lead()  # arrives mid-walk
    second = lead_queue(editor, filters=NO_FILTERS, after_cursor=first.next_cursor, limit=10)
    assert not {lead.id for lead in first.items} & {lead.id for lead in second.items}


def test_telesell_queue_is_scoped_to_their_own_staff_code():
    assert all(lead.customer.staff_code == "S0001"
               for lead in lead_queue(telesell("S0001"), filters=NO_FILTERS, limit=50).items)


def test_age_filter_uses_the_domain_cutoffs():
    old = make_lead(last_order_at=NOW - timedelta(days=100))
    recent = make_lead(last_order_at=NOW - timedelta(days=3))
    ids = {lead.id for lead in lead_queue(
        editor, filters=replace(NO_FILTERS, age_preset="OVER_90"), limit=50).items}
    assert ids == {old.id}


def test_reallocation_queue_never_reassigns_anything():
    lead = make_lead(last_order_at=NOW - timedelta(days=200), caretaker=u1)
    before = lead.caretaker_id
    reallocation_review_queue(editor, threshold_days=45, limit=50)
    lead.refresh_from_db()
    assert lead.caretaker_id == before
    assert not OwnershipSegment.objects.filter(lead=lead).exclude(caretaker=u1).exists()


def test_stopped_review_queue_excludes_unassigned_leads():
    make_lead(lifecycle_status="STOPPED", stopped_reason="MANUAL_STOP", caretaker=u1)
    make_lead(routing_state="TEAM_UNASSIGNED", caretaker=None)
    assert all(lead.lifecycle_status == "STOPPED"
               for lead in stopped_review_queue(editor, limit=50).items)


def test_one_lead_list_page_does_not_scan_orders_per_lead():
    make_leads(10, orders_each=5)
    with django_assert_max_num_queries(6):
        list(lead_queue(editor, filters=NO_FILTERS, limit=10).items)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_keyset_cursor.py -v`
Expected: FAIL with `ImportError: cannot import name 'encode_cursor'`

- [ ] **Step 3: Implement the cursor helpers, then the six queue selectors**

`my_work_queue` pins the caretaker to `user` inside the selector and must ignore any `caretaker_user_id` reaching it from `LeadQueueFilters.from_query`, so a telesale cannot read another caretaker's work by editing the querystring. `global_unassigned_queue` runs over `IntakeItem` filtered to `status="GLOBAL_UNASSIGNED"`, keyed on `(created_at, id)`.

Every queue orders by `(last_order_at NULLS LAST, id)` and seeks with a tuple comparison on the decoded cursor — never `OFFSET`, and never `.count()` on the full queue, since acceptance criterion F forbids both for operational lists. `Lead` carries `last_order_at`, `total_orders` and `missed_channel_count` as maintained columns, so the list page reads them directly instead of aggregating `crm_order` per row. Each selector calls `.for_user(user)` first. Append a `docs/DECISIONS.md` entry recording that the offset pager stays for the legacy follow-up list while the new queues are keyset-only, and why both coexist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_keyset_cursor.py -v && docker compose exec web python -m pytest tests/db/test_lead_queues.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/core/pagination.py crm/leads/selectors.py docs/DECISIONS.md tests/unit/test_keyset_cursor.py tests/db/test_lead_queues.py
git commit -m "feat: add keyset lead queues with no offset pagination"
```

---

### Task 14: Team-lead permissions and server-side denial

Owns **Review Focus #4** (cross-team leak).

**Files:**
- Modify: `crm/core/permissions.py`
- Modify: `crm/core/scoping.py`
- Modify: `crm/accounts/models.py` (add `team_scope`)
- Create: `crm/accounts/migrations/00NN_user_team_scope.py`
- Modify: `tests/conftest.py` — the `make_user` fixture currently accepts only `role` and `staff_code`; extend it to `_make(role="ทั่วไป", staff_code="", team_scope=(), is_active=True)` so `FakeUser` can stand in for the new predicates
- Modify: `docs/DECISIONS.md` (record the role mapping)
- Create: `tests/unit/test_team_lead_permissions.py`
- Create: `tests/view/test_lead_permission_denial.py`

**Interfaces:**
- Consumes: `crm/core/permissions.py` existing predicates, `Lead`, `Team`.
- Produces:
  - `accounts.User.team_scope: ArrayField(CharField(max_length=32), default=list)` — team codes a `TEAM_LEAD` is authorized for. Never read from a request.
  - `crm/core/permissions.py` — `is_team_lead(user) -> bool`; `team_scope(user) -> frozenset[str]`; `can_view_lead(user, lead) -> bool`; `can_edit_lead(user, lead) -> bool`; `can_reassign_caretaker(user, lead) -> bool`; `can_resolve_intake(user) -> bool`; `can_review_stopped(user, lead) -> bool`; plus `MSG_LEAD_OUT_OF_SCOPE = "ไม่มีสิทธิ์เข้าถึง Lead ของทีมอื่น"` and registration of the new predicates in `_PREDICATES`.
  - `crm/core/scoping.py` — `StaffScopedQuerySet.for_user` gains a team-scope branch for `TEAM_LEAD`, reading the **lead's own** `access_team_ids`, declared by a new `TEAM_PATH` class attribute on scoped models.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_team_lead_permissions.py
def test_team_lead_may_act_only_within_their_team_scope():
    lead_user = make_user(role="TEAM_LEAD", team_scope=["CRM_TEAM"])
    assert can_edit_lead(lead_user, lead(team_code="CRM_TEAM")) is True
    assert can_edit_lead(lead_user, lead(team_code="UPSELL_TEAM")) is False


def test_team_lead_with_an_empty_scope_can_do_nothing():
    assert can_edit_lead(make_user(role="TEAM_LEAD", team_scope=[]), lead("CRM_TEAM")) is False


def test_telesale_cannot_reassign_a_caretaker_even_on_their_own_lead():
    telesell_user = make_user(role="พนักงาน", staff_code="S0001")
    assert can_edit_lead(telesell_user, lead("CRM_TEAM", staff_code="S0001")) is True
    assert can_reassign_caretaker(telesell_user, lead("CRM_TEAM", staff_code="S0001")) is False


def test_telesale_cannot_resolve_intake():
    assert can_resolve_intake(make_user(role="พนักงาน", staff_code="S0001")) is False


def test_v2_admin_maps_to_editor_not_to_this_repos_admin():
    assert can_reassign_caretaker(make_user(role="EDITOR"), lead("CRM_TEAM")) is True
    assert can_reassign_caretaker(make_user(role="ADMIN"), lead("CRM_TEAM")) is False


def test_a_disabled_user_has_no_lead_permissions():
    disabled = make_user(role="EDITOR", is_active=False)
    assert can_view_lead(disabled, lead("CRM_TEAM")) is False
    assert can_edit_lead(disabled, lead("CRM_TEAM")) is False
```

```python
# tests/view/test_lead_permission_denial.py
def test_telesale_posting_a_caretaker_change_gets_403_not_a_hidden_button():
    client.force_login(telesell_s1)
    response = client.post(reverse("leads:reassign", args=[lead.id]), {"caretaker": u2.id})
    assert response.status_code == 403
    lead.refresh_from_db()
    assert lead.caretaker_id != u2.id


def test_team_lead_cannot_post_to_another_teams_lead():
    client.force_login(team_lead_crm)
    assert client.post(reverse("leads:reassign", args=[upsell_lead.id]),
                       {"caretaker": u2.id}).status_code == 403


def test_telesale_in_team_a_cannot_read_the_same_customers_team_b_lead():
    # Review Focus #4 — one customer, two teams
    customer = make_customer(_uid="0000001", staff_code="S0001")
    team_a_lead = make_lead(customer=customer, team_code="CRM_TEAM")
    team_b_lead = make_lead(customer=customer, team_code="UPSELL_TEAM")
    client.force_login(telesell_s1)
    body = client.get(reverse("followups:list")).content.decode()
    assert team_a_lead.lead_key in body
    assert team_b_lead.lead_key not in body


def test_client_supplied_actor_and_team_are_ignored():
    client.force_login(telesell_s1)
    response = client.post(reverse("leads:reassign", args=[lead.id]),
                           {"caretaker": u2.id, "role": "EDITOR",
                            "actor_id": str(editor.id), "team_id": "CRM_TEAM"})
    assert response.status_code == 403


def test_disabled_user_session_cannot_reach_the_follow_up_page():
    client.force_login(telesell_s1)
    telesell_s1.is_active = False
    telesell_s1.save()
    assert client.get(reverse("followups:list")).status_code in (302, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -m unit tests/unit/test_team_lead_permissions.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_team_lead'`

- [ ] **Step 3: Implement the predicates, the scoping branch, and `team_scope`**

Every new predicate returns `False` for a falsy or inactive user before any role check, so a disabled account loses authority without a separate code path. `can_view_lead` / `can_edit_lead` read the **lead's** `team` and `access_team_ids`, never the customer's union — that union spans teams and is what leaks in Review Focus #4. Keep the existing asymmetry intact: `EDITOR` is the spec's Admin, this repo's `ADMIN` is not, and the new predicates must not quietly make `ADMIN` stronger. Append a `docs/DECISIONS.md` entry recording the mapping and that `08_SECURITY_AND_AUTH_REQUIREMENTS.md`'s "Admin" means `EDITOR` here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -m unit tests/unit/test_team_lead_permissions.py -v && docker compose exec web python -m pytest tests/view/test_lead_permission_denial.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crm/core/permissions.py crm/core/scoping.py crm/accounts tests/conftest.py docs/DECISIONS.md tests/unit/test_team_lead_permissions.py tests/view/test_lead_permission_denial.py
git commit -m "feat: add team-lead scope predicates with server-enforced lead denial"
```

---

### Task 15: Render the lead sections into the follow-up page

**Files:**
- Modify: `crm/followups/views.py`
- Create: `crm/leads/views.py`, `crm/leads/urls.py`, `crm/leads/forms.py`
- Modify: `config/urls.py`
- Create: `templates/followups/_lead_detail.html`, `_lead_filters.html`, `_lead_queues.html`
- Modify: `templates/followups/index.html`
- Modify: `crm/core/nav.py`
- Create: `tests/view/test_followup_lead_sections.py`

**Interfaces:**
- Consumes: every selector from Task 13, the services from Tasks 7–9 and 12, the predicates from Task 14.
- Produces: routes `leads:reassign`, `leads:set_lifecycle`, `leads:stop`, `leads:reactivate`, `leads:log_attempt`, `leads:set_appointment`, `leads:add_note`, `intake:resolve`, each `@login_required` plus `@require_permission(...)`, and each re-checking object scope in its service. `followups.views.index` gains `lead_queue_page`, `lead_filters`, `lead_detail`, `missed_channel_group`, `last_order_age_label`, `reallocation_candidate` and `queue_counts` in its context. The existing `Followup` list, filters and pager keep working unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/view/test_followup_lead_sections.py
def test_follow_up_page_still_renders_the_legacy_list():
    assert "ติดตามลูกค้า" in get_page(editor).content.decode()


def test_follow_up_page_shows_lead_queue_filters_for_an_editor():
    body = get_page(editor).content.decode()
    for label in ("0–7 วัน", "ไม่มีออเดอร์เลย", "Global Unassigned", "Team Unassigned", "STOPPED"):
        assert label in body


def test_telesale_sees_no_global_unassigned_or_stopped_review_section():
    body = get_page(telesell_s1).content.decode()
    assert "Global Unassigned" not in body
    assert "STOPPED Review" not in body


def test_lead_detail_shows_missed_channel_group_and_last_order_age():
    lead = make_lead(missed_channel_count=3, last_order_at=NOW - timedelta(days=100))
    body = get_page(editor, followup_id=lead.id).content.decode()
    assert "3+" in body
    assert "100 วันที่แล้ว" in body


def test_logging_a_contact_attempt_from_the_page_does_not_change_lifecycle():
    client.force_login(telesell_s1)
    client.post(reverse("leads:log_attempt", args=[lead.id]), {"result": "NO_ANSWER", "note": ""})
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"
    assert ContactAttempt.objects.filter(lead=lead).count() == 1


def test_stopping_a_lead_requires_a_reason():
    client.force_login(editor)
    assert client.post(reverse("leads:stop"), {"lead": lead.id, "reason": ""}).status_code == 400
    lead.refresh_from_db()
    assert lead.lifecycle_status != "STOPPED"


def test_reallocation_review_rows_render_without_reassigning():
    lead = make_lead(last_order_at=NOW - timedelta(days=200), caretaker=u1)
    get_page(editor, reallocation="1")
    lead.refresh_from_db()
    assert lead.caretaker_id == u1.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/view/test_followup_lead_sections.py -v`
Expected: FAIL with `NoReverseMatch: 'leads' is not a registered namespace`

- [ ] **Step 3: Implement the views, forms, urls and templates**

Follow the existing page's conventions: filters in `_lead_filters.html` mirroring `_filters.html`, Thai labels inline (no gettext, per `docs/DECISIONS.md`), and a `_back_to_list_url`-style redirect that strips form keys. Sections the user may not reach are omitted rather than rendered disabled — `crm/core/nav.py` keeps its show-but-gray behavior for nav items, which is a different surface. Each POST view re-derives the actor from `request.user` and passes it to the service as `actor`; no view trusts a posted `actor_id`, `role` or `team_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec web python -m pytest tests/view/test_followup_lead_sections.py tests/view/test_followup_page.py tests/view/test_followup_writes.py -v`
Expected: PASS, including the two pre-existing follow-up suites

- [ ] **Step 5: Commit**

```bash
git add crm/followups/views.py crm/leads/views.py crm/leads/urls.py crm/leads/forms.py config/urls.py templates/followups crm/core/nav.py tests/view/test_followup_lead_sections.py
git commit -m "feat: render lead queues, detail and actions in the follow-up page"
```

---

### Task 16: Backfill command and docker re-run

**Files:**
- Create: `crm/leads/management/commands/backfill_leads.py`
- Modify: `compose.yaml` (add the one-shot `hx63` profile service)
- Create: `docker/hx63/Dockerfile`
- Modify: `docs/RUNBOOK.md`
- Create: `tests/db/test_backfill_leads.py`

**Interfaces:**
- Consumes: every model and service above.
- Produces: `python manage.py backfill_leads [--dry-run] [--team-code CRM_TEAM]` — creates one `Lead` per existing `Customer` with a non-empty `staff_code`, resolving the team from the caretaker's open `TeamAssignment` and falling back to `--team-code`; opens an `OwnershipSegment`; recomputes access arrays, `last_order_at`, `total_orders` and `missed_channel_count`; leaves customers with a blank `staff_code` as `IntakeItem` rows. Idempotent. Plus a `hx63` compose profile that runs the prototype's TypeScript migration tooling as a one-shot container and exits.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_backfill_leads.py
def test_backfill_creates_one_lead_per_owned_customer():
    make_customer(_uid="0000001", staff_code="S0001")
    make_customer(_uid="0000002", staff_code="S0002")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 2


def test_backfill_is_idempotent():
    make_customer(_uid="0000001", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 1
    assert OwnershipSegment.objects.filter(ended_at__isnull=True).count() == 1


def test_unowned_customers_become_intake_not_leads():
    make_customer(_uid="0000003", staff_code="")
    call_command("backfill_leads", team_code="CRM_TEAM")
    assert Lead.objects.count() == 0
    assert IntakeItem.objects.count() == 1


def test_dry_run_writes_nothing():
    make_customer(_uid="0000001", staff_code="S0001")
    call_command("backfill_leads", team_code="CRM_TEAM", dry_run=True)
    assert Lead.objects.count() == 0


def test_backfill_sets_last_order_at_and_missed_channel():
    customer = make_customer(_uid="0000001", staff_code="S0001")
    make_order(customer=customer, closer=None, ordered_at=T1, status="DELIVERED")
    call_command("backfill_leads", team_code="CRM_TEAM")
    lead = Lead.objects.get(customer=customer)
    assert lead.last_order_at is not None
    assert lead.missed_channel_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec web python -m pytest tests/db/test_backfill_leads.py -v`
Expected: FAIL with `CommandError: Unknown command: 'backfill_leads'`

- [ ] **Step 3: Implement the command and the compose profile**

The command processes customers in `iterator(chunk_size=1000)` batches inside per-batch transactions so a 340k-row backfill is restartable rather than one giant transaction. The `hx63` service goes under `profiles: ["hx63"]` so `docker compose up` never starts it; it mounts the prototype repo read-only and the snapshot data, and runs `npx tsx tools/run-hx63-migration.ts`. Record in `docs/RUNBOOK.md` that it is a one-shot cutover tool, never a running service, and that Plan 3 owns its actual execution.

- [ ] **Step 4: Verify the full stack comes up clean from scratch**

```bash
docker compose down -v
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_teams
docker compose exec web python manage.py backfill_leads --team-code CRM_TEAM
docker compose exec web python -m pytest tests/unit tests/db tests/view -v
```
Expected: migrations apply on an empty volume, every suite passes, and `docker compose ps` shows no `hx63` container.

- [ ] **Step 5: Commit**

```bash
git add crm/leads/management compose.yaml docker/hx63 docs/RUNBOOK.md tests/db/test_backfill_leads.py
git commit -m "feat: add lead backfill command and one-shot hx63 compose profile"
```

---

## Plans 2–4 (dependency order, not yet written)

Written as separate plans once Plan 1 ships. Recorded here so Plan 1 does not design them into a corner.

- **Plan 2 — Import engine.** `UPLOAD → VALIDATE → PREVIEW → RESOLVE → CONFIRM → COMMIT`, journal and rollback, field-level merge with blank-does-not-erase and stale-does-not-regress, conflict resolution, Tele business-date confirmation, and the `phone_key` uniqueness drop plus the identity-review queue. Depends on Plan 1's `Lead`, ownership services and intake. Ports `$PROTO/src/import` (~3,844 LOC).
- **Plan 3 — HX63 migration.** Runs the TypeScript `hx63-pipeline-v4` against PostgreSQL via the `hx63` compose profile from Task 16, then reconciles. **Blocked** on the human decisions: 10 staff aliases, 8,613 identity reviews, 1 unknown status affecting 120 orders, 1 null-date order, fifth source sample. Do not start before those are decided — `07_MIGRATION_AND_DATA_REQUIREMENTS.md` forbids guessing them.
- **Plan 4 — Decision Center and Backup/Restore.** Admin-only migration decision queue, backup/restore with independent reconciliation (`SOURCE_ONLY = RESTORED_ONLY = MISMATCHED = 0`), derived-counter rebuild after restore.
