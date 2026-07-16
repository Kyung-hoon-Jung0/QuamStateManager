/* jsdom behavioral check for the Generate-Config wizard's absolute-power
 * (dBm) input mode + hole-aware LO solver.
 *
 * Pins:
 *  A. solvePortFsp / ampForTarget — the lab's confirmed power policy:
 *     strongest pulse picks the FSP (int, prefer [0,10], floor 0, cap 18),
 *     amp = 10^((P-fsp)/20), amp band [0.01, 0.5], readout banks budget the
 *     worst-case coherent sum (n·amp ≤ 0.5 preferred). Reference example
 *     (user-provided): saturation −20 dBm → FSP 0, amp 0.1.
 *  B. solveLoWindow — ±400 MHz window + 5 MHz(+1 margin) demod hole for
 *     resonators + single-band coverage; legacy-midpoint fallback with a
 *     named code on infeasibility. The legacy solver put a lone resonator's
 *     LO exactly ON its RF (IF = 0 — unreadable); the new one must not.
 *  C. DOM integration — the Power-input toggle locks the amp unit to dBm
 *     and the FSP cells; committing a readout dBm re-allocates the whole
 *     multiplexed bank (shared FSP + equal amps); committing an xy dBm
 *     re-solves the qubit port FSP preserving the other pulse's power.
 *
 * Run:  node tests/generate_power_selfcheck.cjs   (driven by test_generate_power.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) {
  console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1);
});

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function close(a, b, tol, m) {
  if (!(Math.abs(a - b) <= (tol == null ? 1e-9 : tol))) {
    console.error('FAIL: ' + m + ' (got ' + a + ', want ' + b + ')'); fails++;
  }
}

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  return win;
}

function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function cell(win, group, rid, field) {
  return win.document.querySelector(
    '.gen-pop-in[data-group="' + group + '"][data-rid="' + rid +
    '"][data-field="' + field + '"]');
}

// ---------------------------------------------------------------------------
// A. Power split — pure functions
// ---------------------------------------------------------------------------
(function powerSplit() {
  const win = makeWorld();
  const T = win.QuamGen._test;

  // The user's reference example: saturation −20 dBm → FSP 0, amp 0.1.
  let fsp = T.solvePortFsp(-20, 1);
  ok(fsp === 0, 'A1: P=-20 dBm -> FSP 0 (got ' + fsp + ')');
  close(T.ampForTarget(-20, fsp), 0.1, 1e-12, 'A1: P=-20 dBm @ FSP 0 -> amp 0.1');

  // Loud pulse: FSP pushes past the [0,10] preference to keep amp ≤ 0.5.
  fsp = T.solvePortFsp(4, 1);
  ok(fsp === 11, 'A2: P=+4 dBm -> FSP 11 (ceil(4+6.02)) (got ' + fsp + ')');
  ok(T.ampForTarget(4, fsp) <= 0.5 + 1e-12, 'A2: amp stays <= 0.5');

  // Past the hardware max: FSP caps at 18, amp rises above 0.5.
  fsp = T.solvePortFsp(16, 1);
  ok(fsp === 18, 'A3: P=+16 dBm -> FSP 18 (hardware max) (got ' + fsp + ')');
  close(T.ampForTarget(16, 18), Math.pow(10, -2 / 20), 1e-12, 'A3: amp = 10^(-2/20)');

  // Unreachable: P > 18 dBm needs amp > 1 (callers warn + clamp).
  ok(T.ampForTarget(20, 18) > 1, 'A4: P=+20 dBm needs amp > 1 at FSP 18');

  // Quiet pulse: FSP floors at 0 (never below), amp goes small.
  fsp = T.solvePortFsp(-46.02, 1);
  ok(fsp === 0, 'A5: P=-46 dBm -> FSP floored at 0 (got ' + fsp + ')');
  ok(T.ampForTarget(-46.02, 0) < 0.01, 'A5: amp < 0.01 (warned, not re-floored)');

  // Bank budget: n tones push the FSP up so n·amp ≤ 0.5.
  fsp = T.solvePortFsp(-10, 8);
  ok(fsp === 15, 'A6: bank n=8, P=-10 -> FSP 15 (ceil(-10+24.08)) (got ' + fsp + ')');
  const amp8 = T.ampForTarget(-10, fsp);
  ok(8 * amp8 <= 0.5 + 1e-9, 'A6: 8·amp <= 0.5 budget (got ' + (8 * amp8) + ')');

  // Bank with a comfortable target: preference floor 0 dominates.
  fsp = T.solvePortFsp(-30, 6);
  ok(fsp === 0, 'A7: bank n=6, P=-30 -> FSP 0 (got ' + fsp + ')');
  ok(6 * T.ampForTarget(-30, 0) <= 0.5, 'A7: sum within budget at FSP 0');

  // Exact-integer boundary must not ceil one dB high: P = -6.0206 → need 0.
  fsp = T.solvePortFsp(-20 * Math.log10(2), 1);
  ok(fsp === 0, 'A8: P=-6.0206 (amp exactly 0.5 at FSP 0) -> FSP 0 (got ' + fsp + ')');

  // Round-trip losslessness: fsp + 20log10(amp) recovers the target.
  const P = -13.7;
  fsp = T.solvePortFsp(P, 1);
  close(fsp + 20 * Math.log10(T.ampForTarget(P, fsp)), P, 1e-9,
    'A9: dBm target recovers exactly from (fsp, amp)');
})();

// ---------------------------------------------------------------------------
// B. LO solver — pure function
// ---------------------------------------------------------------------------
(function loSolver() {
  const win = makeWorld();
  const T = win.QuamGen._test;
  const HOLE = 6e6;   // 5 MHz floor + 1 MHz margin

  // B1: lone resonator — the legacy midpoint put the LO ON the RF (IF=0,
  // unreadable). The solver must clear the hole while staying in-window.
  let r = T.solveLoWindow([{ rf: 7.46e9, needHole: true }]);
  ok(r.ok, 'B1: lone resonator solvable');
  ok(Math.abs(7.46e9 - r.lo) >= HOLE - 1, 'B1: LO clears the 5(+1) MHz hole (|IF|=' +
    Math.abs(7.46e9 - r.lo) / 1e6 + ' MHz)');
  ok(Math.abs(7.46e9 - r.lo) <= 0.4e9, 'B1: LO within the ±400 MHz window');

  // B2: lone xy drive — no hole; midpoint (= its RF) is fine.
  r = T.solveLoWindow([{ rf: 5.0e9, needHole: false }]);
  ok(r.ok && Math.abs(r.lo - 5.0e9) < 1e6, 'B2: xy drive may sit at IF≈0 (lo=' + r.lo + ')');

  // B3: resonator at the bank midpoint — the solver shifts off the hole and
  // keeps every member in-window.
  r = T.solveLoWindow([
    { rf: 7.0e9, needHole: true },
    { rf: 7.2e9, needHole: true },
    { rf: 7.4e9, needHole: true }]);
  ok(r.ok, 'B3: mid-bank resonator solvable');
  [7.0e9, 7.2e9, 7.4e9].forEach(function (rf) {
    ok(Math.abs(rf - r.lo) >= HOLE - 1, 'B3: |IF| of ' + rf + ' clears the hole');
    ok(Math.abs(rf - r.lo) <= 0.4e9 + 1, 'B3: |IF| of ' + rf + ' within window');
  });

  // B4: span too wide — legacy midpoint fallback + named code.
  r = T.solveLoWindow([
    { rf: 7.0e9, needHole: true }, { rf: 7.9e9, needHole: true }]);
  ok(!r.ok && r.code === 'span', 'B4: 0.9 GHz span -> code "span" (got ' + r.code + ')');
  close(r.lo, 7.45e9, 1, 'B4: fallback LO = midpoint');

  // B5: no single band covers 5.4 + 5.6 GHz? band 2 covers [4.5, 7.5) —
  // it DOES. The solver must then pick an LO ≥ 5.5 GHz so bandOf(lo)
  // actually lands in band 2 (bandOf's band-1-first precedence).
  r = T.solveLoWindow([
    { rf: 5.4e9, needHole: true }, { rf: 5.6e9, needHole: true }]);
  ok(r.ok, 'B5: 5.4+5.6 GHz solvable in band 2');
  ok(r.lo >= 5.5e9, 'B5: LO >= 5.5 GHz so bandOf picks band 2 (got ' + r.lo + ')');
  ok(Math.abs(5.4e9 - r.lo) <= 0.4e9 && Math.abs(5.6e9 - r.lo) <= 0.4e9,
    'B5: both members in-window');

  // B6: genuinely no covering band. Within a feasible 0.8 GHz span the
  // band-only regions are ≥1 GHz apart (span fires first), so no_band means
  // an RF outside every band: 10.4 GHz (band 3) + 10.6 GHz (outside all).
  r = T.solveLoWindow([
    { rf: 10.4e9, needHole: true }, { rf: 10.6e9, needHole: true }]);
  ok(!r.ok && r.code === 'no_band', 'B6: 10.4+10.6 GHz -> code "no_band" (got ' + r.code + ')');

  // B7: band-3-only group (7.3 + 7.9 GHz): LO must sit ≥ 7.5 GHz (bandOf
  // precedence) AND within both windows -> [7.5, 7.7].
  r = T.solveLoWindow([
    { rf: 7.3e9, needHole: true }, { rf: 7.9e9, needHole: true }]);
  ok(r.ok, 'B7: 7.3+7.9 GHz solvable');
  ok(r.lo >= 7.5e9 - 1 && r.lo <= 7.7e9 + 1, 'B7: LO in [7.5, 7.7] GHz (got ' + r.lo + ')');

  // B8: real-fleet shape (LabA bank A): 6 resonators, span ~0.63 GHz.
  const bank = [7.156e9, 7.221e9, 7.294e9, 7.366e9, 7.439e9, 7.516e9]
    .map(function (rf) { return { rf: rf, needHole: true }; });
  r = T.solveLoWindow(bank);
  ok(r.ok, 'B8: LabA-shaped bank solvable');
  bank.forEach(function (e) {
    const IF = Math.abs(e.rf - r.lo);
    ok(IF >= HOLE - 1 && IF <= 0.4e9 + 1, 'B8: ' + e.rf + ' IF=' + IF / 1e6 + ' MHz valid');
  });

  // B9: 1 MHz readability rounding sticks when feasible.
  ok(r.ok && Math.abs(r.lo % 1e6) < 1, 'B9: solved LO rounded to 1 MHz (lo=' + r.lo + ')');

  // B10: xy pair >0.8 GHz apart still reports span (documented v1 limit —
  // the wizard models one LO per coupled port pair).
  r = T.solveLoWindow([
    { rf: 4.0e9, needHole: false }, { rf: 5.0e9, needHole: false }]);
  ok(!r.ok && r.code === 'span', 'B10: wide xy pair -> span code');

  // B11: band_window — a band covers every RF but its LO range misses the IF
  // window (7.0 + 7.6 GHz: both in band 3, but the required LO window
  // [7.2, 7.4] GHz is where bandOf picks band 2). Distinct from no_band.
  r = T.solveLoWindow([
    { rf: 7.0e9, needHole: true }, { rf: 7.6e9, needHole: true }]);
  ok(!r.ok && r.code === 'band_window',
    'B11: covering-band-but-window-miss -> code "band_window" (got ' + r.code + ')');
  ok(r.band === 3, 'B11: names the covering band (3)');
})();

// ---------------------------------------------------------------------------
// C. DOM integration — toggle + bank/xy reallocation
// ---------------------------------------------------------------------------
function buildWizard(win) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  const chassis = win.document.getElementById('gen-chassis-count');
  setInput(win, chassis, '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'fixed_frequency';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  // Hand-craft the allocation (normally from /generate/allocate): all three
  // resonators multiplexed on port 1 (out) + port 1 (in), each qubit's xy on
  // its own port.
  const rr = [
    { con: 1, slot: 1, port: 1, io_type: 'output' },
    { con: 1, slot: 1, port: 1, io_type: 'input' }];
  G.state.allocation = {
    q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }], rr: rr },
    q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }], rr: rr },
    q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }], rr: rr }
  };
  G.goToStep(6);
  return G;
}

function powerModeSelect(win) {
  const sels = win.document.querySelectorAll('#gen-pop-units .gen-pop-powermode select');
  return sels.length ? sels[0] : null;
}

(function domToggle() {
  const win = makeWorld();
  const G = buildWizard(win);

  // Toggle exists, defaults to manual, and switching locks the amp unit.
  const pSel = powerModeSelect(win);
  ok(!!pSel, 'C1: Power input toggle rendered in the units row');
  ok(G.state.powerMode === 'manual', 'C1: default mode is manual');
  const fspCellBefore = cell(win, 'resonator', 'q1', 'full_scale_power_dbm');
  ok(fspCellBefore && !fspCellBefore.disabled, 'C1: FSP editable in manual mode');

  setInput(win, pSel, 'absolute');
  ok(G.state.powerMode === 'absolute', 'C2: toggle -> absolute');
  const ampSel = Array.prototype.filter.call(
    win.document.querySelectorAll('#gen-pop-units .gen-pop-unit select'),
    function (s) { return s.disabled; });
  ok(ampSel.length === 1 && ampSel[0].value === 'dBm',
    'C2: amp unit selector locked to dBm (found ' + ampSel.length + ')');
  const fspCell = cell(win, 'resonator', 'q1', 'full_scale_power_dbm');
  ok(fspCell && fspCell.disabled, 'C2: resonator FSP cell locked in absolute mode');
  const qFspCell = cell(win, 'qubit', 'q1', 'full_scale_power_dbm');
  ok(qFspCell && qFspCell.disabled, 'C2: qubit FSP cell locked in absolute mode');
  ok(win.localStorage.getItem('quam_gen_power_mode') === 'absolute',
    'C2: mode persisted to localStorage');

  // C3: readout bank edit — type −20 dBm into q1's readout amp; the whole
  // bank (q1..q3, same port) must get FSP 0... n=3 tones: ceil(−20 +
  // 20log10(6)) = ceil(−4.44) = −4 → floored to 0. amp = 10^(−20/20) = 0.1.
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '-20');
  const res = G.state.spec.populate.resonator;
  ['q1', 'q2', 'q3'].forEach(function (q) {
    ok(res[q] && res[q].full_scale_power_dbm === 0,
      'C3: bank FSP 0 on ' + q + ' (got ' + (res[q] && res[q].full_scale_power_dbm) + ')');
    close(res[q] && res[q].readout_amplitude, 0.1, 1e-9,
      'C3: bank amp 0.1 on ' + q);
  });
  // Achieved dBm shown back in the cells (lossless round-trip).
  close(parseFloat(cell(win, 'resonator', 'q2', 'readout_amplitude').value), -20, 1e-6,
    'C3: sibling cell displays the achieved -20 dBm');

  // C4: xy edit preserves the OTHER pulse's absolute power. First set
  // saturation to −20 dBm (user example: FSP 0, amp 0.1), then x180 to
  // −6 dBm (stronger → FSP re-solves to ceil(0.02)=1... wait: need =
  // −6+6.0206 = 0.0206 → ceil = 1). Saturation must stay at −20 dBm.
  setInput(win, cell(win, 'pulses', 'q1', 'saturation_amplitude'), '-20');
  let qb = G.state.spec.populate.qubit.q1;
  ok(qb.full_scale_power_dbm === 0, 'C4: saturation -20 -> qubit FSP 0 (user example)');
  close(G.state.spec.populate.pulses.q1.saturation_amplitude, 0.1, 1e-9,
    'C4: saturation amp 0.1 (user example)');

  setInput(win, cell(win, 'pulses', 'q1', 'x180_amplitude'), '-6');
  qb = G.state.spec.populate.qubit.q1;
  ok(qb.full_scale_power_dbm === 1, 'C4: x180 -6 dBm -> FSP 1 (got ' + qb.full_scale_power_dbm + ')');
  const pl = G.state.spec.populate.pulses.q1;
  close(1 + 20 * Math.log10(pl.x180_amplitude), -6, 1e-9, 'C4: x180 power preserved');
  close(1 + 20 * Math.log10(pl.saturation_amplitude), -20, 1e-9,
    'C4: saturation power PRESERVED across the FSP re-solve');
  // Displays show the recovered dBm targets.
  close(parseFloat(cell(win, 'pulses', 'q1', 'saturation_amplitude').value), -20, 1e-6,
    'C4: saturation cell displays -20 dBm after re-solve');
  close(parseFloat(cell(win, 'qubit', 'q1', 'full_scale_power_dbm').value), 1, 1e-9,
    'C4: qubit FSP cell displays the derived FSP');

  // C5: switching back to manual unlocks + keeps the derived representation.
  setInput(win, powerModeSelect(win), 'manual');
  ok(G.state.powerMode === 'manual', 'C5: toggle back to manual');
  const fspAfter = cell(win, 'resonator', 'q1', 'full_scale_power_dbm');
  ok(fspAfter && !fspAfter.disabled, 'C5: FSP editable again');
  ok(G.state.spec.populate.resonator.q1.full_scale_power_dbm === 0,
    'C5: derived FSP survives the mode switch');
})();

// C5b: warning surface — unreachable target (amp > 1), bank clipping (Σ > 1)
// and tiny-amp findings land in the conflicts panel, keyed per port.
(function warningSurface() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');

  // Unreachable single-pulse target: +25 dBm > 18 max → amp clamps at 1.
  setInput(win, cell(win, 'pulses', 'q1', 'x180_amplitude'), '25');
  const qb = G.state.spec.populate.qubit.q1;
  ok(qb.full_scale_power_dbm === 18, 'C5b: FSP capped at 18');
  ok(G.state.spec.populate.pulses.q1.x180_amplitude === 1,
    'C5b: amp clamped at 1 (stored, honest achieved value)');
  let panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('maximum output') >= 0,
    'C5b: at-max-reach warning rendered (got: ' + panel.slice(0, 140) + ')');
  ok(panel.indexOf('power conflicts') >= 0,
    'C5b: panel heading names power conflicts');

  // Bank clipping: 3 tones at +9 dBm each → fsp 18 (ceil(9+15.56)=25→18),
  // amp 0.355 each, Σ=1.06 > 1 → clip warning.
  setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '9');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('CLIP') >= 0, 'C5b: bank Σ>1 clip warning rendered');

  // Tiny amp: a very quiet second pulse on the same xy port.
  setInput(win, cell(win, 'pulses', 'q1', 'saturation_amplitude'), '-45');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('sliver') >= 0, 'C5b: tiny-amp DAC-resolution warning rendered');

  // Re-solving a port re-derives findings from spec (no accumulation): bring
  // x180 back in range on q1; saturation stays quiet so its warning persists,
  // but the at-max-reach warning must clear (FSP now 1, not 18).
  setInput(win, cell(win, 'pulses', 'q1', 'x180_amplitude'), '-6');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('maximum output') < 0, 'C5b: cleared warning drops off');
  ok(panel.indexOf('sliver') >= 0, 'C5b: still-true warning persists');
})();

// C5c: warning LIFECYCLE — render-time derivation self-clears on mode flip,
// prunes deleted qubits, keys readout banks by port (the cluster-1 fixes).
(function warningLifecycle() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');

  // (a) mode flip → manual clears power warnings entirely.
  setInput(win, cell(win, 'pulses', 'q1', 'x180_amplitude'), '25');   // at-max warning
  let panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('maximum output') >= 0, 'C5c: absolute-mode warning present');
  setInput(win, powerModeSelect(win), 'manual');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('maximum output') < 0, 'C5c: warnings cleared on flip to manual');
  ok(panel.indexOf('power conflicts') < 0, 'C5c: heading drops "power" in manual');

  // (b) readout bank keyed by PORT — fixing the bank via a sibling clears it.
  setInput(win, powerModeSelect(win), 'absolute');
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '9');   // 3-tone clip
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('CLIP') >= 0, 'C5c: bank clip warning present');
  const clipCount = (panel.match(/CLIP/g) || []).length;
  ok(clipCount === 1, 'C5c: bank clip warning appears ONCE (port-keyed, got ' + clipCount + ')');
  setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '-30');   // fix via sibling
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('CLIP') < 0, 'C5c: fixing bank via sibling clears the stale CLIP');

  // (c) deleting the warned qubit prunes its finding (no ghost).
  setInput(win, cell(win, 'pulses', 'q3', 'x180_amplitude'), '25');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('q3') >= 0, 'C5c: q3 at-max warning present');
  setInput(win, win.document.getElementById('gen-qubit-count'), '2');   // drop q3
  G.goToStep(6);
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('q3') < 0, 'C5c: deleted-qubit warning pruned (no ghost)');
})();

// C5d: findings are DERIVED from spec, not cached from the edit — mutating the
// stored spec directly (as a draft restore would, bypassing the edit handler)
// and re-entering step 6 re-derives the warning to match. This is the fix for
// the "edit-time-ephemeral" gap (a restored clipping bank showed no warning).
(function derivedFromSpec() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '9');   // clip
  ok(win.document.getElementById('gen-band-warnings').textContent.indexOf('CLIP') >= 0,
    'C5d: clip warning present after the edit');

  // Directly clear the clip in the spec (no edit handler) + re-enter step 6.
  ['q1', 'q2', 'q3'].forEach(function (q) {
    G.state.spec.populate.resonator[q].readout_amplitude = 0.05;
  });
  G.goToStep(5); G.goToStep(6);
  ok(win.document.getElementById('gen-band-warnings').textContent.indexOf('CLIP') < 0,
    'C5d: warning GONE after spec cleared out-of-band (re-derived, not cached)');

  // Directly re-introduce a clip in the spec + re-enter — warning reappears.
  ['q1', 'q2', 'q3'].forEach(function (q) {
    G.state.spec.populate.resonator[q].readout_amplitude = 0.5;
  });
  G.goToStep(5); G.goToStep(6);
  ok(win.document.getElementById('gen-band-warnings').textContent.indexOf('CLIP') >= 0,
    'C5d: warning REAPPEARS from spec alone (no edit) — proves render-time derivation');
})();

// C5e: NaN/string-FSP hardening — a corrupt draft FSP does not NaN-corrupt the
// bank; fspForAmp coerces so recovery stays finite.
(function nanHardening() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');
  // Simulate a corrupt draft: string FSP on the resonator bank.
  G.state.spec.populate.resonator = { q1: { full_scale_power_dbm: '4', readout_amplitude: 0.2 } };
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '-25');
  const res = G.state.spec.populate.resonator;
  Object.keys(res).forEach(function (q) {
    ok(res[q].full_scale_power_dbm == null || isFinite(res[q].full_scale_power_dbm),
      'C5e: ' + q + ' FSP is finite, not NaN (got ' + res[q].full_scale_power_dbm + ')');
    ok(res[q].readout_amplitude == null || isFinite(res[q].readout_amplitude),
      'C5e: ' + q + ' amp is finite, not NaN');
  });
})();

// C6: manual mode is UNCHANGED — dBm unit entry converts against the fixed
// FSP and does NOT re-allocate (regression guard for today's users). The one
// deliberate exception is the mode-independent Σ|amp|>1 feedline clip warning
// (customer requirement) — pinned separately in C9.
(function manualUnchanged() {
  const win = makeWorld();
  const G = buildWizard(win);
  // Manual: set FSP by hand, then enter readout amp with the dBm UNIT.
  setInput(win, cell(win, 'resonator', 'q1', 'full_scale_power_dbm'), '-5');
  const unitSels = win.document.querySelectorAll(
    '#gen-pop-units .gen-pop-unit:not(.gen-pop-powermode) select');
  const ampUnit = unitSels[unitSels.length - 1];   // amp is the last dim
  ok(ampUnit && !ampUnit.disabled, 'C6: amp unit selector free in manual mode');
  setInput(win, ampUnit, 'dBm');
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '-25');
  const r1 = G.state.spec.populate.resonator.q1;
  ok(r1.full_scale_power_dbm === -5, 'C6: hand-typed FSP untouched (got ' +
    r1.full_scale_power_dbm + ')');
  close(r1.readout_amplitude, Math.pow(10, (-25 - (-5)) / 20), 1e-9,
    'C6: amp converts against the FIXED FSP (no re-allocation)');
})();

// C9: MANUAL-mode feedline clip — the Σ|amp| > 1 warning is a physical DAC
// fact and must fire regardless of power mode (customer requirement). The
// 0.5 headroom ADVISORY stays absolute-only (allocation policy, not physics).
(function manualModeBankClip() {
  const win = makeWorld();
  const G = buildWizard(win);
  ok(G.state.powerMode === 'manual', 'C9: world starts in manual mode');

  // 0.6 + 0.6 + 0.1 on the shared feedline → Σ = 1.3 > 1 → CLIP, immediately
  // on the amp commit (no mode toggle, no step re-entry).
  setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.6');
  setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.6');
  setInput(win, cell(win, 'resonator', 'q3', 'readout_amplitude'), '0.1');
  let panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('CLIP') >= 0, 'C9: manual-mode bank clip warning rendered');
  ok(panel.indexOf('power conflicts') >= 0, 'C9: heading names power conflicts');
  ok(panel.indexOf('headroom budget') < 0,
    'C9: 0.5 advisory absent in manual mode (policy stays absolute-only)');

  // Lowering one tone under the budget clears it (still manual mode)...
  setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.2');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('CLIP') < 0, 'C9: clip clears when the sum drops under 1');
  // ...and Σ = 0.9 (> 0.5) still shows NO advisory in manual mode.
  ok(panel.indexOf('headroom budget') < 0,
    'C9: sum 0.9 draws no advisory in manual mode');

  // Sliver amps never warn in manual mode (absolute-mode allocation policy).
  setInput(win, cell(win, 'resonator', 'q3', 'readout_amplitude'), '0.001');
  panel = win.document.getElementById('gen-band-warnings').textContent;
  ok(panel.indexOf('sliver') < 0, 'C9: no sliver warning in manual mode');
})();

// C8: multi-dirty flush clobber — two amp cells on one bank both dirty; the
// step-nav flush must NOT let the first cell's bank re-solve overwrite the
// second's still-typed value (finding: refreshAmpCells clobbered dirty cells).
(function multiDirtyFlush() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');
  // Type into q1 and q2 readout amps with INPUT only (no change/blur) — the
  // pywebview swallowed-blur race the flush is designed for.
  const c1 = cell(win, 'resonator', 'q1', 'readout_amplitude');
  const c2 = cell(win, 'resonator', 'q2', 'readout_amplitude');
  c1.value = '7';  c1.dispatchEvent(new win.Event('input', { bubbles: true }));
  c2.value = '-20'; c2.dispatchEvent(new win.Event('input', { bubbles: true }));
  // Both dirty; nav to step 7 flushes them in DOM order.
  G.goToStep(7);
  // q2's correction (-20) is the last-committed bank value; the bank must land
  // on -20, not on q1's stale 7 resurrected by a mid-flush refresh.
  const res = G.state.spec.populate.resonator;
  const achieved = res.q2.full_scale_power_dbm + 20 * Math.log10(res.q2.readout_amplitude);
  close(achieved, -20, 1e-6, 'C8: bank lands on the last-typed -20 dBm, not clobbered to 7');
})();

// C7: LO integration — RF edits assign a hole-clearing LO to a lone
// resonator through the real recomputeLOs path.
(function loIntegration() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.46');   // GHz unit
  const lo = G.state.spec.populate.resonator.q1.LO_frequency;
  ok(lo != null, 'C7: LO auto-assigned on RF edit');
  ok(Math.abs(lo - 7.46e9) >= 5e6, 'C7: LO clears the demod hole (|IF|=' +
    Math.abs(lo - 7.46e9) / 1e6 + ' MHz, legacy was 0)');
  ok(Math.abs(lo - 7.46e9) <= 0.4e9, 'C7: LO within the IF window');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
