/* + -- one per-gate fidelity per pulse, and an unphysical one shown.
 *
 * A macro can hold two per-gate keys (InterleavedRB and a lab alias IRB) that
 * disagree. The server's resolver (query.pair_gate_fidelity) picks one and
 * stamps `gate_pick` on the rows; this page must show THAT one everywhere,
 * never the larger. A fidelity outside (0, 1] is shown with its raw number and
 * marked unphysical, never as a percentage, never dropped.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  G1  the 2Q Fid. panel shows the picked value (90.00, not the alias's 96.00)
 *      and its title names the key and the disagreement;
 *  G2  the disagreeing cell carries a warning mark; a one-key cell does not;
 *  G3  the unphysical IRB is a cell with its raw number and the word unphysical;
 *  G4  the panel's stats leave it out and say so;
 *  G5  it is not a bar;
 *  G6  the Overview 2Q gate (IRB) tile averages the picked values (94.00%);
 *  G7  the unpicked row is kept aside on `_gfAll`, not lost.
 *
 * Run: node tests/chip_status_gate_pick_selfcheck.cjs
 *      (driven by tests/test_pair_gate_fidelity.py)
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
const charts = {};
win._plotlyRender = function (el, data, layout) {
  const id = typeof el === 'string' ? el : el.id;
  charts[id] = { data: data, layout: layout };
  return Promise.resolve();
};
win.Plotly = win.Plotly || {};

function node(id, gl) { return { id: id, grid_location: gl, f_01: 5e9 }; }
function edge(pid, s, t, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_SNZ', gate_fidelities: gfs };
}
const CONFLICT = [['InterleavedRB', 0.90], ['IRB', 0.96]];
const topo = {
  nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0'), node('q4', '3,0')],
  edges: [
    edge('q1-2', 'q1', 'q2', [
      { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.90, level: 'gate', family: 'InterleavedRB',
        gate_pick: true, gate_pick_basis: 'key order', gate_conflict: CONFLICT },
      { gate: 'cz_SNZ', metric: 'IRB', value: 0.96, level: 'gate', family: 'InterleavedRB',
        gate_pick: false, superseded_by: 'InterleavedRB', gate_pick_basis: 'key order', gate_conflict: CONFLICT },
    ]),
    edge('q2-3', 'q2', 'q3', [
      { gate: 'cz_SNZ', metric: 'IRB', raw_value: 1.5345, physical: false, level: 'gate', family: 'InterleavedRB' },
    ]),
    edge('q3-4', 'q3', 'q4', [
      { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.98, level: 'gate', family: 'InterleavedRB' },
    ]),
  ],
  summary: {},
};
win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                       defaultThresholds: {} });

setTimeout(function () {
  const doc = win.document;
  const host = doc.getElementById('topo-2q-rb-panels');
  const sec = host.querySelector('#rb-InterleavedRB-cz-SNZ');
  ok(!!sec, 'the Interleaved RB panel for cz_SNZ exists');
  const c12 = sec && sec.querySelector('.heatmap-cell[data-pair="q1-2"]');
  const t12 = c12 ? (c12.getAttribute('title') || '') : '';
  ok(c12 && /90\.00/.test(c12.textContent) && !/96\.00/.test(c12.textContent),
    'G1a: the picked value is shown, not the larger alias -- ' + (c12 && c12.textContent));
  ok(/from InterleavedRB/.test(t12) && /2 IRB values on this pair: InterleavedRB 0\.9000, IRB 0\.9600/.test(t12),
    'G1b: the title names the key and the disagreement -- ' + t12);
  ok(c12 && /\u26a0/.test(c12.textContent), 'G2a: the disagreeing cell carries a warning mark');
  const c34 = sec && sec.querySelector('.heatmap-cell[data-pair="q3-4"]');
  ok(c34 && !/\u26a0/.test(c34.textContent) && /from InterleavedRB/.test(c34.getAttribute('title') || '')
     && !/IRB values/.test(c34.getAttribute('title') || ''),
    'G2b: a one-key cell has no mark, only its key in the title -- ' + (c34 && c34.getAttribute('title')));

  const c23 = sec && sec.querySelector('.heatmap-cell[data-pair="q2-3"]');
  ok(c23 && c23.getAttribute('data-fid-unphysical') === '1' && /1\.5345/.test(c23.textContent)
     && !/153/.test(c23.textContent) && /unphysical/.test(c23.getAttribute('title') || ''),
    'G3: the unphysical IRB is shown raw and named -- ' + (c23 && c23.textContent));

  const stat = sec && sec.querySelector('.topo-metric-panel-stat');
  ok(stat && /avg 94\.00%/.test(stat.textContent) && /1 unphysical/.test(stat.textContent)
     && /\(2\/3 pairs\)/.test(stat.textContent),
    'G4: the stats leave it out and say so -- ' + (stat && stat.textContent));

  const chart = charts['rb-InterleavedRB-cz-SNZ-chart'];
  const ys = chart && chart.data && chart.data[0].y;
  ok(ys && ys.length === 2 && ys.indexOf('q2-3') < 0, 'G5: it is not a bar -- ' + JSON.stringify(ys));

  const tiles = Array.prototype.slice.call(doc.querySelectorAll('#topo-overview-tiles .topo-card'));
  const irbTile = tiles.find(function (t) {
    return /2Q gate (fid\. )?\(IRB\)/.test((t.querySelector('.topo-card-title') || {}).textContent || ''); });
  const tv = irbTile ? irbTile.textContent : '';
  ok(irbTile && /94\.00%/.test(tv) && !/97\.00%/.test(tv),
    'G6: the Overview IRB tile averages the picked values -- ' + tv.replace(/\s+/g, ' ').slice(0, 120));

  const e12 = topo.edges[0];
  ok(Array.isArray(e12._gfAll) && e12._gfAll.length === 2 && e12.gate_fidelities.length === 1,
    'G7: the unpicked row is kept aside on _gfAll');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_gate_pick_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 200);
