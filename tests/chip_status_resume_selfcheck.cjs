/* QA chipstatus-r2-01 (review) — a refresh of Chip Status keeps the reader's place.
 *
 * Measured in real Chrome before the fix: reading Coherence (all sections
 * built), set q3 T1 in the inspector. The pane re-rendered through GET
 * /topology and came up as a first visit — Trends / 2Q Fid. / 1Q / readout /
 * metric panels back to their 0-51 px placeholders, the absolute scrollTop put
 * back against that shrunken pane (7796 -> 6677, "Read. Fid."; or 1793,
 * "Topology"), and the tab row reset to Topology. The same after Ctrl+Z and
 * Take live.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom; the
 * swap is simulated the way htmx performs it: beforeSwap on the pane, the new
 * markup, the fragment's mount script, afterSwap):
 *  P1  the tab the reader had survives the refresh;
 *  P2  the sections the reader had built are built again with NO intersection
 *      (the observer here never fires, like a section already in view);
 *  P3  the section at the pane top sits at the same offset although Trends
 *      above it is still a placeholder (an absolute scrollTop is 1350 px off);
 *  P4  when Trends lands above it, the section is put back again;
 *  P5  ...unless the reader moved (a wheel on the pane) in the meantime;
 *  P6  a NAVIGATION to /topology (the sidebar link: the request's element is
 *      the link, not <body>) still lands on Topology, by design;
 *  P7  a place captured for a refresh whose answer was NOT Chip Status (an
 *      empty state) is dropped, so a later ?view= deep link is not hijacked.
 *
 * Run: node tests/chip_status_resume_selfcheck.cjs
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const VIEWS = ['overview', 'health', 'topology', 'trends', 'fidelity2q', 'fidelity1q',
               'readout', 'coherence', 'frequencies', 'calibration'];
const DASH = '<div class="topo-dashboard"><nav class="topo-subnav">'
  + VIEWS.map(function (v) { return '<button class="topo-subnav-btn" data-view="' + v + '">' + v + '</button>'; }).join('')
  + '</nav><div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div id="topo-thresh-editor" hidden></div>'
  + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
  + '<div class="topo-section" data-topo-section="overview"></div>'
  + '<div class="topo-section" data-topo-section="health"></div>'
  + '<div id="sec-topology"></div>'
  + '<div class="topo-section" data-topo-section="trends"><div id="topo-trends"></div></div>'
  + '<div class="topo-section" data-topo-section="fidelity" id="sec-fidelity">'
  + '<div id="topo-2q-rb-panels" data-topo-section="2qrb"></div></div>'
  + '<div class="topo-section" data-topo-section="fid1q" id="sec-fidelity-1q"></div>'
  + '<div class="topo-section" data-topo-section="fidro" id="sec-readout"></div>'
  + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
  + '</div>';

const dom = new JSDOM('<!DOCTYPE html><html><body><nav id="sidebar"><a id="nav-cs" href="/topology">Chip Status</a></nav>'
  + '<div id="table-pane">' + DASH + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
const win = dom.window;
const doc = win.document;
win.UI_CONFIG = { topoLivePollInterval: 3 };
// An observer that never reports: nothing is built by intersection, so a
// section that is built after the refresh was built by the resume.
win.IntersectionObserver = function () {};
win.IntersectionObserver.prototype.observe = function () {};
win.IntersectionObserver.prototype.unobserve = function () {};
win.IntersectionObserver.prototype.disconnect = function () {};

/* A fake layout: every section sits at a fixed content Y; Trends is 1400 px
   tall once its charts landed and a 50 px placeholder before. */
