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

// --- QA F6: a resonator pin names the OUTPUT; the input follows its LO ------
// pinToChannel set in_port = out_port, so '1/1/8' asked for MW input 8 (a
// MW-FEM has inputs 1-2) and allocation failed with NotEnoughChannels.
(function () {
  const r8 = T.pinToChannel('1/1/8', 'resonator');
  ok(r8 && r8.kind === 'mw_fem' && r8.con === 1 && r8.slot === 1 && r8.out_port === 8 && r8.in_port === 2,
     'F6: resonator 1/1/8 -> out 8 + its LO partner in 2 (got ' + JSON.stringify(r8) + ')');
  const r1 = T.pinToChannel('1/1/1', 'resonator');
  ok(r1 && r1.out_port === 1 && r1.in_port === 1, 'F6: resonator 1/1/1 -> out 1 + in 1');
  const r3 = T.pinToChannel('1/1/3', 'resonator');
  ok(r3 && r3.out_port === 3 && !('in_port' in r3),
     'F6: an output with no input LO partner leaves in_port to the allocator');
  ok(T.pinToChannel('1//1', 'resonator') === null, 'F6: a blank segment is still refused');
  // QA review of F6: the readout INPUT is its own cable -- retyping the output
  // pin must not rewire it. Real chips read out on (out 1, in 2) (KRS_5Q) and
  // (out 8, in 1); deriving the input from the new output rewired them.
  const was = state.mode;
  state.mode = 'regenerate';
  const krs = { kind: 'mw_fem', con: 1, slot: 3, out_port: 1, in_port: 2 };
  const moved = T.pinToChannel('1/4/1', 'resonator', krs);
  ok(moved && moved.slot === 4 && moved.out_port === 1 && moved.in_port === 2,
     'F6 review: a pin retyped over (out 1, in 2) keeps in 2 (got ' + JSON.stringify(moved) + ')');
  const r81 = T.pinToChannel('1/1/1', 'resonator', { kind: 'mw_fem', con: 1, slot: 1, out_port: 8, in_port: 1 });
  ok(r81 && r81.out_port === 1 && r81.in_port === 1, 'F6 review: (out 8, in 1) retyped to out 1 keeps in 1');
  const r11 = T.pinToChannel('1/1/8', 'resonator', { kind: 'mw_fem', con: 1, slot: 1, out_port: 1, in_port: 1 });
  ok(r11 && r11.out_port === 8 && r11.in_port === 1,
     'F6 review: a source chip\'s (out 1, in 1) retyped to out 8 keeps in 1 (got ' + JSON.stringify(r11) + ')');
  // a partial LO-safe pre-pin is the wizard's guess, not a cable: derive
  const part = T.pinToChannel('1/1/1', 'resonator', { kind: 'mw_fem', out_port: 8, in_port: 2 });
  ok(part && part.in_port === 1, 'F6 review: over a partial pre-pin the input is derived (got ' + JSON.stringify(part) + ')');
  ok(T.pinToChannel('1/1/8', 'resonator', null).in_port === 2, 'F6 review: no previous pin -> derived');
  state.mode = 'generate';
  // Generate: an input that IS the LO partner the wizard derived is re-derived
  const g = T.pinToChannel('1/1/1', 'resonator', { kind: 'mw_fem', con: 1, slot: 1, out_port: 8, in_port: 2 });
  ok(g && g.in_port === 1, 'F6 review: Generate re-derives its own LO-partner input (got ' + JSON.stringify(g) + ')');
  // ...but one it did not derive (a drag, a CSV) is a cable and stays
  const gk = T.pinToChannel('1/1/3', 'resonator', { kind: 'mw_fem', con: 1, slot: 1, out_port: 1, in_port: 2 });
  ok(gk && gk.in_port === 2, 'F6 review: Generate keeps an input it did not derive (got ' + JSON.stringify(gk) + ')');
  state.mode = was;
  // QA generate-r2-11: a pin names real hardware -- con >= 1, slot 1-8, port 1-8,
  // whole numbers only (parseInt read '1.5' and '1e999' as 1)
  ['1/1/99', '1/1/0', '1/9/1', '0/1/1', '1.5/1/1', '1e999/1/1', '1/1/-1', 'x9', '1/1/8a'].forEach(function (p) {
    ok(T.pinToChannel(p, 'drive') === null, 'r2-11: "' + p + '" is not a pin');
  });
  const sp = T.pinToChannel('  2 / 8 / 8  ', 'flux');
  ok(sp && sp.con === 2 && sp.out_slot === 8 && sp.out_port === 8, 'r2-11: spaced, in-range pins still parse');
  // channelToPin: a partial LO-safe pre-pin is not "//8"
  ok(T.channelToPin({ kind: 'mw_fem', out_port: 8, in_port: 2 }) === '',
     'F6: a partial channel renders as an empty pin box, not "//8"');
  ok(T.channelToPin({ kind: 'mw_fem', con: 1, slot: 1, out_port: 8, in_port: 2 }) === '1/1/8',
     'F6: a full channel still renders con/slot/port');
  ok(T.channelToPin({ kind: 'lf_fem', con: 1, out_slot: 3, out_port: 5 }) === '1/3/5',
     'F6: an lf_fem channel renders con/out_slot/port');
})();

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

