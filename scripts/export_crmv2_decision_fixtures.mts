/**
 * Export the CRM V2 prototype's Migration Decision rules as parity fixtures
 * (every `expected` value is produced by EXECUTING the prototype).
 *
 * Usage (cwd = the prototype repo):
 *   node --import tsx scripts/export_crmv2_decision_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {join, resolve} from 'node:path';

type Case = {name: string; input: Record<string, unknown>; expected: Record<string, unknown>};

const [protoRoot, outDir] = process.argv.slice(2);
if (!protoRoot || !outDir) {
  console.error('usage: tsx export_crmv2_decision_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>');
  process.exit(2);
}
const srcUrl = (rel: string) => pathToFileURL(resolve(protoRoot, 'src', rel)).href;
const decisions = await import(srcUrl('migration/decisions.ts'));

const capture = <T,>(fn: () => T) => {
  try {
    return {value: fn()};
  } catch (e) {
    const err = e as {name?: string; code?: string};
    return {error: {name: err.name ?? 'Error', code: err.code ?? null}};
  }
};

const USERS: Record<string, {userId: string; role: string; active: boolean}> = {
  u1: {userId: 'u1', role: 'TELESALE', active: true},
  u2: {userId: 'u2', role: 'TELESALE', active: false},
  lead: {userId: 'lead', role: 'TEAM_LEAD', active: true},
  admin: {userId: 'admin', role: 'ADMIN', active: true},
};
const lookup = (id: string) => (USERS[id] ?? null) as never;

const staffEvidence = {
  kind: 'STAFF_ALIAS', historicalDisplayValue: 'พนักงานเดิม 5', normalizedValue: 'พนักงานเดิม 5', frequency: 200,
  sourceContext: {crm: 150, upsell: 50}, exactCandidates: [{userId: 'u1', active: true, basis: 'EXACT_DISPLAY_NAME'}],
};
const identityEvidence = {
  kind: 'IDENTITY_REVIEW', normalizedPhone: '0812345678', nameVariants: [{name: 'ก', normalizedName: 'ก', orderCount: 2, firstOrderAt: 1, lastOrderAt: 2}],
  nameVariantTotal: 1, similarities: [], sourceOrderCount: 2, sampleOrderIds: ['SO-1'], candidates: [{customerId: 'c1', displayName: 'ก', similarity: 0.9}],
};
const noPhoneEvidence = {...identityEvidence, normalizedPhone: '', candidates: []};
const statusEvidence = {
  kind: 'UNKNOWN_STATUS', rawValue: 'แปลก', count: 3, sourceKind: 'HX63_HISTORICAL', sampleOrderIds: ['SO-1'],
  fallback: {orderStatus: 'PENDING_CONFIRMATION', paymentStatus: 'UNPAID', shippingStatus: 'NOT_SHIPPED'},
};
const dateEvidence = {kind: 'NULL_DATE_ORDER', sourceOrderId: 'SO-9', sourceKind: 'HX63_HISTORICAL', hasPhone: true, itemCount: 2, total: 100, rawStatus: 'สำเร็จ'};

const subjects: Array<[string, string, string, Record<string, unknown>]> = [
  ['staff', 'STAFF_ALIAS', 'NO_EXACT_CANDIDATE', staffEvidence],
  ['identity', 'IDENTITY_REVIEW', 'NAME_CONFLICT', identityEvidence],
  ['identity-blank-name', 'IDENTITY_REVIEW', 'BLANK_NAME', identityEvidence],
  ['identity-no-phone', 'IDENTITY_REVIEW', 'NO_PHONE', noPhoneEvidence],
  ['status', 'UNKNOWN_STATUS', 'UNKNOWN_VALUE', statusEvidence],
  ['date', 'NULL_DATE_ORDER', 'NULL_DATE', dateEvidence],
];
const decisionOf = (category: string, reasonCode: string, evidence: Record<string, unknown>) => ({category, reasonCode, evidence});

const ALL_ACTIONS = ['MAP_TO_USER', 'HISTORICAL_ONLY', 'DEFER', 'MATCH_EXISTING', 'CREATE_NEW_CUSTOMER', 'KEEP_SEPARATE', 'MAP_TO_STATUS', 'ARCHIVE_ONLY', 'SET_AUTHORITATIVE_DATE', 'KEEP_BLOCKED', 'NOPE'];

const cases: Case[] = [];
for (const [label, category, reasonCode] of subjects) {
  cases.push({name: `allowed/${label}`, input: {fn: 'allowedActions', category, reasonCode}, expected: {value: decisions.allowedActions({category, reasonCode})}});
}
for (const action of ALL_ACTIONS) {
  cases.push({name: `nextStatus/${action}`, input: {fn: 'nextStatusFor', action}, expected: {value: decisions.nextStatusFor(action)}});
}
for (const value of ['', '0812345678', '081', '0812', '08123', '0']) {
  cases.push({name: `mask/${value}`, input: {fn: 'maskPhone', value}, expected: {value: decisions.maskPhone(value)}});
}
for (const [label, category, , evidence] of subjects) {
  cases.push({
    name: `evidenceHash/${label}`,
    input: {fn: 'evidenceHashOf', category, subjectKey: `subject-${label}`, evidence},
    expected: {value: decisions.evidenceHashOf(category, `subject-${label}`, evidence)},
  });
  cases.push({
    name: `decisionId/${label}`,
    input: {fn: 'decisionIdFor', category, snapshot: 'snap-1', subjectKey: `subject-${label}`},
    expected: {value: decisions.decisionIdFor(category, 'snap-1', `subject-${label}`)},
  });
}

type Try = [string, string, string, Record<string, unknown>, string, Record<string, unknown> | undefined, string];
const tries: Try[] = [];
const add = (name: string, category: string, reasonCode: string, evidence: Record<string, unknown>, action: string, payload: Record<string, unknown> | undefined, reason = 'เหตุผลทดสอบ') =>
  tries.push([name, category, reasonCode, evidence, action, payload, reason]);

// reasons
for (const reason of ['', 'ab', 'abc', '   ', '  abc  ', 'ก']) add(`reason/${JSON.stringify(reason)}`, 'STAFF_ALIAS', 'NO_EXACT_CANDIDATE', staffEvidence, 'HISTORICAL_ONLY', {}, reason);
// no-payload actions
for (const [action, category, reasonCode, evidence] of [
  ['DEFER', 'STAFF_ALIAS', 'NO_EXACT_CANDIDATE', staffEvidence], ['HISTORICAL_ONLY', 'STAFF_ALIAS', 'NO_EXACT_CANDIDATE', staffEvidence],
  ['CREATE_NEW_CUSTOMER', 'IDENTITY_REVIEW', 'NAME_CONFLICT', identityEvidence], ['KEEP_SEPARATE', 'IDENTITY_REVIEW', 'NAME_CONFLICT', identityEvidence],
  ['KEEP_BLOCKED', 'NULL_DATE_ORDER', 'NULL_DATE', dateEvidence], ['ARCHIVE_ONLY', 'UNKNOWN_STATUS', 'UNKNOWN_VALUE', statusEvidence], ['ARCHIVE_ONLY', 'NULL_DATE_ORDER', 'NULL_DATE', dateEvidence],
] as Array<[string, string, string, Record<string, unknown>]>) {
  add(`${category}/${action}/empty`, category, reasonCode, evidence, action, {});
  add(`${category}/${action}/undefined-payload`, category, reasonCode, evidence, action, undefined);
  add(`${category}/${action}/with-payload`, category, reasonCode, evidence, action, {x: 1});
}
// actions not allowed for the category / reason
for (const action of ALL_ACTIONS) {
  for (const [label, category, reasonCode, evidence] of subjects) add(`gate/${label}/${action}`, category, reasonCode, evidence, action, {});
}
// MAP_TO_USER
for (const [label, payload] of [['u1', {userId: 'u1'}], ['inactive', {userId: 'u2'}], ['admin', {userId: 'admin'}], ['team-lead', {userId: 'lead'}], ['ghost', {userId: 'ghost'}],
  ['empty', {userId: ''}], ['missing', {}], ['number', {userId: 5}]] as Array<[string, Record<string, unknown>]>) {
  add(`MAP_TO_USER/${label}`, 'STAFF_ALIAS', 'NO_EXACT_CANDIDATE', staffEvidence, 'MAP_TO_USER', payload);
}
// MATCH_EXISTING
for (const [label, payload] of [['candidate', {customerId: 'c1'}], ['stranger', {customerId: 'zzz'}], ['missing', {}], ['number', {customerId: 1}]] as Array<[string, Record<string, unknown>]>) {
  add(`MATCH_EXISTING/${label}`, 'IDENTITY_REVIEW', 'NAME_CONFLICT', identityEvidence, 'MATCH_EXISTING', payload);
}
add('MATCH_EXISTING/no-phone-forbidden', 'IDENTITY_REVIEW', 'NO_PHONE', noPhoneEvidence, 'MATCH_EXISTING', {customerId: 'c1'});
add('CREATE_NEW/no-phone-forbidden', 'IDENTITY_REVIEW', 'NO_PHONE', noPhoneEvidence, 'CREATE_NEW_CUSTOMER', {});
add('KEEP_SEPARATE/no-phone-ok', 'IDENTITY_REVIEW', 'NO_PHONE', noPhoneEvidence, 'KEEP_SEPARATE', {});
// MAP_TO_STATUS
for (const [label, payload] of [
  ['valid', {orderStatus: 'SHIPPED', paymentStatus: 'PAID', shippingStatus: 'IN_TRANSIT'}],
  ['missing-axis', {orderStatus: 'SHIPPED', paymentStatus: 'PAID'}],
  ['bad-value', {orderStatus: 'NOPE', paymentStatus: 'PAID', shippingStatus: 'IN_TRANSIT'}],
  ['extra-key', {orderStatus: 'SHIPPED', paymentStatus: 'PAID', shippingStatus: 'IN_TRANSIT', x: 1}],
  ['empty', {}],
] as Array<[string, Record<string, unknown>]>) add(`MAP_TO_STATUS/${label}`, 'UNKNOWN_STATUS', 'UNKNOWN_VALUE', statusEvidence, 'MAP_TO_STATUS', payload);
// SET_AUTHORITATIVE_DATE
for (const [label, payload] of [
  ['valid', {orderedAt: 1_700_000_000_000, evidenceRef: 'ใบส่งของ 123'}],
  ['short-ref', {orderedAt: 1_700_000_000_000, evidenceRef: 'ab'}],
  ['padded-ref', {orderedAt: 1_700_000_000_000, evidenceRef: '  abc  '}],
  ['negative', {orderedAt: -1, evidenceRef: 'abc'}],
  ['fraction', {orderedAt: 1.5, evidenceRef: 'abc'}],
  ['zero', {orderedAt: 0, evidenceRef: 'abc'}],
  ['missing-ref', {orderedAt: 5}],
  ['extra-key', {orderedAt: 5, evidenceRef: 'abc', x: 1}],
  ['long-ref', {orderedAt: 5, evidenceRef: 'x'.repeat(301)}],
] as Array<[string, Record<string, unknown>]>) add(`SET_DATE/${label}`, 'NULL_DATE_ORDER', 'NULL_DATE', dateEvidence, 'SET_AUTHORITATIVE_DATE', payload);

for (const [name, category, reasonCode, evidence, action, payload, reason] of tries) {
  const decision = decisionOf(category, reasonCode, evidence);
  cases.push({
    name: `validate/${name}`,
    input: {fn: 'validateResolution', decision, request: {action, payload: payload ?? null, reason}, users: USERS},
    expected: capture(() => decisions.validateResolution(decision, {action, payload, reason}, lookup)) as never,
  });
}

mkdirSync(outDir, {recursive: true});
writeFileSync(join(outDir, 'import_decisions.json'), `${JSON.stringify(cases, null, 2)}\n`, 'utf-8');
console.log(`import_decisions.json: ${cases.length} cases`);
