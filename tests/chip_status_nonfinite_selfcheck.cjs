/* A NaN / infinite per-gate entry is "no value"; it never blanks a section.
 *
 * Decisions D-NF2 and D-NF4. A pair's CZ macro holds InterleavedRB and IRB;
 * one of them is NaN or infinite. Two worlds: the shape the server sends now
 * (the non-finite row has value/raw_value null, raw_text, gate_pick false)
 * and an older shape where the number itself reached the page (an embedded
 * JS literal NaN / Infinity, no pick marks).
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  N1  the 2Q Fid. panel for the gate renders (no TypeError in the colour
 *      helpers) and every pair cell shows its usable value;
 *  N2  the Overview 2Q gate tile says nothing is excluded, because every pair
 *      has a usable value;
 *  N3  a pair whose ONLY per-gate value is non-finite is counted as excluded
 *      once, as a pair.
 *
 * Run: node tests/chip_status_nonfinite_selfcheck.cjs
 *      (driven by tests/test_nonfinite_fidelity.py)
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

function world() {
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
  win.__errors = [];
  win.addEventListener('error', function (ev) { win.__errors.push(String(ev.message || ev.error)); });
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  win._plotlyRender = function () { return Promise.resolve(); };
  win.Plotly = win.Plotly || {};
  return win;
}

function node(id, gl) { return { id: id, grid_location: gl, f_01: 5e9 }; }
function edge(pid, s, t, gfs) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_SNZ', gate_fidelities: gfs };
}
function badRow(bad, marked) {
  const r = { gate: 'cz_SNZ', metric: 'InterleavedRB', level: 'gate', family: 'InterleavedRB',
              physical: false };
  if (marked) Object.assign(r, { raw_value: null, raw_text: String(bad), gate_pick: false,
                                 superseded_by: 'IRB', gate_pick_basis: 'key order' });
  else r.raw_value = bad;
  return r;
}
function goodRow(v, marked) {
  const r = { gate: 'cz_SNZ', metric: 'IRB', value: v, level: 'gate', family: 'InterleavedRB' };
  if (marked) Object.assign(r, { gate_pick: true, gate_pick_basis: 'key order' });
  return r;
}
function topo(bad, marked, lone) {
  const edges = [
    edge('q1-2', 'q1', 'q2', [badRow(bad, marked), goodRow(0.9434, marked)]),
    // the non-finite row AFTER the usable one: the panel's per-pair lookup
    // keeps the last row of a pair, so this is the order that reaches the
    // colour helpers with a non-finite value
    edge('q2-3', 'q2', 'q3', [goodRow(0.9600, marked), badRow(bad, marked)]),
  ];
  if (lone) edges.push(edge('q3-4', 'q3', 'q4', [Object.assign(badRow(bad, false),
                                                  marked ? { raw_value: null, raw_text: String(bad) } : {})]));
  return { nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0'), node('q4', '3,0')],
           edges: edges, summary: {} };
}

function check(label, bad, marked, lone) {
  const win = world();
  let threw = null;
  try {
    win.ChipStatus.mount({ topo: topo(bad, marked, lone), rawWiring: {}, diagFindings: [],
                           metricMeta: {}, defaultThresholds: {} });
  } catch (e) { threw = e; }
  return new Promise(function (resolve) {
    setTimeout(function () {
      const doc = win.document;
      ok(!threw && win.__errors.length === 0,
        label + ' mount runs without an error -- ' + (threw || win.__errors.join(' | ')));
      const sec = doc.querySelector('#topo-2q-rb-panels .topo-section');
      ok(!!sec, label + ' N1a: a 2Q Fid. panel renders');
      const c12 = sec && sec.querySelector('[data-pair="q1-2"].heatmap-cell, .heatmap-cell[data-pair="q1-2"]');
      const c23 = sec && sec.querySelector('.heatmap-cell[data-pair="q2-3"]');
      ok(c12 && /94\.34/.test(c12.textContent) && c23 && /96\.00/.test(c23.textContent),
        label + ' N1b: every pair cell shows its usable value -- '
        + (c12 && c12.textContent) + ' / ' + (c23 && c23.textContent));
      const tiles = Array.prototype.slice.call(doc.querySelectorAll('#topo-overview-tiles .topo-card'));
      const t = tiles.find(function (x) {
        return /2Q gate (fid\. )?\(IRB\)/.test((x.querySelector('.topo-card-title') || {}).textContent || ''); });
      const tv = t ? t.textContent.replace(/\s+/g, ' ') : '';
      if (!lone) {
        ok(t && !/excluded/.test(tv), label + ' N2: nothing is excluded -- ' + tv.slice(0, 140));
      } else {
        ok(t && /1 excluded/.test(tv), label + ' N3: the lone non-finite pair is excluded once -- '
          + tv.slice(0, 140));
      }
      resolve();
    }, 200);
  });
}

(async function () {
  {
    // N0 (D-NF2): the colour helper answers "no value" for a non-finite or
    // missing position instead of throwing
    const win = world();
    win.ChipStatus.mount({ topo: topo(0.5, true, false), rawWiring: {}, diagFindings: [],
                           metricMeta: {}, defaultThresholds: {} });
    const f = win.ChipStatus._interpolateColor;
    const stops = ['#000000', '#ffffff'];
    let out = [];
    try { out = [NaN, Infinity, -Infinity, null, undefined].map(function (t) { return f(t, stops); }); }
    catch (e) { out = ['threw ' + e]; }
    ok(out.length === 5 && out.every(function (c) { return c === 'rgb(136,136,136)'; }),
      'N0: non-finite / missing positions get the neutral colour -- ' + JSON.stringify(out));
    ok(f(1, stops) === 'rgb(255,255,255)', 'N0: a finite position still interpolates');
  }
  for (const bad of [NaN, Infinity]) {
    await check('[server shape, ' + bad + ']', bad, true, false);
    await check('[legacy literal, ' + bad + ']', bad, false, false);
    await check('[server shape + lone, ' + bad + ']', bad, true, true);
  }
  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_nonfinite_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
