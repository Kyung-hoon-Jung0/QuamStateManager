// Behavioral check: the step-5 readout-feedline panel (docs/242).
//
// Customer feedback: building feedlines by dragging 15 px sub-dots on the
// rack, one qubit at a time, is too hard for 20-50 qubits, and the readout
// INPUT is really a consequence of the output (neighbor or crossing). The
// panel edits the same truth the rack does -- each resonator line's `group`
// and `channel` -- as a pool of unassigned qubits plus one card per feedline.
//
// Executes the real generate.js under jsdom:
//   R1  inPortFor: Out1/Out8 neighbor = the coupled input (QM: Out1-In1,
//       Out8-In2), crossing = the other; Out 2-7 name no input (null)
//   R2  deriveLines' default feedlines follow spec.readout_input
//   R3  assigning qubits to a feedline copies its channel; a 9th is refused
//   R4  the pool: no group/channel, survives a re-derive, blocks allocation
//       (no POST, stale allocation dropped) and the step-5 Next guard
//   R5  the chip-wide Crossing button flips Out1/Out8 feedlines and leaves an
//       Out 2-7 feedline's input alone
//   R6  changing a feedline's output keeps its neighbor/crossing relation
//   R7  topoSig sees a membership-only move (auto feedlines share a channel)
//   R8  cards are ordered by name: a move never renumbers the target card
//   R9  one Ctrl+Z puts group/channel/pool back
//   R10 DOM: click / Shift-click selects, a digit key moves, 0 unassigns,
//       Escape clears; the card's selects carry the channel
//   R11 two FEM-pinned feedlines sharing an input are both flagged
//   R12 Review names each feedline's Out/In and the relation
//   R13 a typed resonator pin's input follows the chip-wide mode
//   R14 Fill from pool tops feedlines up to the step-4 size
//
// Run: node tests/generate_readout_pool_selfcheck.cjs   (needs jsdom)
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
function run(tag, fn) {
  try { fn(); } catch (e) { ok(false, tag + ' threw: ' + (e && e.stack || e)); }
}

function world(nq, mux) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  const posts = [];
  win.fetch = function (url, opts) {
    posts.push({ url: url, body: opts && opts.body });
    return new win.Promise(function () {});   // never answers: no allocation adopted
  };
  new win.Function(GEN_JS).call(win);
  const G = win.QuamGen, T = G._test, state = G.state;
  state.spec.instruments.controllers = [{ con: 1, fems: [
    { slot: 1, fem: 'mw' }, { slot: 2, fem: 'mw' }, { slot: 3, fem: 'lf' }] }];
  state.spec.network = { host: '1.2.3.4', cluster_name: 'C', port: null };
  state.spec.qubits = [];
  for (let i = 1; i <= nq; i++) state.spec.qubits.push('q' + i);
  win.document.getElementById('gen-mux-size').value = String(mux || 5);
  state.env = 'python';
  T.deriveLines();
  return { win, G, T, state, posts, doc: win.document };
}

function rline(state, q) {
  return state.spec.lines.find(l => l.element === q && l.line === 'resonator');
}
function feeds(T) {
  return T.readoutModel().feeds.map(f => f.name + ':' + f.members.join(','));
}

// R1
run('R1', () => {
  const w = world(2);
  const T = w.T;
  ok(T.inPortFor(1, 'neighbor') === 1, 'R1 Out1 neighbor -> In1');
  ok(T.inPortFor(1, 'crossing') === 2, 'R1 Out1 crossing -> In2');
  ok(T.inPortFor(8, 'neighbor') === 2, 'R1 Out8 neighbor -> In2');
  ok(T.inPortFor(8, 'crossing') === 1, 'R1 Out8 crossing -> In1');
  [2, 3, 4, 5, 6, 7].forEach(p => ok(T.inPortFor(p, 'crossing') === null, 'R1 Out' + p + ' names no input'));
  ok(T.inModeOf({ out_port: 1, in_port: 2 }) === 'crossing', 'R1 inModeOf crossing');
  ok(T.inModeOf({ out_port: 8, in_port: 2 }) === 'neighbor', 'R1 inModeOf neighbor');
  ok(T.inModeOf({ out_port: 3, in_port: 2 }) === 'manual', 'R1 inModeOf Out3 manual');
});

