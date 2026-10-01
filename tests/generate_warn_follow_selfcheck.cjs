// Warnings follow edits (docs/239). Customer report: a Generate-Config
// warning (e.g. a port's LO cannot cover its RFs -- the 0.4 GHz IF window)
// stayed on screen after the user typed the corrected value; it only went
// away on blur, or -- for a coupled cell -- not even then.
//
// Contract pinned here (generate.js refreshEditFindings): every warning is
// re-derived on the debounced 'input' of ANY value it depends on, and again
// on commit -- the typed cell's flag, every coupled cell's flag and the
// conflict panel -- so it disappears on the keystroke that fixes it, whichever
// field the user fixes it through. Every check below types WITHOUT blur
// (typeOnly) unless it says "commit".
//
// Run: node tests/generate_warn_follow_selfcheck.cjs   (needs jsdom)
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

function loFlags(win) {
  return ['q1', 'q2', 'q3'].map(function (r) {
    return flagged(cell(win, 'resonator', r, 'LO_frequency'));
  });
}
function ampFlags(win) {
  return ['q1', 'q2', 'q3'].map(function (r) {
    return flagged(cell(win, 'resonator', r, 'readout_amplitude'));
  });
}

(async function main() {

  // W1: the reported case. Port Out1 carries three readout tones; q3's RF
  // pushes the span past one LO's +-0.4 GHz window -> panel + every LO cell
  // flagged. The user retypes q3's RF -- the fix is on the KEYSTROKE, no
  // blur: the panel empties and the LO cells of q1/q2 (coupled cells the
  // user never touched) clear with it.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.1');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '7.2');
    setInput(win, cell(win, 'resonator', 'q3', 'RF_freq'), '8.0');
    ok(/IF window/.test(panelText(win)), 'W1: the bad commit shows the window warning (got "' +
      panelText(win) + '")');
    ok(loFlags(win).join() === 'err,err,err', 'W1: every LO cell flagged (got ' + loFlags(win) + ')');
    typeOnly(win, cell(win, 'resonator', 'q3', 'RF_freq'), '7.3');
    await tick();
    ok(panelText(win) === '', 'W1: the panel empties on the fixing keystroke (got "' +
      panelText(win) + '")');
    ok(loFlags(win).join() === ',,', 'W1: the LO cells clear on the keystroke (got ' +
      loFlags(win) + ')');
    // ...and a re-break while still typing brings it straight back.
    typeOnly(win, cell(win, 'resonator', 'q3', 'RF_freq'), '8.0');
    await tick();
    ok(/IF window/.test(panelText(win)), 'W1: a re-break shows again before blur');
    typeOnly(win, cell(win, 'resonator', 'q3', 'RF_freq'), '7.3');
    await tick();
    setInput(win, cell(win, 'resonator', 'q3', 'RF_freq'), '7.3');
    ok(panelText(win) === '' && loFlags(win).join() === ',,', 'W1: the commit agrees');
  }

  // W2: the LO itself is the fixed field. A hand-typed LO that leaves q2's
  // RF outside the window -> the LO cell AND the panel say so on the
  // keystroke; retyping the port's LO clears both on the keystroke. A
  // hand-typed LO is never re-solved over (QA r2-07).
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.1');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '7.2');
    const good = G.state.spec.populate.resonator.q2.LO_frequency;
    ok(panelText(win) === '' && loFlags(win).join() === ',,', 'W2: clean start');
    const lo = cell(win, 'resonator', 'q1', 'LO_frequency');
    typeOnly(win, lo, '6.6');
    await tick();
    ok(flagged(lo) === 'err', 'W2: LO 6.6 GHz flags its cell');
    ok(/different LOs/.test(panelText(win)), 'W2: the panel follows the typed LO (got "' +
      panelText(win) + '")');
    ok(G.state.spec.populate.resonator.q1.LO_frequency === 6.6e9,
      'W2: the typed LO is not re-solved over');
    typeOnly(win, lo, String(good / 1e9));
    await tick();
    ok(flagged(lo) === null, 'W2: the good LO clears its cell on the keystroke');
    ok(panelText(win) === '', 'W2: ...and the panel (got "' + panelText(win) + '")');
  }

  // W3: a coupled cell fixed through the OTHER field. Band 1 set on q1's row
  // while its LO is 7.2 GHz -> the band cell warns. Retyping the LO (not
  // the band) into band 1's range clears the band cell on the keystroke.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '5.2');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '5.3');
    setInput(win, cell(win, 'resonator', 'q1', 'LO_frequency'), '7.2');
    const band = cell(win, 'resonator', 'q1', 'band');
    setInput(win, band, '1');
    ok(flagged(band) === 'warn', 'W3: band 1 under a 7.2 GHz LO warns (got ' + flagged(band) + ')');
    typeOnly(win, cell(win, 'resonator', 'q1', 'LO_frequency'), '5.25');
    await tick();
    ok(flagged(band) === null, 'W3: fixing the LO clears the BAND cell on the keystroke (got ' +
      flagged(band) + ')');
  }

  // W4: feedline sum|amp| fixed through a SIBLING tone. q3's commit pushes
  // the bank to 1.2 -> every tone red + panel CLIP. Lowering q1 (the other
  // field) clears q3's flag and the panel on the keystroke -- and after the
  // commit too (q3's flag used to survive q1's commit: only the typed cell
  // was re-validated).
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.5');
    setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.4');
    setInput(win, cell(win, 'resonator', 'q3', 'readout_amplitude'), '0.3');
    ok(ampFlags(win)[2] === 'err' && /CLIP/.test(panelText(win)), 'W4: sum 1.2 -> q3 red + CLIP');
    ok(ampFlags(win).join() === 'err,err,err',
      'W4: every tone of the clipping bank is flagged (got ' + ampFlags(win) + ')');
    typeOnly(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.2');
    await tick();
    ok(ampFlags(win).join() === ',,', 'W4: lowering q1 clears every tone on the keystroke (got ' +
      ampFlags(win) + ')');
    ok(!/CLIP/.test(panelText(win)), 'W4: ...and the panel CLIP (got "' + panelText(win) + '")');
    setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.2');
    ok(ampFlags(win).join() === ',,', 'W4: the commit keeps them clear (got ' + ampFlags(win) + ')');
  }

  // W4b: the COMMIT alone (synchronously, before any debounce timer can
  // fire) re-judges every coupled cell -- blur is authoritative.
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'resonator', 'q1', 'readout_amplitude'), '0.5');
    setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.4');
    setInput(win, cell(win, 'resonator', 'q3', 'readout_amplitude'), '0.3');
    await tick();
    setInput(win, cell(win, 'resonator', 'q2', 'readout_amplitude'), '0.1');
    ok(ampFlags(win).join() === ',,' && !/CLIP/.test(panelText(win)),
      'W4b: a sibling commit clears every tone at once (got ' + ampFlags(win) + ')');
  }

  // W5: FSP typed in dBm display mode. +2 dBm committed at FSP 10 (amp
  // 0.398). Typing FSP 0 must re-display the amp cell (-8 dBm, same amp)
  // before re-judging it -- never read the stale "+2" against the new FSP
  // (that would claim amp 1.26 > 1, a false "Unreachable").
  {
    const win = makeWorld();
    const G = buildWizard(win);
    G.QT.setValidateDebounce(0);
    setInput(win, cell(win, 'qubit', 'q1', 'full_scale_power_dbm'), '10');
    setInput(win, ampUnitSelect(win), 'dBm');
    const amp = cell(win, 'pulses', 'q1', 'x180_amplitude');
    setInput(win, amp, '2');
    ok(flagged(amp) === null, 'W5: +2 dBm at FSP 10 is reachable');
    typeOnly(win, cell(win, 'qubit', 'q1', 'full_scale_power_dbm'), '0');
    await tick();
    const amp2 = cell(win, 'pulses', 'q1', 'x180_amplitude');
    ok(flagged(amp2) === null, 'W5: typing FSP 0 raises no false Unreachable (got ' +
      flagged(amp2) + ': ' + amp2.title + ')');
    ok(Math.abs(parseFloat(amp2.value) - (-8)) < 0.5,
      'W5: the amp cell re-displays at the typed FSP (got ' + amp2.value + ')');
  }

  // W6: real debounce, and a text that is not a number yet never re-solves
  // the LOs (its commit restores the stored RF, QA r2-19).
  {
    const win = makeWorld();
    const G = buildWizard(win);          // real 250 ms debounce
    setInput(win, cell(win, 'resonator', 'q1', 'RF_freq'), '7.1');
    setInput(win, cell(win, 'resonator', 'q2', 'RF_freq'), '7.2');
    setInput(win, cell(win, 'resonator', 'q3', 'RF_freq'), '8.0');
    typeOnly(win, cell(win, 'resonator', 'q3', 'RF_freq'), '7.3');
    ok(/IF window/.test(panelText(win)), 'W6: nothing re-derived synchronously (debounced)');
    await tick(350);
    ok(panelText(win) === '', 'W6: the panel follows after the debounce window');
    setInput(win, cell(win, 'resonator', 'q3', 'RF_freq'), '7.3');
    // A hand-typed LO (inside the window) must survive junk typed into an
    // RF cell: the junk's commit stores nothing, so nothing may re-solve.
    setInput(win, cell(win, 'resonator', 'q1', 'LO_frequency'), '7.25');
    await tick(350);
    ok(G.state.spec.populate.resonator.q1.LO_frequency === 7.25e9, 'W6: hand LO stored');
    typeOnly(win, cell(win, 'resonator', 'q3', 'RF_freq'), 'abc');
    await tick(350);
    ok(G.state.spec.populate.resonator.q1.LO_frequency === 7.25e9,
      'W6: unparseable text re-solves nothing (q1 LO ' +
      G.state.spec.populate.resonator.q1.LO_frequency + ')');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_warn_follow_selfcheck: all checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
