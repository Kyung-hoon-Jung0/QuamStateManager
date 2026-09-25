// Behavioral check for the wizard's CR wiring plumbing (docs/54):
//  - pinToChannel maps cross_resonance / zz_drive pins to MW-FEM (the old
//    lf_fem fallthrough mis-pinned CR onto LF hardware);
//  - ALLOC_KEY carries the 'cr'/'zz' WiringLineType values (the step-5
//    "Auto-allocated" column showed a dash for CR lines without them);
//  - deriveLines stamps cr_port_mode + emits zz_drive lines under the toggle;
//  - pairPopCols appends the ZZ columns only when the toggle is on;
//  - applyPortCsv installs the CSV payload (qubits, DIRECTED pairs, pins,
//    feedline groups) and flips the chip to fixed_frequency + shared_xy.
//
// Run: node tests/generate_crwiring_selfcheck.cjs   (needs jsdom)
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
const T = win.QuamGen._test;
const state = T.state;

// --- pinToChannel: CR/ZZ are MW drive tones ---------------------------------
ok(T.pinToChannel('1/2/3', 'cross_resonance').kind === 'mw_fem',
   'cross_resonance pin must map to mw_fem');
ok(T.pinToChannel('1/2/3', 'zz_drive').kind === 'mw_fem',
   'zz_drive pin must map to mw_fem');
ok(T.pinToChannel('1/2/3', 'coupler').kind === 'lf_fem',
   'coupler pin stays lf_fem');
ok(T.pinToChannel('1/2/3', 'cross_resonance').out_port === 3,
   'cr pin carries out_port');

// --- ALLOC_KEY: WiringLineType values ---------------------------------------
ok(T.ALLOC_KEY.cross_resonance === 'cr', 'ALLOC_KEY.cross_resonance === cr');
ok(T.ALLOC_KEY.zz_drive === 'zz', 'ALLOC_KEY.zz_drive === zz');

// --- deriveLines: cr_port_mode stamp + zz toggle -----------------------------
// minimal CR chip: MW-FEM, two qubits, one directed pair each way
state.spec.instruments = { controllers: [
  { con: 1, fems: [{ slot: 1, fem: 'mw' }] }], opx_plus: [], octaves: [] };
state.spec.qubits = ['q0', 'q1'];
state.spec.qubit_pairs = [['q0', 'q1'], ['q1', 'q0']];
state.pairGate = 'cr';
state.qubitFlux = false;
state.crPortMode = 'shared_xy';
state.zzEnabled = false;
T.deriveLines();
ok(state.spec.cr_port_mode === 'shared_xy', 'deriveLines stamps cr_port_mode');
let crLines = state.spec.lines.filter(l => l.line === 'cross_resonance');
let zzLines = state.spec.lines.filter(l => l.line === 'zz_drive');
ok(crLines.length === 2, 'one CR line per directed pair (got ' + crLines.length + ')');
ok(zzLines.length === 0, 'no zz lines with the toggle off');

state.zzEnabled = true;
T.deriveLines();
zzLines = state.spec.lines.filter(l => l.line === 'zz_drive');
ok(zzLines.length === 2, 'zz_drive line per pair with the toggle on');

// pairPopCols appends ZZ columns only under the toggle
let cols = T.pairPopCols().map(c => c.field);
ok(cols.indexOf('zz_detuning') !== -1, 'ZZ columns present when zzEnabled');
ok(cols.indexOf('cr_shapes') !== -1, 'cr_shapes column present');
state.zzEnabled = false;
cols = T.pairPopCols().map(c => c.field);
ok(cols.indexOf('zz_detuning') === -1, 'ZZ columns dropped when toggle off');

// CZ chips never see the stamp
state.pairGate = 'cz_tunable';
state.qubitFlux = true;
T.deriveLines();
ok(!('cr_port_mode' in state.spec), 'cz chips carry no cr_port_mode');

