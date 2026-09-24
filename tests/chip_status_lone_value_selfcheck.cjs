/* QA F-19b — one value, one position on the colour scale.
 *
 * Measured on the customer chip copy: /topology?view=gate, the 'flattop'
 * Standard RB panel held ONE pair (65.83 %). Its tile was painted mid-scale
 * (Citrus stops[2], rgb(173,221,142)) while its only bar was painted with the
 * LOW end (stops[0], rgb(247,252,185)): the tile used t = 0.5 for a lone value,
 * the bar computed (v - min) / range = 0 / 1 = 0. And the bar changed colour
 * on the first bar-palette switch, because recolorBarCharts rebuilds each bar
 * from its tile's data-heat-t (0.5), or mid-scale when a tile carries none.
 * The 1Q metric panels had the same bar arithmetic.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom, both
 * palettes set to Citrus exactly as the tester had them):
 *  L1  a lone 2Q value: tile t = 0.5 and the tile is the mid stop (unchanged);
 *  L2  its bar is the SAME mid stop, not the low end;
 *  L3  a bar-palette switch to the same palette leaves that bar's colour alone;
 *  L4  a lone 1Q value: its bar is the mid stop and survives the switch too;
 *  L5  multi-value panels are unchanged: min bar = low stop, max bar = high stop,
 *      for 2Q and 1Q alike.
 *
 * Run: node tests/chip_status_lone_value_selfcheck.cjs
 *      (driven by tests/test_chip_status_lone_value.py)
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
  + '<div id="topo-metric-panels"></div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=gate' });
const win = dom.window;
// The tester's palettes (both selectors on Citrus), read by chip-status.js at load.
win.localStorage.setItem('quam_heatmap_palette', 'Citrus');
win.localStorage.setItem('quam_bar_palette', 'Citrus');
win.htmx = { process: function () {}, ajax: function () {} };
win.fetch = function () { return new Promise(function () {}); };   // nothing answers: no races
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);
// Capture every chart the panels hand to Plotly and make it look drawn, so the
// shipped recolorBarCharts (it looks for .js-plotly-plot + el.data) reaches it.
const charts = {};
win._plotlyRender = function (el, data, layout) {
  const id = typeof el === 'string' ? el : el.id;
  const node = typeof el === 'string' ? win.document.getElementById(el) : el;
  charts[id] = { data: data, layout: layout };
  if (node) { node.classList.add('js-plotly-plot'); node.data = data; }
  return Promise.resolve();
};
let restyles = 0;
win.Plotly = {
  restyle: function (el, upd) { restyles++; el.data[0].marker.color = upd['marker.color'][0]; },
};

const CITRUS = { low: 'rgb(247,252,185)', mid: 'rgb(173,221,142)', high: 'rgb(49,163,84)' };

function node(id, gl, extra) {
  const n = { id: id, grid_location: gl, f_01: 5e9 };
  Object.keys(extra || {}).forEach(function (k) { n[k] = extra[k]; });
  return n;
}
function edge(pid, s, t, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_SNZ', gate_fidelities: gfs };
}
const topo = {
  nodes: [
    // T1 on ONE qubit only (the lone 1Q value); f_01 on all three (multi-value).
    node('q1', '0,0', { T1: 2.0e-5, f_01: 4.9e9 }),
    node('q2', '1,0', { f_01: 5.0e9 }),
    node('q3', '2,0', { f_01: 5.1e9 }),
  ],
  edges: [
    edge('q1-2', 'q1', 'q2', [
      { gate: 'cz_flattop', metric: 'StandardRB', value: 0.6583, level: 'clifford' },
      { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.9104, level: 'clifford' },
    ]),
    edge('q2-3', 'q2', 'q3', [
      { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.9706, level: 'clifford' },
    ]),
  ],
  summary: {},
};
win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                       defaultThresholds: {} });

function barColor(chartId, label) {
  const c = charts[chartId];
  if (!c) return null;
  const tr = c.data[0];
  const i = tr.y.indexOf(label);
  return i < 0 ? null : tr.marker.color[i];
}
function norm(css) {   // '#addd8e' or 'rgb(173, 221, 142)' -> 'rgb(173,221,142)'
  if (!css) return css;
  const s = String(css).trim();
  if (s[0] === '#') {
    return 'rgb(' + parseInt(s.substr(1, 2), 16) + ',' + parseInt(s.substr(3, 2), 16) + ','
      + parseInt(s.substr(5, 2), 16) + ')';
  }
  return s.replace(/\s+/g, '');
}

setTimeout(function () {
  const doc = win.document;
  const lone2qCell = doc.querySelector('#rb-StandardRB-cz-flattop .heatmap-cell[data-pair="q1-2"]');
  const lone2qChart = 'rb-StandardRB-cz-flattop-chart';
  const lone1qChart = 'mp-T1-chart';
  const multi2qChart = 'rb-StandardRB-cz-SNZ-chart';
  const multi1qChart = 'mp-f-01-chart';

  ok(lone2qCell && lone2qCell.getAttribute('data-heat-t') === '0.500000'
     && norm(lone2qCell.style.backgroundColor) === CITRUS.mid,
    'L1: the lone 2Q tile sits mid-scale — t=' + (lone2qCell && lone2qCell.getAttribute('data-heat-t'))
    + ' bg=' + (lone2qCell && lone2qCell.style.backgroundColor));
  const b2 = barColor(lone2qChart, 'q1-2');
  ok(norm(b2) === CITRUS.mid, 'L2: the lone 2Q bar is the same mid stop as its tile — ' + b2);
  const b1 = barColor(lone1qChart, 'q1');
  ok(norm(b1) === CITRUS.mid, 'L4a: the lone 1Q bar is the mid stop — ' + b1);

  ok(norm(barColor(multi2qChart, 'q2-3')) === CITRUS.high && norm(barColor(multi2qChart, 'q1-2')) === CITRUS.low,
    'L5a: a multi-value 2Q panel is unchanged (max=high, min=low) — '
    + barColor(multi2qChart, 'q2-3') + ' / ' + barColor(multi2qChart, 'q1-2'));
  ok(norm(barColor(multi1qChart, 'q3')) === CITRUS.high && norm(barColor(multi1qChart, 'q1')) === CITRUS.low,
    'L5b: a multi-value 1Q panel is unchanged (max=high, min=low) — '
    + barColor(multi1qChart, 'q3') + ' / ' + barColor(multi1qChart, 'q1'));

  // The shipped palette switch: same palette, so nothing may move.
  win.switchBarPalette('Citrus');
  ok(restyles > 0, 'L3/L4 precondition: recolorBarCharts actually restyled the drawn charts (' + restyles + ')');
  ok(norm(barColor(lone2qChart, 'q1-2')) === norm(b2),
    'L3: a bar-palette switch leaves the lone 2Q bar alone — ' + b2 + ' -> ' + barColor(lone2qChart, 'q1-2'));
  ok(norm(barColor(lone1qChart, 'q1')) === norm(b1),
    'L4b: a bar-palette switch leaves the lone 1Q bar alone — ' + b1 + ' -> ' + barColor(lone1qChart, 'q1'));

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_lone_value_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 300);
