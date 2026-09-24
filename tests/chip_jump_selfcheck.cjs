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
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
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
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {} });
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

  console.log(fails ? ('FAILED ' + fails) : 'chip_jump_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
