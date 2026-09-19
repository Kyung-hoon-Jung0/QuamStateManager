// docs/202 — a class substitution is named in the build panel.
//
// The customer's KRS_5Q chip carries the lab's OWN readout pulse classes
// (ComplexWeightsReadoutPulse / GefWeightsReadoutPulse). A re-generate rebuilds
// them as the stock SquareReadoutPulse, because the build spec has no slot for
// a per-pulse class — so every field only their classes declare drops out. The
// DROP was already reported as `schema_dropped`; the substitution that caused
// it was reported nowhere, and it is the only part naming what to do about it.
//
// This EXECUTES the shipped renderer rather than grepping it — a source-only
// pin in this project has twice survived an `if (false)`.
//
//  C1  the chip appears, counted, only when there IS a substitution
//  C2  one class swap at five qubits reads as ONE line, not five
//  C3  the line names both classes and how many places
//  C4  two distinct swaps are two lines
//  C5  the capped list is honest: the chip counts the TOTAL, not the page
//  C6  an ordinary rebuild says nothing at all
//
// Run: node tests/generate_classchange_selfcheck.cjs   (needs jsdom)
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
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
// Bridge every global the code reads BARE (the standing rule since docs/78).
win.NumberInput = {
  fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} },
  format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
};
win.armPlainResize = function () {};
win.renderInstrumentWiring = function () {};
win.WiringGrid = null;
win.fetch = function () { return new win.Promise(function () {}); };
new win.Function(GEN_JS).call(win);

const T = win.QuamGen._test;
const doc = win.document;

const LAB = 'quam_config.complex_weights_pulse.ComplexWeightsReadoutPulse';
const LAB2 = 'quam_config.gef_weights_pulse.GefWeightsReadoutPulse';
const STOCK = 'quam.components.pulses.SquareReadoutPulse';

function swaps(op, oldCls, qs) {
  return qs.map(function (q) {
    return { path: 'qubits.' + q + '.resonator.operations.' + op,
             old: oldCls, new: STOCK };
  });
}

// The panel's ok-branch needs a `result`; everything else here is the merge.
function render(merge) {
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
                      merge: merge }, 'D:\out');
}

function chipText() {
  const el = doc.getElementById('gen-build-result');
  const hit = Array.prototype.filter.call(
    el.querySelectorAll('.gen-merge-stat'),
    function (s) { return /class substitution/.test(s.textContent); });
  return hit.length ? hit[0].textContent : null;
}

function classLines() {
  const el = doc.getElementById('gen-build-result');
  return Array.prototype.map.call(el.querySelectorAll('.gen-merge-detail'),
    function (d) { return d.textContent; })
    .filter(function (t) { return t.indexOf('rebuilt as') === 0; });
}

const BASE = { carried: 100, grafted: 0, kept_new_pointer: 0, kept_new_only: 0,
               graft_subtrees: [], superseded: 0, residual_lost: [],
               dangling_grafts: [], pruned_ops: 0, schema_dropped: 0,
               populate_protected: 0, populate_conflicts: [] };

function merge(over) { return Object.assign({}, BASE, over); }

// ── C1/C2/C3: the real case — one swap, five qubits ─────────────────────────
const five = swaps('readout', LAB, ['q1', 'q2', 'q3', 'q4', 'q5']);
render(merge({ class_changed: 5, class_changed_paths: five, class_changed_total: 5,
               schema_dropped: 15 }));

ok(chipText() !== null, 'C1: the chip renders when a class was substituted');
ok(/\b5 class substitutions\b/.test(chipText() || ''),
  'C1: it counts the places — got ' + JSON.stringify(chipText()));

let lines = classLines();
ok(lines.length === 1,
  'C2: one swap at five qubits is ONE line, not five — got ' + lines.length);
ok(lines[0].indexOf(STOCK) >= 0 && lines[0].indexOf(LAB) >= 0,
  'C3: the line names BOTH classes — got ' + JSON.stringify(lines[0]));
ok(/5 places/.test(lines[0]),
  'C3: and how many places — got ' + JSON.stringify(lines[0]));
ok(lines[0].indexOf('qubits.q1.resonator.operations.readout') >= 0,
  'C3: with a real path as evidence');

// ── C4: two distinct swaps are two lines ────────────────────────────────────
const both = five.concat(swaps('readout_GEF', LAB2, ['q1', 'q2', 'q3', 'q4', 'q5']));
render(merge({ class_changed: 10, class_changed_paths: both,
               class_changed_total: 10, schema_dropped: 50 }));
