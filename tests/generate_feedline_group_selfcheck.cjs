// Behavioral check: a step-5 drag keeps every feedline's NAME (QA F25).
//
// Customer QA: on a re-generated 5-qubit chip, dragging q5's DRIVE circle to
// another port renamed every resonator's feedline group from "feedline1" to
// "fl_1_3_1" — syncSpecChannels re-derived each resonator line's group as
// "fl_<con>_<slot>_<port>" after ANY drag. Membership must still be
// re-derived from the dragged layout (same rr output port <=> same group:
// run_build / script_emitter / regen_script pin a whole group to its first
// member's channel, so a stale shared name multiplexes a qubit onto another
// feedline's port with no error) — only the NAME is kept.
//
// Pinned here, executing the real generate.js under jsdom through the same
// drag entry points the diagram calls (applyPortEdit / applyQubitReadoutEdit):
//   G1  a drive-only drag leaves every group name as it was — "feedline1"
//       and a CSV-imported "mux0_0" alike — and the step-5 table says so
//   G2  swapping two feedlines' output ports as units: each name follows
//       its qubits
//   G3  one qubit's rr output dragged alone to an empty port: the others
//       keep "feedline1", the split qubit gets a fresh, unused feedline<n>
//       (G3b: also when the split qubit is the feedline's FIRST member)
//   G4  a qubit's output dragged onto another feedline's port joins that
//       feedline's name
//   G5  invariant after every case: same group <=> same rr output port
//   G6  a resonator line whose allocation lacks the rr input is not rewired
//       and keeps its prior group
//
// Run: node tests/generate_feedline_group_selfcheck.cjs   (needs jsdom)
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
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

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

function mw(port, io) {
  return { con: 1, slot: 1, port: port, io_type: io || 'output', instrument_id: 'mw-fem' };
}

// feeds: [{name, out, in, qubits}] — one rr output/input port pair per feedline;
// drive ports are handed out from xyPorts in qubit order.
function chip(feeds, xyPorts) {
  const win = makeWorld();
  const G = win.QuamGen;
  const qubits = [];
  feeds.forEach(f => f.qubits.forEach(q => qubits.push(q)));
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C' },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                   opx_plus: [], octaves: [] },
    qubits: qubits.slice(), qubit_pairs: [], lines: [],
    populate: { qubits: {}, pairs: {} }
  }, { mode: 'regenerate' });
  const state = G.state;
  const lines = [], alloc = {};
  let xi = 0;
  feeds.forEach(f => f.qubits.forEach(q => {
    const xp = xyPorts[xi++];
    lines.push({ element: q, line: 'drive',
                 channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: xp } });
    lines.push({ element: q, line: 'resonator', group: f.name,
                 channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: f.out, in_port: f.in } });
    alloc[q] = { xy: [mw(xp)], rr: [mw(f.out), mw(f.in, 'input')] };
  }));
  state.spec.lines = lines;
  state.allocation = alloc;
  state.wiringTouched = true;
  return { win, G, T: G._test, state };
}

function groupOf(state, q) {
  const ln = state.spec.lines.find(l => l.element === q && l.line === 'resonator');
  return ln && ln.group;
}
function groups(state) {
  return state.spec.lines.filter(l => l.line === 'resonator')
    .map(l => l.element + ':' + l.group).join(', ');
}
function run(tag, fn) {
  try { fn(); } catch (e) { ok(false, tag + ' threw: ' + (e && e.stack || e)); }
}

// G5: same group <=> same rr output port, over every rewired resonator line.
function invariant(tag, w) {
  const rows = w.state.spec.lines.filter(l => l.line === 'resonator').map(l => {
    const rr = (w.state.allocation[l.element] || {}).rr || [];
    const o = rr.find(c => (c.io_type || 'output') === 'output');
    return { q: l.element, g: l.group, port: o ? o.con + '/' + o.slot + '/' + o.port : null };
  });
  for (let i = 0; i < rows.length; i++) {
    for (let j = i + 1; j < rows.length; j++) {
      const a = rows[i], b = rows[j];
      ok((a.g === b.g) === (a.port === b.port),
        tag + ' G5: ' + a.q + '/' + b.q + ' share a group iff they share an rr output port ' +
        '(groups ' + a.g + '/' + b.g + ', ports ' + a.port + '/' + b.port + ')');
    }
  }
}

const Q5 = ['q1', 'q2', 'q3', 'q4', 'q5'];

// ── G1: a drive-only drag renames nothing (the reported journey) ──
['feedline1', 'mux0_0'].forEach(function (name) {
  run('G1 ' + name, function () {
    const w = chip([{ name: name, out: 1, in: 1, qubits: Q5 }], [2, 3, 4, 5, 6]);
    // q5's drive circle: out 6 -> the free out 8
    w.T.applyPortEdit({ con: 1, slot: 1, port: 6, io: 'output' }, { con: 1, slot: 1, port: 8 });
    const d5 = w.state.spec.lines.find(l => l.element === 'q5' && l.line === 'drive');
    ok(d5.channel.out_port === 8, 'G1 ' + name + ': the drive drag itself landed (p' + d5.channel.out_port + ')');
    ok(Q5.every(q => groupOf(w.state, q) === name),
      'G1 ' + name + ': every feedline name survives a drive drag (' + groups(w.state) + ')');
    const tbl = w.win.document.getElementById('gen-wiring-table');
    const txt = tbl ? tbl.textContent : '';
    ok(txt.indexOf('· ' + name) >= 0 && txt.indexOf('fl_') < 0,
      'G1 ' + name + ': the step-5 table rows read "· ' + name + '", no fl_ name');
    invariant('G1 ' + name, w);
  });
});

