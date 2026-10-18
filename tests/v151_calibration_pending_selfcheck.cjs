/* v1.5.1 SA-F9 (g): Calibration Age while the change ledger is rebuilt.
 *
 * After a Take live the ledger the ages come from is rebuilt; a topology
 * built in that moment had no ages, and Chip Status showed "— no timestamps"
 * until a reload. The server now marks such a topology calibration_pending
 * (tests/test_v151_messages.py); the page says it is reading, asks again every
 * 2 s while the answer is pending, and re-renders through its one refresh
 * path once it is not.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  A  a pending topology shows "reading history", never "no timestamps";
 *  B  it re-asks /api/topology while pending, and re-renders once settled;
 *  C  a settled topology asks nothing more.
 *
 * Run: node tests/v151_calibration_pending_selfcheck.cjs
 *      (driven by tests/test_v151_messages.py)
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

function node(id, gl, t1, cal) {
  const n = { id: id, grid_location: gl, f_01: 5.0e9, T1: t1, gate_fidelity_avg: 0.999,
              metrics: { T1: { value: t1 }, gate_fidelity_avg: { value: 0.999 } } };
  if (cal) { n.last_calibrated = cal; n.last_calibrated_from = 'run'; }
  return n;
}
const NOW = Date.now();
function topo(pending, cal) {
  const t = {
    nodes: [node('q1', '0,0', 2.4e-5, cal), node('q2', '1,0', 1.9e-5, cal)],
    edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
              gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' }],
    summary: cal ? { oldest_calibration: cal, newest_calibration: cal } : {},
  };
  if (pending) t.calibration_pending = true;
  return t;
}

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div id="topo-thresh-editor" hidden></div>'
  + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=overview' });
const win = dom.window;
win.UI_CONFIG = { topoLivePollInterval: 3 };
const ajaxCalls = [];
win.htmx = { process: function () {},
             ajax: function (verb, url, spec) { ajaxCalls.push({ verb: verb, url: url }); } };
let served = topo(true, null);
let topoFetches = 0;
win.fetch = function (url) {
  const p = String(url).split('?')[0];
  if (p === '/api/topology') {
    topoFetches++;
    const body = JSON.parse(JSON.stringify(served));
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
  }
  if (p === '/api/topology-mtime') {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ changed: false }); } });
  }
  return new Promise(function () {});
};
// the 2 s re-ask, shortened so the check runs in well under a second
const realST = win.setTimeout.bind(win);
win.setTimeout = function (f, ms) { return realST(f, ms === 2000 ? 15 : ms); };

new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);

const wait = (ms) => new Promise(function (r) { setTimeout(r, ms); });
const text = () => win.document.getElementById('table-pane').textContent;

(async function () {
  win.ChipStatus.mount({ topo: topo(true, null), rawWiring: {}, diagFindings: [], metricMeta: {},
                         defaultThresholds: {} });
  win.ChipStatus.liveDetection();
  ok(/reading history/.test(text()), 'A1: a pending topology says it is reading the history');
  ok(!/no timestamps/.test(text()), 'A2: ...and never "no timestamps"');

  await wait(80);
  ok(topoFetches >= 2, 'B1: it re-asks while the answer is pending (' + topoFetches + ' fetches)');
  ok(ajaxCalls.length === 0, 'B2: a still-pending answer re-renders nothing (' + ajaxCalls.length + ')');

  served = topo(false, NOW - 3600 * 1000);
  await wait(400);
  ok(ajaxCalls.some(function (c) { return c.verb === 'GET' && c.url === '/topology'; }),
     'B3: the settled answer re-renders the page through its one path (' + JSON.stringify(ajaxCalls) + ')');

  // C: a settled mount asks nothing more
  const before = topoFetches;
  win.ChipStatus.mount({ topo: topo(false, NOW - 3600 * 1000), rawWiring: {}, diagFindings: [],
                         metricMeta: {}, defaultThresholds: {} });
  await wait(120);
  ok(topoFetches === before, 'C1: a settled topology is not re-asked (' + (topoFetches - before) + ')');
  ok(!/reading history/.test(text()) && /Calibration Age/i.test(text()),
     'C2: the settled page shows an age, not "reading"');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('v151_calibration_pending_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
