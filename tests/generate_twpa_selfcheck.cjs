// Behavioral check for the wizard's TWPA plumbing (review-r6 TWPA-loss fix):
//  - hydrateFromSpec normalizes bare-string twpa ids (old sidecars / the
//    pre-fix reconstructor) to the wizard's {id, qubits} object shape;
//  - deriveLines PRESERVES twpa_pump/twpa_isolation lines with their pinned
//    channels (it used to rebuild qubit/pair lines only, silently wiping the
//    reconstructed TWPA pins on the first count/rename/gate edit — the chip
//    then rebuilt without its TWPAs);
//  - a newly added TWPA gets its pump line (channel null → allocator assigns).
//  - review-r7: TWPA ports (qualang_tools WiringLineType "p"/"i") are
//    recognized by the step-5 wiring diagram's drag&drop (buildInstrumentData
//    role mapping + isValidDrop + syncSpecChannels) — they used to always
//    reject drops because the wizard-local regroup fell through to the LF-FEM
//    z/coupler branch for any unrecognized role.
//
// Run: node tests/generate_twpa_selfcheck.cjs   (needs jsdom)
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
  qubits: ['qA1', 'qA2'],
  qubit_pairs: [['qA2', 'qA1']],
  // bare STRINGS — the old sidecar / pre-fix reconstructor shape
  twpas: ['twpaA', 'twpaB'],
  pair_gate: 'cz_tunable',
  lines: [
    { element: 'twpaA', line: 'twpa_pump',
      channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: 8 } },
    { element: 'twpaB', line: 'twpa_pump',
      channel: { kind: 'mw_fem', con: 1, slot: 2, out_port: 8 } },
    { element: 'twpaB', line: 'twpa_isolation',
      channel: { kind: 'mw_fem', con: 1, slot: 2, out_port: 7 } }
  ],
  populate: { qubits: {}, pairs: {} }
};

G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
const state = G.state;

// T1: string twpas normalized to {id, qubits} objects
ok(state.spec.twpas.length === 2, 'T1: two TWPAs hydrated');
ok(state.spec.twpas.every(function (t) { return t && typeof t === 'object'; }),
  'T1: twpas are objects after hydrate');
ok(state.spec.twpas[0].id === 'twpaA' && state.spec.twpas[1].id === 'twpaB',
  'T1: ids preserved');

// T2: deriveLines keeps the twpa lines + their pinned channels
T.deriveLines();
function twpaLines(kind) {
  return state.spec.lines.filter(function (l) { return l.line === kind; });
}
var pumps = twpaLines('twpa_pump');
ok(pumps.length === 2, 'T2: both twpa_pump lines survive deriveLines (got ' + pumps.length + ')');
var pa = pumps.find(function (l) { return l.element === 'twpaA'; });
ok(pa && pa.channel && pa.channel.slot === 1 && pa.channel.out_port === 8,
  'T2: twpaA pump keeps its pinned channel');
ok(twpaLines('twpa_isolation').length === 1
   && twpaLines('twpa_isolation')[0].element === 'twpaB',
  'T2: pinned isolation line survives');

// T3: survives REPEATED derives (every count/rename/gate edit re-derives)
T.deriveLines();
T.deriveLines();
ok(twpaLines('twpa_pump').length === 2, 'T3: lines stable across repeated derives');

// T4: a newly added TWPA gets a pump line with a null channel (allocator's job)
state.spec.twpas.push({ id: 'twpaE', qubits: [] });
T.deriveLines();
var pe = twpaLines('twpa_pump').find(function (l) { return l.element === 'twpaE'; });
ok(!!pe && pe.channel === null, 'T4: new TWPA gets an unpinned pump line');

// T5: buildInstrumentData assigns the color-coded twpa_pump/twpa_ro/twpa_in
// roles (not the raw qualang_tools "p"/"i" key) so the diagram + drag logic
// recognize TWPA ports the same way it recognizes xy/rr ports.
var alloc = {
  twpaA: { p: [{ con: 1, slot: 1, port: 8, io_type: 'output', instrument_id: 'mw-fem' }] },
  twpaB: { i: [
    { con: 1, slot: 1, port: 7, io_type: 'output', instrument_id: 'mw-fem' },
    { con: 1, slot: 1, port: 6, io_type: 'input', instrument_id: 'mw-fem' }
  ] },
  qA1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output', instrument_id: 'mw-fem' }] }
};
var idata = T.buildInstrumentData(alloc);
var pumpPort = idata.controllers['1'].fems['1'].output_ports['8'];
ok(pumpPort && pumpPort[0].role === 'twpa_pump',
  'T5: TWPA pump port gets role twpa_pump (got ' + (pumpPort && pumpPort[0].role) + ')');
var isoOut = idata.controllers['1'].fems['1'].output_ports['7'];
var isoIn = idata.controllers['1'].fems['1'].input_ports['6'];
ok(isoOut && isoOut[0].role === 'twpa_ro', 'T5: TWPA isolation output gets role twpa_ro');
ok(isoIn && isoIn[0].role === 'twpa_in', 'T5: TWPA isolation input gets role twpa_in');

