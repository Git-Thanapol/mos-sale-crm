# Decisions requiring customer sign-off

Status: **DRAFT — awaiting sign-off.** Nothing below is implemented yet; each row becomes final only when checked off. This document exists so every deliberate behavior change between `crm_streamlit` (legacy) and the new Django app is visible in one place before it ships, not discovered after.

Reference: full analysis in `docs/legacy/data-layer-report.md` and `docs/legacy/auth-permissions-report.md`.

## Confirmed already (via /plan discussion)

| # | Decision | Chosen |
|---|---|---|
| 1 | Data model | Normalize into Customer / Order / OrderLine / Followup / Product / User |
| 2 | Legacy data | Start empty — no Neon dump/ETL; history re-enters via Excel import |
| 3 | Auth | Custom Django user model, admin-issued initial passwords, no SMTP |
| 4 | Frontend | Django templates + HTMX + Alpine |
| 5 | First milestone | `/followup` end to end |

## Behavior changes needing explicit sign-off

- [ ] **6. Deactivated user login rejected.** Today: `is_active=false` silently downgrades a user to viewer role (still logs in, sees nothing). New: login is refused outright. *Why:* matches what the `/users` "deactivate" button visibly implies; the old behavior is a fail-closed accident, not a designed feature.
- [ ] **7. Database outage shows an error page, not a silent permission downgrade.** Today: if Neon is unreachable, `fetch_user_role` swallows the exception and returns a viewer-role default with no error. New: a 500 with a Thai error message. *Why:* flagged HIGH severity in the legacy audit — an outage should never silently change what a user can see.
- [ ] **8. Follow-up keyword search no longer drops other filters.** Today: if the search box contains any digit, `build_followup_where` returns early and every other filter (status/priority/owner/product/date) is silently ignored. New: all filters always compose. *Why:* this is a bug, not a designed shortcut — no one asked for "typing a phone number disables the priority filter."
- [ ] **9. Priority sort and priority filter now agree.** Today: the follow-up list's `ORDER BY` contains four mojibake (CP874-corrupted) Thai literals that can never match a stored value, so legacy Thai priorities (`ด่วนมาก`, `สูง`, `ต่ำ`, `ปกติ`) sort as if unset even though the `WHERE` clause matches them correctly. New: a stored `priority_rank` integer used everywhere, so sort and filter use the same rule. *Why:* pure bug fix; the two code paths currently disagree with each other, not with any intended design.
- [ ] **10. One `customer_key` / one `followup_status` vocabulary.** Today: `pages/customers.py` writes a bare phone number as `customer_key` while every other writer uses `customer_id:<id>` — so a follow-up marker set from the Customers page is invisible on the Follow-up page. Similarly, two incompatible `followup_status` vocabularies (`none/scheduled/round_1..4/done/missed` vs `0/1/2/3/RESET`) write the same column. New: the normalized schema makes both impossible by construction (`customer_id` is a real foreign key; `status` is a single enum). *Why:* fixes a real data-visibility bug — expect previously invisible follow-up markers to suddenly appear once merged. Flag this explicitly to whoever reviews the data after cutover so it doesn't read as new/duplicate activity.
- [ ] **11. Customers list scoping — kept as today (no change), confirmed by explicit setting.** Every logged-in role can see every customer on `/customers` (`enforce_user_scope=False` in the legacy code), while `/followup` and the dashboard are scoped to the logged-in staff member's own rows. New: identical behavior, but as a named setting (`CRM_SCOPE_CUSTOMERS_LIST = False`) instead of an easy-to-miss default argument. *Why flagged:* worth a second look from the customer — is unscoped customer browsing actually intended for every role, or was it meant to be tightened later and never was?
- [ ] **12. Duplicate-phone lock closes its bypass window.** Today: the lock only checks the first 50 matching rows, so a 51st conflicting row is silently missed. New: checks existence over the full match set. Also today: the write and the conflict check are two separate round-trips (a race is possible); new: both happen in one transaction. *Why:* strengthens an existing safety control; behavior only changes for a currently-missed edge case.
- [ ] **13. Dashboard revenue math uses Bangkok time everywhere.** Today: the dashboard's "due today"/"overdue" use the database server's `current_date` while the sales report explicitly converts to `Asia/Bangkok` — the two can disagree near midnight. New: `Asia/Bangkok` used consistently. *Why:* bug fix, not a design choice.
- [ ] **14. `crm_owner_assignments` table dropped.** Its only writer (`assign_owner_to_phones`) is called solely from the orphaned, unreachable `customer360.py` — nothing in the live app writes to it. *Why:* dead code cleanup; flagging in case it was intended for a feature that never got wired up.
- [ ] **15. `crm_product_options.active` shadow column dropped.** Written once at migration time, never read by any app code (the real flag is `is_active`). *Why:* dead column cleanup.