// --- QA F11: pair lines are "q1-q2" in the spec, "q1-2" in the allocation ----
// allocText/syncSpecChannels looked the spec element up verbatim, so EVERY
// coupler / CR / ZZ row read "—" and a dragged coupler never reached its pin.
(function () {
  state.allocation = {
    'q1': { xy: [{ instrument_id: 'mw-fem', con: 1, slot: 1, port: 2 }] },
    'q1-2': { c: [{ instrument_id: 'lf-fem', con: 1, slot: 3, port: 6 }] },
    'q0-1': { cr: [{ instrument_id: 'mw-fem', con: 1, slot: 1, port: 5 }],
              zz: [{ instrument_id: 'mw-fem', con: 1, slot: 1, port: 6 }] }
  };
  ok(T.allocText('q1-q2', 'coupler') === 'lf-fem con1 s3 p6',
     'F11: the coupler row shows its allocated port (got ' + T.allocText('q1-q2', 'coupler') + ')');
  ok(T.allocText('q0-q1', 'cross_resonance') === 'mw-fem con1 s1 p5',
     'F11: the CR row shows its allocated port (got ' + T.allocText('q0-q1', 'cross_resonance') + ')');
  ok(T.allocText('q0-q1', 'zz_drive') === 'mw-fem con1 s1 p6', 'F11: the ZZ row too');
  ok(T.allocText('q1-2', 'coupler') === 'lf-fem con1 s3 p6', 'F11: a short-form spec id still hits');
  ok(T.allocText('q1', 'drive') === 'mw-fem con1 s1 p2', 'F11: a qubit row is unchanged');
  ok(T.allocText('q2-q3', 'coupler') === '—', 'F11: an unallocated pair still reads "—"');
  // a drag moved the coupler to port 7: syncSpecChannels must pin the LINE
  state.spec.qubits = ['q1', 'q2'];
  state.spec.lines = [{ element: 'q1-q2', line: 'coupler', channel: null }];
  state.allocation['q1-2'].c[0].port = 7;
  T.syncSpecChannels();
  const cl = state.spec.lines[0].channel;
  ok(cl && cl.kind === 'lf_fem' && cl.con === 1 && cl.out_slot === 3 && cl.out_port === 7,
     'F11: a dragged coupler port reaches its spec pin (got ' + JSON.stringify(cl) + ')');
  state.allocation = null;
})();

if (fails) {
  console.error(fails + ' failure(s)');
  process.exit(1);
}
console.log('generate_crwiring_selfcheck: all checks passed');
