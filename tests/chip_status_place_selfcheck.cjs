/* w8 chipplace -- F5 on Chip Status lands on the panel the reader was on.
 *
 * Measured in real Chrome on big30x before the fix: F5 right after an Overview
 * tile jump came back on the exact panel only 8 times in 10; the other two
 * landed at the top of the tab's section (for the IRB tile that is the 2Q Fid.
 * section top, with the IRB heading 80-107k px further down). The F-20 scroll
 * record that a reload restores was written only by a 250 ms debounced scroll
 * handler, and on a big chip still loading after the jump every scroll event of
 * its growth re-armed that timer: 11 events in 10 s, zero records. The record
 * also named the tab's SECTION plus a pixel offset, which is right only once
 * every panel above the reader has its final height.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom, a small
 * layout model -- sections, RB headings and panels stacked in page order, the
 * pane scrolling over them, scroll-margin-top 64 px -- and VIRTUAL time,
 * tests/jsdom_vclock.cjs: nothing waits on the wall clock):
 *  J1  a tile jump writes the record in the SAME call that writes its URL:
 *      the panel it goes to, marked as a jump (F5 at 0 s has it)
 *  J2  so does a press on the in-page tab bar (the tab's section)
 *  J3  while the jump is still landing (smooth scroll, the guard re-landing it
 *      on growth) a scroll record keeps the jump's target, not the pane's
 *      mid-flight position
 *  J4  the reader moved on (a wheel) and the debounce is starved by scroll
 *      events: F5 (pagehide) writes the place anyway -- the PANEL at the pane
 *      top and how far into it
 *  J5  ...and so does the tab being hidden (visibilitychange)
 *  R1  F5 on a jump's record lands the jump's target again, under the sticky
 *      bar, and follows the page's growth
 *  R2  F5 on a place inside a panel lands on that panel + offset, and a panel
 *      ABOVE it growing does not move the reader (a section-relative pixel
 *      offset would have)
 *  R3  a place whose panel is gone falls back to its section + the section's
 *      own offset, also when the page grows
 *  R4  a record naming something that is not a panel is not used as a selector
 *  T1  the pagehide / visibilitychange listeners leave with the page
 *  M   the matrix the user asked for: mouse / Enter / Space on a tile and a
 *      tab-bar press, F5 after 0 s, 0.3 s, 2 s and 12 s, on a page still
 *      growing (big chip) and on one that has settled (5Q) -- every reload
 *      lands the target exactly under the sticky bar
 *
 * Run: node tests/chip_status_place_selfcheck.cjs
 *      (driven by tests/test_chip_status.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}
const { installClock } = require('./jsdom_vclock.cjs');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const APP_JS = read('app.js'), TOPO_JS = read('topo-graph.js'), CS_JS = read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const VIEWS = ['overview', 'health', 'topology', 'trends', 'fidelity2q', 'fidelity1q',
               'readout', 'coherence', 'frequencies', 'calibration'];
// the _wiring.html section skeleton, in page order
const PAGE = '<!DOCTYPE html><html><body>'
  + '<ul id="chip-status-subnav">' + VIEWS.map((v) => '<li><a data-view="' + v + '">' + v + '</a></li>').join('') + '</ul>'
  + '<div id="table-pane"><div class="topo-dashboard">'
  + '<div class="topo-section" data-topo-section="overview"><div id="topo-overview-tiles"></div></div>'
  + '<div class="topo-section" data-topo-section="health"><div id="topo-health-tiles"></div></div>'
  + '<div class="topo-subnav">' + VIEWS.map((v) => '<button class="topo-subnav-btn" data-view="' + v + '"></button>').join('') + '</div>'
  + '<div class="topo-section" id="sec-topology"><div id="topo-hero"></div><div id="topo-html-wrap"></div></div>'
  + '<div class="topo-section" data-topo-section="trends"><div id="topo-trends"></div></div>'
  + '<div class="topo-section" data-topo-section="fidelity" id="sec-fidelity"><div id="topo-2q-rb-panels" data-topo-section="2qrb"></div></div>'
  + '<div class="topo-section" data-topo-section="fid1q" id="sec-fidelity-1q"><div id="topo-fidelity-1q-panels"></div></div>'
  + '<div class="topo-section" data-topo-section="fidro" id="sec-readout"><div id="topo-fidelity-ro-panels"></div></div>'
  + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
  + '</div></div></body></html>';

function node(id, loc) {
  return { id: id, grid_location: loc, T1: 12e-6, T2ramsey: 15e-6, f_01: 5.1e9,
           readout_frequency: 7.2e9, x180_amplitude: 0.12, x90_amplitude: 0.06,
           gate_fidelity_avg: 0.998, assignment_fidelity: 0.95 };
}
function edge(a, b) {
  return { pair_id: a + '-' + b, source: a, target: b, has_cz: true, gate_kind: 'cz',
           gate_fidelities: [{ metric: 'StandardRB', gate: 'cz_SNZ', level: 'gate', value: 0.93 },
                             { metric: 'InterleavedRB', gate: 'cz_SNZ', level: 'gate', value: 0.95 }] };
}
const CHAIN = { nodes: ['0,0', '1,0', '2,0', '3,0', '4,0'].map((l, i) => node('q' + (i + 1), l)),
                edges: [edge('q1', 'q2'), edge('q2', 'q3'), edge('q3', 'q4'), edge('q4', 'q5')] };

const T1SEL = '.topo-section[data-density-panel="T1"]';
const T2SEL = '.topo-section[data-density-panel="T2ramsey"]';
const IRBSEL = '[data-rb-heading="InterleavedRB"]';
const ROSEL = '.topo-section[data-density-panel="assignment_fidelity"]';
const SEC = { coherence: '#topo-metric-panels [data-group="coherence"]',
              frequencies: '#topo-metric-panels [data-group="frequency"]',
              fidelity2q: '[data-topo-section="fidelity"]' };

/* The layout model. Blocks stack in page order; a container (a fidelity
   section, the RB host) spans its children. The pane sits 100 px down the
   viewport, 700 px tall; a jump lands its target 64 px under the pane top
   (scroll-margin-top: 3.2rem, what the sticky tab bar needs). */
