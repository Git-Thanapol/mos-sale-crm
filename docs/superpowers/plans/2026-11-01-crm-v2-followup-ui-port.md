# CRM V2 Follow-up UI Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "ติดตามลูกค้า" sidebar sub-menu whose pages look the same as the customer's CRM V2 prototype screens, backed by the Lead backend that already exists in this repo's Postgres.

**Architecture:** Presentation-only port. A new Django app `crm/leadui/` renders server-side templates that reuse the prototype's class names and DOM from `src/ui/App.tsx`, styled by the prototype's `styles.css` rules re-scoped under a `.v2` wrapper so they cannot restyle the rest of the site. All data and rules come from the already-built `crm/leads`, `crm/followcycles`, `crm/intake`, `crm/domain` packages; this plan adds no business logic.

**Tech Stack:** Django 5.1, PostgreSQL 16, Django templates, vanilla JS (filter auto-submit), pytest + pytest-django. No React, no Node at runtime.

**Spec:** `$PROTO/src/ui/App.tsx`, `src/ui/styles.css`, `src/ui/permissions.ts`, where `$PROTO` = `C:\Users\Thana\OneDrive\เดสก์ท็อป\customer-crm-v2-codex\customer-crm-v2-codex`; plus `$PROTO\CRM_V2_Developer_Handoff_Server_Data\02_LOCKED_BUSINESS_RULES.md`, `06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md`, `08_SECURITY_AND_AUTH_REQUIREMENTS.md`. The backend this plan sits on was planned in `docs/superpowers/plans/2026-11-01-crm-v2-followup-lead-surface.md` (that file exists only on branch `feat/crm-v2-followup-lead-surface`).

## Global Constraints

- Fidelity rule: same class names, same element nesting, same Thai strings as the prototype source. Do not "improve" markup, spacing, colors or copy. Every deviation is listed in the Rulings table below and nowhere else.
- Prototype shell (`.app-shell`, `aside`, `nav`, `.brand`, `.role-box`, `.topbar`, `.uat-banner`, `main`) is NOT ported; this site's own sidebar and `base.html` stay.
- Every ported CSS rule is prefixed `.v2 `. No bare `body`, `*`, `button`, `input`, `select`, `textarea`, `table`, `th`, `td`, `h1`-`h3`, `aside`, `nav`, `main`, `:root` selector may appear in `v2-followup.css`.
- Locked rules unchanged: D019 (Global Unassigned is pre-Lead intake, never a null-team Lead), D023, D024, D025, ownership policies, missed-channel recomputed from history, last-order-age. Do not edit `crm/domain/`, `crm/leads/services.py`, `crm/intake/services.py`.
- Keyset pagination only: no `COUNT(*)`, no offset, no page numbers. The pager shows "หน้าแรก" (only when a cursor is present) and "หน้าถัดไป" (only when more exist), as in the prototype.
- Hidden menu items are convenience, never security. Every page and POST re-checks authorization with `crm/core/permissions.py` predicates and `.for_user(user)` querysets. Out-of-scope ids return 404, not 403 and not a rendered page.
- Roles: prototype ADMIN = repo `EDITOR`; `TEAM_LEAD` as-is; TELESALE = `พนักงาน`. Repo `ADMIN` stays deliberately weaker (see `docs/DECISIONS.md`).
- Tests run only through Docker: `docker compose exec -T web python -m pytest <path> -q`. Never `docker compose down -v` (the `crm_pgdata` volume holds the real 216k-customer dataset).
- Known-red baseline: 15 failures in `test_import_excel*`, `test_matrix*`, `test_online_orders*`. Every task ends with no failure outside that list.
- Django `{# #}` comments are single-line only; multi-line comments use `{% comment %}`. A leak-guard test exists (Task 3).

## Prototype screen to Django route map (the whole scope)

| Prototype screen (App.tsx function, lines) | Sidebar label | Route name | Path under `/followup/` | Who sees it (prototype `ALLOWED`) |
|---|---|---|---|---|
| `MyWork` 60-92 | งานของฉัน | `leadui:my_work` | `work/` | EDITOR, TEAM_LEAD, พนักงาน |
| `GlobalUnassigned` 93-100 | Global Unassigned | `leadui:global_unassigned` | `global-unassigned/` | EDITOR (`can_resolve_intake`) |
| `ReviewQueue` TEAM 101-108 | Team Unassigned | `leadui:team_unassigned` | `team-unassigned/` | EDITOR, TEAM_LEAD (`can_review_any_lead`) |
| `ReviewQueue` STOPPED 101-108 | STOPPED Review | `leadui:stopped_review` | `stopped/` | EDITOR, TEAM_LEAD (`can_review_any_lead`) |
| `LeadDetail` 122-132 | none (drill-down) | `leadui:lead_detail` | `lead/<int:lead_id>/` | anyone who `can_view_lead` |
| `CustomerDetail` 109-121 | none (drill-down) | `leadui:customer_detail` | `customer/<int:customer_id>/` | customer in `for_user` scope |
| lazy order history | none | `leadui:customer_history` | `customer/<int:customer_id>/history/` | same as customer detail |

Part B (Tasks 10-17) adds the four import screens to the same sub-menu:

| Prototype screen | Sidebar label | Route name | Path under `/followup/` | Who sees it (prototype `ALLOWED`) |
|---|---|---|---|---|
| `ImportScreen` 133-138 | นำเข้าข้อมูล | `leadui:import` | `import/` | EDITOR (Confirm/Commit buttons EDITOR only) |
| `ConflictScreen` 140-145 | แก้ Conflict | `leadui:import_conflicts` | `import/conflicts/` | EDITOR, TEAM_LEAD (own-team, non-IDENTITY only) |
| `ImportHistory` 147-152 | ประวัตินำเข้า | `leadui:import_history` | `import/history/` | EDITOR, TEAM_LEAD (Rollback button EDITOR only) |
| `DecisionCenter` (DecisionCenter.tsx) | Migration Decisions | `leadui:migration_decisions` | `import/decisions/` | EDITOR only |

Reference screenshots of every prototype screen (captured from the running prototype at `http://127.0.0.1:5174/`, role Admin, synthetic data) are committed in `docs/parity/crmv2-ui/proto-*.jpg`: `my-work`, `global-unassigned`, `team-unassigned`, `stopped-review`, `lead-detail`, `customer-detail`, `import`, `import-conflicts`, `import-history`, `migration-decisions`. They are the visual acceptance reference for Task 18.

## Rulings table (the only allowed deviations)

| # | Prototype behavior | Django behavior | Why |
|---|---|---|---|
| R1 | Screens swap client-side with a loading skeleton | Full page loads, no skeleton. Error and empty states use the same `.state-box` markup | Server-rendered |
| R2 | Filters apply on change | Same, via `static/crm/js/v2-filters.js` (`requestSubmit()` on change; 400 ms debounce on the text box). Works without JS via Enter | Same feel |
| R3 | Global Unassigned has two hard-coded buttons (`team-crm`, `team-upsell`) | One button per assignable `Team`, same `.actions` row | Teams are configurable here |
| R4 | "เปลี่ยน caretaker" assigns a hard-coded demo user after `window.confirm` | Opens a `<dialog>` with a caretaker `<select>` and a reason field, same button | A real chooser is required |
| R5 | "นัดพรุ่งนี้" sends `Date.now() + 86 400 000` | Same: server-side `now + 24 h`, shown in Bangkok time | Identical meaning |
| R6 | Sidebar hides unreachable screens | Same: children hidden (not grayed) when the predicate fails | Prototype `navigationFor` |
| R7 | Customer/Lead detail default to `cust-001` when no id | Detail routes need an id and have no menu item | No demo ids |

