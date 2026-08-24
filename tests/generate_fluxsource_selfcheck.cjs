// docs/136 WS5b/WS6/WS7 — the wizard side of "QDAC-II is a component".
//
// What this pins, and why each one is a bug that already happened once:
//
//  F1  the chip-level Flux source is DERIVED from the per-qubit shapes, never
//      a mode field of its own — so a per-qubit override can never disagree
//      with the chip-level selector, and "Per qubit…" is a REPORT, not a
//      command (offering it as one would mean "make them differ", which names
//      no particular arrangement).
//  F2  a bias tee KEEPS its OPX flux line. That is the whole point: the QDAC
//      holds the DC operating point while the LF-FEM plays pulses on top. The
//      pre-docs/136 deriveLines dropped the flux line for ANY qubit with a
//      QDAC entry.
//  F3  the QDAC band renders a three-way source picker per qubit (it was an
//      on/off checkbox), and changing it moves that qubit's flux line.
//  F4  prunePopulate reaches spec.qdac.qubits. It was the one qubit-keyed map
//      nothing pruned: lowering the qubit count left an orphan that
//      validate_spec rejects from step 8, naming a qubit no longer on screen.
//  F5  trigger cabling — one OPX digital output drives one QDAC ext input and
//      arms every channel on it. Round-robin reproduces the reference chip's
//      own cabling; sharing pins the group onto ONE port; and a pin carried in
//      by re-generate (how the bench is actually cabled TODAY) is never
//      withdrawn by the wizard's own grouping.
//  F6  the Populate QDAC cells write onto spec.qdac.qubits — the single home
//      run_build reads — and refuse to CREATE an entry, because a qubit becomes
//      QDAC-biased through the step-4 source picker, not by someone typing a
//      dwell time into a table.
//
// Run: node tests/generate_fluxsource_selfcheck.cjs   (needs jsdom)
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
  // The harness hands Node individual globals rather than a real script realm,
  // so every global the code reads BARE has to be bridged or the miss throws
  // instead of degrading (the standing rule since docs/78's CSS-global bug).
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
  return win;
}

const win = makeWorld();
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
  qubit_pairs: [],
  twpas: [],
  pair_gate: 'cz_tunable',
  lines: [],
  populate: { qubits: {}, pairs: {} }
};

function reset() {
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'generate' });
  G.state.qubitFlux = true;
  T.deriveLines();
  return G.state;
}

function fluxEls() {
  return G.state.spec.lines.filter(function (l) { return l.line === 'flux'; })
    .map(function (l) { return l.element; }).sort().join(',');
}

// ── F1: the chip-level answer is derived, and "mixed" is a report ─────────────
let st = reset();
ok(T.chipFluxSource() === 'opx', 'F1: a chip with no QDAC entries reads as opx');

T.applyFluxSource('qdac');
ok(T.chipFluxSource() === 'qdac', 'F1: applyFluxSource(qdac) moves every qubit');
ok(Object.keys(st.spec.qdac.qubits).sort().join(',') === 'q1,q2,q3',
  'F1: every qubit got a QDAC entry');

T.setQubitFluxSource('q2', 'opx');
T.deriveLines();
ok(T.chipFluxSource() === 'mixed', 'F1: one differing qubit makes the chip mixed');
ok(T.fluxSourceOf('q2') === 'opx' && T.fluxSourceOf('q1') === 'qdac',
  'F1: fluxSourceOf reports each qubit for itself');

T.renderFluxSource();
const sel = doc.getElementById('gen-flux-source');
ok(!!sel && sel.value === 'mixed', 'F1: the selector shows the derived answer');
const mixedOpt = sel && sel.querySelector('option[value="mixed"]');
ok(mixedOpt && mixedOpt.hidden === false,
  'F1: "Per qubit…" is offered WHILE the chip is mixed');
T.applyFluxSource('qdac');
T.renderFluxSource();
ok(mixedOpt && mixedOpt.hidden === true,
  'F1: and hidden once it would be a command rather than a report');

// A DC bias source is a question on EVERY architecture. The chip that
// motivated all of this is FIXED-frequency qubits biased by a QDAC
// (QdacBiasedFixedFrequencyTransmon), so hiding this row when the chip has no
// LF-FEM z line would make the component unreachable for exactly that shape.
// What the architecture decides is which SOURCES are possible.
G.state.qubitFlux = false;
T.renderFluxSource();
ok(doc.getElementById('gen-line-flux-source').hidden === false,
  'F1: a fixed-frequency chip still gets the source question');
