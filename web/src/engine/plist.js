// Apple XML property list <-> JS, keeping every plist type distinct so a config can be
// written back byte-for-byte compatible with OpenCore (integer vs real, data vs string).
//   dict -> plain object (insertion order kept), array -> Array, string -> string, true/false -> boolean
//   integer -> PInt, real -> PReal, data -> PData (Uint8Array), date -> PDate

export class PInt {
  constructor(v) { this.v = v; }            // Number, or BigInt beyond 2^53
  valueOf() { return Number(this.v); }
  toString() { return String(this.v); }
}
export class PReal {
  constructor(v) { this.v = v; }
  valueOf() { return this.v; }
  toString() { return String(this.v); }
}
export class PData {
  constructor(bytes) { this.bytes = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []); }
  get hex() { return Array.from(this.bytes, (b) => b.toString(16).padStart(2, '0')).join('').toUpperCase(); }
  get base64() { return bytesToBase64(this.bytes); }
  toString() { return '<' + this.hex + '>'; }
  static fromHex(h) {
    const s = h.replace(/[^0-9a-f]/gi, '');
    const out = new Uint8Array(s.length >> 1);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
    return new PData(out);
  }
}
export class PDate {
  constructor(iso) { this.iso = iso; }
  toString() { return this.iso; }
}

function base64ToBytes(b64) {
  const clean = (b64 || '').replace(/\s+/g, '');
  if (typeof atob === 'function') {
    const bin = atob(clean);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  return new Uint8Array(Buffer.from(clean, 'base64'));
}
function bytesToBase64(bytes) {
  if (typeof btoa === 'function') {
    let bin = '';
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin);
  }
  return Buffer.from(bytes).toString('base64');
}

// ---------------------------------------------------------------- parse ----
// A tiny tokenizer instead of DOMParser so the same code runs in the browser and in Node tests.
export function parsePlist(text) {
  if (typeof text !== 'string') throw new Error('expected text');
  const src = text.replace(/^﻿/, '');
  if (!/<plist[\s>]/.test(src)) throw new Error('This is not an XML property list (no <plist> element).');
  const re = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!DOCTYPE[^>]*>|<(\/?)([A-Za-z]+)([^>]*?)(\/?)>|([^<]+)/g;
  const tokens = [];
  let m;
  while ((m = re.exec(src))) {
    if (m[2]) tokens.push({ close: !!m[1], tag: m[2], self: !!m[4] || /\/\s*$/.test(m[3] || '') });
    else if (m[5] !== undefined) tokens.push({ text: m[5] });
  }
  let i = 0;
  const skipWs = () => { while (i < tokens.length && tokens[i].text !== undefined && !tokens[i].text.trim()) i++; };
  const textUntil = (tag) => {
    let s = '';
    while (i < tokens.length && !(tokens[i].close && tokens[i].tag === tag)) {
      if (tokens[i].text === undefined) throw new Error(`unexpected <${tokens[i].tag}> inside <${tag}>`);
      s += tokens[i].text; i++;
    }
    if (i >= tokens.length) throw new Error(`missing </${tag}>`);
    i++;
    return decodeEntities(s);
  };
  const value = () => {
    skipWs();
    const t = tokens[i++];
    if (!t || t.text !== undefined || t.close) throw new Error('malformed plist: expected a value');
    switch (t.tag) {
      case 'dict': {
        const o = {};
        if (t.self) return o;
        for (;;) {
          skipWs();
          const k = tokens[i];
          if (!k) throw new Error('missing </dict>');
          if (k.close && k.tag === 'dict') { i++; return o; }
          if (k.tag !== 'key' || k.close) throw new Error('malformed <dict>: expected <key>');
          i++;
          const key = k.self ? '' : textUntil('key');
          o[key] = value();
        }
      }
      case 'array': {
        const a = [];
        if (t.self) return a;
        for (;;) {
          skipWs();
          const k = tokens[i];
          if (!k) throw new Error('missing </array>');
          if (k.close && k.tag === 'array') { i++; return a; }
          a.push(value());
        }
      }
      case 'string': return t.self ? '' : textUntil('string');
      case 'true': return true;
      case 'false': return false;
      case 'integer': {
        const s = (t.self ? '0' : textUntil('integer')).trim();
        const n = Number(s);
        return new PInt(Number.isSafeInteger(n) ? n : BigInt(s));
      }
      case 'real': return new PReal(parseFloat(t.self ? '0' : textUntil('real')));
      case 'data': return new PData(base64ToBytes(t.self ? '' : textUntil('data')));
      case 'date': return new PDate(t.self ? '' : textUntil('date').trim());
      default: throw new Error(`unknown plist element <${t.tag}>`);
    }
  };
  while (i < tokens.length && !(tokens[i].tag === 'plist' && !tokens[i].close)) i++;
  if (i >= tokens.length) throw new Error('no <plist> element');
  i++;
  const root = value();
  return root;
}

