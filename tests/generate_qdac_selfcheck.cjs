// Behavioral check for the wizard's QDAC-II bias plumbing:
//  - freshSpec()/hydrateFromSpec normalize spec.qdac (missing/malformed);
//  - toggling a qubit's QDAC checkbox adds/removes its spec.qdac.qubits[qid]
//    entry with defaults;
//  - deriveLines omits the qubit's flux line for a QDAC-biased qubit even
//    when the chip-wide flux flag is on, but keeps it for other qubits on
//    the same chip (the mixed-architecture case: real chips mix QDAC-biased
//    and OPX-flux-tunable qubits);
//  - applyQubitIdMap re-keys spec.qdac.qubits on rename (survives across a
//    qubit-id remap the same way populate.<group> does).
//
// Deliberately NO wiring-diagram/ALLOC_KEY/drop-validation checks — QDAC has
// no step-5 drag&drop surface (per the UI decision: simple per-qubit fields,
// auto-allocated trigger port, no diagram integration).
//
// Run: node tests/generate_qdac_selfcheck.cjs   (needs jsdom)
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

const win = makeWorld();
const G = win.QuamGen;
const T = G._test;

const SPEC = {
  network: { host: '1.2.3.4', cluster_name: 'C' },
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }] }],
                 opx_plus: [], octaves: [] },
  qubits: ['q1', 'q2', 'q3'],
  qubit_pairs: [],
  twpas: [],
  pair_gate: 'cz_tunable',
  lines: [],
  populate: { qubits: {}, pairs: {} }
};

// T1: hydrateFromSpec normalizes a missing spec.qdac
G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
const state = G.state;
ok(state.spec.qdac && typeof state.spec.qdac === 'object', 'T1: spec.qdac present after hydrate');
ok(state.spec.qdac.qubits && typeof state.spec.qdac.qubits === 'object', 'T1: spec.qdac.qubits present');
ok(Object.keys(state.spec.qdac.qubits).length === 0, 'T1: no qubits biased by default');

// T1b: a malformed spec.qdac (old sidecar with no .qubits map) is normalized too
const SPEC2 = JSON.parse(JSON.stringify(SPEC));
SPEC2.qdac = { communication_type: 'Ethernet', ip_address: '5.6.7.8' };  // no .qubits
G.hydrateFromSpec(SPEC2, { mode: 'regenerate' });
ok(state.spec.qdac.ip_address === '5.6.7.8', 'T1b: existing qdac fields preserved');
ok(state.spec.qdac.qubits && typeof state.spec.qdac.qubits === 'object',
  'T1b: missing .qubits map filled in');

// Reset to the clean SPEC for the rest of the checks.
G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });

// T2: marking a qubit QDAC-biased (simulating the checkbox handler) adds a
// defaulted entry; isQdacBiased reflects it.
ok(T.isQdacBiased('q2') === false, 'T2: q2 not biased initially');
state.spec.qdac.qubits.q2 = T.qdacDefaults();
state.spec.qdac.qubits.q2.channel = 7;
ok(T.isQdacBiased('q2') === true, 'T2: q2 biased after adding an entry');
ok(T.isQdacBiased('q1') === false, 'T2: q1 (untouched) still not biased');

// T3: deriveLines omits the flux line for the QDAC-biased qubit but keeps it
// for the others — the mixed-architecture case (real chips mix QDAC-biased
// and OPX-flux-tunable qubits on one chip).
state.qubitFlux = true;   // chip-wide flux wanted (cz_tunable pair_gate implies it too)
T.deriveLines();
function fluxLines() {
  return state.spec.lines.filter(function (l) { return l.line === 'flux'; });
}
var flux = fluxLines();
ok(!flux.some(function (l) { return l.element === 'q2'; }),
  'T3: QDAC-biased q2 gets NO flux line');
ok(flux.some(function (l) { return l.element === 'q1'; }),
  'T3: non-biased q1 still gets its flux line');
ok(flux.some(function (l) { return l.element === 'q3'; }),
  'T3: non-biased q3 still gets its flux line');

// T4: survives REPEATED derives (every count/rename/gate edit re-derives)
T.deriveLines();
T.deriveLines();
ok(!fluxLines().some(function (l) { return l.element === 'q2'; }),
  'T4: q2 stays flux-less across repeated derives');
ok(T.isQdacBiased('q2') === true, 'T4: q2 stays biased across repeated derives');

// T5: unbiasing (checkbox unchecked) restores the flux line on next derive.
delete state.spec.qdac.qubits.q2;
T.deriveLines();
ok(fluxLines().some(function (l) { return l.element === 'q2'; }),
  'T5: unbiased q2 gets its flux line back');

// T6: applyQubitIdMap re-keys spec.qdac.qubits on rename (same pattern as
// populate.<group>) — a renamed qubit doesn't silently lose its QDAC fields.
state.spec.qdac.qubits.q3 = T.qdacDefaults();
state.spec.qdac.qubits.q3.channel = 9;
T.applyQubitIdMap({ q3: 'qNew' });
ok(!state.spec.qdac.qubits.q3, 'T6: old key q3 removed after rename');
ok(state.spec.qdac.qubits.qNew && state.spec.qdac.qubits.qNew.channel === 9,
  'T6: renamed key qNew carries the same channel value');

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_qdac_selfcheck: all checks passed');
process.exit(0);
