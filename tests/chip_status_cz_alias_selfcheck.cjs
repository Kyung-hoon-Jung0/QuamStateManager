/* A pair's `cz` alias decides its one 2Q number on Chip Status.
 *
 * A pair can hold two CZ macros and point `macros.cz` at one of them; the
 * server ships that macro as `op_gate` (query.pair_operational_gate). Every
 * surface that shows ONE 2Q number per pair shows the aliased macro's value,
 * even when the other macro measured better. A pair with no alias keeps the
 * best macro, as before. The per-gate panels stay: they are labelled views of
 * every macro.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  A1  the Overview 2Q gate (IRB) tile averages the aliased value of q1-2
 *      (94.80) with the best of the alias-free q3-4 (98.00): 96.40%;
 *  A2  the default 2Q source value of q1-2 is the aliased one (0.948);
 *  A3  the alias-free pair keeps its best macro (0.98);
 *  A4  both per-gate panels still exist and the cz_SNZ panel still names
 *      q1-2's cz_SNZ value (96.00).
 *
 * Run: node tests/chip_status_cz_alias_selfcheck.cjs
 *      (driven by tests/test_pair_verdicts.py)
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

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
  + '<div class="topo-section" data-topo-section="2qrb"><div id="topo-2q-rb-panels"></div></div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=gate' });
const win = dom.window;
win.htmx = { process: function () {}, ajax: function () {} };
win.fetch = function () { return new Promise(function () {}); };
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);
win._plotlyRender = function () { return Promise.resolve(); };
win.Plotly = win.Plotly || {};

function node(id, gl) { return { id: id, grid_location: gl, f_01: 5e9 }; }
function irb(gate, v) { return { gate: gate, metric: 'InterleavedRB', value: v, level: 'gate', family: 'InterleavedRB' }; }
function edge(pid, s, t, op, best, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: best, op_gate: op, gate_fidelities: gfs };
}
const topo = {
  nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0'), node('q4', '3,0')],
  edges: [
    edge('q1-2', 'q1', 'q2', 'cz_unipolar', 'cz_unipolar',
         [irb('cz_unipolar', 0.948), irb('cz_SNZ', 0.960)]),
    edge('q3-4', 'q3', 'q4', null, 'cz_SNZ',
         [irb('cz_unipolar', 0.90), irb('cz_SNZ', 0.98)]),
  ],
  summary: {},
};
win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                       defaultThresholds: {} });

setTimeout(function () {
  const doc = win.document;
  const tiles = Array.prototype.slice.call(doc.querySelectorAll('#topo-overview-tiles .topo-card'));
  const irbTile = tiles.find(function (t) {
    return /2Q gate (fid\. )?\(IRB\)/.test((t.querySelector('.topo-card-title') || {}).textContent || ''); });
  const tv = irbTile ? irbTile.textContent.replace(/\s+/g, ' ') : '';
  ok(irbTile && /96\.40%/.test(tv) && !/97\.00%/.test(tv),
    'A1: the Overview IRB tile reads the aliased macro of q1-2 -- ' + tv.slice(0, 140));

  const rows12 = win.ChipStatus._opRows(topo.edges[0]);
  ok(rows12.length === 1 && rows12[0].gate === 'cz_unipolar',
    'A2a: the pair\'s rows are its aliased macro\'s -- ' + JSON.stringify(rows12.map(function (r) { return r.gate; })));
  const rows34 = win.ChipStatus._opRows(topo.edges[1]);
  ok(rows34.length === 2, 'A3a: an alias-free pair keeps every macro\'s rows');

  // the hero map's edge patch for the default source (IRB) on q1-2 / q3-4
  const hero = doc.getElementById('topo-hero');
  const htxt = hero ? hero.textContent : '';
  ok(/94\.8/.test(htxt) && !/96\.0/.test(htxt),
    'A2b: the map labels q1-2 with the aliased value -- ' + htxt.replace(/\s+/g, ' ').slice(0, 200));
  ok(/98\.0/.test(htxt), 'A3b: the map labels the alias-free pair with its best macro');

  const host = doc.getElementById('topo-2q-rb-panels');
  const snz = host && host.querySelector('#rb-InterleavedRB-cz-SNZ');
  const uni = host && host.querySelector('#rb-InterleavedRB-cz-unipolar');
  const c12 = snz && snz.querySelector('.heatmap-cell[data-pair="q1-2"]');
  ok(!!snz && !!uni && c12 && /96\.00/.test(c12.textContent),
    'A4: both per-gate panels remain, labelled, with their own values');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_cz_alias_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 200);