// T6: isValidDrop treats a twpa_pump port like any other MW-FEM output (free
// to land on an empty xy/rr/twpa port on an MW-FEM slot), and correctly
// rejects an LF-FEM target (slot 5 in SPEC.instruments is 'lf').
state.allocation = alloc;
var pumpSrc = { role: 'twpa_pump', whole: false, element: 'twpaA', con: 1, slot: 1, port: 8, io: 'output' };
ok(T.isValidDrop(pumpSrc, { con: 1, slot: 1, port: 3, io: 'output' }) === true,
  'T6: twpa_pump -> empty MW-FEM output port is a valid drop');
ok(T.isValidDrop(pumpSrc, { con: 1, slot: 5, port: 1, io: 'output' }) === false,
  'T6: twpa_pump -> LF-FEM port is rejected');
var isoInSrc = { role: 'twpa_in', whole: false, element: 'twpaB', con: 1, slot: 1, port: 6, io: 'input' };
ok(T.isValidDrop(isoInSrc, { con: 1, slot: 1, port: 4, io: 'input' }) === true,
  'T6: twpa_in -> empty MW-FEM input port is a valid drop');
ok(T.isValidDrop(isoInSrc, { con: 1, slot: 1, port: 4, io: 'output' }) === false,
  'T6: twpa_in -> an output port (io mismatch) is rejected');

// T7: syncSpecChannels rewrites the twpa_pump/twpa_isolation spec lines from
// the drag-mutated allocation — without this the diagram MOVES visually but
// the actual build spec silently keeps the OLD pin.
state.spec.lines.push(
  { element: 'twpaA', line: 'twpa_pump', channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: 8 } },
  { element: 'twpaB', line: 'twpa_isolation', channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: 7, in_port: 6 } }
);
alloc.twpaA.p[0].port = 3;            // simulate a drag: pump moved 8 -> 3
alloc.twpaB.i[0].port = 4;            // isolation output moved 7 -> 4
T.syncSpecChannels();
var pumpLine = state.spec.lines.filter(function (l) { return l.element === 'twpaA' && l.line === 'twpa_pump'; }).pop();
ok(pumpLine.channel.out_port === 3, 'T7: twpa_pump spec line channel follows the drag (got ' + pumpLine.channel.out_port + ')');
var isoLine = state.spec.lines.filter(function (l) { return l.element === 'twpaB' && l.line === 'twpa_isolation'; }).pop();
ok(isoLine.channel.out_port === 4 && isoLine.channel.in_port === 6,
  'T7: twpa_isolation spec line channel follows the drag (out=' + isoLine.channel.out_port + ', in=' + isoLine.channel.in_port + ')');


// T8: the Populate step grows a TWPA section (docs/175, customer report: the
// wizard created the TWPA line but Populate had no TWPA fields at all). Rows
// are the step-4 TWPAs by id; the table is the same buildPopTable every
// section uses. A harness-state gap must REPORT, not crash the run.
var doc = win.document;
try {
  T.renderPopulateTables();
  var secTwpa = doc.getElementById('gen-pop-sec-twpa');
  ok(!!secTwpa && secTwpa.hidden === false, 'T8: #gen-pop-sec-twpa is shown when the spec has TWPAs');
  var twpaRids = Array.prototype.slice.call(doc.querySelectorAll('#gen-pop-twpa [data-group="twpa"]'))
    .map(function (el) { return el.getAttribute('data-rid'); })
    .filter(function (v, i, a) { return v && a.indexOf(v) === i; });
  ok(twpaRids.indexOf('twpaA') >= 0 && twpaRids.indexOf('twpaB') >= 0,
    'T8: twpaA + twpaB rows render in the TWPA table (got ' + JSON.stringify(twpaRids) + ')');
} catch (e) {
  ok(false, 'T8 threw: ' + (e && e.message));
}

// T9: unit honesty at the column definition (the docs/136 lesson — dwell is s
// and settle_time is ns on ONE component): settling_time is a FIXED ns label,
// never a stage-unit dim; pump_amplitude is a scale, never dim:"amp" (that
// would read the QUBIT table's FSP for a twpa row); the FSP column owns its
// own unit label so colHeader can never rewrite it to "(dBm · auto)".
var colBy = {}; T.POP_TWPA_COLS.forEach(function (c) { colBy[c.field] = c; });
ok(!!colBy.settling_time && colBy.settling_time.unit === 'ns' && !colBy.settling_time.dim,
  'T9: settling_time is a fixed ns label, not a dim');
ok(!!colBy.pump_amplitude && !colBy.pump_amplitude.dim,
  'T9: pump_amplitude carries no dim (never amp/dBm-converted)');
ok(!!colBy.full_scale_power_dbm && colBy.full_scale_power_dbm.unit === 'dBm',
  'T9: the TWPA FSP column owns its unit label');

// T10: degrade-only — a spec with no TWPAs hides the section and never touches
// the host, so the no-TWPA Populate render stays byte-identical.
try {
  var savedTwpas = T.state.spec.twpas;
  T.state.spec.twpas = [];
  var twpaHost = doc.getElementById('gen-pop-twpa'); twpaHost.innerHTML = '';
  T.renderPopulateTables();
  ok(doc.getElementById('gen-pop-sec-twpa').hidden === true, 'T10: no TWPAs -> section hidden');
  ok(twpaHost.innerHTML === '', 'T10: no TWPAs -> host untouched');
  T.state.spec.twpas = savedTwpas;
} catch (e) {
  ok(false, 'T10 threw: ' + (e && e.message));
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_twpa_selfcheck: all checks passed');
process.exit(0);
