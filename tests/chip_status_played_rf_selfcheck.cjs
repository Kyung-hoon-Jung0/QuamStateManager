/* Chip Status > Frequencies shows the readout frequency the
 * resonator PLAYS (its RF_frequency, resolved), not resonator.f_01.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  R1  the panel is keyed `readout_rf_frequency` and titled "played RF";
 *  R2  its tile and bar carry the played RF value, not f_01;
 *  R3  where f_01 differs (at the shown precision) the panel names it on a
 *      secondary line and in that qubit's tooltip;
 *  R4  a qubit whose RF equals f_01 gets no secondary line (no clutter);
 *  R5  a qubit with no RF_frequency (source f_01) is named as a fallback.
 *  H1  the hero map's "Readout freq" patch is keyed readout_rf_frequency, and a
 *      remembered `readout_frequency` choice lands on it;
 *  H2  a stone shows the played RF; where f_01 differs it rides the sub-line
 *      and the hover; RF == f_01 shows one value (no sub, no f_01 in hover);
 *  H3  a stone with no RF_frequency says it shows f_01.
 *
 * Run: node tests/chip_status_played_rf_selfcheck.cjs
 *      (driven by tests/test_pair_detuning_and_played_rf.py)
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
  + '<div id="topo-metric-panels"></div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
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
win.Plotly = { restyle: function () {} };

function node(id, gl, ro, rf, src) {
  return { id: id, grid_location: gl, f_01: 5e9, readout_frequency: ro,
           readout_rf_frequency: rf, readout_rf_source: src };
}
const topo = {
  nodes: [
    node('qa', '0,0', 7.6266e9, 7.61027e9, 'RF_frequency'),   // differs
    node('qb', '1,0', 7.2e9, 7.2e9, 'RF_frequency'),          // equal
    node('qc', '2,0', 7.4e9, 7.4e9, 'f_01'),                  // no RF_frequency
  ],
  edges: [],
  summary: {},
};
try { win.localStorage.setItem('quam_topo_hero_metric_v2', 'readout_frequency'); } catch (e) {}
win.ChipStatus.mount({ topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
                       defaultThresholds: {} });

setTimeout(function () {
  const doc = win.document;
  const sec = doc.getElementById('mp-readout-rf-frequency');
  ok(!!sec, 'R1: the Frequencies panel is keyed readout_rf_frequency');
  ok(!doc.getElementById('mp-readout-frequency'), 'R1: the f_01 panel is not drawn instead');
  const title = sec && sec.querySelector('.topo-metric-panel-title');
  ok(title && /played RF/.test(title.textContent), 'R1: the title says "played RF" -- ' + (title && title.textContent));

  const cell = (q) => sec && sec.querySelector('.tmap-node[data-qubit="' + q + '"]');
  const val = (q) => { const c = cell(q); const v = c && c.querySelector('.heatmap-cell-value'); return v ? v.textContent : null; };
  ok(val('qa') === '7.6103', 'R2: qa tile shows the played RF -- ' + val('qa'));
  const ch = charts['mp-readout-rf-frequency-chart'];
  const i = ch ? ch.data[0].y.indexOf('qa') : -1;
  ok(ch && Math.abs(ch.data[0].x[i] - 7.61027) < 1e-9, 'R2: qa bar is the played RF -- ' + (ch && ch.data[0].x[i]));

  const f01Line = sec && sec.querySelector('[data-ro-f01]');
  ok(f01Line && f01Line.textContent.indexOf('qa: resonator f_01 7.6266 GHz') >= 0,
    'R3: the secondary line names qa\'s f_01 -- ' + (f01Line && f01Line.textContent));
  ok((cell('qa').getAttribute('title') || '').indexOf('resonator f_01 7.6266 GHz') >= 0,
    'R3: qa tooltip carries f_01 -- ' + cell('qa').getAttribute('title'));
  ok(ch && String(ch.data[0].customdata[i]).indexOf('resonator f_01 7.6266 GHz') >= 0, 'R3: qa bar hover carries f_01');

  ok(f01Line && f01Line.textContent.indexOf('qb') < 0, 'R4: qb (RF == f_01) is not listed');
  ok((cell('qb').getAttribute('title') || '').indexOf('resonator f_01') < 0, 'R4: qb tooltip has one value only');

  const fb = sec && sec.querySelector('[data-ro-fallback]');
  ok(fb && /no RF_frequency/.test(fb.textContent) && fb.textContent.indexOf('qc') >= 0,
    'R5: qc is named as an f_01 fallback -- ' + (fb && fb.textContent));
  ok((cell('qc').getAttribute('title') || '').indexOf('no RF_frequency') >= 0, 'R5: qc tooltip says so');

  const hero = doc.getElementById('topo-hero');
  const stone = (q) => hero && hero.querySelector('.topo-hero-node[data-hero-qubit="' + q + '"]');
  const txt = (q, cls) => { const g = stone(q); const t = g && g.querySelector('.' + cls); return t ? t.textContent : null; };
  const hov = (q) => { const g = stone(q); const t = g && g.querySelector('title'); return t ? t.textContent : ''; };
  ok(!!stone('qa'), 'H1: the hero map rendered stones');
  ok(txt('qa', 'topo-hero-val') === '7.6103 GHz' || /^7\.6103/.test(txt('qa', 'topo-hero-val') || ''),
    'H1/H2: a remembered readout_frequency choice shows the played RF -- ' + txt('qa', 'topo-hero-val'));
  ok(/f_01 7\.6266/.test(txt('qa', 'topo-hero-sub') || ''), 'H2: qa sub-line names f_01 -- ' + txt('qa', 'topo-hero-sub'));
  ok(/resonator f_01 7\.6266 GHz/.test(hov('qa')), 'H2: qa hover names f_01 -- ' + hov('qa'));
  ok(txt('qb', 'topo-hero-sub') == null && hov('qb').indexOf('f_01') < 0,
    'H2: qb (RF == f_01) shows one value -- sub=' + txt('qb', 'topo-hero-sub') + ' hover=' + hov('qb'));
  ok(/no RF_frequency/.test(hov('qc')), 'H3: qc hover says it shows f_01 -- ' + hov('qc'));
  const patch = hero && Array.prototype.find.call(hero.querySelectorAll('[data-hero-metric]'),
    (b) => /Readout freq/.test(b.textContent));
  ok(patch && patch.getAttribute('data-hero-metric') === 'readout_rf_frequency',
    'H1: the Readout freq patch is keyed readout_rf_frequency -- ' + (patch && patch.getAttribute('data-hero-metric')));

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_played_rf_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
}, 300);
