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

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_validation_selfcheck: all checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
