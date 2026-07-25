// Behavioral check for Chip Status topology edge orientation. Two review
// rounds: (1) the separate line arrowhead read as too small to notice —
// replaced with a pointed EDGE LABEL; (2) a small triangle glued onto the
// label box still read as a decoration, not a direction — the label's own
// TARGET-facing edge is now reshaped into a point instead (style.css
// .topo-edge-label-arrow-* clip-path; this test only pins the JS-side class
// assignment + label text, not the CSS shape itself):
//  - the SVG line-drawing no longer emits any arrowhead <polygon> at all;
//  - every edge that actually renders (source/target both resolve to a real
//    node — the pre-existing idToIdx guard) gets one of the 4 directional
//    "topo-edge-label-arrow-*" classes AND the "source→target" label text —
//    this now covers CR, CZ, AND a tunable-coupler pair with no CZ macro yet
//    (a real chip's coupler still has a real control/target from its wiring
//    before any gate gets calibrated);
//  - CR's anti-parallel label offset (so the two directions of one physical
//    edge don't overlap) is unchanged.
//
// Run: node tests/chip_status_edge_orientation_selfcheck.cjs   (needs jsdom)
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
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const CS_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'chip-status.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="topo-html-wrap"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  // Single Function scope so app.js's top-level `var UI_CONFIG` (chip-status.js
  // reads it as a bare global) is visible — two separate new Function() calls
  // would each get their own scope and chip-status.js would see it as undefined.
  new win.Function(APP_JS + '\n;\n' + CS_JS).call(win);
  return win;
}

const win = makeWorld();

// grid_location is "col,row" (buildTopology parses THIS field, not bare
// row/col properties — a bare-property fixture silently falls back to an
// index-based layout that doesn't match the intended geometry at all).
const TOPO = {
  nodes: [
    { id: 'qA1', chain: 'A', grid_location: '1,1' },
    { id: 'qA2', chain: 'A', grid_location: '2,1' },   // right of qA1
    { id: 'qA3', chain: 'A', grid_location: '1,0' },   // below qA1 (row0 < row1)
    { id: 'qA4', chain: 'A', grid_location: '2,0' },   // right of qA3
  ],
  edges: [
    { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', has_cz: false, cz_fidelity: null,
      gate_kind: 'cr', directed: true, active: true, best_gate: null },
    { pair_id: 'qA1-qA3', source: 'qA1', target: 'qA3', has_cz: true, cz_fidelity: 0.97,
      gate_kind: 'cz', directed: false, active: null, best_gate: 'cz_flattop' },
    // A tunable coupler with no CZ macro yet — gate_kind='none', but a REAL
    // resolved control/target (the real-chip regression: query.py used to
    // return the wiring key "control_qubit" here instead of a qubit id).
    { pair_id: 'coupler_qA3_qA4', source: 'qA3', target: 'qA4', has_cz: false, cz_fidelity: null,
      gate_kind: 'none', directed: false, active: null, best_gate: null },
  ],
};

win.ChipStatus.mount({ topo: TOPO, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {} });
const html = win.document.getElementById('topo-html-wrap').innerHTML;

ok(html.indexOf('<polygon') < 0, 'no arrowhead <polygon> is drawn on the line anymore');

['qA2→qA1', 'qA1→qA3', 'qA3→qA4'].forEach(function (txt) {
  ok(html.indexOf(txt) >= 0, 'label shows source→target text: ' + txt);
});

const labelDivs = html.match(/<div class="topo-edge-label[^"]*"[^>]*>/g) || [];
ok(labelDivs.length === 3, 'exactly 3 edge labels rendered, got ' + labelDivs.length);
ok(labelDivs.every(function (d) { return /topo-edge-label-arrow-(right|left|up|down)/.test(d); }),
  'every rendered edge label carries a directional arrow class, got: ' + JSON.stringify(labelDivs));

function directionFor(pairId) {
  var div = labelDivs.filter(function (d) { return d.indexOf('data-pair="' + pairId + '"') >= 0; })[0];
  var m = div && div.match(/topo-edge-label-arrow-(right|left|up|down)/);
  return m ? m[1] : null;
}
// CR (qA2->qA1, target directly left of source) points left; CZ (qA1->qA3,
// target directly below source) points down; coupler (qA3->qA4, target
// directly right of source) points right.
ok(directionFor('qA2-qA1') === 'left', 'CR label points toward its target (left), got ' + directionFor('qA2-qA1'));
ok(directionFor('qA1-qA3') === 'down', 'CZ label points toward its target (down), got ' + directionFor('qA1-qA3'));
ok(directionFor('coupler_qA3_qA4') === 'right',
  'uncalibrated-coupler label points toward its target (right), got ' + directionFor('coupler_qA3_qA4'));

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('chip_status_edge_orientation_selfcheck: all checks passed');
process.exit(0);
