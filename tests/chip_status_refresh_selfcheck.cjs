/* QA chipstatus-r2-01 — an in-app edit, a Ctrl+Z or a "Take live" must
 * refresh Chip Status.
 *
 * Measured in real Chrome before the fix: set q3 T1 in the inspector, then
 * Ctrl+Z. The page kept showing the edited 60.0 µs while /api/topology (and
 * the chip) held 11.35 µs. liveDetection's refreshMetrics lives OUTSIDE
 * ChipStatus.mount, so `topo = data` leaked a global and the next line,
 * buildHealthSummary(), threw a ReferenceError that `.catch(function(){})`
 * swallowed -- nothing on the page ever changed.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  A  a mutation whose /api/topology payload equals the rendered one (keys in
 *     another order, as jsonify sorts them) re-renders nothing;
 *  B  a mutation that changed the topology re-renders the pane through its
 *     one render path: htmx GET /topology into #table-pane, without ?view=;
 *  C  an undo's second, identical fetch does not re-render twice;
 *  D  no global `topo` leaks at any point;
 *  E  a threshold typed and not applied is never wiped by that re-render.
 *
 * Run: node tests/chip_status_refresh_selfcheck.cjs
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

function node(id, gl, t1) {
  return { id: id, grid_location: gl, f_01: 5.0e9, T1: t1, gate_fidelity_avg: 0.999,
           metrics: { T1: { value: t1 }, gate_fidelity_avg: { value: 0.999 } } };
}
const T0 = {
  nodes: [node('q1', '0,0', 2.4e-5), node('q2', '1,0', 1.9e-5), node('q3', '0,1', 1.1349e-5)],
  edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
            gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' }],
  summary: {},
};
// The same payload with every object's keys reversed: what jsonify (sorted
// keys) returns for a page embedded with json.dumps (insertion order).
function reorder(v) {
  if (Array.isArray(v)) return v.map(reorder);
  if (v && typeof v === 'object') {
    const out = {};
    Object.keys(v).reverse().forEach(function (k) { out[k] = reorder(v[k]); });
    return out;
  }
  return v;
}

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div id="topo-thresh-editor" hidden></div>'
  + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=overview' });
const win = dom.window;
// Bridge every global chip-status.js reads bare.
win.UI_CONFIG = { topoLivePollInterval: 3 };
const ajaxCalls = [];
win.htmx = { process: function () {},
             ajax: function (verb, url, spec) { ajaxCalls.push({ verb: verb, url: url, spec: spec }); } };
let served = T0;
win.fetch = function (url) {
  const p = String(url).split('?')[0];
  if (p === '/api/topology') {
    const body = JSON.parse(JSON.stringify(served));
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
  }
  if (p === '/api/topology-mtime') {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ changed: false }); } });
  }
  return new Promise(function () {});   // everything else: hold, never race the asserts
};
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);

win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(T0)), rawWiring: {}, diagFindings: [],
                       metricMeta: {},
                       defaultThresholds: { T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' } } });
win.ChipStatus.liveDetection();

function mutate() { win.document.body.dispatchEvent(new win.CustomEvent('pulses-rows-changed', { bubbles: true })); }
function settle() { return new Promise(function (r) { setTimeout(r, 450); }); }

(async function () {
  ok(typeof win.topo === 'undefined', 'D0: no global `topo` after mount');

  /* A: nothing changed on this page */
  served = reorder(T0);
  mutate();
  await settle();
  ok(ajaxCalls.length === 0,
    'A1: a mutation that moved nothing here re-renders nothing (' + ajaxCalls.length + ' ajax)');
  ok(typeof win.topo === 'undefined', 'D1: …and leaks no global `topo` (' + typeof win.topo + ')');

  /* B: q3 T1 changed (the undo in the report) */
  const T1 = JSON.parse(JSON.stringify(T0));
  T1.nodes[2].T1 = 6e-5; T1.nodes[2].metrics.T1.value = 6e-5;
  served = T1;
  mutate();
  await settle();
  ok(ajaxCalls.length === 1, 'B1: a real topology change re-renders the pane — ' + ajaxCalls.length + ' ajax');
  const c = ajaxCalls[0] || {};
  ok(c.verb === 'GET' && c.url === '/topology',
    'B2: …through the page\'s own render path, without ?view= — ' + c.verb + ' ' + c.url);
  ok(c.spec && c.spec.target === '#table-pane' && c.spec.swap === 'innerHTML',
    'B3: …into #table-pane — ' + JSON.stringify(c.spec));
  ok(typeof win.topo === 'undefined', 'D2: …and leaks no global `topo` (' + typeof win.topo + ')');

  /* C: an undo fetches twice; the second one is the same answer */
  mutate();
  await settle();
  ok(ajaxCalls.length === 1, 'C1: the same answer twice does not re-render twice — ' + ajaxCalls.length);

  /* and a change back (the Ctrl+Z) re-renders again */
  served = T0;
  mutate();
  await settle();
  ok(ajaxCalls.length === 2, 'C2: changing back re-renders again — ' + ajaxCalls.length);

  /* E: a typed, unapplied threshold survives a mutation */
  win.toggleThresholdEditor();
  const f = win.document.querySelector('#topo-thresh-editor .thresh-in');
  const saved = f.getAttribute('data-saved');
  f.value = String(parseFloat(saved) + 10);
  f.dispatchEvent(new win.Event('input', { bubbles: true }));
  served = T1;
  mutate();
  await settle();
  ok(ajaxCalls.length === 2, 'E1: a typed threshold holds the re-render back — ' + ajaxCalls.length);
  f.value = saved;
  f.dispatchEvent(new win.Event('input', { bubbles: true }));
  mutate();
  await settle();
  ok(ajaxCalls.length === 3, 'E2: …and the next mutation catches up once it is gone — ' + ajaxCalls.length);

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_refresh_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
