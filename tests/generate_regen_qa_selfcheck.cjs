// QA pass (package gen-regen) -- the Re-generate wizard's own JS, executed:
//
//  P1-P5  regenerate-r2-09: reversing a SOURCE pair's Control/Target on step
//         4 drags its populate bucket, pinned line and moving_qubit ROLE along
//         (the flux pulse stays on the same physical qubit), marks the row
//         "reversed vs source", and Review says so before the build; setting
//         it back restores everything.
//  W1-W4  regenerate-r2-10: renaming a TWPA through an EMPTY id (select-all +
//         Delete, then type) keeps its step-6 bucket and its pump/isolation
//         pins; x during a mid-rename deletes the bucket it really has.
//  B1-B4  F1 / r2-09 / r2-10: the build panel names a port calibration that
//         moved with its line, a port left on fresh defaults, a reversed
//         pair and a removed TWPA -- instead of anonymous raw paths.
//
// Run: node tests/generate_regen_qa_selfcheck.cjs   (needs jsdom)
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

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
// Bridge every global the code reads BARE (the standing rule since docs/78).
win.NumberInput = {
  fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} },
  format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
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
const state = G.state;

function spec() {
  return {
    network: { host: '1.2.3.4', cluster_name: 'C' },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 3, fem: 'mw' }, { slot: 5, fem: 'lf' }] }],
                   opx_plus: [], octaves: [] },
    qubits: ['q1', 'q2', 'q3'],
    qubit_pairs: [['q1', 'q2'], ['q2', 'q3']],
    twpas: [{ id: 'twpa1', qubits: ['q1', 'q2', 'q3'] }],
    pair_gate: 'cz_fixed',
    lines: [
      { element: 'twpa1', line: 'twpa_pump', channel: { kind: 'mw_fem', con: 1, slot: 3, out_port: 7 } },
      { element: 'twpa1', line: 'twpa_isolation', channel: { kind: 'mw_fem', con: 1, slot: 3, out_port: 8 } }
    ],
    populate: {
      qubits: {}, pairs: {
        'q1-q2': { moving_qubit: 'control', cz_amplitude: 0.3 },
        'q2-q3': { moving_qubit: 'control', cz_amplitude: 0.4, cz_variant: 'unipolar' }
      },
      twpa: { twpa1: { pump_frequency: 8.205e9, pump_amplitude: 0.48 } }
    }
  };
}

G.init();
G.hydrateFromSpec(spec(), { mode: 'regenerate' });

function pairRow(i) {
  return doc.querySelectorAll('#gen-pair-list .gen-pair-row:not(.gen-pair-head)')[i];
}
function setSel(row, cls, v) {
  const s = row.querySelector('.' + cls);
  s.value = v;
  s.dispatchEvent(new win.Event('change', { bubbles: true }));
}

// ── P1: the user's own two-step swap on the q2 -> q3 row ──────────────────
setSel(pairRow(1), 'gen-pair-c', 'q3');      // transient q3 -> q3
setSel(pairRow(1), 'gen-pair-t', 'q2');      // now q3 -> q2
const pp = state.spec.populate.pairs;
ok(JSON.stringify(state.spec.qubit_pairs[1]) === '["q3","q2"]',
  'P1: the row reads q3 -> q2 — got ' + JSON.stringify(state.spec.qubit_pairs[1]));
ok(pp['q3-q2'] && pp['q3-q2'].cz_amplitude === 0.4 && !pp['q2-q3'],
  'P1: the populate bucket followed the reversal — got ' + JSON.stringify(Object.keys(pp)));
ok(pp['q3-q2'] && pp['q3-q2'].moving_qubit === 'target',
  'P1: the ROLE swapped so q2 keeps the flux pulse — got ' +
  JSON.stringify(pp['q3-q2'] && pp['q3-q2'].moving_qubit));
ok(pp['q1-q2'].moving_qubit === 'control', 'P1: the other pair is untouched');