## Review Focus

1. Tampered or garbage `?cursor=` on every queue page gives the first page, HTTP 200, never 500. Owner: Tasks 4, 5, 6, 8.
2. A พนักงาน opening `/followup/lead/<id>/` or `/followup/customer/<id>/` of another team or person gets 404 with no customer name in the body. Owner: Tasks 7, 8.
3. A customer with zero leads, a lead with zero notes/attempts/appointments, and an empty queue render an empty card or `.state-box`, no exception, no literal "None". Owner: Tasks 4, 7, 8.
4. Blank `customer_name` and blank phone show "ไม่มีเบอร์" and still link; no "None". Owner: Task 4.
5. Nonsense filter combinations (min > max days, negative days, unknown preset, unknown sort) give 200, and the form re-renders the user's raw values so they can fix them. Owner: Task 4.

## File Structure

- Create `crm/leadui/`: `__init__.py`, `apps.py`, `urls.py`, `views.py` (thin: authorize, call selectors, build context), `forms.py` (GET filter parsing), `templatetags/__init__.py`, `templatetags/v2.py`.
- Create `templates/leadui/`: `base_v2.html` (extends `base.html`, loads CSS/JS, opens `<div class="v2">`), `_page_title.html`, `_state.html`, `_pager.html`, `_lead_table.html`, `my_work.html`, `global_unassigned.html`, `review_queue.html`, `lead_detail.html`, `customer_detail.html`, `_history_rows.html`.
- Create `static/crm/css/v2-followup.css`, `static/crm/js/v2-filters.js`.
- Modify `crm/core/nav.py`, `templates/_sidebar.html`, `static/crm/css/components/sidebar.css`, `config/urls.py`, `config/settings/base.py` (INSTALLED_APPS), `crm/leads/selectors.py` (sort option), `crm/leads/views.py` and `crm/leads/urls.py` (`close_cycle`), `docs/DECISIONS.md`.
- Tests: `tests/unit/test_v2_css_scoped.py`, `tests/view/leadui/` (`test_nav.py`, `test_shell.py`, `test_my_work.py`, `test_review_queues.py`, `test_global_unassigned.py`, `test_lead_detail.py`, `test_customer_detail.py`, `test_permission_matrix.py`), `tests/db/test_lead_sort.py`.

---

### Task 0: Remove the inline lead UI from the main follow-up page, re-verify the backend

Ruling (user, after weighing both): work stays on the existing branch `feat/crm-v2-followup-lead-surface` (no new branch from `main`). The backend there already passed two reviews and 760 tests. The only thing to undo is the lead UI that was embedded in the existing follow-up page, because the new sub-menu is the access point and the main page must end up identical to `main`.

**Files:**
- Modify (restore to `main`): `crm/followups/views.py`, `templates/followups/index.html`
- Delete: `templates/followups/_lead_detail.html`, `_lead_filters.html`, `_lead_queues.html`
- Delete or trim tests that exercise the inline UI (listed in step 2)

**Interfaces:**
- Keeps: everything in `crm/leads`, `crm/followcycles`, `crm/intake`, `crm/domain`, `crm/core/*`, accounts/teams/customers changes, including `can_view_followup` admitting `TEAM_LEAD` (a predicate, needed for the sub-menu).
- Produces: `git diff main -- crm/followups templates/followups` is empty.
- Note for later tasks: `crm/followups/views.py` on this branch builds the lead-queue context (`lead_detail`, queues, filters). Task 4-8 views reuse that logic; read it with `git show HEAD:crm/followups/views.py` BEFORE restoring it and keep the helpers you need in `crm/leadui/` (do not leave them in `crm/followups/`).

- [ ] **Step 1: Copy out the reusable view logic.** `git show HEAD:crm/followups/views.py > <scratchpad>/old_followups_views.py` and read it; note the context-building helpers for queues, filters, `lead_detail`, `detail_next`, `assignable_teams`.
- [ ] **Step 2: Restore the main follow-up page.** `git checkout main -- crm/followups/views.py templates/followups/index.html` and `git rm templates/followups/_lead_detail.html templates/followups/_lead_filters.html templates/followups/_lead_queues.html`. Delete `tests/view/test_lead_detail_panel.py` and `tests/view/test_followup_lead_sections.py`. In `tests/view/test_review2_findings.py`, `test_lead_permission_denial.py`, `test_deactivation_reroutes_leads.py`, `test_redirect_safety.py` drop (or rewrite against `leads:*` POST routes) every test whose body requests `reverse("followups:list")` with `lead_id` or `lead_section`; find them with `grep -rln "lead_section\|lead_id" tests/view`. Pure predicate, queryset and POST-route tests stay.
- [ ] **Step 3: Verify the main page is untouched.** `git diff main --stat -- crm/followups templates/followups` prints nothing.
- [ ] **Step 4: Re-check the parity fixtures from the source.** Re-run the prototype's own functions (`node --import tsx` inside `$PROTO`) over the inputs in `tests/fixtures/crmv2/{ownership,missed_channel,last_order_age}.json` and assert each `expected` still equals the freshly executed output (79 cases). Any difference stops the plan.
- [ ] **Step 5: Run the suite.** `docker compose exec -T web python -m pytest -q -p no:randomly --tb=no`. Expected: only the 15 known-red baseline failures; the test count drops by the removed inline-UI tests only.
- [ ] **Step 6: Commit** `refactor: remove the inline lead UI from the main follow-up page`.

### Task 1: Prototype stylesheet, scoped

**Files:**
- Create: `static/crm/css/v2-followup.css`
- Test: `tests/unit/test_v2_css_scoped.py`

**Interfaces:**
- Produces CSS for `.page-title .toolbar .hint .table-shell .chip(.danger/.warn/.ok) .team-badge .metric .state-box(.error/.ok) .skeleton .card-grid .work-card .detail-card .detail-layout .actions .list-button .history-row .note .lead-heading .callout .toast .pager .quick-filter`, plus `.v2 button`, `.v2 button.secondary`, `.v2 button.danger-button`, `.v2 input`, `.v2 select`, `.v2 textarea`.

