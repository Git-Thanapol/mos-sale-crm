/**
 * Export CRM V2 prototype behaviour as language-neutral parity fixtures.
 *
 * The Python ports in crm/domain/ are proven correct by replaying these files
 * (see tests/unit/test_parity_fixtures.py). The `expected` value of every case
 * is produced by EXECUTING the prototype's own functions here — it is never
 * transcribed by hand, which is what makes this an oracle rather than a second
 * opinion.
 *
 * The prototype's tests are inline `it()` blocks, not declarative tables, so
 * the case inputs below mirror those blocks and add the extra coverage the
 * implementation plan requires (every policy x scenario, the full missed-channel
 * reason matrix, every last-order-age preset boundary at both ends).
 *
 * Usage, from anywhere:
 *   npx tsx scripts/export_crmv2_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>
 *
 * Run it with the tsx from the prototype repo's node_modules; this repo has no
 * Node toolchain of its own.
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {join, resolve} from 'node:path';

type Case = {name: string; input: Record<string, unknown>; expected: Record<string, unknown>};

const protoRoot = process.argv[2];
const outDir = process.argv[3];
if (!protoRoot || !outDir) {
  console.error('usage: tsx export_crmv2_fixtures.ts <PROTOTYPE_REPO> <OUTPUT_DIR>');
  process.exit(2);
}

const srcUrl = (rel: string) => pathToFileURL(resolve(protoRoot, 'src', rel)).href;

const {resolveOwnership} = await import(srcUrl('domain/ownership.ts'));
const {recomputeMissedChannel} = await import(srcUrl('domain/missed-channel.ts'));
const {matchesLastOrderAge, LAST_ORDER_AGE_PRESETS} = await import(srcUrl('read-model/last-order-age.ts'));

/** The prototype's own test epoch: tests/support/factories.ts. */
const T0 = Date.UTC(2026, 9, 1, 3, 0, 0);
const DAY = 86_400_000;

const CARETAKER = 'user-ts-crm-1';
const OTHER = 'user-ts-upsell-1';
const RESIGNED = 'user-ts-resigned';
const ACTIVE = [CARETAKER, OTHER, 'user-lead-crm'];

// --- ownership ------------------------------------------------------------
const POLICIES = ['EXCLUSIVE', 'LATEST_ORDER', 'LATEST_ORDER_WITH_LOCK'] as const;

const SCENARIOS: Array<{
  label: string;
  currentCaretakerUserId: string | null;
  closerUserId: string | null;
  lockActive: boolean;
  lifecycleStatus: string;
}> = [
  {label: 'no-caretaker', currentCaretakerUserId: null, closerUserId: CARETAKER, lockActive: false, lifecycleStatus: 'NEW'},
  {label: 'same-closer', currentCaretakerUserId: CARETAKER, closerUserId: CARETAKER, lockActive: false, lifecycleStatus: 'FOLLOWING'},
  {label: 'other-closer', currentCaretakerUserId: CARETAKER, closerUserId: OTHER, lockActive: false, lifecycleStatus: 'FOLLOWING'},
  {label: 'null-closer', currentCaretakerUserId: CARETAKER, closerUserId: null, lockActive: false, lifecycleStatus: 'FOLLOWING'},
  {label: 'lock-active', currentCaretakerUserId: CARETAKER, closerUserId: OTHER, lockActive: true, lifecycleStatus: 'WAITING_CUSTOMER'},
  {label: 'stopped', currentCaretakerUserId: CARETAKER, closerUserId: OTHER, lockActive: false, lifecycleStatus: 'STOPPED'},
  {label: 'resigned-closer-no-caretaker', currentCaretakerUserId: null, closerUserId: RESIGNED, lockActive: false, lifecycleStatus: 'NEW'},
  {label: 'resigned-closer-held', currentCaretakerUserId: CARETAKER, closerUserId: RESIGNED, lockActive: false, lifecycleStatus: 'FOLLOWING'},
];