// ── P2: the row says it is reversed, and why it matters ───────────────────
function noteAfter(row) {
  const n = row && row.nextElementSibling;
  return n && n.classList.contains('gen-pair-reversed-chip') ? n : null;
}
const chip = noteAfter(pairRow(1));
ok(!!chip, 'P2: the reversed row carries a "reversed vs source" line under it');
ok(chip && /reversed vs the source chip/.test(chip.textContent), 'P2: the line says so in words');
ok(chip && /q2 → q3/.test(chip.title) && /NOT/.test(chip.title),
  'P2: its title names the source order and what will not carry');
ok(!noteAfter(pairRow(0)) && doc.querySelectorAll('.gen-pair-reversed-chip').length === 1,
  'P2: an unreversed row has no line');

// ── P3: Review says so before the build ───────────────────────────────────
G.goToStep(8);
const rev = Array.prototype.filter.call(doc.querySelectorAll('#gen-review th'),
  function (th) { return /Reversed pairs/.test(th.textContent); });
ok(rev.length === 1, 'P3: Review lists the reversed pair before the build');
ok(rev.length && /q2 → q3 is now q3 → q2/.test(rev[0].nextSibling.textContent),
  'P3: naming both orders — got ' + (rev.length ? rev[0].nextSibling.textContent : ''));
G.goToStep(4);

// ── P4: setting it back restores the bucket, the role and the chip ───────
setSel(pairRow(1), 'gen-pair-c', 'q2');      // transient q2 -> q2
setSel(pairRow(1), 'gen-pair-t', 'q3');
ok(JSON.stringify(state.spec.qubit_pairs[1]) === '["q2","q3"]', 'P4: back to q2 -> q3');
ok(pp['q2-q3'] && pp['q2-q3'].moving_qubit === 'control' && !pp['q3-q2'],
  'P4: bucket and role restored — got ' + JSON.stringify(pp));
ok(!doc.querySelector('.gen-pair-reversed-chip'), 'P4: the line is gone');

// ── P5: a plain Generate session is untouched (no source orientation) ────
// (czAutoOrient owns orientation there; the regen guard must stay out.)
state.mode = 'generate';
setSel(pairRow(1), 'gen-pair-c', 'q3');
setSel(pairRow(1), 'gen-pair-t', 'q2');
ok(!doc.querySelector('.gen-pair-reversed-chip'),
  'P5: no reversed line outside Re-generate');
state.mode = 'regenerate';

// ── W1: rename twpa1 -> twpaMain through an EMPTY intermediate id ─────────
G.hydrateFromSpec(spec(), { mode: 'regenerate' });
const tin = doc.querySelector('#gen-twpa-list .gen-twpa-row input');
function typeId(v) { tin.value = v; tin.dispatchEvent(new win.Event('input', { bubbles: true })); }
typeId('');                          // select-all + Delete (or the harness clear)
'twpaMain'.split('').reduce(function (acc, ch) { acc += ch; typeId(acc); return acc; }, '');
const pt = state.spec.populate.twpa;
ok(state.spec.twpas[0].id === 'twpaMain', 'W1: the TWPA id is twpaMain');
ok(pt.twpaMain && pt.twpaMain.pump_frequency === 8.205e9 && !pt.twpa1,
  'W1: the step-6 bucket followed the rename — got ' + JSON.stringify(Object.keys(pt)));

// ── W2: its pump / isolation pins followed too (port kept, isolation kept)
const els = state.spec.lines.filter(function (l) { return /^twpa_/.test(l.line); })
  .map(function (l) { return l.element + '|' + l.line; }).sort();
ok(JSON.stringify(els) === '["twpaMain|twpa_isolation","twpaMain|twpa_pump"]',
  'W2: pins re-keyed to twpaMain — got ' + JSON.stringify(els));
T.deriveLines();
const iso = state.spec.lines.filter(function (l) {
  return l.element === 'twpaMain' && l.line === 'twpa_isolation'; });
ok(iso.length === 1, 'W2: the isolation line survives a re-derive');

