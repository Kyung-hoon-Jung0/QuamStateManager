// Behavioral check for CZ automatic control/target orientation
// (generate.js czAutoOrient / flipPairOrder / markPairManual).
//
// Customer requirement: for CZ gates the pair roles follow the physics —
// the HIGHER-RF_freq qubit is the control, the lower the target,
// automatically. Pairs are drawn in step 4 before frequencies exist, so the
// orientation re-runs on every qubit RF_freq commit and flips the STORED
// spec pair, dragging along the populate bucket, the moving_qubit role,
// pinned wiring lines, and allocation keys. CR pairs never flip.
//
// Run: node tests/generate_czorder_selfcheck.cjs   (needs jsdom)
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
const TOPO_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'topo-graph.js'), 'utf8');

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
  new win.Function(TOPO_JS).call(win);   // real quamPairId (allocation remap)
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

// A CZ-tunable world: 1 MW-FEM + 1 LF-FEM, 3 qubits, pairs q1-q2 + q2-q3.
function buildCzWizard(win, gate) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = gate === 'cz_fixed' ? 'flux_tunable_fixed_coupler' : 'flux_tunable_coupler';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  G.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q3']];
  G.state.pairsTouched = true;
  G.goToStep(6);
  G.QT = win.QuamGen._test;
  return G;
}

function pairIds(G) {
  return G.state.spec.qubit_pairs.map(function (p) { return p[0] + '-' + p[1]; });
}

// E1: the core flip — target typed higher than control reorders the pair,
// and the populate bucket + moving_qubit role follow the id.
(function coreFlip() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  ok(G.state.pairGate === 'cz_tunable', 'E1: world is a CZ-tunable chip');

  // Seed a pair bucket first so we can watch it follow the flip.
  G.state.spec.populate.pairs = {
    'q1-q2': { cz_amplitude: 0.123, moving_qubit: 'control' }
  };
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '4.8');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.2');   // q2 > q1 → flip
  ok(pairIds(G)[0] === 'q2-q1', 'E1: pair flipped to q2-q1 (got ' + pairIds(G)[0] + ')');
  const bucket = G.state.spec.populate.pairs['q2-q1'];
  ok(!!bucket && !G.state.spec.populate.pairs['q1-q2'],
    'E1: populate bucket followed the key');
  ok(bucket && bucket.cz_amplitude === 0.123, 'E1: bucket values preserved');
  ok(bucket && bucket.moving_qubit === 'target',
    'E1: moving_qubit role swapped — same PHYSICAL qubit (q1) keeps the pulse');
  // The message + note surfaced.
  const note = win.document.getElementById('gen-pop-pair-order-note');
  ok(note && !note.hidden && note.textContent.indexOf('higher RF-freq') >= 0,
    'E1: pair-order note rendered');
  const msg = win.document.getElementById('gen-message');
  ok(msg && !msg.hidden && msg.textContent.indexOf('reordered') >= 0,
    'E1: reorder message shown');
})();

// E2: no-flip cases — equal, missing, already-correct, manual.
(function noFlipCases() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');

  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '5.0');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.0');   // equal
  ok(pairIds(G)[0] === 'q1-q2', 'E2: equal frequencies never flip');
  ok(pairIds(G)[1] === 'q2-q3', 'E2: missing frequency (q3) never flips');

  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '5.4');   // correct order
  ok(pairIds(G)[0] === 'q1-q2', 'E2: already-correct order untouched');

  // Manual pin: q3 typed higher than q2 but cz_order=manual holds the order.
  G.state.spec.populate.pairs = { 'q2-q3': { cz_order: 'manual' } };
  setInput(win, cell(win, 'qubit', 'q3', 'RF_freq'), '6.0');
  ok(pairIds(G)[1] === 'q2-q3', 'E2: manual pair never auto-flips');
  const st = G.QT.czOrderStatus();
  ok(st[1] && st[1].status === 'manual', 'E2: status reports manual');
})();

// E3: flip-back — frequencies edited the other way re-flip, values intact.
(function flipBack() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  G.state.spec.populate.pairs = { 'q1-q2': { cz_amplitude: 0.2 } };
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '4.8');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.2');
  ok(pairIds(G)[0] === 'q2-q1', 'E3: flipped');
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '5.6');   // now q1 higher
  ok(pairIds(G)[0] === 'q1-q2', 'E3: flipped back when frequencies reversed');
  const bucket = G.state.spec.populate.pairs['q1-q2'];
  ok(bucket && bucket.cz_amplitude === 0.2, 'E3: values survived both flips');
})();

