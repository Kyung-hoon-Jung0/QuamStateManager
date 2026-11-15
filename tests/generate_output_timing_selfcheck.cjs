// Behavioral check: the Review row "Output timing" states the build's RULE
// (no predicted numbers), and the build result lists what the build wrote
// (result.output_timing, read from the built chip by core/output_timing.py).
//
//   rule: the same sentence for every spec, a shared feedline and a CR port included
//   result: one kit table per MW / flux port; a typed time of flight is named once
//   the shift offer's list lines use the escaped bullet
//
// Run: node tests/generate_output_timing_selfcheck.cjs   (needs jsdom)
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

let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

async function world() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  await new Promise(function (r) { win.setTimeout(r, 0); });
  const G = win.QuamGen;
  G.state.spec.instruments.controllers = [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }];
  return { T: G._test, state: G.state, doc: win.document };
}

function spec(qubits, fluxFem) {
  // qubits: [[id, xyBand, roBand], ...]; every line pinned
  const lines = [], qp = {}, rp = {};
  let port = 1;
  qubits.forEach(function (q) {
    lines.push({ element: q[0], line: 'drive', channel: { con: 1, slot: 1, out_port: port++ } });
    lines.push({ element: q[0], line: 'resonator', channel: { con: 1, slot: 1, out_port: 8, in_port: 1 } });
    lines.push({ element: q[0], line: 'flux', channel: { con: 1, slot: fluxFem || 2, out_port: port } });
    qp[q[0]] = { band: q[1] };
    rp[q[0]] = { band: q[2] };
  });
  return { lines: lines, populate: { qubit: qp, resonator: rp } };
}

