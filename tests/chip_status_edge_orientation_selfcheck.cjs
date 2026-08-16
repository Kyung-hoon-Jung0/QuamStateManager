// Behavioral check for Chip Status edge ORIENTATION — which end of a pair is
// the control and which the target, readable off the map itself.
//
// History: an arrowhead on the line read as too small to notice, so it became a
// pointed EDGE LABEL on the card diagram, and this file pinned that label's
// text and its four directional classes.
//
// docs/120 item 11 deleted the card diagram (the customer reported the chip map
// rendering twice), so the LABELS are gone — but the property they carried is
// not. Direction now lives on the hero map as the C / T / M role markers the
// customer asked for, and this file is retargeted at those rather than deleted:
// the question "can a user see which qubit is the control?" still has to have a
// yes, and it is now answered on the only map there is.
//
// What is pinned:
//  - a C marker nearer the SOURCE stone and a T marker nearer the TARGET stone,
//    for every edge that renders;
//  - covering CR, CZ, AND a tunable-coupler pair with no CZ macro yet — a real
//    chip's coupler has a real control/target from its wiring long before any
//    gate is calibrated (the regression where query.py returned the wiring key
//    "control_qubit" instead of a qubit id would surface here);
//  - the M marker sits at the end that actually moves;
//  - no arrowhead <polygon> is drawn on the lines.
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
const read = (f) => fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="topo-hero"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  // Single Function scope so app.js's top-level `var UI_CONFIG` and
  // topo-graph's window.TopoGraph are visible to chip-status.js exactly as in
  // the browser's <head> load order.
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  return win;
}

const win = makeWorld();

// grid_location is "col,row". f_01 is required for the chevrons but the role
// markers do not depend on it — the roles come from the wiring, not the physics.
const TOPO = {
  nodes: [
    { id: 'qA1', chain: 'A', grid_location: '1,1', f_01: 6.1e9, metrics: {} },
    { id: 'qA2', chain: 'A', grid_location: '2,1', f_01: 6.4e9, metrics: {} },   // right of qA1
    { id: 'qA3', chain: 'A', grid_location: '1,0', f_01: 5.9e9, metrics: {} },   // below qA1
    { id: 'qA4', chain: 'A', grid_location: '2,0', f_01: 6.7e9, metrics: {} },   // right of qA3
  ],
  edges: [
    { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', has_cz: false, cz_fidelity: null,
      gate_kind: 'cr', directed: true, active: true, best_gate: null,
      moving_qubit: 'control', metrics: {} },
    { pair_id: 'qA1-qA3', source: 'qA1', target: 'qA3', has_cz: true, cz_fidelity: 0.97,
      gate_kind: 'cz', directed: false, active: null, best_gate: 'cz_flattop',
      moving_qubit: 'target', metrics: {} },
    // A tunable coupler with no CZ macro yet — gate_kind='none', but a REAL
    // resolved control/target (the real-chip regression: query.py used to
    // return the wiring key "control_qubit" here instead of a qubit id).
    { pair_id: 'coupler_qA3_qA4', source: 'qA3', target: 'qA4', has_cz: false, cz_fidelity: null,
      gate_kind: 'none', directed: false, active: null, best_gate: null,
      moving_qubit: null, metrics: {} },
  ],
  summary: {},
};

win.ChipStatus.mount({ topo: TOPO, rawWiring: {}, defaultThresholds: {},
                       diagFindings: [], metricMeta: {} });
const hero = win.document.getElementById('topo-hero');
const html = hero.innerHTML;

ok(html.indexOf('<polygon') < 0, 'no arrowhead <polygon> is drawn on the line');

function stoneXY(id) {
  const g = hero.querySelector('[data-hero-qubit="' + id + '"]');
  if (!g) return null;
  const m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(g.getAttribute('transform') || '');
  return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]) } : null;
}
function markerAt(letter, pairId, qubitId) {
  // Every marker names its pair AND its endpoint, because a qubit can be the
  // control of one pair and the target of another — "the C nearest qA1" is an
  // ambiguous question on any chip with a chain running through it.
  const g = hero.querySelector('.cm-role-' + letter
    + '[data-cm-pair="' + pairId + '"][data-cm-at="' + qubitId + '"]');
  if (!g) return null;
  const c = g.querySelector('circle');
  return { x: parseFloat(c.getAttribute('cx')), y: parseFloat(c.getAttribute('cy')) };
}
function d(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

ok(hero.querySelectorAll('.cm-role-c').length === 3,
   'a C marker for every rendered edge, got ' + hero.querySelectorAll('.cm-role-c').length);
ok(hero.querySelectorAll('.cm-role-t').length === 3,
   'a T marker for every rendered edge, got ' + hero.querySelectorAll('.cm-role-t').length);

// For each pair: its C is anchored at the SOURCE stone and its T at the TARGET
// stone -- each nearer its own end than the other end. That is the orientation,
// stated positionally and per pair.
[['qA2-qA1', 'qA2', 'qA1'],
 ['qA1-qA3', 'qA1', 'qA3'],
 ['coupler_qA3_qA4', 'qA3', 'qA4']].forEach(function (row) {
  const pid = row[0], srcId = row[1], tgtId = row[2];
  const src = stoneXY(srcId), tgt = stoneXY(tgtId);
  ok(!!src && !!tgt, pid + ': both stones placed');
  const c = markerAt('c', pid, srcId), t = markerAt('t', pid, tgtId);
  ok(!!c, pid + ': C is anchored at the CONTROL end (' + srcId + ')');
  ok(!!t, pid + ': T is anchored at the TARGET end (' + tgtId + ')');
  if (!c || !t || !src || !tgt) return;
  ok(d(c, src) < d(c, tgt), pid + ': C sits beside ' + srcId + ', not ' + tgtId);
  ok(d(t, tgt) < d(t, src), pid + ': T sits beside ' + tgtId + ', not ' + srcId);
});

// M marks the qubit whose flux moves -- one per pair that declares it, and the
// coupler pair declares none, so exactly two. It is a ROLE, so it always
// coincides with that pair's C or T rather than being a third position.
ok(hero.querySelectorAll('.cm-role-m').length === 2,
   'an M only where moving_qubit is recorded, got '
   + hero.querySelectorAll('.cm-role-m').length);
ok(!!markerAt('m', 'qA2-qA1', 'qA2'),
   'moving_qubit="control" on qA2-qA1 puts M at qA2 (its control)');
ok(!!markerAt('m', 'qA1-qA3', 'qA3'),
   'moving_qubit="target" on qA1-qA3 puts M at qA3 (its target)');
ok(!markerAt('m', 'coupler_qA3_qA4', 'qA3')
   && !markerAt('m', 'coupler_qA3_qA4', 'qA4'),
   'a pair that records no moving_qubit gets no M invented for it');

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('chip_status_edge_orientation_selfcheck: all checks passed');
process.exit(0);