const optOpx = sel.querySelector('option[value="opx"]');
const optTee = sel.querySelector('option[value="tee"]');
const optQdac = sel.querySelector('option[value="qdac"]');
ok(optOpx.disabled === true && optTee.disabled === true,
  'F1: LF-FEM and bias tee need a z line to play pulses on');
ok(!!optTee.title, 'F1: and say why, rather than being silently inert');
ok(optQdac.disabled === false, 'F1: the QDAC is available on any architecture');
ok(optOpx.textContent.indexOf('None') === 0,
  'F1: with no z line, "opx" means NO DC bias — not "biased from an LF-FEM"');
G.state.qubitFlux = true;
T.renderFluxSource();
ok(optOpx.disabled === false && optOpx.textContent.indexOf('LF-FEM') === 0,
  'F1: and it comes back when the architecture has one');

// ── F2: a bias tee KEEPS its OPX flux line ───────────────────────────────────
reset();
T.applyFluxSource('qdac');
ok(fluxEls() === '', 'F2: QDAC-only qubits derive no OPX flux line');

T.applyFluxSource('tee');
ok(fluxEls() === 'q1,q2,q3', 'F2: a bias tee keeps every OPX flux line');
ok(Object.keys(st.spec.qdac.qubits).length === 3 &&
   st.spec.qdac.qubits.q1.bias_tee === true,
  'F2: and every entry is flagged as a tee');
ok(T.isBiasTee('q1') === true && T.chipFluxSource() === 'tee',
  'F2: isBiasTee/chipFluxSource agree');

T.applyFluxSource('qdac');
ok(!('bias_tee' in st.spec.qdac.qubits.q1),
  'F2: dropping back to QDAC clears the flag (co-presence alone is a MISTAKE)');
ok(fluxEls() === '', 'F2: and the flux lines go with it');

// ── F3: the band renders a three-way picker, and it moves the flux line ──────
reset();
T.setQubitFluxSource('q1', 'qdac');
T.deriveLines();
T.renderQdacBand();
const rows = doc.querySelectorAll('#gen-qdac-list .gen-qdac-row');
ok(rows.length === 3, 'F3: one row per qubit');
const q1row = doc.querySelector('#gen-qdac-list .gen-qdac-row[data-qubit="q1"]');
const q1pick = q1row && q1row.querySelector('select.gen-qdac-source');
ok(!!q1pick, 'F3: the row carries a source SELECT (it was a checkbox)');
ok(q1pick.value === 'qdac', 'F3: showing that qubit own source');
ok(q1row.getAttribute('data-source') === 'qdac',
  'F3: and the row is marked so a mixed chip is readable at a glance');
ok(Array.prototype.map.call(q1pick.options, function (o) { return o.value; })
   .join(',') === 'opx,qdac,tee', 'F3: all three shapes offered');

q1pick.value = 'tee';
q1pick.dispatchEvent(new win.Event('change', { bubbles: true }));
ok(T.isBiasTee('q1') === true, 'F3: changing the picker sets the tee flag');
ok(fluxEls().indexOf('q1') >= 0, 'F3: and q1 gets its OPX flux line back');

const q1pick2 = doc.querySelector(
  '#gen-qdac-list .gen-qdac-row[data-qubit="q1"] select.gen-qdac-source');
ok(q1pick2 && q1pick2.value === 'tee', 'F3: the re-render shows the new value');

// ── F4: prunePopulate reaches spec.qdac.qubits ───────────────────────────────
reset();
T.applyFluxSource('qdac');
ok(Object.keys(st.spec.qdac.qubits).length === 3, 'F4: three entries before the prune');
T.prunePopulate({ q1: true, q2: true });
ok(Object.keys(st.spec.qdac.qubits).sort().join(',') === 'q1,q2',
  'F4: the entry for the removed qubit is dropped');

// ── F5: trigger cabling ──────────────────────────────────────────────────────
reset();
T.applyFluxSource('qdac');
G.state.allocation = {
  q1: { qt: [{ con: 1, slot: 5, port: 1 }] },
  q2: { qt: [{ con: 1, slot: 5, port: 2 }] },
  q3: { qt: [{ con: 1, slot: 5, port: 3 }] }
};
T.renderQdacCabling();
const host = doc.getElementById('gen-qdac-cabling');
ok(host && host.hidden === false, 'F5: the cabling panel appears for a QDAC chip');

