# RUNBOOK

Operational reference for running, backing up, and administering the Django
CRM. Written for whoever is on call — no prior session context assumed.

## Start / stop

```bash
# Dev (bind-mounted source, runserver, DEBUG=1 — compose.override.yaml auto-loads)
docker compose up -d
docker compose logs -f web          # tail app logs
docker compose down                 # stop everything, keep volumes

# Prod (gunicorn, nginx, DEBUG=0 — must name both files explicitly)
docker compose -f compose.yaml -f compose.prod.yaml up -d
docker compose -f compose.yaml -f compose.prod.yaml down
```

`web` runs migrations on boot (`RUN_MIGRATIONS=1` in `compose.yaml`, gated in
`docker/entrypoint.sh`) — `worker` deliberately does not, to avoid two
containers racing the same migration on startup (this raced and broke
Phase 1; see the gate in `entrypoint.sh` before touching it).

Health: `docker compose ps` — `db` must show `healthy` before `web` starts
(compose `depends_on: condition: service_healthy`). If `web` restarts in a
loop, check `docker compose logs web` first — almost always either a bad
`.env` value or `db` not yet accepting connections.

## Deploy (remote)

```bash
cp scripts/deploy.env.example scripts/deploy.env   # once, fill in real host/user/path
scripts/deploy.sh
```

Backs up the remote DB, `git pull --ff-only` on remote, `docker compose -f
compose.yaml -f compose.prod.yaml build && up -d`, then health-checks
container status + `curl http://localhost:8001/` on the remote (nginx maps
host 8001 -> container 80, since 80/8000 are already taken on the deploy target).
Migrations and
`collectstatic` run automatically inside `docker/entrypoint.sh` on the `web`
container's boot (`RUN_MIGRATIONS=1`) — the script doesn't call them
separately. Refuses to run if the local branch has unpushed commits, since
remote only ever deploys what's on the git remote. `scripts/deploy.env` is
gitignored — never commit real host/path values.

## Backup

```bash
scripts/backup.sh                 # writes backups/crm_<timestamp>.sql.gz
scripts/backup.sh /path/to/other  # custom output dir
```

Wraps `docker compose exec db pg_dump`, gzipped, `--no-owner --no-privileges`
so it restores cleanly regardless of which role runs the restore. `backups/`
is gitignored — never commit a dump. Schedule nightly via Windows Task
Scheduler (or cron) calling this script from the repo root; retention/offsite
copy is not handled by the script itself.

**Drill performed 2026-07-26** (dev stack, 200 customers / 501 orders / 502
order lines / 1 product / 1 team assignment): `pg_dump` → restored into a
disposable throwaway `postgres:16-alpine` container on the same Docker
network (not the live `db`, to avoid touching the demo data mid-session) →
row counts matched exactly, `pg_trgm`/`btree_gist` extensions and the
`ex_team_assignment_period` GiST exclusion constraint all survived the
round-trip intact. Confirmed working end to end.

## Restore

```bash
scripts/restore.sh backups/crm_20260726_115158.sql.gz
```

**Destructive** — drops and recreates the `public` schema in the target
database, then loads the dump. Prompts for the database name as a
confirmation gate; pass `--yes` only for scripted/CI use, never alias it away
in a shell profile.

After restoring, run `docker compose exec web python manage.py check
--database default` to confirm the app can see the restored schema, and spot
check a couple of tables' row counts against what you expected.

## Add a user

```bash
# One-off, via the CSV seeder (columns: email, role, staff_code, staff_name, owner_alias)
docker compose exec web python manage.py seed_users --csv seed/users.csv

# Or via the web UI: /accounts/users/ → "เพิ่ม user ใหม่" (EDITOR/ADMIN only)
```

Either path creates the row with `set_unusable_password()` — the user cannot
log in until you also issue them a password (below). `seed_users` is a true
upsert keyed on email; re-running it with an updated CSV is safe.

## Reset / issue a password

```bash
# Bulk / CLI (writes a CSV once — hand-deliver out of band, then delete it)
docker compose exec web python manage.py issue_initial_passwords --out /app/seed/reset.csv --emails user@example.com
docker compose exec web cat /app/seed/reset.csv     # read it
rm seed/reset.csv                                    # delete immediately after handing off — this is the only place the plaintext exists
```

Or per-user from the web UI: `/accounts/users/` → expand the row → "ออกรหัสผ่านใหม่"
→ confirm → the new password appears **once** in the flash message banner.
Both paths share one implementation (`crm/accounts/services.py::issue_password`)
so they can't drift. Either way sets `must_change_password=True`; the user is
forced to `/accounts/password-change/` on their next request
(`ForcePasswordChangeMiddleware`).

There is no self-service reset and no SMTP anywhere in this app — issuing a
password is always an EDITOR/ADMIN action taken on someone else's behalf.

## Re-import customer/order data

```bash
# CLI, one workbook at a time, same fixed-template importer the web upload uses
docker compose exec web python manage.py import_xlsx /app/<file>.xlsx --uploaded-by staff@example.com
```

Or via the web UI: `/orders/import/` (EDITOR/telesell-with-import-rights only).
Both call the same `crm.imports.services.import_workbook` — single-step,
fixed header template (see that module's docstring for the exact header
list), writes staging + normalized rows in one transaction per row, applies
the multi-SKU merge rule. Invalid rows are recorded with
`import_status='invalid'`, never silently dropped — check the response
message's counts (`valid`/`invalid`/`customers_created`/`orders_created`/
`lines_created`) against what you expected from the source file.