// R2
run('R2', () => {
  const w = world(10, 5);
  // a NEW chip defaults to crossing (QM's recommended readout pairing)
  ok(w.state.spec.readout_input === 'crossing', 'R2 a new chip defaults to crossing');
  ok(JSON.stringify(rline(w.state, 'q1').channel) === JSON.stringify({ kind: 'mw_fem', out_port: 8, in_port: 1 }),
     'R2 default feedline1 is Out8/In1 (crossing): ' + JSON.stringify(rline(w.state, 'q1').channel));
  ok(rline(w.state, 'q6').channel.in_port === 2, 'R2 default feedline2 is Out1/In2');
  w.state.spec.readout_input = 'neighbor';
  w.T.deriveLines();
  ok(rline(w.state, 'q1').channel.in_port === 2, 'R2 neighbor: Out8 -> In2');
  ok(rline(w.state, 'q6').channel.in_port === 1, 'R2 neighbor: Out1 -> In1');
});

// R3 + R8
run('R3', () => {
  const w = world(20, 5);
  const T = w.T;
  ok(T.assignReadout(['q1', 'q2', 'q3'], 'feedline3') === true, 'R3 move accepted');
  ok(rline(w.state, 'q1').group === 'feedline3', 'R3 q1 joined feedline3');
  ok(JSON.stringify(rline(w.state, 'q1').channel) === JSON.stringify(rline(w.state, 'q11').channel),
     'R3 q1 carries feedline3\'s channel');
  ok(w.state.wiringTouched === true, 'R3 the wiring is now the user\'s');
  ok(T.readoutModel().feeds[2].name === 'feedline3', 'R8 card 3 is still feedline3: ' + feeds(T));
  ok(T.assignReadout(['q4'], 'feedline3') === false, 'R3 a 9th qubit is refused (MW-FEM bound 8)');
  ok(rline(w.state, 'q4').group === 'feedline1', 'R3 the refused qubit stayed');
});

// R4
run('R4', () => {
  const w = world(10, 5);
  const T = w.T, state = w.state;
  state.allocation = { q1: { rr: [] } };
  T.assignReadout(['q2', 'q7'], '__pool__');
  const l2 = rline(state, 'q2');
  ok(l2.pool === true && l2.group === undefined && l2.channel === null, 'R4 pooled line has no group/channel');
  ok(JSON.stringify(T.readoutModel().pool) === '["q2","q7"]', 'R4 pool lists q2,q7');
  ok(w.posts.length === 0, 'R4 nothing posted to /generate/allocate while the pool is non-empty');
  ok(state.allocation === null, 'R4 the stale allocation is dropped');
  ok(/not on a feedline/.test(w.doc.getElementById('gen-allocate-status').textContent),
     'R4 the button says why: ' + w.doc.getElementById('gen-allocate-status').textContent);
  const g = T.stepGuard5();
  ok(g && /2 qubits are not on a readout feedline \(q2, q7\)/.test(g), 'R4 Next refuses: ' + g);
  T.deriveLines();
  ok(rline(state, 'q2').pool === true && rline(state, 'q7').pool === true, 'R4 a re-derive keeps the pool');
  T.assignReadout(['q2', 'q7'], 'feedline2');
  ok(!rline(state, 'q2').pool && rline(state, 'q2').group === 'feedline2', 'R4 back on a feedline');
  ok(w.posts.length === 1 && /\/generate\/allocate/.test(w.posts[0].url), 'R4 an empty pool allocates again');
  ok(T.stepGuard5() === null || !/readout feedline/.test(T.stepGuard5()), 'R4 the pool guard is gone');
});

