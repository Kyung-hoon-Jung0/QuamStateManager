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
  ok(/time of flight left as typed \(300 ns\); its readout output is delayed by 20 ns: measure time of flight again\./
     .test(tbl.textContent), '[result] typed time of flight named');
  ok(T.outputTimingResultTable(null) === null, '[result] nothing to list');
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [],
    warnings: ['q1: time of flight left as typed (300 ns); its readout output is delayed by 20 ns: measure time of flight again.'],
    output_timing: { mw: [{ port: 'b', label: 'p5', band: 1, delay: 20 }], flux: [],
                     tof: [{ qubit: 'q1', shift: 20, value: 300, moved: false }] } } }, '/o');
  const res = w.doc.getElementById('gen-build-result');
  ok(res.querySelector('table.ui-table') &&
     res.textContent.split('left as typed').length === 2, '[result] shown once in the result panel');

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
  console.log(fails ? fails + ' failure(s)' : 'OK (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();
