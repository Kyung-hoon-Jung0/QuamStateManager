/* docs/241 -- "Reset step" resets ONLY the step on screen (customer ask: the
 * old Reset took the whole wizard back to the Environment page).
 *
 * Pins:
 *  R1. Network: cleared; stays on step 2; env, qubits, populate untouched.
 *  R2. Populate: values cleared; board placement, CZ orientation and the
 *      QDAC channel assignment (step-4 decisions) kept; qubits kept.
 *  R3. Wiring: pins + allocation cleared; qubits + populate kept.
 *  R4. Output: folder cleared; populate kept.
 *  R5. Qubits: the qubit set cleared; network + chassis kept.
 *  R6. Ctrl+Z puts the reset step back.
 *  R7. The button is disabled where there is nothing to reset (1, 8).
 *  R8. Re-generate: a step resets to the SOURCE chip's values, and the
 *      cells the user had touched stop counting as touched.
 *  R9. Start over still exists and still goes back to step 1, env kept.
 *
 * Run:  node tests/generate_reset_step_selfcheck.cjs
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
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function world() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  const G = win.QuamGen;
  G.init();
  const S = G.state;
  S.env = '/envs/lab/python';
  S.spec.network = { host: '10.0.0.5', cluster_name: 'C1', port: 9510 };
  S.spec.instruments.controllers = [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }];
  S.spec.qubits = ['q1', 'q2'];
  S.spec.qubit_pairs = [['q1', 'q2']];
  S.spec.populate = {
    qubit: { q1: { grid_location: '0,0', RF_freq: 5.1e9 }, q2: { grid_location: '1,0', RF_freq: 5.3e9 } },
    pairs: { 'q1-q2': { cz_order: 'manual', cz_amplitude: 0.12 } },
    resonator: { q1: { RF_freq: 7.1e9 } }
  };
  S.spec.qdac.qubits = { q2: { channel: 3, trigger_port: 'ext1', dc_offset: 0.2, dwell: 1e-3 } };
  S.spec.lines = [{ element: 'q1', line: 'xy', channel: { con: 1, slot: 1, port: 2 } }];
  S.allocation = null;
  S.outputPath = 'D:/out/chip';
  // the wizard re-reads these boxes on every step change: they must say
  // what the state says, or the fixture itself "resets" them
  const d = win.document;
  d.getElementById('gen-net-host').value = '10.0.0.5';
  d.getElementById('gen-net-cluster').value = 'C1';
  d.getElementById('gen-net-port').value = '9510';
  d.getElementById('gen-qubit-count').value = '2';
  d.getElementById('gen-output-path').value = 'D:/out/chip';
  return { win, G, S, $: (id) => win.document.getElementById(id) };
}
function press(W) { W.$('gen-reset-step').dispatchEvent(new W.win.MouseEvent('click', { bubbles: true })); }
function jump(W, n) { W.G.goToStep(n); }

// R1
let W = world();
jump(W, 2);
press(W);
ok(W.S.step === 2, 'R1 Network reset stays on step 2 (' + W.S.step + ')');
ok(W.S.spec.network.host === '' && W.S.spec.network.port == null, 'R1 network cleared');
ok(W.$('gen-net-host').value === '', 'R1 the host box is empty');
ok(W.S.env === '/envs/lab/python', 'R1 env kept');
ok(W.S.spec.qubits.join() === 'q1,q2' && W.S.spec.populate.resonator.q1.RF_freq === 7.1e9, 'R1 qubits + populate kept');

// R2
W = world();
jump(W, 6);
W.S.spec.populate.qubit.q1.RF_freq = 5.1e9;           // whatever the visit prefilled, this one is ours
W.S.spec.populate.pairs['q1-q2'].cz_amplitude = 0.12;
W.S.spec.qdac.qubits.q2.dc_offset = 0.2;
press(W);
const pop = W.S.spec.populate;
ok(W.S.step === 6, 'R2 Populate reset stays on step 6');
ok(!((pop.qubit || {}).q1 || {}).RF_freq || pop.qubit.q1.RF_freq !== 5.1e9, 'R2 a typed qubit RF is gone');
ok(pop.qubit.q1.grid_location === '0,0' && pop.qubit.q2.grid_location === '1,0', 'R2 board placement kept');
ok(pop.pairs['q1-q2'].cz_order === 'manual', 'R2 CZ orientation kept');
ok(pop.pairs['q1-q2'].cz_amplitude !== 0.12, 'R2 a typed CZ amplitude is gone');
ok(W.S.spec.qdac.qubits.q2.channel === 3 && W.S.spec.qdac.qubits.q2.trigger_port === 'ext1', 'R2 QDAC assignment kept');
ok(W.S.spec.qdac.qubits.q2.dc_offset !== 0.2, 'R2 a typed QDAC bias is gone');
ok(W.S.spec.qubits.join() === 'q1,q2', 'R2 qubits kept');
// R6 on the same world
W.win._wizUndo.tryUndo();
ok(W.S.spec.populate.qubit.q1.RF_freq === 5.1e9 && W.S.spec.qdac.qubits.q2.dc_offset === 0.2,
   'R6 Ctrl+Z puts the Populate values back');

// R3
W = world();
jump(W, 5);
const freshLines = JSON.stringify(W.S.spec.lines);     // what entering Wiring derives on its own
const xy = W.S.spec.lines.filter(function (ln) { return ln.element === 'q1' && ln.line === 'drive'; })[0];
ok(!!xy, 'R3 fixture: q1 has a drive line');
xy.channel = { kind: 'mw_fem', out_port: 5 };            // the user pins it by hand
W.S.wiringTouched = true;
W.S.allocation = { q1: { xy: ['con1-1-5'] } };
press(W);
ok(W.S.step === 5, 'R3 Wiring reset stays on step 5');
ok(JSON.stringify(W.S.spec.lines) === freshLines, 'R3 the lines are back to what Wiring derives on its own (user pin gone)');
ok(W.S.allocation === null && W.S.wiringTouched === false, 'R3 allocation + touched cleared');
ok(W.S.spec.qubits.join() === 'q1,q2' && W.S.spec.populate.resonator.q1.RF_freq === 7.1e9, 'R3 qubits + populate kept');

// R4
W = world();
jump(W, 7);
W.$('gen-output-path').value = 'D:/out/chip';
W.S.outputPath = 'D:/out/chip';
press(W);
ok(W.S.step === 7 && W.S.outputPath === '' && W.$('gen-output-path').value === '', 'R4 output folder cleared, on step 7');
ok(W.S.spec.populate.resonator.q1.RF_freq === 7.1e9, 'R4 populate kept');

// R5
W = world();
jump(W, 4);
press(W);
ok(W.S.step === 4 && W.S.spec.qubits.length === 0, 'R5 Qubits reset empties the qubit set, on step 4');
ok(W.S.spec.network.host === '10.0.0.5', 'R5 network kept');
ok(W.S.spec.instruments.controllers.length === 1 && W.S.spec.instruments.controllers[0].fems.length === 2, 'R5 chassis kept');
ok(W.S.env === '/envs/lab/python', 'R5 env kept');

// R7
W = world();
jump(W, 1);
ok(W.$('gen-reset-step').disabled, 'R7 disabled on Environment');
jump(W, 8);
ok(W.$('gen-reset-step').disabled, 'R7 disabled on Review');
jump(W, 6);
ok(!W.$('gen-reset-step').disabled, 'R7 enabled on Populate');

// R8 Re-generate
W = world();
W.G.hydrateFromSpec({
  network: { host: '10.1.1.1', cluster_name: 'SRC', port: null },
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }], opx_plus: [], octaves: [] },
  qubits: ['qA1', 'qA2'], qubit_pairs: [], twpas: [], lines: [],
  populate: { qubit: { qA1: { RF_freq: 4.9e9 } } }, pair_gate: 'cz_tunable'
}, { mode: 'regenerate', sourcePath: 'D:/chips/src', step: 6 });
jump(W, 6);
W.S.spec.populate.qubit.qA1.RF_freq = 6.0e9;
W.S.regenTouched = { 'qubit|qA1|RF_freq': 1 };
press(W);
ok(W.S.spec.populate.qubit.qA1.RF_freq === 4.9e9, 'R8 Re-generate Populate goes back to the SOURCE value (' + W.S.spec.populate.qubit.qA1.RF_freq + ')');
ok(!W.S.regenTouched || !W.S.regenTouched['qubit|qA1|RF_freq'], 'R8 the reset cell no longer counts as touched');
ok(W.S.mode === 'regenerate' && W.S.sourcePath === 'D:/chips/src', 'R8 still the Re-generate session');
jump(W, 2);
W.S.spec.network.host = '9.9.9.9';
press(W);
ok(W.S.spec.network.host === '10.1.1.1', 'R8 Re-generate Network goes back to the source host');

// R9
W = world();
jump(W, 6);
W.$('gen-reset').dispatchEvent(new W.win.MouseEvent('click', { bubbles: true }));
ok(W.S.step === 1 && W.S.spec.qubits.length === 0, 'R9 Start over still starts over');
ok(W.S.env === '/envs/lab/python', 'R9 and keeps the env');
ok(/Start over/.test(W.$('gen-reset').textContent) && /Reset step/.test(W.$('gen-reset-step').textContent), 'R9 the two buttons are labelled');

console.log(fails ? fails + ' FAILED' : 'all passed');
process.exit(fails ? 1 : 0);
