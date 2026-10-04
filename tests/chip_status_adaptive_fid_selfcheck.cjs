/* The 2Q Fid. section draws whatever 2Q fidelity a gate carries, in any
 * spelling: the panel family is the SERVER's classification
 * (query.fidelity_family), so the builder never compares metric names itself.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  A1  a lowercase / separated spelling (family InterleavedRB from metric "irb")
 *      lands in the Interleaved RB panel with its "per gate" wording;
 *  A2  a Bell-state row gets a "Bell state" panel that says "state fidelity";
 *  A3  a metric no table knows ("other:MyGateScore") gets a panel under its OWN
 *      name, "as stored", and is not judged against the CZ spec;
 *  A4  a row the server marked as not-a-fidelity (family null) draws nothing;
 *  A5  the sub-heading lists what is shown; RB-only keeps its old wording.
 *
 * Run: node tests/chip_status_adaptive_fid_selfcheck.cjs
 *      (driven by tests/test_adaptive_2q_fid.py)
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

function mount(topo) {
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
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                         defaultThresholds: {} });
  return win;
}

function node(id, gl) { return { id: id, grid_location: gl, f_01: 5e9 }; }
function edge(pid, s, t, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_wave', gate_fidelities: gfs };
}
const topo = {
  nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0')],
  edges: [
    edge('q1-2', 'q1', 'q2', [
      { gate: 'cz_wave', metric: 'irb', value: 0.9931, level: 'gate', family: 'InterleavedRB' },
      { gate: 'cz_wave', metric: 'bell state', value: 0.962, level: 'state', family: 'Bell' },
      { gate: 'cz_wave', metric: 'MyGateScore', value: 0.951, level: null, family: 'other:MyGateScore' },
      { gate: 'cz_wave', metric: 'irb_err', value: 0.0007, level: null, family: null },
    ]),
    edge('q2-3', 'q2', 'q3', [
      { gate: 'cz_wave', metric: 'Interleaved_RB', value: 0.9902, level: 'gate', family: 'InterleavedRB' },
    ]),
  ],
  summary: {},
};
const rbOnly = {
  nodes: topo.nodes,
  edges: [edge('q1-2', 'q1', 'q2', [
    { gate: 'cz_wave', metric: 'srb', value: 0.97, level: 'clifford', family: 'StandardRB' }])],
  summary: {},
};

const w1 = mount(topo);
const w2 = mount(rbOnly);
setTimeout(function () {
  const host = w1.document.getElementById('topo-2q-rb-panels');
  const head = function (k) { return host.querySelector('h5[data-rb-heading="' + k + '"]'); };

  const irbH = head('InterleavedRB');
  ok(irbH && /per gate \(1 − EPG\)/.test(irbH.textContent),
    'A1a: "irb" / "Interleaved_RB" land under Interleaved RB, per gate -- ' + (irbH && irbH.textContent));
  const irbCells = host.querySelectorAll('#rb-InterleavedRB-cz-wave .heatmap-cell:not(.heatmap-cell-none)');
  ok(irbCells.length === 2, 'A1b: both spellings are cells of one panel -- ' + irbCells.length);

  const bellH = head('Bell');
  ok(bellH && /^Bell state fidelity/.test(bellH.textContent),
    'A2: a Bell row gets a Bell state panel -- ' + (bellH && bellH.textContent));

  const othH = head('other:MyGateScore');
  ok(othH && /^MyGateScore/.test(othH.textContent) && /as stored/.test(othH.textContent),
    'A3a: an unknown metric is shown under its own name, as stored -- ' + (othH && othH.textContent));
  const othCell = host.querySelector('#rb-other-MyGateScore-cz-wave .heatmap-cell[data-pair="q1-2"]');
  ok(othCell && /95\.10% as stored/.test(othCell.getAttribute('title')),
    'A3b: its cell says as stored -- ' + (othCell && othCell.getAttribute('title')));

  ok(!host.textContent.includes('0.07%') && !host.querySelector('[data-rb-heading*="err"]'),
    'A4: a row with no family (an uncertainty) draws nothing');

  const h4 = host.querySelector('h4.topo-fidelity-subtitle');
  ok(h4 && h4.textContent === '2Q Gate Fidelity — RB · Bell · other',
    'A5a: the sub-heading lists what is shown -- ' + (h4 && h4.textContent));
  const h4b = w2.document.querySelector('#topo-2q-rb-panels h4.topo-fidelity-subtitle');
  ok(h4b && h4b.textContent === '2Q Gate Fidelity — RB',
    'A5b: RB only keeps the old sub-heading -- ' + (h4b && h4b.textContent));
  const srbH = w2.document.querySelector('#topo-2q-rb-panels h5[data-rb-heading="StandardRB"]');
  ok(srbH && /per Clifford/.test(srbH.textContent), 'A5c: "srb" lands under Standard RB, per Clifford');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_adaptive_fid_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 300);
