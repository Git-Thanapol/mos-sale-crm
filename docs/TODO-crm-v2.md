# CRM V2 follow-up UI and import: open work and decisions

Status when the PR was opened: 3013 tests pass; the only failures are the 15
pre-existing ones (`test_import_excel`, `test_matrix`, `test_online_orders`;
17 on a fresh database because two more depend on data).

## Decisions needed from the business / owner

- [ ] **Run `backfill_leads` on production?** A real-data dry run shows 146 leads
      with an owner and 216,309 without. `--resolve-to TEAM_CODE` avoids a
      216k-row hand-resolve queue. Needs a decision on which team receives the
      unowned customers.
- [ ] **Imports never open follow-up cycles** (a cycle opens when someone works the
      lead). Confirm that is what the business wants for imported orders.
- [ ] **ROLLBACK_FAILED has no exit** (faithful to the prototype state machine), and
      the drift check compares whole rows including `updated_at`, so a later
      routine edit of an imported lead ends rollback for good. Decide whether a
      human-reconciled batch needs a way back.
- [ ] **TEAM_LEAD sees import history only for batches holding a conflict visible to
      them** (the prototype shows no scoping). Confirm.
- [ ] **Import sources.** CRM_CUSTOMERS, INOUT_MANAGE_MAIN and LITE_DETAIL_ORDER@2 import (Nov 2026).
      `TELE_SALES_REPORT`, `CUTOVER_ORDER_SHEET` and Lite v1 are still refused at Preview. Inout
      carries no channel or lines, so Lite supplies them; confirm that is the intended division.
- [ ] **Order status vocabulary.** The live data holds only `สำเร็จ` and blank, so
      `crm/leads/order_status.py` maps only those. The prototype's status table
      (cancelled, COD-returned, ...) is ported in `crm/domain/status_map.py` but not
      yet wired into the lead missed-channel rule. Confirm the business meaning of
      each status before wiring.
- [ ] **Staff and team name matching** in imports resolves only an exact, unique
      active `staff_name` / `staff_code` and exact team code/name/display name.
      Confirm, or supply the alias list.

## To do before or at merge

- [ ] Run migrations on every environment: `importv2` 0001-0004, `leads` 0003-0005,
      `orders` 0003, `customers` 0006. All are additive except `customers` 0006, which
      drops the UNIQUE constraint on `phone_key` (client request, Nov 2026).
- [ ] Decide what `seed_leadui_demo` data (if any) stays on shared environments;
      remove with `manage.py seed_leadui_demo --clean`. It refuses to run unless
      `DEBUG` is on.
- [ ] Confirm the HX63 planner container writes decisions in the prototype's
      `MigrationDecision` JSON shape for `manage.py load_migration_decisions`
      (`decisionId`, `evidenceHash` are re-derived and verified on load).
- [ ] Customer sign-off on the ten screens against the prototype screenshots in
      `docs/parity/crmv2-ui/`.

## Deferred review findings (not fixed in this PR)

From the final review of the import work:

- [ ] `upsert_intake` rewriting `source_ref` on an existing intake is not journalled,
      so a rollback leaves it pointing at the rolled-back batch.
- [ ] Older lead POST routes in `crm/leads/views.py` answer 403 (not 404) for an
      out-of-scope lead (an existence oracle); `close_cycle` copies the pattern.
- [ ] `crm/leadui/selectors.py::visible_customer` builds a hashed subplan over all
      customers (~50 ms per customer page at 216k); use `Exists()`.
- [ ] My Work keyword search (`icontains` over name/phone/lead_key) has no trigram
      index; cheap now, measure at full lead volume.
- [ ] Rollback's drift pre-check and the undo are not one atomic step.

From the earlier lead-surface reviews (M3-M14, minors):

- [ ] No unconditional index on `crm_lead.last_order_at` (sequential scan as leads grow).
- [ ] No `CheckConstraint` tying `routing_state` to `caretaker`; production code
      cannot produce a mismatch, but the database does not forbid it.
- [ ] Remaining minors M3-M6 and M8-M14 from the first review of the lead surface.

## Known limits (by design, documented)

- Upload limits: 25 MB and 100,000 rows.
- Identity candidates come from the phone index only (exact `phone1`/`phone2`/`phone_key`),
  so the prototype's "phone differs by 1-2 digits" path is unreachable.
- An order with any bad row is INVALID as a whole and blocks Confirm.
- Re-running Preview resets conflict resolutions.