const ownershipCases: Case[] = [];
for (const policy of POLICIES) {
  for (const scenario of SCENARIOS) {
    const input = {
      policy,
      teamId: 'team-crm',
      currentCaretakerUserId: scenario.currentCaretakerUserId,
      ownershipStartedAt: scenario.currentCaretakerUserId === null ? null : T0,
      lifecycleStatus: scenario.lifecycleStatus,
      lockActive: scenario.lockActive,
      order: {
        orderId: '700010',
        orderedAt: T0 + 1000,
        closerUserId: scenario.closerUserId,
        closerTeamId: scenario.closerUserId === null ? null : 'team-crm',
      },
      activeUserIds: ACTIVE,
    };
    const decision = resolveOwnership({...input, activeUserIds: new Set(ACTIVE)});
    ownershipCases.push({
      name: `${policy}/${scenario.label}`,
      input,
      expected: {
        caretakerUserId: decision.caretakerUserId,
        changed: decision.changed,
        newSegmentStartedAt: decision.newSegmentStartedAt,
        reason: decision.reason,
        conflict: decision.conflict
          ? {kind: decision.conflict.kind, blocking: decision.conflict.blocking, options: decision.conflict.options}
          : null,
      },
    });
  }
}

// --- missed channel -------------------------------------------------------
const mcOrder = (orderId: string, offsetMs: number, closerUserId: string | null, orderStatus = 'DELIVERED') => ({
  orderId,
  orderedAt: T0 + offsetMs,
  closerUserId,
  orderStatus,
});

const MC_INPUTS: Array<{name: string; input: Record<string, unknown>}> = [
  {name: 'two-marketplace-orders-no-closer', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('b', 2, null)]}},
  {name: 'caretaker-close-resets-to-zero', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('b', 2, OTHER), mcOrder('c', 3, CARETAKER)]}},
  {name: 'other-closers-same-or-different-team', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, OTHER), mcOrder('b', 2, 'user-lead-crm')]}},
  {name: 'cancelled-and-cod-returned-excluded', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null, 'CANCELLED'), mcOrder('b', 2, null, 'COD_RETURNED'), mcOrder('c', 3, null)]}},
  {name: 'before-baseline-ignored', input: {baselineStartedAt: T0 + 10, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('b', 11, null)]}},
  {name: 'duplicate-order-ids-do-not-inflate', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('a', 1, null)]}},
  {name: 'tied-timestamps-sort-by-order-id', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('b', 1, OTHER), mcOrder('a', 1, OTHER)]}},
  {name: 'tied-timestamps-reversed-input', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, OTHER), mcOrder('b', 1, OTHER)]}},
  {name: 'no-caretaker-counts-every-valid-order', input: {baselineStartedAt: T0, caretakerUserId: null, orders: [mcOrder('a', 1, CARETAKER), mcOrder('b', 2, null)]}},
  {name: 'policy-disabled-returns-zero', input: {baselineStartedAt: T0, caretakerUserId: null, orders: [mcOrder('a', 1, null)], policy: {enabled: false}}},
  {name: 'policy-does-not-count-null-closer', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('b', 2, OTHER)], policy: {countNullCloser: false}}},
  {name: 'policy-no-reset-on-caretaker-close', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('a', 1, null), mcOrder('b', 2, CARETAKER)], policy: {resetOnCaretakerClose: false}}},
  {name: 'shuffled-history-settles-identically', input: {baselineStartedAt: T0, caretakerUserId: CARETAKER, orders: [mcOrder('e', 5, OTHER), mcOrder('d', 4, null), mcOrder('c', 3, CARETAKER), mcOrder('b', 2, OTHER), mcOrder('a', 1, null)]}},
];

