// docs/301 F31 -- the step-4 QDAC-II band on a chip with no qubit flux line.
//
// The band stays on a fixed-frequency (cross-resonance) chip by design: a
// QDAC can DC-bias fixed-frequency qubits (docs/136 section 18, pinned F1 in
// generate_fluxsource_selfcheck.cjs). What was wrong is what it SAID: every
// per-qubit source picker read "LF-FEM" on a chip that has no LF-FEM line,
// while the chip-level selector already read "None (no DC bias)", and the
// picker still offered "Bias tee", which step 4 then refuses.
//
//  X1  on a fixed-frequency chip each per-qubit picker reads "None" and its
//      bias tee is disabled with a reason; the band says it is optional
//  X2  rendering the band changes nothing in the spec, and the switch to a
//      fixed-frequency chip leaves every qubit's QDAC choice in place
//  X3  switching back to a flux-tunable chip restores "LF-FEM", re-enables
//      the bias tee, and keeps the previous choices
//
// Run: node tests/generate_qdac_fixedfreq_selfcheck.cjs   (needs jsdom)
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
let n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }

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
win.WiringGrid = null;
win.confirm = function () { return true; };
win.fetch = function () { return new win.Promise(function () {}); };
new win.Function(GEN_JS).call(win);

const G = win.QuamGen;
const T = G._test;
const doc = win.document;

const SPEC = {
  network: { host: '1.2.3.4', cluster_name: 'C' },
  instruments: {
    controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }] }],
    opx_plus: [], octaves: []
  },
  qubits: ['q1', 'q2', 'q3'],
  qubit_pairs: [['q1', 'q2'], ['q2', 'q3']],
  twpas: [],
  pair_gate: 'cz_tunable',
  lines: [],
  populate: { qubits: {}, pairs: {} }
};

G.init();
G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'generate' });
const arch = doc.getElementById('gen-chip-arch');
function setArch(v) {
  arch.value = v;
  arch.dispatchEvent(new win.Event('change'));
}
function pick(q) {
  return doc.querySelector('#gen-qdac-list .gen-qdac-row[data-qubit="' + q + '"] select.gen-qdac-source');
}
function opt(q, v) { return pick(q).querySelector('option[value="' + v + '"]'); }

setArch('flux_tunable_coupler');
T.renderQdacBand();
// q2 goes to the QDAC through its own picker (a real change event)
pick('q2').value = 'qdac';
pick('q2').dispatchEvent(new win.Event('change', { bubbles: true }));
ok(T.fluxSourceOf('q2') === 'qdac', 'precondition: q2 is QDAC-biased');
ok(opt('q1', 'opx').textContent === 'LF-FEM', 'precondition: a flux-tunable chip reads LF-FEM');
const qdacBefore = JSON.stringify(G.state.spec.qdac);

// -- X1 --
setArch('fixed_frequency');
ok(G.state.qubitFlux === false, 'precondition: a fixed-frequency chip has no qubit flux');
ok(doc.getElementById('gen-qdac-band').hidden === false,
  'X1: the band stays (a QDAC can bias fixed-frequency qubits)');
['q1', 'q2', 'q3'].forEach(function (q) {
  ok(opt(q, 'opx').textContent === 'None',
    'X1: ' + q + ' picker reads None, not LF-FEM -- got ' + opt(q, 'opx').textContent);
  ok(opt(q, 'tee').disabled === true && !!opt(q, 'tee').title,
    'X1: ' + q + ' bias tee is disabled with a reason');
  ok(opt(q, 'qdac').disabled === false, 'X1: ' + q + ' QDAC stays available');
});
ok(/optional/.test(doc.getElementById('gen-qdac-band-sub').textContent),
  'X1: the band says the bias is optional here -- got ' +
  doc.getElementById('gen-qdac-band-sub').textContent);

// a fresh render of the band (a revisit) says the same
T.renderQdacBand();
ok(opt('q1', 'opx').textContent === 'None' && opt('q1', 'tee').disabled === true,
  'X1: a re-rendered band reads the same');

// -- X2 --
ok(JSON.stringify(G.state.spec.qdac) === qdacBefore,
  'X2: the switch left spec.qdac untouched');
const specBefore = JSON.stringify(G.state.spec);
T.renderFluxSource();
T.renderQdacBand();
ok(JSON.stringify(G.state.spec) === specBefore, 'X2: rendering the band writes nothing');
ok(pick('q2').value === 'qdac', 'X2: q2 still shows QDAC');

// -- X3 --
setArch('flux_tunable_coupler');
ok(T.fluxSourceOf('q2') === 'qdac' && T.fluxSourceOf('q1') === 'opx',
  'X3: the previous choices come back');
ok(JSON.stringify(G.state.spec.qdac) === qdacBefore, 'X3: spec.qdac is what it was');
['q1', 'q2', 'q3'].forEach(function (q) {
  ok(opt(q, 'opx').textContent === 'LF-FEM' && opt(q, 'tee').disabled === false,
    'X3: ' + q + ' reads LF-FEM and offers the bias tee again');
});
ok(doc.getElementById('gen-qdac-band-sub').textContent.indexOf('optional') < 0,
  'X3: and the optional note goes');

if (fails) { console.error(fails + ' of ' + n + ' check(s) failed'); process.exit(1); }
console.log('generate_qdac_fixedfreq_selfcheck: all ' + n + ' checks passed');
process.exit(0);