const rr = doc.getElementById('gen-qdac-rr');
ok(!!rr, 'F5: a one-press round-robin exists (nobody fills 11 boxes)');
rr.dispatchEvent(new win.Event('click', { bubbles: true }));
ok(st.spec.qdac.qubits.q1.trigger_port === 'ext1' &&
   st.spec.qdac.qubits.q2.trigger_port === 'ext2' &&
   st.spec.qdac.qubits.q3.trigger_port === 'ext3',
  'F5: round-robin walks ext1..ext4 down the qubit list');

// Two qubits on ONE ext input must land on ONE physical output.
st.spec.qdac.qubits.q2.trigger_port = 'ext1';
T.renderQdacCabling();
const p1 = st.spec.qdac.qubits.q1.trigger_pin;
const p2 = st.spec.qdac.qubits.q2.trigger_pin;
ok(p1 && p2 && p1.con === p2.con && p1.slot === p2.slot && p1.port === p2.port,
  'F5: same ext ⇒ same cable');
ok(p1.port === 1, 'F5: the group takes the lowest allocated port (stable across renders)');
ok(!st.spec.qdac.qubits.q3.trigger_pin,
  'F5: a lone qubit on its own ext keeps the allocator free hand');

// Turning sharing off withdraws only the pins the wizard made.
st.spec.qdac.share_cables = false;
T.renderQdacCabling();
ok(!st.spec.qdac.qubits.q1.trigger_pin && !st.spec.qdac.qubits.q2.trigger_pin,
  'F5: sharing off withdraws the grouped pins');

// A pin carried in by re-generate records how the bench is cabled TODAY.
st.spec.qdac.share_cables = true;
st.spec.qdac.qubits.q3.trigger_pin = { con: 2, slot: 7, port: 4 };   // no pin_source
st.spec.qdac.qubits.q3.trigger_port = 'ext4';
T.renderQdacCabling();
ok(st.spec.qdac.qubits.q3.trigger_pin &&
   st.spec.qdac.qubits.q3.trigger_pin.con === 2 &&
   st.spec.qdac.qubits.q3.trigger_pin.port === 4,
  'F5: a re-generate-carried pin is never withdrawn by the wizard grouping');

// A carried pin is EVIDENCE about the bench; an allocation is a guess. So the
// carried pin is the group's cable — taking the numeric minimum instead would
// pin the unpinned members to a port the carried member will never move to,
// splitting one ext across two physical outputs.
reset();
T.applyFluxSource('qdac');
G.state.allocation = {
  q1: { qt: [{ con: 1, slot: 5, port: 1 }] },
  q2: { qt: [{ con: 1, slot: 5, port: 2 }] },
  q3: { qt: [{ con: 1, slot: 5, port: 3 }] }
};
st.spec.qdac.qubits.q1.trigger_port = 'ext1';
st.spec.qdac.qubits.q2.trigger_port = 'ext1';
// q2 carries a HIGHER-numbered pin than q1's fresh allocation.
st.spec.qdac.qubits.q2.trigger_pin = { con: 1, slot: 5, port: 8 };
T.renderQdacCabling();
ok(st.spec.qdac.qubits.q1.trigger_pin &&
   st.spec.qdac.qubits.q1.trigger_pin.port === 8,
  'F5: the group joins the CARRIED cable, not the lowest allocated port');
ok(st.spec.qdac.qubits.q2.trigger_pin.port === 8,
  'F5: and the carried member is left exactly where the bench has it');

reset();
T.applyFluxSource('qdac');
G.state.allocation = {
  q1: { qt: [{ con: 1, slot: 5, port: 1 }] },
  q2: { qt: [{ con: 1, slot: 5, port: 2 }] },
  q3: { qt: [{ con: 1, slot: 5, port: 3 }] }
};
const rr2 = doc.getElementById('gen-qdac-rr');
rr2.dispatchEvent(new win.Event('click', { bubbles: true }));
const clr = doc.getElementById('gen-qdac-clear');
clr.dispatchEvent(new win.Event('click', { bubbles: true }));
ok(!st.spec.qdac.qubits.q1.trigger_port && !st.spec.qdac.qubits.q3.trigger_pin,
  'F5: Clear is the explicit act that does drop them');

// A chip with no QDAC shows nothing at all.
reset();
T.renderQdacCabling();
ok(doc.getElementById('gen-qdac-cabling').hidden === true,
  'F5: no QDAC ⇒ no cabling panel');

// ── F6: the Populate cells write onto the single home ────────────────────────
reset();
T.applyFluxSource('qdac');
ok(T.POP_QDAC_COLS.some(function (c) { return c.field === 'dc_offset'; }),
  'F6: the DC operating point is a Populate column');
