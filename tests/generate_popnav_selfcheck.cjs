// Behavioral check for the Populate step's spreadsheet-style arrow-key
// navigation (generate.js popGridKeydown).
//
// Customer feedback: value boxes must be walkable with the arrows — → moves
// to the next cell only when the caret is at the END of the text, ← moves
// back only from the START (mid-text arrows keep native caret movement),
// ↑/↓ ALWAYS move within the column (including the "Set all" row). SELECT
// cells are never hijacked; disabled cells (FSP in absolute power mode) are
// skipped.
//
// Run: node tests/generate_popnav_selfcheck.cjs   (needs jsdom)
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
function cell(win, group, rid, field) {
  return win.document.querySelector(
    '.gen-pop-in[data-group="' + group + '"][data-rid="' + rid +
    '"][data-field="' + field + '"]');
}
function arrow(win, el, key) {
  el.dispatchEvent(new win.KeyboardEvent('keydown',
    { key: key, bubbles: true, cancelable: true }));
}
function powerModeSelect(win) {
  const sels = win.document.querySelectorAll('#gen-pop-units .gen-pop-powermode select');
  return sels.length ? sels[0] : null;
}

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
  return G;
}

// N1: → moves right only from the caret END; mid-text keeps native caret.
(function rightArrow() {
  const win = makeWorld();
  buildWizard(win);
  const rf = cell(win, 'resonator', 'q1', 'RF_freq');
  rf.value = '4.554';
  rf.focus();
  rf.setSelectionRange(rf.value.length, rf.value.length);   // caret at end
  arrow(win, rf, 'ArrowRight');
  ok(win.document.activeElement === cell(win, 'resonator', 'q1', 'LO_frequency'),
    'N1: → at end moves to the next cell (LO)');
  // Mid-text: stays in the box.
  const rf2 = cell(win, 'resonator', 'q1', 'RF_freq');
  rf2.focus();
  rf2.setSelectionRange(2, 2);
  arrow(win, rf2, 'ArrowRight');
  ok(win.document.activeElement === rf2, 'N1: → mid-text stays in the box');
})();

// N2: ← moves left only from the caret START.
(function leftArrow() {
  const win = makeWorld();
  buildWizard(win);
  const lo = cell(win, 'resonator', 'q1', 'LO_frequency');
  lo.value = '7.1';
  lo.focus();
  lo.setSelectionRange(0, 0);
  arrow(win, lo, 'ArrowLeft');
  ok(win.document.activeElement === cell(win, 'resonator', 'q1', 'RF_freq'),
    'N2: ← at start moves to the previous cell (RF)');
  const lo2 = cell(win, 'resonator', 'q1', 'LO_frequency');
  lo2.value = '7.1';
  lo2.focus();
  lo2.setSelectionRange(3, 3);   // at end — ← must NOT leave the box
  arrow(win, lo2, 'ArrowLeft');
  ok(win.document.activeElement === lo2, 'N2: ← at end stays (native caret)');
  // Leftmost editable cell: ← at start stays put (label td is not focusable).
  const rf = cell(win, 'resonator', 'q1', 'RF_freq');
  rf.focus();
  rf.setSelectionRange(0, 0);
  arrow(win, rf, 'ArrowLeft');
  ok(win.document.activeElement === rf, 'N2: row edge stops');
})();

// N3: ↑/↓ always move within the column — including into the Set-all row.
(function verticalArrows() {
  const win = makeWorld();
  buildWizard(win);
  const q2rf = cell(win, 'resonator', 'q2', 'RF_freq');
  q2rf.value = '7.2';
  q2rf.focus();
  q2rf.setSelectionRange(1, 1);   // caret position must not matter for ↑/↓
  arrow(win, q2rf, 'ArrowUp');
  ok(win.document.activeElement === cell(win, 'resonator', 'q1', 'RF_freq'),
    'N3: ↑ moves to the row above (same column)');
  arrow(win, win.document.activeElement, 'ArrowDown');
  ok(win.document.activeElement === q2rf, 'N3: ↓ moves back down');
  // ↑ from the FIRST data row lands in the Set-all row's input.
  const q1rf = cell(win, 'resonator', 'q1', 'RF_freq');
  q1rf.focus();
  arrow(win, q1rf, 'ArrowUp');
  const active = win.document.activeElement;
  ok(active && active.tagName === 'INPUT' &&
     active.closest('tr').classList.contains('gen-pop-setall'),
    'N3: ↑ from row 1 reaches the Set-all input');
  arrow(win, active, 'ArrowUp');
  ok(win.document.activeElement === active, 'N3: table top stops');
})();

// N4: SELECT cells are never hijacked (arrows keep native behavior).
(function selectsUntouched() {
  const win = makeWorld();
  const G = buildWizard(win);
  // Flux table needs an LF-FEM — rebuild with one.
  G.goToStep(3);
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'flux_tunable_coupler';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  G.goToStep(6);
  const sel = cell(win, 'flux', 'q1', 'flux_point');
  ok(!!sel && sel.tagName === 'SELECT', 'N4: flux_point renders as a select');
  sel.focus();
  arrow(win, sel, 'ArrowDown');
  ok(win.document.activeElement === sel, 'N4: ↓ on a select does not move focus');
  arrow(win, sel, 'ArrowRight');
  ok(win.document.activeElement === sel, 'N4: → on a select does not move focus');
})();

// N5: disabled cells (FSP in absolute power mode) are skipped, and arrows can
// still land ON a select from a neighboring input (focus is fine — only
// arrows FROM selects are native).
(function disabledSkipped() {
  const win = makeWorld();
  const G = buildWizard(win);
  setInput(win, powerModeSelect(win), 'absolute');
  const amp = cell(win, 'resonator', 'q1', 'readout_amplitude');
  const fsp = cell(win, 'resonator', 'q1', 'full_scale_power_dbm');
  ok(fsp.disabled, 'N5: FSP is disabled in absolute mode');
  amp.value = '-20';
  amp.focus();
  amp.setSelectionRange(amp.value.length, amp.value.length);
  arrow(win, amp, 'ArrowRight');
  ok(win.document.activeElement === amp,
    'N5: → skips the disabled FSP (last column) and stays');
})();

// N6: the keystroke live-write means a value typed then arrow-moved is
// already committed to the spec (input handler), independent of blur timing.
(function valueCommitted() {
  const win = makeWorld();
  const G = buildWizard(win);
  const rf = cell(win, 'qubit', 'q2', 'RF_freq');
  rf.focus();
  rf.value = '4.554';
  rf.dispatchEvent(new win.Event('input', { bubbles: true }));
  rf.setSelectionRange(rf.value.length, rf.value.length);
  arrow(win, rf, 'ArrowRight');
  ok(win.document.activeElement !== rf, 'N6: focus moved on');
  ok(Math.abs(G.state.spec.populate.qubit.q2.RF_freq - 4.554e9) < 1,
    'N6: the typed value is committed in the spec (got ' +
    G.state.spec.populate.qubit.q2.RF_freq + ')');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_popnav_selfcheck: all checks passed');
