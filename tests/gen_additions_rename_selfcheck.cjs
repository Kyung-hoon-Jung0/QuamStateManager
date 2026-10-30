// Z2 + C3: a qubit rename in the wizard rewrites spec.additions, EXECUTED under jsdom.
//
//  R1  renaming q2 -> qA moves a rule-named instance whole: the pulse's target and
//      name, the gate's target, the pointer naming the pulse, the default gate's `to`
//  R2  a token inside another id (q1 in q10) and anything below extras stay as they are
//  R3  a swap stays a swap
//  R4  a spec with no additions gets none
//  R5  every pointer of the renamed additions still names an addition
//
// `node tests/gen_additions_rename_selfcheck.cjs parity <cases.json>` prints
// GenAdditions.renameQubits for each case instead (the pytest compares it with
// additions.rename_qubits, the Python twin).
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', p), 'utf8');
const HTML = read('web/templates/_generate.html');
const GEN_JS = read('web/static/generate.js');
const TOPO_JS = read('web/static/topo-graph.js');
const ADD_JS = read('web/static/gen-additions.js');
const J = JSON.stringify;

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }

function makeWorld(full) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' + (full ? HTML : '') +
    '</div></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(TOPO_JS).call(win);
  if (full) new win.Function(GEN_JS).call(win);
  new win.Function(ADD_JS).call(win);
  return win;
}

if (process.argv[2] === 'parity') {
  const win = makeWorld(false);
  const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  const out = cases.map(c => win.GenAdditions.renameQubits(c.adds, c.map, c.qubits, c.pairs));
  process.stdout.write(J(out));
  process.exit(0);
}

const ADDS = () => [
  { kind: 'pulse', target: 'qubits.q2.z.operations', name: 'cz_bm_high_q2_q3', 'class': 'lab.P', fields: { amplitude: 0.1 } },
  { kind: 'pulse', target: 'qubits.q3.z.operations', name: 'cz_bm_low_q2_q3', 'class': 'lab.P', fields: {} },
  { kind: 'macro', target: 'qubit_pairs.q2-3.macros', name: 'cz_bm_q2_q3', 'class': 'lab.G',
    fields: { flux_pulse_high: '#/qubits/q2/z/operations/cz_bm_high_q2_q3',
              flux_pulse_low: '#/qubits/q3/z/operations/cz_bm_low_q2_q3' } },
  { kind: 'default_gate', target: 'qubit_pairs.q2-3.macros', name: 'cz', to: 'cz_bm_q2_q3' },
  { kind: 'pulse', target: 'qubits.q10.xy.operations', name: 'x_q10', 'class': 'lab.P', fields: {} },
  { kind: 'field', target: 'qubits.q2.extras.note_q2', value: 'q2 stays q2 in free text' },
  { kind: 'field', target: 'extras.ref_q2', value: '#/qubits/q2/extras/q2' },
];

function hydrate(win, adds) {
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }], opx_plus: [], octaves: [] },
    qubits: ['q1', 'q2', 'q3', 'q10'], qubit_pairs: [['q1', 'q2'], ['q2', 'q3']], twpas: [], pair_gate: 'cz_tunable',
    lines: [], populate: { qubit: {}, pairs: {} },
  }, { mode: 'generate' });
  if (adds) G._test.state.spec.additions = adds;
  return G;
}

function pointersLand(adds) {
  const have = {};
  adds.forEach(a => { if (a.kind === 'pulse' || a.kind === 'macro') have['#/' + a.target.split('.').join('/') + '/' + a.name] = 1; });
  const bad = [];
  adds.forEach(a => Object.values(a.fields || {}).forEach(v => {
    if (typeof v === 'string' && v.indexOf('#/qubits/') === 0 && /\/operations\//.test(v) && !have[v]) bad.push(v);
  }));
  return bad;
}

let win = makeWorld(true);
let G = hydrate(win, ADDS());
G._test.applyQubitIdMap({ q2: 'qA' });
let a = G._test.state.spec.additions;
ok(a[0].target === 'qubits.qA.z.operations' && a[0].name === 'cz_bm_high_qA_q3', 'R1 pulse moved: ' + J(a[0]));
ok(a[1].target === 'qubits.q3.z.operations' && a[1].name === 'cz_bm_low_qA_q3', 'R1 partner pulse name: ' + J(a[1]));
ok(a[2].target === 'qubit_pairs.qA-3.macros' && a[2].name === 'cz_bm_qA_q3', 'R1 gate target and name: ' + J(a[2]));
ok(a[2].fields.flux_pulse_high === '#/qubits/qA/z/operations/cz_bm_high_qA_q3', 'R1 pointer follows: ' + a[2].fields.flux_pulse_high);
ok(a[2].fields.flux_pulse_low === '#/qubits/q3/z/operations/cz_bm_low_qA_q3', 'R1 partner pointer follows: ' + a[2].fields.flux_pulse_low);
ok(a[3].target === 'qubit_pairs.qA-3.macros' && a[3].to === 'cz_bm_qA_q3' && a[3].name === 'cz', 'R1 default gate: ' + J(a[3]));
ok(J(G._test.state.spec.qubit_pairs) === J([['q1', 'qA'], ['qA', 'q3']]), 'R1 the pairs themselves renamed as before');
ok(a[4].target === 'qubits.q10.xy.operations' && a[4].name === 'x_q10', 'R2 q10 untouched: ' + J(a[4]));
ok(a[5].target === 'qubits.qA.extras.note_q2' && a[5].value === 'q2 stays q2 in free text', 'R2 below extras verbatim: ' + J(a[5]));
ok(a[6].target === 'extras.ref_q2' && a[6].value === '#/qubits/qA/extras/q2', 'R2 a pointer into extras renames its owner only: ' + J(a[6]));
ok(pointersLand(a).length === 0, 'R5 every pointer names an addition: ' + pointersLand(a));

win = makeWorld(true);
G = hydrate(win, ADDS());
G._test.applyQubitIdMap({ q2: 'q3', q3: 'q2' });
a = G._test.state.spec.additions;
ok(a[0].target === 'qubits.q3.z.operations' && a[0].name === 'cz_bm_high_q3_q2', 'R3 swap: ' + J(a[0]));
ok(a[2].target === 'qubit_pairs.q3-2.macros' && a[2].fields.flux_pulse_low === '#/qubits/q2/z/operations/cz_bm_low_q3_q2', 'R3 swap pointers: ' + J(a[2]));
ok(pointersLand(a).length === 0, 'R5 swap pointers land: ' + pointersLand(a));

win = makeWorld(true);
G = hydrate(win, null);
G._test.applyQubitIdMap({ q2: 'qA' });
ok(!('additions' in G._test.state.spec), 'R4 no additions key created');

console.log((fails ? 'FAILED' : 'OK') + ' gen_additions_rename_selfcheck: ' + asserts + ' assertions, ' + fails + ' failed');
process.exit(fails ? 1 : 0);
