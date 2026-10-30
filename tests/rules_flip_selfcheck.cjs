// A CZ orientation flip keeps a pair's lab additions and rules (X1).
//
// generate.js flipPairOrder renames the builder's pair id ("q1-2" -> "q2-1")
// when Populate frequencies reorder a CZ pair. Everything on the pair must
// follow it by membership: the gate's target, every pointer naming the pair,
// and a rule's `source`. Before the fix the gates stayed on the old id, the
// pair table showed none, and the build would have dropped them.
//
// Prints `SPEC_JSON <json>` with the flipped spec so the pytest driver can
// judge it with the server's own rule expansion.
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const S = (n) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', n), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.NumberInput = { fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
  strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
win.armPlainResize = function () {};
win.renderInstrumentWiring = function () {};
win.confirm = function () { return true; };
win.fetch = function () { return new win.Promise(function () {}); };
['topo-graph.js', 'gen-additions.js', 'gen-rules.js', 'generate.js'].forEach(function (n) { new win.Function(S(n)).call(win); });

function setInput(el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function cell(rid) {
  return win.document.querySelector('.gen-pop-in[data-group="qubit"][data-rid="' + rid + '"][data-field="RF_freq"]');
}

const G = win.QuamGen;
G.init();
G.goToStep(3);
setInput(win.document.getElementById('gen-chassis-count'), '1');
G.state.spec.instruments.controllers[0].con = 1;
G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
G.goToStep(4);
setInput(win.document.getElementById('gen-qubit-count'), '3');
const arch = win.document.getElementById('gen-chip-arch');
arch.value = 'flux_tunable_coupler';
arch.dispatchEvent(new win.Event('change', { bubbles: true }));
G.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q3']];
G.state.pairsTouched = true;
G.goToStep(6);

function pulse(q, name) { return { kind: 'pulse', target: 'qubits.' + q + '.z.operations', name: name, class: 'pkg.P', fields: { amplitude: 0.1 } }; }
function gate(pid, c, t, extra) {
  return Object.assign({ kind: 'macro', target: 'qubit_pairs.' + pid + '.macros', name: 'cz_tf', class: 'pkg.G',
    fields: { flux_pulse_control: '#/qubits/' + c + '/z/operations/cz_tf_control_' + c + '_' + t,
              flux_pulse_target: '#/qubits/' + t + '/z/operations/cz_tf_target_' + c + '_' + t } }, extra || {});
}
const sp = G.state.spec;
// The person's hand gate on q1-q2, and the rule's instance on q2-q3.
sp.additions = [
  pulse('q1', 'cz_tf_control_q1_q2'), pulse('q2', 'cz_tf_target_q1_q2'), gate('q1-2', 'q1', 'q2'),
  Object.assign(pulse('q2', 'cz_tf_control_q2_q3'), { rule_id: 'r1' }),
  Object.assign(pulse('q3', 'cz_tf_target_q2_q3'), { rule_id: 'r1' }),
  gate('q2-3', 'q2', 'q3', { rule_id: 'r1' })];
sp.addition_rules = [{ id: 'r1', rev: 1, kind: 'pair_gate', source: 'q1-2',
  gate: { name: 'cz_tf', macro_class: 'pkg.G', as_default: false },
  items: [{ role: 'control', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' },
          { role: 'target', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' }],
  numbers: { 'control.amplitude': 0.1, 'target.amplitude': 0.1 },
  source_names: { control: 'cz_tf_control_q1_q2', target: 'cz_tf_target_q1_q2' },
  auto_new: false, excluded: [], reviewed: {} }];
const before = JSON.parse(JSON.stringify(sp));

// q2 above q1 flips q1-q2; q3 above q2 flips q2-q3.
setInput(cell('q1'), '4.6');
setInput(cell('q2'), '4.8');
setInput(cell('q3'), '5.0');
// X2: the flip's redraw is the decorated pair table (Gates column + Set-all gate cell).
ok(!!win.document.querySelector('#gen-pop-tbl-pairs th.gen-gates-th'), 'the redrawn pair table keeps its Gates column');
ok(!!win.document.querySelector('#gen-pop-tbl-pairs td.gen-gates-setall'), 'the redrawn pair table keeps its Set-all gate cell');
ok(!!win.document.querySelector('#gen-pop-tbl-pairs tr[data-rid="q2-q1"]'), 'the redrawn table has the flipped row');
const ids = sp.qubit_pairs.map(function (p) { return p[0] + '-' + p[1]; });
ok(ids[0] === 'q2-q1' && ids[1] === 'q3-q2', 'both pairs flipped (got ' + ids.join(',') + ')');

const macros = sp.additions.filter(function (a) { return a.kind === 'macro'; }).map(function (a) { return a.target; }).sort();
ok(JSON.stringify(macros) === JSON.stringify(['qubit_pairs.q2-1.macros', 'qubit_pairs.q3-2.macros']),
  'both gates followed their pair to the new id (got ' + macros.join(',') + ')');
ok(!sp.additions.some(function (a) { return /qubit_pairs\.q(1-2|2-3)\b/.test(a.target); }), 'nothing left on an old pair id');
ok(sp.additions.length === before.additions.length, 'no addition lost');
const pulses = sp.additions.filter(function (a) { return a.kind === 'pulse'; });
ok(JSON.stringify(pulses.map(function (a) { return a.target + '/' + a.name; }).sort()) ===
   JSON.stringify(before.additions.filter(function (a) { return a.kind === 'pulse'; }).map(function (a) { return a.target + '/' + a.name; }).sort()),
   'pulses stay on their physical qubits with their names');
ok(sp.addition_rules[0].source === 'q2-1', 'the rule source followed the pair (got ' + sp.addition_rules[0].source + ')');

// The pure helper rewrites only the pair segment of a pointer.
const moved = win.GenAdditions.flipPair([{ kind: 'pulse', target: 'qubit_pairs.q1-2.coupler.operations', name: 'p',
  fields: { x: '#/qubit_pairs/q1-2/coupler/operations/p', y: '#/qubit_pairs/q1-23/coupler', z: '#/qubits/q1/z' } }], 'q1-2', 'q2-1')[0];
ok(moved.target === 'qubit_pairs.q2-1.coupler.operations', 'flipPair: coupler target');
ok(moved.fields.x === '#/qubit_pairs/q2-1/coupler/operations/p', 'flipPair: pointer into the pair');
ok(moved.fields.y === '#/qubit_pairs/q1-23/coupler' && moved.fields.z === '#/qubits/q1/z', 'flipPair: other ids untouched');

// The card count is true: only gates on a pair the spec still names count.
ok(win.GenRules.countOn(sp, sp.addition_rules[0]) === 2, 'card count is 2 of 2 after the flip');
const stale = JSON.parse(JSON.stringify(sp));
stale.additions.forEach(function (a) { if (a.target === 'qubit_pairs.q3-2.macros') a.target = 'qubit_pairs.q2-3.macros'; });
ok(win.GenRules.countOn(stale, stale.addition_rules[0]) === 1, 'a gate left on a pair id the spec no longer names is not counted');

// The person's source gate asks for a look too when the server says so.
win.GenRules.noteExpand(sp.addition_rules[0], { pairs: [{ pair: 'q2-1', status: 'Needs a look',
  reason: 'control and target swapped by Populate frequency' }] }, 'x');
const src = sp.additions.filter(function (a) { return a.target === 'qubit_pairs.q2-1.macros'; })[0];
const info = win.GenRules.gateInfo(sp, 'q2-1', src);
ok(info.kind === 'source' && info.look === true, 'the source gate carries the look (got ' + info.kind + '/' + info.look + ')');

console.log('SPEC_JSON ' + JSON.stringify(sp));
if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('OK rules_flip: additions and rules follow a CZ orientation flip');
console.log('all checks passed');
