// QA round 2 (package cs-ui) -- a Chip Status jump lands where it points, and
// the lit tab is the one the user pressed. Drives the REAL chip-status.js
// (mounted, not a copy of its logic) under jsdom:
//
//   F-02  a 1Q / readout / metrics jump builds the lazy 2Q RB host FIRST (it
//         sits above the target and grew ~2.5k px mid-scroll), and re-anchors
//         once the charts are DRAWN (a chart takes its height only then)
//   F-02  the chart pump waits for Plotly instead of queueing every chart
//         into one burst that outlived the jump guard's window
//   F-02  a scroll made while the spy is suppressed is looked at again once
//         the suppression ends (the tab passed on the way stayed lit)
//   F-06  a jump TO Trends is re-anchored when its (late) content lands
//   F-07  while a jump is live the CLICKED item stays lit; at the bottom of
//         the pane the last section on screen wins
//   F-03  the 2Q pair grid drops tracks no pair uses (a chain used half)
//   F-17  Escape closes the Panels / tile popovers and the History drawer
//   F-19  an in-page tab press keeps the URL in step (F5 lands there)
//   F-20  Back returns to where the user had scrolled, not the section anchor
//
// Run: node tests/chip_jump_selfcheck.cjs   (needs jsdom)
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const APP_JS = read('app.js'), TOPO_JS = read('topo-graph.js'), CS_JS = read('chip-status.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const VIEWS = ['overview', 'health', 'topology', 'trends', 'fidelity2q', 'fidelity1q',
               'readout', 'coherence', 'frequencies', 'calibration'];
// production TAB_SPEC selectors (pinned in test_chip_status_layout.py)
const SEL = {
  overview: '[data-topo-section="overview"]', health: '[data-topo-section="health"]',
  topology: '#sec-topology', trends: '[data-topo-section="trends"]',
  fidelity2q: '[data-topo-section="fidelity"]', fidelity1q: '#sec-fidelity-1q', readout: '#sec-readout',
  coherence: '#topo-metric-panels [data-group="coherence"]',
  frequencies: '#topo-metric-panels [data-group="frequency"]',
  calibration: '#topo-metric-panels [data-group="calibration"]',
};

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
           gate_fidelities: [{ metric: 'StandardRB', gate: 'cz_SNZ', level: 'gate', value: 0.93 }] };
}
// the KRISS shape: a 5-qubit chain
const CHAIN = { nodes: ['0,0', '1,0', '2,0', '3,0', '4,0'].map((l, i) => node('q' + (i + 1), l)),
                edges: [edge('q1', 'q2'), edge('q2', 'q3'), edge('q3', 'q4'), edge('q4', 'q5')] };

/* opts.plotly: 'loaded' (window.Plotly present), 'pending' (requirePlotly
   deferred); renders are recorded and resolved by the test */
function world(topo, opts) {
  opts = opts || {};
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: 'http://localhost' + (opts.url || '/') });
  const win = dom.window;
  win.htmx = { ajax: function () {}, process: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  // no IntersectionObserver fallback "build everything now": the lazy
  // sections stay unbuilt until something asks for them, as in a browser
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  const T = { win: win, doc: win.document, renders: [], scrolled: [], plotlyGate: null };
  win.Element.prototype.scrollIntoView = function (o) {
    T.scrolled.push({ id: this.id || this.getAttribute('data-topo-section') || this.getAttribute('data-group'),
                      behavior: o && o.behavior });
  };
  win._plotlyRender = function (el) {
    let res; const p = new win.Promise(function (r) { res = r; });
    T.renders.push({ id: el && el.id, resolve: res });
    return p;
  };
  if (opts.plotly === 'pending') {
    win.Plotly = undefined;
    win.requirePlotly = function () {
      if (!T.plotlyGate) { let r; const p = new win.Promise(function (x) { r = x; }); T.plotlyGate = { p: p, resolve: r }; }
      return T.plotlyGate.p;
    };
  } else {
    win.Plotly = {};
  }
  // QA F-20: the entry's state as a Back / reload finds it, BEFORE the mount
  if (opts.state) win.history.replaceState(opts.state, '');
  if (opts.beforeMount) opts.beforeMount(T);
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {}, diagFindings: [],
                         metricMeta: {}, chipView: opts.chipView || '' });
  T.lit = function () {
    const b = T.doc.querySelector('.topo-subnav-btn.active'), a = T.doc.querySelector('#chip-status-subnav a.active');
    return (b && b.getAttribute('data-view')) + '/' + (a && a.getAttribute('data-view'));
  };
  return T;
}