// ── W3: x in the middle of a rename deletes the bucket it really has ─────
G.hydrateFromSpec(spec(), { mode: 'regenerate' });
const tin2 = doc.querySelector('#gen-twpa-list .gen-twpa-row input');
tin2.value = ''; tin2.dispatchEvent(new win.Event('input', { bubbles: true }));
doc.querySelector('#gen-twpa-list .gen-twpa-row .gen-row-del').click();
ok(!state.spec.populate.twpa.twpa1, 'W3: x mid-rename deletes the real bucket');

// ── W4: a rename onto ANOTHER TWPA's id never steals its bucket ──────────
const two = spec();
two.twpas.push({ id: 'twpaB', qubits: [] });
two.populate.twpa.twpaB = { pump_frequency: 7e9 };
G.hydrateFromSpec(two, { mode: 'regenerate' });
const tin3 = doc.querySelector('#gen-twpa-list .gen-twpa-row input');
tin3.value = 'twpaB'; tin3.dispatchEvent(new win.Event('input', { bubbles: true }));
ok(state.spec.populate.twpa.twpaB.pump_frequency === 7e9 &&
   state.spec.populate.twpa.twpa1 && state.spec.populate.twpa.twpa1.pump_frequency === 8.205e9,
  'W4: a colliding id keeps both buckets where they are');

// ── B1-B4: the build panel names what moved / was reset / was reversed ───
const BASE = { carried: 100, grafted: 0, kept_new_pointer: 0, kept_new_only: 0,
               graft_subtrees: [], superseded: 0, residual_lost: [],
               dangling_grafts: [], pruned_ops: 0, schema_dropped: 0,
               populate_protected: 0, populate_conflicts: [] };
function render(over) {
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] },
                      merge: Object.assign({}, BASE, over) }, 'D:\\out');
  const el = doc.getElementById('gen-build-result');
  return {
    stats: Array.prototype.map.call(el.querySelectorAll('.gen-merge-stat'),
      function (s) { return s.textContent; }),
    lines: Array.prototype.map.call(el.querySelectorAll('.gen-merge-detail'),
      function (d) { return d.textContent; })
  };
}
let r = render({
  ports_moved: [{ from: 'ports.analog_outputs.con1.5.1', to: 'ports.analog_outputs.con1.5.6',
                  owner: 'q1.z' }],
  ports_moved_total: 1,
  ports_fresh: [{ port: 'ports.analog_outputs.con1.5.1', was: 'q1.z', now: 'q6.z' }],
  pairs_reversed: [{ old: 'q2-3', new: 'q3-2', lost: 139 }],
  twpas_removed: [{ id: 'twpa1', lost: 44 }]
});
ok(r.stats.some(function (s) { return /1 port calibration moved with its line/.test(s); }),
  'B1: the stat chip counts the moved port — got ' + JSON.stringify(r.stats));
ok(r.lines.some(function (l) {
  return /^q1\.z: analog_outputs con1\/5\/1 → analog_outputs con1\/5\/6/.test(l); }),
  'B1: the line names the LINE and both ports — got ' + JSON.stringify(r.lines));
ok(r.lines.some(function (l) {
  return /q6\.z on analog_outputs con1\/5\/1: fresh defaults/.test(l) && /q1\.z/.test(l); }),
  'B2: a port left on fresh defaults names whose values moved away');
ok(r.lines.some(function (l) {
  return /pair q2-3 was rebuilt reversed as q3-2/.test(l) && /139/.test(l); }),
  'B3: a reversed pair is named with its loss');
ok(r.lines.some(function (l) { return /TWPA twpa1 is not in this build/.test(l) && /44/.test(l); }),
  'B4: a removed TWPA is named with its loss');
r = render({});
ok(!r.stats.some(function (s) { return /moved with its line/.test(s); }) &&
   !r.lines.some(function (l) { return /fresh defaults|reversed as|not in this build/.test(l); }),
  'B*: an ordinary rebuild says none of it');

if (fails) { console.error(fails + ' of ' + asserts + ' check(s) failed'); process.exit(1); }
console.log('generate_regen_qa_selfcheck: all ' + asserts + ' checks passed');
process.exit(0);
