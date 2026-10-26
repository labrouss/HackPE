// node --test web/tests  - engine tests on the two sample configs (EliteBook 840 G8 vs ProBook 450 G8)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parsePlist, writePlist, deepEqual, PData, PInt } from '../src/engine/plist.js';
import { compare, applyPlan, neededFiles, planMarkdown, lint } from '../src/engine/compare.js';
import { decodeCsr, decodeScanPolicy, decodeCpuid, decodeProperty } from '../src/engine/knowledge.js';

const load = (n) => parsePlist(readFileSync(fileURLToPath(new URL('../src/samples/' + n, import.meta.url)), 'utf8'));
const REF = load('config-elitebook-840-g8.plist');
const TGT = load('config-probook-450-g8.plist');

test('plist round-trip keeps every value and type', () => {
  for (const c of [REF, TGT]) assert.ok(deepEqual(parsePlist(writePlist(c)), c));
  const t = parsePlist('<?xml version="1.0"?><plist><dict><key>a</key><integer>2147483650</integer><key>b</key><data>AQI=</data>'
    + '<key>c</key><string>x &amp; y</string><key>d</key><array/><key>e</key><real>1.5</real></dict></plist>');
  assert.equal(String(t.a), '2147483650');
  assert.equal(t.b.hex, '0102');
  assert.equal(t.c, 'x & y');
  assert.ok(writePlist(t).includes('<string>x &amp; y</string>'));
  assert.throws(() => parsePlist('hello'), /not an XML property list/);
});

test('decoders', () => {
  assert.match(decodeCsr(PData.fromHex('03080000')), /0x803.*OCLP/);
  assert.match(decodeCsr(PData.fromHex('00000000')), /fully enabled/);
  assert.equal(decodeScanPolicy(new PInt(0)), 'scan everything (0)');
  assert.match(decodeScanPolicy(new PInt(2687747)), /USB/);
  assert.match(decodeCpuid(PData.fromHex('EA060900000000000000000000000000')), /Coffee Lake/);
  assert.match(decodeProperty('AAPL,ig-platform-id', PData.fromHex('0000499A')), /0x9A490000.*Tiger Lake/);
  assert.match(decodeProperty('layout-id', new PInt(11)), /layout 11/);
});

test('compare finds the major differences', () => {
  const r = compare(REF, TGT);
  const has = (pred, msg) => assert.ok(r.items.some(pred), msg);
  has((i) => i.path === 'ACPI/Add' && i.kind === 'entryOnlyRef' && i.label === 'SSDT-EC.aml', 'missing SSDT-EC');
  has((i) => i.path === 'Booter/Quirks/DevirtualiseMmio' && i.severity === 'critical', 'DevirtualiseMmio');
  has((i) => i.path === 'Kernel/Emulate/Cpuid1Data' && /Coffee Lake/.test(i.decodedRef) && /Ice Lake/.test(i.decodedTarget), 'CPUID decode');
  has((i) => i.label === 'boot-args' && i.detail.added.includes('alcid=11'), 'boot-args token diff');
  has((i) => i.kind === 'order' && i.path === 'Kernel/Add', 'kext order');
  has((i) => i.path === 'UEFI/Drivers' && i.label === 'OpenCanopy.efi', 'OpenCanopy driver');
  const ids = r.items.filter((i) => i.severity === 'identity').map((i) => i.path).sort();
  assert.deepEqual(ids, ['PlatformInfo/Generic/MLB', 'PlatformInfo/Generic/ROM', 'PlatformInfo/Generic/SystemSerialNumber', 'PlatformInfo/Generic/SystemUUID']);
  assert.ok(r.items.every((i) => i.severity !== 'identity' || !i.defaultOn), 'identity is never selected by default');
  // disabled Sample.plist leftovers are inactive, not critical
  assert.ok(r.items.filter((i) => i.path === 'Kernel/Patch').every((i) => i.severity === 'inactive'));
});

test('applying the default plan makes the target match, except its own identity', () => {
  const r = compare(REF, TGT);
  const out = applyPlan(TGT, r.items.filter((i) => i.defaultOn));
  const left = compare(REF, out).items;
  assert.deepEqual(left.map((i) => i.severity), ['identity', 'identity', 'identity', 'identity']);
  assert.equal(out.PlatformInfo.Generic.SystemSerialNumber, TGT.PlatformInfo.Generic.SystemSerialNumber);
  assert.ok(deepEqual(parsePlist(writePlist(out)), out));
  assert.ok(neededFiles(r.items.filter((i) => i.defaultOn)).some((f) => f.dir === 'ACPI' && f.name === 'SSDT-EC.aml'));
  assert.match(planMarkdown(r.items.filter((i) => i.defaultOn), { ref: 'A', target: 'B' }), /## Kernel/);
});

test('partial plan: only the selected steps change', () => {
  const r = compare(REF, TGT);
  const one = r.items.find((i) => i.path === 'Booter/Quirks/DevirtualiseMmio');
  const out = applyPlan(TGT, [one]);
  assert.equal(out.Booter.Quirks.DevirtualiseMmio, false);
  assert.equal(compare(REF, out).items.length, r.items.length - 1);
});

test('health check spots broken kext setups', () => {
  const t = lint(TGT).map((l) => l.title);
  assert.ok(t.some((x) => /VoodooGPIO\.kext loads before VoodooI2C\.kext/.test(x)));
  const a = lint(REF).map((l) => l.title);
  assert.ok(a.some((x) => /SMCBatteryManager\.kext is loaded 2 times/.test(x)));
  assert.ok(!a.some((x) => /AirportItlwm/.test(x)), 'per-macOS variants with Min/MaxKernel are not duplicates');
});