ok(T.POP_QDAC_COLS.every(function (c) { return c.dim !== 'time'; }),
  'F6: no shared time unit — dwell is in seconds and settle_time in ns on the ' +
  'SAME component, so one dim would silently convert one of them by 1e9');

const bucket = T.popBucketWrite('qdac', 'q1');
ok(bucket === st.spec.qdac.qubits.q1,
  'F6: a QDAC cell writes onto the qubit own QDAC entry, not populate.qdac');
bucket.dwell = 5e-6;
ok(T.popBucketRead('qdac', 'q1').dwell === 5e-6, 'F6: and reads back from it');

T.setQubitFluxSource('q2', 'opx');
ok(T.popBucketWrite('qdac', 'q2') === null,
  'F6: a non-QDAC qubit has nowhere to write — the table never rewires a chip');
ok(T.presetRowIds('qdac').indexOf('q2') < 0,
  'F6: and it is not a row of the QDAC preset section');
ok(T.popBucketWrite('flux', 'q2') && st.spec.populate.flux &&
   st.spec.populate.flux.q2,
  'F6: every other group still writes into spec.populate as before');

// ── F7: the popBucket refactor must not change what a preset captures ────────
// docs/136 review — routing every populate read through popBucketRead tempted
// a rewrite of capturePresetSections to ask for its rows BY NAME. That breaks
// pairs: a pair bucket may be keyed in the SHORT second-member form ("q1-2",
// r16 0-1) while presetRowIds renders the full one ("q1-q2"), so the capture
// would find nothing and a saved preset would silently lose its pair values.
// Nothing else in the tree covered this — verified by reverting the fix and
// watching four generate harnesses all stay green.
reset();
G.state.spec.qubit_pairs = [['q1', 'q2']];
G.state.spec.populate.pairs = { 'q1-2': { cz_amplitude: 0.077 } };   // SHORT key
G.state.spec.populate.qubit = { q1: { anharmonicity: -2e8 } };
const cap = T.capturePresetSections(['qubit', 'pairs']);
ok(cap.qubit && cap.qubit.defaults.anharmonicity === -2e8,
  'F7: a per-qubit value is captured');
ok(cap.pairs && (cap.pairs.defaults.cz_amplitude === 0.077 ||
                 (cap.pairs.overrides['q1-2'] || {}).cz_amplitude === 0.077),
  'F7: a SHORT-keyed pair value survives the capture');


// ── F8: the bias-tee mark survives into the wizard diagram ───────────────────
// Customer report (docs/136 r3): /instrument showed the amber bias-tee port,
// but "Modify wiring" rebuilt the same diagram from the ALLOCATION via
// buildInstrumentData — which never stamped qdac_shared — so the same physical
// port was amber on one page and plain z blue one click later.
reset();
T.setQubitFluxSource('q1', 'tee');
T.setQubitFluxSource('q2', 'opx');
T.deriveLines();
G.state.spec.qdac.qubits.q1.channel = 13;
G.state.spec.qdac.qubits.q1.dc_offset = -0.09;
G.state.spec.qdac.qubits.q1.trigger_port = 'ext1';
const allocF8 = {
  q1: { z:  [{ con: 1, slot: 5, port: 1, instrument_id: 'lf-fem' }],
        qt: [{ con: 1, slot: 4, port: 1, io_type: 'digital', instrument_id: 'lf-fem' }] },
  q2: { z:  [{ con: 1, slot: 5, port: 2, instrument_id: 'lf-fem' }] }
};
const dataF8 = T.buildInstrumentData(allocF8);
const femsF8 = dataF8.controllers['1'].fems;
const teeZ = femsF8['5'].output_ports[1][0];
const plainZ = femsF8['5'].output_ports[2][0];
ok(teeZ.qdac_shared === true, 'F8: the bias-tee z entry is stamped qdac_shared');
ok(teeZ.qdac_channel === 13 && teeZ.qdac_dc_offset === -0.09
   && teeZ.qdac_trigger_port === 'ext1',
  'F8: ...and carries the QDAC facts the dual hover shows');
ok(!plainZ.qdac_shared, 'F8: a plain z entry is NOT stamped (no false amber)');
const trigF8 = femsF8['4'].digital_ports[1][0];
ok(trigF8.qdac_trigger === true && trigF8.qdac_ext === 'ext1',
  'F8: the trigger entry names its ext input, matching /instrument');

