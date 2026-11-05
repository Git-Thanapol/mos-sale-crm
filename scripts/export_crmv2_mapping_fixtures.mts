/**
 * Export the CRM V2 prototype's mapping profiles, status map, Thai date parsing and
 * profile validation/application as parity fixtures. Same contract as the other
 * exporters: every `expected` value is produced by EXECUTING the prototype.
 *
 * It also writes crm/importv2/profiles.json from the prototype's own
 * MAPPING_PROFILES, so the profile data is generated, never retyped.
 *
 * Usage (cwd = the prototype repo):
 *   node --import tsx scripts/export_crmv2_mapping_fixtures.mts <PROTOTYPE_REPO> <FIXTURE_DIR> <PROFILES_JSON>
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {dirname, join, resolve} from 'node:path';

type Case = {name: string; input: Record<string, unknown>; expected: Record<string, unknown>};

const [protoRoot, outDir, profilesOut] = process.argv.slice(2);
if (!protoRoot || !outDir || !profilesOut) {
  console.error('usage: tsx export_crmv2_mapping_fixtures.mts <PROTOTYPE_REPO> <FIXTURE_DIR> <PROFILES_JSON>');
  process.exit(2);
}
const srcUrl = (rel: string) => pathToFileURL(resolve(protoRoot, 'src', rel)).href;

const time = await import(srcUrl('domain/time.ts'));
const statusMap = await import(srcUrl('import/status-map.ts'));
const mapping = await import(srcUrl('import/mapping.ts'));
const {MAPPING_PROFILES} = await import(srcUrl('config/mapping-profiles.ts'));

const plain = <T,>(value: T): T => JSON.parse(JSON.stringify(value === undefined ? null : value));

mkdirSync(dirname(profilesOut), {recursive: true});
writeFileSync(profilesOut, `${JSON.stringify(plain(MAPPING_PROFILES), null, 2)}\n`, 'utf-8');

// --- Thai time ---------------------------------------------------------------
const timeCases: Case[] = [];
const dates = ['31/08/2569', '31/08/2026', '1/1/2569', '01-01-2026', '29/02/2024', '29/02/2026', '31/04/2026', '00/01/2026', '32/01/2026', '15/13/2026',
  '15/00/2026', '1/1/0050', '1/1/2400', '1/1/2401', ' 26/10/2569 ', '', '   ', '26.10.2569', '2026-10-26', '26/10/26', 'abc', '26/10/2569 10:00', '26/10/๒๕๖๙'];
for (const input of dates) {
  timeCases.push({name: `parseThaiDate/${input}`, input: {fn: 'parseThaiDate', text: input}, expected: {value: time.parseThaiDate(input)}});
}
for (const serial of [1, 2, 59, 60, 61, 25569, 45000, 45000.5, 45000.4999999, 46000.123456, 0, -1, 0.5, Number.NaN, Number.POSITIVE_INFINITY]) {
  const value = time.excelSerialToMillis(serial);
  timeCases.push({name: `excelSerial/${serial}`, input: {fn: 'excelSerialToMillis', serial: Number.isFinite(serial) ? serial : String(serial)}, expected: {value}});
}
for (const [day, hhmm] of [['2026-10-26', '09:30'], ['2026-10-26', '9:05'], ['2026-10-26', '00:00'], ['2026-10-26', '23:59'], ['2026-10-26', '24:00'], ['2026-10-26', '12:60'],
  ['2026-10-26', ' 9:30 '], ['2026-10-26', '9.30'], ['nope', '09:30'], ['2026-02-30', '09:30'], ['2026-10-26', '']]) {
  timeCases.push({name: `bangkokTimeOnDay/${day}/${hhmm}`, input: {fn: 'bangkokTimeOnDay', dayKey: day, hhmm}, expected: {value: time.bangkokTimeOnDay(day, hhmm)}});
}
for (const ts of [0, 1_700_000_000_000, 1_700_000_000_000 - 7 * 3_600_000, 1_700_000_000_000 + 17 * 3_600_000 - 1, 1_700_000_000_000 + 17 * 3_600_000, -86_400_000, 4_102_444_800_000]) {
  timeCases.push({name: `bangkokDayKey/${ts}`, input: {fn: 'bangkokDayKey', ts}, expected: {value: time.bangkokDayKey(ts)}});
  timeCases.push({name: `startOfBangkokDay/${ts}`, input: {fn: 'startOfBangkokDay', ts}, expected: {value: time.startOfBangkokDay(ts)}});
}

// --- status map ----------------------------------------------------------------
const statusCases: Case[] = [];
const rawStatuses: Array<string | null | undefined> = [...statusMap.knownSourceStatuses(), null, undefined, '', '  สำเร็จ  ', 'สำเร็จ ', 'ไม่รู้จัก', 'DELIVERED', 'สำเร็จ'.normalize('NFD')];
for (const raw of rawStatuses) {
  statusCases.push({name: `mapSourceStatus/${String(raw)}`, input: {fn: 'mapSourceStatus', raw: raw === undefined ? null : raw}, expected: {value: plain(statusMap.mapSourceStatus(raw))}});
}
for (const raw of ['COD', 'cod', 'เก็บเงินปลายทาง', 'ปลายทาง', 'โอนเงิน', 'Transfer', 'PAID', 'paid online', 'เก็บเงิน', '', null, undefined, 'บัตรเครดิต', ' โอน ']) {
  statusCases.push({name: `mapPaymentMethod/${String(raw)}`, input: {fn: 'mapPaymentMethod', raw: raw === undefined ? null : raw}, expected: {value: statusMap.mapPaymentMethod(raw)}});
}
for (const raw of ['', null, undefined, 'แสกนรับแล้ว', 'สแกนรับ', 'รับพัสดุ', 'Pickup', 'picked up', 'กำลังจัดส่ง', 'ระหว่างขนส่ง', 'In Transit', 'ขนส่งรับ', 'สำเร็จ', 'delivered', 'ส่งถึงแล้ว', 'ตีกลับ', 'Return', 'ปัญหา', 'failed',
  'ล้มเหลว', 'รอขนส่ง', 'label printed', 'อื่นๆ', ' กำลัง ']) {
  statusCases.push({name: `mapShippingText/${String(raw)}`, input: {fn: 'mapShippingText', raw: raw === undefined ? null : raw}, expected: {value: statusMap.mapShippingText(raw)}});
}
statusCases.push({name: 'knownSourceStatuses', input: {fn: 'knownSourceStatuses'}, expected: {value: statusMap.knownSourceStatuses()}});

// --- mapping ---------------------------------------------------------------------
const mappingCases: Case[] = [];
const profile = (id: string) => MAPPING_PROFILES.find((p: {profileId: string}) => p.profileId === id);
for (const header of ['  สั่ง  ซื้อ ', 'a b', 'x​y', '﻿head', 'ชื่อ​ร้าน', 'Mixed   Case ', '']) {
  mappingCases.push({name: `normalizeHeader/${JSON.stringify(header)}`, input: {fn: 'normalizeHeader', header}, expected: {value: mapping.normalizeHeader(header)}});
}
const goodCrmRow = (n: number): unknown[] => [
  '26/10/2569', `SO-${100 + n}`, 'LINE', 'SKU1', 'สินค้า', 2, '1,000', 'COD', 'Kerry', 'TH1', 'http://x', 'สมชาย', '0812345678', '',
  '1 ถ.', 'ต', 'อ', 'กรุงเทพ', '10100', 'ก', '', 'ข', 'CRM', 'สูง', '27/10/2569', 'โน้ต',
];
const crm = profile('crm-customers');
const tableOf = (p: {headerSignature: string[]}, rows: unknown[][], headers?: string[]) => ({headers: headers ?? [...p.headerSignature], rows});
const validate = (label: string, p: unknown, table: unknown, options?: Record<string, unknown>) => {
  mappingCases.push({
    name: `validate/${label}`,
    input: {fn: 'validateProfileAgainstSample', profileId: (p as {profileId: string}).profileId, table, options: options ?? null},
    expected: {value: plain(mapping.validateProfileAgainstSample(p, table, options ?? {}))},
  });
};
validate('crm-good', crm, tableOf(crm, [0, 1, 2, 3, 4].map(goodCrmRow)));
validate('crm-header-mismatch', crm, tableOf(crm, [0, 1, 2].map(goodCrmRow), crm.headerSignature.map((h: string, i: number) => (i === 3 ? 'SKU ใหม่' : h))));
validate('crm-fewer-columns', crm, tableOf(crm, [[1, 2, 3]], crm.headerSignature.slice(0, 20)));
validate('crm-too-few-samples-skips-shape', crm, tableOf(crm, [0, 1].map(goodCrmRow).map(r => r.map((v, i) => (i === 1 ? 'x y' : v)))));
validate('crm-min-samples-option', crm, tableOf(crm, [0, 1].map(goodCrmRow).map(r => r.map((v, i) => (i === 12 ? 'abc' : v)))), {minSamples: 2});
validate('crm-shape-block', crm, tableOf(crm, [0, 1, 2, 3].map(goodCrmRow).map(r => r.map((v, i) => (i === 12 ? 'ไม่ใช่เบอร์' : v)))));
validate('crm-shape-partial', crm, tableOf(crm, [0, 1, 2, 3, 4, 5, 6, 7].map(goodCrmRow).map((r, n) => r.map((v, i) => (i === 6 && n < 2 ? 'ราคา?' : v)))));
// Boundaries of the shape ratio: <0.5 blocks, <0.9 warns, otherwise silent.
const withBadPhones = (total: number, bad: number) =>
  tableOf(crm, Array.from({length: total}, (_, n) => goodCrmRow(n)).map((r, n) => r.map((v, i) => (i === 12 && n < bad ? 'ไม่ใช่เบอร์' : v))));
validate('crm-ratio-0.45-blocks', crm, withBadPhones(20, 11));
validate('crm-ratio-0.50-warns', crm, withBadPhones(10, 5));
validate('crm-ratio-0.85-warns', crm, withBadPhones(20, 3));
validate('crm-ratio-0.90-silent', crm, withBadPhones(10, 1));
validate('crm-ratio-1.00-silent', crm, withBadPhones(10, 0));
validate('crm-shifted-columns', crm, tableOf(crm, [0, 1, 2, 3].map(n => {
  const r = goodCrmRow(n);
  r[1] = '0812345678';
  r[12] = `SO-${n}`;
  return r;
})));
validate('crm-blank-cells-ignored', crm, tableOf(crm, [0, 1, 2, 3].map(goodCrmRow).map(r => r.map((v, i) => (i === 13 || i === 18 ? null : v)))));
validate('crm-duplicate-header', crm, tableOf(crm, [0, 1, 2].map(goodCrmRow), crm.headerSignature.map((h: string, i: number) => (i === 20 ? crm.headerSignature[19] : h))));
const inout = profile('inoutmanagemain');
validate('inout-duplicates-and-unmapped', inout, tableOf(inout, []));
const tele = profile('tele-sales-report');
validate('tele-good', tele, tableOf(tele, [[1, '09:30', 'ขาย', 'SO-1', 'ก', 1, 100, 'ข'], [2, '10:15', 'ขาย', 'SO-2', 'ก', 2, 200, 'ข'], [3, '11:00', 'ขาย', 'SO-3', 'ก', 1, 50, 'ข']]));
const lite = profile('litedetailorder');
validate('lite-empty', lite, tableOf(lite, []));

const applyRow = (label: string, p: {profileId: string}, row: unknown[]) => {
  mappingCases.push({
    name: `apply/${label}`,
    input: {fn: 'applyMapping', profileId: p.profileId, row},
    expected: {value: plain(mapping.applyMapping(p, row))},
  });
};
const setAt = (row: unknown[], overrides: Record<number, unknown>) => row.map((v, i) => (i in overrides ? overrides[i] : v));
applyRow('crm-good', crm, goodCrmRow(1));
applyRow('crm-blank-required-date', crm, setAt(goodCrmRow(1), {0: null}));
applyRow('crm-blank-required-name', crm, setAt(goodCrmRow(1), {11: '   '}));
applyRow('crm-bad-date', crm, setAt(goodCrmRow(1), {0: 'not a date'}));
applyRow('crm-gregorian-date', crm, setAt(goodCrmRow(1), {0: '26/10/2026'}));
applyRow('crm-bad-phone', crm, setAt(goodCrmRow(1), {12: '123'}));
applyRow('crm-phone-formats', crm, setAt(goodCrmRow(1), {12: '+66 81 234 5678', 13: '081-111-2222'}));
applyRow('crm-money-with-commas', crm, setAt(goodCrmRow(1), {6: '12,345.50'}));
applyRow('crm-money-number', crm, setAt(goodCrmRow(1), {6: 99.5}));
applyRow('crm-money-bad', crm, setAt(goodCrmRow(1), {6: 'ฟรี'}));
applyRow('crm-integer-bad', crm, setAt(goodCrmRow(1), {5: '1.5'}));
applyRow('crm-integer-number', crm, setAt(goodCrmRow(1), {5: 3}));
applyRow('crm-integer-commas', crm, setAt(goodCrmRow(1), {5: '1,000'}));
applyRow('crm-number-hex', crm, setAt(goodCrmRow(1), {6: '0x10'}));
applyRow('crm-number-exp', crm, setAt(goodCrmRow(1), {6: '1e3'}));
applyRow('crm-number-underscore', crm, setAt(goodCrmRow(1), {6: '1_0'}));
applyRow('crm-number-infinity', crm, setAt(goodCrmRow(1), {6: 'Infinity'}));
applyRow('crm-number-trailing-dot', crm, setAt(goodCrmRow(1), {6: '5.'}));
applyRow('crm-number-leading-dot', crm, setAt(goodCrmRow(1), {6: '.5'}));
applyRow('crm-number-whitespace', crm, setAt(goodCrmRow(1), {6: '  7  '}));
applyRow('crm-number-mixed', crm, setAt(goodCrmRow(1), {6: '12abc'}));
applyRow('crm-order-id-number', crm, setAt(goodCrmRow(1), {1: 12345}));
applyRow('crm-short-row', crm, goodCrmRow(1).slice(0, 12));
applyRow('crm-all-blank', crm, new Array(26).fill(null));
applyRow('lite-excel-serial', lite, setAt(new Array(30).fill(null), {0: 'I-1', 3: 'สำเร็จ', 6: 45000.5, 9: '1,200', 25: 2}));
applyRow('lite-excel-serial-as-text', lite, setAt(new Array(30).fill(null), {0: 'I-1', 3: 'สำเร็จ', 6: '45000.5'}));
applyRow('lite-excel-bad-serial', lite, setAt(new Array(30).fill(null), {0: 'I-1', 3: 'สำเร็จ', 6: 'x'}));
applyRow('lite-excel-thai-date-fallback', lite, setAt(new Array(30).fill(null), {0: 'I-1', 3: 'สำเร็จ', 6: '26/10/2569'}));
applyRow('lite-excel-zero', lite, setAt(new Array(30).fill(null), {0: 'I-1', 3: 'สำเร็จ', 6: 0}));
for (const [label, value] of [['ok', '9:30'], ['padded', ' 09:30 '], ['bad', '9.30'], ['long', '123:45']] as Array<[string, string]>) {
  applyRow(`tele-time-${label}`, tele, [1, value, 'ขาย', 'SO-1', 'ก', 1, 100, 'ข']);
}

mkdirSync(outDir, {recursive: true});
const files: Array<[string, Case[]]> = [
  ['import_thai_time', timeCases],
  ['import_status_map', statusCases],
  ['import_mapping', mappingCases],
];
for (const [name, cases] of files) {
  writeFileSync(join(outDir, `${name}.json`), `${JSON.stringify(cases, null, 2)}\n`, 'utf-8');
  console.log(`${name}.json: ${cases.length} cases`);
}
console.log(`profiles.json: ${MAPPING_PROFILES.length} profiles`);