// ── G2: swap two feedlines as units — each name follows its qubits ──
run('G2', function () {
  const w = chip([{ name: 'feedline1', out: 1, in: 1, qubits: ['q1', 'q2', 'q3'] },
                  { name: 'feedline2', out: 8, in: 2, qubits: ['q4', 'q5'] }], [2, 3, 4, 5, 6]);
  w.T.applyPortEdit({ con: 1, slot: 1, port: 1, io: 'output' }, { con: 1, slot: 1, port: 8 });
  ok(w.state.allocation.q1.rr[0].port === 8 && w.state.allocation.q4.rr[0].port === 1,
    'G2: the swap moved both feedlines');
  ok(['q1', 'q2', 'q3'].every(q => groupOf(w.state, q) === 'feedline1') &&
     ['q4', 'q5'].every(q => groupOf(w.state, q) === 'feedline2'),
    'G2: each feedline keeps its name through the swap (' + groups(w.state) + ')');
  invariant('G2', w);
});

// ── G3: one qubit's output split off alone — a fresh, unused name ──
run('G3', function () {
  const w = chip([{ name: 'feedline1', out: 1, in: 1, qubits: ['q1', 'q2', 'q3', 'q4'] },
                  { name: 'feedline2', out: 8, in: 2, qubits: ['q5'] }], [2, 3, 4, 5, 6]);
  // q3's rr output alone -> the free out 7
  w.T.applyQubitReadoutEdit({ element: 'q3', io: 'output' }, { con: 1, slot: 1, port: 7 });
  ok(w.state.allocation.q3.rr[0].port === 7, 'G3: q3 output moved');
  ok(['q1', 'q2', 'q4'].every(q => groupOf(w.state, q) === 'feedline1'),
    'G3: the three that stayed keep "feedline1" (' + groups(w.state) + ')');
  ok(groupOf(w.state, 'q5') === 'feedline2', 'G3: the untouched feedline2 keeps its name');
  ok(groupOf(w.state, 'q3') === 'feedline3',
    'G3: the split-off q3 gets the fresh unused name feedline3 (got ' + groupOf(w.state, 'q3') + ')');
  invariant('G3', w);
});

// ── G3b: the split-off qubit is the FIRST member — the name still goes to
// the port holding most of the feedline, not to the first one seen ──
run('G3b', function () {
  const w = chip([{ name: 'feedline1', out: 1, in: 1, qubits: ['q1', 'q2', 'q3', 'q4'] },
                  { name: 'feedline2', out: 8, in: 2, qubits: ['q5'] }], [2, 3, 4, 5, 6]);
  w.T.applyQubitReadoutEdit({ element: 'q1', io: 'output' }, { con: 1, slot: 1, port: 7 });
  ok(['q2', 'q3', 'q4'].every(q => groupOf(w.state, q) === 'feedline1'),
    'G3b: the majority keeps "feedline1" (' + groups(w.state) + ')');
  ok(groupOf(w.state, 'q1') === 'feedline3',
    'G3b: the split-off first member gets feedline3 (got ' + groupOf(w.state, 'q1') + ')');
  invariant('G3b', w);
});

// ── G4: a qubit's output dropped onto another feedline's port joins it ──
run('G4', function () {
  const w = chip([{ name: 'feedline1', out: 1, in: 1, qubits: ['q1', 'q2', 'q3'] },
                  { name: 'feedline2', out: 8, in: 2, qubits: ['q4', 'q5'] }], [2, 3, 4, 5, 6]);
  w.T.applyQubitReadoutEdit({ element: 'q5', io: 'output' }, { con: 1, slot: 1, port: 1 });
  ok(groupOf(w.state, 'q5') === 'feedline1',
    'G4: q5 dropped on feedline1\'s port joins "feedline1" (got ' + groupOf(w.state, 'q5') + ')');
  ok(groupOf(w.state, 'q4') === 'feedline2', 'G4: q4 keeps "feedline2" alone');
  invariant('G4', w);
});

// ── G6: a line with no rr input is not rewired and keeps its group ──
run('G6', function () {
  const w = chip([{ name: 'feedline1', out: 1, in: 1, qubits: Q5 }], [2, 3, 4, 5, 6]);
  w.state.allocation.q2.rr = [mw(1)];          // no input channel
  w.state.spec.lines.find(l => l.element === 'q2' && l.line === 'resonator').group = 'solo';
  w.T.applyPortEdit({ con: 1, slot: 1, port: 6, io: 'output' }, { con: 1, slot: 1, port: 8 });
  ok(groupOf(w.state, 'q2') === 'solo', 'G6: an input-less resonator line keeps its prior group');
  ok(['q1', 'q3', 'q4', 'q5'].every(q => groupOf(w.state, q) === 'feedline1'),
    'G6: the others keep "feedline1" (' + groups(w.state) + ')');
});

if (fails) {
  console.error(fails + ' of ' + asserts + ' checks failed');
  process.exit(1);
}
console.log('generate_feedline_group_selfcheck: all checks passed (' + asserts + ' assertions)');
