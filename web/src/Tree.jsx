import { useEffect, useMemo, useRef, useState } from 'react';
import { typeOf, deepEqual, show } from './engine/plist.js';
import { ARRAYS } from './engine/compare.js';
import { decodeValue, QUIRKS, SECTION_INFO } from './engine/knowledge.js';

// One merged tree of both configs: reference value | key | target value.
function build(a, b, key, path, depth) {
  const id = path.join('/');
  const ta = a === undefined ? null : typeOf(a), tb = b === undefined ? null : typeOf(b);
  const node = { id, key, depth, a, b, ta, tb, same: a !== undefined && b !== undefined && deepEqual(a, b), children: null };
  const spec = ARRAYS[id];
  if ((ta === 'dict' || tb === 'dict') && ta !== 'array' && tb !== 'array') {
    const A = ta === 'dict' ? a : {}, B = tb === 'dict' ? b : {};
    const keys = [...Object.keys(A), ...Object.keys(B).filter((k) => !(k in A))];
    node.children = keys.map((k) => build(A[k], B[k], k, [...path, k], depth + 1));
  } else if (ta === 'array' || tb === 'array') {
    const A = ta === 'array' ? a : [], B = tb === 'array' ? b : [];
    if (spec) {
      const kb = new Map(B.map((e) => [spec.key(e), e]));
      const ka = new Set(A.map((e) => spec.key(e)));
      const order = [...A.map((e) => [spec.key(e), e, kb.get(spec.key(e))]), ...B.filter((e) => !ka.has(spec.key(e))).map((e) => [spec.key(e), undefined, e])];
      node.children = order.map(([k, ea, eb]) => {
        const label = spec.label(ea ?? eb);
        return build(ea, eb, label, [...path, '#' + k], depth + 1);
      });
    } else {
      const n = Math.max(A.length, B.length);
      node.children = Array.from({ length: n }, (_, i) => build(A[i], B[i], `Item ${i}`, [...path, String(i)], depth + 1));
    }
  }
  // same counting as the plan: an entry/device/key present on one side only is ONE difference
  if (node.children) node.diffs = node.children.reduce((s, c) => s + (c.a === undefined || c.b === undefined ? 1 : c.children ? c.diffs : c.same ? 0 : 1), 0);
  return node;
}

const canon = (id) => id.split('/').filter((s) => !s.startsWith('#')).join('/');

function Cell({ v, t, id }) {
  if (v === undefined) return <span className="absent">-</span>;
  if (t === 'dict') return <span className="cnt">{show(v)}</span>;
  if (t === 'array') return <span className="cnt">{show(v)}</span>;
  const dec = decodeValue(canon(id), v);
  return (
    <span className={`v v-${t}`}>
      {show(v, 140)}
      {dec && <span className="dec">{dec}</span>}
    </span>
  );
}

export default function Tree({ docs, result, names, focus }) {
  const root = useMemo(() => build(docs.ref.cfg, docs.target.cfg, '', [], -1), [docs.ref, docs.target]);
  const [open, setOpen] = useState(() => new Set());
  const [q, setQ] = useState('');
  const [onlyDiff, setOnlyDiff] = useState(true);
  const [flash, setFlash] = useState(null);
  const box = useRef(null);

  // jump from the plan: open the path and scroll to it
  useEffect(() => {
    if (!focus) return;
    const parts = focus.path.split('/');
    setOpen((o) => { const n = new Set(o); parts.forEach((_, i) => n.add(parts.slice(0, i + 1).join('/'))); return n; });
    setOnlyDiff(false);
    setQ('');
    setFlash(focus.path);
    const t = setTimeout(() => {
      const el = box.current?.querySelector(`[data-id="${CSS.escape(focus.path)}"]`);
      el?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      el?.focus({ preventScroll: true });
    }, 60);
    return () => clearTimeout(t);
  }, [focus]);

  const needle = q.trim().toLowerCase();
  const matches = (n) => {
    if (!needle) return true;
    if (String(n.key).toLowerCase().includes(needle)) return true;
    if (!n.children) return [n.a, n.b].some((v) => v !== undefined && show(v, 400).toLowerCase().includes(needle));
    return false;
  };
  // which nodes stay visible with the search / difference filter
  const keep = useMemo(() => {
    const k = new Set();
    const visit = (n) => {
      let any = false;
      if (n.children) for (const c of n.children) any = visit(c) || any;
      const self = matches(n) && (!onlyDiff || (n.children ? n.diffs > 0 : !n.same));
      if (self || any) k.add(n.id);
      return self || any;
    };
    visit(root);
    return k;
  }, [root, needle, onlyDiff]);  // eslint-disable-line react-hooks/exhaustive-deps

  const rows = [];
  const walk = (n) => {
    for (const c of n.children || []) {
      if (!keep.has(c.id)) continue;
      rows.push(c);
      const expanded = needle ? true : open.has(c.id);
      if (c.children && expanded) walk(c);
    }
  };
  walk(root);

  const toggle = (id) => setOpen((o) => { const n = new Set(o); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const expandAll = () => { const s = new Set(); const v = (n) => { s.add(n.id); (n.children || []).forEach(v); }; v(root); setOpen(s); };

  return (
    <div className="browser">
      <div className="browser-tools">
        <input type="search" placeholder="Search keys and values (e.g. layout-id, AirportItlwm, boot-args)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the configs" />
        <label className="toggle"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Only differences</label>
        <button type="button" className="link" onClick={expandAll}>Expand all</button>
        <button type="button" className="link" onClick={() => setOpen(new Set())}>Collapse all</button>
      </div>
      <div className="tree-head">
        <span>{names.ref}</span><span>Key</span><span>{names.target}</span>
      </div>
      <div className="tree" ref={box} role="tree" aria-label="Both configs side by side">
        {rows.length === 0 && <p className="empty">{needle ? `Nothing matches "${q}".` : 'The two configs are identical.'}</p>}
        {rows.map((n) => {
          const state = n.a === undefined ? 'only-tgt' : n.b === undefined ? 'only-ref' : n.same || n.children ? 'same' : 'diff';
          const expanded = needle ? true : open.has(n.id);
          const help = n.depth === 0 ? SECTION_INFO[n.key] : QUIRKS[canon(n.id)];
          return (
            <div key={n.id} data-id={n.id} tabIndex={-1} role="treeitem" aria-level={n.depth + 1}
              aria-expanded={n.children ? expanded : undefined}
              className={`row st-${state}${flash === n.id ? ' is-flash' : ''}${n.children ? ' is-branch' : ''}`}>
              <div className="cell cell-ref"><Cell v={n.a} t={n.ta} id={n.id} /></div>
              <div className="cell cell-key" style={{ '--d': n.depth }}>
                {n.children ? (
                  <button type="button" className="twisty" onClick={() => toggle(n.id)} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${n.key}`}>
                    <span className={`chev${expanded ? ' is-open' : ''}`} aria-hidden="true" />
                    <span className="mono k">{n.key}</span>
                    {n.diffs > 0 && <span className="badge">{n.diffs}</span>}
                  </button>
                ) : (
                  <span className="mono k" title={help || undefined}>{n.key}</span>
                )}
              </div>
              <div className="cell cell-tgt"><Cell v={n.b} t={n.tb} id={n.id} /></div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
