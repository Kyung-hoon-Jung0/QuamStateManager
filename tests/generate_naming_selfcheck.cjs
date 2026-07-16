// Behavioral check for the step-4 qubit naming scheme
// (generate.js schemeNames / applyNamingScheme / renameQubit / applyQubitIdMap).
//
// Customer requirement: the wizard must let users pick their own notation —
// q0, q1, … or qA1, qA2, qB1, … — via scheme presets AND per-qubit rename.
// A rename remaps populate values, pairs, and TWPA lists in ONE pass
// (applyQubitIdMap); hand-renamed sets detach from the scheme (namesTouched)
// so the contiguity gate and count changes keep them.
//
// Run: node tests/generate_naming_selfcheck.cjs   (needs jsdom)
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

function buildWizard(win, nQubits) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), String(nQubits));
  G.QT = win.QuamGen._test;
  return G;
}

// F1: zero_based scheme apply — the whole identity web remaps in one pass.
(function zeroBasedApply() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  ok(JSON.stringify(G.state.spec.qubits) === '["q1","q2","q3"]',
    'F1: default scheme is the historical q1…qN');
  // Seed values + a pair bucket + a TWPA so the remap is observable.
  G.state.spec.populate.qubit = { q1: { RF_freq: 5.0e9 } };
  G.state.spec.populate.pairs = { 'q1-q2': { cz_amplitude: 0.1 } };
  G.state.spec.twpas = [{ id: 'twpaA', qubits: ['q1', 'q2', 'q3'] }];
  G.state.allocation = { q1: {} };

  G.state.naming.preset = 'zero_based';
  G.QT.applyNamingScheme();
  ok(JSON.stringify(G.state.spec.qubits) === '["q0","q1","q2"]',
    'F1: zero_based names applied (got ' + JSON.stringify(G.state.spec.qubits) + ')');
  ok(G.state.spec.populate.qubit.q0 && G.state.spec.populate.qubit.q0.RF_freq === 5.0e9,
    'F1: populate bucket followed q1→q0');
  ok(!!G.state.spec.populate.pairs['q0-q1'], 'F1: pair bucket key remapped');
  ok(JSON.stringify(G.state.spec.twpas[0].qubits) === '["q0","q1","q2"]',
    'F1: TWPA qubit list remapped (the old renumber missed this)');
  ok(JSON.stringify(G.state.spec.qubit_pairs[0]) === '["q0","q1"]',
    'F1: pair entries remapped');
  ok(G.state.allocation === null, 'F1: stale allocation dropped');
  ok(G.state.namesTouched === false, 'F1: scheme apply re-arms the expectation');
})();

// F2: custom prefix + start.
(function customApply() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  G.state.naming = { preset: 'custom', prefix: 'qb', start: 10 };
  G.QT.applyNamingScheme();
  ok(JSON.stringify(G.state.spec.qubits) === '["qb10","qb11","qb12"]',
    'F2: custom prefix+start applied (got ' + JSON.stringify(G.state.spec.qubits) + ')');
  // Count change regenerates from the scheme.
  setInput(win, win.document.getElementById('gen-qubit-count'), '4');
  ok(JSON.stringify(G.state.spec.qubits) === '["qb10","qb11","qb12","qb13"]',
    'F2: count grow follows the custom scheme');
  // An ILLEGAL prefix is rejected with the values untouched.
  G.state.naming = { preset: 'custom', prefix: 'x', start: 1 };
  G.QT.applyNamingScheme();
  ok(G.state.spec.qubits[0] === 'qb10', 'F2: illegal prefix rejected (names kept)');
  const msg = win.document.getElementById('gen-message');
  ok(msg && !msg.hidden && /invalid/i.test(msg.textContent),
    'F2: illegal prefix shows the name-rule message');
})();

// F3: grid preset — board-derived letters; blocked while any qubit unplaced.
(function gridApply() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  // Unplaced → error, no rename.
  G.state.naming.preset = 'grid';
  G.QT.applyNamingScheme();
  ok(G.state.spec.qubits[0] === 'q1', 'F3: unplaced board blocks the grid preset');
  // Place: q1 at col0,row0 (A1), q2 at col1,row0 (A2), q3 at col0,row1 (B1).
  G.state.spec.populate.qubit = {
    q1: { grid_location: '0,0' },
    q2: { grid_location: '1,0' },
    q3: { grid_location: '0,1' }
  };
  G.QT.applyNamingScheme();
  ok(JSON.stringify(G.state.spec.qubits) === '["qA1","qA2","qB1"]',
    'F3: grid names applied (got ' + JSON.stringify(G.state.spec.qubits) + ')');
  ok(G.state.spec.populate.qubit.qA1.grid_location === '0,0',
    'F3: grid_location followed the rename');
  // Grid is one-shot: no standing expectation → no renumber gate.
  ok(G.QT.expectedNamesOrNull() === null, 'F3: grid preset has no standing expectation');
})();