// R5
run('R5', () => {
  const w = world(15, 5);
  const T = w.T;
  T.setFeedlineOutput('feedline3', { con: 1, slot: 2 }, 3);   // Out 3: no coupled input
  const kept = rline(w.state, 'q11').channel.in_port;
  T.setAllReadoutInputs('crossing');
  ok(w.state.spec.readout_input === 'crossing', 'R5 the chip-wide mode is stored');
  ok(rline(w.state, 'q1').channel.in_port === 1, 'R5 feedline1 Out8 -> In1');
  ok(rline(w.state, 'q6').channel.in_port === 2, 'R5 feedline2 Out1 -> In2');
  ok(rline(w.state, 'q11').channel.in_port === kept, 'R5 the Out 3 feedline keeps its input');
  ok(/Out 2–7 kept/.test(w.doc.querySelector('.gen-ro-note').textContent), 'R5 the note says one was kept');
});

// R6
run('R6', () => {
  const w = world(10, 5);
  const T = w.T;
  T.setFeedlineInput('feedline1', 'crossing');                // Out8 -> In1
  ok(rline(w.state, 'q1').channel.in_port === 1, 'R6 crossing on Out 8 is In 1');
  T.setFeedlineOutput('feedline1', { con: 1, slot: 1 }, 1);
  const c = rline(w.state, 'q1').channel;
  ok(c.out_port === 1 && c.in_port === 2 && c.con === 1 && c.slot === 1, 'R6 Out 1 keeps crossing -> In 2: ' + JSON.stringify(c));
  ok(rline(w.state, 'q5').channel.in_port === 2, 'R6 every member follows');
  T.setFeedlineOutput('feedline1', null, 5);
  ok(rline(w.state, 'q1').channel.in_port === 2 && rline(w.state, 'q1').channel.con === undefined,
     'R6 Out 5 keeps the input number; auto FEM drops con/slot');
  T.setFeedlineInput('feedline1', '1');
  ok(rline(w.state, 'q1').channel.in_port === 1, 'R6 manual In 1 on Out 5');
});

// R7
run('R7', () => {
  const w = world(20, 5);
  const before = w.T.topoSig();
  w.T.assignReadout(['q1'], 'feedline3');       // feedline1 and 3 share Out8/In2 partial pins
  ok(JSON.stringify(rline(w.state, 'q1').channel) === JSON.stringify(rline(w.state, 'q2').channel),
     'R7 premise: the move changed no channel');
  ok(w.T.topoSig() !== before, 'R7 topoSig sees the membership change');
});

// R9
run('R9', () => {
  const w = world(10, 5);
  const norm = () => JSON.stringify(w.state.spec.lines.map(l =>
    [l.element, l.line, l.group, l.channel, l.pool]));
  const before = norm();
  w.T.unassignAllReadout();
  ok(w.T.readoutModel().pool.length === 10, 'R9 everything pooled');
  ok(w.win._wizUndo.tryUndo() === true, 'R9 Ctrl+Z consumed');
  ok(norm() === before, 'R9 group/channel/pool restored exactly');
});

