// OpenCore-aware comparison: "make the TARGET config behave like the REFERENCE config".
import { PData, PInt, typeOf, deepEqual, clone, show } from './plist.js';
import {
  QUIRKS, SECTION_INFO, decodeValue, isIdentityPath, kextLeaf, kextParent, kextHardware, CORE_KEXTS, CONFLICTS,
  ssdtIsMachineSpecific, bootArgTokens, argName, BOOT_ARGS, DEBUG_ARGS,
} from './knowledge.js';

export const SECTIONS = ['ACPI', 'Booter', 'DeviceProperties', 'Kernel', 'Misc', 'NVRAM', 'PlatformInfo', 'UEFI'];

const hex = (v) => (v instanceof PData ? v.hex : '');
const lc = (s) => String(s || '').toLowerCase();

// Arrays whose entries are matched by identity, not by position.
export const ARRAYS = {
  'ACPI/Add': { key: (e) => lc(e.Path), label: (e) => e.Path, file: (e) => ['ACPI', e.Path] },
  'ACPI/Delete': { key: (e) => hex(e.TableSignature) + '|' + hex(e.OemTableId), label: (e) => e.Comment || 'Delete table ' + hex(e.TableSignature) },
  'ACPI/Patch': { key: (e) => hex(e.TableSignature) + '|' + hex(e.Find) + '|' + hex(e.Replace), label: (e) => e.Comment || `${hex(e.Find)} -> ${hex(e.Replace)}` },
  'Booter/MmioWhitelist': { key: (e) => String(e.Address), label: (e) => e.Comment || 'MMIO 0x' + Number(e.Address).toString(16) },
  'Booter/Patch': { key: (e) => (e.Identifier || '') + '|' + hex(e.Find) + '|' + hex(e.Replace), label: (e) => e.Comment || 'Booter patch' },
  'Kernel/Add': { key: (e) => lc(e.BundlePath).replace('/contents/plugins/', '/contents/plugins/'), label: (e) => e.BundlePath, file: (e) => ['Kexts', e.BundlePath.split('/')[0]] },
  'Kernel/Block': { key: (e) => lc(e.Identifier), label: (e) => e.Comment || 'Block ' + e.Identifier },
  'Kernel/Force': { key: (e) => lc(e.Identifier) + '|' + lc(e.BundlePath), label: (e) => e.Comment || 'Force ' + e.Identifier },
  'Kernel/Patch': { key: (e) => (e.Identifier || '') + '|' + (e.Base || '') + '|' + hex(e.Find) + '|' + hex(e.Replace), label: (e) => e.Comment || `${e.Identifier} ${e.Base || ''}`.trim() },
  'Misc/Entries': { key: (e) => lc(e.Path) || lc(e.Name), label: (e) => e.Name || e.Path },
  'Misc/Tools': { key: (e) => lc(e.Path), label: (e) => e.Name || e.Path, file: (e) => ['Tools', e.Path] },
  'Misc/BlessOverride': { key: (e) => lc(e), label: (e) => e },
  'UEFI/Drivers': { key: (e) => lc(typeof e === 'string' ? e : e.Path), label: (e) => (typeof e === 'string' ? e : e.Path), file: (e) => ['Drivers', typeof e === 'string' ? e : e.Path] },
  'UEFI/ReservedMemory': { key: (e) => String(e.Address), label: (e) => e.Comment || 'Reserved 0x' + Number(e.Address).toString(16) },
  'UEFI/Unload': { key: (e) => lc(e), label: (e) => e },
};

const enabled = (e) => !(e && typeof e === 'object' && e.Enabled === false);

