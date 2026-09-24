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
// The ONE Δ implementation (docs/76), taken from the shipped app.js -- the
// page loads it before generate.js (QA review of F17).
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const VD_AT = APP_JS.indexOf('window.ValueDelta = (function () {');
new win.Function(APP_JS.slice(VD_AT, APP_JS.indexOf('\n})();', VD_AT) + 6)).call(win);
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

  // ── QA regenerate-r2-17: network keys the wizard never shows, carried ─────
  const NK = ['qmm_class', 'qmm_settings', 'quantum_computer_backend', 'use_custom_qmm'];
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE, { network_carried: NK }) }, 'D:\out');
  const nel = doc.getElementById('gen-build-result');
  const nchip = nel.querySelector('.gen-merge-net');
  ok2(nchip && /^4 network settings carried$/.test(nchip.textContent.trim()),
    'N1: the chip counts the carried network keys — got ' + (nchip && nchip.textContent));
  const nline = nel.querySelector('.gen-merge-net-line');
  ok2(nline && NK.every(function (k) { return nline.textContent.indexOf(k) >= 0; }),
    'N2: the carried keys are named — got ' + (nline && nline.textContent));
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: Object.assign({}, BASE) }, 'D:\out');
  ok2(!doc.querySelector('.gen-merge-net') && !doc.querySelector('.gen-merge-net-line'),
    'N3: nothing carried (or a result predating the field) says nothing');

  // ── QA regenerate-r2-28: groups measured over the FULL list ───────────────
  const OLDD = 'quam.components.pulses.DragCosinePulse';
  const NEWD = 'quam_builder.architecture.superconducting.components.pulses.DragCosinePulse';
  const MWC = 'quam.components.channels.MWChannel', XYD = 'quam_builder.XYDriveMW';
  const page = [];
  for (let i = 1; i <= 40; i++) {
    ['x180', 'x90'].forEach(function (op) {
      page.push({ path: 'qubits.q' + i + '.xy.operations.' + op, old: OLDD, new: NEWD });
    });
  }
  const bigGroups = [
    { old: OLDD, new: NEWD, count: 126, dropped: 0,
      paths: page.slice(0, 3).map(function (c) { return c.path; }) },
    { old: MWC, new: XYD, count: 8, dropped: 0,
      paths: ['twpas.twpaA.pump', 'twpas.twpaA.pump_', 'twpas.twpaB.pump'] }];
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ class_changed: 134, class_changed_paths: page,
                   class_changed_total: 134, class_changed_groups: bigGroups,
                   class_changed_lossy_total: 0 }) }, 'D:\out');
  let gl = classLines();
  ok2(gl.length === 2, 'G1: two groups, the one past the page included — got ' +
    JSON.stringify(gl));
  ok2(/— 126 places, no fields lost/.test(gl[0] || '') && /— 8 places, no fields lost/.test(gl[1] || ''),
    'G1: each group counts its TRUE total — got ' + JSON.stringify(gl));
  ok2(chipText() === null,
    'G2: a lossless re-type is not a "class substitution" — got ' + JSON.stringify(chipText()));
  const rt = doc.querySelector('#gen-build-result .gen-merge-retyped');
  ok2(rt && /^134 re-typed, no fields lost$/.test(rt.textContent.trim()) &&
      !rt.classList.contains('gen-merge-warn') && !/is gone/.test(rt.title),
    'G2: it reads as a neutral re-type — got ' + (rt && rt.textContent + ' / ' + rt.className));
  ok2(!Array.prototype.some.call(doc.querySelectorAll('#gen-build-result .gen-merge-detail'),
      function (d) { return /^rebuilt as/.test(d.textContent) && d.classList.contains('gen-merge-warn'); }),
    'G2: and no lossless line is amber');
  // G3: a group whose objects dropped fields stays an amber substitution
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ class_changed: 139, class_changed_paths: page, class_changed_total: 139,
                   schema_dropped: 15,
                   class_changed_groups: bigGroups.concat([{ old: LAB, new: STOCK,
                     count: 5, dropped: 15, paths: five.slice(0, 3).map(function (c) { return c.path; }) }]),
                   class_changed_lossy_total: 5 }) }, 'D:\out');
  ok2(/^5 class substitutions$/.test((chipText() || '').trim()),
    'G3: only the lossy places are counted amber — got ' + JSON.stringify(chipText()));
  gl = classLines();
  const lossyLine = Array.prototype.filter.call(
    doc.querySelectorAll('#gen-build-result .gen-merge-detail'),
    function (d) { return d.textContent.indexOf(LAB) >= 0; })[0];
  ok2(lossyLine && lossyLine.classList.contains('gen-merge-warn') &&
      /5 places, 15 fields dropped/.test(lossyLine.textContent),
    'G3: its line says what dropped, in amber — got ' + (lossyLine && lossyLine.textContent));
  // G4: an older result (no groups) says the listed page is not the total
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ class_changed: 134, class_changed_paths: page,
                   class_changed_total: 134 }) }, 'D:\out');
  ok2(/first 80 of 134/.test(doc.getElementById('gen-build-result').textContent),
    'G4: the fallback names the cap');
  // G5: more groups than lines -> "+K more"
  const many = [];
  for (let i = 0; i < 9; i++) many.push({ old: 'a.C' + i, new: 'b.C' + i, count: 1, dropped: 0, paths: ['x' + i] });
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ class_changed: 9, class_changed_total: 9, class_changed_paths: [],
                   class_changed_groups: many }) }, 'D:\out');
  ok2(/\+ 3 more class changes not shown/.test(doc.getElementById('gen-build-result').textContent),
    'G5: groups past the six lines are counted');

  // ── QA F17: every chip explained, losses grouped, edits itemised ─────────
  const q5 = [];
  for (let i = 0; i < 20; i++) q5.push('qubits.q5.leaf' + i);
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ carried: 737, grafted: 1267,
      residual_lost: q5.concat(['ports.analog_outputs.con1.5.3.controller_id']),
      residual_lost_total: 316,
      residual_lost_groups: [
        { kind: 'qubit', owner: 'q5', present: false, n: 142, paths: q5 },
        { kind: 'pair', owner: 'q4-5', present: false, n: 31,
          paths: ['qubit_pairs.q4-5.macros.cz.phase_shift_control'] },
        { kind: 'pair', owner: 'q2-3', present: false, n: 2, reversed_as: 'q3-2',
          paths: ['qubit_pairs.q2-3.a', 'qubit_pairs.q2-3.b'] },
        { kind: 'port', owner: 'analog_outputs con1/5/3', present: false, n: 7,
          paths: ['ports.analog_outputs.con1.5.3.controller_id'] }],
      pruned_ops: 1, pruned_ops_paths: ['qubits.q1.z.operations.cz_old'],
      populate_protected: 2, populate_cells: 1,
      populate_protected_detail: [
        { path: 'qubits.q1.xy.operations.x180.amplitude', old: 0.3, new: 0.25, derived_from: null },
        { path: 'qubits.q1.xy.operations.x90.amplitude', old: 0.15169, new: 0.125,
          derived_from: 'x180' }] }) }, 'D:\out');
  const fel = doc.getElementById('gen-build-result');
  const stats = Array.prototype.slice.call(fel.querySelectorAll('.gen-merge-stat'));
  const statBy = function (re) { return stats.filter(function (x) { return re.test(x.textContent); })[0]; };
  ok2(statBy(/737 carried/) && statBy(/737 carried/).title.length > 20,
    'F17: the carried chip explains itself');
  ok2(statBy(/1267 grafted/) && statBy(/1267 grafted/).title.length > 20,
    'F17: the grafted chip explains itself');
  const pop = statBy(/populate edit/);
  ok2(pop && /^1 populate edit applied \(2 values\)$/.test(pop.textContent.trim()),
    'F17: one edited cell reads as ONE edit, with its value count — got ' +
    (pop && pop.textContent));
  const popLines = Array.prototype.map.call(fel.querySelectorAll('.gen-merge-pop-line'),
    function (d) { return d.textContent; });
  const x90d = win.ValueDelta.compute(0.15169, 0.125).text;
  ok2(popLines.length === 2 &&
      popLines[1].indexOf('x90.amplitude: 0.15169 → 0.125 ' + x90d) >= 0 &&
      /\(re-derived from the x180 seed/.test(popLines[1]),
    'F17: the re-derived x90 is named, old → new — got ' + JSON.stringify(popLines));
  // QA review of F17: the Δ is ValueDelta's own chip, not a second renderer
  const popEls = fel.querySelectorAll('.gen-merge-pop-line');
  const chip0 = popEls[0] && popEls[0].querySelector('.val-delta');
  ok2(chip0 && chip0.textContent.indexOf(win.ValueDelta.compute(0.3, 0.25).text) === 0 &&
      chip0.classList.contains('delta-down'),
    'F17: each edited value carries the ValueDelta chip — got ' +
    (popEls[0] && popEls[0].innerHTML));
  ok2(/cleaned 1 redundant legacy op .*: qubits\.q1\.z\.operations\.cz_old/.test(fel.textContent),
    'F17: the cleaned op is named');
  const groupsEl = Array.prototype.map.call(fel.querySelectorAll('.gen-merge-lost-group > summary'),
    function (d) { return d.textContent; });
  ok2(groupsEl[0] === 'qubit q5 — removed: 142 values',
    'F17: a removed qubit is ONE line — got ' + JSON.stringify(groupsEl));
  ok2(groupsEl[1] === 'pair q4-5 — removed: 31 values', 'F17: so is a removed pair');
  ok2(groupsEl[2] === 'pair q2-3 — rebuilt reversed as q3-2: 2 values',
    'F17: a reversed pair is not called removed — got ' + groupsEl[2]);
  ok2(/^port analog_outputs con1\/5\/3 — not in the rebuild/.test(groupsEl[3] || ''),
    'F17: a port reads as a port — got ' + groupsEl[3]);
  const q5g = fel.querySelector('.gen-merge-lost-group');
  ok2(q5g.querySelectorAll('.gen-merge-lost-line').length === 20 &&
      /shown 20 of 142/.test(q5g.textContent),
    'F17: a truncated group says so');
  // an OLDER result (no groups) keeps the flat list, and says where it stops
  const flat = [];
  for (let i = 0; i < 200; i++) flat.push('qubits.q5.v' + i);
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
    merge: merge({ residual_lost: flat, residual_lost_total: 316 }) }, 'D:\out');
  const fel2 = doc.getElementById('gen-build-result');
  ok2(fel2.querySelectorAll('.gen-merge-lost-group').length === 0 &&
      fel2.querySelectorAll('.gen-merge-lost-line').length === 80 &&
      /shown 80 of 316/.test(fel2.textContent),
    'F17: the flat fallback names its cap');
  ok2(/Generated 1 qubit and 0 pairs/.test(fel2.textContent),
    'F15: the headline is singular for one qubit');
  console.log(fails ? ('FAILED ' + fails + ' of ' + asserts)
                    : ('ok (' + asserts + ' assertions)'));
  process.exitCode = fails ? 1 : 0;
}
