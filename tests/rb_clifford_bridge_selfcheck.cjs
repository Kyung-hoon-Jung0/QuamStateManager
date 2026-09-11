/* docs/184 — a fidelity of MINUS twenty percent, and the bridge that produced it.
 *
 * Customer screenshot, from the chip qualibrate was running on 8001:
 *
 *     2Q CLIFFORD FID. (IRB×)
 *     -20.18%  AVG
 *     med -20.18% · -117.27%–76.90% · (2)
 *     EPC 120.18% (×5.37)
 *
 * The fixture below IS those numbers: two pairs whose interleaved-RB gate
 * fidelities are 95.70% and 59.55%, with the run's own divisor 5.37. The second
 * one gives EPG 40.45% × 5.37 = EPC 217%, i.e. a Clifford fidelity of −117%.
 *
 * `epc = n · epg` is a FIRST-ORDER identity — the one the lab's own fidelity.py
 * uses in the other direction. The ceiling is the RB model's own: the fit is a
 * depolarizing decay, EPC = (d−1)/d·(1−α) with α in [0,1], so at d = 4 no
 * depolarizing fit gives an EPC above 0.75. Past it the bridge has left the
 * model.
 *
 * Pins:
 *   B1  no tile on this page ever shows a negative fidelity
 *   B2  the good pair survives, with its real value
 *   B3  the broken pair is EXCLUDED, not hidden — the count says so
 *   B4  …and the tile names the reason, not just "excluded"
 *   B5  every pair broken ⇒ an honest dash, never a number
 *   B6  a healthy chip is completely unchanged by all of this
 *   B7  the ceiling is the model's, applied at the boundary in both directions
 *
 * Run: node tests/rb_clifford_bridge_selfcheck.cjs
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

const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');
const APP_JS = read('app.js');
const TOPO_JS = read('topo-graph.js');
const CS_JS = read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>'
    + '<div id="topo-hero"></div><div id="topo-html-wrap"></div>'
    + '<div id="topo-overview-tiles"></div>'
    + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  return win;
}

const DIV = 5.37;   // the run's own average_gates_per_clifford

function edge(pid, a, b, irbFid) {
  return {
    pair_id: pid, source: a, target: b, has_cz: true, gate_kind: 'cz',
    directed: false, active: null, best_gate: 'cz', cz_fidelity: irbFid,
    gate_fidelities: [{
      metric: 'InterleavedRB', gate: 'cz', level: 'gate',
      value: irbFid, average_gates_per_clifford: DIV
    }]
  };
}

function chip(edges) {
  return {
    nodes: [
      { id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' },
      { id: 'q3', grid_location: '0,1' }, { id: 'q4', grid_location: '1,1' }
    ],
    edges: edges
  };
}

function tiles(win, topo) {
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
  // The Overview tiles mount into their own host, not the html wrap.
  const wrap = win.document.getElementById('topo-overview-tiles');
  return Array.prototype.map.call(wrap.querySelectorAll('.topo-card'), function (c) {
    const t = c.querySelector('.topo-card-title');
    const v = c.querySelector('.topo-card-value');
    const s = c.querySelector('.topo-card-sub');
    return {
      title: t ? t.textContent.replace(/\s+/g, ' ').trim() : '',
      value: v ? v.textContent.replace(/\s+/g, ' ').trim() : '',
      sub: s ? s.textContent.replace(/\s+/g, ' ').trim() : ''
    };
  });
}
function byTitle(all, needle) {
  return all.filter(function (t) { return t.title.indexOf(needle) >= 0; })[0];
}

// ── the customer's chip: one good pair, one far too noisy ───────────────────
{
  const win = makeWorld();
  const all = tiles(win, chip([edge('q1-q2', 'q1', 'q2', 0.9570),
                               edge('q3-q4', 'q3', 'q4', 0.5955)]));

  // B1 — the property the whole round is about, over EVERY tile.
  const negative = all.filter(function (t) { return /-\d/.test(t.value); });
  ok(negative.length === 0,
    'B1: no tile shows a negative fidelity — ' + JSON.stringify(negative));

  const cliff = byTitle(all, 'Clifford fid. (IRB');
  ok(!!cliff, 'the IRB-derived Clifford tile is rendered — titles seen: '
     + JSON.stringify(all.map(function (t) { return t.title; })));

  // B2 — 1 − (1 − 0.9570)·5.37 = 0.76909
  ok(/76\.9/.test(cliff.value),
    'B2: the pair the bridge can carry keeps its real value — ' + cliff.value);

  // B3 — the other one is set aside and COUNTED
  ok(/\(1\)/.test(cliff.sub),
    'B3: the aggregate is over one pair now — ' + cliff.sub);
  ok(/1 excluded/.test(cliff.sub),
    'B3: …and the excluded one is not silently missing — ' + cliff.sub);

  // B4 — and the reason is named
  ok(/too noisy/.test(cliff.sub) && /depolarizing limit/.test(cliff.sub),
    'B4: the tile says WHY, not just "excluded" — ' + cliff.sub);
  ok(cliff.sub.indexOf('5.37') >= 0,
    'B4: …and names the bridge it could not cross — ' + cliff.sub);

  // the measured IRB tile beside it is untouched: those are real measurements,
  // however bad the gate is. Only the DERIVED number was nonsense.
  const irb = byTitle(all, 'gate fid. (IRB)');
  ok(irb && /77\.6/.test(irb.value),
    'the measured IRB tile still averages both pairs — ' + (irb && irb.value));
}

// ── B5: every pair past the ceiling ⇒ a dash, never a number ───────────────
{
  const win = makeWorld();
  const all = tiles(win, chip([edge('q1-q2', 'q1', 'q2', 0.5955),
                               edge('q3-q4', 'q3', 'q4', 0.5000)]));
  const cliff = byTitle(all, 'Clifford fid. (IRB');
  ok(cliff && cliff.value === '—',
    'B5: with nothing the bridge can carry, the tile is an honest dash — '
    + (cliff && cliff.value));
  ok(cliff && /2 excluded/.test(cliff.sub),
    'B5: …saying how many it set aside — ' + (cliff && cliff.sub));
}

// ── B6: a healthy chip is unchanged ────────────────────────────────────────
{
  const win = makeWorld();
  const all = tiles(win, chip([edge('q1-q2', 'q1', 'q2', 0.995),
                               edge('q3-q4', 'q3', 'q4', 0.990)]));
  const cliff = byTitle(all, 'Clifford fid. (IRB');
  // 1 − 0.005·5.37 = 0.97315 ; 1 − 0.010·5.37 = 0.9463 ; avg 0.95973
  ok(cliff && /95\.9|96\.0/.test(cliff.value),
    'B6: both pairs still average normally — ' + (cliff && cliff.value));
  ok(cliff && /\(2\)/.test(cliff.sub) && !/excluded/.test(cliff.sub),
    'B6: nothing excluded, nothing said — ' + (cliff && cliff.sub));
}

// ── B7: the ceiling is the model's, and it is applied AT the boundary ──────
{
  // epc exactly 0.75 is the maximally depolarizing fit — still inside the
  // model, so it is kept; a hair past it is not.
  const at = 1 - 0.75 / DIV;            // epc === 0.75 exactly
  const past = 1 - 0.7501 / DIV;
  let win = makeWorld();
  let cliff = byTitle(tiles(win, chip([edge('q1-q2', 'q1', 'q2', at)])),
                      'Clifford fid. (IRB');
  ok(cliff && /25\.0/.test(cliff.value) && !/excluded/.test(cliff.sub),
    'B7: epc at exactly (d−1)/d is kept — ' + (cliff && cliff.value + ' | ' + cliff.sub));

  win = makeWorld();
  cliff = byTitle(tiles(win, chip([edge('q1-q2', 'q1', 'q2', past)])),
                  'Clifford fid. (IRB');
  ok(cliff && cliff.value === '—' && /1 excluded/.test(cliff.sub),
    'B7: a hair past it is not — ' + (cliff && cliff.value + ' | ' + cliff.sub));
}

console.log(fails ? 'FAILED (' + fails + ')'
  : 'rb_clifford_bridge_selfcheck ok (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