lines = classLines();
ok(lines.length === 2, 'C4: two distinct substitutions are two lines — got ' + lines.length);
ok(lines.some(function (l) { return l.indexOf(LAB) >= 0; })
   && lines.some(function (l) { return l.indexOf(LAB2) >= 0; }),
  'C4: each lab class is named');
ok(/\b10 class substitutions\b/.test(chipText() || ''),
  'C4: the chip counts all ten places');

// ── C5: the server CAPS the path list at 80 — the chip must count the TOTAL,
//        or a big rebuild understates itself the way docs/118 did.
render(merge({ class_changed: 400, class_changed_paths: five,
               class_changed_total: 400 }));
ok(/\b400 class substitutions\b/.test(chipText() || ''),
  'C5: the chip reports the TOTAL, not the page — got ' + JSON.stringify(chipText()));

// ── C6: the ordinary rebuild is silent ──────────────────────────────────────
render(merge({ class_changed: 0, class_changed_paths: [], class_changed_total: 0 }));
ok(chipText() === null, 'C6: no chip when nothing was substituted');
ok(classLines().length === 0, 'C6: and no lines');

// An OLD build result (no class_changed key at all) must render as before.
render(merge({}));
ok(chipText() === null && classLines().length === 0,
  'C6: a result predating this field renders unchanged');

// ── docs/202 §15: classes the merge KEPT are named, grouped, in green ────────
{
  const kept = ['q1', 'q2', 'q3'].map(function (q) {
    return { path: 'qubits.' + q + '.resonator.operations.readout', cls: LAB };
  }).concat([{ path: 'qubits.q1.resonator.operations.readout_GEF', cls: LAB2 }]);
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE, { class_kept: 4, class_kept_paths: kept,
                                     class_kept_total: 4 }) }, 'D:\out');
  const el = doc.getElementById('gen-build-result');
  const chip = Array.prototype.filter.call(el.querySelectorAll('.gen-merge-stat'),
    function (s) { return /kept as your class/.test(s.textContent); })[0];
  let n = 0; function ok2(c, m) { asserts++; n++; if (!c) { console.error('FAIL: ' + m); fails++; } }
  ok2(chip && /\b4 kept as your class/.test(chip.textContent),
    'K1: the kept chip counts every place — got ' + (chip && chip.textContent));
  ok2(chip && chip.classList.contains('gen-merge-ok'), 'K1: and reads as good news, not a warning');
  const lines = Array.prototype.map.call(el.querySelectorAll('.gen-merge-kept'),
    function (d) { return d.textContent; });
  ok2(lines.length === 2, 'K2: one line per kept CLASS, not per place — got ' + lines.length);
  ok2(lines.some(function (l) { return l.indexOf(LAB) >= 0 && /3 places/.test(l); }),
    'K2: the class and its count — got ' + JSON.stringify(lines));
  // an ordinary build (nothing kept) says nothing
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE) }, 'D:\out');
  ok2(doc.querySelectorAll('.gen-merge-kept').length === 0 &&
      !/kept as your class/.test(doc.getElementById('gen-build-result').textContent),
    'K3: nothing kept, nothing said');

  // ── docs/202 §17: a declared port nothing used, carried ───────────────────
  const P8 = 'ports.mw_outputs.con1.3.8';
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE, { ports_carried: [P8], ports_carried_total: 1 }) },
    'D:\out');
  const pel = doc.getElementById('gen-build-result');
  const pchip = pel.querySelector('.gen-merge-ports');
  ok2(pchip && /^1 unused port carried$/.test(pchip.textContent.trim()),
    'P1: the chip says what was carried, singular — got ' + (pchip && pchip.textContent));
  ok2(pchip && pchip.classList.contains('gen-merge-ok'), 'P1: and reads as good news');
  const plines = Array.prototype.map.call(pel.querySelectorAll('.gen-merge-port-line'),
    function (d) { return d.textContent; });
  ok2(plines.length === 1 && plines[0].indexOf(P8) >= 0,
    'P2: the port is named by its path — got ' + JSON.stringify(plines));
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE) }, 'D:\out');
  ok2(!doc.querySelector('.gen-merge-ports') && !doc.querySelector('.gen-merge-port-line'),
    'P3: a result with no carried port (or predating the field) says nothing');
  console.log(fails ? ('FAILED ' + fails + ' of ' + asserts)
                    : ('ok (' + asserts + ' assertions)'));
  process.exitCode = fails ? 1 : 0;
}