## Preserved verbatim — explicitly NOT changing, listed so no one "fixes" them later

- **ADMIN is weaker than EDITOR** for several actions (export customers, assign owner, delete order, view system pages, view follow-up) despite ADMIN otherwise outranking EDITOR for user/product/import management. This is a signed-off invariant from the existing UAT docs, not a bug.
- **Fail-closed scoping**: a non-manager with a blank `staff_code` sees nothing, never everything.
- **`staff_code` is the sole authorization key** — never falls back to `owner`/`staff_name`/`owner_alias`.
- **Staff-code normalization stays out of scope.** `JEEB`'s 3,087 rows and the 730 rows with no assigned owner/staff_code keep exactly their current (non-)values; unassigned rows display `ยังไม่มอบหมาย`. This was already explicitly rejected in the legacy `docs/STAFF_MAPPING_DECISION_REQUIRED.md`.
- **Duplicate-phone lock fails open** if the team-membership lookup itself errors (deliberate: never block a save because of an unrelated lookup failure).
- **`dedupe_key` keeps no unique constraint** — the hash is tested for correctness, but a real constraint would fail immediately against existing duplicate rows.

## Resolved

- [x] **Users CSV — bootstrap account provided.** `seed/users.csv`: `thanapolkpst@gmail.com, EDITOR, S0001, Thanapol, Max`. Role was initially given as ADMIN; changed to EDITOR on customer confirmation, since ADMIN cannot export customers/assign owners/delete orders/view Follow-up or System pages under the preserved-verbatim matrix (see "Preserved verbatim" below) and this is the one account actually driving day-to-day use. Additional users can be added later through the `/users` page (EDITOR-only) once the app exists — no need to collect a full roster up front. No Supabase password hash is carried over; this account gets a fresh admin-issued initial password on first run.

## CRM V2 Lead surface (Plan 1) — pagination boundary

The repo now has two pagination strategies on purpose, and both are correct
for what they page.

- **Offset (`crm.core.pagination.Page`)** stays in use for the legacy
  follow-up list (`crm.followups.selectors.followup_page`) and the other
  existing pages. It renders a numeric pager with a total page count, which
  needs `COUNT(*)`, and those pages have shipped against it. Nothing here
  changes them.
- **Keyset (`crm.core.pagination.KeysetPage`)** is used by every new Lead
  operational queue (`crm.leads.selectors`). CRM V2's
  `06_IMPLEMENTATION_ACCEPTANCE_CRITERIA.md` section F requires "offset
  pagination count = 0 for large operational lists", forbids unbounded
  queries, and asks for stable cursor behaviour. A keyset queue therefore has
  no total and no page numbers — it has "next" and nothing else, which is why
  it cannot simply replace the numeric pager on the legacy list.

The queues sort on `(last_order_at NULLS LAST, id)`. NULLS LAST makes the seek
predicate asymmetric: from a row with a timestamp, what remains is later
timestamps, the same timestamp with a higher id, or any null row; from a null
row, only higher-id nulls remain. Cursors are opaque base64 so a caller cannot
hand-build one and couple itself to that sort key, and a malformed cursor
decodes to `None` (first page) rather than raising, because cursors live in
URLs that get truncated and edited.