const PANE_TOP = 100, PANE_H = 700, SM = 64;
const BLOCKS = '[data-topo-section="overview"], [data-topo-section="health"], #sec-topology, '
  + '[data-topo-section="trends"], #sec-fidelity, [data-rb-heading], .topo-section[data-density-panel], '
  + '#sec-fidelity-1q, #sec-readout, #topo-metric-panels > h3[data-group]';

function world(opts) {
  opts = opts || {};
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: 'http://localhost' + (opts.url || '/topology?view=overview') });
  const win = dom.window, doc = win.document;
  const clock = installClock(win);
  const T = { win: win, doc: doc, clock: clock, scrolled: [], listeners: { pagehide: 0, visibilitychange: 0 } };
  // count the page-lifecycle listeners (T1)
  [[win, 'pagehide'], [doc, 'visibilitychange']].forEach(function (p) {
    const tgt = p[0], type = p[1];
    const add = tgt.addEventListener.bind(tgt), rem = tgt.removeEventListener.bind(tgt);
    tgt.addEventListener = function (t) { if (t === type) T.listeners[type]++; return add.apply(null, arguments); };
    tgt.removeEventListener = function (t) { if (t === type) T.listeners[type]--; return rem.apply(null, arguments); };
  });
  win.htmx = { ajax: function () { return new win.Promise(function () {}); }, process: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  win.Plotly = {};

  const pane = doc.getElementById('table-pane'), dash = doc.querySelector('.topo-dashboard');
  const G = { h: Object.assign({ overview: 900, health: 300, topology: 800, trends: 50,
                                 header: 40, rbHeading: 30, panel: 600 }, opts.h || {}), panel: {} };
  function ownH(el) {
    const s = el.getAttribute('data-topo-section');
    if (s === 'overview') return G.h.overview;
    if (s === 'health') return G.h.health;
    if (el.id === 'sec-topology') return G.h.topology;
    if (s === 'trends') return G.h.trends;
    if (el.id === 'sec-fidelity' || el.id === 'sec-fidelity-1q' || el.id === 'sec-readout') return G.h.header;
    if (el.hasAttribute('data-rb-heading')) return G.h.rbHeading;
    if (el.tagName === 'H3') return G.h.header;
    const k = el.getAttribute('data-density-panel');
    if (k !== null) return G.panel[k] != null ? G.panel[k] : G.h.panel;
    return 0;
  }
  /* cached: jsdom's selector engine is the whole cost of this harness
     otherwise. Dropped the moment the DOM changes (every mutating DOM call
     the page can make is wrapped -- a browser lays out again on the next
     read after a change, in the same task), on a mutation record (attribute
     changes), and when the model's heights change (relayout). */
  let cache = null, blocks = null;          // heights / the block list (DOM-only)
  const drop = function () { cache = null; blocks = null; };
  [[win.Node.prototype, ['appendChild', 'insertBefore', 'removeChild', 'replaceChild']],
   [win.Element.prototype, ['insertAdjacentHTML', 'insertAdjacentElement', 'replaceChildren', 'append',
                            'prepend', 'remove', 'replaceWith', 'before', 'after', 'setAttribute', 'removeAttribute']]]
    .forEach(function (pr) {
      pr[1].forEach(function (name) {
        const f = pr[0][name];
        if (typeof f !== 'function') return;
        pr[0][name] = function () { drop(); return f.apply(this, arguments); };
      });
    });
  [[win.Element.prototype, 'innerHTML'], [win.Element.prototype, 'outerHTML'], [win.Node.prototype, 'textContent']]
    .forEach(function (pr) {
      let proto = pr[0], d = null;
      while (proto && !(d = Object.getOwnPropertyDescriptor(proto, pr[1]))) proto = Object.getPrototypeOf(proto);
      if (!d || !d.set) return;
      Object.defineProperty(proto, pr[1], { configurable: true, enumerable: d.enumerable, get: d.get,
        set: function (v) { drop(); return d.set.call(this, v); } });
    });
  function layout() {
    if (!cache) cache = layoutNow();
    return cache;
  }
  function layoutNow() {
    if (!blocks) blocks = Array.prototype.slice.call(doc.querySelectorAll(BLOCKS));
    const m = new Map(), list = blocks;
    let y = 0;
    list.forEach(function (el) { const h = ownH(el); m.set(el, { y: y, h: h }); y += h; });
    list.forEach(function (el) {
      const b = m.get(el);
      list.forEach(function (c) {
        if (c !== el && el.contains(c)) { const cb = m.get(c); b.h = Math.max(b.h, cb.y + cb.h - b.y); }
      });
    });
    return { m: m, total: y + 400 };
  }
  T.layout = function () { drop(); return layout(); };
  const box = { st: 0 };
  let anim = null, scrollPending = false;
  function setST(v, fromAnim) {
    if (!fromAnim) anim = null;               // an instant scroll ends a smooth one
    const max = Math.max(0, layout().total - PANE_H);
    const nv = Math.max(0, Math.min(max, Math.round(v)));
    if (nv === box.st) return;
    box.st = nv;
    if (!scrollPending) {                     // the browser's scroll event, a task later
      scrollPending = true;
      win.setTimeout(function () { scrollPending = false; pane.dispatchEvent(new win.Event('scroll')); }, 0);
    }
  }
  Object.defineProperty(pane, 'scrollTop', { configurable: true, get: () => box.st, set: (v) => setST(v, false) });
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: () => layout().total });
  Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => PANE_H });
  pane.scrollTo = function (o) { setST(o && o.top || 0, false); };
  win.Element.prototype.getBoundingClientRect = function () {
    if (this === pane) return { top: PANE_TOP, bottom: PANE_TOP + PANE_H, left: 0, right: 1000, width: 1000, height: PANE_H };
    const b = layout().m.get(this);
    if (!b) return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
    const top = PANE_TOP + b.y - box.st;
    return { top: top, bottom: top + b.h, left: 0, right: 1000, width: 1000, height: b.h };
  };
  /* hit-testing (Chrome's elementFromPoint): the innermost block whose band
     holds the point's content y; T.overlay puts something else on top (a
     hover card), which is not a panel */
  T.overlay = null;
  doc.elementFromPoint = function (x, y) {
    if (T.overlay) return T.overlay;
    const cy = y - PANE_TOP + box.st, L = layout();
    let hit = null, hitH = Infinity;
    L.m.forEach(function (b, el) { if (cy >= b.y && cy < b.y + b.h && b.h > 0 && b.h <= hitH) { hit = el; hitH = b.h; } });
    return hit || doc.body;
  };
  win.Element.prototype.scrollIntoView = function (o) {
    const b = layout().m.get(this);
    T.scrolled.push({ el: this, behavior: o && o.behavior });
    if (!b) return;
    const target = b.y - SM;
    if (o && o.behavior === 'smooth') {       // a fixed target, 8 steps over 400 ms
      const from = box.st, id = {};
      anim = id;
      for (let i = 1; i <= 8; i++) {
        win.setTimeout(function () { if (anim === id) setST(from + (target - from) * i / 8, true); }, 50 * i);
      }
    } else {
      setST(target, false);
    }
  };
  // the dashboard's height is the layout's; its ResizeObserver fires a frame
  // after the layout changed (what _setupJumpFollow listens to)
  const ros = [];
  win.ResizeObserver = function (cb) {
    const o = { cb: cb, els: [] }; ros.push(o);
    this.observe = function (el) { o.els.push(el); };
    this.unobserve = function () {};
    this.disconnect = function () { o.els = []; };
  };
  Object.defineProperty(dash, 'offsetHeight', { configurable: true, get: () => layout().total });
  let lastTotal = -1;
  new win.MutationObserver(drop).observe(dash, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['id', 'data-density-panel', 'data-rb-heading', 'data-group', 'data-topo-section'] });
  T.relayout = function () {
    cache = null;
    const tot = layout().total;
    if (tot === lastTotal) return;
    lastTotal = tot;
    win.setTimeout(function () {
      ros.forEach(function (o) { if (o.els.indexOf(dash) >= 0) o.cb([{ target: dash }]); });
    }, 16);
  };
  T.G = G;
  T.pane = pane;
  T.box = box;

  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  // a chart takes 300 ms to draw (a real one takes its height only then);
  // set after app.js, which defines its own
  win._plotlyRender = function () { return new win.Promise(function (r) { win.setTimeout(r, 300); }); };
  // the entry's state as a reload finds it, BEFORE the mount
  if (opts.state) win.history.replaceState(opts.state, '');
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(CHAIN)), rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {}, chipView: opts.chipView || '' });
  lastTotal = layout().total;
  T.rec = function () { return win.history.state && win.history.state.smChipScroll; };
  T.url = function () { return win.location.pathname + win.location.search; };
  T.topOf = function (sel) {
    const el = doc.querySelector(sel);
    return el ? el.getBoundingClientRect().top - PANE_TOP : null;
  };
  T.lit = function () {
    const b = doc.querySelector('.topo-subnav-btn.active');
    return b && b.getAttribute('data-view');
  };
  T.advance = function (ms) { return clock.advance(ms); };
  T.tile = function (id) { return doc.querySelector('#topo-overview-tiles .topo-card[data-tile-id="' + id + '"]'); };
  /* a big chip loading: Trends lands at +3 s, then panels keep taking their
     height (charts drawn) every 250 ms until `until` ms after the mount;
     every step is a layout change the jump guard sees */
  T.growUntil = function (until) {
    win.setTimeout(function () { G.h.trends = 1400; T.relayout(); }, 3000);
    let n = 0;
    for (let t = 250; t <= until; t += 250) {
      win.setTimeout(function () {
        const keys = Array.prototype.map.call(doc.querySelectorAll('.topo-section[data-density-panel]'),
                                              function (e) { return e.getAttribute('data-density-panel'); });
        if (!keys.length) return;
        const k = keys[(n++) % keys.length];
        G.panel[k] = (G.panel[k] != null ? G.panel[k] : G.h.panel) + 37;
        T.relayout();
      }, t);
    }
  };
  T.errors = function () { return clock.errors(); };
  return T;
}
function clone(o) { return JSON.parse(JSON.stringify(o)); }
function press(T, how, what) {
  const win = T.win;
  if (how === 'tab') {
    const btn = T.doc.querySelector('.topo-subnav-btn[data-view="' + what + '"]');
    btn.dispatchEvent(new win.Event('pointerdown', { bubbles: true }));
    win.setChipStatusView(what, btn, true);       // its onclick
    return;
  }
  const tile = T.tile(what);
  if (how === 'mouse') {
    tile.dispatchEvent(new win.Event('pointerdown', { bubbles: true }));
    tile.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  } else {
    tile.focus();
    tile.dispatchEvent(new win.KeyboardEvent('keydown', { key: how === 'Enter' ? 'Enter' : ' ', bubbles: true, cancelable: true }));
  }
}

