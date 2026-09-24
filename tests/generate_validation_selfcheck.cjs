// Behavioral check for the populate step's INLINE as-you-type validation
// (generate.js validateCellValue / validateCellInline / validateAllPopCells).
//
// The layering contract under test: the inline layer flags per-cell,
// single-cell-derivable facts IMMEDIATELY on 'input' (debounced), while the
// conflict panel keeps the cross-cell findings at commit time; the inline
// validator never writes panel entries. The customer requirement pinned here:
// "if a user types 15.3 GHz, SM should warn right away" — unit-aware, on the
// keystroke, not on blur.
//
// Run: node tests/generate_validation_selfcheck.cjs   (needs jsdom)
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
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  // attach() mirrors the real NumberInput: it flips number→text so free text
  // ("abc", "100,000,000") reaches the validator the way it does in the app.
  win.NumberInput = {
    fit() {},
    attach(el) { try { el.type = 'text'; } catch (e) {} },
    format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
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
// Keystroke without blur — the as-you-type path.
function typeOnly(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
}
function cell(win, group, rid, field) {
  return win.document.querySelector(
    '.gen-pop-in[data-group="' + group + '"][data-rid="' + rid +
    '"][data-field="' + field + '"]');
}
function flagged(el) {
  return el.classList.contains('gen-cell-err') ? 'err'
    : el.classList.contains('gen-cell-warn') ? 'warn' : null;
}
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

// Same 3-qubit world as the power selfcheck: 1 MW-FEM, all resonators
// multiplexed on port 1, each xy on its own port.
function buildWizard(win) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'fixed_frequency';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  const rr = [
    { con: 1, slot: 1, port: 1, io_type: 'output' },
    { con: 1, slot: 1, port: 1, io_type: 'input' }];
  G.state.allocation = {
    q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }], rr: rr },
    q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }], rr: rr },
    q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }], rr: rr }
  };
  G.goToStep(6);
  G.QT = win.QuamGen._test;
  return G;
}

function freqUnitSelect(win) {
  // Unit selects render in dim order — freq is the first non-powermode one.
  return win.document.querySelectorAll(
    '#gen-pop-units .gen-pop-unit:not(.gen-pop-powermode) select')[0];
}
function ampUnitSelect(win) {
  const sels = win.document.querySelectorAll(
    '#gen-pop-units .gen-pop-unit:not(.gen-pop-powermode) select');
  return sels[sels.length - 1];
}
function powerModeSelect(win) {
  const sels = win.document.querySelectorAll('#gen-pop-units .gen-pop-powermode select');
  return sels.length ? sels[0] : null;
}
function panelText(win) {
  return win.document.getElementById('gen-band-warnings').textContent;
}