// E4: wiring lines + allocation keys are remapped in place (pinned channels
// and step-5 allocation survive the flip without a re-allocate).
(function keysFollow() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  // Pin a coupler channel + hand-craft an allocation in both key forms.
  const pin = { kind: 'lf_fem', con: 1, out_slot: 2, out_port: 5 };
  G.state.spec.lines.forEach(function (ln) {
    if (ln.element === 'q1-q2' && ln.line === 'coupler') ln.channel = pin;
  });
  G.state.allocation = {
    'q1-2': { coupler: [{ con: 1, slot: 2, port: 5, io_type: 'output' }] },
    q1: {}, q2: {}, q3: {}
  };
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '4.8');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.2');   // flip
  const line = G.state.spec.lines.find(function (ln) {
    return ln.line === 'coupler' && ln.element === 'q2-q1';
  });
  ok(!!line, 'E4: coupler line element renamed to q2-q1');
  ok(line && line.channel && line.channel.out_port === 5,
    'E4: pinned channel survived deriveLines through the rename');
  ok(!G.state.allocation['q1-2'] && !!G.state.allocation['q2-1'],
    'E4: allocation QUAM key remapped q1-2 → q2-1');
})();

// E5: CR chips never flip, and the CZ note stays hidden.
(function crNeverFlips() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  // Switch to a CR chip (fixed-frequency + CR gate).
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'fixed_frequency';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(G.state.pairGate === 'cr', 'E5: CR world (got ' + G.state.pairGate + ')');
  G.goToStep(6);
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '4.8');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.2');
  ok(pairIds(G)[0] === 'q1-q2', 'E5: CR pair kept its drawn direction');
  const note = win.document.getElementById('gen-pop-pair-order-note');
  ok(note && note.hidden, 'E5: CZ order note hidden on a CR chip');
})();

// E6: step-4 dropdown edit marks the pair manual (hand-picked order wins).
(function dropdownMarksManual() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  G.goToStep(4);
  const row = win.document.querySelectorAll('#gen-pair-list .gen-pair-row:not(.gen-pair-head)')[0];
  ok(!!row, 'E6: pair row rendered');
  const tSel = row.querySelector('.gen-pair-t');
  tSel.value = 'q3';                                   // q1-q2 → q1-q3 by hand
  tSel.dispatchEvent(new win.Event('change', { bubbles: true }));
  const bucket = (G.state.spec.populate.pairs || {})['q1-q3'];
  ok(!!bucket && bucket.cz_order === 'manual', 'E6: hand-edited pair marked manual');
  // And the CZ hint is visible on the step-4 list.
  ok(!!win.document.querySelector('#gen-pair-list .gen-pair-cz-hint'),
    'E6: step-4 CZ auto-orientation hint rendered');
  // Frequencies that would flip it now do nothing.
  G.goToStep(6);
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '4.8');
  setInput(win, cell(win, 'qubit', 'q3', 'RF_freq'), '5.2');
  ok(pairIds(G)[0] === 'q1-q3', 'E6: manual pair held its order');
})();

// E7: draft-restore path — a stale order in the spec is corrected on the
// populate render (czAutoOrient runs in renderPopulateTables).
(function draftRestore() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  G.state.spec.populate.qubit = {
    q1: { RF_freq: 4.8e9 }, q2: { RF_freq: 5.2e9 }
  };
  G.goToStep(5); G.goToStep(6);   // re-render from spec, no keystrokes
  ok(pairIds(G)[0] === 'q2-q1', 'E7: stale draft order corrected on render');
})();

// E8: review step — the orientation summary row appears for CZ chips.
(function reviewRow() {
  const win = makeWorld();
  const G = buildCzWizard(win, 'cz_tunable');
  setInput(win, cell(win, 'qubit', 'q1', 'RF_freq'), '5.4');
  setInput(win, cell(win, 'qubit', 'q2', 'RF_freq'), '5.0');
  G.goToStep(8);
  const review = win.document.getElementById('gen-review').textContent;
  ok(review.indexOf('CZ pair orientation') >= 0, 'E8: review row present');
  ok(review.indexOf('1 auto') >= 0 && review.indexOf('1 pending') >= 0,
    'E8: review row counts auto + pending (got: ' +
    review.slice(review.indexOf('CZ pair orientation'), review.indexOf('CZ pair orientation') + 90) + ')');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_czorder_selfcheck: all checks passed');