(async function main() {
  // ── J1: a tile jump writes its record in the same call as its URL ────────
  {
    const T = world({ state: { htmx: true } });
    await T.advance(1000);
    press(T, 'mouse', 't1');
    const r = T.rec();                          // no time has passed
    ok(T.url() === '/topology?view=coherence' && r && r.url === '/topology?view=coherence'
       && r.view === 'coherence' && r.sel === T1SEL && r.jump === true && typeof r.top === 'number',
       'J1 a tile jump writes the record in the same call as its URL: the T1 PANEL, as a jump — ' + JSON.stringify(r));
    ok(T.win.history.state.htmx === true, 'J1 ...merged, htmx\'s {htmx:true} marker kept');

    // ── J3: while the jump is landing, the record stays the jump's target ──
    await T.advance(100);                       // the smooth scroll is part way
    const mid = T.topOf(T1SEL), midSt = T.box.st;
    T.win.history.replaceState({ htmx: true }, '');     // wipe it: what follows must be pagehide's own write
    T.win.dispatchEvent(new T.win.Event('pagehide'));   // F5 mid-flight: the record is written NOW
    const r3 = T.rec();
    ok(mid > SM + 100 && midSt > 0 && r3 && r3.jump === true && r3.sel === T1SEL && r3.view === 'coherence',
       'J3 a record written while the jump is still in flight (T1 at ' + mid + ' px) is the jump\'s target, not the pane\'s passing place — ' + JSON.stringify(r3));
    await T.advance(3000);
    ok(T.topOf(T1SEL) === SM, 'J3 (the jump landed the T1 panel under the sticky bar: ' + T.topOf(T1SEL) + ')');
  }
  // ── J2: a tab-bar press writes its record at once too ─────────────────────
  {
    const T = world({ state: { htmx: true } });
    await T.advance(1000);
    press(T, 'tab', 'readout');
    const r = T.rec();
    ok(T.url() === '/topology?view=readout' && r && r.view === 'readout' && r.jump === true && !r.sel,
       'J2 a tab-bar press writes the record in the same call as its URL: the tab\'s section — ' + JSON.stringify(r));
  }
  // ── J4 / J5: the reader moved on; a starved debounce; F5 / hidden writes ──
  for (const how of ['pagehide', 'hidden', 'overlay']) {
    const T = world({ state: { htmx: true } });
    await T.advance(1000);
    press(T, 'mouse', 't1');
    await T.advance(9500);                      // landed, the guard's window over
    const recJump = clone(T.rec());
    T.pane.dispatchEvent(new T.win.Event('wheel', { bubbles: true }));   // the reader takes over
    const t2y = T.layout().m.get(T.doc.querySelector(T2SEL)).y;
    // scroll events every 100 ms for 3 s: the 250 ms debounce never runs
    for (let i = 1; i <= 30; i++) {
      T.pane.scrollTop = t2y + 10 * i;
      T.pane.dispatchEvent(new T.win.Event('scroll'));
      await T.advance(100);
    }
    T.pane.scrollTop = t2y + 300;
    T.pane.dispatchEvent(new T.win.Event('scroll'));
    await T.advance(100);
    const starved = clone(T.rec());
    const tag = how === 'pagehide' ? 'J4' : (how === 'hidden' ? 'J5' : 'J4b');
    if (how === 'overlay') T.overlay = T.doc.body;       // a card on the 130 px line: no panel hit
    const hits = [];
    const efp = T.doc.elementFromPoint;
    T.doc.elementFromPoint = function (x, y) { const e = efp.call(this, x, y); hits.push(e); return e; };
    if (how !== 'hidden') {
      T.win.dispatchEvent(new T.win.Event('pagehide'));
    } else {
      Object.defineProperty(T.doc, 'visibilityState', { configurable: true, get: () => 'hidden' });
      T.doc.dispatchEvent(new T.win.Event('visibilitychange'));
    }
    const r = T.rec();
    ok(JSON.stringify(starved) === JSON.stringify(recJump),
       tag + ' setup: the debounced record was starved (still the jump\'s)');
    ok(r && !r.jump && r.view === 'coherence' && r.sel === T2SEL && r.ds === 300 && r.url === '/topology?view=coherence',
       ({ J4: 'J4 F5 (pagehide)', J5: 'J5 hiding the tab (visibilitychange)',
          J4b: 'J4b F5 with a card over the line (the hit-test misses, the panel scan finds it)' })[tag]
       + ' writes the place at once: the T2 Ramsey PANEL, 300 px into it — ' + JSON.stringify(r));
    ok(hits.length === 1 && (how === 'overlay' ? hits[0] === T.doc.body : hits[0] === T.doc.querySelector(T2SEL)),
       tag + ' ...by ONE hit-test at the 130 px line, which found ' + (how === 'overlay' ? 'the overlay' : 'the T2 Ramsey panel itself')
       + ' — ' + hits.length + ' ' + (hits[0] && (hits[0].id || hits[0].tagName)));
  }
  // ── R1: F5 on a jump's record lands the jump's target again ───────────────
  {
    const rec = { url: '/topology?view=fidelity2q', view: 'fidelity2q', sel: IRBSEL, jump: true, top: 3 };
    const T = world({ url: '/topology?view=fidelity2q', chipView: 'fidelity2q', state: { htmx: true, smChipScroll: rec } });
    T.growUntil(14000);
    await T.advance(100);
    ok(T.topOf(IRBSEL) === SM && T.lit() === 'fidelity2q'
       && !T.scrolled.some((s) => s.behavior === 'smooth'),
       'R1 F5 on a jump\'s record lands the IRB heading under the sticky bar at once, 2Q Fid. lit — '
       + T.topOf(IRBSEL) + ' ' + T.lit());
    await T.advance(20000);
    ok(T.topOf(IRBSEL) === SM, 'R1 ...and it is still there after Trends and 14 s of panels growing above it — ' + T.topOf(IRBSEL));
  }
  // ── R2: a place inside a panel: panel + offset, whatever grows above ──────
  {
    const T0 = world({ url: '/topology?view=coherence', chipView: 'coherence' });
    const secY = T0.layout().m.get(T0.doc.querySelector(SEC.coherence)).y;
    const t2y0 = T0.layout().m.get(T0.doc.querySelector(T2SEL)).y;
    const rec = { url: '/topology?view=coherence', view: 'coherence', d: t2y0 - secY + 300,
                  sel: T2SEL, ds: 300, top: 1 };
    const T = world({ url: '/topology?view=coherence', chipView: 'coherence', state: { htmx: true, smChipScroll: rec } });
    await T.advance(100);
    ok(T.topOf(T2SEL) === -300, 'R2 F5 on a place 300 px into the T2 Ramsey panel lands there — ' + T.topOf(T2SEL));
    T.G.panel.T1 = 600 + 480;                   // the T1 chart above it takes its height
    T.relayout();
    await T.advance(100);
    ok(T.topOf(T2SEL) === -300,
       'R2 ...and when the T1 panel ABOVE it grows 480 px the reader stays 300 px into T2 Ramsey (a section offset would be 480 px off) — '
       + T.topOf(T2SEL));
  }
  // ── R3: the panel is gone: the section + its own offset ───────────────────
  {
    const rec = { url: '/topology?view=coherence', view: 'coherence', d: 250,
                  sel: '.topo-section[data-density-panel="T9"]', ds: 40, top: 1 };
    const T = world({ url: '/topology?view=coherence', chipView: 'coherence', state: { htmx: true, smChipScroll: rec } });
    await T.advance(100);
    ok(T.topOf(SEC.coherence) === -250, 'R3 a place whose panel the page does not have lands on its section + d — ' + T.topOf(SEC.coherence));
    T.G.h.trends = 1400;                        // Trends lands above
    T.relayout();
    await T.advance(100);
    ok(T.topOf(SEC.coherence) === -250,
       'R3 ...and the re-landing on growth keeps the SECTION\'s offset, not the missing panel\'s — ' + T.topOf(SEC.coherence));
  }
  // ── R4: not an anchor shape: never used as a selector ─────────────────────
  {
    const rec = { url: '/topology?view=coherence', view: 'coherence', d: 120, sel: '#table-pane', ds: 0, top: 1 };
    const T = world({ url: '/topology?view=coherence', chipView: 'coherence', state: { htmx: true, smChipScroll: rec } });
    await T.advance(100);
    ok(T.topOf(SEC.coherence) === -120 && T.errors().length === 0,
       'R4 a record whose sel is not a panel / RB heading falls back to the section + d — ' + T.topOf(SEC.coherence));
  }
  // ── T1: the lifecycle listeners leave with the page ───────────────────────
  {
    const T = world({ state: { htmx: true } });
    await T.advance(100);
    const up = Object.assign({}, T.listeners);
    T.doc.body.dispatchEvent(new T.win.CustomEvent('htmx:beforeSwap', { bubbles: true,
      detail: { target: T.pane, shouldSwap: true, requestConfig: {} } }));
    await T.advance(10);
    // the dashboard is still in this DOM (the harness swaps nothing), so a
    // listener left behind WOULD write: nothing may
    T.win.history.replaceState({ htmx: true, marker: 1 }, '');
    T.win.dispatchEvent(new T.win.Event('pagehide'));
    Object.defineProperty(T.doc, 'visibilityState', { configurable: true, get: () => 'hidden' });
    T.doc.dispatchEvent(new T.win.Event('visibilitychange'));
    ok(up.pagehide >= 1 && T.listeners.pagehide < up.pagehide && T.listeners.visibilitychange < up.visibilitychange
       && !T.win.history.state.smChipScroll,
       'T1 navigating away removes the record\'s pagehide + visibilitychange listeners (no write after it) — '
       + JSON.stringify(up) + ' -> ' + JSON.stringify(T.listeners) + ' ' + JSON.stringify(T.win.history.state));
  }

  // ── M: the F5 matrix ──────────────────────────────────────────────────────
  const CASES = [['mouse', 't1', 'coherence', T1SEL], ['Enter', 'irb', 'fidelity2q', IRBSEL],
                 ['Space', 'ro_ge', 'readout', ROSEL], ['tab', 'frequencies', 'frequencies', SEC.frequencies]];
  for (const prof of [['big chip, still loading', 21000], ['5Q, settled', 7500]]) {
    for (const c of CASES) {
      for (const delay of [0, 300, 2000, 12000]) {
        const A = world({ state: { htmx: true } });
        A.growUntil(prof[1]);
        await A.advance(7000);                  // the page open 7 s
        press(A, c[0], c[1]);
        await A.advance(delay);
        A.win.dispatchEvent(new A.win.Event('pagehide'));        // F5
        const st = clone(A.win.history.state), url = A.url();
        const B = world({ url: url, chipView: (url.match(/view=([^&]*)/) || [])[1] || '', state: st });
        B.growUntil(prof[1]);
        await B.advance(20000);
        const top = B.topOf(c[3]);
        const errs = A.errors().concat(B.errors());
        ok(url === '/topology?view=' + c[2] && top === SM && B.lit() === c[2] && errs.length === 0,
           'M ' + prof[0] + ': ' + c[0] + ' -> ' + c[1] + ', F5 after ' + delay + ' ms lands on it — top '
           + top + ' (want ' + SM + '), tab ' + B.lit() + ', rec ' + JSON.stringify(st.smChipScroll)
           + (errs.length ? ' ERR ' + errs[0].slice(0, 200) : ''));
      }
    }
  }

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_place_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