// --- applyPortCsv -------------------------------------------------------------
const payload = {
  ok: true,
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                 opx_plus: [], octaves: [] },
  qubits: ['q0', 'q1', 'q2'],
  grid: { q0: '0,0', q1: '1,0', q2: '2,0' },
  qubit_pairs: [['q0', 'q1'], ['q1', 'q0'], ['q1', 'q2'], ['q2', 'q1']],
  pins: {
    q0: { drive: { kind: 'mw_fem', con: 1, slot: 1, out_port: 2 },
          resonator: { kind: 'mw_fem', con: 1, slot: 1, out_port: 1, in_port: 2 } },
    q1: { drive: { kind: 'mw_fem', con: 1, slot: 1, out_port: 3 },
          resonator: { kind: 'mw_fem', con: 1, slot: 1, out_port: 1, in_port: 2 } },
    q2: { drive: { kind: 'mw_fem', con: 1, slot: 1, out_port: 4 },
          resonator: { kind: 'mw_fem', con: 1, slot: 1, out_port: 1, in_port: 2 } }
  },
  feedlines: { q0: 'mux0_0', q1: 'mux0_0', q2: 'mux0_0' },
  warnings: []
};
// QA regenerate-r2-38: the chip before the import pinned lines onto slots the
// CSV's instruments do not have (a Re-generate pins EVERY line) -- a TWPA pump
// on slot 3, a CR drive the CSV does not pin -- and step 3 showed the old
// chassis. "Replaces the ... port pins" must mean it.
state.spec.twpas = [{ id: 'twpa1', qubits: ['qA1', 'q1'] },
                    { id: 'twpa2', qubits: ['#/qubits/q2', '#/qubits/q9'] }];
state.spec.lines = [
  { element: 'twpa1', line: 'twpa_pump',
    channel: { kind: 'mw_fem', con: 1, slot: 3, out_port: 7 } },
  { element: 'q0-q1', line: 'cross_resonance',
    channel: { kind: 'mw_fem', con: 1, slot: 3, out_port: 5 } }];
state.spec.instruments = { controllers: [{ con: 1, fems: [
  { slot: 3, fem: 'mw' }, { slot: 5, fem: 'lf' }] }], opx_plus: [], octaves: [] };
win.document.getElementById('gen-chassis-count').value = '2';
ok(T.applyPortCsv(payload) === true, 'applyPortCsv accepts a valid payload');
{
  const pump = state.spec.lines.find(l => l.element === 'twpa1' && l.line === 'twpa_pump');
  ok(pump && pump.channel == null,
     'r2-38: the TWPA pump is re-allocated, not left on the old slot 3 — got ' +
     JSON.stringify(pump && pump.channel));
  const cr = state.spec.lines.find(l => l.element === 'q0-q1' && l.line === 'cross_resonance');
  ok(cr && cr.channel == null,
     'r2-38: an old pin the CSV does not set is gone too — got ' +
     JSON.stringify(cr && cr.channel));
  ok(JSON.stringify(state.spec.twpas[0].qubits) === '["q1"]',
     'r2-38: TWPA links keep only qubits that still exist — got ' +
     JSON.stringify(state.spec.twpas[0].qubits));
  ok(JSON.stringify(state.spec.twpas[1].qubits) === '["#/qubits/q2"]',
     'r2-38: a re-generated pointer link is judged by its qubit id — got ' +
     JSON.stringify(state.spec.twpas[1].qubits));
  ok(win.document.getElementById('gen-chassis-count').value === '1',
     'r2-38: the chassis count follows the imported instruments');
  const tiles = win.document.getElementById('gen-chassis-list').textContent;
  ok(/MW/.test(tiles) && !/LF/.test(tiles),
     'r2-38: step 3 shows the imported MW-FEM, not the old LF slot — got ' +
     JSON.stringify(tiles.replace(/\s+/g, ' ').slice(0, 200)));
}
ok(state.spec.qubits.join(',') === 'q0,q1,q2', 'qubits installed');
ok(state.spec.qubit_pairs.length === 4, 'directed pairs installed');
ok(state.pairGate === 'cr', 'architecture flipped to CR');
ok(state.crPortMode === 'shared_xy', 'shared_xy port mode set');
ok(state.spec.cr_port_mode === 'shared_xy', 'spec stamp present after import');
const q1drive = state.spec.lines.find(l => l.element === 'q1' && l.line === 'drive');
ok(q1drive && q1drive.channel && q1drive.channel.out_port === 3,
   'drive pin applied from the CSV payload');
const q0rr = state.spec.lines.find(l => l.element === 'q0' && l.line === 'resonator');
ok(q0rr && q0rr.group === 'mux0_0', 'feedline group applied');
ok(q0rr && q0rr.channel && q0rr.channel.in_port === 2, 'readout pin applied');
const popq = state.spec.populate.qubit || {};
ok(popq.q2 && popq.q2.grid_location === '2,0', 'grid_location populated');
// pins survive a later deriveLines pass (the pinned map)
T.deriveLines();
const q1again = state.spec.lines.find(l => l.element === 'q1' && l.line === 'drive');
ok(q1again && q1again.channel && q1again.channel.out_port === 3,
   'CSV pins survive deriveLines');
ok(T.applyPortCsv({ ok: false, errors: ['x'] }) === false,
   'rejected payloads are not applied');

if (fails) {
  console.error(fails + ' failure(s)');
  process.exit(1);
}
console.log('generate_crwiring_selfcheck: all checks passed');