// R10
run('R10', () => {
  const w = world(10, 5);
  const doc = w.doc, win = w.win;
  w.state.spec.readout_input = 'neighbor';   // this pin is about neighbor mechanics
  w.T.deriveLines();
  w.T.bindReadoutPool();
  w.T.renderReadoutPool();
  const host = doc.getElementById('gen-readout-pool');
  ok(!host.hidden && host.querySelectorAll('.gen-ro-card[data-ro-target^="feedline"]').length === 2, 'R10 two cards');
  const chip = q => host.querySelector('.gen-ro-chip[data-q="' + q + '"]');
  chip('q1').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  chip('q3').dispatchEvent(new win.MouseEvent('click', { bubbles: true, shiftKey: true }));
  ok(host.querySelectorAll('.gen-ro-chip.is-sel').length === 3, 'R10 Shift-click selects q1..q3');
  chip('q3').dispatchEvent(new win.KeyboardEvent('keydown', { key: '2', bubbles: true }));
  ok(rline(w.state, 'q1').group === 'feedline2' && rline(w.state, 'q3').group === 'feedline2', 'R10 key 2 moves them');
  ok(host.querySelectorAll('.gen-ro-chip.is-sel').length === 0, 'R10 the selection clears after a move');
  chip('q4').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  chip('q4').dispatchEvent(new win.KeyboardEvent('keydown', { key: '0', bubbles: true }));
  ok(rline(w.state, 'q4').pool === true, 'R10 key 0 unassigns');
  chip('q5').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  chip('q5').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  ok(host.querySelectorAll('.gen-ro-chip.is-sel').length === 0, 'R10 Escape clears');
  // click-to-assign on a card
  chip('q4').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  host.querySelector('.gen-ro-card[data-ro-target="feedline1"] .gen-ro-chips')
    .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(rline(w.state, 'q4').group === 'feedline1', 'R10 clicking a card assigns the selection');
  // the card's selects carry the channel
  const card = host.querySelector('.gen-ro-card[data-ro-target="feedline1"]');
  const inSel = card.querySelector('.gen-ro-in');
  ok(inSel.value === '2' && /neighbor/.test(inSel.options[inSel.selectedIndex].textContent), 'R10 In 2 · neighbor shown');
  inSel.value = '1';
  inSel.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(rline(w.state, 'q4').channel.in_port === 1, 'R10 the input select writes the channel');
  const femSel = host.querySelector('.gen-ro-card[data-ro-target="feedline1"] .gen-ro-fem');
  femSel.value = '1/2';
  femSel.dispatchEvent(new win.Event('change', { bubbles: true }));
  const c = rline(w.state, 'q4').channel;
  ok(c.con === 1 && c.slot === 2 && c.out_port === 8 && c.in_port === 1, 'R10 the FEM select pins con/slot: ' + JSON.stringify(c));
});

// R11
run('R11', () => {
  const w = world(10, 5);
  const T = w.T;
  w.state.spec.readout_input = 'neighbor';   // this pin is about neighbor mechanics
  T.deriveLines();
  T.setFeedlineOutput('feedline1', { con: 1, slot: 1 }, 8);   // In 2 (neighbor)
  T.setFeedlineOutput('feedline2', { con: 1, slot: 1 }, 1);   // In 1
  ok(Object.keys(T.readoutClashes(T.readoutModel().feeds)).length === 0, 'R11 Out8/In2 + Out1/In1: no clash');
  T.setFeedlineInput('feedline2', 'crossing');                  // Out1 -> In2: clash
  const cl = T.readoutClashes(T.readoutModel().feeds);
  ok(/In 2/.test(cl.feedline1 || '') && /In 2/.test(cl.feedline2 || ''), 'R11 both flagged: ' + JSON.stringify(cl));
  ok(w.doc.querySelectorAll('.gen-ro-card.has-err').length === 2, 'R11 both cards red');
});

// R12
run('R12', () => {
  const w = world(10, 5);
  w.T.setAllReadoutInputs('crossing');
  const t = w.T.readoutReviewText();
  ok(/feedline1 \(q1, q2, q3, q4, q5\): auto FEM · Out 8 → In 1 · crossing/.test(t), 'R12 review: ' + t);
  w.state.allocation = {
    q1: { rr: [{ con: 1, slot: 2, port: 8, io_type: 'output' }, { con: 1, slot: 2, port: 1, io_type: 'input' }] } };
  ok(/feedline1 \([^)]*\): con1 slot 2 · Out 8 → In 1 · crossing/.test(w.T.readoutReviewText()),
     'R12 review prefers the allocation: ' + w.T.readoutReviewText());
});