function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, e) => {
    if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[e.toLowerCase()];
  });
}
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ---------------------------------------------------------------- write ----
export function writePlist(root) {
  const out = ['<?xml version="1.0" encoding="UTF-8"?>',
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
    '<plist version="1.0">'];
  const w = (v, d) => {
    const pad = '\t'.repeat(d);
    if (v === true) return out.push(pad + '<true/>');
    if (v === false) return out.push(pad + '<false/>');
    if (typeof v === 'string') return out.push(v === '' ? pad + '<string></string>' : `${pad}<string>${esc(v)}</string>`);
    if (v instanceof PInt) return out.push(`${pad}<integer>${v.v}</integer>`);
    if (v instanceof PReal) return out.push(`${pad}<real>${v.v}</real>`);
    if (v instanceof PData) return out.push(`${pad}<data>${v.base64}</data>`);
    if (v instanceof PDate) return out.push(`${pad}<date>${esc(v.iso)}</date>`);
    if (typeof v === 'number') return out.push(`${pad}<integer>${Math.trunc(v)}</integer>`);
    if (Array.isArray(v)) {
      if (!v.length) return out.push(pad + '<array/>');
      out.push(pad + '<array>');
      v.forEach((x) => w(x, d + 1));
      return out.push(pad + '</array>');
    }
    if (v && typeof v === 'object') {
      const keys = Object.keys(v);
      if (!keys.length) return out.push(pad + '<dict/>');
      out.push(pad + '<dict>');
      for (const k of keys) { out.push(`${pad}\t<key>${esc(k)}</key>`); w(v[k], d + 1); }
      return out.push(pad + '</dict>');
    }
    throw new Error('cannot write value ' + String(v));
  };
  w(root, 0);
  out.push('</plist>', '');
  return out.join('\n');
}

// ------------------------------------------------------------ helpers ----
export function typeOf(v) {
  if (v === true || v === false) return 'boolean';
  if (typeof v === 'string') return 'string';
  if (v instanceof PInt) return 'integer';
  if (v instanceof PReal) return 'real';
  if (v instanceof PData) return 'data';
  if (v instanceof PDate) return 'date';
  if (Array.isArray(v)) return 'array';
  if (v && typeof v === 'object') return 'dict';
  return typeof v;
}

export function deepEqual(a, b) {
  const ta = typeOf(a), tb = typeOf(b);
  if (ta !== tb) return false;
  switch (ta) {
    case 'integer': return String(a.v) === String(b.v);
    case 'real': return a.v === b.v;
    case 'data': return a.hex === b.hex;
    case 'date': return a.iso === b.iso;
    case 'array': return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
    case 'dict': {
      const ka = Object.keys(a), kb = Object.keys(b);
      return ka.length === kb.length && ka.every((k) => k in b && deepEqual(a[k], b[k]));
    }
    default: return a === b;
  }
}

export function clone(v) {
  if (v instanceof PInt) return new PInt(v.v);
  if (v instanceof PReal) return new PReal(v.v);
  if (v instanceof PData) return new PData(new Uint8Array(v.bytes));
  if (v instanceof PDate) return new PDate(v.iso);
  if (Array.isArray(v)) return v.map(clone);
  if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v)) o[k] = clone(v[k]); return o; }
  return v;
}

/** Short human form of any plist value (for tables). */
export function show(v, max = 120) {
  const t = typeOf(v);
  let s;
  if (t === 'string') s = v === '' ? '(empty)' : v;
  else if (t === 'boolean') s = v ? 'True' : 'False';
  else if (t === 'data') s = v.bytes.length ? '<' + v.hex + '>' : '<>';
  else if (t === 'array') s = `${v.length} item${v.length === 1 ? '' : 's'}`;
  else if (t === 'dict') s = `${Object.keys(v).length} key${Object.keys(v).length === 1 ? '' : 's'}`;
  else if (v === undefined) s = '(not set)';
  else s = String(v);
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

/** Little-endian integer from data (OpenCore stores ids like this). */
export function leInt(d) {
  if (!(d instanceof PData) || !d.bytes.length || d.bytes.length > 8) return null;
  let n = 0n;
  for (let i = d.bytes.length - 1; i >= 0; i--) n = (n << 8n) | BigInt(d.bytes[i]);
  return n;
}