// ── F9: the QDAC trigger cable is draggable, and the drop is honest ──────────
// docs/135 disabled digital drag ("an edit nothing could carry out") — stale
// since trigger_pin exists. The drag moves the whole CABLE: peeling one qubit
// off would split one ext input across two ports.
reset();
T.applyFluxSource('qdac');
G.state.spec.qdac.qubits.q1.trigger_port = 'ext1';
G.state.spec.qdac.qubits.q2.trigger_port = 'ext1';
G.state.spec.qdac.qubits.q3.trigger_port = 'ext2';
// The harness spec declares slots 1 (mw) and 5 (lf) ONLY — so every cable
// here lives on the DECLARED slot 5, and slot 4 doubles as the
// undeclared-slot case. (The first version parked cables on slot 4 and its
// occupied-port assertion passed vacuously via the undeclared-slot guard —
// caught by mutation, which is what mutation is for.)
G.state.allocation = {
  q1: { qt: [{ con: 1, slot: 5, port: 1, io_type: 'digital', instrument_id: 'lf-fem' }] },
  q2: { qt: [{ con: 1, slot: 5, port: 1, io_type: 'digital', instrument_id: 'lf-fem' }] },
  q3: { qt: [{ con: 1, slot: 5, port: 2, io_type: 'digital', instrument_id: 'lf-fem' }] }
};
ok(T.qtElementsAtPort(1, 5, 1).sort().join(',') === 'q1,q2',
  'F9: qtElementsAtPort finds every qubit on one cable');

const dragQt = { role: 'digital', con: 1, slot: 5, port: 1, io: 'digital' };
ok(T.isValidDrop(dragQt, { con: 1, slot: 5, port: 3, io: 'digital' }) === true,
  'F9: an empty digital port on a declared FEM is a valid target');
ok(T.isValidDrop(dragQt, { con: 1, slot: 5, port: 2, io: 'digital' }) === false,
  'F9: a digital port carrying ANOTHER cable is refused (two exts, one port)');
ok(T.isValidDrop(dragQt, { con: 1, slot: 5, port: 3, io: 'output' }) === false,
  'F9: an analog output is refused');
ok(T.isValidDrop(dragQt, { con: 1, slot: 5, port: 1, io: 'digital' }) === false,
  'F9: the same port is a no-op, not a drop');
ok(T.isValidDrop(dragQt, { con: 1, slot: 4, port: 1, io: 'digital' }) === false,
  'F9: an undeclared slot is refused');

T.applyQdacTriggerEdit(dragQt, { con: 1, slot: 5, port: 5, io: 'digital' });
ok(G.state.allocation.q1.qt[0].port === 5 && G.state.allocation.q2.qt[0].port === 5,
  'F9: the WHOLE cable moves — both qubits land on the new port');
ok(G.state.allocation.q3.qt[0].port === 2,
  'F9: the other cable does not move');
const p1F9 = G.state.spec.qdac.qubits.q1.trigger_pin;
const p2q = G.state.spec.qdac.qubits.q2.trigger_pin;
ok(p1F9 && p1F9.con === 1 && p1F9.slot === 5 && p1F9.port === 5
   && p2q && p2q.port === 5,
  'F9: trigger_pin recorded for every qubit on the cable');
ok(!('pin_source' in G.state.spec.qdac.qubits.q1),
  'F9: ...as USER evidence (no pin_source), so the sharing pass keeps it');
T.applyQdacSharing();
ok(G.state.spec.qdac.qubits.q1.trigger_pin.port === 5,
  'F9: applyQdacSharing does not withdraw a user-dragged pin');

// The attach gate: a QDAC trigger circle stays draggable, a foreign digital
// marker does not (its cursor is forced to default, no listener path).
const hostF9 = doc.getElementById('gen-wiring-diagram');
hostF9.innerHTML =
  '<div class="iw-port" data-con="1" data-slot="4" data-port="1" data-io="digital">'
  + '<g class="iw-port-circle" id="f9-qt" data-element="q1" data-role="digital"></g></div>'
  + '<div class="iw-port" data-con="1" data-slot="4" data-port="7" data-io="digital">'
  + '<g class="iw-port-circle" id="f9-alien" data-element="twpaA" data-role="digital"></g></div>';
T.attachWiringDrag();
ok(doc.getElementById('f9-qt').style.cursor !== 'default',
  'F9: a QDAC trigger circle is left draggable');
ok(doc.getElementById('f9-alien').style.cursor === 'default',
  'F9: a digital marker with no QDAC home is not');
hostF9.innerHTML = '';

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_fluxsource_selfcheck: all checks passed');
process.exit(0);