const missedChannelCases: Case[] = MC_INPUTS.map(({name, input}) => {
  const result = recomputeMissedChannel(input as never);
  return {
    name,
    input,
    expected: {
      count: result.count,
      explanations: result.explanations.map((e: Record<string, unknown>) => ({
        orderId: e.orderId,
        orderedAt: e.orderedAt,
        closerUserId: e.closerUserId,
        counted: e.counted,
        reason: e.reason,
      })),
    },
  };
});

// --- last order age -------------------------------------------------------
/** Bangkok noon, so a whole-day offset never straddles a day boundary. */
const NOW = Date.UTC(2026, 10, 1, 5, 0, 0);

const lastOrderAgeCases: Case[] = [];
for (const preset of LAST_ORDER_AGE_PRESETS as ReadonlyArray<Record<string, unknown>>) {
  const id = preset.id as string;
  if (preset.noOrder) {
    for (const [label, lastOrderAt] of [['null', null], ['recent', NOW - 3 * DAY]] as const) {
      lastOrderAgeCases.push({
        name: `${id}/${label}`,
        input: {lastOrderAt, preset: id, minDays: null, maxDays: null, now: NOW},
        expected: {matches: matchesLastOrderAge(lastOrderAt, {preset: id}, NOW)},
      });
    }
    continue;
  }
  const minDays = preset.minDays as number;
  const maxDays = preset.maxDays as number | undefined;
  const probes: Array<[string, number]> = [['at-min', minDays], ['below-min', minDays - 1]];
  if (maxDays != null) probes.push(['at-max', maxDays], ['above-max', maxDays + 1]);
  else probes.push(['far-above-min', minDays + 1000]);
  for (const [label, days] of probes) {
    if (days < 0) continue;
    const lastOrderAt = NOW - days * DAY;
    lastOrderAgeCases.push({
      name: `${id}/${label}-${days}d`,
      input: {lastOrderAt, preset: id, minDays: null, maxDays: null, now: NOW},
      expected: {matches: matchesLastOrderAge(lastOrderAt, {preset: id}, NOW)},
    });
  }
  // A null last order must never satisfy a numeric bucket.
  lastOrderAgeCases.push({
    name: `${id}/no-order`,
    input: {lastOrderAt: null, preset: id, minDays: null, maxDays: null, now: NOW},
    expected: {matches: matchesLastOrderAge(null, {preset: id}, NOW)},
  });
}
// Custom ranges, with no preset at all.
for (const [label, minDays, maxDays, days] of [
  ['custom-inside', 10, 20, 15],
  ['custom-at-min', 10, 20, 10],
  ['custom-at-max', 10, 20, 20],
  ['custom-below', 10, 20, 9],
  ['custom-above', 10, 20, 21],
  ['custom-open-ended', 30, null, 365],
] as Array<[string, number, number | null, number]>) {
  const lastOrderAt = NOW - days * DAY;
  const filter: Record<string, number> = {minDays};
  if (maxDays != null) filter.maxDays = maxDays;
  lastOrderAgeCases.push({
    name: `${label}-${days}d`,
    input: {lastOrderAt, preset: null, minDays, maxDays, now: NOW},
    expected: {matches: matchesLastOrderAge(lastOrderAt, filter, NOW)},
  });
}
// No filter at all matches everything, including a lead with no order.
lastOrderAgeCases.push({
  name: 'no-filter/null-last-order',
  input: {lastOrderAt: null, preset: null, minDays: null, maxDays: null, now: NOW},
  expected: {matches: matchesLastOrderAge(null, undefined, NOW)},
});

// --- write ----------------------------------------------------------------
mkdirSync(outDir, {recursive: true});
const files: Array<[string, Case[]]> = [
  ['ownership', ownershipCases],
  ['missed_channel', missedChannelCases],
  ['last_order_age', lastOrderAgeCases],
];
for (const [name, cases] of files) {
  writeFileSync(join(outDir, `${name}.json`), `${JSON.stringify(cases, null, 2)}\n`, 'utf-8');
  console.log(`${name}.json: ${cases.length} cases`);
}