Queues read the maintained columns on `Lead` (`last_order_at`, `total_orders`,
`missed_channel_count`) instead of aggregating `crm_order` per row, per
`03_INFRASTRUCTURE_REPLACEMENT_GUIDE.md`: "do not scan all Orders every time a
Lead list is opened."

## CRM V2 Lead surface (Plan 1) — role mapping

CRM V2's `08_SECURITY_AND_AUTH_REQUIREMENTS.md` defines three roles: Admin
(full permitted scope), Team Lead (own Team only), Telesale (permitted
Leads/Customers only, no ownership/admin/import authority). This repo's matrix
is EDITOR / ADMIN / พนักงาน / ทั่วไป and is deliberately asymmetric — see
"Preserved verbatim" above, where ADMIN is recorded as WEAKER than EDITOR for
follow-up, export, owner-assign, order-delete and system pages.

Both cannot hold at once, so the mapping is:

| CRM V2 role | This repo |
|---|---|
| Admin | **EDITOR** |
| Team Lead | **TEAM_LEAD**, new, authorized per team by `User.team_scope` |
| Telesale | พนักงาน |

**This repo's ADMIN is not the spec's Admin.** It keeps its documented weaker
scope: it cannot reassign a caretaker and cannot resolve Global Unassigned
intake. The new predicates were written not to strengthen it, and
`test_this_repos_admin_is_not_the_specs_admin` pins that.

`crm/core/permissions.py` stays the single source of truth; the Lead
predicates were added there rather than in a second module. `User.team_scope`
is server-side only and is never read from a request, because
08_SECURITY forbids trusting a client-supplied Team scope.

TEAM_LEAD row scoping (`crm/core/scoping.py`) filters on the row's own team
via `Lead.TEAM_PATH = "team__code"`, not on the denormalised
`access_team_ids` array. Two reasons: the FK is the authority and the array is
a cache a service must refresh, and `can_view_lead` reads `lead.team.code`, so
scoping on anything else would let the queryset and the predicate disagree. A
queryset that admits a row the predicate rejects — or the reverse — is how a
leak gets in. `derive_customer_access` is a union across every team holding a
lead for that customer and must therefore never scope a single lead.


## CRM V2 follow-up UI and import

The follow-up pages are a port of the customer's CRM V2 prototype UI, not a
redesign. Pure rules (ownership, missed-channel, last-order-age, import state
machine, confirm gate, batch business date, conflict scope, identity, order
merge, row evaluation, mapping, status map, migration decisions) live in
`crm/domain` and are proven against fixtures produced by EXECUTING the
prototype (`scripts/export_crmv2_*.mts`, `tests/fixtures/crmv2/`). Never
hand-edit those fixtures; regenerate them.

Import scope. The engine (`crm/importv2`) runs end to end for `CRM_CUSTOMERS`,
the CRM's own 26-column line-level export. LITE_DETAIL_ORDER, INOUT_MANAGE_MAIN,
TELE_SALES_REPORT and CUTOVER_ORDER_SHEET are recognised and validated but
Preview refuses them (`SOURCE_NOT_SUPPORTED`), because they need data from other
files joined first. It never half-imports.

Import rulings: file rows are grouped by order id and line amounts summed;
identity candidates come from the phone index only; a staff or team name resolves
only when exactly one active user/team matches; an order with a bad row is
INVALID as a whole and blocks Confirm; imports never open follow-up cycles; a
NEW_CUSTOMER resolution against an exact phone match creates a split
`phone_key` (`<key>#<order id>`) because `crm_customer.phone_key` is still
unique; an order with no known team and no lead goes to Global Unassigned
(D019/D024). Commit writes in transactions of 100, journals every change
(including lead, ownership segment, audit and cycle rows by diffing them) and
replays by operation key. Rollback undoes exactly the journal, in reverse, and
stops with ROLLBACK_FAILED, changing nothing, if a person has edited an imported
row since.

Roles map as above (Admin = EDITOR); repo ADMIN, the read-only roles and
inactive users hold no import authority. Hidden menu entries are a convenience:
every page returns 404 to a user outside its audience and every write re-checks.