(async function main() {
  // ── F-02: a 1Q jump builds the 2Q RB host above it BEFORE measuring ──────
  {
    const T = world(CHAIN);
    const host = T.doc.getElementById('topo-2q-rb-panels');
    ok(host.innerHTML === '', 'F-02 setup: the 2Q RB host is lazy (unbuilt at mount)');
    T.win.setChipStatusView('fidelity1q', null, true);
    ok(!!host.querySelector('.topo-2q-pair-grid'),
       'F-02 a 1Q jump builds the 2Q RB host that sits above its target, synchronously');
    await sleep(60);
    const smooth = T.scrolled.filter((s) => s.behavior === 'smooth').map((s) => s.id);
    ok(smooth.join(',') === 'sec-fidelity-1q', 'F-02 then smooth-scrolls to the 1Q section (' + smooth + ')');

    // the charts are drawn later; only then do they have a height
    await sleep(250);                                   // let the pump queue every chart
    const n = T.renders.length;
    ok(n >= 4, 'F-02 setup: the 2Q and 1Q charts were handed to Plotly (' + n + ')');
    ok(!T.scrolled.some((s) => s.behavior === 'auto'), 'F-02 nothing re-anchors while the charts are not drawn yet');
    T.renders.forEach((r) => r.resolve(null));
    await sleep(80);
    const auto = T.scrolled.filter((s) => s.behavior === 'auto').map((s) => s.id);
    ok(auto.length >= 1 && auto.every((id) => id === 'sec-fidelity-1q'),
       'F-02 once every chart is drawn the jump is put back on the 1Q section (' + auto + ')');
  }

  // ── F-02: the pump waits for Plotly instead of queueing one huge burst ───
  {
    const T = world(CHAIN, { plotly: 'pending' });
    T.win.setChipStatusView('fidelity1q', null, true);
    await sleep(150);
    ok(!!T.plotlyGate, 'F-02 setup: the charts asked for Plotly');
    ok(T.renders.length === 0, 'F-02 no chart is queued while Plotly is still loading (' + T.renders.length + ')');
    T.plotlyGate.resolve({});
    await sleep(0);
    const first = T.renders.length;
    ok(first > 0 && first <= 6, 'F-02 once it loads the pump starts in batches (first frame: ' + first + ')');
    await sleep(300);
    ok(T.renders.length > first, 'F-02 ...and the rest follow frame by frame (' + T.renders.length + ')');
  }

  // ── F-06: a jump TO Trends is re-anchored when Trends lands ──────────────
  {
    const T = world(CHAIN);
    const J = T.win.ChipStatus.jumpGuard;
    const pane = T.doc.getElementById('table-pane');
    const selOf = (v) => SEL[v];
    J.note('trends', pane);
    const real = T.win.Date.now;
    T.win.Date.now = function () { return real() + 100; };
    T.scrolled.length = 0;
    ok(J.reanchor(selOf) === true && T.scrolled.map((s) => s.id).join(',') === 'trends',
       'F-06 a jump to Trends made 100 ms ago is put back on Trends when its content lands');
    T.win.Date.now = function () { return real() + 9000; };
    ok(J.reanchor(selOf) === false, 'F-06 ...inside the same window as every other jump');
    T.win.Date.now = real;
    J.note('overview', pane);
    ok(J.reanchor(selOf) === false, 'F-06 a jump above Trends is still left alone');
  }

  // ── the scroll-spy: deferred re-check (F-02), live jump + bottom (F-07) ──
  {
    const T = world(CHAIN);
    const win = T.win, doc = T.doc;
    const pane = doc.getElementById('table-pane');
    let st = 3000;
    Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => 700 });
    Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: () => 10000 });
    Object.defineProperty(pane, 'scrollTop', { configurable: true, get: () => st, set: (v) => { st = v; } });
    pane.getBoundingClientRect = () => ({ top: 0, bottom: 700, left: 0, right: 1000, width: 1000, height: 700 });
    function geom(tops) {
      VIEWS.forEach(function (v) {
        const el = doc.querySelector(SEL[v]);
        if (!el) return;
        const t = tops[v] == null ? -5000 : tops[v];
        el.getBoundingClientRect = () => ({ top: t, bottom: t + 300, left: 0, right: 1000, width: 1000, height: 300 });
      });
    }
    const real = win.Date.now;
    let skew = 0;
    win.Date.now = function () { return real() + skew; };
    const scroll = () => pane.dispatchEvent(new win.Event('scroll'));

    // F-02: the jump sets the tab and suppresses the spy; a scroll that
    // arrives during the suppression is evaluated once it is over
    win.setChipStatusView('fidelity2q', null, true);
    ok(T.lit() === 'fidelity2q/fidelity2q', 'spy setup: the pressed view is lit (' + T.lit() + ')');
    geom({ fidelity1q: 64, readout: 520 });             // the pane actually ended on 1Q
    scroll();
    ok(T.lit() === 'fidelity2q/fidelity2q', 'F-02 a scroll during the suppression does not flip the tab at once');
    await sleep(900);
    ok(T.lit() === 'fidelity1q/fidelity1q',
       'F-02 ...but is looked at once the suppression ends: the tab follows where the pane is (' + T.lit() + ')');

    // F-07: Calibration (the last group) stops ~150 px short of the top;
    // while that jump is live it stays lit, not the Frequencies above it
    await sleep(150);                                    // clear the spy throttle
    win.setChipStatusView('calibration', null, true);
    skew += 900;                                         // past the suppression, inside the jump window
    geom({ coherence: -1400, frequencies: -600, calibration: 150 });
    scroll();
    ok(T.lit() === 'calibration/calibration',
       'F-07 while a jump is live the CLICKED item stays lit when its target is on screen (' + T.lit() + ')');

    // no live jump: plain geometry, plus the bottom-of-pane rule
    win.ChipStatus.jumpGuard.cancel();
    skew += 200;
    scroll();
    ok(T.lit() === 'frequencies/frequencies', 'F-07 control: without a live jump, mid-pane, the spy is geometric (' + T.lit() + ')');
    st = 10000 - 700;
    skew += 200;
    scroll();
    ok(T.lit() === 'calibration/calibration',
       'F-07 at the bottom of the pane the last section on screen wins (' + T.lit() + ')');
    win.Date.now = real;
  }

  // ── the bottom rule never lights a lazy placeholder; a bare page has no jump
  {
    const T = world(CHAIN);                              // fresh, nothing lazy built
    const win = T.win, doc = T.doc;
    const pane = doc.getElementById('table-pane');
    Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => 700 });
    Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: () => 2500 });
    Object.defineProperty(pane, 'scrollTop', { configurable: true, get: () => 1800, set: () => {} });
    pane.getBoundingClientRect = () => ({ top: 0, bottom: 700, left: 0, right: 1000, width: 1000, height: 700 });
    const tops = { overview: -2000, health: -1500, topology: -300, trends: 455, fidelity2q: 532, fidelity1q: 609, readout: 686 };
    Object.keys(tops).forEach(function (v) {
      const el = doc.querySelector(SEL[v]);
      el.getBoundingClientRect = () => ({ top: tops[v], bottom: tops[v] + 77, left: 0, right: 1000, width: 1000, height: 77 });
    });
    pane.dispatchEvent(new win.Event('scroll'));
    ok(T.lit() === 'topology/topology',
       'F-07 a fresh page scrolled straight to its end does not light the unbuilt placeholder at the bottom (' + T.lit() + ')');

    const J = win.ChipStatus.jumpGuard;
    J.note('coherence', pane);
    ok(J.current() === 'coherence', 'setup: a noted jump is live');
    win.ChipStatus.mount({ topo: CHAIN, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {} });
    ok(J.current() === null, 'a bare Chip Status page (no ?view=) carries no live jump over from the previous visit');
  }

  // ── F-03: the 2Q pair grid uses only the tracks its pairs occupy ─────────
  {
    const T = world(CHAIN);
    T.win.setChipStatusView('fidelity2q', null, false);
    const g = T.doc.querySelector('.topo-2q-pair-grid');
    // jsdom's CSSOM drops grid-template-*: read the attribute the builder wrote
    const tpl = (el, prop) => { const m = el && (el.getAttribute('style') || '').match(new RegExp(prop + ':([^;]*)')); return m ? m[1].replace(/\s+/g, '') : ''; };
    const cols = tpl(g, 'grid-template-columns');
    ok(cols === 'repeat(4,var(--topo-panel-cell-size))',
       'F-03 a 5-qubit chain lays its 4 pairs on 4 tracks, not 8 (' + cols + ')');
    const at = Array.from(g.querySelectorAll('[data-pair]')).map((c) => c.getAttribute('data-pair') + '@' + c.style.gridColumn.split('/')[0].trim());
    ok(at.join(',') === 'q1-q2@1,q2-q3@2,q3-q4@3,q4-q5@4', 'F-03 ...in chain order, no blank track between them (' + at + ')');

    // a square lattice already uses every doubled track: unchanged
    const SQ = { nodes: [node('a', '0,0'), node('b', '1,0'), node('c', '0,1'), node('d', '1,1')],
                 edges: [edge('a', 'b'), edge('c', 'd'), edge('a', 'c'), edge('b', 'd')] };
    const S = world(SQ);
    S.win.setChipStatusView('fidelity2q', null, false);
    const gs = S.doc.querySelector('.topo-2q-pair-grid');
    ok(tpl(gs, 'grid-template-columns') === 'repeat(3,var(--topo-panel-cell-size))'
       && tpl(gs, 'grid-template-rows') === 'repeat(3,auto)',
       'F-03 a 2x2 lattice keeps its 3x3 doubled grid (' + tpl(gs, 'grid-template-columns') + ' x ' + tpl(gs, 'grid-template-rows') + ')');
  }

  // ── QA F-17: Escape closes the Overview popovers and the History drawer ──
  {
    const T = world(CHAIN);
    const win = T.win, doc = T.doc;
    const esc = (target, pre) => {
      const e = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      if (pre) e.preventDefault();
      (target || doc.body).dispatchEvent(e);
      return e;
    };
    // ⚙ Panels
    const sb = doc.createElement('button'); sb.id = 'ov-settings-btn';
    doc.body.appendChild(sb);
    win._ovOpenSettings(sb);
    ok(!!doc.getElementById('ov-settings-pop'), 'F-17 setup: the Panels popover is open');
    let e = esc();
    ok(!doc.getElementById('ov-settings-pop') && e.defaultPrevented,
       'F-17 Escape closes the Panels popover (it closed only on an outside click)');
    ok(doc.activeElement === sb, 'F-17 ...and focus goes back to the Panels button');
    // a tile's ⋮
    const kebab = doc.querySelector('.ov-tile-menu');
    ok(!!kebab, 'F-17 setup: an Overview tile carries its ⋮');
    kebab.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    const tp = doc.getElementById('ov-tile-popover');
    ok(!!tp, 'F-17 setup: the tile popover is open');
    const sel = tp.querySelector('select, button');
    sel.focus();
    ok(doc.activeElement === sel, 'F-17 setup: focus is on a control inside the popover');
    e = esc(sel);
    ok(!doc.getElementById('ov-tile-popover') && e.defaultPrevented,
       'F-17 Escape closes the tile ⋮ popover, from a control inside it too');
    ok(doc.activeElement === kebab, 'F-17 ...and focus goes back to that ⋮, not <body>');
    // the State History drawer
    const hp = doc.createElement('div');
    hp.id = 'history-panel'; hp.className = 'history-panel history-panel-open';
    hp.innerHTML = '<button id="hp-x">x</button><div id="history-content" data-loaded="1"></div>';
    doc.body.appendChild(hp);
    const dlg = doc.createElement('dialog'); dlg.setAttribute('open', '');
    doc.body.appendChild(dlg);
    esc();
    ok(hp.classList.contains('history-panel-open'), 'F-17 a modal open over the page owns the Escape, not the drawer');
    dlg.remove();
    esc(null, true);
    ok(hp.classList.contains('history-panel-open'),
       'F-17 an Escape another layer already consumed does not also close the drawer');
    // the grid's inspector rule (docs/192 CS01) still comes first
    const cell = doc.querySelector('[data-kbd-cell]');
    const ip = doc.createElement('div'); ip.id = 'inspector-pane'; ip.innerHTML = '<p>q1</p>';
    doc.body.appendChild(ip);
    if (cell) {
      cell.focus();
      esc(cell);
      ok(!ip.innerHTML.trim() && hp.classList.contains('history-panel-open'),
         'F-17 from a grid cell Escape closes the INSPECTOR first; the drawer stays');
    }
    doc.getElementById('hp-x').focus();
    e = esc(doc.getElementById('hp-x'));
    ok(!hp.classList.contains('history-panel-open') && e.defaultPrevented,
       'F-17 Escape closes the State History drawer (only its ✕ did)');
    ok(win.localStorage.getItem('quam_history_panel_open') === '0',
       'F-17 ...through its one toggle, so the remembered state says closed as the ✕ would');
  }

  // ── QA F-19: the in-page jump bar keeps the URL in step ─────────────────
  {
    const T = world(CHAIN, { url: '/topology?view=overview', state: { htmx: true } });
    const win = T.win, doc = T.doc;
    const btn = doc.querySelector('.topo-subnav-btn[data-view="coherence"]');
    win.setChipStatusView('coherence', btn, true);
    ok(win.location.pathname + win.location.search === '/topology?view=coherence',
       'F-19 an in-page tab rewrites the URL, so F5 / a copied link lands there ('
       + win.location.search + ')');
    ok(win.history.state && win.history.state.htmx === true,
       'F-19 ...keeping htmx\'s marker on the entry (replaceState, no new entry)');
    win.setChipStatusView('frequencies', null, true);
    ok(win.location.search === '?view=coherence',
       'F-19 a call with no button (the mount\'s deep link, the sidebar) leaves the URL alone');
  }

  // ── QA F-20: Back returns to where the user had scrolled, not the anchor ──
  function stubPane(T, st0) {
    const pane = T.doc.getElementById('table-pane');
    const box = { st: st0 };
    Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => 700 });
    Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: () => 20000 });
    Object.defineProperty(pane, 'scrollTop', { configurable: true, get: () => box.st, set: (v) => { box.st = v; } });
    pane.getBoundingClientRect = () => ({ top: 0, bottom: 700, left: 0, right: 1000, width: 1000, height: 700 });
    return box;
  }
  function geomAll(T, tops) {
    VIEWS.forEach(function (v) {
      const el = T.doc.querySelector(SEL[v]);
      if (!el) return;
      const t = tops[v] == null ? 99999 : tops[v];
      el.getBoundingClientRect = () => ({ top: t, bottom: t + 300, left: 0, right: 1000, width: 1000, height: 300 });
    });
  }
  {
    // the record: section-relative, merged into the entry's state
    const T = world(CHAIN, { url: '/topology?view=coherence', state: { htmx: true } });
    const win = T.win, pane = T.doc.getElementById('table-pane');
    win.setChipStatusView('coherence', null, false);     // build the metric groups
    await sleep(900);                                    // past the spy suppression
    const box = stubPane(T, 5751);
    geomAll(T, { overview: -5000, health: -4600, topology: -4000, trends: -3000,
                 fidelity2q: -2000, fidelity1q: -1200, readout: -900, coherence: -300, frequencies: 400 });
    pane.dispatchEvent(new win.Event('scroll'));
    await sleep(320);
    const rec = win.history.state && win.history.state.smChipScroll;
    ok(rec && rec.url === '/topology?view=coherence' && rec.view === 'coherence' && rec.d === 300 && rec.top === 5751,
       'F-20 a scroll is recorded ON the history entry: section + offset inside it (' + JSON.stringify(rec) + ')');
    ok(win.history.state.htmx === true, 'F-20 ...merged, so htmx\'s {htmx:true} marker survives');
    // a jump lands its target just UNDER the sticky bar (+67 px in real Chrome):
    // that section is the one at the top, not the one above it
    geomAll(T, { readout: -1014, coherence: 67, frequencies: 700 });
    pane.dispatchEvent(new win.Event('scroll'));
    await sleep(320);
    const recJ = win.history.state.smChipScroll;
    ok(recJ && recJ.view === 'coherence' && recJ.d === -67,
       'F-20 a section just under the sticky bar is the one recorded, as the spy lights it ('
       + JSON.stringify(recJ) + ')');
    // a scroll right before navigating away is not lost to the debounce
    box.st = 5900;
    geomAll(T, { coherence: -449, frequencies: 251 });
    pane.dispatchEvent(new win.Event('scroll'));
    // bubbling, as htmx's triggerEvent fires it (QA chipstatus-r2-04: the
    // teardowns listen on `document` now, through the leave registry)
    const ev = new win.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: pane } });
    T.doc.body.dispatchEvent(ev);
    const rec2 = win.history.state.smChipScroll;
    ok(rec2 && rec2.d === 449 && rec2.top === 5900,
       'F-20 a record still pending when the page is swapped out is written first (' + JSON.stringify(rec2) + ')');
    // what htmx 2 does next on a pushed navigation (measured in real Chrome):
    // its history save REPLACES this entry's state with a bare {htmx:true},
    // then fires htmx:beforeHistoryUpdate, then pushes the next URL
    win.history.replaceState({ htmx: true }, '');
    T.doc.body.dispatchEvent(new win.CustomEvent('htmx:beforeHistoryUpdate', { detail: {} }));
    const rec3 = win.history.state && win.history.state.smChipScroll;
    ok(win.history.state.htmx === true && rec3 && rec3.d === 449 && rec3.url === '/topology?view=coherence',
       'F-20 the record survives htmx\'s own history save of the outgoing page ('
       + JSON.stringify(win.history.state) + ')');
    await sleep(10);
    win.history.replaceState({ htmx: true }, '');
    T.doc.body.dispatchEvent(new win.CustomEvent('htmx:beforeHistoryUpdate', { detail: {} }));
    ok(!win.history.state.smChipScroll,
       'F-20 ...and that listener does not outlive the navigation it was kept for');
  }
  {
    // the restore: Back into that entry re-renders the page, whose deep link
    // used to win and land on the section anchor
    const rec = { url: '/topology?view=coherence', view: 'coherence', d: 300, top: 5751 };
    let box;
    const T = world(CHAIN, { url: '/topology?view=coherence', chipView: 'coherence',
                             state: { smChipScroll: rec },
                             beforeMount: function (T0) { box = stubPane(T0, 0); } });
    geomAll(T, { coherence: 3827 });
    await sleep(60);
    ok(!T.scrolled.some((s) => s.behavior === 'smooth'),
       'F-20 the deep link\'s smooth jump to the anchor is NOT taken for a recorded entry');
    ok(box.st === 3827 + 300, 'F-20 the pane lands on the section PLUS the offset the user had scrolled (' + box.st + ')');
    ok(T.lit() === 'coherence/coherence', 'F-20 ...with that section lit (' + T.lit() + ')');
    // lazy content lands above it: the guard carries the offset, not the anchor
    geomAll(T, { coherence: 1200 });
    const before = box.st;
    ok(T.win.ChipStatus.jumpGuard.reanchor((v) => SEL[v]) === true && box.st === before + 1200 + 300,
       'F-20 when Trends / the charts land above it, the re-anchor keeps the offset (' + (box.st - before) + ')');
    ok(!T.scrolled.some((s) => s.behavior === 'auto'), 'F-20 ...by geometry, not by snapping to the section top');
  }
  {
    // htmx's own Back: its restore re-saves the page it leaves and REPLACES the
    // returned-to entry's state with a bare {htmx:true} before the page mounts
    // (measured in real Chrome). The popstate event still carried the record.
    const rec = { url: '/topology?view=coherence', view: 'coherence', d: 300, top: 5751 };
    let box;
    const T = world(CHAIN, { url: '/topology?view=coherence', chipView: 'coherence',
                             state: { htmx: true },
                             beforeMount: function (T0) {
                               box = stubPane(T0, 0);
                               T0.win.dispatchEvent(new T0.win.PopStateEvent('popstate',
                                 { state: { htmx: true, smChipScroll: rec } }));
                             } });
    geomAll(T, { coherence: 3827 });
    await sleep(60);
    ok(box.st === 3827 + 300 && !T.scrolled.some((s) => s.behavior === 'smooth'),
       'F-20 a Back through htmx\'s cache restore (entry state already wiped) still restores (' + box.st + ')');
    // handed out once: a later FORWARD visit to the same URL is a fresh entry
    T.win.ChipStatus.mount({ topo: CHAIN, rawWiring: {}, defaultThresholds: {}, diagFindings: [],
                             metricMeta: {}, chipView: 'coherence' });
    await sleep(60);
    ok(T.scrolled.some((s) => s.behavior === 'smooth' && s.id === 'coherence'),
       'F-20 ...once: the next visit to that URL is a fresh entry and deep-links as before');
  }
  {
    // a record for another URL is not this entry's: the deep link stands
    const rec = { url: '/topology?view=overview', view: 'overview', d: 10, top: 10 };
    const T = world(CHAIN, { url: '/topology?view=coherence', chipView: 'coherence', state: { smChipScroll: rec } });
    await sleep(60);
    ok(T.scrolled.some((s) => s.behavior === 'smooth' && s.id === 'coherence'),
       'F-20 a record taken at another URL is ignored; the ?view= deep link still jumps');
    const T2 = world(CHAIN, { url: '/topology?view=coherence', chipView: 'coherence' });
    await sleep(60);
    ok(T2.scrolled.some((s) => s.behavior === 'smooth' && s.id === 'coherence'),
       'F-20 control: a fresh entry (no record) jumps to the section as before');
  }
  {
    // the jump guard's offset is optional: a plain jump is unchanged
    const T = world(CHAIN);
    T.win.setChipStatusView('coherence', null, false);
    const box = stubPane(T, 1000);
    geomAll(T, { coherence: 500 });
    const J = T.win.ChipStatus.jumpGuard, pane = T.doc.getElementById('table-pane');
    J.note('coherence', pane);
    T.scrolled.length = 0;
    ok(J.reanchor((v) => SEL[v]) === true && box.st === 1000
       && T.scrolled.map((s) => s.id + ':' + s.behavior).join(',') === 'coherence:auto',
       'F-20 control: a jump noted without an offset still re-anchors to the section top');
  }

  console.log(fails ? ('FAILED ' + fails) : 'chip_jump_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