- [ ] **Step 1: Failing test `test_every_selector_is_scoped_under_v2`:** parse rule preludes with a regex (descend into `@media`, skip `@keyframes` bodies) and assert every comma-separated selector starts with `.v2 ` or equals `.v2`.
- [ ] **Step 2: Failing test `test_no_prototype_shell_rules_leak`:** assert the file text contains none of `.app-shell`, `aside`, `.brand`, `.role-box`, `.topbar`, `.uat-banner`.
- [ ] **Step 3: Failing test `test_prototype_declarations_preserved`:** for a fixed list of 12 (selector, declaration) pairs copied from `styles.css` (for example `.chip.danger` has `background: #fee2e2; color: #a42323`, `.table-shell` has `border-radius: 10px`, `.toolbar` has `padding: 13px`), assert the declaration text is present under the scoped selector.
- [ ] **Step 4: Run, expect FAIL** (`FileNotFoundError`): `docker compose exec -T web python -m pytest tests/unit/test_v2_css_scoped.py -q`.
- [ ] **Step 5: Create the stylesheet** by transcribing the `styles.css` rules for the classes above with the `.v2 ` prefix, dropping the shell rules, keeping the `@media (max-width: 1180px)` rules for `.card-grid` and `.detail-layout` only. Read the whole file with `python -c` and `textwrap`, since its long lines get truncated by `cut`. Use `font-family: inherit` (the host app owns the font stack).
- [ ] **Step 6: Run, expect PASS.**
- [ ] **Step 7: Commit** `feat: scoped port of the CRM V2 prototype stylesheet`

### Task 2: Sidebar sub-menu under ติดตามลูกค้า

**Files:**
- Modify: `crm/core/nav.py`, `templates/_sidebar.html`, `static/crm/css/components/sidebar.css`
- Test: `tests/view/leadui/test_nav.py`