(async function main() {
  const w = await world();
  const T = w.T;
  const txt = function (sp) { return T.outputTimingReviewText(sp); };

  const RULE = 'The build aligns outputs in time: when MW band 2 and bands 1 / 3 are both in use, ' +
    'band 1 / 3 outputs get 20 ns and every flux port the band 2 value; otherwise nothing ' +
    'is added. The values written are listed after the build.';
  let t = txt(spec([['q1', 2, 2], ['q2', 2, 2]]));
  ok(t === RULE, '[rule] one class: ' + t);
  // two resonators typed band 2 and band 3 on one feedline port: no per-line numbers
  const shared = spec([['q1', 1, 2], ['q2', 1, 3]]);
  t = txt(shared);
  ok(t === RULE && !/\+20 ns on/.test(t), '[rule] shared feedline states no shift: ' + t);
  // a dedicated CR port in band 1 beside band 2 xy: never "All MW outputs are in band 2"
  const cr = spec([['q1', 2, 2], ['q2', 2, 2]]);
  cr.lines.push({ element: 'q1-q2', line: 'cross_resonance', channel: { con: 1, slot: 1, out_port: 5 } });
  t = txt(cr);
  ok(t === RULE, '[rule] dedicated CR port: ' + t);

  // the build result table, from the server's summary
  const tbl = T.outputTimingResultTable({
    mw: [{ port: 'a', label: 'con1 / slot 1 / port 2', band: 2, delay: 0 },
         { port: 'b', label: 'con1 / slot 1 / port 5', band: 1, delay: 20 }],
    flux: [{ port: 'c', label: 'con1 / slot 2 / port 1', delay: 161, of: 'q1' }],
    tof: [{ qubit: 'q1', shift: 20, value: 300, moved: false }] });
  const rows = tbl.querySelectorAll('tr');
  ok(tbl.querySelector('table.ui-table') && rows.length === 4, '[result] header + 3 port rows');
  ok(/MW band 1con1 \/ slot 1 \/ port 520/.test(rows[2].textContent), '[result] CR port 20: ' + rows[2].textContent);
  ok(/Time of flight left as you set it on q1; its readout output is delayed by 20 ns: measure time of flight again\./
     .test(tbl.textContent), '[result] a time of flight the user set is named');
  // grouped: one line for the moved defaults, one for the values the user set
  const grp = T.outputTimingResultTable({ mw: [{ port: 'b', label: 'p8', band: 3, delay: 20 }], flux: [],
    tof: [{ qubit: 'q1', shift: 20, value: 48, moved: true }, { qubit: 'q2', shift: 20, value: 300, moved: false },
          { qubit: 'q3', shift: 20, value: 28, moved: false }, { qubit: 'q4', shift: 20, value: 48, moved: true },
          { qubit: 'q5', shift: 20, value: 48, moved: true }] });
  const notes = Array.from(grp.querySelectorAll('p.ui-note')).map(function (p) { return p.textContent; });
  ok(notes.length === 2 &&
     notes[0] === 'Time of flight moved from 28 to 48 ns with their readout output: q1, q4, q5.' &&
     notes[1] === 'Time of flight left as you set it on q2, q3; their readout output is delayed by 20 ns: ' +
       'measure time of flight again.', '[result] grouped lines: ' + JSON.stringify(notes));
  // a Re-generate names the stored time of flight; two different old values never share a line
  const fr = T.outputTimingResultTable({ mw: [{ port: 'b', label: 'p8', band: 3, delay: 0 }], flux: [],
    tof: [{ qubit: 'q1', shift: 0, value: 32, moved: true, from: 52 },
          { qubit: 'q2', shift: 0, value: 32, moved: true, from: 12 }] });
  const frn = Array.from(fr.querySelectorAll('p.ui-note')).map(function (p) { return p.textContent; });
  ok(frn.length === 2 && frn[0] === 'Time of flight moved from 52 to 32 ns with its readout output: q1.' &&
     frn[1] === 'Time of flight moved from 12 to 32 ns with its readout output: q2.',
     '[result] moved from the stored value: ' + JSON.stringify(frn));
  ok(T.outputTimingResultTable(null) === null, '[result] nothing to list');
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [],
    warnings: ['time of flight left as you set it on q1; its readout output is delayed by 20 ns: measure time of flight again.'],
    output_timing: { mw: [{ port: 'b', label: 'p5', band: 1, delay: 20 }], flux: [],
                     tof: [{ qubit: 'q1', shift: 20, value: 300, moved: false }] } } }, '/o');
  const res = w.doc.getElementById('gen-build-result');
  ok(res.querySelector('table.ui-table') &&
     res.textContent.split('left as you set it').length === 2, '[result] shown once in the result panel');

  w.state.mode = 'regenerate';
  t = txt(spec([['q1', 2, 3]]));
  ok(/Stored port delays and times of flight are kept/.test(t), '[regenerate] stored values kept: ' + t);

  // [regenerate offer] both stored-delay offers are one question step
  T.showBuildConfirm({
    error: 'q', confirm_kind: 'coupler_delay',
    coupler_delay: [{ pair: 'q1-2', pairs: ['q1-2'], port: 'p', port_label: 'con1 / slot 3 / port 9', z_delay: 61 }],
    mw_shift: { ports: [{ port: 'x', port_label: 'con1 / slot 1 / port 2', band: 1, old: 0, new: 20 }],
                flux: [{ qubit: 'q2', port: 'z', port_label: 'con1 / slot 3 / port 2', old: 63, new: 83 }],
                tof: [], spacing: [{ qubit: 'q2', xy_shift: 20, readout_shift: 0 }] } }, '/o');
  const box = w.doc.getElementById('gen-build-result');
  ok(box && /delay 0 to 20 ns/.test(box.textContent) && /63 to 83 ns/.test(box.textContent),
     '[regenerate offer] the shift lists what changes: ' + (box && box.textContent));
  ok(box.querySelector('.gen-mw-shift-shift') && box.querySelector('.gen-coupler-delay-set'),
     '[regenerate offer] both questions in one step');
  box.querySelector('.gen-mw-shift-shift').click();
  ok(w.state.regenMwShiftAck === 'shift' && box.querySelector('.gen-mw-shift-keep').disabled &&
     w.state.regenCouplerDelayAck == null && !box.querySelector('.gen-coupler-delay-set').disabled,
     '[regenerate offer] one answer waits for the other');
  ok(/• MW output/.test(box.textContent), '[regenerate offer] bulleted lines');
  // [regenerate offer] each question is its own block: question, why, its own buttons
  T.showBuildConfirm({
    error: 'run-on', confirm_kind: 'coupler_delay',
    questions: [{ kind: 'coupler_delay', question: 'QC?', why: 'WC.' },
                { kind: 'mw_shift', question: 'QS?', why: 'WS.' }],
    coupler_delay: [{ pair: 'q1-2', pairs: ['q1-2'], port: 'p', port_label: 'c9', z_delay: 61 }],
    mw_shift: { ports: [{ port: 'x', port_label: 'c2', band: 1, old: 0, new: 20 }],
                flux: [], tof: [], spacing: [] } }, '/o');
  const blocks = box.querySelectorAll('[data-regen-question]');
  ok(blocks.length === 2, '[F4] two question blocks: ' + blocks.length);
  const cb = box.querySelector('[data-regen-question="coupler_delay"]');
  const sb = box.querySelector('[data-regen-question="mw_shift"]');
  ok(cb && sb && cb.querySelector('.ui-callout-title').textContent === 'QC?' &&
     sb.querySelector('.ui-callout-title').textContent === 'QS?',
     '[F4] each block carries its own question');
  ok(cb && sb && /WC\./.test(cb.textContent) && !/WS\./.test(cb.textContent) && /WS\./.test(sb.textContent),
     '[F4] each block carries its own explanation line');
  ok(cb && sb && cb.querySelector('.gen-coupler-delay-set') && !cb.querySelector('.gen-mw-shift-shift') &&
     sb.querySelector('.gen-mw-shift-shift') && !sb.querySelector('.gen-coupler-delay-set'),
     '[F4] each block holds only its own answer buttons');
  ok(!/run-on/.test(box.textContent) && /Two questions before the build/.test(box.textContent),
     '[F4] the run-on paragraph is replaced by a short header: ' + box.firstChild.textContent);
  T.showBuildConfirm({
    error: 'run-on', confirm_kind: 'mw_shift',
    questions: [{ kind: 'mw_shift', question: 'QS?', why: 'WS.' }],
    mw_shift: { ports: [{ port: 'x', port_label: 'c2', band: 1, old: 0, new: 20 }],
                flux: [], tof: [], spacing: [] } }, '/o');
  ok(box.querySelectorAll('[data-regen-question]').length === 1 &&
     box.querySelector('[data-regen-question="mw_shift"]') &&
     /One question before the build/.test(box.textContent),
     '[F4] one question asked: one block, no empty second');
  // [F8] the moved line starts with a capital
  const cap = T.outputTimingResultTable({ mw: [{ port: 'b', label: 'p8', band: 3, delay: 0 }], flux: [],
    tof: [{ qubit: 'q1', shift: 0, value: 52, moved: true, from: 32 }] });
  ok(/^Time of flight/.test(cap.querySelector('p.ui-note').textContent), '[F8] note starts with a capital');
  const gridNote = 'Time of flight 34 ns rounded up to 36 ns (the controller takes multiples of 4 ns, ' +
    'at least 28 ns): acquisition starts 2 ns after the return; measure time of flight again.';
  const roundedTable = T.outputTimingResultTable({ mw: [{ label: 'p8', band: 3, delay: 20 }],
    tof: [{ qubit: 'q1', shift: 6, value: 36, from: 28, moved: true, grid_note: gridNote }] });
  ok(roundedTable.textContent.includes(gridNote), '[grid] result states original sum and reason');
  T.showBuildConfirm({ error: 'q', confirm_kind: 'mw_shift',
    mw_shift: { ports: [], flux: [], spacing: [],
      tof: [{ qubit: 'q1', old: 26, new: 48, grid_note: gridNote }] } }, '/o');
  ok(box.textContent.includes(gridNote), '[grid] offer states original sum and reason');
  // a value the user set off the grid on an undelayed readout output: only the grid is named
  const keptNote = 'Time of flight 46 ns is kept as you set it, but the controller takes multiples ' +
    'of 4 ns, at least 28 ns (nearest allowed 48 ns).';
  const gridOnly = T.outputTimingResultTable({ mw: [{ label: 'p8', band: 2, delay: 0 }],
    tof: [{ qubit: 'q1', shift: 0, value: 46, moved: false, grid_only: true, grid_note: keptNote },
          { qubit: 'q2', shift: 0, value: 46, moved: false, grid_only: true, grid_note: keptNote }] });
  const gon = Array.from(gridOnly.querySelectorAll('p.ui-note')).map(function (p) { return p.textContent; });
  ok(gon.length === 1 && gon[0] === 'q1, q2: ' + keptNote && !/delayed by 0 ns/.test(gridOnly.textContent),
     '[grid] an undelayed typed value names only the grid: ' + JSON.stringify(gon));
  // two typed values with different grid sentences: one line each, never the first one's value
  const kept50 = 'Time of flight 50 ns is kept as you set it, but the controller takes multiples ' +
    'of 4 ns, at least 28 ns (nearest allowed 52 ns).';
  const twoKept = T.outputTimingResultTable({ mw: [{ label: 'p8', band: 2, delay: 0 }],
    tof: [{ qubit: 'q1', shift: 0, value: 46, moved: false, grid_only: true, grid_note: keptNote },
          { qubit: 'q2', shift: 0, value: 50, moved: false, grid_only: true, grid_note: kept50 }] });
  const two = Array.from(twoKept.querySelectorAll('p.ui-note')).map(function (p) { return p.textContent; });
  ok(two.length === 2 && two[0] === 'q1: ' + keptNote && two[1] === 'q2: ' + kept50,
     '[grid] two typed values keep their own grid sentences: ' + JSON.stringify(two));
  // one stored value the rule cannot place: the offer still lists the rest and names that one
  const unplaced = 'Time of flight of q2 is not moved: -50 + 20 ns gives -30 ns, and time of flight ' +
    'must be a finite non-negative number; measure time of flight again.';
  T.showBuildConfirm({ error: 'q', confirm_kind: 'mw_shift',
    mw_shift: { ports: [{ port: 'x', port_label: 'c8', band: 3, old: 0, new: 20 }], flux: [], spacing: [],
      tof: [{ qubit: 'q1', old: 28, new: 48 }], tof_unplaced: [{ qubit: 'q2', note: unplaced }] } }, '/o');
  ok(box.textContent.includes(unplaced) && /MW output c8/.test(box.textContent) &&
     /Time of flight of q1: 28 to 48 ns\. Re-check it on the chip\./.test(box.textContent),
     '[grid] offer keeps the other lines and names the unplaced one');
  // after Apply the result review names a stored value SM did not place, with the server's sentence
  const follows = 'Time of flight of q2 is a reference to qubits/q1/resonator/time_of_flight; it ' +
    'follows that value (now 48 ns).';
  const notNumber = "Time of flight of q3 is not moved: the stored value 'abc' is not a number SM " +
    'can place; measure time of flight again.';
  const review = T.outputTimingResultTable({ mw: [{ label: 'p8', band: 3, delay: 20 }], flux: [],
    tof: [{ qubit: 'q1', shift: 20, value: 48, moved: true, from: 28 }],
    tof_unplaced: [{ qubit: 'q2', note: follows }, { qubit: 'q3', note: notNumber }] });
  const rvn = Array.from(review.querySelectorAll('p.ui-note')).map(function (p) { return p.textContent; });
  ok(rvn.indexOf(follows) >= 0 && rvn.indexOf(notNumber) >= 0,
     '[grid] result review names every unplaced value: ' + JSON.stringify(rvn));
  console.log(fails ? fails + ' failure(s)' : 'OK (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();
