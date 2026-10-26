import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { parsePlist, writePlist, show } from './engine/plist.js';
import { compare, applyPlan, neededFiles, planMarkdown, SECTIONS, ARRAYS } from './engine/compare.js';
import { SEVERITY } from './engine/knowledge.js';
import Tree from './Tree.jsx';
import eliteRaw from './samples/config-elitebook-840-g8.plist?raw';
import proRaw from './samples/config-probook-450-g8.plist?raw';

const SIDES = {
  ref: { title: 'Reference', hint: 'The config that works - the one to copy from' },
  target: { title: 'Target', hint: 'The config to fix - it will be changed to match' },
};
const SEV_ORDER = ['critical', 'hardware', 'minor', 'inactive', 'identity'];

function readFile(file) {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result));
    r.onerror = () => bad(r.error);
    r.readAsText(file);
  });
}

function load(name, text) {
  try {
    const cfg = parsePlist(text);
    if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw new Error('The top level is not a dictionary.');
    const known = SECTIONS.filter((s) => s in cfg).length;
    if (known < 3) throw new Error('This plist does not look like an OpenCore config (missing ACPI / Kernel / Misc...).');
    return { name, cfg, error: null };
  } catch (e) {
    return { name, cfg: null, error: e.message };
  }
}

function download(name, text, type = 'application/xml') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ------------------------------------------------------------------ slot ----
function Slot({ side, doc, onLoad }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const take = async (files) => {
    const f = files && files[0];
    if (f) onLoad(side, load(f.name, await readFile(f)));
  };
  return (
    <div
      className={`slot slot-${side}${over ? ' is-over' : ''}${doc?.cfg ? ' is-loaded' : ''}${doc?.error ? ' is-error' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
    >
      <div className="slot-head">
        <span className="slot-title">{SIDES[side].title}</span>
        <span className="slot-hint">{SIDES[side].hint}</span>
      </div>
      {doc?.cfg ? (
        <div className="slot-file"><span className="mono">{doc.name}</span></div>
      ) : (
        <p className="slot-empty">Drop a config.plist here</p>
      )}
      {doc?.error && <p className="slot-error" role="alert">{doc.error}</p>}
      <button type="button" className="btn btn-quiet" onClick={() => input.current.click()}>
        {doc?.cfg ? 'Choose another file' : 'Choose config.plist'}
      </button>
      <input ref={input} type="file" accept=".plist,.xml,application/xml,text/xml" hidden onChange={(e) => { take(e.target.files); e.target.value = ''; }} />
    </div>
  );
}

// --------------------------------------------------------------- summary ----
function Overview({ s }) {
  const rows = [
    ['Mac model', s.ref.model, s.target.model],
    ['Kexts loaded', `${s.ref.kexts}${s.ref.kextsOff ? ` (+${s.ref.kextsOff} off)` : ''}`, `${s.target.kexts}${s.target.kextsOff ? ` (+${s.target.kextsOff} off)` : ''}`],
    ['SSDTs', s.ref.ssdts, s.target.ssdts],
    ['UEFI drivers', s.ref.drivers, s.target.drivers],
    ['CPU', s.ref.cpuid, s.target.cpuid],
    ['SIP', s.ref.sip, s.target.sip],
    ['Boot picker', s.ref.picker, s.target.picker],
  ];
  return (
    <table className="overview">
      <tbody>
        {rows.map(([k, a, b]) => (
          <tr key={k} className={String(a) !== String(b) ? 'differs' : ''}>
            <td className="ov-ref">{a ?? '-'}</td>
            <th scope="row">{k}</th>
            <td className="ov-tgt">{b ?? '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ------------------------------------------------------------------ plan ----
const ACTION = {
  entryOnlyRef: 'add', entryOnlyTarget: 'remove', entryChanged: 'change', onlyRef: 'add key', onlyTarget: 'remove key', order: 'reorder', changed: 'set',
};
function valueText(it, side) {
  const v = it[side];
  const dec = side === 'ref' ? it.decodedRef : it.decodedTarget;
  if (it.kind === 'order') return side === 'ref' ? 'reference order' : 'current order';
  if (v === undefined) return null;
  if (it.kind.startsWith('entry')) {
    if (typeof v !== 'object') return show(v);
    if (it.kind === 'entryChanged') return (it.fields || []).map((f) => `${f} = ${show(v[f], 40)}`).join(', ');
    const range = v.MinKernel || v.MaxKernel ? `, Darwin ${v.MinKernel || 'any'} to ${v.MaxKernel || 'any'}` : '';
    return (v.Enabled === false ? 'disabled' : 'enabled') + range;
  }
  return dec ? `${show(v, 60)}  (${dec})` : show(v, 90);
}

function PlanRow({ it, on, toggle, onBrowse }) {
  const a = valueText(it, 'ref'), b = valueText(it, 'target');
  const locked = it.severity === 'identity';
  return (
    <li className={`step sev-${it.severity}${on ? ' is-on' : ''}`}>
      <label className="step-top">
        <input type="checkbox" checked={on} disabled={locked} onChange={() => toggle(it.id)}
          aria-label={`${ACTION[it.kind] || 'set'} ${it.label}`} />
        <span className="step-label mono">{it.label}</span>
        <span className={`sev sev-tag-${it.severity}`}>{SEVERITY[it.severity].label}</span>
      </label>
      <div className="spine-row">
        <div className="val val-ref">{a ?? <span className="absent">not in reference</span>}</div>
        <div className={`act act-${it.kind}`}>{locked ? 'keep' : ACTION[it.kind] || 'set'}</div>
        <div className="val val-tgt">{b ?? <span className="absent">not in target</span>}</div>
      </div>
      {it.explain && <p className="step-why">{it.explain}</p>}
      <button type="button" className="link" onClick={() => onBrowse(it.kind.startsWith('entry') && ARRAYS[it.path]
        ? `${it.path}/#${ARRAYS[it.path].key(it.ref ?? it.target)}` : it.path)}>Show in the config browser</button>
    </li>
  );
}

function Plan({ result, selected, setSelected, names, onBrowse, target }) {
  const [sevs, setSevs] = useState(new Set(['critical', 'hardware', 'minor', 'identity']));
  const [section, setSection] = useState(null);
  const [copied, setCopied] = useState(false);
  const items = result.items;
  const visible = items.filter((i) => sevs.has(i.severity) && (!section || i.section === section));
  const chosen = items.filter((i) => selected.has(i.id));
  const files = neededFiles(chosen);
  const hidden = chosen.filter((i) => !visible.includes(i)).length;
  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setMany = (list, on) => setSelected((s) => { const n = new Set(s); list.forEach((i) => (on && i.severity !== 'identity' ? n.add(i.id) : n.delete(i.id))); return n; });
  const counts = Object.fromEntries(SEV_ORDER.map((k) => [k, items.filter((i) => i.severity === k).length]));

  const matchedXml = () => writePlist(applyPlan(target, chosen));
  const exportConfig = () => {
    const base = names.target.replace(/\.plist$/i, '');
    download(`${base}-matched.plist`, matchedXml());
  };
  const [copiedCfg, setCopiedCfg] = useState(false);
  const copyConfig = async () => {
    try { await navigator.clipboard.writeText(matchedXml()); setCopiedCfg(true); setTimeout(() => setCopiedCfg(false), 2000); }
    catch { exportConfig(); }
  };
  const copyPlan = async () => {
    const md = planMarkdown(chosen, names);
    try { await navigator.clipboard.writeText(md); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { download('opencore-plan.md', md, 'text/markdown'); }
  };

  return (
    <div className="plan">
      <aside className="rail" aria-label="Filter the plan">
        <h3>Show</h3>
        {SEV_ORDER.map((k) => (
          <label key={k} className={`filter sev-tag-${k}`}>
            <input type="checkbox" checked={sevs.has(k)} onChange={() => setSevs((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; })} />
            <span>{SEVERITY[k].label}</span><span className="count">{counts[k]}</span>
          </label>
        ))}
        <h3>Section</h3>
        <button type="button" className={`sec${!section ? ' is-active' : ''}`} onClick={() => setSection(null)}>
          All sections <span className="count">{items.length}</span>
        </button>
        {result.sections.filter((s) => s.count).map((s) => (
          <button type="button" key={s.name} title={s.info} className={`sec${section === s.name ? ' is-active' : ''}`} onClick={() => setSection(s.name)}>
            {s.name} <span className="count">{s.count}{s.critical ? <b> / {s.critical}</b> : null}</span>
          </button>
        ))}
        <p className="rail-note">Bold numbers are boot-critical differences.</p>
      </aside>

      <section className="steps-wrap" aria-label="Plan">
        <div className="steps-head">
          <div className="steps-col ref">{names.ref}</div>
          <div className="steps-col mid">
            <button type="button" className="link" onClick={() => setMany(visible, true)}>Select shown</button>
            <button type="button" className="link" onClick={() => setMany(visible, false)}>Clear shown</button>
          </div>
          <div className="steps-col tgt">{names.target}</div>
        </div>
        {visible.length === 0 ? (
          <p className="empty">Nothing to show with these filters.</p>
        ) : (
          <ol className="steps">
            {visible.map((it) => <PlanRow key={it.id} it={it} on={selected.has(it.id)} toggle={toggle} onBrowse={onBrowse} />)}
          </ol>
        )}
      </section>

      <div className="dock" role="region" aria-label="Apply the plan">
        <div className="dock-info">
          <strong>{chosen.length}</strong> of {items.filter((i) => i.severity !== 'identity').length} changes selected
          {hidden > 0 && <span className="dock-hidden"> ({hidden} of them hidden by your filters)</span>}
          {files.length > 0 && (
            <details className="files">
              <summary>{files.length} files to copy into the target EFI/OC</summary>
              <ul>{files.map((f) => <li key={f.dir + f.name} className="mono">{f.dir}/{f.name}</li>)}</ul>
            </details>
          )}
        </div>
        <div className="dock-actions">
          <button type="button" className="btn" onClick={copyPlan}>{copied ? 'Plan copied' : 'Copy plan as checklist'}</button>
          <button type="button" className="btn" onClick={copyConfig} disabled={!chosen.length}>{copiedCfg ? 'Config copied' : 'Copy matched config'}</button>
          <button type="button" className="btn btn-primary" onClick={exportConfig} disabled={!chosen.length}>Download matched config</button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- health ----
function Health({ result, names }) {
  return (
    <div className="health">
      {['ref', 'target'].map((side) => (
        <section key={side} className={`health-col health-${side}`}>
          <h2>{names[side]}</h2>
          {result.lints[side].length === 0 ? <p className="empty">No problems found.</p> : (
            <ul>
              {result.lints[side].map((l, i) => (
                <li key={i} className={`lint sev-${l.severity}`}>
                  <span className={`sev sev-tag-${l.severity}`}>{SEVERITY[l.severity].label}</span>
                  <strong>{l.title}</strong>
                  <p>{l.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
      <p className="health-note">Problems in the reference are copied along with it - fix them there too, or untick those steps in the plan.</p>
    </div>
  );
}

// ----------------------------------------------------------------- theme ----
const THEME_KEY = 'hackpe-theme';
function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute('data-theme') || 'light');
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); }, [theme]);
  useEffect(() => {                                    // follow the system until the user picks a theme
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return undefined;
    const onChange = (e) => {
      let saved = null;
      try { saved = localStorage.getItem(THEME_KEY); } catch { /* storage blocked */ }
      if (!saved) setTheme(e.matches ? 'dark' : 'light');
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  const toggle = () => setTheme((t) => {
    const next = t === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage blocked: still switches for this visit */ }
    return next;
  });
  return [theme, toggle];
}

function ThemeSwitch() {
  const [theme, toggle] = useTheme();
  const dark = theme === 'dark';
  return (
    <button type="button" className="theme" role="switch" aria-checked={dark} onClick={toggle}
      title={dark ? 'Switch to the light theme' : 'Switch to the dark theme'}>
      <span className="theme-track" aria-hidden="true">
        <span className="theme-knob">
          {dark
            ? <svg viewBox="0 0 12 12"><path d="M9.5 7.5A4 4 0 0 1 4.5 2.5a4 4 0 1 0 5 5z" /></svg>
            : <svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="2.2" /><path d="M6 .8v1.2M6 10v1.2M.8 6H2M10 6h1.2M2.3 2.3l.8.8M8.9 8.9l.8.8M2.3 9.7l.8-.8M8.9 3.1l.8-.8" /></svg>}
        </span>
      </span>
      Dark theme
    </button>
  );
}

// ------------------------------------------------------------------- app ----
export default function App() {
  const [docs, setDocs] = useState({ ref: null, target: null });
  const [tab, setTab] = useState('plan');
  const [focus, setFocus] = useState(null);
  const ready = docs.ref?.cfg && docs.target?.cfg;
  const result = useMemo(() => (ready ? compare(docs.ref.cfg, docs.target.cfg) : null), [ready, docs.ref, docs.target]);
  const [selected, setSelected] = useState(new Set());
  useEffect(() => { if (result) setSelected(new Set(result.items.filter((i) => i.defaultOn).map((i) => i.id))); }, [result]);

  const onLoad = useCallback((side, doc) => setDocs((d) => ({ ...d, [side]: doc })), []);
  const example = () => setDocs({ ref: load('config-elitebook-840-g8.plist', eliteRaw), target: load('config-probook-450-g8.plist', proRaw) });
  const swap = () => setDocs((d) => ({ ref: d.target, target: d.ref }));
  const names = { ref: docs.ref?.name || 'reference', target: docs.target?.name || 'target' };
  const browse = (path) => { setFocus({ path, t: Date.now() }); setTab('browse'); };

  return (
    <div className={`app${ready ? ' is-ready' : ''}`}>
      <header className="top">
        <a className="brand" href="https://github.com/labrouss/HackPE" aria-label="HackPE on GitHub">
          <svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" /><path d="M9 8v16M23 8v16M9 16h14" /></svg>
          <span>HackPE</span>
        </a>
        <span className="top-title">Config compare</span>
        <span className="top-note">Everything runs in your browser. Your config files are never uploaded.</span>
        <ThemeSwitch />
      </header>

      <section className="hero">
        <h1>Make one OpenCore config match another</h1>
        <p className="lede">
          Load a config.plist that boots and one that doesn&rsquo;t. You get every difference that matters, explained, a health check of
          both files, and a step-by-step plan you can apply and download.
        </p>
        <div className="slots">
          <Slot side="ref" doc={docs.ref} onLoad={onLoad} />
          <div className="slots-mid">
            <button type="button" className="swap" onClick={swap} disabled={!docs.ref && !docs.target} title="Swap reference and target">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h11l-3-3M17 17H6l3 3" /></svg>
              <span className="sr">Swap reference and target</span>
            </button>
          </div>
          <Slot side="target" doc={docs.target} onLoad={onLoad} />
        </div>
        {!ready && (
          <p className="try">
            No configs at hand? <button type="button" className="link" onClick={example}>Compare an HP EliteBook 840 G8 with a ProBook 450 G8</button>
            {' '}(serial numbers replaced).
          </p>
        )}
      </section>

      {result && (
        <main className="work">
          <Overview s={result.summary} />
          <nav className="tabs" role="tablist" aria-label="Views">
            {[['plan', `Plan (${result.items.length} differences)`], ['health', `Health check (${result.lints.ref.length + result.lints.target.length})`], ['browse', 'Browse configs']].map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={`tab${tab === k ? ' is-active' : ''}`} onClick={() => setTab(k)}>{label}</button>
            ))}
          </nav>
          {tab === 'plan' && <Plan result={result} selected={selected} setSelected={setSelected} names={names} onBrowse={browse} target={docs.target.cfg} />}
          {tab === 'health' && <Health result={result} names={names} />}
          {tab === 'browse' && <Tree docs={docs} result={result} names={names} focus={focus} />}
        </main>
      )}

      <footer className="foot">
        <p>
          Part of <a href="https://github.com/labrouss/HackPE">HackPE</a>, the bootable hackintosh toolkit. After applying a plan, run
          <span className="mono"> ocvalidate config.plist</span> and keep each PC&rsquo;s own serial numbers.
        </p>
      </footer>
    </div>
  );
}
