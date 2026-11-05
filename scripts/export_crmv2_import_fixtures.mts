/**
 * Export the CRM V2 prototype's import rules as language-neutral parity fixtures.
 *
 * Same contract as export_crmv2_fixtures.mts: every `expected` value is produced
 * by EXECUTING the prototype's own functions, never transcribed. Covers the pure
 * parts of src/import: state machine, hashing, confirm gate, batch business
 * date (D3) and conflict-resolver scope.
 *
 * Usage:
 *   node --import tsx scripts/export_crmv2_import_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>
 *
 * Run it with the tsx from the prototype repo's node_modules (cwd = the prototype).
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {join, resolve} from 'node:path';

type Case = {name: string; input: Record<string, unknown>; expected: Record<string, unknown>};

const protoRoot = process.argv[2];
const outDir = process.argv[3];
if (!protoRoot || !outDir) {
  console.error('usage: tsx export_crmv2_import_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>');
  process.exit(2);
}
const srcUrl = (rel: string) => pathToFileURL(resolve(protoRoot, 'src', rel)).href;

const sm = await import(srcUrl('import/state-machine.ts'));
const hashing = await import(srcUrl('import/hashing.ts'));
const confirm = await import(srcUrl('import/confirm.ts'));
const tele = await import(srcUrl('import/tele-date.ts'));
const resolution = await import(srcUrl('import/conflict-resolution.ts'));
const enums = await import(srcUrl('domain/enums.ts'));

const capture = <T,>(fn: () => T): {value: T} | {error: {name: string; code: string | null; from?: string; to?: string}} => {
  try {
    return {value: fn()};
  } catch (e) {
    const err = e as {name?: string; code?: string; from?: string; to?: string; blockers?: Array<{code: string}>};
    return {
      error: {
        name: err.name ?? 'Error',
        code: err.code ?? null,
        ...(err.from ? {from: err.from} : {}),
        ...(err.to ? {to: err.to} : {}),
        ...(err.blockers ? {blockerCodes: err.blockers.map(b => b.code)} : {}),
      } as never,
    };
  }
};

// --- state machine ----------------------------------------------------------
const STATUSES: string[] = [...enums.IMPORT_BATCH_STATUSES];
const stateCases: Case[] = [];
for (const from of STATUSES) {
  for (const to of STATUSES) {
    stateCases.push({
      name: `can/${from}->${to}`,
      input: {fn: 'canTransition', from, to},
      expected: {value: sm.canTransition(from, to)},
    });
  }
  stateCases.push({name: `terminal/${from}`, input: {fn: 'isTerminal', status: from}, expected: {value: sm.isTerminal(from)}});
  stateCases.push({name: `retry/${from}`, input: {fn: 'canRetryCommit', status: from}, expected: {value: sm.canRetryCommit(from)}});
  stateCases.push({name: `rollback/${from}`, input: {fn: 'needsRollback', status: from}, expected: {value: sm.needsRollback(from)}});
}
const guardContexts: Array<[string, Record<string, unknown>]> = [
  ['no-confirm', {actor: 'u', at: 1}],
  ['confirmed-clean', {actor: 'u', at: 1, confirmedBy: 'u', blockingConflictsOpen: 0}],
  ['confirmed-blocking', {actor: 'u', at: 1, confirmedBy: 'u', blockingConflictsOpen: 2}],
  ['blocking-no-confirm', {actor: 'u', at: 1, blockingConflictsOpen: 1}],
];
for (const from of STATUSES) {
  for (const to of STATUSES) {
    for (const [label, context] of guardContexts) {
      stateCases.push({
        name: `assert/${from}->${to}/${label}`,
        input: {fn: 'assertTransition', from, to, context},
        expected: capture(() => {
          sm.assertTransition(from, to, context);
          return true;
        }) as never,
      });
    }
  }
}
const baseBatch = (status: string, extra: Record<string, unknown> = {}) => ({
  status,
  transitionHistory: [] as unknown[],
  attemptCount: 0,
  confirmedBy: null,
  confirmedAt: null,
  committedAt: null,
  rolledBackAt: null,
  ...extra,
});
for (const [from, to, extra, context] of [
  ['AWAITING_RESOLUTION', 'COMMITTING', {}, {actor: 'admin', at: 1000, confirmedBy: 'admin', reason: 'go'}],
  ['AWAITING_RESOLUTION', 'AWAITING_RESOLUTION', {}, {actor: 'lead', at: 2000, reason: 'resolved one'}],
  ['FAILED_RETRYABLE', 'COMMITTING', {attemptCount: 1, confirmedBy: 'admin', confirmedAt: 500}, {actor: 'admin', at: 3000}],
  ['COMMITTING', 'COMMITTED', {attemptCount: 1}, {actor: 'system', at: 4000}],
  ['ROLLING_BACK', 'ROLLED_BACK', {}, {actor: 'admin', at: 5000, reason: 'rollback'}],
  ['COMMITTED', 'COMMITTING', {}, {actor: 'admin', at: 6000, confirmedBy: 'admin'}],
] as Array<[string, string, Record<string, unknown>, Record<string, unknown>]>) {
  const batch = baseBatch(from, extra);
  stateCases.push({
    name: `apply/${from}->${to}`,
    input: {fn: 'applyTransition', batch, to, context},
    expected: capture(() => sm.applyTransition(batch, to, context)) as never,
  });
}

// --- hashing ----------------------------------------------------------------
const hashValues: Array<[string, unknown]> = [
  ['null', null],
  ['string', 'ทดสอบ "quote"'],
  ['int', 42],
  ['float-integral', 3.0],
  ['float', 1.5],
  ['bool', true],
  ['array', [1, 'a', null, {b: 2, a: 1}]],
  ['key-order-lower', {b: 1, a: 2, c: 3}],
  ['key-order-mixed-case', {Bb: 1, aa: 2, AA: 3, ab: 4, B: 5, _x: 6, '1k': 7, k1: 8}],
  ['nested', {z: {y: [1, {x: null}], a: 'ก'}, m: 'v'}],
  ['thai-keys', {ข: 1, ก: 2}],
];
const hashCases: Case[] = [];
for (const [label, value] of hashValues) {
  hashCases.push({
    name: `stable/${label}`,
    input: {fn: 'stableStringify', value},
    expected: {value: hashing.stableStringify(value), sha256: hashing.sha256Hex(hashing.stableStringify(value))},
  });
}
for (const [label, data, fields] of [
  ['exists-all', {a: 1, b: 2}, ['a', 'b']],
  ['missing-field', {a: 1}, ['a', 'b']],
  ['null-doc', null, ['a', 'b']],
  ['empty-doc', {}, ['a']],
  ['null-value-vs-missing', {a: null}, ['a', 'b']],
] as Array<[string, Record<string, unknown> | null, string[]]>) {
  hashCases.push({
    name: `fields/${label}`,
    input: {fn: 'hashFields', data, fields},
    expected: {value: hashing.hashFields(data, fields)},
  });
}
const previewBase = {
  fileSha256: 'f'.repeat(64),
  mappingProfileId: 'crm-customers',
  mappingProfileVersion: 1,
  batchBusinessDate: null as string | null,
  rows: [{rowIndex: 1, orderId: 'SO-1', changes: {phone: '081', name: 'ก'}}],
};
for (const [label, input] of [
  ['base', previewBase],
  ['with-date', {...previewBase, batchBusinessDate: '2026-10-26'}],
  ['two-rows', {...previewBase, rows: [...previewBase.rows, {rowIndex: 2, orderId: 'SO-2', changes: {}}]}],
  ['decisions', {...previewBase, decisions: [{conflictId: 'c1', choice: 'KEEP_CURRENT'}]}],
  ['extra-key-ignored', {...previewBase, rows: [{rowIndex: 1, orderId: 'SO-1', changes: {}, ignored: true}]}],
] as Array<[string, Record<string, unknown>]>) {
  hashCases.push({name: `preview/${label}`, input: {fn: 'computePreviewHash', value: input}, expected: {value: confirm.computePreviewHash(input)}});
}
hashCases.push({
  name: 'next-resolution-hash/first',
  input: {fn: 'nextResolutionPreviewHash', previous: null, decision: {conflictId: 'c1', version: 1, resolution: 'KEEP_CURRENT'}},
  expected: {value: resolution.nextResolutionPreviewHash(null, {conflictId: 'c1', version: 1, resolution: 'KEEP_CURRENT'})},
});
hashCases.push({
  name: 'next-resolution-hash/chained',
  input: {fn: 'nextResolutionPreviewHash', previous: 'abc', decision: {conflictId: 'c2', version: 3, resolution: 'REASSIGN'}},
  expected: {value: resolution.nextResolutionPreviewHash('abc', {conflictId: 'c2', version: 3, resolution: 'REASSIGN'})},
});
const conflictBase = {
  conflictId: 'c1', batchId: 'b1', rowIndex: 4, kind: 'OWNERSHIP', severity: 'BLOCKING', status: 'OPEN',
  teamId: 'team-crm', affectedTeamIds: ['team-crm'], summary: 'Caretaker ปัจจุบันต่างจากไฟล์นำเข้า',
  sourceRowRef: 'row:4', options: ['KEEP_CURRENT', 'REASSIGN'], version: 1, resolution: null,
};
for (const [label, conflict] of [
  ['base', conflictBase],
  ['resolved', {...conflictBase, status: 'RESOLVED', version: 2, resolution: 'REASSIGN'}],
  ['missing-optional', (({resolution: _r, ...rest}) => rest)(conflictBase)],
  ['extra-field-ignored', {...conflictBase, createdAt: 123}],
] as Array<[string, Record<string, unknown>]>) {
  hashCases.push({name: `conflict-hash/${label}`, input: {fn: 'conflictVersionHash', conflict}, expected: {value: resolution.conflictVersionHash(conflict)}});
}

// --- confirm gate -----------------------------------------------------------
const confirmBatch = {
  status: 'AWAITING_RESOLUTION', mappingProfileId: 'crm-customers', mappingProfileVersion: 1,
  fileSha256: 'f'.repeat(64), previewHash: 'p'.repeat(64),
};
const confirmedDate = {dayKey: '2026-10-26', timezone: 'Asia/Bangkok', source: 'OPERATOR', confirmedBy: 'admin', confirmedAt: 10, filenameCandidate: null, filenameAmbiguous: false};
const okInput = {
  batch: confirmBatch,
  actor: {userId: 'admin', role: 'ADMIN', teamIds: []},
  profile: {profileId: 'crm-customers', version: 1, status: 'ACTIVE'},
  validation: {ok: true, issues: []},
  conflicts: [],
  staged: {fileSha256: 'f'.repeat(64), mappingProfileId: 'crm-customers', mappingProfileVersion: 1, previewHash: 'p'.repeat(64)},
  requiresBatchBusinessDate: false,
  batchBusinessDate: null,
};
const merge = (patch: Record<string, unknown>) => ({...okInput, ...patch});
const confirmVariants: Array<[string, Record<string, unknown>]> = [
  ['ok', okInput],
  ['team-lead-not-authorized', merge({actor: {userId: 'tl', role: 'TEAM_LEAD', teamIds: ['t']}})],
  ['telesale-not-authorized', merge({actor: {userId: 'ts', role: 'TELESALE', teamIds: []}})],
  ['preview-state', merge({batch: {...confirmBatch, status: 'PREVIEW'}})],
  ['paused-quota-ok', merge({batch: {...confirmBatch, status: 'PAUSED_QUOTA'}})],
  ['failed-retryable-ok', merge({batch: {...confirmBatch, status: 'FAILED_RETRYABLE'}})],
  ['committed-illegal', merge({batch: {...confirmBatch, status: 'COMMITTED'}})],
  ['profile-missing', merge({profile: null})],
  ['profile-draft', merge({profile: {profileId: 'crm-customers', version: 1, status: 'DRAFT'}})],
  ['profile-retired', merge({profile: {profileId: 'crm-customers', version: 1, status: 'RETIRED'}})],
  ['profile-version-mismatch', merge({profile: {profileId: 'crm-customers', version: 2, status: 'ACTIVE'}})],
  ['profile-id-mismatch', merge({profile: {profileId: 'other', version: 1, status: 'ACTIVE'}})],
  ['validation-not-ok', merge({validation: {ok: false}})],
  ['validation-block-issue', merge({validation: {ok: true, issues: [{severity: 'BLOCK', code: 'X'}, {severity: 'WARNING'}]}})],
  ['validation-warning-only', merge({validation: {ok: true, issues: [{severity: 'WARNING'}]}})],
  ['blocking-open', merge({conflicts: [{severity: 'BLOCKING', status: 'OPEN'}, {severity: 'BLOCKING', status: 'RESOLVED'}, {severity: 'WARNING', status: 'OPEN'}]})],
  ['blocking-resolved', merge({conflicts: [{severity: 'BLOCKING', status: 'RESOLVED'}, {severity: 'BLOCKING', status: 'AUTO_RESOLVED'}]})],
  ['summary-wins', merge({conflicts: [], conflictSummary: {openBlockingConflicts: 3}})],
  ['summary-zero-wins', merge({conflicts: [{severity: 'BLOCKING', status: 'OPEN'}], conflictSummary: {openBlockingConflicts: 0}})],
  ['preview-missing', merge({staged: null})],
  ['staged-file-changed', merge({staged: {...okInput.staged, fileSha256: 'x'.repeat(64)}})],
  ['preview-hash-mismatch', merge({staged: {...okInput.staged, previewHash: 'q'.repeat(64)}})],
  ['batch-has-no-preview-hash', merge({batch: {...confirmBatch, previewHash: ''}})],
  ['staged-profile-mismatch', merge({staged: {...okInput.staged, mappingProfileVersion: 9}})],
  ['date-required-missing', merge({requiresBatchBusinessDate: true, batchBusinessDate: null})],
  ['date-required-unconfirmed', merge({requiresBatchBusinessDate: true, batchBusinessDate: {...confirmedDate, confirmedBy: null, confirmedAt: null}})],
  ['date-required-invalid', merge({requiresBatchBusinessDate: true, batchBusinessDate: {...confirmedDate, dayKey: '2026-13-40'}})],
  ['date-required-ok', merge({requiresBatchBusinessDate: true, batchBusinessDate: confirmedDate})],
  ['everything-wrong', merge({
    actor: {userId: 'ts', role: 'TELESALE', teamIds: []}, batch: {...confirmBatch, status: 'COMMITTED'}, profile: null,
    validation: {ok: false}, conflicts: [{severity: 'BLOCKING', status: 'OPEN'}], staged: null,
    requiresBatchBusinessDate: true, batchBusinessDate: null,
  })],
];
const confirmCases: Case[] = confirmVariants.map(([name, input]) => ({
  name: `confirm/${name}`,
  input: {fn: 'evaluateConfirm', value: input},
  expected: {value: confirm.evaluateConfirm(input)},
}));
for (const kind of ['CRM_CUSTOMERS', 'LITE_DETAIL_ORDER', 'INOUT_MANAGE_MAIN', 'TELE_SALES_REPORT', 'CUTOVER_ORDER_SHEET', 'HX63_HISTORICAL', 'PENDING_SOURCE_SAMPLE']) {
  confirmCases.push({name: `requires-date/${kind}`, input: {fn: 'sourceRequiresBatchDate', kind}, expected: {value: confirm.sourceRequiresBatchDate(kind)}});
}
confirmCases.push({name: 'confirm-roles', input: {fn: 'IMPORT_CONFIRM_ROLES'}, expected: {value: [...confirm.IMPORT_CONFIRM_ROLES].sort()}});

// --- tele batch business date ----------------------------------------------
const teleCases: Case[] = [];
const fileNames = [
  'Tele 26.10.69.xlsx', 'Tele_26-10-2569.xlsx', 'tele 2026-10-26.csv', 'tele 2026_10_26.csv', 'Tele 26.10.26.xlsx',
  'Tele 26.10.69 (copy 27.10.69).xlsx', 'no-date.xlsx', 'Tele 31.02.69.xlsx', 'Tele 99.99.99.xlsx', 'C:\\data\\Tele 05.11.69.xlsx',
  '/uploads/Tele 05-11-2569.xlsx', 'order 12345678.xlsx', 'Tele 26.10.69 26.10.69.xlsx', 'report 2026-10-26 2026-10-26.csv',
  'Tele 1.1.51.xlsx', 'Tele 1.1.50.xlsx', 'Tele ๒๖.๑๐.๖๙.xlsx', 'x 26.10.2400.xlsx', 'x 26.10.1899.xlsx', 'x 26.10.2201.xlsx',
];
for (const fileName of fileNames) {
  teleCases.push({name: `parse/${fileName}`, input: {fn: 'parseFilenameDate', fileName}, expected: {value: tele.parseFilenameDate(fileName)}});
  for (const evidence of [undefined, [], ['2026-10-26'], ['2026-10-27'], ['2026-10-26', '2026-10-27']] as Array<string[] | undefined>) {
    teleCases.push({
      name: `propose/${fileName}/${JSON.stringify(evidence ?? null)}`,
      input: {fn: 'proposeBatchBusinessDate', fileName, sourceEvidenceDayKeys: evidence},
      expected: {value: tele.proposeBatchBusinessDate({fileName, sourceEvidenceDayKeys: evidence})},
    });
  }
}
for (const [fileName, operatorDayKey, evidence] of [
  ['Tele 26.10.69.xlsx', undefined, undefined],
  ['Tele 26.10.69.xlsx', '', undefined],
  ['Tele 26.10.69.xlsx', '2026-10-26', undefined],
  ['Tele 26.10.69.xlsx', '2026-10-27', undefined],
  ['Tele 26.10.69.xlsx', '2026-10-27', ['2026-10-26']],
  ['Tele 26.10.69.xlsx', '2026-10-26', ['2026-10-26']],
  ['no-date.xlsx', undefined, undefined],
  ['no-date.xlsx', '2026-10-26', undefined],
  ['no-date.xlsx', 'not-a-date', undefined],
  ['no-date.xlsx', '2026-02-30', undefined],
  ['Tele 26.10.69 (copy 27.10.69).xlsx', '2026-10-26', undefined],
  ['Tele 26.10.69 (copy 27.10.69).xlsx', undefined, undefined],
  ['no-date.xlsx', '  2026-10-26  ', undefined],
] as Array<[string, string | undefined, string[] | undefined]>) {
  const input = {fileName, operatorDayKey, actor: 'admin', at: 1730000000000, sourceEvidenceDayKeys: evidence};
  teleCases.push({
    name: `confirm/${fileName}/${operatorDayKey ?? 'none'}/${JSON.stringify(evidence ?? null)}`,
    input: {fn: 'confirmBatchBusinessDate', ...input},
    expected: capture(() => tele.confirmBatchBusinessDate(input)) as never,
  });
}
for (const text of ['2026-10-26', '2026-02-29', '2024-02-29', '2569-10-26', '2026-1-1', ' 2026-10-26 ', '2026/10/26', '1899-01-01', '2201-01-01', '2400-01-01', '2401-01-01']) {
  teleCases.push({name: `normalizeDayKey/${text}`, input: {fn: 'normalizeDayKey', text}, expected: {value: tele.normalizeDayKey(text)}});
}
for (const [year, digits] of [[69, 2], [26, 2], [50, 2], [51, 2], [0, 2], [99, 2], [100, 2], [-1, 2], [2569, 4], [2026, 4], [2400, 4], [2401, 4], [1899, 4], [2201, 4], [2743, 4]] as Array<[number, 2 | 4]>) {
  teleCases.push({name: `normalizeYear/${year}/${digits}`, input: {fn: 'normalizeYear', year, digits}, expected: {value: tele.normalizeYear(year, digits)}});
}
for (const [y, m, d] of [[2026, 10, 26], [2026, 2, 29], [2024, 2, 29], [2026, 4, 31], [2026, 0, 1], [2026, 13, 1], [2026, 1, 32], [2026, 1, 0], [2026.5, 1, 1]] as Array<[number, number, number]>) {
  teleCases.push({name: `dayKeyOf/${y}-${m}-${d}`, input: {fn: 'dayKeyOf', year: y, month: m, day: d}, expected: {value: tele.dayKeyOf(y, m, d)}});
}
for (const [dayKey, time] of [['2026-10-26', '09:30'], ['2026-10-26', '9:05'], ['2026-10-26', '00:00'], ['2026-10-26', '23:59'], ['2026-10-26', '24:00'], ['2026-10-26', '12:60'], ['2026-10-26', '9.30'], ['2026-10-26', ' 9:30 '], ['2026-02-30', '09:30'], ['nope', '09:30'], ['2026-10-26', '']] as Array<[string, string]>) {
  teleCases.push({
    name: `buildOrderedAt/${dayKey}/${time}`,
    input: {fn: 'buildOrderedAt', dayKey, timeText: time},
    expected: capture(() => tele.buildOrderedAt(dayKey, time)) as never,
  });
}
for (const [label, value] of [
  ['null', null], ['undefined', undefined],
  ['unconfirmed', {...confirmedDate, confirmedBy: null, confirmedAt: null}],
  ['no-time', {...confirmedDate, confirmedAt: null}],
  ['bad-day', {...confirmedDate, dayKey: '2026-99-99'}],
  ['ok', confirmedDate],
] as Array<[string, unknown]>) {
  teleCases.push({
    name: `assertConfirmedBatchDate/${label}`,
    input: {fn: 'assertConfirmedBatchDate', value: value === undefined ? null : value},
    expected: capture(() => tele.assertConfirmedBatchDate(value as never)) as never,
  });
}

// --- conflict scope ----------------------------------------------------------
const scopeCases: Case[] = [];
const conflictVariants: Array<[string, Record<string, unknown>]> = [
  ['own-team', {kind: 'OWNERSHIP', teamId: 't1', affectedTeamIds: ['t1']}],
  ['own-team-no-affected', {kind: 'OWNERSHIP', teamId: 't1', affectedTeamIds: []}],
  ['other-team', {kind: 'OWNERSHIP', teamId: 't2', affectedTeamIds: ['t2']}],
  ['multi-team', {kind: 'OWNERSHIP', teamId: 't1', affectedTeamIds: ['t1', 't2']}],
  ['duplicated-same-team', {kind: 'STALE', teamId: 't1', affectedTeamIds: ['t1', 't1']}],
  ['no-team', {kind: 'OWNERSHIP', teamId: null, affectedTeamIds: []}],
  ['identity', {kind: 'IDENTITY', teamId: 't1', affectedTeamIds: ['t1']}],
  ['reactivation', {kind: 'REACTIVATION', teamId: 't1', affectedTeamIds: ['t1']}],
  ['affected-overrides-teamId', {kind: 'OWNERSHIP', teamId: 't1', affectedTeamIds: ['t2']}],
];
const actors: Array<[string, Record<string, unknown>]> = [
  ['admin', {userId: 'a', role: 'ADMIN', teamIds: []}],
  ['team-lead-t1', {userId: 'l', role: 'TEAM_LEAD', teamIds: ['t1']}],
  ['team-lead-t1-t2', {userId: 'l2', role: 'TEAM_LEAD', teamIds: ['t1', 't2']}],
  ['team-lead-none', {userId: 'l3', role: 'TEAM_LEAD', teamIds: []}],
  ['telesale', {userId: 't', role: 'TELESALE', teamIds: ['t1']}],
];
for (const [actorLabel, actor] of actors) {
  for (const [conflictLabel, conflict] of conflictVariants) {
    scopeCases.push({
      name: `scope/${actorLabel}/${conflictLabel}`,
      input: {fn: 'assertResolverScope', actor, conflict},
      expected: capture(() => {
        resolution.assertResolverScope(actor, conflict);
        return true;
      }) as never,
    });
  }
}

// --- write -------------------------------------------------------------------
mkdirSync(outDir, {recursive: true});
const files: Array<[string, Case[]]> = [
  ['import_state', stateCases],
  ['import_hashing', hashCases],
  ['import_confirm', confirmCases],
  ['import_tele_date', teleCases],
  ['import_conflict_scope', scopeCases],
];
for (const [name, cases] of files) {
  writeFileSync(join(outDir, `${name}.json`), `${JSON.stringify(cases, null, 2)}\n`, 'utf-8');
  console.log(`${name}.json: ${cases.length} cases`);
}