const PANE_TOP = 100;
function trendsBuilt() { return !!doc.querySelector('#table-pane .topo-trend-box'); }
function contentY(el) {
  const T = trendsBuilt() ? 1400 : 50;
  const sec = el.getAttribute && el.getAttribute('data-topo-section');
  const grp = el.getAttribute && el.closest && el.closest('#topo-metric-panels') && el.getAttribute('data-group');
  if (grp === 'coherence') return 2000 + T + 4200;
  if (grp === 'frequency') return 2000 + T + 5000;
  if (grp === 'calibration') return 2000 + T + 5800;
  if (sec === 'overview') return 0;
  if (sec === 'health') return 900;
  if (el.id === 'sec-topology') return 1200;
  if (sec === 'trends') return 2000;
  if (sec === 'fidelity') return 2000 + T;
  if (sec === 'fid1q') return 2000 + T + 2500;
  if (sec === 'fidro') return 2000 + T + 3000;
  return null;
}
win.Element.prototype.getBoundingClientRect = function () {
  const pane = doc.getElementById('table-pane');
  if (this === pane) return { top: PANE_TOP, left: 0, width: 800, height: 600, bottom: PANE_TOP + 600, right: 800 };
  const y = pane && pane.contains(this) ? contentY(this) : null;
  if (y === null) return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 };
  const top = PANE_TOP + y - pane.scrollTop;
  return { top: top, left: 0, width: 800, height: 100, bottom: top + 100, right: 800 };
};
win.Element.prototype.scrollIntoView = function () {};   // jsdom has none (a deep link calls it)

function node(id, gl, t1) {
  return { id: id, grid_location: gl, f_01: 5.0e9, T1: t1, T2ramsey: 1e-5, gate_fidelity_avg: 0.999,
           metrics: { T1: { value: t1 }, T2ramsey: { value: 1e-5 }, f_01: { value: 5.0e9 },
                      gate_fidelity_avg: { value: 0.999 } } };
}
const T0 = {
  nodes: [node('q1', '0,0', 2.4e-5), node('q2', '1,0', 1.9e-5), node('q3', '0,1', 1.1349e-5)],
  edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
            gate_kind: 'cz', directed: false, active: null, best_gate: 'cz',
            gate_fidelities: [{ gate: 'cz', metric: 'StandardRB', value: 0.97, level: 'clifford' }] }],
  summary: {},
};
function opts(topo, chipView) {
  return { topo: JSON.parse(JSON.stringify(topo)), rawWiring: {}, diagFindings: [], metricMeta: {},
           chipView: chipView || '',
           defaultThresholds: { T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' } } };
}
let served = T0, rendered = T0;
win.fetch = function (url) {
  const p = String(url).split('?')[0];
  if (p === '/api/topology') {
    const body = JSON.parse(JSON.stringify(served));
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
  }
  if (p === '/api/topology-mtime') {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ changed: false }); } });
  }
  return new Promise(function () {});
};

/* htmx: /topology/trends lands only when the test says so; /topology swaps
   the pane the way htmx does (beforeSwap -> new markup + its mount script ->
   afterSwap). */
let trendsPending = [];
let swapElt = null;       // the element that ISSUED the next pane request (null: htmx.ajax, <body>)
let nextEmpty = false;    // the next pane answer is not Chip Status (an empty state)
win.htmx = {
  process: function () {},
  ajax: function (verb, url, spec) {
    if (String(url).indexOf('/topology/trends') === 0) {
      return new Promise(function (resolve) {
        trendsPending.push(function () {
          const host = doc.getElementById('topo-trends');
          if (host) host.innerHTML = '<div class="topo-trend-box"></div>';
          resolve();
        });
      });
    }
    if (String(url).split('?')[0] === '/topology' && spec && spec.target === '#table-pane') {
      return new Promise(function (resolve) {
        setTimeout(function () { swapPane(url, swapElt || doc.body); swapElt = null; resolve(); }, 20);
      });
    }
    return Promise.resolve();
  },
};
function swapPane(url, elt) {
  const pane = doc.getElementById('table-pane');
  const q = String(url).split('?')[1] || '';
  const view = (q.match(/(?:^|&)view=([^&]*)/) || [])[1] || '';
  pane.dispatchEvent(new win.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: {
    target: pane, shouldSwap: true, requestConfig: { elt: elt, path: url, verb: 'get' } } }));
  if (nextEmpty) {
    nextEmpty = false;
    pane.innerHTML = '<p class="muted">No state loaded</p>';
  } else {
    pane.innerHTML = DASH;
    win.ChipStatus.mount(opts(rendered = served, view));   // the fragment's inline script
    win.ChipStatus.liveDetection();
  }
  pane.dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
}

new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);
win._plotlyRender = function () { return Promise.resolve(); };   // no Plotly here

