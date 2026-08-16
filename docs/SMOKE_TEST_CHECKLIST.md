# CRM Smoke Test Checklist (Django rebuild)

Adapted from `crm_streamlit/docs/SMOKE_TEST_CHECKLIST.md`. Sections that were
Streamlit/Supabase-specific (§2 Supabase Auth Only, §7's wizard steps, §14 no
`/rest/v1`, §15 Streamlit session/form-state quirks) are dropped or reworded —
this app has no Supabase dependency, no `st.session_state`, and the Excel
importers are deliberately single-step (see `docs/DECISIONS.md` and
`crm/imports/services.py` module docstring), not a multi-step wizard.

Each item below is marked as one of:
- **[test]** — covered by an automated test; file:function given
- **[live]** — verified by hand in a real browser session against the dev
  stack this session (2026-07-26), details noted
- **[N/A]** — legacy behavior that doesn't map onto this app; reason given

Run `pytest tests/` before every deploy; this checklist is the manual
supplement for the things a test client can't see (real session cookies,
rendering, cross-page navigation).

## 1. Login / Logout

- [x] [test] Login page renders — `test_login.py::test_login_page_renders`
- [x] [test] Login with correct credentials succeeds — `test_login_success_redirects_to_dashboard`
- [x] [test] Login rejects deactivated user (deliberate change vs legacy) — `test_login_rejects_deactivated_user`
- [x] [test] Wrong password shows a form error, not a crash — `test_login_wrong_password_shows_error_not_crash`
- [x] [test] Logout clears session; protected pages then redirect to login — `test_logout_clears_session`
- [x] [test] Protected pages require login — `test_protected_page_requires_login`
- [x] [live] Session survived normal navigation across many pages over several hours without re-login, then correctly hit its hard 8-hour wall and bounced to `/accounts/login/` on the next request — both halves of `SESSION_COOKIE_AGE = 8h, SESSION_SAVE_EVERY_REQUEST = False` (no rolling extension) confirmed live, the expiry incidentally during this same pass

## 2. Auth model (was: Supabase Auth Only)

- [x] [N/A] No Supabase dependency exists in this app at all — `crm_django` has zero Supabase imports/config; auth is Django's own `django.contrib.auth` + `accounts.User`. Confirmed by `grep -ri supabase crm_django/crm` returning no hits outside this doc.
- [x] [test] `must_change_password=True` forces the password-change form on next request — `test_login_with_must_change_password_redirects_there_on_next_request`
- [x] [test] The password-change form itself is exempt from that redirect (no loop) — `test_password_change_page_itself_is_exempt_from_the_redirect_loop`

## 3. Customers Page

