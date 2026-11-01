# CRM V2 parity fixtures

These files are the oracle for the Python ports in `crm/domain/`. Each case's
`expected` value was produced by **executing** the CRM V2 prototype's own
functions, not by transcribing them. That is what makes
`tests/unit/*_parity.py` evidence that the port matches the reference
implementation rather than a second opinion about it.

## Never hand-edit

Regenerate. A hand-edited fixture silently turns the oracle into a mirror of
whatever the Python happens to do, which is the one thing these files exist to
prevent. If a case looks wrong, the right move is to fix the exporter or the
port — not the JSON.

## Provenance

Reference implementation: the CRM V2 prototype handed over in
`CRM_V2_Developer_Handoff_Server_Data`.

The handoff package (`00_START_HERE.md`, `10_CURRENT_PROJECT_STATUS.md`) names
the reviewed checkpoint as `f97f955874f6bea0b43cd7723039b6ca50fe398c`. That
commit id could **not** be verified here: the prototype directory is a git
worktree whose parent repository
(`C:/Users/admin/Downloads/CRM_V2_MULTI_AGENT_DOCS`) does not exist on this
machine, so `git` refuses to operate in it. The source files actually read by
the exporter hashed as follows at generation time, which is verifiable where
the commit id is not:

| File | sha256 |
|---|---|
| `src/domain/ownership.ts` | `cf3557485162d1abd80a561fa5650173af6292de81f0323c168ec4fc2b13d20b` |
| `src/domain/missed-channel.ts` | `f97447da9395080508ee0d80c67220fce467d62c78f7ba13d4693f63bb39cc6d` |
| `src/read-model/last-order-age.ts` | `576175ebdbc33054454437a14ed99614d80519bc44cce6b4b61de68c2bb6bb5d` |
| `src/domain/enums.ts` | `2e99e3b7f60f3d64887f771410112c25ed2598f7753735c60ad87e809ce06d87` |

If a regeneration produces different hashes, the reference implementation
changed and the diff in these fixtures is the behavioral consequence — review
it, do not just accept it.

## Regenerating

The exporter lives in this repo but must run against the prototype's
`node_modules`, since this repo has no Node toolchain:

```bash
cd "<PROTOTYPE_REPO>"
./node_modules/.bin/tsx \
  "C:/Laptop files/MOS/mos-sale-crm/scripts/export_crmv2_fixtures.mts" \
  "<PROTOTYPE_REPO>" \
  "C:/Laptop files/MOS/mos-sale-crm/tests/fixtures/crmv2"
```

`npm ci` is only needed if `node_modules` is absent. The script is `.mts`, not
`.ts`, because it runs outside the prototype's `package.json` scope and tsx
would otherwise compile it as CommonJS, where its top-level `await` is illegal.

## Contents

| File | Cases | Covers |
|---|---|---|
| `ownership.json` | 24 | 3 ownership policies x 8 scenarios (no caretaker, same closer, other closer, null/marketplace closer, active lock, STOPPED, resigned closer with and without an incumbent). All six `reason` values appear. |
| `missed_channel.json` | 13 | The full reason matrix — `COUNTED_NO_CLOSER`, `COUNTED_OTHER_CLOSER`, `RESET_BY_CARETAKER`, `EXCLUDED_CANCELLED`, `EXCLUDED_COD_RETURNED`, `BEFORE_BASELINE` — plus duplicate order ids, tied timestamps in both input orders, shuffled history, and all three policy toggles. |
| `last_order_age.json` | 42 | Every `LAST_ORDER_AGE_PRESETS` entry probed at `min-1`, `min`, `max`, `max+1`, each preset against a null last order, six custom ranges, and the no-filter case. |

## Case shape

```json
{
  "name": "EXCLUSIVE/other-closer",
  "input":    { "...": "arguments, with Sets as sorted arrays and timestamps as integer milliseconds" },
  "expected": { "...": "what the prototype returned for that input" }
}
```

Timestamps are epoch milliseconds (the prototype's `Millis`). The prototype's
test epoch is `T0 = Date.UTC(2026, 9, 1, 3, 0, 0)`, from
`tests/support/factories.ts`; the last-order-age cases use a Bangkok-noon
`now` so whole-day offsets never straddle a day boundary.