After a large import, run:

```bash
docker compose exec web python manage.py recompute_rollups
```

to refresh `Customer.last_order_date`/`order_count` if you suspect drift.

## Read the performance numbers

`docs/perf_baseline.json` — the "before" (Streamlit) baseline captured in
Phase 0. Compare against a fresh run of `tests/perf/` (seeds 20k
customers/40k orders locally, asserts p95 and "no seq scan / no WindowAgg" on
`/followup`) before claiming a regression or an improvement — don't compare
against the baseline numbers directly without re-running the current app's
own perf suite in the same class of environment, since baseline numbers were
measured against production Streamlit infrastructure, not this dev stack.

```bash
docker compose exec web python -m pytest tests/perf -m perf -q
```

## Known-outstanding items (not done by this rebuild)

These require the customer's real data and an explicit go/no-go — nothing
here should be executed without that.

1. **Final historical import.** Everything through Phase 6 was validated
   against synthetic seed data (`manage.py seed_demo`) plus hand-entered
   smoke-test rows. The real historical `.xlsx` has not been imported. Do
   this during an announced freeze window: `scripts/backup.sh` first, then
   `import_xlsx` against the real file, then re-run
   `docs/SMOKE_TEST_CHECKLIST.md` end to end against real data before
   announcing cutover.
2. **Retiring the Streamlit app.** Not touched by this rebuild. Once the
   historical import above is done and the smoke checklist passes against
   real data, set the Streamlit app read-only (or take it down) and update
   any bookmarks/links pointing at it. This is a business decision with a
   customer-visible cutover moment — do not do it unannounced.
3. Four `[ ]` (unchecked, not failed) items in
   `docs/SMOKE_TEST_CHECKLIST.md` — manual-order form re-render on
   validation failure, Excel-import failure rendering, archived-product
   read-only rendering, and dashboard date-range presets — each has
   automated coverage at the logic layer already; only the live click-through
   wasn't completed in the 2026-07-26 hardening pass (session hit its
   8-hour hard expiry mid-check). Re-click these before go-live; they are
   pre-existing UI paths from earlier phases, not new-code risk.

## CRM V2 Lead surface — backfill and the HX63 tooling

### Backfilling Leads from existing customers

After migrating, seed the teams and then build the Lead rows:

```bash
docker compose exec web python manage.py seed_teams
docker compose exec web python manage.py backfill_leads --dry-run --team-code CRM_TEAM
docker compose exec web python manage.py backfill_leads --team-code CRM_TEAM
```

Always run `--dry-run` first; it reports counts and writes nothing.

What it does, per customer:

- **With a `staff_code`** → a `Lead` with an open ownership segment. The team
  comes from that caretaker's open `TeamAssignment` when there is one,
  otherwise from `--team-code`. A `staff_code` matching no user leaves the lead
  `TEAM_UNASSIGNED` rather than inventing a caretaker.
- **Without a `staff_code`** → an `IntakeItem` in the Global Unassigned queue.
  That is D019: the customer exists, the team is unknown, so there is no Lead
  yet. It is deliberately not given a placeholder team.

It is idempotent and processes 1000 rows per transaction, so it is safe to
re-run and safe to interrupt on the ~216k-row table.

**Missed-channel starts at zero.** The backfill opens the ownership baseline at
migration time, so orders predating it are not retroactively counted. This
mirrors D023, which fixes the HX63 baseline at the snapshot instant for the
same reason: a migrated lead must not arrive blaming a caretaker for
marketplace orders that predate the rule being enforced. Counting accrues from
cutover onward.

### HX63 migration tooling (one-shot, not a service)

The CRM V2 migration, backup and cutover pipeline stays TypeScript. Its plan
hash and reconciliation digest are certified by the prototype's test suite, and
after cutover it never runs again — re-porting it to Python would discard that
evidence for nothing.

It runs under a compose profile, so `docker compose up` never starts it:

```bash
PROTO_DIR=/path/to/customer-crm-v2-codex \
  docker compose --profile hx63 run --rm hx63 tools/run-hx63-migration.ts
```

The prototype is mounted read-only, so a run cannot modify the reference
implementation it is certified against. `docker compose ps` must never show an
`hx63` container.

**Plan 3 owns the actual migration run.** It is blocked on human decisions that
`07_MIGRATION_AND_DATA_REQUIREMENTS.md` forbids guessing: 10 staff aliases,
8,613 identity reviews, 1 unknown status affecting 120 orders, 1 null-date
order, and the fifth source sample.

### Verifying a from-empty install

Do **not** run `docker compose down -v` to test this. On a working machine that
destroys the development database. Use a throwaway compose project instead, so
only its volumes are created and removed:

Pass `-f compose.yaml` so `compose.override.yaml` is excluded: the override
publishes 5432 and 8000, and a second project trying to bind them fails with
`port is already allocated` while the normal stack is up.

The `web` container already runs migrations on boot (`RUN_MIGRATIONS=1`), so
do not also call `migrate` immediately after `up` — both racing to create
`django_migrations` fails with a `pg_class_relname_nsp_index` unique violation.
Wait for the container to be healthy and go straight to seeding.

```bash
docker compose -f compose.yaml -p crmverify up -d        # migrates on boot
docker compose -f compose.yaml -p crmverify exec -T web python manage.py seed_teams
docker compose -f compose.yaml -p crmverify exec -T web python -m pytest tests/unit tests/db tests/view
docker compose -f compose.yaml -p crmverify down -v   # removes ONLY crmverify_* volumes
```