**Interfaces:**
- Consumes: `perm.can_view_followup`, `perm.can_resolve_intake(user)`, `perm.can_review_any_lead(user)`.
- Produces: a `NAV_GROUPS` entry may carry an optional 4th element, a list of `(label, url_name, predicate)` children. `nav_context` returns each item as `{"label", "url_name", "allowed", "children": [{"label", "url_name", "active"}]}`. A child whose predicate fails is omitted (R6). The parent gets `crm-sidebar__item--active` when any child's `url_name` equals `request.resolver_match.view_name`. Children render as `<a class="crm-sidebar__subitem{% if child.active %} crm-sidebar__subitem--active{% endif %}">` inside `<div class="crm-sidebar__sub">`.
- Children, in order: `งานของฉัน` to `leadui:my_work`; `Global Unassigned` to `leadui:global_unassigned` (`can_resolve_intake`); `Team Unassigned` to `leadui:team_unassigned` (`can_review_any_lead`); `STOPPED Review` to `leadui:stopped_review` (`can_review_any_lead`); then (Part B routes, same list in the prototype's order) `นำเข้าข้อมูล` to `leadui:import` (`can_import_v2`), `แก้ Conflict` to `leadui:import_conflicts` (`can_resolve_import_conflicts`), `ประวัตินำเข้า` to `leadui:import_history` (`can_view_import_history`), `Migration Decisions` to `leadui:migration_decisions` (`can_import_v2`). The four predicates are defined in Task 10; until then the children are skipped by the failed-reverse guard.

- [ ] **Step 1: Failing tests** (`RequestFactory` + `nav_context`): `test_editor_sees_four_children`, `test_team_lead_sees_work_team_unassigned_stopped`, `test_telesale_sees_only_my_work`, `test_viewer_sees_no_children_and_parent_disabled`, `test_parent_active_when_child_view_matches`, `test_items_without_children_unchanged`.
- [ ] **Step 2: Run, expect FAIL** (no `children` key).
- [ ] **Step 3: Implement** the optional 4th element in `NAV_GROUPS` and `nav_context`. In the template resolve child URLs with `{% url child.url_name as child_url %}` and skip a child whose reverse fails, so the sidebar still renders before Task 3 mounts the routes.
- [ ] **Step 4: Template and CSS.** Children sit under the parent; `.crm-sidebar__subitem` is indented `var(--space-5)`, 13px, with the same hover and active tokens as `.crm-sidebar__item`.
- [ ] **Step 5: Run, expect PASS**, then `docker compose exec -T web python -m pytest tests -q -k "nav or sidebar"`.
- [ ] **Step 6: Commit** `feat: sidebar sub-menu under ติดตามลูกค้า with per-role visibility`

### Task 3: `leadui` app skeleton and shared partials

**Files:**
- Create: `crm/leadui/{__init__,apps,urls,views,forms}.py`, `crm/leadui/templatetags/{__init__,v2}.py`, `templates/leadui/{base_v2,_page_title,_state,_pager}.html`, `static/crm/js/v2-filters.js`
- Modify: `config/settings/base.py` (INSTALLED_APPS), `config/urls.py` (`path("followup/", include("crm.leadui.urls"))` placed BEFORE the existing `followup/` include)
- Test: `tests/view/leadui/test_shell.py`

**Interfaces:**
- Produces tag `{% v2_chip value %}` giving `<span class="chip {tone}">{value}</span>`, where tone replicates `StatusChip` (App.tsx:34-37): `danger` if value matches `OVERDUE|STOPPED|FAILED|BLOCKING`, else `warn` if `TODAY|PAUSED|WARNING`, else `ok` if `COMMITTED|ASSIGNED|DONE`, else `neutral`.
- Produces filter `{{ dt|bangkok }}` giving `dd/mm/yyyy HH:mm` in `Asia/Bangkok`, and `—` for `None`.
- Produces `_page_title.html` (`title`, `subtitle`) giving `<header class="page-title"><div><h1>..</h1><p>..</p></div></header>`.
- Produces `_state.html` (`kind` = `empty` or `error`, `message`) giving `<div class="state-box[ error]">`; default empty text `ยังไม่มีรายการในคิวนี้`.
- Produces `_pager.html` (`first_url`, `next_url`) giving `<div class="pager">` with links styled as `button.secondary` reading `หน้าแรก` / `หน้าถัดไป`, each rendered only when its URL is set.
- Produces `base_v2.html`: `{% extends "base.html" %}`; `extra_head` links `v2-followup.css`; content is wrapped in `<div class="v2">`; Django `messages` render as `<div class="toast">` at the top.
- Produces `v2-filters.js`: for every `form[data-v2-filters]`, `change` on select, checkbox and number inputs calls `form.requestSubmit()`; `input` on `input[type=search]` calls it debounced 400 ms.
- URL names are the seven in the route map; views not yet built raise `Http404`, except `my_work`, which renders the empty shell so Task 2's links resolve.

- [ ] **Step 1: Failing tests:** `test_chip_tone_matches_prototype` parametrised over `("OVERDUE","danger")`, `("STOPPED","danger")`, `("APPOINTMENT_TODAY","warn")`, `("PAUSED","warn")`, `("COMMITTED","ok")`, `("ASSIGNED","ok")`, `("DONE","ok")`, `("FOLLOWING","neutral")`, `("","neutral")`; `test_no_template_comment_markers_leak_into_the_page` (asserts `{#`, `#}`, `{% comment %}`, `{% endcomment %}` are absent; later tasks add their routes to its parametrisation); `test_page_requires_login` (anonymous gets a 302 to login); `test_base_v2_wraps_content_in_v2_div`; `test_bangkok_filter_formats_and_handles_none`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the app, tags, partials, JS and URL include.
- [ ] **Step 4: Run, expect PASS**, and `docker compose exec -T web python manage.py check`.
- [ ] **Step 5: Commit** `feat: add leadui app shell, chip and pager partials, filter auto-submit`

### Task 4: งานของฉัน (My Work)

**Files:**
- Modify: `crm/leadui/views.py` (`my_work`), `crm/leads/selectors.py` (sort), `crm/leadui/forms.py`
- Create: `templates/leadui/my_work.html`, `templates/leadui/_lead_table.html`
- Test: `tests/view/leadui/test_my_work.py`, `tests/db/test_lead_sort.py`

**Interfaces:**
- Consumes: `selectors.my_work_queue(user, *, after_cursor, limit)`, `LeadQueueFilters.from_query`, `presenters.decorate(lead, *, lock_active)`, `KeysetPage(items, next_cursor, has_more)`.
- Produces: `LeadQueueFilters.sort: str` (`WORK_PRIORITY` default, `OLDEST_LAST_ORDER`, `NEWEST_LAST_ORDER`), read from query key `age_sort`; an unknown value falls back to the default. `_lead_table.html` takes `rows` (decorated leads) and `link_customer: bool`.
- Copy (verbatim): title `งานของฉัน`, subtitle `เรียงงานเร่งด่วน: เกินกำหนด → นัดวันนี้ → ติดตาม → Lead ใหม่`.
- Toolbar, in prototype order (App.tsx:60-92): search input (placeholder `เบอร์โทร / ชื่อ / ชื่อมาตรฐาน / Order ID`, name `lead_keyword`); missed-channel select (`ทุกกลุ่มออเดอร์ตกหล่น`, `0`, `1`, `2`, `3+`); age-preset select (`ทุกช่วงออเดอร์ล่าสุด` plus `presenters.age_preset_options()`); number `ขั้นต่ำ (วัน)`; number `สูงสุด (วัน)`; sort select (`ลำดับงานปกติ`, `ออเดอร์เก่าสุดก่อน`, `ออเดอร์ใหม่สุดก่อน`); appointment select (`ทุกสถานะนัดหมาย`, `มีนัดหมายทำงานอยู่`, `ไม่มีนัดหมายทำงานอยู่`); follow-up select (`ทุกรอบติดตาม`, `มีรอบติดตาม`, `ไม่มีรอบติดตาม`); checkbox `ไม่รวม STOPPED`; checkbox `หลีดที่ควรตรวจสอบการจัดสรรใหม่` only when `can_review_any_lead`; then `<span class="hint">เกณฑ์ทดสอบ Local: {N} วัน · เป็นคิวตรวจสอบเท่านั้น</span>` with N from `REALLOCATION_REVIEW_DEFAULT_DAYS`, not a literal.
- Table (App.tsx:48-58), headers verbatim: ลูกค้า · Team / ผู้ดูแล · สถานะ · นัดหมาย · ระยะห่างจากออเดอร์ล่าสุด · ออเดอร์ตกหล่น · action. Cells: customer name as a link to `leadui:customer_detail` over `<small>` phone or `ไม่มีเบอร์`; `<span class="team-badge">` team over `<small>` caretaker or `ยังไม่มอบหมาย`; `v2_chip` lifecycle, one chip per computed status, plus an `ACTIVE_FOLLOWUP` chip when a cycle is active; next appointment through `bangkok`; age date over `<small>` age label; `<span class="metric">` count over `<small>กลุ่ม {group}</small>`; a `button`-styled link `เปิด Lead` to `leadui:lead_detail`.

- [ ] **Step 1: Failing DB tests:** `test_sort_oldest_last_order_first`, `test_sort_newest_last_order_first`, `test_unknown_sort_falls_back_to_work_priority`, `test_keyset_cursor_is_stable_under_each_sort` (pages 1 and 2 of 30 leads under each sort give 30 distinct ids, in order).
- [ ] **Step 2: Failing view tests:** `test_page_has_every_toolbar_control_in_prototype_order`, `test_reallocation_checkbox_hidden_for_telesale`, `test_hint_uses_configured_threshold`, `test_table_headers_match_prototype`, `test_blank_name_and_phone_render_no_phone_text_not_None` (RF 4), `test_garbage_cursor_returns_first_page_200` (RF 1), `test_nonsense_filters_return_200_and_echo_values` (RF 5: `age_min_days=10&age_max_days=5`, `age_min_days=-1`, `age_preset=foo`, `age_sort=zzz`, and `value="10"` still in the body), `test_first_and_next_pager_links`, `test_telesale_sees_only_own_leads`, `test_empty_queue_shows_state_box` (RF 3).
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement** `LeadQueueFilters.sort` and the ordering in `selectors._lead_queue_queryset` / `_seek` (stable keyset: sort key plus `id` tiebreak, reusing `encode_cursor` / `decode_cursor`), the view (`my_work_queue` pins the caretaker server-side, so a `caretaker=` query value is ignored) and the templates.
- [ ] **Step 5: Run, expect PASS**, then `docker compose exec -T web python -m pytest tests/view/leadui tests/db -q` and compare with the baseline.
- [ ] **Step 6: Commit** `feat: add งานของฉัน page matching the prototype`

### Task 5: Team Unassigned and STOPPED Review

**Files:**
- Modify: `crm/leadui/views.py`
- Create: `templates/leadui/review_queue.html`
- Test: `tests/view/leadui/test_review_queues.py`

**Interfaces:**
- Consumes: `selectors.team_unassigned_queue(user, *, team=None, after_cursor, limit)`, `selectors.stopped_review_queue(user, *, after_cursor, limit)`, `_lead_table.html` with `link_customer=False` (the prototype passes a no-op `onCustomer`).
- Copy (verbatim): Team Unassigned: title `Team Unassigned`, subtitle `Lead มี Team แล้ว แต่ยังไม่มี caretaker`, toolbar with one age-preset select (`ทุกช่วงออเดอร์ล่าสุด`). STOPPED Review: title `STOPPED Review`, subtitle `STOPPED เป็น lifecycle state แยกจาก Unassigned`, no toolbar.
- A user failing `can_review_any_lead` gets 404 on both (route hidden, R6).

- [ ] **Step 1: Failing tests:** `test_team_lead_sees_only_scoped_team_leads` (two teams, scope `["A"]`), `test_stopped_queue_lists_only_stopped_and_never_routes_on_unassigned`, `test_telesale_gets_404_on_both`, `test_viewer_gets_404_on_both`, `test_age_preset_filters_team_unassigned`, `test_garbage_cursor_returns_first_page_200` (RF 1), `test_titles_and_subtitles_verbatim`.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add Team Unassigned and STOPPED Review pages`

### Task 6: Global Unassigned

**Files:**
- Modify: `crm/leadui/views.py`
- Create: `templates/leadui/global_unassigned.html`
- Test: `tests/view/leadui/test_global_unassigned.py`

**Interfaces:**
- Consumes: `selectors.global_unassigned_queue(user, *, after_cursor, limit)` (keyed `(created_at, id)`), `crm.teams.models.Team`, existing POST `leads:resolve_intake` (path `intake_id`; form fields `team`, `next`).
- Copy (verbatim): title `Global Unassigned`, subtitle `Customer มีอยู่แล้ว แต่ยังไม่รู้ Team — ยังไม่มี Lead`, and `<div class="callout">D019: เลือก Team จริงเท่านั้น ระบบจึงสร้าง Lead; ไม่ใช้ Team ปลอมและไม่สร้าง Lead ที่ teamId ว่าง</div>`.
- Card (`.card-grid > article.work-card`): `v2_chip` of the intake status, `<h3>` customer display name, `<p>Order: {source_order_id or "—"}</p>`, `<small>{source_ref}</small>`, and `.actions` holding one `<form method="post">` + `<button>` per assignable team labelled `ส่งเข้า {team.display_name}` (R3).

- [ ] **Step 1: Failing tests:** `test_only_editor_may_open` (TEAM_LEAD, พนักงาน, VIEWER get 404), `test_one_button_per_assignable_team`, `test_posting_a_team_creates_a_lead_and_redirects_back_with_toast`, `test_no_lead_with_null_team_is_ever_created` (D019), `test_callout_text_verbatim`, `test_garbage_cursor_returns_first_page_200` (RF 1), `test_empty_state` (RF 3).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add Global Unassigned page with the D019 callout`

### Task 7: Lead detail and its actions

**Files:**
- Modify: `crm/leadui/views.py` (`lead_detail`), `crm/leads/views.py` and `crm/leads/urls.py` (`close_cycle`)
- Create: `templates/leadui/lead_detail.html`
- Test: `tests/view/leadui/test_lead_detail.py`

**Interfaces:**
- Consumes: `can_view_lead`, `can_edit_lead`, `can_reassign_caretaker`, `can_review_stopped`; services `end_cycle(*, actor, cycle, reason)`, `reassign_caretaker`, `stop_lead`, `reactivate_lead`; existing POST routes `leads:log_attempt`, `leads:set_appointment`, `leads:reassign`, `leads:stop`, `leads:reactivate`, `leads:add_note`.
- Produces: `leads:close_cycle`, POST `<int:lead_id>/close-cycle/`, calling `end_cycle(actor=request.user, cycle=lead.current_cycle, reason="confirmed in UI")`; 400 when no active cycle, 403 when `not can_edit_lead`, redirect through `_back`.
- Lookup: `Lead.objects.for_user(request.user).filter(pk=lead_id)`, else 404.
- Layout (App.tsx:122-132): `PageTitle` (`รายละเอียด Lead`, `Notes / Contact Attempts / Appointments เขียนผ่าน server mutation`), then `.detail-layout` with four `.detail-card` sections. (1) `.lead-heading` (name, `{team} · {caretaker or ยังไม่มอบหมาย}`, lifecycle chip), `<dl>` เริ่มถือ Lead / รอบติดตาม / นัดหมายถัดไป / ออเดอร์ตกหล่น `{n} (กลุ่ม {g})`, then `.actions`. (2) `เพิ่มโน้ต`: form with `textarea maxlength=2000` (placeholder `โน้ตอยู่กับ Lead / Follow-up Cycle`) and `<button>บันทึกโน้ต</button>`, then `<h3>โน้ตล่าสุด</h3>` with `.note` rows. (3) `การติดต่อล่าสุด` as `.history-row` (chip result, time, note). (4) `นัดหมาย` as `.history-row` (chip status, time).
- Buttons in `.actions`, in this order: `บันทึกติดต่อไม่ได้` (POST result `NO_ANSWER`, note `บันทึกจากหน้าจอ`); `button.secondary` `นัดพรุ่งนี้` (R5); `button.secondary` `เปลี่ยน caretaker` only when `can_reassign_caretaker` (R4); `button.secondary` `ปิดรอบติดตาม` only when a cycle is active; `button.danger-button` `STOP` only when not STOPPED and the user is a reviewer (not พนักงาน); `Reactivate` only when STOPPED and reviewer. Edit buttons and the note form render only when `can_edit_lead`.

- [ ] **Step 1: Failing tests:** `test_telesale_caretaker_sees_edit_buttons_but_not_caretaker_or_stop`, `test_editor_sees_all_buttons_with_active_cycle`, `test_stopped_lead_shows_reactivate_not_stop`, `test_out_of_scope_lead_is_404_and_leaks_no_name` (RF 2), `test_lead_with_no_notes_attempts_appointments_renders` (RF 3), `test_close_cycle_ends_cycle_and_releases_lock`, `test_close_cycle_403_for_viewer`, `test_close_cycle_400_without_active_cycle`, `test_no_active_cycle_hides_close_button`, `test_note_post_returns_to_detail_with_toast`, `test_tomorrow_button_creates_appointment_about_24h_ahead`, `test_headings_and_dl_labels_verbatim`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the view, template, `close_cycle`, and the `<dialog>` for R4.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add Lead detail page and close-cycle action`

### Task 8: Customer detail with lazy, audited order history

**Files:**
- Modify: `crm/leadui/views.py` (`customer_detail`, `customer_history`)
- Create: `templates/leadui/customer_detail.html`, `templates/leadui/_history_rows.html`
- Test: `tests/view/leadui/test_customer_detail.py`

**Interfaces:**
- Consumes: `Customer.objects.for_user(user)`, `Lead.objects.for_user(user).filter(customer=...)`, `LeadAuditEvent` action `CUSTOMER_HISTORY_VIEW`, `crm.core.pagination.KeysetPage`.
- Layout (App.tsx:109-121): `PageTitle` (`ข้อมูลลูกค้า`, `สรุปก่อน โหลดประวัติออเดอร์เมื่อผู้ใช้กดเท่านั้น`), `.detail-layout` with card 1 (`<h2>` name, phone or `ไม่มีเบอร์หลัก`, `<dl>` ออเดอร์ล่าสุด (last order number or `—`) / จำนวนออเดอร์ / ยอดซื้อรวม `฿{n:,}`, `<button>ดูประวัติเก่า</button>`) and card 2 (`<h2>Leads ตาม Team</h2>` with one `button.list-button` link per visible lead: team plus chip). History is NOT rendered on first load.
- `leadui:customer_history` GET (`?cursor=`) returns the `_history_rows.html` fragment: 25 `.history-row` per page (order no, date Bangkok, channel, chip status, `฿{total}`) and a `โหลดเพิ่ม` `button.secondary` when more exist. The first request (no cursor) writes one `CUSTOMER_HISTORY_VIEW` audit event; cursor requests write none. `ดูประวัติเก่า` fetches the fragment into a `section.detail-card.history` via a small handler added to `v2-filters.js`.
- A customer outside scope gets 404.

- [ ] **Step 1: Failing tests:** `test_first_load_has_no_order_history_and_no_audit_event`, `test_history_endpoint_returns_rows_and_audits_once`, `test_history_next_page_does_not_audit_again`, `test_totals_exclude_invalid_orders` (CANCELLED and COD_RETURNED are not in `ยอดซื้อรวม`), `test_customer_with_no_leads_renders_empty_card` (RF 3), `test_out_of_scope_customer_is_404_and_leaks_no_name` (RF 2), `test_history_garbage_cursor_returns_first_page` (RF 1), `test_lead_links_only_for_visible_leads`.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add Customer detail page with lazy audited order history`

### Task 9: Permission matrix over HTTP

**Files:**
- Test: `tests/view/leadui/test_permission_matrix.py`

- [ ] **Step 1: Write one parametrised test** over `{EDITOR, TEAM_LEAD (scope ["A"]), พนักงาน, ADMIN, VIEWER}` crossed with the seven routes (data: one lead and customer in team A, one in team B, and a lead whose caretaker is the พนักงาน). Assert the exact expected status from the route-map table: 200 where allowed, 404 where hidden or out of scope, 302 for anonymous. Also assert the sidebar HTML contains exactly the expected sub-menu `href`s per role (R6).
- [ ] **Step 2: Run.** Expected PASS on the first run if Tasks 2-8 are correct. Any FAIL is a defect in the owning task: fix it there, with that task's own failing test first.
- [ ] **Step 3: Run the whole suite**; expect only the 15 baseline failures. **Commit** `test: pin the lead UI permission matrix over HTTP`.

## Part B: import, conflicts, history, migration decisions

Source of truth for Part B: `$PROTO/src/import/*.ts` (3.8k lines: `state-machine`, `confirm`, `tele-date`, `preview`, `plan`, `mapping`, `status-map`, `commit`, `journal`, `rollback`, `conflict-resolution`, `hashing`, `recompute`, `store`), `src/migration/{decisions,decision-service}.ts`, `src/ui/DecisionCenter.tsx`, `07_MIGRATION_AND_DATA_REQUIREMENTS.md`, `08_SECURITY_AND_AUTH_REQUIREMENTS.md`. Method is the same as the backend port: pure rules go to `crm/domain/` with parity fixtures produced by executing the prototype functions (`node --import tsx` inside `$PROTO`); the Firebase-bound parts (`store.ts`, `raw-storage.ts`) are replaced by Django models and services in a new app `crm/importv2/`. The existing `imports` app (legacy Excel upload at `imports:upload`) is not touched (R8). Quota states (`PAUSED_QUOTA`) stay in the status enum because the UI lists them, but nothing in Postgres produces them.

Additional ruling: **R8** the prototype has one import path; this site already has a legacy Excel import. Both coexist until cutover; the new pages never write through the legacy importer.

### Task 10: Import domain rules, parity fixtures, permission predicates

**Files:**
- Create: `crm/domain/import_state.py`, `crm/domain/import_confirm.py`, `crm/domain/tele_date.py`, `crm/domain/row_evaluation.py`, `crm/domain/conflict_scope.py`, `scripts/crmv2_fixtures/gen_import_fixtures.mts`, `tests/fixtures/crmv2/{import_state,import_confirm,tele_date,row_evaluation,conflict_scope}.json`, `tests/unit/test_import_*_parity.py`
- Modify: `crm/core/permissions.py`

**Interfaces:**
- Produces (names mirror the TS exports): `IMPORT_TRANSITIONS: dict[str, tuple[str, ...]]`, `can_transition(frm, to) -> bool`, `apply_transition(...)`, `is_terminal(status)`, `can_retry_commit(status)`, `needs_rollback(status)`; `evaluate_confirm(input) -> ConfirmDecision`, `compute_preview_hash(input) -> str`, `source_requires_batch_date(source_kind) -> bool`, `IMPORT_CONFIRM_ROLES = frozenset({"EDITOR"})` (prototype ADMIN); `parse_filename_date`, `propose_batch_business_date`, `confirm_batch_business_date`, `normalize_day_key`; `evaluate_row(facts, context) -> RowEvaluation`, `aggregate_preview(evaluations, new_customers) -> PreviewAggregate`; `assert_resolver_scope(actor, conflict)`, `conflict_version_hash(conflict) -> str`.
- Predicates: `can_import_v2(user)` = repo `EDITOR`; `can_resolve_import_conflicts(user)` = `EDITOR` or `TEAM_LEAD`; `can_view_import_history(user)` = `EDITOR` or `TEAM_LEAD`.
- The purity guard `tests/unit/test_domain_purity.py` covers the new modules (no Django import).

- [ ] **Step 1: Generate fixtures** by executing the prototype: the `.mts` script imports the TS functions, runs them over a hand-written input list (every state pair; confirm blockers; filename dates such as `crm_28102569.xlsx`, `2026-10-28`, `28-10-26`, no date; at least 25 `evaluateRow` rows covering NEW_CUSTOMER, NEW_ORDER, UPDATE, CONFLICT and each conflict kind; scope cases for ADMIN, TEAM_LEAD own-team, TEAM_LEAD multi-team, IDENTITY kind) and writes `{input, expected}` per case. Run it from `$PROTO`; commit the JSON.
- [ ] **Step 2: Failing parity tests**, one per module, loading the JSON and asserting equality of the Python result with `expected`. Role strings in inputs are mapped ADMIN to `EDITOR`, TELESALE to `พนักงาน` by one helper, not per test.
- [ ] **Step 3: Failing predicate tests** `test_import_predicates_by_role` over EDITOR, TEAM_LEAD, พนักงาน, ADMIN, VIEWER.
- [ ] **Step 4: Run, expect FAIL. Step 5: Port** the modules by transcription (same branching, same constants such as the 100/200 chunk sizes, no new behavior). **Step 6: Run, expect PASS.**
- [ ] **Step 7: Commit** `feat: port CRM V2 import state machine, confirm, batch-date and row rules`

### Task 11: Import models

**Files:**
- Create: `crm/importv2/{__init__,apps,models}.py`, `crm/importv2/migrations/0001_initial.py`
- Modify: `config/settings/base.py`
- Test: `tests/db/test_importv2_models.py`

**Interfaces:**
- Produces models: `ImportBatch(batch_id uuid pk-str, file_name, source_kind, status, row_count, preview_hash, batch_business_date nullable date, raw_sha256, uploaded_by, team nullable, created_at, committed_at)`; `ImportRowPlan(batch FK, row_no, facts JSONB, classification, conflict_kind blank, target_customer nullable)`; `ImportConflict(conflict_id, batch FK, kind, severity, summary, team nullable, source_row_ref, options JSONB, status OPEN/RESOLVED, resolution blank, version_hash, resolved_by)`; `ImportJournalEntry(batch FK, attempt int, seq int, entity, entity_id, before JSONB null, after JSONB, unique(batch, attempt, seq))`; `ImportOperation(batch FK, operation_key, action, result JSONB, unique(batch, operation_key))` for idempotent replays. Status choices come from `IMPORT_TRANSITIONS` keys.

- [ ] **Step 1: Failing tests:** `test_status_choices_equal_the_state_machine_keys`, `test_operation_key_is_unique_per_batch`, `test_journal_sequence_is_unique_per_attempt`, `test_batch_defaults_to_the_first_state`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement + `makemigrations importv2`. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add import batch, plan, conflict, journal and operation models`

### Task 12: Upload, validate, preview (no business write)

**Files:**
- Create: `crm/importv2/services/{upload,mapping,status_map,preview}.py`
- Test: `tests/db/test_importv2_preview.py`, `tests/unit/test_importv2_mapping.py`

**Interfaces:**
- Consumes: Task 10 `evaluate_row`, `aggregate_preview`, `compute_preview_hash`; Task 11 models.
- Produces: `create_batch(*, actor, uploaded_file, source_kind) -> ImportBatch` (validates by file signature, size cap and parseability server-side, never the extension or browser MIME, per `08_SECURITY`; stores sha256, never trusts client row counts); `map_row(raw: dict, mapping_id="crm-customers@1") -> RowFacts` (port of `mapping.ts`); `map_status(raw) -> StatusAxes` (port of `status-map.ts`); `build_preview(*, actor, batch) -> PreviewAggregate` (fills `ImportRowPlan` and `ImportConflict`, computes `preview_hash`, moves status UPLOADED to VALIDATED to PREVIEW or AWAITING_RESOLUTION through `apply_transition`).

- [ ] **Step 1: Failing tests:** `test_renamed_exe_with_xlsx_extension_is_rejected`, `test_oversize_file_rejected`, `test_preview_writes_no_customer_order_or_lead_rows` (row counts of those tables unchanged), `test_preview_counts_match_aggregate` (new customers, new orders, updates, conflicts), `test_unknown_status_value_is_a_conflict_not_a_guess`, `test_preview_hash_changes_when_a_row_changes`, `test_tele_source_without_batch_date_blocks_confirm_readiness`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add import upload, validation and no-write preview`

### Task 13: Confirm and chunked, idempotent commit

**Files:**
- Create: `crm/importv2/services/{confirm,commit}.py`
- Test: `tests/db/test_importv2_commit.py`

**Interfaces:**
- Consumes: `evaluate_confirm`, `assert_transition`, existing order and customer services (so `sync_leads_for_order` keeps Leads current) and `IntakeItem` upsert for D024.
- Produces: `confirm_batch(*, actor, batch, preview_hash, batch_business_date=None, operation_key) -> ImportBatch` (EDITOR only; blockers from `evaluate_confirm`; raises `ConfirmError` with the blocker code); `commit_batch(*, actor, batch, operation_key) -> CommitResult` (chunks of `DEFAULT_CHUNK_SIZE` = 100, each chunk one transaction writing the business rows and one `ImportJournalEntry` per write; raises `StalePreviewError` when the stored hash no longer matches the recomputed one; replaying the same `operation_key` returns the stored result and writes nothing).

- [ ] **Step 1: Failing tests:** `test_only_editor_may_confirm`, `test_confirm_blocked_while_conflicts_open`, `test_confirm_requires_batch_date_for_tele`, `test_commit_writes_customers_orders_and_updates_leads`, `test_commit_replay_with_same_operation_key_creates_no_duplicates`, `test_commit_is_chunked_in_100s` (250 rows give 3 transactions), `test_stale_preview_aborts_before_any_write`, `test_failure_in_chunk_two_leaves_batch_failed_retryable_and_journal_complete_for_chunk_one`, `test_commit_respects_d024_intake_identity`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add import confirm and chunked idempotent commit`

### Task 14: Rollback from the journal

**Files:**
- Create: `crm/importv2/services/rollback.py`
- Test: `tests/db/test_importv2_rollback.py`

**Interfaces:**
- Produces: `rollback_batch(*, actor, batch, reason, operation_key) -> RollbackOutcome`. EDITOR only; allowed from `COMMITTED`, `PAUSED_QUOTA`, `FAILED_RETRYABLE` (`needs_rollback`); replays journal entries in reverse (`before is None` deletes the created row, otherwise restores `before`), in chunks of `DEFAULT_ROLLBACK_CHUNK_SIZE` = 100, then recomputes affected Leads (`recompute.py` port) and moves the batch to its rolled-back terminal state.

- [ ] **Step 1: Failing tests:** `test_rollback_removes_created_rows_and_restores_updated_ones`, `test_rollback_recomputes_lead_rollups_and_missed_channel`, `test_rollback_twice_is_a_noop`, `test_team_lead_cannot_rollback_403`, `test_rollback_refused_from_preview_status`, `test_rollback_survives_a_mid_run_failure_and_resumes`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add journal-based import rollback`

### Task 15: Conflict resolution

**Files:**
- Create: `crm/importv2/services/conflicts.py`
- Test: `tests/db/test_importv2_conflicts.py`

**Interfaces:**
- Consumes: `assert_resolver_scope`, `conflict_version_hash`, `next_resolution_preview_hash`.
- Produces: `list_conflicts(user, *, batch_id, after_cursor, limit=25) -> KeysetPage` (scoped: EDITOR all, TEAM_LEAD only own-team conflicts, never kind `IDENTITY`, never multi-team); `resolve_conflict(*, actor, conflict, resolution, operation_key) -> ImportConflict` (checks `resolution in conflict.options`, scope, version hash; refreshes the batch `preview_hash`; moves AWAITING_RESOLUTION to PREVIEW when none remain open).

- [ ] **Step 1: Failing tests:** `test_team_lead_sees_only_own_team_conflicts`, `test_team_lead_cannot_resolve_identity_conflict_403`, `test_team_lead_cannot_resolve_multi_team_conflict_403`, `test_unknown_resolution_option_is_400`, `test_resolve_changes_preview_hash`, `test_last_resolution_returns_batch_to_preview`, `test_resolving_twice_with_the_same_key_is_idempotent`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add scoped import conflict resolution`

### Task 16: Pages นำเข้าข้อมูล, แก้ Conflict, ประวัตินำเข้า

**Files:**
- Modify: `crm/leadui/views.py`, `crm/leadui/urls.py`
- Create: `templates/leadui/{import,import_conflicts,import_history}.html`
- Test: `tests/view/leadui/test_import_pages.py`

**Interfaces:**
- Consumes: Task 12-15 services; `_page_title`, `_state`, `_pager`, `v2_chip`.
- `import/` (App.tsx:133-138): title `นำเข้าข้อมูล`, subtitle `ไม่มี business write ก่อน Confirm; Batch Business Date ต้องยืนยันสำหรับ Tele`; `.stepper` with six `.step` (`Upload`, `Validate`, `Preview`, `Resolve`, `Confirm`, `Commit`) numbered 1-6; `section.detail-card` with `.lead-heading` (file name, `{mapping} · {n} rows · {note}`, status chip), file input (`accept=".xlsx,.csv"`, label `เลือกไฟล์ fixture (.xlsx / .csv)` becomes `เลือกไฟล์นำเข้า (.xlsx / .csv)`, R9), `.metric-grid` with four `div > strong + span` (ลูกค้าใหม่, ออเดอร์ใหม่, อัปเดต, Conflict) from the real preview, `Batch Business Date (Asia/Bangkok)` date input, `.actions` with `สร้าง Preview` and, EDITOR only, `Confirm` and `Commit` (each POST with a fresh `operation_key` hidden field).
- `import/conflicts/` (App.tsx:144): title `Import Conflict Resolution`, subtitle `Team Lead เห็นเฉพาะ own-Team; Identity และ multi-Team เป็น Admin เท่านั้น`; `.card-grid > article.work-card`: kind chip + severity chip, `<h3>` summary, `<p>Team: {team or GLOBAL}</p>`, `<small>` source row ref, `.actions` one `<button>` per option.
- `import/history/` (App.tsx:151): title `ประวัตินำเข้า`, subtitle `สถานะจริงเท่านั้น — แสดงสำเร็จเมื่อ COMMITTED`; `.table-shell > table` headers Batch · ไฟล์ · สถานะ · จำนวน · (blank); `button.danger-button` `Rollback` only for EDITOR and status in `COMMITTED`, `PAUSED_QUOTA`, `FAILED_RETRYABLE`, with a `<dialog>` confirm (`Rollback {batch_id}?`) in place of `window.confirm`.
- Ruling R9: the prototype's fixture file picker label becomes `เลือกไฟล์นำเข้า (.xlsx / .csv)` and the card shows the actually uploaded file, never the hard-coded `synthetic-crm.xlsx`.
- CSS additions to `v2-followup.css` (test in Task 1 style): `.stepper`, `.step`, `.step span`, `.metric-grid`, `.metric-grid > div` copied from `styles.css`.

- [ ] **Step 1: Failing tests:** `test_import_page_shows_six_steps_and_metric_grid`, `test_confirm_and_commit_buttons_editor_only`, `test_upload_post_creates_batch_and_shows_preview_counts`, `test_tele_batch_without_date_shows_a_blocker_message`, `test_conflicts_page_cards_and_option_buttons`, `test_team_lead_gets_no_identity_conflict_card`, `test_history_table_headers_and_rollback_visibility`, `test_rollback_post_403_for_team_lead`, `test_garbage_cursor_returns_first_page_200` (all three), `test_titles_and_subtitles_verbatim`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: add import, conflict and import-history pages`

### Task 17: Migration Decisions (service, loader, page) and Part B permission matrix

**Files:**
- Create: `crm/importv2/decisions.py`, `crm/importv2/management/commands/load_migration_decisions.py`, `templates/leadui/migration_decisions.html`, `templates/leadui/_decision_evidence.html`
- Modify: `crm/importv2/models.py` (+ migration), `crm/leadui/views.py`
- Test: `tests/db/test_migration_decisions.py`, `tests/view/leadui/test_migration_decisions_page.py`, extend `tests/view/leadui/test_permission_matrix.py`

**Interfaces:**
- Model `MigrationDecision(decision_id, category in STAFF_ALIAS/IDENTITY_REVIEW/UNKNOWN_STATUS/NULL_DATE_ORDER, status OPEN/DEFERRED/RESOLVED, reason_code, revision int, evidence JSONB, evidence_hash, resolution JSONB null, resolved_by, resolved_at)`.
- Port of `decisions.ts` / `decision-service.ts`: `list_decisions(user, *, category, status, reason_code, after_cursor, limit=25)`, `summary(user) -> dict[category, unresolved_count]`, `resolve_decision(*, actor, decision_id, revision, evidence_hash, action, payload, reason)` (reason at least 3 chars; allowed actions per category from the source; `MATCH_EXISTING` only with an exact-phone candidate from the evidence; stale `revision` or `evidence_hash` raises `CONCURRENT_MODIFICATION`), `reopen_decision(*, actor, decision_id, revision, reason)`. Every call re-checks `can_import_v2`.
- `load_migration_decisions --from <json>` loads the decision rows produced by the run-once HX63 planner container (`docker/hx63`); the command is idempotent on `decision_id` and refuses to overwrite a RESOLVED row.
- Page `import/decisions/` (DecisionCenter.tsx): `.decision-center` wrapper; title `Migration Decisions`, subtitle `ตัดสินโดย Admin เท่านั้น · ทุกการตัดสินมีเหตุผลและประวัติ · ไม่มีการจับคู่อัตโนมัติ`; four category tabs with unresolved counts (`ชื่อพนักงานเดิม`, `ตรวจตัวตนลูกค้า`, `สถานะที่ไม่รู้จัก`, `ออเดอร์ไม่มีวันที่`); status and reason filters; `article.decision-card` with chips, evidence block per category (phones masked with `mask_phone`: all but the last 4 characters replaced by `*`), and the action form with its field set per action; RESOLVED rows show the resolution and an `เปิดใหม่` button. CSS: the `.decision-center`, `.decision-*`, `.evidence*` rules from `styles.css` lines 24-48, scoped under `.v2`.
- Action labels (verbatim from `ACTION_LABELS`): `MAP_TO_USER` ผูกกับผู้ใช้ V2; `HISTORICAL_ONLY` เก็บเป็นประวัติเท่านั้น; `DEFER` เลื่อนการตัดสิน; `MATCH_EXISTING` รวมกับลูกค้าที่มีอยู่; `CREATE_NEW_CUSTOMER` สร้างลูกค้าใหม่ 1 ราย; `KEEP_SEPARATE` แยกตามชื่อ (ไม่รวม); `MAP_TO_STATUS` กำหนดสถานะมาตรฐาน; `ARCHIVE_ONLY` เก็บเป็นประวัติ (ค่าเริ่มต้นเดิม); `SET_AUTHORITATIVE_DATE` ใส่วันที่ที่ยืนยันแล้ว; `KEEP_BLOCKED` คงสถานะถูกกัน.

- [ ] **Step 1: Failing DB tests:** `test_only_editor_can_resolve`, `test_resolve_needs_reason_of_3_chars`, `test_stale_revision_raises_concurrent_modification`, `test_stale_evidence_hash_raises_concurrent_modification`, `test_match_existing_without_exact_candidate_is_refused`, `test_resolve_then_reopen_bumps_revision_and_keeps_history`, `test_loader_is_idempotent_and_never_overwrites_resolved`, `test_summary_counts_unresolved_per_category`.
- [ ] **Step 2: Failing view tests:** `test_page_has_four_tabs_with_counts`, `test_phone_is_masked_even_for_editor`, `test_non_editor_gets_404`, `test_resolve_post_with_stale_revision_shows_reload_message` (`ข้อมูลเปลี่ยนไปแล้ว — โหลดใหม่ก่อนตัดสิน`), `test_garbage_cursor_returns_first_page_200`.
- [ ] **Step 3: Extend the permission matrix** (Task 9's test) with the four Part B routes: EDITOR 200 on all four; TEAM_LEAD 200 on conflicts and history, 404 on import and decisions; พนักงาน, ADMIN, VIEWER 404 on all four; anonymous 302; sidebar children per role match the route map.
- [ ] **Step 4: Run, expect FAIL. Step 5: Implement. Step 6: Run, expect PASS; run the whole suite (only the 15 baseline failures).**
- [ ] **Step 7: Commit** `feat: add Migration Decisions service, loader and page`

### Task 18: Visual parity check, docs, finish

**Files:**
- Modify: `docs/DECISIONS.md` (entry "Follow-up UI is a port of the V2 prototype; deviations R1-R9"), `docs/RUNBOOK.md` (how to open the pages, how to load decisions)
- Create: `docs/parity/crmv2-ui/README.md` (screen list, route map, rulings; links the `proto-*.jpg` already committed), `scripts/seed_leadui_demo.py` (about 12 leads across two teams, 4 intake rows, one import batch with a conflict, 4 migration decisions; idempotent; refuses to run when `DEBUG` is false)

- [ ] **Step 1: Reference screenshots already exist** (`docs/parity/crmv2-ui/proto-*.jpg`, ten screens, captured from the running prototype). If a screen needs another state (for example empty queue), capture it from `http://127.0.0.1:5174/` the same way.
- [ ] **Step 2: Capture the Django pages** with the demo seed, same viewport, as `crm-<screen>.jpg`. Compare each pair side by side and list every difference that is not in the Rulings table. Fix each in the owning task's template or CSS, with a regression assertion where the difference is text or class based.
- [ ] **Step 3: Browser console** shows 0 errors on all ten pages.
- [ ] **Step 4: Full suite** `docker compose exec -T web python -m pytest -q -p no:randomly --tb=no`: only the 15 baseline failures. **Commit** `docs: record the V2 UI port, route map and rulings`.
- [ ] **Step 5:** Use `superpowers:finishing-a-development-branch`.
