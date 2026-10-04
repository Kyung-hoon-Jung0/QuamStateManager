/* On-site report: a CZ gate whose only 2Q figure is XEB vanished from
 * Chip Status > 2Q Fid. The panel builder drew Standard RB and Interleaved RB
 * only, so a gate (cz_SNZ) with a flat `fidelity.XEB` block had no panel.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  X1  an XEB-only gate gets its own panel under an "XEB" heading, and the
 *      section sub-heading names XEB beside RB;
 *  X2  the XEB heading says "as stored" -- never an RB kind (per Clifford /
 *      per gate) the chip does not record for XEB;
 *  X3  an XEB cell's tooltip carries the stored uncertainty and run number;
 *  X4  the RB panels beside it are unchanged (their headings + density keys);
 *  X5  a chip with ONLY XEB is not reported as "no 2Q values".
 *
 * Run: node tests/chip_status_xeb_panel_selfcheck.cjs
 *      (driven by tests/test_xeb_fidelity.py)
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
           active: null, best_gate: 'cz_unipolar', gate_fidelities: gfs };
}
const nodes = [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0')];
const mixed = {
  nodes: nodes,
  edges: [
    edge('q1-2', 'q1', 'q2', [
      { gate: 'cz_unipolar', metric: 'StandardRB', value: 0.9972, level: 'clifford' },
      { gate: 'cz_SNZ', metric: 'XEB', value: 0.9889, err: 0.0012, load_id: 1782, level: null },
    ]),
    edge('q2-3', 'q2', 'q3', [
      { gate: 'cz_unipolar', metric: 'StandardRB', value: 0.9928, level: 'clifford' },
      { gate: 'cz_SNZ', metric: 'XEB', value: 0.9856, err: 0.0021, load_id: 1749, level: null },
    ]),
  ],
  summary: {},
};
const xebOnly = {
  nodes: nodes,
  edges: [edge('q1-2', 'q1', 'q2', [
    { gate: 'cz_SNZ', metric: 'XEB', value: 0.998, err: 0.0023, load_id: 1780, level: null }])],
  summary: {},
};

const w1 = mount(mixed);
const w2 = mount(xebOnly);
setTimeout(function () {
  const host = w1.document.getElementById('topo-2q-rb-panels');
  const h5s = Array.prototype.slice.call(host.querySelectorAll('h5'));
  const xebH = h5s.find(function (h) { return h.getAttribute('data-rb-heading') === 'XEB'; });
  ok(xebH && /^XEB/.test(xebH.textContent),
    'X1a: an XEB heading exists -- ' + (xebH && xebH.textContent));
  ok(!!host.querySelector('#rb-XEB-cz-SNZ'),
    'X1b: the XEB-only gate cz_SNZ has its own panel');
  const h4 = host.querySelector('h4.topo-fidelity-subtitle');
  ok(h4 && h4.textContent === '2Q Gate Fidelity — RB · XEB',
    'X1c: the section sub-heading names RB and XEB -- ' + (h4 && h4.textContent));

  ok(xebH && /as stored/.test(xebH.textContent) && !/Clifford|per gate|EPC|EPG/.test(xebH.textContent),
    'X2: the XEB heading says "as stored" and claims no RB kind -- ' + (xebH && xebH.textContent));

  const cell = host.querySelector('#rb-XEB-cz-SNZ .heatmap-cell[data-pair="q1-2"]');
  const tip = cell && cell.getAttribute('title');
  ok(tip && /98\.89% ± 0\.12% as stored/.test(tip) && /run #1782/.test(tip),
    'X3: the XEB cell tooltip carries the uncertainty and the run -- ' + tip);

  const srbH = h5s.find(function (h) { return h.getAttribute('data-rb-heading') === 'StandardRB'; });
  ok(srbH && /per Clifford \(1 − EPC\)/.test(srbH.textContent),
    'X4a: the Standard RB heading is unchanged -- ' + (srbH && srbH.textContent));
  const keys = Array.prototype.map.call(host.querySelectorAll('[data-density-panel]'),
                                        function (e) { return e.getAttribute('data-density-panel'); });
  ok(keys.indexOf('2q:StandardRB:cz_unipolar') >= 0 && keys.indexOf('2q:XEB:cz_SNZ') >= 0,
    'X4b: density keys -- ' + keys.join(','));

  const host2 = w2.document.getElementById('topo-2q-rb-panels');
  ok(!host2.querySelector('.topo-2qrb-empty') && !!host2.querySelector('#rb-XEB-cz-SNZ'),
    'X5: an XEB-only chip shows its XEB panel, not the empty note');
  const h4b = host2.querySelector('h4.topo-fidelity-subtitle');
  ok(h4b && h4b.textContent === '2Q Gate Fidelity — XEB',
    'X5b: an XEB-only sub-heading says XEB only -- ' + (h4b && h4b.textContent));

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_xeb_panel_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 300);
