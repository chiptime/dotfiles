'use strict';
// Deterministic behavior tests for the embedded catalogue page.
// No browser, no network, no live data: the inline scripts of static/index.html
// run inside a vm context against a tiny DOM stub driven by virtual timers.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');
const SCRIPTS = [...SOURCE.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
  .map(m => ({ attrs: m[1], body: m[2] }));
const NAV_JS = (SCRIPTS.find(s => /id="portal-nav"/.test(s.attrs)) || {}).body;
const MAIN_JS = (SCRIPTS.find(s => !/id="portal-nav"/.test(s.attrs)) || {}).body;
const STYLE = (SOURCE.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1];
const MARKUP = SOURCE.replace(/<!doctype[^>]*>/i, '')
  .replace(/<script[\s\S]*?<\/script>/g, '').replace(/<style>[\s\S]*?<\/style>/g, '');

// ---------------------------------------------------------------- DOM stub
const VOID = new Set(['input', 'meta', 'link', 'br', 'img', 'hr']);
const decode = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'")
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

function parseAttrs(src) {
  const attrs = {};
  const re = /([^\s=\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(src))) attrs[m[1]] = decode(m[2] ?? m[3] ?? m[4] ?? '');
  return attrs;
}

function simple(sel) {
  const m = /^([a-zA-Z][\w-]*)?((?:[.#][\w-]+|\[[^\]]+\])*)$/.exec(sel.trim());
  if (!m) throw new Error('unsupported selector: ' + sel);
  const preds = [];
  if (m[1]) preds.push(e => e.localName === m[1].toLowerCase());
  for (const p of m[2].match(/[.#][\w-]+|\[[^\]]+\]/g) || []) {
    if (p[0] === '.') preds.push(e => (e.attrs.class || '').split(/\s+/).includes(p.slice(1)));
    else if (p[0] === '#') preds.push(e => e.attrs.id === p.slice(1));
    else {
      const a = /^\[([\w-]+)(?:=(?:"([^"]*)"|([^\]]*)))?\]$/.exec(p);
      const v = a[2] ?? a[3];
      preds.push(e => (v === undefined ? a[1] in e.attrs : e.attrs[a[1]] === v));
    }
  }
  return e => preds.every(f => f(e));
}
const matcher = sel => { const fs_ = sel.split(',').map(simple); return e => fs_.some(f => f(e)); };

class El {
  constructor(tag, attrs, doc) {
    this.localName = tag; this.tagName = tag.toUpperCase();
    this.attrs = attrs; this.children = []; this.parent = null; this.doc = doc;
    this.listeners = {}; this.writes = 0; this.style = {}; this._value = attrs.value ?? '';
  }
  get id() { return this.attrs.id || ''; }
  get hidden() { return 'hidden' in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  get textContent() {
    return this.children.map(c => (typeof c === 'string' ? c : c.textContent)).join('');
  }
  set textContent(v) { this.children = [String(v)]; this.writes++; }
  set innerHTML(html) {
    if (this.doc.activeElement && this.contains(this.doc.activeElement)) {
      this.doc.activeElement = this.doc.body; // the focused node is gone, like a browser
    }
    this.children = []; this.writes++;
    parseInto(this, html, this.doc);
  }
  get innerHTML() { return ''; }
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  hasAttribute(n) { return n in this.attrs; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  contains(n) { for (let x = n; x; x = x.parent) if (x === this) return true; return false; }
  closest(sel) {
    const ok = matcher(sel);
    for (let x = this; x && x.localName !== '#root'; x = x.parent) if (ok(x)) return x;
    return null;
  }
  querySelectorAll(sel) {
    const ok = matcher(sel); const out = [];
    const walk = n => n.children.forEach(c => { if (typeof c !== 'string') { if (ok(c)) out.push(c); walk(c); } });
    walk(this); return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  focus() { this.doc.activeElement = this; }
  select() {}
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
}

function parseInto(parent, html, doc) {
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>|([^<]+|<)/g;
  const stack = [parent]; let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith('<!--')) continue;
    const top = stack[stack.length - 1];
    if (m[1]) {
      const i = stack.map(n => n.localName).lastIndexOf(m[1].toLowerCase());
      if (i > 0) stack.length = i;
    } else if (m[2]) {
      const tag = m[2].toLowerCase();
      const el = new El(tag, parseAttrs(m[3].replace(/\/\s*$/, '')), doc);
      el.parent = top; top.children.push(el);
      if (!VOID.has(tag) && !/\/\s*$/.test(m[3])) stack.push(el);
    } else top.children.push(decode(m[4]));
  }
}

class Doc {
  constructor(markup) {
    this.root = new El('#root', {}, this);
    parseInto(this.root, markup, this);
    this.body = this.root.querySelector('body') || this.root;
    this.activeElement = this.body;
    this.execCopied = [];
    this.execOk = true;
  }
  getElementById(id) { return this.root.querySelectorAll('#' + id)[0] || null; }
  querySelectorAll(sel) { return this.root.querySelectorAll(sel); }
  createElement(tag) { return new El(tag, {}, this); }
  execCommand(cmd) {
    const ta = this.body.children.filter(c => typeof c !== 'string' && c.localName === 'textarea').pop();
    if (cmd === 'copy' && this.execOk && ta) this.execCopied.push(ta.value);
    return cmd === 'copy' && this.execOk;
  }
}

function makeClock() {
  let now = 0; let seq = 0; const timers = new Map();
  return {
    setTimeout: (fn, ms = 0) => { timers.set(++seq, { fn, at: now + ms, every: 0 }); return seq; },
    setInterval: (fn, ms) => { timers.set(++seq, { fn, at: now + ms, every: ms }); return seq; },
    clearTimeout: id => timers.delete(id),
    clearInterval: id => timers.delete(id),
    intervals: () => [...timers.values()].filter(t => t.every).map(t => t.every),
    tick(ms) {
      const end = now + ms;
      for (;;) {
        let next = null; let nid;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next.at)) { next = t; nid = id; }
        if (!next) break;
        now = next.at;
        if (next.every) next.at += next.every; else timers.delete(nid);
        next.fn();
      }
      now = end;
    },
  };
}

const flush = () => new Promise(r => setImmediate(r));

function fire(el, type, extra = {}) {
  const ev = { type, target: el, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (let n = el; n; n = n.parent) {
    ev.currentTarget = n;
    (n.listeners[type] || []).forEach(fn => fn(ev));
  }
  return ev;
}

// ---------------------------------------------------------------- fixtures
const TILES = [
  { id: 'ai-quotas', kind: 'web', name: 'AI Quotas', icon: '📊', url: 'http://127.0.0.1:47623', host: '127.0.0.1:47623',
    description: 'Cuotas IA locales', health: { state: 'up', latency_ms: 12 }, tags: ['ia'] },
  { id: 'voice', kind: 'web', name: 'Voz', url: 'http://voz.lan:1', host: 'voz.lan:1',
    description: '', health: { state: 'down', error: 'refused' }, tags: ['voz'] },
  { id: 'sap-search', kind: 'script', name: 'SAP search', command: 'sap-search --all',
    location: '~/.dotfiles/scripts/sap-search.sh', description: 'Busca en SAP', tags: ['sap'] },
  { id: 'docs-only', kind: 'script', name: 'Docs only', docs: '~/doc.md', description: 'sin comando' },
  { id: 'teams-pull', kind: 'script', name: 'Teams pull', command: 'teams-pull', tags: ['teams'] },
  { id: 'quotas-rs', kind: 'project', name: 'Quotas rs', location: '~/Code/quotas', tags: ['personal', 'ia'] },
  { id: 'sis', kind: 'project', name: 'Clece <SIS> & "friends" con un nombre largo que debe verse completo',
    location: '/work/sis/Mixed-Case', tags: ['trabajo', 'sap'] },
  { id: 'nowhere', kind: 'project', name: 'No location', tags: ['personal'] },
];

async function boot({ tiles = TILES, secure = true, host = 'lan.test' } = {}) {
  const doc = new Doc(MARKUP);
  const clock = makeClock();
  const state = { tiles, fail: null, clipboardFail: false, calls: [], copied: [] };
  const win = { isSecureContext: secure, location: { protocol: 'http:', hostname: host } };
  const sandbox = {
    document: doc, window: win, console, clock,
    navigator: { clipboard: { writeText: async t => { if (state.clipboardFail) throw new Error('denied'); state.copied.push(t); } } },
    fetch: async (url, opts) => {
      state.calls.push({ url, opts });
      if (state.fail === 'net') throw new Error('network down');
      if (state.fail) return { ok: false, status: state.fail, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ tiles: structuredClone(state.tiles) }) };
    },
    setTimeout: clock.setTimeout, setInterval: clock.setInterval,
    clearTimeout: clock.clearTimeout, clearInterval: clock.clearInterval,
  };
  const ctx = vm.createContext(sandbox);
  if (NAV_JS) vm.runInContext(NAV_JS, ctx);
  vm.runInContext(MAIN_JS, ctx);
  await flush();
  const $ = id => doc.getElementById(id);
  const api = {
    doc, ctx, state, clock, $,
    names: grid => $(grid).querySelectorAll('.name').map(n => n.textContent),
    chips: group => $(group).querySelectorAll('button').map(b => ({
      value: b.getAttribute('data-value'), pressed: b.getAttribute('aria-pressed'),
      label: b.children.find(c => typeof c === 'string').trim(),
      count: Number(b.querySelector('.n').textContent),
    })),
    chip: (group, value) => $(group).querySelectorAll('button').find(b => b.getAttribute('data-value') === value),
    tile: id => doc.root.querySelectorAll('[data-key]').find(n => n.getAttribute('data-key') === 'tile:' + id),
    allNames: () => ['grid-web', 'grid-script', 'grid-project'].flatMap(g => api.names(g)),
    type: value => { fire(api.chip('type-chips', value), 'click'); },
    topic: value => { fire(api.chip('topic-chips', value), 'click'); },
    search: text => { $('search').value = text; fire($('search'), 'input'); },
    poll: async () => { clock.tick(5000); await flush(); },
    reload: async () => { vm.runInContext('load()', ctx); await flush(); },
    flush,
  };
  return api;
}

// ---------------------------------------------------------------- tests
test('static shell: Ecosistema header, labelled navigation, active page, no external assets', () => {
  assert.ok(NAV_JS && MAIN_JS, 'portal-nav and main inline scripts exist');
  const doc = new Doc(MARKUP);
  const header = doc.root.querySelector('header');
  assert.match(header.textContent, /Ecosistema/);
  const nav = header.querySelector('nav');
  assert.ok(nav.getAttribute('aria-label'));
  const links = nav.querySelectorAll('a');
  assert.deepEqual(links.map(a => a.textContent.trim()), ['Catálogo', 'Proyectos', 'Seguimiento']);
  assert.deepEqual(links.map(a => a.getAttribute('data-portal-port')), ['47625', '47624', '47624']);
  assert.deepEqual(links.map(a => a.getAttribute('data-portal-path')), ['/', '/PROYECTOS.html', '/PROJECTS.html']);
  assert.deepEqual(links.map(a => a.getAttribute('aria-current')), ['page', null, null]);
  assert.equal(doc.root.querySelectorAll('h1').length, 1);
  assert.ok(doc.root.querySelector('a.skip'), 'skip link');
  assert.ok(doc.root.querySelector('meta[name="viewport"]'));
  assert.doesNotMatch(SOURCE, /<iframe|<link\b|<script[^>]*\bsrc=|@import|url\(\s*["']?https?:/i);
  assert.doesNotMatch(SOURCE, /<(?:img|source|video|audio)[^>]+src=["']https?:/i);
});

test('static shell: focus, reduced motion, readable width and 375px reflow rules', () => {
  assert.match(STYLE, /:focus-visible/);
  assert.match(STYLE, /prefers-reduced-motion:\s*reduce/);
  assert.match(STYLE, /max-width:\s*\d+(\.\d+)?(rem|px)/);
  assert.match(STYLE, /minmax\(min\(100%/);
  const name = /\.name\s*\{([^}]*)\}/.exec(STYLE)[1];
  assert.match(name, /overflow-wrap:\s*anywhere/);
  assert.doesNotMatch(name, /ellipsis|nowrap/);
});

test('navigation derives the current hostname instead of hard-coded loopback', () => {
  const run = (location) => {
    const doc = new Doc(MARKUP);
    vm.runInNewContext(NAV_JS, { document: doc, window: { location } });
    return doc.root.querySelectorAll('a[data-portal-port]').map(a => a.getAttribute('href'));
  };
  assert.deepEqual(run({ protocol: 'http:', hostname: '192.168.1.20' }),
    ['http://192.168.1.20:47625/', 'http://192.168.1.20:47624/PROYECTOS.html', 'http://192.168.1.20:47624/PROJECTS.html']);
  assert.deepEqual(run({ protocol: 'https:', hostname: 'portal.lan' }),
    ['https://portal.lan:47625/', 'https://portal.lan:47624/PROYECTOS.html', 'https://portal.lan:47624/PROJECTS.html']);
  assert.deepEqual(run({ protocol: 'http:', hostname: '[::1]' }),
    ['http://[::1]:47625/', 'http://[::1]:47624/PROYECTOS.html', 'http://[::1]:47624/PROJECTS.html']);
  assert.deepEqual(run({ protocol: 'file:', hostname: '' }),
    ['http://localhost:47625/', 'http://localhost:47624/PROYECTOS.html', 'http://localhost:47624/PROJECTS.html']);
});

test('initial load: sections, summary and chip groups', async () => {
  const p = await boot();
  assert.deepEqual(p.names('grid-web'), ['AI Quotas', 'Voz']);
  assert.deepEqual(p.names('grid-script'), ['SAP search', 'Docs only', 'Teams pull']);
  assert.equal(p.names('grid-project').length, 3);
  assert.equal(p.$('summary').textContent, '2 servicios · 3 scripts · 3 proyectos');
  assert.equal(p.$('loading').hidden, true);
  assert.equal(p.$('empty').hidden, true);
  assert.equal(p.$('noresults').hidden, true);
  assert.equal(p.$('loaderror').hidden, true);
  assert.equal(p.$('reset').hidden, true);
  assert.deepEqual(p.chips('type-chips').map(c => [c.value, c.label, c.count, c.pressed]), [
    ['all', 'Todos', 8, 'true'], ['web', 'Servicios', 2, 'false'], ['script', 'Scripts', 3, 'false'],
    ['personal', 'Proyectos personales', 2, 'false'], ['trabajo', 'Proyectos de trabajo', 1, 'false']]);
  assert.deepEqual(p.chips('topic-chips').map(c => [c.value, c.label, c.count, c.pressed]), [
    ['all', 'Todos los temas', 8, 'true'], ['ia', 'IA', 2, 'false'], ['sap', 'SAP', 2, 'false'],
    ['teams', 'Teams', 1, 'false'], ['voz', 'Voz', 1, 'false']]);
  assert.equal(p.clock.intervals().includes(5000), true);
  assert.deepEqual(p.state.calls.map(c => c.url), ['/api/tiles']);
});

test('type and topic are independent filters that combine with text (AND)', async () => {
  const p = await boot();
  p.type('personal');
  assert.deepEqual(p.allNames(), ['Quotas rs', 'No location']);
  assert.equal(p.chip('type-chips', 'personal').getAttribute('aria-pressed'), 'true');
  assert.equal(p.chip('type-chips', 'all').getAttribute('aria-pressed'), 'false');
  p.topic('ia');
  assert.deepEqual(p.allNames(), ['Quotas rs']);
  // type counts are faceted by the topic, topic counts by the type
  assert.deepEqual(p.chips('type-chips').map(c => c.count), [2, 1, 0, 1, 0]);
  assert.deepEqual(p.chips('topic-chips').map(c => c.count), [2, 1, 0, 0, 0]);
  p.type('personal'); // pressing the active type again clears just the type
  assert.deepEqual(p.allNames().sort(), ['AI Quotas', 'Quotas rs']);
  p.search('quotas rs');
  assert.deepEqual(p.allNames(), ['Quotas rs']);
  p.type('web');
  assert.deepEqual(p.allNames(), []);
  assert.equal(p.$('noresults').hidden, false);
  assert.match(p.$('summary').textContent, /^0 de 8 elementos$/);
});

test('scope tags never leak into the topic chips and topic tags label custom values', async () => {
  const p = await boot({ tiles: [...TILES, { id: 'x', kind: 'web', name: 'Nuevo', url: 'http://x:1', host: 'x:1', tags: ['laboratorio'] }] });
  const topics = p.chips('topic-chips').map(c => c.value);
  assert.deepEqual(topics, ['all', 'ia', 'laboratorio', 'sap', 'teams', 'voz']);
  assert.equal(p.chips('topic-chips').find(c => c.value === 'laboratorio').label, 'Laboratorio');
});

test('search is case-insensitive, trimmed and covers location and command', async () => {
  const p = await boot();
  p.search('  MIXED-case ');
  assert.deepEqual(p.allNames(), [TILES[6].name]);
  p.search('SAP-SEARCH --all');
  assert.deepEqual(p.allNames(), ['SAP search']);
  p.search('~/code/QUOTAS');
  assert.deepEqual(p.allNames(), ['Quotas rs']);
  p.search('127.0.0.1:47623');
  assert.deepEqual(p.allNames(), ['AI Quotas']);
});

test('reset clears type, topic and text and is reachable from the empty result', async () => {
  const p = await boot();
  p.type('script'); p.topic('sap'); p.search('zzz');
  assert.equal(p.$('reset').hidden, false);
  assert.equal(p.$('noresults').hidden, false);
  fire(p.$('noresults-reset'), 'click');
  assert.equal(p.$('search').value, '');
  assert.equal(p.allNames().length, 8);
  assert.equal(p.$('reset').hidden, true);
  assert.equal(p.$('noresults').hidden, true);
  p.type('web'); p.search('ai');
  fire(p.$('reset'), 'click');
  assert.equal(p.chip('type-chips', 'all').getAttribute('aria-pressed'), 'true');
  assert.equal(p.chip('topic-chips', 'all').getAttribute('aria-pressed'), 'true');
  assert.equal(p.allNames().length, 8);
  assert.equal(p.doc.activeElement, p.$('search'), 'focus moves to the search box when reset disappears');
});

test('empty, loading and error states are explicit', async () => {
  const empty = await boot({ tiles: [] });
  assert.equal(empty.$('empty').hidden, false);
  assert.equal(empty.$('noresults').hidden, true);
  assert.equal(empty.$('summary').textContent, '');

  const down = await (async () => {
    const doc = new Doc(MARKUP);
    const clock = makeClock();
    const ctx = vm.createContext({ document: doc, window: { isSecureContext: true, location: { protocol: 'http:', hostname: 'h' } },
      navigator: {}, fetch: async () => ({ ok: false, status: 503, json: async () => ({}) }),
      setTimeout: clock.setTimeout, setInterval: clock.setInterval, clearTimeout: clock.clearTimeout });
    vm.runInContext(MAIN_JS, ctx);
    assert.equal(doc.getElementById('loading').hidden, false, 'loading before the first response');
    assert.equal(doc.getElementById('empty').hidden, true, 'no false "sin tiles" while loading');
    await flush();
    return doc;
  })();
  assert.equal(down.getElementById('loading').hidden, true);
  assert.equal(down.getElementById('empty').hidden, true);
  assert.equal(down.getElementById('loaderror').hidden, false);
  assert.match(down.getElementById('loaderror').textContent, /503/);
  assert.equal(down.getElementById('loaderror').getAttribute('role'), 'alert');
});

test('a failed poll keeps the last good catalogue visible and recovers', async () => {
  const p = await boot();
  p.type('web');
  p.state.fail = 'net';
  await p.poll();
  assert.equal(p.$('loaderror').hidden, false);
  assert.match(p.$('loaderror').textContent, /network down/);
  assert.deepEqual(p.allNames(), ['AI Quotas', 'Voz']);
  p.state.fail = null;
  await p.poll();
  assert.equal(p.$('loaderror').hidden, true);
  assert.deepEqual(p.allNames(), ['AI Quotas', 'Voz']);
});

test('services open their URL; scripts and projects never navigate', async () => {
  const p = await boot();
  const web = p.tile('ai-quotas');
  assert.equal(web.localName, 'a');
  assert.equal(web.getAttribute('href'), 'http://127.0.0.1:47623');
  assert.equal(web.getAttribute('target'), '_blank');
  assert.match(web.getAttribute('rel'), /noopener/);
  assert.equal(web.hasAttribute('data-copy'), false);
  assert.match(web.textContent, /12 ms/);
  assert.match(p.tile('voice').textContent, /down/);
  for (const id of ['sap-search', 'quotas-rs', 'docs-only', 'nowhere']) {
    assert.notEqual(p.tile(id).localName, 'a');
    assert.equal(p.tile(id).hasAttribute('href'), false);
  }
});

test('script command and project location are copy-only with visible feedback', async () => {
  const p = await boot();
  fire(p.tile('sap-search'), 'click');
  await flush();
  assert.deepEqual(p.state.copied, ['sap-search --all']);
  assert.match(p.tile('sap-search').textContent, /Copiado ✓/);
  assert.match(p.$('live').textContent, /Copiado/);
  p.clock.tick(2000);
  assert.doesNotMatch(p.tile('sap-search').textContent, /Copiado/);
  assert.match(p.tile('sap-search').textContent, /Copiar comando/);

  fire(p.tile('quotas-rs'), 'click');
  await flush();
  assert.deepEqual(p.state.copied, ['sap-search --all', '~/Code/quotas']);
  assert.match(p.tile('quotas-rs').textContent, /Copiado ✓/);
  assert.deepEqual(p.state.calls.map(c => c.url), ['/api/tiles'], 'copying never calls the server');
  assert.ok(p.state.calls.every(c => !c.opts || !c.opts.method || c.opts.method === 'GET'));
});

test('keyboard activation copies with Enter and Space, other keys do nothing', async () => {
  const p = await boot();
  const tile = p.tile('teams-pull');
  assert.equal(tile.getAttribute('role'), 'button');
  assert.equal(tile.getAttribute('tabindex'), '0');
  const space = fire(tile, 'keydown', { key: ' ' });
  assert.equal(space.defaultPrevented, true);
  fire(tile, 'keydown', { key: 'Enter' });
  fire(tile, 'keydown', { key: 'a' });
  await flush();
  assert.deepEqual(p.state.copied, ['teams-pull', 'teams-pull']);
});

test('cards without something to copy expose no phantom action', async () => {
  const p = await boot();
  for (const id of ['docs-only', 'nowhere']) {
    const tile = p.tile(id);
    assert.equal(tile.hasAttribute('data-copy'), false, id);
    assert.equal(tile.hasAttribute('role'), false, id);
    assert.equal(tile.hasAttribute('tabindex'), false, id);
    assert.doesNotMatch(tile.textContent, /[Cc]opiar|click/, id);
    fire(tile, 'click');
    fire(tile, 'keydown', { key: 'Enter' });
  }
  await flush();
  assert.deepEqual(p.state.copied, []);
});

test('copy failure is reported, and non-secure origins fall back to execCommand', async () => {
  const p = await boot();
  p.state.clipboardFail = true;
  fire(p.tile('sap-search'), 'click');
  await flush();
  assert.match(p.tile('sap-search').textContent, /No se pudo copiar/);
  assert.match(p.$('live').textContent, /No se pudo copiar/);

  const lan = await boot({ secure: false });
  fire(lan.tile('sap-search'), 'click');
  await flush();
  assert.deepEqual(lan.doc.execCopied, ['sap-search --all']);
  assert.deepEqual(lan.state.copied, []);
  lan.doc.execOk = false;
  fire(lan.tile('teams-pull'), 'click');
  await flush();
  assert.match(lan.tile('teams-pull').textContent, /No se pudo copiar/);
});

test('full titles are rendered escaped and untruncated', async () => {
  const p = await boot();
  assert.equal(p.names('grid-project')[1], TILES[6].name);
  assert.equal(p.tile('sis').textContent.includes('con un nombre largo que debe verse completo'), true);
});

test('five-second poll keeps query, filters, focus and copy feedback', async () => {
  const p = await boot();
  p.type('script'); p.topic('sap'); p.search('Sap');
  const input = p.$('search');
  input.focus();
  assert.deepEqual(p.allNames(), ['SAP search']);
  const gridWrites = p.$('grid-script').writes;

  await p.poll(); // identical payload: nothing is rewritten, nothing resets
  assert.equal(p.$('grid-script').writes, gridWrites, 'unchanged data does not touch the DOM');
  assert.equal(p.$('search').value, 'Sap');
  assert.equal(p.doc.activeElement, input);
  assert.deepEqual(p.allNames(), ['SAP search']);
  assert.equal(vm.runInContext('TYPE + "|" + TOPIC + "|" + QUERY', p.ctx), 'script|sap|sap');

  // focused chip survives a changed payload
  p.chip('topic-chips', 'sap').focus();
  p.state.tiles = [...TILES, { id: 'late', kind: 'web', name: 'Late', url: 'http://late:1', host: 'late:1', tags: ['sap'] }];
  await p.poll();
  assert.equal(p.doc.activeElement.getAttribute('data-key'), 'topic:sap');
  assert.equal(p.chip('topic-chips', 'sap').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(p.chips('type-chips').map(c => c.count), [3, 1, 1, 0, 1]);

  // focused tile + copied feedback survive a changed payload
  const tile = p.tile('sap-search');
  tile.focus();
  fire(tile, 'click');
  await flush();
  p.state.tiles = p.state.tiles.map(t => (t.id === 'sap-search' ? { ...t, description: 'Cambiado' } : t));
  p.clock.tick(1000); // still inside the copy-feedback window
  await p.reload();
  const again = p.tile('sap-search');
  assert.notEqual(again, tile, 'the grid was re-rendered');
  assert.equal(p.doc.activeElement, again, 'focus follows the re-rendered tile');
  assert.match(again.textContent, /Cambiado/);
  assert.match(again.textContent, /Copiado ✓/);
  assert.equal(p.$('search').value, 'Sap');
});

test('a topic that disappears from the data stays clearable', async () => {
  const p = await boot();
  p.topic('voz');
  p.state.tiles = TILES.filter(t => t.id !== 'voice');
  await p.poll();
  const voz = p.chips('topic-chips').find(c => c.value === 'voz');
  assert.deepEqual([voz.pressed, voz.count], ['true', 0]);
  assert.equal(p.$('noresults').hidden, false);
  p.topic('voz');
  assert.equal(p.allNames().length, 7);
});