// R13
run('R13', () => {
  const w = world(4, 5);
  w.state.spec.readout_input = 'crossing';
  const ch = w.T.pinToChannel('1/1/1', 'resonator', null);
  ok(ch && ch.in_port === 2, 'R13 typed Out 1 under crossing reads In 2: ' + JSON.stringify(ch));
  w.state.spec.readout_input = 'neighbor';
  ok(w.T.pinToChannel('1/1/1', 'resonator', null).in_port === 1, 'R13 neighbor reads In 1');
});

// R14
run('R14', () => {
  const w = world(12, 5);
  w.T.unassignAllReadout();
  w.T.fillReadoutFromPool();
  ok(JSON.stringify(feeds(w.T)) === JSON.stringify([
    'feedline1:q1,q2,q3,q4,q5', 'feedline2:q6,q7,q8,q9,q10', 'feedline3:q11,q12']),
     'R14 fill: ' + feeds(w.T));
  ok(rline(w.state, 'q6').channel.out_port === 1 && rline(w.state, 'q1').channel.out_port === 8,
     'R14 new feedlines alternate Out 8 / Out 1');
});
run('R14b', () => {
  const w = world(10, 5);
  w.T.assignReadout(['q4', 'q5'], '__pool__');
  w.T.fillReadoutFromPool();
  ok(JSON.stringify(feeds(w.T)) === JSON.stringify(['feedline1:q1,q2,q3,q4,q5', 'feedline2:q6,q7,q8,q9,q10']),
     'R14b fill tops up the short feedline before opening a new one: ' + feeds(w.T));
});

// R20 (docs/242, user decision 2026-10-02): crossing is the NEW-chip default
// only. A draft or a Re-generate source without the field keeps neighbor --
// its cabling already exists -- and Reset step on Wiring goes back to crossing.
run('R20', () => {
  // an older draft (no readout_input) restored on page load
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {}; win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; }; win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  win.sessionStorage.setItem('quam_generate_draft', JSON.stringify({ v: 2, step: 2,
    spec: { network: { host: 'h', cluster_name: 'c', port: null },
            instruments: { controllers: [], opx_plus: [], octaves: [] },
            qubits: ['q1'], qubit_pairs: [], twpas: [], lines: [], populate: {}, pair_gate: 'cz_tunable' } }));
  new win.Function(GEN_JS).call(win);
  win.QuamGen.init();
  ok(win.QuamGen.state.spec.qubits.join() === 'q1', 'R20 fixture: the draft was restored');
  ok(win.QuamGen.state.spec.readout_input !== 'crossing', 'R20 an older draft keeps neighbor: ' + win.QuamGen.state.spec.readout_input);
  // a Re-generate source without the field
  const w = world(4, 4);
  w.G.hydrateFromSpec({ network: { host: 'h', cluster_name: 'c', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }], opx_plus: [], octaves: [] },
    qubits: ['qA1', 'qA2'], qubit_pairs: [], twpas: [], lines: [], populate: {}, pair_gate: 'cz_tunable' },
    { mode: 'regenerate', sourcePath: 'D:/chips/src' });
  ok(w.state.spec.readout_input !== 'crossing', 'R20 a Re-generate source keeps neighbor: ' + w.state.spec.readout_input);
  // Reset step on Wiring: back to the new-chip default
  const v = world(10, 5);
  v.G.init();                                  // binds the header buttons
  v.state.spec.readout_input = 'neighbor';
  v.G.goToStep(5);
  v.doc.getElementById('gen-reset-step').dispatchEvent(new v.win.MouseEvent('click', { bubbles: true }));
  ok(v.state.spec.readout_input === 'crossing', 'R20 Reset step on Wiring goes back to crossing');
});

if (fails) {
  console.error(fails + ' check(s) failed (' + asserts + ' assertions)');
  process.exit(1);
}
console.log('all checks passed (' + asserts + ' assertions)');
