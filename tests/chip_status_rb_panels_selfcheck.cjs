/* QA F-21 — the 2Q RB panels say WHICH fidelity they show.
 *
 * Measured on the customer chip copy: /topology?view=gate read
 * "2Q Gate Fidelity" > "2Q Gate Fidelity — RB" > "Standard RB" > "SNZ" over
 * tiles like 91.04 %, and the bar axis read "Standard RB — SNZ (%)". A
 * Standard RB number is 1 − EPC per CLIFFORD, not a gate fidelity
 * (docs/138: "StandardRB 0.970608 = 1 - EPC per CLIFFORD" / "InterleavedRB
 * 0.993363 = 1 - EPG per GATE"); the Overview tile and the pair popup already
 * said so, this panel did not.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  R1  the Standard RB sub-heading says "per Clifford (1 − EPC)";
 *  R2  the Interleaved RB sub-heading says "per gate (1 − EPG)";
 *  R3  a Standard RB cell's tooltip says "per Clifford", an IRB cell's "per gate";
 *  R4  each bar chart's axis title carries the same words;
 *  R5  the section's own headings (user-directed, docs/141 4o) are unchanged,
 *      and the per-panel density keys (persisted sizes) are unchanged.
 *
 * Run: node tests/chip_status_rb_panels_selfcheck.cjs
 *      (driven by tests/test_chip_status_rb_wording.py)
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
win.fetch = function () { return new Promise(function () {}); };   // nothing answers: no races
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);
// Capture every chart the panels hand to Plotly (chip-status.js calls the
// app.js global bare, so replacing it on window is what it reaches).
const charts = {};
win._plotlyRender = function (el, data, layout) {
  const id = typeof el === 'string' ? el : el.id;
  charts[id] = { data: data, layout: layout };
  return Promise.resolve();
};
// QA F-02 (cs-ui): the chart pump waits for Plotly to load first; the renderer
// above is stubbed, so Plotly counts as loaded.
win.Plotly = win.Plotly || {};

function node(id, gl) { return { id: id, grid_location: gl, f_01: 5e9 }; }
function edge(pid, s, t, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_SNZ', gate_fidelities: gfs };
}
const topo = {
  nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0')],
  edges: [
    edge('q1-2', 'q1', 'q2', [
      { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.9104, level: 'clifford' },
      { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.9912, level: 'gate' },
    ]),
    edge('q2-3', 'q2', 'q3', [
      { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.9706, level: 'clifford' },
      { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.9934, level: 'gate' },
    ]),
  ],
  summary: {},
};
win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                       defaultThresholds: {} });

setTimeout(function () {
  const doc = win.document;
  const host = doc.getElementById('topo-2q-rb-panels');
  const h5s = Array.prototype.slice.call(host.querySelectorAll('h5'));
  const srbH = h5s.find(function (h) { return /Standard RB/.test(h.textContent); });
  const irbH = h5s.find(function (h) { return /Interleaved RB/.test(h.textContent); });
  ok(srbH && /per Clifford \(1 − EPC\)/.test(srbH.textContent),
    'R1: the Standard RB heading says per Clifford (1 − EPC) — ' + (srbH && srbH.textContent));
  ok(irbH && /per gate \(1 − EPG\)/.test(irbH.textContent),
    'R2: the Interleaved RB heading says per gate (1 − EPG) — ' + (irbH && irbH.textContent));

  const srbCell = host.querySelector('#rb-StandardRB-cz-SNZ .heatmap-cell[data-pair="q1-2"]');
  const irbCell = host.querySelector('#rb-InterleavedRB-cz-SNZ .heatmap-cell[data-pair="q1-2"]');
  ok(srbCell && /91\.04% per Clifford/.test(srbCell.getAttribute('title')),
    'R3a: a Standard RB cell tooltip says per Clifford — ' + (srbCell && srbCell.getAttribute('title')));
  ok(irbCell && /99\.12% per gate/.test(irbCell.getAttribute('title')),
    'R3b: an Interleaved RB cell tooltip says per gate — ' + (irbCell && irbCell.getAttribute('title')));

  const srbChart = charts['rb-StandardRB-cz-SNZ-chart'];
  const irbChart = charts['rb-InterleavedRB-cz-SNZ-chart'];
  const t = function (c) { return c && c.layout && c.layout.xaxis && c.layout.xaxis.title
                                   && c.layout.xaxis.title.text; };
  ok(/per Clifford/.test(t(srbChart) || ''), 'R4a: the Standard RB axis title says per Clifford — ' + t(srbChart));
  ok(/per gate/.test(t(irbChart) || '') && !/Clifford/.test(t(irbChart) || ''),
    'R4b: the Interleaved RB axis title says per gate — ' + t(irbChart));

  const h4 = host.querySelector('h4.topo-fidelity-subtitle');
  ok(h4 && h4.textContent === '2Q Gate Fidelity — RB',
    'R5a: the section sub-heading is unchanged — ' + (h4 && h4.textContent));
  const keys = Array.prototype.map.call(host.querySelectorAll('[data-density-panel]'),
                                        function (e) { return e.getAttribute('data-density-panel'); });
  ok(keys.indexOf('2q:StandardRB:cz_SNZ') >= 0 && keys.indexOf('2q:InterleavedRB:cz_SNZ') >= 0,
    'R5b: the persisted per-panel density keys are unchanged — ' + keys.join(','));

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_rb_panels_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 200);