// ------------------------------------------------------------- severity ----
function severity(path, ctx = {}) {
  if (isIdentityPath(path)) return 'identity';
  if (ctx.inactive) return 'inactive';
  const s = path.split('/')[0];
  if (s === 'DeviceProperties') return 'hardware';
  if (/^UEFI\/Audio\//.test(path)) return 'hardware';
  if (path === 'ACPI/Add' && ctx.entry && ssdtIsMachineSpecific(ctx.entry.Path || '')) return 'hardware';
  if (path === 'Kernel/Add' && ctx.entry) {
    if (CORE_KEXTS.test(kextLeaf(ctx.entry.BundlePath))) return 'critical';
    if (kextHardware(ctx.entry.BundlePath)) return 'hardware';
    return 'critical';
  }
  if (/^(ACPI|Booter|Kernel)\//.test(path)) return 'critical';
  if (/^UEFI\/(Drivers|Quirks|APFS)/.test(path)) return 'critical';
  if (/^Misc\/Security\/(SecureBootModel|Vault|DmgLoading|ScanPolicy)$/.test(path)) return 'critical';
  if (/^PlatformInfo\/(Generic\/SystemProductName|UpdateSMBIOSMode|UpdateSMBIOS|Automatic)$/.test(path)) return 'critical';
  if (/csr-active-config$/.test(path)) return 'critical';
  if (/boot-args$/.test(path)) return 'critical';
  if (/^PlatformInfo\/(DataHub|PlatformNVRAM|SMBIOS)\//.test(path)) return ctx.automatic ? 'inactive' : 'minor';
  return 'minor';
}

function explain(path, ctx = {}) {
  if (QUIRKS[path]) return QUIRKS[path];
  if (isIdentityPath(path)) return 'Unique to each PC. Keep the target\'s own value (or generate a new serial with macserial).';
  if (path === 'ACPI/Add' && ctx.entry) {
    return ssdtIsMachineSpecific(ctx.entry.Path)
      ? 'This table is usually dumped from or written for one specific machine - only copy it if both PCs are the same model.'
      : 'Generic SSDT (Dortania / SSDTTime style). Copy the .aml file into EFI/OC/ACPI as well.';
  }
  if (path === 'Kernel/Add' && ctx.entry) {
    const hw = kextHardware(ctx.entry.BundlePath);
    const p = kextParent(ctx.entry.BundlePath);
    return [p ? `Plugin inside ${p}.` : 'Copy the kext into EFI/OC/Kexts as well.',
      hw ? `Only useful if the target PC has the same ${hw}.` : '',
      ctx.entry.MinKernel || ctx.entry.MaxKernel ? `Loads on Darwin ${ctx.entry.MinKernel || 'any'} to ${ctx.entry.MaxKernel || 'any'}.` : ''].filter(Boolean).join(' ');
  }
  if (path === 'UEFI/Drivers') return 'Copy the .efi into EFI/OC/Drivers as well.';
  if (path === 'ACPI/Patch') return 'Binary find/replace on the firmware ACPI tables. Patches written for one PC\'s DSDT may do nothing (or harm) on another - check the Find bytes exist in the target\'s DSDT.';
  if (path === 'ACPI/Delete') return 'Drops a firmware ACPI table (e.g. DMAR or a CPU table) before macOS sees it.';
  if (path === 'Kernel/Patch') return 'Binary patch inside a macOS kext or the kernel.';
  if (path === 'Kernel/Block') return 'Stops a macOS kext from loading.';
  if (path === 'Kernel/Force') return 'Forces a system kext to load (older macOS).';
  if (path === 'Booter/MmioWhitelist') return 'MMIO region kept for the firmware while DevirtualiseMmio is on - specific to each board.';
  if (path === 'UEFI/ReservedMemory') return 'Memory reserved from macOS - specific to each board.';
  if (path === 'Misc/Tools') return 'Tool entry in the boot picker (shell, NVRAM reset...).';
  if (/^DeviceProperties\/Add\//.test(path)) return 'Device properties depend on the PCI path and hardware of each PC - compare with the target\'s own devices.';
  if (/^PlatformInfo\/(DataHub|PlatformNVRAM|SMBIOS)\//.test(path) && ctx.automatic) return 'Ignored while PlatformInfo > Automatic is True.';
  return '';
}

// ---------------------------------------------------------------- diff ----
/**
 * compare(ref, target) -> { items, sections, lints:{ref,target}, summary }
 * item: { id, section, path, label, kind, ref, target, severity, explain, decodedRef, decodedTarget, op, defaultOn, files, detail }
 */
export function compare(ref, target) {
  const items = [];
  const automatic = !!(ref?.PlatformInfo?.Automatic && target?.PlatformInfo?.Automatic);
  let n = 0;
  const push = (it) => { it.id = 'd' + (++n); it.section = it.path.split('/')[0]; items.push(it); };

  const walk = (a, b, path) => {
    const pstr = path.join('/');
    if (ARRAYS[pstr] && (Array.isArray(a) || Array.isArray(b))) return diffArray(a || [], b || [], pstr);
    const ta = typeOf(a), tb = typeOf(b);
    if (a !== undefined && b !== undefined && ta === 'dict' && tb === 'dict') {
      const keys = [...Object.keys(a), ...Object.keys(b).filter((k) => !(k in a))];
      for (const k of keys) {
        if (k.startsWith('#')) continue;                          // OpenCore comment keys
        walk(a[k], b[k], [...path, k]);
      }
      return;
    }
    if (a === undefined && b === undefined) return;
    if (a !== undefined && b !== undefined && deepEqual(a, b)) return;
    // whole dict present on one side only (e.g. a device in DeviceProperties)
    const kind = a === undefined ? 'onlyTarget' : b === undefined ? 'onlyRef' : 'changed';
    const sev = severity(pstr, { automatic });
    if (/boot-args$/.test(pstr) && kind === 'changed') return push(bootArgsItem(pstr, a, b, sev));
    const schemaKey = kind !== 'changed' && /\/(Quirks|Boot|Debug|Security|Output|Input|AppleInput|Audio|APFS|ProtocolOverrides|Scheme|Emulate|Serial)\//.test(pstr + '/') &&
      path.length >= 3 && ta !== 'dict' && tb !== 'dict' && !/^NVRAM|^DeviceProperties/.test(pstr);
    push({
      path: pstr, label: path.slice(1).join(' > '), kind, ref: a, target: b,
      severity: schemaKey ? 'minor' : sev,
      schema: schemaKey ? (kind === 'onlyRef' ? 'newer' : 'older') : null,
      explain: schemaKey
        ? (kind === 'onlyRef'
          ? 'Only the reference has this key: it was written for a newer OpenCore. Update the target\'s OpenCore.efi and drivers to the same release, then add the key.'
          : 'Only the target has this key: an older OpenCore option that the reference\'s OpenCore no longer uses, or a newer one. Match the OpenCore release first.')
        : explain(pstr, { automatic }),
      decodedRef: a !== undefined ? decodeValue(pstr, a) : null,
      decodedTarget: b !== undefined ? decodeValue(pstr, b) : null,
      op: kind === 'onlyTarget' ? { type: 'del', path } : { type: 'set', path, value: a },
      defaultOn: sev !== 'identity' && !(schemaKey && kind === 'onlyTarget'),
    });
  };

  function diffArray(A, B, pstr) {
    const spec = ARRAYS[pstr];
    const path = pstr.split('/');
    const mapA = new Map(), mapB = new Map();
    A.forEach((e, i) => { const k = spec.key(e); if (!mapA.has(k)) mapA.set(k, { e, i }); });
    B.forEach((e, i) => { const k = spec.key(e); if (!mapB.has(k)) mapB.set(k, { e, i }); });
    for (const [k, { e, i }] of mapA) {
      if (!mapB.has(k)) {
        const inactive = !enabled(e);
        const sev = severity(pstr, { entry: e, inactive, automatic });
        push({
          path: pstr, label: spec.label(e), kind: 'entryOnlyRef', ref: e, target: undefined, severity: sev,
          explain: (inactive ? 'Disabled in the reference - no effect. ' : '') + explain(pstr, { entry: e }),
          op: { type: 'arrAdd', path, entry: e, afterKey: i > 0 ? spec.key(A[i - 1]) : null },
          defaultOn: !inactive, files: spec.file && !inactive ? [spec.file(e)] : [],
        });
        continue;
      }
      const t = mapB.get(k).e;
      if (deepEqual(e, t)) continue;
      const fields = typeof e === 'object' && typeof t === 'object'
        ? [...new Set([...Object.keys(e), ...Object.keys(t)])].filter((f) => !deepEqual(e[f], t[f])) : [];
      const inactive = !enabled(e) && !enabled(t);
      const sev = fields.every((f) => f === 'Comment') ? 'minor' : severity(pstr, { entry: e, inactive, automatic });
      push({
        path: pstr, label: spec.label(e), kind: 'entryChanged', ref: e, target: t, severity: sev, fields,
        explain: fields.map((f) => `${f}: ${show(t[f])} -> ${show(e[f])}`).join('; ') + '. ' + explain(pstr, { entry: e }),
        op: { type: 'arrSet', path, key: k, fields: Object.fromEntries(fields.map((f) => [f, e[f]])) },
        defaultOn: sev !== 'inactive',
      });
    }
    for (const [k, { e }] of mapB) {
      if (mapA.has(k)) continue;
      const inactive = !enabled(e);
      const sev = severity(pstr, { entry: e, inactive, automatic });
      push({
        path: pstr, label: spec.label(e), kind: 'entryOnlyTarget', ref: undefined, target: e, severity: sev,
        explain: (inactive ? 'Disabled leftover in the target - removing it is just cleanup. ' : 'The reference does not use this. ') + explain(pstr, { entry: e }),
        op: { type: 'arrDel', path, key: k },
        defaultOn: true,
      });
    }
    // order of kexts matters (Lilu first, plugins after their parent)
    if (pstr === 'Kernel/Add') {
      const common = A.map(spec.key).filter((k) => mapB.has(k));
      const inB = B.map(spec.key).filter((k) => mapA.has(k));
      if (common.join('\n') !== inB.join('\n')) {
        push({
          path: pstr, label: 'Kext load order', kind: 'order', ref: common.length, target: inB.length, severity: 'critical',
          explain: 'Kexts load in list order: Lilu first, then plugins after their parent. Reorder the target\'s kexts like the reference.',
          op: { type: 'arrOrder', path, order: A.map(spec.key) }, defaultOn: true,
        });
      }
    }
  }

  walk(ref, target, []);
  // sort: section order, then severity
  const rank = { critical: 0, hardware: 1, identity: 2, minor: 3, inactive: 4 };
  items.sort((x, y) => (SECTIONS.indexOf(x.section) - SECTIONS.indexOf(y.section)) || (rank[x.severity] - rank[y.severity]));

  const sections = SECTIONS.map((s) => ({
    name: s, info: SECTION_INFO[s],
    count: items.filter((i) => i.section === s).length,
    critical: items.filter((i) => i.section === s && i.severity === 'critical').length,
  }));
  return { items, sections, lints: { ref: lint(ref), target: lint(target) }, summary: { ref: summarize(ref), target: summarize(target) } };
}

function bootArgsItem(pstr, a, b, sev) {
  const ta = bootArgTokens(a), tb = bootArgTokens(b);
  const na = new Map(ta.map((t) => [argName(t), t])), nb = new Map(tb.map((t) => [argName(t), t]));
  const added = ta.filter((t) => !nb.has(argName(t)));
  const removed = tb.filter((t) => !na.has(argName(t)));
  const changed = ta.filter((t) => nb.has(argName(t)) && nb.get(argName(t)) !== t);
  const desc = (t) => `${t}${BOOT_ARGS[argName(t)] ? ' (' + BOOT_ARGS[argName(t)] + ')' : ''}`;
  const parts = [];
  if (added.length) parts.push('Reference adds: ' + added.map(desc).join(', '));
  if (removed.length) parts.push('Target only: ' + removed.map(desc).join(', '));
  if (changed.length) parts.push('Different values: ' + changed.map((t) => `${nb.get(argName(t))} -> ${t}`).join(', '));
  const hwArgs = [...added, ...changed].filter((t) => /^(alcid|-wegnoegpu|-igfx|agdpmod|-radvesa|brcmfx|-vi2c)/.test(argName(t)));
  return {
    path: pstr, label: 'boot-args', kind: 'changed', ref: a, target: b, severity: sev,
    detail: { added, removed, changed, debug: [...ta].filter((t) => DEBUG_ARGS.has(argName(t))) },
    explain: parts.join('. ') + '.' + (hwArgs.length ? ` Hardware-specific: ${hwArgs.join(', ')} - check they fit the target PC.` : ''),
    op: { type: 'set', path: pstr.split('/'), value: a }, defaultOn: true,
  };
}

// ---------------------------------------------------------- health check ----
export function lint(cfg) {
  const out = [];
  const add = (severity, title, detail, path) => out.push({ severity, title, detail, path });
  const kexts = cfg?.Kernel?.Add || [];
  const on = kexts.filter(enabled);
  const leaves = on.map((k) => kextLeaf(k.BundlePath));
  // Lilu first
  const lilu = on.findIndex((k) => /^lilu\.kext$/i.test(k.BundlePath));
  if (lilu > 0) add('critical', 'Lilu is not the first kext', 'Lilu must load before every plugin that depends on it. Move it to the top of Kernel > Add.', 'Kernel/Add');
  // plugin before its parent
  on.forEach((k, i) => {
    const p = kextParent(k.BundlePath);
    if (!p) return;
    const pi = on.findIndex((x) => lc(x.BundlePath) === lc(p));
    if (pi === -1) add('critical', `${kextLeaf(k.BundlePath)} has no parent entry`, `Its parent ${p} is not in Kernel > Add (or is disabled).`, 'Kernel/Add');
    else if (pi > i) add('critical', `${kextLeaf(k.BundlePath)} loads before ${p}`, 'Plugins must come after their parent kext in Kernel > Add.', 'Kernel/Add');
  });
  // duplicates
  const seen = {};
  leaves.forEach((l) => { seen[lc(l)] = (seen[lc(l)] || 0) + 1; });
  for (const [l, c] of Object.entries(seen)) {
    if (c < 2) continue;
    const same = on.filter((k) => lc(kextLeaf(k.BundlePath)) === l);
    // the same kext for different macOS versions (MinKernel/MaxKernel ranges) is fine
    const ranges = same.map((k) => `${k.MinKernel || ''}-${k.MaxKernel || ''}`);
    if (new Set(ranges).size === ranges.length && ranges.every((r) => r !== '-')) continue;
    add('critical', `${same[0] ? kextLeaf(same[0].BundlePath) : l} is loaded ${c} times`,
      same.map((k) => k.BundlePath).join(', ') + ' - keep one, a kext loaded twice can stop the boot.', 'Kernel/Add');
  }
  // conflicts
  const all = [...on.map((k) => kextLeaf(k.BundlePath)), ...(cfg?.UEFI?.Drivers || []).filter(enabled).map((d) => (typeof d === 'string' ? d : d.Path))];
  for (const [ra, rb, msg] of CONFLICTS) {
    const x = all.find((n) => ra.test(n)), y = all.find((n) => rb.test(n));
    if (x && y) add('hardware', `${x} + ${y}`, msg, 'Kernel/Add');
  }
  // AppleALC on Tahoe
  const alc = on.find((k) => /^applealc\.kext$/i.test(kextLeaf(k.BundlePath)));
  if (alc && !alc.MaxKernel) add('minor', 'AppleALC on macOS 26 Tahoe', 'Tahoe removed AppleHDA, so AppleALC alone gives no audio there. Cap it with MaxKernel 24.99.99 and use VoodooHDA or an OCLP-style audio patch for Tahoe.', 'Kernel/Add');
  // picker
  const drivers = (cfg?.UEFI?.Drivers || []).filter(enabled).map((d) => lc(typeof d === 'string' ? d : d.Path));
  if (cfg?.Misc?.Boot?.PickerMode === 'External' && !drivers.includes('opencanopy.efi'))
    add('critical', 'PickerMode External without OpenCanopy', 'Load OpenCanopy.efi (and copy the Resources folder) or set PickerMode to Builtin.', 'Misc/Boot/PickerMode');
  if (!drivers.includes('openruntime.efi')) add('critical', 'OpenRuntime.efi is not loaded', 'OpenCore needs OpenRuntime.efi in UEFI > Drivers.', 'UEFI/Drivers');
  if (!drivers.some((d) => /hfsplus/.test(d))) add('minor', 'No HFS+ driver', 'Without HfsPlus.efi or OpenHfsPlus.efi the macOS recovery/installer may not show in the picker.', 'UEFI/Drivers');
  // debug leftovers
  const ba = cfg?.NVRAM?.Add?.['7C436110-AB2A-4BBB-A880-FE41995C9F82']?.['boot-args'];
  const dbg = bootArgTokens(ba).filter((t) => DEBUG_ARGS.has(argName(t)));
  if (dbg.length) add('minor', 'Debug boot-args', `${dbg.join(' ')} - fine while setting up; remove when the system is stable.`, 'NVRAM/Add/7C436110-AB2A-4BBB-A880-FE41995C9F82/boot-args');
  if (cfg?.Kernel?.Quirks?.XhciPortLimit === true) add('minor', 'XhciPortLimit is on', 'Only meant for USB mapping - turn it off once you have a USB map.', 'Kernel/Quirks/XhciPortLimit');
  if (cfg?.Kernel?.Quirks?.AppleCpuPmCfgLock && cfg?.Kernel?.Quirks?.AppleXcpmCfgLock) add('minor', 'Both CFG-Lock quirks on', 'AppleCpuPmCfgLock is for Ivy Bridge and older, AppleXcpmCfgLock for Haswell and newer. Usually only one is needed.', 'Kernel/Quirks');
  // inactive leftovers
  for (const s of ['Kernel/Patch', 'Kernel/Block', 'Misc/Tools', 'Misc/Entries', 'ACPI/Patch', 'Booter/Patch', 'UEFI/ReservedMemory']) {
    const [a, b] = s.split('/');
    const arr = cfg?.[a]?.[b] || [];
    const off = arr.filter((e) => !enabled(e)).length;
    if (off >= 2) add('inactive', `${off} disabled entries in ${a} > ${b}`, 'Leftovers from Sample.plist or an earlier setup. They do nothing; removing them makes the config easier to read.', s);
  }
  if (cfg?.PlatformInfo?.Automatic && (cfg.PlatformInfo.DataHub || cfg.PlatformInfo.SMBIOS))
    add('inactive', 'DataHub / SMBIOS sections are ignored', 'PlatformInfo > Automatic is True, so only Generic is used.', 'PlatformInfo/Automatic');
  const rank = { critical: 0, hardware: 1, identity: 2, minor: 3, inactive: 4 };
  return out.sort((x, y) => rank[x.severity] - rank[y.severity]);
}

export function summarize(cfg) {
  const kexts = (cfg?.Kernel?.Add || []);
  return {
    model: cfg?.PlatformInfo?.Generic?.SystemProductName || '?',
    kexts: kexts.filter(enabled).length, kextsOff: kexts.filter((k) => !enabled(k)).length,
    ssdts: (cfg?.ACPI?.Add || []).filter(enabled).length,
    drivers: (cfg?.UEFI?.Drivers || []).filter(enabled).length,
    cpuid: decodeValue('Kernel/Emulate/Cpuid1Data', cfg?.Kernel?.Emulate?.Cpuid1Data),
    sip: (decodeValue('NVRAM/Add/7C436110-AB2A-4BBB-A880-FE41995C9F82/csr-active-config', cfg?.NVRAM?.Add?.['7C436110-AB2A-4BBB-A880-FE41995C9F82']?.['csr-active-config']) || '').split(':')[0] || null,
    secureBoot: cfg?.Misc?.Security?.SecureBootModel,
    picker: cfg?.Misc?.Boot?.PickerMode,
  };
}

// ------------------------------------------------------------- apply plan ----
/** Apply the selected plan items to a copy of the target config. Identity values are never touched. */
export function applyPlan(target, items) {
  const cfg = clone(target);
  const ensure = (path) => {
    let o = cfg;
    for (const k of path) {
      if (o[k] === undefined) o[k] = {};
      o = o[k];
    }
    return o;
  };
  const sorted = [...items].sort((a, b) => (a.op.type === 'arrOrder') - (b.op.type === 'arrOrder'));   // order last
  for (const it of sorted) {
    const op = it.op;
    if (!op || it.severity === 'identity') continue;
    const spec = ARRAYS[op.path.join('/')];
    if (op.type === 'set') {
      ensure(op.path.slice(0, -1))[op.path[op.path.length - 1]] = clone(op.value);
    } else if (op.type === 'del') {
      const parent = op.path.slice(0, -1).reduce((o, k) => (o ? o[k] : undefined), cfg);
      if (parent) delete parent[op.path[op.path.length - 1]];
    } else if (op.type === 'arrAdd') {
      const arr = ensureArr(cfg, op.path);
      const idx = op.afterKey == null ? -1 : arr.findIndex((e) => spec.key(e) === op.afterKey);
      arr.splice(idx + 1, 0, clone(op.entry));
    } else if (op.type === 'arrDel') {
      const arr = ensureArr(cfg, op.path);
      const i = arr.findIndex((e) => spec.key(e) === op.key);
      if (i >= 0) arr.splice(i, 1);
    } else if (op.type === 'arrSet') {
      const arr = ensureArr(cfg, op.path);
      const e = arr.find((x) => spec.key(x) === op.key);
      if (e) for (const [f, v] of Object.entries(op.fields)) { if (v === undefined) delete e[f]; else e[f] = clone(v); }
    } else if (op.type === 'arrOrder') {
      const arr = ensureArr(cfg, op.path);
      const pos = new Map(op.order.map((k, i) => [k, i]));
      // entries the reference knows follow its order; target-only entries keep their place relative to the start
      const known = arr.filter((e) => pos.has(spec.key(e))).sort((a, b) => pos.get(spec.key(a)) - pos.get(spec.key(b)));
      let j = 0;
      for (let i = 0; i < arr.length; i++) if (pos.has(spec.key(arr[i]))) arr[i] = known[j++];
    }
  }
  return cfg;
}
function ensureArr(cfg, path) {
  let o = cfg;
  path.slice(0, -1).forEach((k) => { if (o[k] === undefined) o[k] = {}; o = o[k]; });
  const last = path[path.length - 1];
  if (!Array.isArray(o[last])) o[last] = [];
  return o[last];
}

/** Files the target EFI also needs for the selected items. */
export function neededFiles(items) {
  const out = new Map();
  for (const it of items) for (const [dir, name] of it.files || []) out.set(`${dir}/${name}`, { dir, name });
  return [...out.values()].sort((a, b) => a.dir.localeCompare(b.dir) || a.name.localeCompare(b.name));
}

export function planMarkdown(items, names) {
  const L = [`# OpenCore config plan: make ${names.target} match ${names.ref}`, ''];
  const files = neededFiles(items);
  if (files.length) {
    L.push('## Copy these files into the target EFI/OC', '');
    files.forEach((f) => L.push(`- [ ] \`${f.dir}/${f.name}\``));
    L.push('');
  }
  for (const s of SECTIONS) {
    const its = items.filter((i) => i.section === s);
    if (!its.length) continue;
    L.push(`## ${s}`, '');
    for (const it of its) L.push(`- [ ] **${it.label}** (${it.severity}): ${describeOp(it)}${it.explain ? ' - ' + it.explain : ''}`);
    L.push('');
  }
  L.push('Then validate with `ocvalidate config.plist` and keep the target\'s own serial numbers.');
  return L.join('\n');
}

export function describeOp(it) {
  switch (it.kind) {
    case 'entryOnlyRef': return 'add entry';
    case 'entryOnlyTarget': return 'remove entry';
    case 'entryChanged': return 'change ' + (it.fields || []).join(', ');
    case 'onlyRef': return `add = ${show(it.ref, 60)}`;
    case 'onlyTarget': return 'remove key';
    case 'order': return 'reorder like the reference';
    default: return `${show(it.target, 40)} -> ${show(it.ref, 40)}`;
  }
}
export { show, PData, PInt };
