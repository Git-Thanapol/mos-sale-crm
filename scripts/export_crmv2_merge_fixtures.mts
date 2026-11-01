/**
 * Export the CRM V2 prototype's identity, status-transition, field-merge and
 * row-evaluation rules as parity fixtures (same contract as the other exporters:
 * every `expected` value is produced by EXECUTING the prototype).
 *
 * Usage (cwd = the prototype repo, so its tsx and node_modules are used):
 *   node --import tsx scripts/export_crmv2_merge_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {join, resolve} from 'node:path';

type Case = {name: string; input: Record<string, unknown>; expected: Record<string, unknown>};

const protoRoot = process.argv[2];
const outDir = process.argv[3];
if (!protoRoot || !outDir) {
  console.error('usage: tsx export_crmv2_merge_fixtures.mts <PROTOTYPE_REPO> <OUTPUT_DIR>');
  process.exit(2);
}
const srcUrl = (rel: string) => pathToFileURL(resolve(protoRoot, 'src', rel)).href;

const phone = await import(srcUrl('domain/phone.ts'));
const identity = await import(srcUrl('domain/identity.ts'));
const status = await import(srcUrl('domain/order-status.ts'));
const merge = await import(srcUrl('domain/order-merge.ts'));
const preview = await import(srcUrl('import/preview.ts'));
const enums = await import(srcUrl('domain/enums.ts'));

const plain = <T,>(value: T): T => JSON.parse(JSON.stringify(value === undefined ? null : value));

// --- phone ------------------------------------------------------------------
const phoneCases: Case[] = [];
const phones: Array<string | number | null | undefined> = [
  '081-234-5678', '+66 81 234 5678', '0066812345678', '812345678', '0812345678', '021234567', '0212345678',
  'abc', '', null, undefined, 812345678, '66812345678', '6681234567', '  081 234 5678  ', '0912345678',
  '0612345678', '0512345678', '0312345678', '12345', '+66 2 123 4567', '00 66 81 234 5678', '081.234.5678',
  '๐๘๑๒๓๔๕๖๗๘', '08123456789', '66 8123 4567',
];
for (const raw of phones) {
  phoneCases.push({name: `normalize/${String(raw)}`, input: {fn: 'normalizePhone', raw: raw === undefined ? null : raw}, expected: {value: plain(phone.normalizePhone(raw))}});
}
for (const [a, b] of [['0812345678', '0812345678'], ['0812345678', '0812345679'], ['0812345678', '0812345699'], ['0812345678', '0899999999'], ['0812345678', '081234567'], ['', '0812345678'], ['0812345678', ''], ['', '']]) {
  const d = phone.digitEditDistance(a, b);
  phoneCases.push({name: `distance/${a}/${b}`, input: {fn: 'digitEditDistance', a, b}, expected: {value: Number.isFinite(d) ? d : 'Infinity'}});
}
for (const value of ['0812345678', '66812345678', '081-234-5678', '12345', 'SO-1234567', '021234567', '0512345678', '+66812345678', '6612345678', '0812345678901', '']) {
  phoneCases.push({name: `looks/${value}`, input: {fn: 'looksLikePhone', value}, expected: {value: phone.looksLikePhone(value)}});
}

// --- identity ----------------------------------------------------------------
const identityCases: Case[] = [];
const names: Array<string | null | undefined> = [
  'นางสาว สมหญิง ใจดี', 'น.ส. สมหญิง ใจดี', 'นาย สมชาย', 'คุณ  สมชาย', 'Mr. John  Smith', 'MRS Jane', 'dr.who', 'Khun Somchai',
  'สมชาย (ลูกค้า)', 'สมชาย-ใจดี_01', null, undefined, '   ', 'นางสมศรี', 'นางสาวนางสมศรี', 'ท่าน สมชาย', 'Miss  Ann-Marie O\'Neil',
  'คุณหญิง สมศรี', 'ด.ช. เด็กชาย', 'ด.ญ. เด็กหญิง', 'MR.', 'ms.  ',
];
for (const name of names) {
  identityCases.push({name: `normalizeName/${String(name)}`, input: {fn: 'normalizeName', raw: name === undefined ? null : name}, expected: {value: identity.normalizeName(name)}});
}
const pairs: Array<[string, string]> = [
  ['สมชาย ใจดี', 'สมชาย ใจดี'], ['นายสมชาย ใจดี', 'สมชาย ใจดี'], ['สมชาย ใจดี', 'สมชาย ใจร้าย'], ['สมชาย', 'สมศรี'], ['สมชาย ใจดี', 'ใจดี สมชาย'],
  ['', 'สมชาย'], ['สมชาย', ''], ['', ''], ['John Smith', 'john smith'], ['John Smith', 'Jon Smyth'], ['ก', 'ข'], ['สมชาย ใจดี มาก', 'สมชาย ใจดี'],
];
for (const [a, b] of pairs) {
  identityCases.push({name: `similarity/${a}/${b}`, input: {fn: 'nameSimilarity', a, b}, expected: {value: identity.nameSimilarity(a, b)}});
}
const cand = (customerId: string, name: string, ...phones: string[]) => ({customerId, name, phones});
const identityScenarios: Array<[string, {name: string; phone: string; candidates: unknown[]}]> = [
  ['no-candidates', {name: 'สมชาย', phone: '0812345678', candidates: []}],
  ['exact-phone-same-name', {name: 'สมชาย ใจดี', phone: '0812345678', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['exact-phone-similar-name', {name: 'นายสมชาย ใจดี', phone: '+66812345678', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['exact-phone-different-name', {name: 'ประเสริฐ', phone: '0812345678', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['strong-name-no-phone', {name: 'สมชาย ใจดี', phone: '0899999999', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['near-phone-similar-name', {name: 'สมชาย ใจดี', phone: '0812345679', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['near-phone-different-name', {name: 'ประเสริฐ', phone: '0812345679', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['unrelated', {name: 'ประเสริฐ', phone: '0600000000', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['two-candidates-prefers-auto', {name: 'สมชาย ใจดี', phone: '0812345678', candidates: [cand('c1', 'สมชาย ใจดี', '0899999999'), cand('c2', 'สมชาย ใจดี', '0812345678')]}],
  ['two-review-picks-higher-name', {name: 'สมชาย ใจดี', phone: '0812345678', candidates: [cand('c1', 'ประเสริฐ', '0812345678'), cand('c2', 'สมชาย ใจร้าย', '0812345678')]}],
  ['candidate-with-two-phones', {name: 'สมชาย ใจดี', phone: '0899999999', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678', '0899999999')]}],
  ['blank-incoming-phone', {name: 'สมชาย ใจดี', phone: '', candidates: [cand('c1', 'สมชาย ใจดี', '0812345678')]}],
  ['blank-everything', {name: '', phone: '', candidates: [cand('c1', '', '')]}],
  ['candidate-without-phone', {name: 'สมชาย ใจดี', phone: '0812345678', candidates: [cand('c1', 'สมชาย ใจดี')]}],
];
for (const [label, input] of identityScenarios) {
  identityCases.push({name: `classify/${label}`, input: {fn: 'classifyIdentity', value: input}, expected: {value: plain(identity.classifyIdentity(input))}});
}
// Name pairs that sit just either side of the 0.82 (similar) and 0.85 (strong) thresholds.
const NEAR = {
  base: 'alpha bravo charlie delta echo',
  similar: 'alpha bravo charlie delta ecno', // ~0.847: similar, not strong
  belowSimilar: 'alpha bravo charlie delto', // ~0.816: just under similar (base4 below)
  base4: 'alpha bravo charlie delta',
  base6: 'alpha bravo charlie delta echo foxtrot',
  strong: 'alpha bravo charlie delta echo foxtrat', // ~0.870: strong
};
for (const [label, a, b] of [
  ['similar', NEAR.base, NEAR.similar],
  ['below-similar', NEAR.base4, NEAR.belowSimilar],
  ['strong', NEAR.base6, NEAR.strong],
] as Array<[string, string, string]>) {
  identityCases.push({name: `similarity-band/${label}`, input: {fn: 'nameSimilarity', a, b}, expected: {value: identity.nameSimilarity(a, b)}});
}
const bandScenarios: Array<[string, {name: string; phone: string; candidates: unknown[]}]> = [
  ['band/exact-phone-similar-name-auto', {name: NEAR.similar, phone: '0812345678', candidates: [cand('c1', NEAR.base, '0812345678')]}],
  ['band/exact-phone-below-similar-review', {name: NEAR.belowSimilar, phone: '0812345678', candidates: [cand('c1', NEAR.base4, '0812345678')]}],
  ['band/no-phone-similar-not-strong-new', {name: NEAR.similar, phone: '0899999999', candidates: [cand('c1', NEAR.base, '0812345678')]}],
  ['band/no-phone-strong-review', {name: NEAR.strong, phone: '0899999999', candidates: [cand('c1', NEAR.base6, '0812345678')]}],
  ['band/near-phone-similar-review', {name: NEAR.similar, phone: '0812345679', candidates: [cand('c1', NEAR.base, '0812345678')]}],
  ['band/near-phone-below-similar-new', {name: NEAR.belowSimilar, phone: '0812345679', candidates: [cand('c1', NEAR.base4, '0812345678')]}],
  ['band/two-digit-near-similar-review', {name: NEAR.similar, phone: '0812345699', candidates: [cand('c1', NEAR.base, '0812345678')]}],
  ['band/three-digit-near-similar-new', {name: NEAR.similar, phone: '0812345999', candidates: [cand('c1', NEAR.base, '0812345678')]}],
];
for (const [label, input] of bandScenarios) {
  identityCases.push({name: `classify/${label}`, input: {fn: 'classifyIdentity', value: input}, expected: {value: plain(identity.classifyIdentity(input))}});
}
for (const kind of enums.IDENTITY_MATCH_KINDS) {
  identityCases.push({name: `blocks/${kind}`, input: {fn: 'identityBlocksCommit', kind}, expected: {value: identity.identityBlocksCommit(kind)}});
}

// --- status transitions --------------------------------------------------------
const statusCases: Case[] = [];
const axes: Array<[string, readonly string[], (c: string, i: string, o: {allowReversal: boolean}) => string]> = [
  ['order', enums.ORDER_STATUSES, status.classifyOrderStatusTransition],
  ['payment', enums.PAYMENT_STATUSES, status.classifyPaymentTransition],
  ['shipping', enums.SHIPPING_STATUSES, status.classifyShippingTransition],
];
for (const [axis, values, classify] of axes) {
  for (const current of values) {
    for (const incoming of values) {
      for (const allowReversal of [false, true]) {
        statusCases.push({
          name: `${axis}/${current}->${incoming}/${allowReversal}`,
          input: {fn: axis, current, incoming, allowReversal},
          expected: {value: classify(current, incoming, {allowReversal})},
        });
      }
    }
  }
}
for (const verdict of ['SAME', 'FORWARD', 'REVERSAL', 'CANCELLATION', 'INVALID', 'TERMINAL']) {
  for (const allowReversal of [false, true]) {
    statusCases.push({
      name: `allowed/${verdict}/${allowReversal}`,
      input: {fn: 'statusChangeAllowed', verdict, allowReversal},
      expected: {value: status.statusChangeAllowed(verdict, {allowReversal})},
    });
  }
}

// --- order merge ----------------------------------------------------------------
const baseOrder = () => ({
  orderId: 'SO-1', customerId: 'cust-1', orderedAt: 1_700_000_000_000, closerUserId: 'user-a', closerTeamId: 'team-crm',
  caretakerAtOrderTime: 'user-a', channel: 'LINE', shop: 'main', subtotal: 100, total: 120, trackingNumber: '', carrier: '',
  url: '', recipientName: 'สมชาย', phone: '0812345678', orderStatus: 'WAITING_SHIPMENT', paymentStatus: 'PAID',
  shippingStatus: 'LABEL_CREATED', items: [{sku: 'A', qty: 1}],
  address: {line: '1 ถนน', subdistrict: '', district: 'เมือง', province: 'กรุงเทพ', postalCode: '10100'},
  fieldProvenance: {} as Record<string, unknown>,
});
const prov = (sourceId: string, sourceUpdatedAt: number | null, precedence: number) => ({sourceId, sourceUpdatedAt, precedence});
const stamp = (sourceUpdatedAt: number | null, precedence = 10) => ({sourceId: 'file', sourceUpdatedAt, precedence});
const mergeCases: Case[] = [];
const addMerge = (label: string, existing: Record<string, unknown>, incoming: Record<string, unknown>, allowReversal = false) => {
  mergeCases.push({
    name: `merge/${label}`,
    input: {fn: 'mergeOrder', existing, incoming, allowReversal},
    expected: {value: plain(merge.mergeOrder(existing, incoming, {allowReversal}))},
  });
};
const T = 1_700_000_000_000;
addMerge('blank-incoming', baseOrder(), {orderId: 'SO-1', fields: {channel: '', shop: null, trackingNumber: '   '}, source: stamp(T)});
addMerge('identical', baseOrder(), {orderId: 'SO-1', fields: {channel: 'LINE', total: 120}, source: stamp(T)});
addMerge('fills-empty', baseOrder(), {orderId: 'SO-1', fields: {trackingNumber: 'TH123', carrier: 'Kerry', 'address.subdistrict': 'ในเมือง'}, source: stamp(T)});
addMerge('items-replaced', baseOrder(), {orderId: 'SO-1', fields: {items: [{sku: 'B', qty: 2}]}, source: stamp(T)});
addMerge('customer-id-change-conflict', baseOrder(), {orderId: 'SO-1', fields: {customerId: 'cust-2'}, source: stamp(T)});
addMerge('customer-id-blank-ok', baseOrder(), {orderId: 'SO-1', fields: {customerId: ''}, source: stamp(T)});
addMerge('customer-id-same-ok', baseOrder(), {orderId: 'SO-1', fields: {customerId: 'cust-1'}, source: stamp(T)});
addMerge('status-forward', baseOrder(), {orderId: 'SO-1', fields: {orderStatus: 'SHIPPED', shippingStatus: 'IN_TRANSIT'}, source: stamp(T)});
addMerge('status-reversal-blocked', baseOrder(), {orderId: 'SO-1', fields: {orderStatus: 'PENDING_CONFIRMATION'}, source: stamp(T)});
addMerge('status-reversal-allowed', baseOrder(), {orderId: 'SO-1', fields: {orderStatus: 'PENDING_CONFIRMATION'}, source: stamp(T)}, true);
addMerge('status-cancellation', baseOrder(), {orderId: 'SO-1', fields: {orderStatus: 'CANCELLED', paymentStatus: 'REFUNDED'}, source: stamp(T)});
addMerge('status-terminal', {...baseOrder(), orderStatus: 'CANCELLED'}, {orderId: 'SO-1', fields: {orderStatus: 'SHIPPED'}, source: stamp(T)});
addMerge('shipping-invalid', baseOrder(), {orderId: 'SO-1', fields: {shippingStatus: 'NOT_SHIPPED'}, source: stamp(T)});
addMerge('shipping-reversal-allowed', baseOrder(), {orderId: 'SO-1', fields: {shippingStatus: 'NOT_SHIPPED'}, source: stamp(T)}, true);
addMerge('payment-reversal', baseOrder(), {orderId: 'SO-1', fields: {paymentStatus: 'UNPAID'}, source: stamp(T)});
const provenanced = (extra: Record<string, unknown>) => ({...baseOrder(), fieldProvenance: extra});
addMerge('newer-source', provenanced({channel: prov('old', T - 5000, 10)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(T)});
addMerge('older-source-stale', provenanced({channel: prov('old', T + 5000, 10)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(T)});
addMerge('same-version-conflict', provenanced({channel: prov('old', T, 10)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(T)});
addMerge('undated-no-advantage-stale', provenanced({channel: prov('old', T, 10)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(null, 10)});
addMerge('undated-lower-precedence-stale', provenanced({channel: prov('old', T, 20)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(null, 10)});
addMerge('undated-higher-precedence-accepted', provenanced({channel: prov('old', T, 5)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(null, 10)});
addMerge('lower-precedence-conflict', provenanced({channel: prov('old', null, 20)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(T, 10)});
addMerge('lower-precedence-undated-both-conflict', provenanced({channel: prov('old', null, 20)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE'}, source: stamp(null, 10)});
addMerge('no-provenance-accepted', baseOrder(), {orderId: 'SO-1', fields: {channel: 'SHOPEE', total: 150}, source: stamp(T)});
addMerge('address-fields-merge', baseOrder(), {orderId: 'SO-1', fields: {'address.line': '9 ถนนใหม่', 'address.postalCode': '10200'}, source: stamp(T)});
addMerge('mixed', provenanced({channel: prov('old', T + 1, 10), total: prov('old', T - 1, 10)}), {orderId: 'SO-1', fields: {channel: 'SHOPEE', total: 200, trackingNumber: 'TH9', orderStatus: 'SHIPPED'}, source: stamp(T)});
addMerge('no-fields', baseOrder(), {orderId: 'SO-1', fields: {}, source: stamp(T)});
addMerge('existing-provenance-preserved', provenanced({shop: prov('keep', 1, 3)}), {orderId: 'SO-1', fields: {trackingNumber: 'TH1'}, source: stamp(T)});
for (const [label, decisions] of [
  ['empty', []],
  ['mixed', [{kind: 'UNCHANGED'}, {kind: 'SAFE_UPDATE'}, {kind: 'SAFE_UPDATE'}, {kind: 'STALE_INPUT'}, {kind: 'CONFLICT'}, {kind: 'EXPLICIT_REVERSAL_REQUIRED'}]],
] as Array<[string, Array<{kind: string}>]>) {
  mergeCases.push({name: `summarize/${label}`, input: {fn: 'summarizeDecisions', decisions}, expected: {value: plain(merge.summarizeDecisions(decisions))}});
}

// --- row evaluation -------------------------------------------------------------
const rowCases: Case[] = [];
const ACTIVE = ['user-a', 'user-b', 'user-lead'];
const facts = (extra: Record<string, unknown> = {}) => ({
  rowIndex: 3, orderId: 'SO-1', orderedAt: T, closerUserId: 'user-a', closerTeamId: 'team-crm', customerName: 'สมชาย', phone: '0812345678',
  fields: {channel: 'LINE', total: 120}, ...extra,
});
const lead = (extra: Record<string, unknown> = {}) => ({
  leadId: 'team-crm:cust-1', teamId: 'team-crm', lifecycleStatus: 'FOLLOWING', caretakerUserId: 'user-a', ownershipStartedAt: T - 86_400_000,
  ownershipPolicy: 'EXCLUSIVE', lockActive: false, ...extra,
});
const ident = (kind: string, reasons: string[] = []) => ({kind, candidateCustomerId: null, nameScore: 0, phoneExact: false, phoneNear: false, reasons});
const addRow = (label: string, factsValue: Record<string, unknown>, context: Record<string, unknown>) => {
  const ctx = {...context, activeUserIds: new Set(ACTIVE)};
  rowCases.push({
    name: `row/${label}`,
    input: {fn: 'evaluateRow', facts: factsValue, context: {...context, activeUserIds: ACTIVE}},
    expected: {value: plain(preview.evaluateRow(factsValue, ctx))},
  });
};
addRow('new-order-new-lead-no-closer', facts({closerUserId: null, closerTeamId: null}), {identity: ident('NEW_CUSTOMER'), existingOrder: null, existingLead: null});
addRow('new-order-new-lead-with-closer', facts(), {identity: ident('NEW_CUSTOMER'), existingOrder: null, existingLead: null});
addRow('identity-review-blocks', facts(), {identity: ident('HUMAN_REVIEW', ['exact normalized phone but name differs']), existingOrder: null, existingLead: null});
addRow('identity-review-no-reasons', facts(), {identity: ident('HUMAN_REVIEW'), existingOrder: null, existingLead: null});
addRow('identity-review-ignored-with-lead', facts(), {identity: ident('HUMAN_REVIEW'), existingOrder: null, existingLead: lead()});
addRow('identity-review-ignored-with-order', facts(), {identity: ident('HUMAN_REVIEW'), existingOrder: baseOrder(), existingLead: null});
addRow('auto-match-new-order', facts(), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead()});
addRow('unchanged', facts(), {identity: ident('AUTO_MATCH'), existingOrder: baseOrder(), existingLead: null});
addRow('safe-update', facts({fields: {trackingNumber: 'TH1'}}), {identity: ident('AUTO_MATCH'), existingOrder: baseOrder(), existingLead: null});
addRow('stale-input', facts({fields: {channel: 'SHOPEE'}}), {identity: ident('AUTO_MATCH'), existingOrder: provenanced({channel: prov('old', T + 5000, 10)}), existingLead: null});
addRow('merge-conflict-same-version', facts({fields: {channel: 'SHOPEE'}, orderedAt: T}), {identity: ident('AUTO_MATCH'), existingOrder: provenanced({channel: prov('old', T, 10)}), existingLead: null});
addRow('reversal-blocked', facts({fields: {orderStatus: 'PENDING_CONFIRMATION'}}), {identity: ident('AUTO_MATCH'), existingOrder: baseOrder(), existingLead: null});
addRow('reversal-allowed', facts({fields: {orderStatus: 'PENDING_CONFIRMATION'}, allowReversal: true}), {identity: ident('AUTO_MATCH'), existingOrder: baseOrder(), existingLead: null});
addRow('customer-repoint-conflict', facts({fields: {customerId: 'cust-9'}}), {identity: ident('AUTO_MATCH'), existingOrder: baseOrder(), existingLead: null});
for (const policy of ['EXCLUSIVE', 'LATEST_ORDER', 'LATEST_ORDER_WITH_LOCK']) {
  addRow(`ownership/${policy}/same-closer`, facts(), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: policy})});
  addRow(`ownership/${policy}/other-closer`, facts({closerUserId: 'user-b'}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: policy})});
  addRow(`ownership/${policy}/other-closer-locked`, facts({closerUserId: 'user-b'}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: policy, lockActive: true})});
  addRow(`ownership/${policy}/no-closer`, facts({closerUserId: null, closerTeamId: null}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: policy})});
  addRow(`ownership/${policy}/unassigned-lead`, facts(), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: policy, caretakerUserId: null, ownershipStartedAt: null})});
}
addRow('stopped-same-caretaker', facts(), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({lifecycleStatus: 'STOPPED'})});
addRow('stopped-other-closer', facts({closerUserId: 'user-b'}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({lifecycleStatus: 'STOPPED'})});
addRow('stopped-no-closer', facts({closerUserId: null}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({lifecycleStatus: 'STOPPED'})});
addRow('team-from-lead', facts({closerTeamId: null}), {identity: ident('HUMAN_REVIEW', ['x']), existingOrder: null, existingLead: null});
addRow('inactive-closer', facts({closerUserId: 'user-gone'}), {identity: ident('AUTO_MATCH'), existingOrder: null, existingLead: lead({ownershipPolicy: 'LATEST_ORDER'})});

const aggCases: Case[] = [];
const evaluations = rowCases.map(c => (c.expected as {value: {classification: string; blocking: boolean}}).value);
aggCases.push({
  name: 'aggregate/all-rows',
  input: {fn: 'aggregatePreview', evaluations: evaluations.map(e => ({classification: e.classification, blocking: e.blocking})), newCustomers: 4},
  expected: {value: plain(preview.aggregatePreview(evaluations, 4))},
});
aggCases.push({name: 'aggregate/empty', input: {fn: 'aggregatePreview', evaluations: [], newCustomers: 0}, expected: {value: plain(preview.aggregatePreview([], 0))}});
for (const classification of ['NEW_ORDER', 'SAFE_UPDATE', 'UNCHANGED', 'STALE_INPUT', 'CONFLICT', 'INVALID']) {
  const one = [{classification, blocking: classification === 'CONFLICT'}];
  aggCases.push({name: `aggregate/only-${classification}`, input: {fn: 'aggregatePreview', evaluations: one, newCustomers: 1}, expected: {value: plain(preview.aggregatePreview(one as never, 1))}});
}

mkdirSync(outDir, {recursive: true});
const files: Array<[string, Case[]]> = [
  ['import_phone', phoneCases],
  ['import_identity', identityCases],
  ['import_status_transitions', statusCases],
  ['import_order_merge', mergeCases],
  ['import_row_evaluation', [...rowCases, ...aggCases]],
];
for (const [name, cases] of files) {
  writeFileSync(join(outDir, `${name}.json`), `${JSON.stringify(cases, null, 2)}\n`, 'utf-8');
  console.log(`${name}.json: ${cases.length} cases`);
}
