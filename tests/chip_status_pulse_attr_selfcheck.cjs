/* QA F-04 — adjacent 2Q tiles quote different pulses of one pair, so each
 * number must name the pulse it came from.
 *
 * On the customer's 5Q chip, pair q1-2 carries two pulses:
 *   cz_flattop  StandardRB 0.6583   InterleavedRB 0.9570
 *   cz_SNZ      StandardRB 0.9104   InterleavedRB 0.9233
 * Each Overview tile takes its OWN per-pair best (by design: chip_health's
 * "best of the pair's candidate gates"), so the SRB tile quoted cz_SNZ while
 * the IRB tile, the "(Best)" tile and Health quoted cz_flattop -- and nothing
 * on the page said so. Health also called an interleaved-RB number "Bell".
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  A  every pair tile's hover row names its own pulse, in a span of its own
 *     (the value span keeps exactly the formatted number);
 *  B  Health's "lowest" line names the pulse and says "Bell" only when the
 *     number is the Bell-state one (fidelity_source 'macro').
 *
 * Run: node tests/chip_status_pulse_attr_selfcheck.cjs
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
const SRC = read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function node(id, gl) {
  return { id: id, grid_location: gl, T1: 2e-5, gate_fidelity_avg: 0.999,
           metrics: { T1: { value: 2e-5 }, gate_fidelity_avg: { value: 0.999 } } };
}
function topoFixture(source) {
  return {
    nodes: [node('q1', '0,0'), node('q2', '1,0')],
    edges: [{
      pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, gate_kind: 'cz',
      directed: false, active: null,
      cz_fidelity: 0.957, best_gate: 'cz_flattop', fidelity_source: source,
      metrics: { cz_fidelity: { value: 0.957 } },
      gate_fidelities: [
        { gate: 'cz_flattop', metric: 'StandardRB', value: 0.6583,
          derived_gate_fidelity: 0.70, average_gates_per_clifford: 1.5 },
        { gate: 'cz_flattop', metric: 'InterleavedRB', value: 0.9570 },
        { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.9104,
          derived_gate_fidelity: 0.9829, average_gates_per_clifford: 1.5 },
        { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.9233 },
      ],
      gate_details: [{ name: 'cz_SNZ', length: 78 }, { name: 'cz_flattop', length: 120 }],
    }],
    summary: { gate_vocab: 'CZ' },
  };
}

function world(source) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
    + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
    + '<div id="topo-health-worst"></div>'
    + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
  const win = dom.window;
  win.UI_CONFIG = {};
  win.htmx = { process: function () {}, ajax: function () {} };
  win.fetch = function () { return new Promise(function () {}); };
  new win.Function(SRC).call(win);
  win.ChipStatus.mount({ topo: topoFixture(source), rawWiring: {}, diagFindings: [],
                         metricMeta: {}, defaultThresholds: {} });
  return win;
}

function hoverRows(win, tileId) {
  const tile = win.document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="' + tileId + '"]');
  if (!tile) return null;
  tile.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const pop = win.document.getElementById('ov-hover-pop');
  if (!pop) return [];
  return Array.prototype.map.call(pop.querySelectorAll('.ov-hover-item'), function (it) {
    const g = it.querySelector('.ov-hover-gate');
    return { id: it.querySelector('.ov-hover-id').textContent,
             val: it.querySelector('.ov-hover-val').textContent,
             gate: g ? g.textContent : null };
  });
}

/* A: the hover names the pulse behind every pair number */
{
  const win = world('interleaved_rb');
  const expect = {
    srb: ['cz_SNZ', '91.04%'],
    srb_gate: ['cz_SNZ', '98.29%'],
    irb: ['cz_flattop', '95.70%'],
    irb_cliff: ['cz_flattop', null],
    gate2q: ['cz_flattop', null],
    gate2q_len: ['cz_flattop', null],
  };
  Object.keys(expect).forEach(function (tid) {
    const rows = hoverRows(win, tid);
    const r = (rows || []).filter(function (x) { return x.id === 'q1-2'; })[0];
    ok(!!r, 'A0 ' + tid + ': the tile lists q1-2 — ' + JSON.stringify(rows));
    if (!r) return;
    ok(r.gate === expect[tid][0],
      'A1 ' + tid + ': q1-2 names the pulse its number came from — ' + r.gate);
    if (expect[tid][1]) {
      ok(r.val === expect[tid][1],
        'A2 ' + tid + ': the value span is exactly the number (' + r.val + ')');
    }
  });
  const pop = win.document.getElementById('ov-hover-pop');
  ok(pop && pop.querySelector('.ov-hover-grid-gate'),
    'A3: a grid that carries pulse names is laid out wider');

  /* B: Health */
  const worst = win.document.getElementById('topo-health-worst');
  const chip = worst && Array.prototype.filter.call(worst.querySelectorAll('.worst-chip'),
    function (b) { return b.getAttribute('data-inspect-id') === 'q1-2'; })[0];
  ok(!!chip, 'B0: Health lists q1-2 as the lowest pair');
  const t = chip ? chip.textContent : '';
  ok(/cz_flattop/.test(t), 'B1: …naming the pulse that number came from — ' + t);
  ok(!/Bell/.test(t), 'B2: …and an interleaved-RB number is not called "Bell" — ' + t);
}
{
  const win = world('macro');
  const worst = win.document.getElementById('topo-health-worst');
  const chip = worst && worst.querySelector('.worst-chip[data-inspect-id="q1-2"]');
  const t = chip ? chip.textContent : '';
  ok(/Bell/.test(t), 'B3: a Bell-state number still says "Bell" — ' + t);
}

console.log(fails ? ('FAILED (' + fails + ')')
  : ('chip_status_pulse_attr_selfcheck ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