win.ChipStatus.mount(opts(T0, ''));
win.ChipStatus.liveDetection();

const sleep = (ms) => new Promise(function (r) { setTimeout(r, ms); });
const pane = () => doc.getElementById('table-pane');
function tab() { const a = doc.querySelector('.topo-subnav-btn.active'); return a ? a.getAttribute('data-view') : null; }
function cohOffset() {
  const el = doc.querySelector('#topo-metric-panels [data-group="coherence"]');
  return el ? el.getBoundingClientRect().top - PANE_TOP : null;
}
function landTrends() { const fns = trendsPending; trendsPending = []; fns.forEach(function (f) { f(); }); }
function mutate(to) {
  served = to;
  doc.body.dispatchEvent(new win.CustomEvent('pulses-rows-changed', { bubbles: true }));
}

(async function () {
  /* the reader: every heavy section built, Coherence tab, 180 px into Coherence */
  win.setChipStatusView('fidelity2q', null, false);
  win.setChipStatusView('trends', null, false);
  landTrends();
  await sleep(40);
  win.setChipStatusView('coherence', null, false);
  const cohY = 2000 + 1400 + 4200;
  pane().scrollTop = cohY + 180;
  ok(tab() === 'coherence' && cohOffset() === -180 && trendsBuilt(),
    'setup: Coherence tab, 180 px into Coherence, Trends built — ' + tab() + ' ' + cohOffset());

  /* the edit: /api/topology changed, the pane re-renders through GET /topology */
  const T1 = JSON.parse(JSON.stringify(T0));
  T1.nodes[2].T1 = 6e-5; T1.nodes[2].metrics.T1.value = 6e-5;
  mutate(T1);
  await sleep(450);     // 250 ms debounce + fetch + the 20 ms swap + frames
  ok(tab() === 'coherence', 'P1: the tab survives the refresh — ' + tab());
  ok(!!doc.querySelector('#topo-metric-panels [data-group="coherence"]')
     && doc.getElementById('topo-2q-rb-panels').children.length > 0,
    'P2: the metric panels and the 2Q RB panels are built again with no intersection');
  ok(!trendsBuilt() && trendsPending.length === 1,
    'P2: …and Trends is being fetched again (' + trendsPending.length + ' pending)');
  ok(cohOffset() === -180,
    'P3: Coherence sits at the same offset with Trends still a placeholder — ' + cohOffset());

  landTrends();
  await sleep(60);
  ok(trendsBuilt() && cohOffset() === -180,
    'P4: Trends landed above it and Coherence was put back — ' + cohOffset());

  /* P5: a refresh, then the reader wheels before Trends lands */
  mutate(T0);
  await sleep(450);
  ok(cohOffset() === -180, 'P5 setup: resumed again — ' + cohOffset());
  pane().dispatchEvent(new win.Event('wheel', { bubbles: true }));
  const before = pane().scrollTop;
  landTrends();
  await sleep(60);
  ok(pane().scrollTop === before,
    'P5: after a wheel, Trends landing does not pull the pane back — ' + before + ' -> ' + pane().scrollTop);

  /* P6: the sidebar link navigates to /topology: top of the page, Topology tab */
  swapElt = doc.getElementById('nav-cs');
  win.htmx.ajax('GET', '/topology', { target: '#table-pane', swap: 'innerHTML' });
  await sleep(120);
  ok(tab() === 'topology', 'P6: a navigation (not <body>) still lands on Topology — ' + tab());
  ok(!doc.querySelector('#topo-metric-panels [data-group="coherence"]'),
    'P6: …and does not build what the last page had built');

  /* P7: a refresh answered by an empty state, then a deep link to Overview */
  win.setChipStatusView('coherence', null, false);
  nextEmpty = true;
  win.htmx.ajax('GET', '/topology', { target: '#table-pane', swap: 'innerHTML' });
  await sleep(80);
  ok(!doc.querySelector('.topo-dashboard'), 'P7 setup: the refresh answered with no Chip Status');
  win.htmx.ajax('GET', '/topology?view=overview', { target: '#table-pane', swap: 'innerHTML' });
  await sleep(120);
  ok(tab() === 'overview', 'P7: the deep link lands on its own view, not a stale resumed one — ' + tab());

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_resume_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