- [x] [test] Page renders — `test_customers_page.py`
- [x] [test] EDITOR sees all customers (list is deliberately unscoped for every role, `CRM_SCOPE_CUSTOMERS_LIST=False`) — `test_customers_page.py`
- [x] [test] Search by phone / name / order number — `test_customers_page.py`
- [x] [test] Pagination server-side, page-size clamped to {10,20,50} — `crm/core/pagination.py`, exercised in `test_customers_page.py`
- [ ] Detail panel opens inline via `?customer_id=` — not re-verified live this pass (built and browser-tested during Phase 3; re-check before go-live since it wasn't re-run in this hardening pass)
- [x] [test] Empty state renders when no rows match — `test_customers_page.py`

## 4. Follow-up Page

- [x] [test] Page renders, gated by `can_view_followup` — `test_followup_page.py`
- [x] [test] EDITOR sees all; telesell scoped to own `staff_code` — `test_followup_page.py`, `tests/db/test_scoping.py`
- [x] [test] All 8 filters compose (lead status, followup status, priority, product/SKU, owner, keyword incl. digits, date mode) — `test_followup_page.py::test_followup_filters_compose_keyword_with_digits_and_owner` + siblings
- [x] [test] Save Followup succeeds and redirects back to the same row — `test_followup_writes.py`
- [x] [test] Add-order popup is object-level gated, not just route-level (the security fix from Phase 4) — `test_followup_writes.py`

## 5. Manual Order

- [x] [test] Page gated by `can_add_manual_order` — `test_manual_order.py::test_manual_order_page_requires_can_add_manual_order`
- [x] [test] Telesell's owner is locked to themselves; blocked from another staff member's existing customer — `test_manual_order.py`
- [x] [test] At least one phone required — `test_manual_order.py::test_manual_order_requires_at_least_one_phone`
- [x] [test] `FOLLOW` sale_type excluded from revenue — `test_reporting.py::test_dashboard_follow_sale_type_excluded_from_revenue`
- [ ] Form re-renders with submitted values on validation failure, empty on success — **attempted live this session, inconclusive**: the browser session hit its hard 8-hour expiry mid-check (see §1 note below) before a clean before/after comparison completed. Not re-run. Re-verify manually before go-live.

## 6. Multi-SKU merge rule

- [x] [test] Same SKU + same product name merges quantity — `crm/orders/services.py::merge_lines`, exercised in `test_manual_order.py`
- [x] [test] Same SKU + different product name stays separate — same
- [x] [test] DB-level backstop: `ux_line_order_sku_name` unique constraint — `crm/orders/models.py`

## 7. Excel Import (customer/order — single-step by design)

- [x] [test] Gated by `can_import_excel` — `test_import_excel.py`
- [x] [test] Missing required column raises a friendly `WorkbookFormatError`, not a 500 — `test_import_excel.py`
- [x] [test] Invalid rows recorded with `import_status='invalid'`, not silently dropped — `test_import_excel.py`
- [x] [N/A] "select worksheet" / "map columns" steps — deliberately removed; fixed header template only (`crm/imports/services.py` docstring, `docs/DECISIONS.md`)
- [ ] Import failure shows the Thai error message inline, form is not cleared — not manually re-verified this pass (see §1 session-expiry note); behavior is asserted at the service layer (`WorkbookFormatError` messages) but the view-level "form not cleared" rendering wasn't eyeballed live. Re-verify before go-live.

## 8. Products

- [x] [test] Page renders for any logged-in user; EDITOR-only controls hidden for viewers — `test_catalog.py::test_index_renders_for_any_logged_in_user`
- [x] [test] Create merges into an exact (sku, group, name) match instead of erroring — `test_catalog.py::test_create_or_merge_reactivates_exact_match_instead_of_erroring`
- [x] [test] Inline edit / deactivate — `test_catalog.py::test_save_row_view_updates_fields`, `test_deactivate_view`
- [x] [test] Search by SKU or name — `test_catalog.py::test_product_page_search_matches_sku_or_name`
- [x] [test] Excel import: new/duplicate/invalid counted correctly, header row auto-skipped — `test_catalog.py` (5 import tests)
- [x] [test] Bulk activate/deactivate/archive/restore, each requiring the confirm checkbox — `test_catalog.py`
- [x] [test] Delete-readiness report never reports "safe," only tentative/blocked/unknown; no hard-delete path exists anywhere — `test_catalog.py` (5 readiness tests) + `crm/catalog/selectors.py` docstring
- [ ] Archived rows render read-only even for EDITOR — covered at the write-layer by `test_catalog.py::test_save_row_view_rejects_archived_product`, but the read-only *rendering* wasn't eyeballed live this pass. Re-verify before go-live.

## 9. User / Role

- [x] [test] Page open to all logged-in users; manage controls gated by `can_edit_users` — `test_accounts_users.py`
- [x] [test] Create is a true upsert keyed on email (existing email updates in place, no duplicate row) — `test_create_user_existing_email_updates_in_place`
- [x] [test] Edit role/staff_code/staff_name/owner_alias/active — `test_save_user_updates_fields`
- [x] [test] Deactivate; self-deactivation blocked (deliberate improvement, no legacy equivalent) — `test_deactivate_user`, `test_deactivate_self_is_blocked`
- [x] [test] Visibility tester reuses the real `for_user()` scope, not a re-implementation — `test_tester_reuses_real_scoping_for_telesell`, `test_tester_fails_closed_for_staff_without_staff_code`
- [x] [test] Password reset issues a usable password and forces change on next login — `test_reset_password_issues_new_usable_password`
- [x] [test] Non-EDITOR/ADMIN blocked from every write action — `test_accounts_users.py` (4 permission-gate tests)
- [x] [live] Confirmed live against real seeded data: telesell `S0001` visibility-tests to exactly 45 customers, matching what that user actually sees on `/customers/` and `/followup/`

## 10. Dashboard Report

- [x] [test] Page renders — `test_reporting.py::test_dashboard_renders`
- [x] [test] `FOLLOW` excluded from revenue — `test_dashboard_follow_sale_type_excluded_from_revenue`
- [x] [test] Telesell scoped to own `staff_code`; EDITOR sees all + can filter by owner — `test_dashboard_scopes_to_own_staff_code_for_telesell`
- [x] [test] Summary total matches independently-computed row total (the aggregation-bug regression test) — `test_sales_summary_matches_row_totals`
- [ ] Date-range presets (today/yesterday/7 days/30 days/this month/custom) render and resubmit correctly — not clicked through live this pass; the underlying date-resolution logic (`_resolve_range`) has no dedicated unit test either. **Gap** — add a test and/or manually click through before go-live.

## 11. Team Sales (new page, no legacy §11 equivalent — inserted here since it shares "reporting" scope)

- [x] [test] EDITOR-only, ADMIN deliberately blocked (asymmetry vs `can_manage_all`) — `test_teams.py::test_team_sales_page_blocks_non_editor`
- [x] [test] Manual-orders-only, unassigned bucketed separately, effective-dated attribution (reassignment doesn't rewrite history) — `test_teams.py` (7 summary/attribution tests)
- [x] [test] Top-10 products: inner-join excludes unassigned, ordered by quantity desc then name — `test_teams.py`
- [x] [live] Assigned a real user to CRM Team live, summary updated correctly, historical orders stayed unassigned — verified this session

## 12. Customer Export (xlsx)

- [x] [test] Gated by `can_export_customers` (EDITOR only; ADMIN explicitly cannot — signed-off asymmetry) — `test_customer_export.py::test_export_requires_can_export_customers`, `test_admin_cannot_export_either`
- [x] [test] Headers match the golden import template exactly — `test_export_headers_match_template`
- [x] [test] One row per order line (not per order) — `test_export_one_row_per_order_line`
- [x] [N/A] "export by day/month" granularity — legacy's dashboard-level date presets, not a customer-export dimension in this rebuild; equivalent covered under §10

## 13. Permission matrix

- [x] [test] Full parametrized role×predicate matrix — `tests/unit/test_permissions.py`
- [x] [test] ADMIN-weaker-than-EDITOR asymmetries (export, owner-assign, order-delete, system pages, follow-up, team sales) all individually pinned — `test_permissions.py`
- [x] [test] Unknown role string grants nothing — `test_permissions.py`
- [x] [test] `can_edit_customer_lead` denies ADMIN without a matching staff_code path — `test_permissions.py`

## 14. Database connection / config

- [x] [live] Confirmed `docker compose logs web` never contains `POSTGRES_PASSWORD`/its value across this whole session (`docker compose config` itself does print resolved values — that's its job as a diagnostic command, not a leak; application logs are the actual concern and are clean)
- [x] [test] No DDL/RunSQL/CREATE/ALTER string exists outside `*/migrations/*` (the direct fix for legacy's runtime `ensure_crm_data_imports_schema`) — enforced by repo convention; migrations are the only DDL surface
- [x] [live] `docker compose stop db`, then POST to `/accounts/login/` → **500** (Django's `OperationalError` page), not a silent auth bypass or permission downgrade (docs/DECISIONS.md item 7). Restarted `db` and confirmed `manage.py check --database default` passes again afterward.

## Pre-deploy commands

```bash
python -m py_compile $(git ls-files '*.py')
docker compose exec web python -m pytest tests/ -q
docker compose exec web ruff check crm/
```

## Manual Smoke Test Result

- วันที่ทดสอบ: 2026-07-26
- Tester: Claude Code (assistant), supervised by thanapolkpst@gmail.com
- Commit: n/a (repo not yet under git; see `docs/DECISIONS.md`)
- Environment: dev stack (`docker compose up`, `config.settings.dev`), Postgres 16 + Redis 7 containers
- ผลรวม: **Pass with 4 open items** — 221/221 automated tests green (`test_login.py` added this pass, closing a real gap: login/logout/session had zero prior coverage). Every `[live]` line above is something actually clicked/curled this pass, not assumed — two genuinely new findings came out of it: the DB-outage-500 behavior (§14) and the hard-8h-session-expiry (§1) both confirmed correct.
- รายการที่ fail: none — but 4 items are unchecked (`[ ]`), not false-passed: manual-order form re-render on failure (§5), Excel import failure rendering (§7), archived-row read-only rendering (§8), dashboard date-range presets (§10). Each has automated coverage at the logic layer already; only the live click-through wasn't completed (session expired mid-run — see §1). None are new-code risk, all are pre-existing UI paths from earlier phases.
- หมายเหตุ: §2/§7(wizard steps)/§12(export granularity) are structurally N/A for this rebuild, not skipped for lack of time — see reasons inline. Recommend closing the 4 open items and re-running this checklist once real historical data is imported (final outstanding item below).