(async function main() {

  // D1: the headline case — 15.3 typed in GHz mode flags the cell on the
  // KEYSTROKE (no blur), with the hardware-reach message; and the inline
  // layer writes NOTHING to the conflict panel.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    const panelBefore = panelText(win);
    typeOnly(win, c, '15.3');
    await tick();
    ok(flagged(c) === 'err', 'D1: 15.3 GHz flags err on keystroke (got ' + flagged(c) + ')');
    ok(c.title.indexOf('hardware reach') >= 0, 'D1: title names hardware reach');
    const flag = c.parentNode.querySelector('.gen-cell-flag');
    ok(!!flag && flag.classList.contains('err'), 'D1: ⚠ icon rendered in the td');
    ok(panelText(win) === panelBefore, 'D1: inline layer wrote nothing to the panel');
    // Fixing the value clears the decoration.
    typeOnly(win, c, '5.1');
    await tick();
    ok(flagged(c) === null, 'D1: valid value clears the flag');
    ok(!c.parentNode.querySelector('.gen-cell-flag'), 'D1: ⚠ icon removed');
  }

  // D2: unit-awareness — the same digits mean different base values per unit.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, freqUnitSelect(win), 'MHz');
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    typeOnly(win, c, '15.3');            // 15.3 MHz < 50 MHz floor
    await tick();
    ok(flagged(c) === 'err', 'D2: 15.3 MHz (< 50 MHz floor) flags err');
    typeOnly(win, c, '5100');            // 5.1 GHz — fine
    await tick();
    ok(flagged(c) === null, 'D2: 5100 MHz is clean');
  }

  // D3: asymmetric floors — readout has a 2 GHz floor, drive only 50 MHz.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const rq = cell(win, 'resonator', 'q1', 'RF_freq');
    const qq = cell(win, 'qubit', 'q1', 'RF_freq');
    typeOnly(win, rq, '1.5');
    typeOnly(win, qq, '1.5');
    await tick();
    ok(flagged(rq) === 'err', 'D3: resonator 1.5 GHz errs (2 GHz input floor)');
    ok(rq.title.indexOf('input range') >= 0, 'D3: resonator message names input range');
    ok(flagged(qq) === null, 'D3: qubit 1.5 GHz is clean (drive reach)');
  }

  // D4: dimensionless amp bounds in 0-1 mode.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const c = cell(win, 'resonator', 'q1', 'readout_amplitude');
    typeOnly(win, c, '1.2');
    await tick();
    ok(flagged(c) === 'err', 'D4: amp 1.2 errs');
    ok(c.title.indexOf('full scale') >= 0, 'D4: message names DAC full scale');
    typeOnly(win, c, '0.9');
    await tick();
    ok(flagged(c) === null, 'D4: amp 0.9 clean');
    typeOnly(win, c, '-1.2');
    await tick();
    ok(flagged(c) === 'err', 'D4: amp -1.2 errs (|amp| checked)');
  }

  // D5: feedline Σ|amp| — immediate on the typed cell, panel CLIP on commit.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.5');
    setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.4');
    const c3 = cell(win, 'resonator', 'q3', 'readout_amplitude');
    typeOnly(win, c3, '0.3');            // Σ = 1.2 while typing
    await tick();
    ok(flagged(c3) === 'err', 'D5: Σ=1.2 errs on the typed cell before blur');
    ok(c3.title.indexOf('1.2') >= 0 && c3.title.indexOf('CLIP') >= 0,
      'D5: message carries the sum + CLIP');
    typeOnly(win, c3, '0.05');           // Σ = 0.95
    await tick();
    ok(flagged(c3) === null, 'D5: Σ=0.95 clears while typing');
    // Committing an over-budget bank ALSO lands the port-keyed panel CLIP.
    setInput(win, c3, '0.3');
    const clips = (panelText(win).match(/CLIP/g) || []).length;
    ok(clips === 1, 'D5: panel CLIP appears exactly once on commit (got ' + clips + ')');
    ok(flagged(c3) === 'err', 'D5: inline flag persists after commit');
  }

  // D6: comma-grouped input + Hz unit edge.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, freqUnitSelect(win), 'Hz');
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    typeOnly(win, c, '5,100,000,000');   // 5.1 GHz
    await tick();
    ok(flagged(c) === null, 'D6: comma-grouped 5.1 GHz (Hz unit) is clean');
    typeOnly(win, c, '15,300,000');      // 15.3 MHz — below the 50 MHz floor
    await tick();
    ok(flagged(c) === 'err', 'D6: 15.3 MHz (Hz unit) errs (below hardware reach)');
  }

  // D7: hand-typed LO — window, band and demod-hole facts on the LO cell.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    // RFs commit → recomputeLOs caches the LO groups for the inline layer.
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.10');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '7.20');
    const lo = cell(win, 'resonator', 'q1', 'LO_frequency');
    typeOnly(win, lo, '6.0');
    await tick();
    ok(flagged(lo) === 'err', 'D7: LO 6.0 GHz errs (member outside ±0.4 GHz window)');
    ok(lo.title.indexOf('IF window') >= 0, 'D7: message names the IF window');
    typeOnly(win, lo, '20');
    await tick();
    ok(flagged(lo) === 'err', 'D7: LO 20 GHz errs (outside every band)');
    ok(lo.title.indexOf('band') >= 0, 'D7: message names the bands');
    // Demod hole: resonators at 7.148/7.152, LO typed onto them.
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.152');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '7.148');
    const lo2 = cell(win, 'resonator', 'q1', 'LO_frequency');
    typeOnly(win, lo2, '7.15');
    await tick();
    ok(flagged(lo2) === 'warn', 'D7: LO 2 MHz from a resonator warns (demod hole), got ' + flagged(lo2));
    ok(lo2.title.indexOf('demod hole') >= 0, 'D7: message names the demod hole');
  }

  // D8: nonsense input.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    typeOnly(win, c, 'abc');
    await tick();
    ok(flagged(c) === 'err', 'D8: "abc" errs (not a number)');
    typeOnly(win, c, '');
    await tick();
    ok(flagged(c) === null, 'D8: empty clears the flag');
    typeOnly(win, c, '-5');
    await tick();
    ok(flagged(c) === 'err', 'D8: negative frequency errs');
  }

  // D9: manual-mode FSP bounds + integer grid.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const c = cell(win, 'resonator', 'q1', 'full_scale_power_dbm');
    typeOnly(win, c, '25');
    await tick();
    ok(flagged(c) === 'err', 'D9: FSP 25 errs (> 18 max)');
    typeOnly(win, c, '3.5');
    await tick();
    ok(flagged(c) === 'warn', 'D9: FSP 3.5 warns (integer grid)');
    typeOnly(win, c, '-11');
    await tick();
    ok(flagged(c) === null, 'D9: FSP -11 boundary clean');
    typeOnly(win, c, '18');
    await tick();
    ok(flagged(c) === null, 'D9: FSP 18 boundary clean');
  }

  // D10: first-render sweep — a bad value restored from a draft (spec seeded
  // directly, zero keystrokes) is flagged at render; unit flips revalidate.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    G.state.spec.populate.qubit = { q1: { RF_freq: 15.3e9 } };
    G.goToStep(5); G.goToStep(6);        // re-render from spec
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    ok(flagged(c) === 'err', 'D10: restored 15.3e9 flagged with zero keystrokes');
    setInput(win, freqUnitSelect(win), 'MHz');   // re-renders the tables
    const c2 = cell(win, 'qubit', 'q1', 'RF_freq');
    ok(flagged(c2) === 'err', 'D10: flag survives the unit flip (revalidated)');
  }

  // D11: manual mode + dBm display unit — unreachable amp under a fixed FSP.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'full_scale_power_dbm'), '-5');
    setInput(win, ampUnitSelect(win), 'dBm');
    const c = cell(win, 'resonator', 'q1', 'readout_amplitude');
    typeOnly(win, c, '2');               // +2 dBm at FSP -5 → amp 2.24 > 1
    await tick();
    ok(flagged(c) === 'err', 'D11: +2 dBm at FSP -5 errs (needs amp > 1)');
    ok(c.title.indexOf('Unreachable') >= 0, 'D11: message says unreachable');
    typeOnly(win, c, '-25');
    await tick();
    ok(flagged(c) === null, 'D11: -25 dBm at FSP -5 clean');
  }

  // D12: absolute power mode — a reachable dBm target never flags inline
  // (commit re-solves the FSP), the >18 dBm ceiling does.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, powerModeSelect(win), 'absolute');
    const c = cell(win, 'resonator', 'q1', 'readout_amplitude');
    typeOnly(win, c, '-20');
    await tick();
    ok(flagged(c) === null, 'D12: -20 dBm target clean in absolute mode');
    typeOnly(win, c, '5');               // reachable after FSP re-solve
    await tick();
    ok(flagged(c) === null, 'D12: +5 dBm clean (FSP re-solve makes it reachable)');
    typeOnly(win, c, '25');
    await tick();
    ok(flagged(c) === 'err', 'D12: +25 dBm errs (beyond the +18 dBm port maximum)');
  }

  // D13: real-debounce mechanics — no flag immediately, flag after the wait;
  // a table re-render before the timer fires must not throw (isConnected).
  {
    const win = makeWorld();
    const G = buildWizard(win);          // real 250 ms debounce
    const c = cell(win, 'qubit', 'q1', 'RF_freq');
    typeOnly(win, c, '15.3');
    ok(flagged(c) === null, 'D13: no flag synchronously (debounced)');
    await tick(350);
    ok(flagged(c) === 'err', 'D13: flag lands after the debounce window');
    // Orphan the cell mid-debounce: re-render, then let the timer fire.
    typeOnly(win, c, '15.3');
    G.goToStep(5); G.goToStep(6);
    await tick(350);                      // isConnected guard — must not throw
    ok(true, 'D13: orphaned-cell timer is harmless');
  }

  // D14 (QA generate-r2-02): a pulse length <= 0 and any negative duration
  // err on the keystroke — QM refused the built config ("Value out of range:
  // -3") while the cells stayed clean. A zero depletion / ToF stays clean.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const x = cell(win, 'pulses', 'q1', 'x180_length');
    typeOnly(win, x, '-3');
    await tick();
    ok(flagged(x) === 'err', 'D14: x180 length -3 ns errs');
    typeOnly(win, x, '0');
    await tick();
    ok(flagged(x) === 'err', 'D14: x180 length 0 errs (a pulse needs a length)');
    typeOnly(win, x, '40');
    await tick();
    ok(flagged(x) === null, 'D14: x180 length 40 clean');
    const r = cell(win, 'resonator', 'q2', 'readout_length');
    typeOnly(win, r, '0');
    await tick();
    ok(flagged(r) === 'err', 'D14: readout length 0 errs');
    const d = cell(win, 'resonator', 'q1', 'depletion_time');
    typeOnly(win, d, '0');
    await tick();
    ok(flagged(d) === null, 'D14: depletion 0 clean (legitimately zero)');
    typeOnly(win, d, '-5');
    await tick();
    ok(flagged(d) === 'err', 'D14: negative depletion errs');
    const V = G.QT.validateCellValue;
    ok(V('twpa', 'twpaA', { field: 'settling_time', unit: 'ns' }, -1, '-1') &&
       V('twpa', 'twpaA', { field: 'settling_time', unit: 'ns' }, 0, '0') === null,
       'D14: settling time: negative errs, 0 clean');
    ok(V('twpa', 'twpaA', { field: 'pump_length', dim: 'time' }, 0, '0'),
       'D14: TWPA pump length 0 errs');
    ok(V('qdac', 'q1', { field: 'dwell', unit: 's' }, -2e-6, '-2e-6') &&
       V('qdac', 'q1', { field: 'dwell', unit: 's' }, 0, '0') === null,
       'D14: QDAC dwell: negative errs, 0 clean');
  }

  // D15 (QA generate-r2-02): a TWPA tone scale is QUA amp(): [-2, 2 - 2^-16].
  {
    const win = makeWorld();
    const G = buildWizard(win);
    const V = G.QT.validateCellValue;
    const pump = { field: 'pump_amplitude', label: 'pump amp (scale)' };
    const iso = { field: 'isolation_amplitude', label: 'isolation amp (scale)' };
    ok((V('twpa', 'twpaA', pump, 5, '5') || {}).severity === 'err',
       'D15: pump amp 5 errs');
    ok((V('twpa', 'twpaA', iso, -2.5, '-2.5') || {}).severity === 'err',
       'D15: isolation amp -2.5 errs');
    ok((V('twpa', 'twpaA', pump, 2, '2') || {}).severity === 'err',
       'D15: pump amp 2 errs (amp() stops at 2 - 2^-16)');
    ok(V('twpa', 'twpaA', pump, 1, '1') === null &&
       V('twpa', 'twpaA', pump, 1.5, '1.5') === null &&
       V('twpa', 'twpaA', iso, -2, '-2') === null,
       'D15: 1, 1.5 and -2 are clean');
  }

  // D16 (QA generate-r2-07): the QDAC channel range the driver asserts.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    const V = G.QT.validateCellValue;
    const ch = { field: 'channel', label: 'channel' };
    const e30 = V('qdac', 'q1', ch, 30, '30');
    ok(e30 && e30.severity === 'err' && e30.message.indexOf('1 to 24') >= 0,
       'D16: channel 30 errs naming 1 to 24');
    ok((V('qdac', 'q1', ch, 0, '0') || {}).severity === 'err', 'D16: channel 0 errs');
    ok((V('qdac', 'q1', ch, 2.5, '2.5') || {}).severity === 'err',
       'D16: a fractional channel errs');
    ok(V('qdac', 'q1', ch, 1, '1') === null && V('qdac', 'q1', ch, 24, '24') === null,
       'D16: 1 and 24 are clean');
  }

  // D17 (QA generate-r2-19): an unparseable commit keeps the stored value.
  // The keystroke live-write used to leave whatever the last parseable key
  // wrote — after clear-then-type, NOTHING: RF deleted, and the unit switch's
  // re-render then showed an empty cell (the red "abc" gone with it).
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const pq = function () { return (G.state.spec.populate.qubit || {}).q2 || {}; };
    let c = cell(win, 'qubit', 'q2', 'RF_freq');
    setInput(win, c, '5');                                   // 5 GHz committed
    ok(pq().RF_freq === 5e9, 'D17: 5 GHz stored');
    // clear, then type, then commit (blur)
    typeOnly(win, c, '');
    typeOnly(win, c, 'abc');
    c.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(pq().RF_freq === 5e9,
       'D17: clear-then-"abc" commit keeps 5 GHz (got ' + pq().RF_freq + ')');
    ok(flagged(c) === 'err' && c.value === 'abc', 'D17: the typed text stays, flagged');
    ok(c.title.indexOf('Not saved') >= 0, 'D17: the flag says nothing was saved');
    setInput(win, freqUnitSelect(win), 'MHz');               // re-render
    c = cell(win, 'qubit', 'q2', 'RF_freq');
    ok(c.value === '5000' && flagged(c) === null,
       'D17: after the unit switch the cell shows the kept 5000 MHz (got "' + c.value + '")');
    // An UNcommitted typo flushed by the unit switch (captureDomFields).
    typeOnly(win, c, '');
    typeOnly(win, c, 'zz');
    setInput(win, freqUnitSelect(win), 'GHz');
    c = cell(win, 'qubit', 'q2', 'RF_freq');
    ok(pq().RF_freq === 5e9 && c.value === '5',
       'D17: a dirty typo flushed by a unit switch keeps 5 GHz (got ' + pq().RF_freq + ')');
    // 1e999 is no finite number either.
    typeOnly(win, c, '1e999');
    c.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(pq().RF_freq === 5e9, 'D17: "1e999" never stores Infinity');
    // A real value still commits; a genuine clear still clears.
    setInput(win, c, '4.8');
    ok(pq().RF_freq === 4.8e9 && flagged(c) === null, 'D17: a valid commit still lands');
    setInput(win, c, '');
    ok(!('RF_freq' in pq()), 'D17: an explicit clear still clears');
  }

  // D18 (QA generate-r2-19): Set-all "abc" changes no row AND says so.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    const rf = function () {
      return ['q1', 'q2', 'q3'].map(function (q) {
        return ((G.state.spec.populate.qubit || {})[q] || {}).RF_freq;
      });
    };
    setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '5');
    setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.2');
    const sa = win.document.querySelector(
      '#gen-pop-tbl-qubit tr.gen-pop-setall input[aria-label="Set all RF freq"]');
    ok(!!sa, 'D18: the RF Set-all box renders');
    const before = JSON.stringify(rf());
    sa.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    setInput(win, sa, 'abc');
    ok(JSON.stringify(rf()) === before, 'D18: no row changed');
    ok(flagged(sa) === 'err' && sa.title.indexOf('no row was changed') >= 0,
       'D18: the Set-all box is flagged (got class="' + sa.className + '")');
    ok(!!sa.parentNode.querySelector('.gen-cell-flag'), 'D18: with the ⚠ marker');
    // Ctrl+Z on that commit must NOT replay the box's "" (an empty commit
    // would clear the whole column).
    win._wizUndo.tryUndo();
    ok(JSON.stringify(rf()) === before, 'D18: Ctrl+Z after the typo clears no row');
    ok(flagged(sa) === null, 'D18: …and the flag is gone');
    // A valid Set-all clears the flag and fills.
    setInput(win, sa, 'abc');
    setInput(win, sa, '6');
    ok(JSON.stringify(rf()) === '[6000000000,6000000000,6000000000]' && flagged(sa) === null,
       'D18: a valid Set-all fills every row and clears the flag');
    // docs/27: an EMPTY Set-all commit still clears the column.
    setInput(win, sa, '');
    ok(rf().every(function (v) { return v === undefined; }), 'D18: empty commit clears the column');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_validation_selfcheck: all checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