// F4: per-qubit rename — valid renames remap; invalid ones restore the input.
(function perQubitRename() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  G.state.spec.populate.qubit = { q2: { RF_freq: 6.1e9 } };

  const err0 = G.QT.renameQubit('q2', 'qB7');
  ok(err0 === null, 'F4: valid rename accepted');
  ok(G.state.spec.qubits[1] === 'qB7', 'F4: qubit list renamed');
  ok(G.state.spec.populate.qubit.qB7.RF_freq === 6.1e9, 'F4: values followed');
  ok(G.state.namesTouched === true, 'F4: hand rename detaches the scheme');

  ['x1', 'q-a', 'q 1', 'q'].forEach(function (bad) {
    ok(G.QT.renameQubit('q1', bad) !== null, 'F4: "' + bad + '" rejected');
  });
  ok(G.QT.renameQubit('q1', 'qB7') !== null, 'F4: duplicate rejected');
  ok(G.state.spec.qubits[0] === 'q1', 'F4: failed renames leave the name');

  // The DOM inputs restore on an invalid edit.
  const inputs = win.document.querySelectorAll('#gen-qubit-name-list input');
  ok(inputs.length === 3, 'F4: one rename input per qubit (got ' + inputs.length + ')');
  setInput(win, inputs[0], 'q-broken');
  const inputsAfter = win.document.querySelectorAll('#gen-qubit-name-list input');
  ok(inputsAfter[0].value === 'q1', 'F4: invalid DOM edit restores the old name');
  setInput(win, inputsAfter[2], 'qC3');
  ok(G.state.spec.qubits[2] === 'qC3', 'F4: DOM rename lands in the spec');
})();

// F5: the renumber gate — fires on default-scheme holes, silent after a
// hand rename (namesTouched detaches the expectation).
(function renumberGate() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  // Hole under the default scheme (q1,q3,q4): decline → blocked on step 4.
  // (Clear the auto-chain pairs — the dangling-pair guard runs before the
  // ID-gate and would mask it.)
  G.state.spec.qubits = ['q1', 'q3', 'q4'];
  G.state.spec.qubit_pairs = [];
  G.state.pairsTouched = true;
  win.confirm = function () { return false; };
  G.tryNext();
  ok(G.state.step === 4, 'F5: declined renumber keeps the user on step 4');
  ok(G.state.spec.qubits[1] === 'q3', 'F5: declined renumber mutates nothing');
  // Accept → renamed onto the scheme.
  win.confirm = function () { return true; };
  G.tryNext();
  ok(JSON.stringify(G.state.spec.qubits) === '["q1","q2","q3"]',
    'F5: accepted renumber closes the holes');
  // Hand-renamed set: no expectation → the same holes pass the gate.
  G.goToStep(4);
  G.QT.renameQubit('q3', 'qZ9');
  win.confirm = function () { throw new Error('gate must not fire'); };
  G.tryNext();
  ok(G.state.step === 5, 'F5: hand-renamed set passes the gate without a prompt');
})();

// F6: draft roundtrip — naming + namesTouched survive save/restore.
(function draftRoundtrip() {
  const win = makeWorld();
  const G = buildWizard(win, 2);
  G.state.naming = { preset: 'custom', prefix: 'qx', start: 3 };
  G.QT.applyNamingScheme();
  G.QT.renameQubit('qx3', 'qHero');
  G.goToStep(5);   // saveDraft runs on nav
  const draft = JSON.parse(win.sessionStorage.getItem('quam_generate_draft'));
  ok(draft.naming && draft.naming.prefix === 'qx' && draft.naming.start === 3,
    'F6: naming persisted in the draft');
  ok(draft.namesTouched === true, 'F6: namesTouched persisted');
  ok(JSON.stringify(draft.spec.qubits) === '["qHero","qx4"]',
    'F6: renamed qubits persisted');
})();

// F7: count changes on a hand-renamed set append collision-free fallbacks
// and truncate from the end — never regenerate the whole set.
(function countChanges() {
  const win = makeWorld();
  const G = buildWizard(win, 3);
  G.QT.renameQubit('q2', 'qB5');
  setInput(win, win.document.getElementById('gen-qubit-count'), '5');
  const qs = G.state.spec.qubits;
  ok(qs[0] === 'q1' && qs[1] === 'qB5' && qs[2] === 'q3',
    'F7: existing names kept on grow (got ' + JSON.stringify(qs) + ')');
  ok(qs.length === 5 && qs[3] === 'q2' && qs[4] === 'q4',
    'F7: grow appends the free q<k> fallbacks (got ' + JSON.stringify(qs) + ')');
  setInput(win, win.document.getElementById('gen-qubit-count'), '2');
  ok(JSON.stringify(G.state.spec.qubits) === '["q1","qB5"]',
    'F7: shrink truncates from the end');
})();

// F8: regenerate mode — naming block hidden, names never touched by the gate.
(function regenerateMode() {
  const win = makeWorld();
  const G = buildWizard(win, 2);
  win.QuamGen.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'c' },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }], opx_plus: [], octaves: [] },
    qubits: ['qA1', 'qB2'], qubit_pairs: [], twpas: [], lines: [],
    populate: { qubit: {}, resonator: {}, flux: {}, pulses: {}, pairs: {} }
  }, { mode: 'regenerate', step: 4 });
  ok(G.state.namesTouched === true, 'F8: hydrate detaches the scheme');
  ok(G.QT.expectedNamesOrNull() === null, 'F8: no expectation in regenerate');
  const block = win.document.getElementById('gen-naming');
  ok(block && block.hidden, 'F8: naming block hidden in regenerate mode');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_naming_selfcheck: all checks passed');
