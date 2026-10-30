// Z3: removing an added gate removes the pulses it created, EXECUTED under jsdom.
//
//  D1  a CZ group (one pulse + the gate): the gate's ✕ asks with "1 pulse" and its name, both go
//  D2  a both-movers group with the coupler pulse: "3 pulses", all three go
//  D3  a pulse another gate still plays stays, and is not counted
//  D4  Cancel in the confirm removes nothing
//  D5  a gate that plays no added pulse: the text says 0 pulses; unrelated pulses stay
//  D6  the removal is one undo step: the gate, its pulses and its default come back together
//
// Run: node tests/gen_additions_gate_remove_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
const J = JSON.stringify;
const tick = () => new Promise(r => setTimeout(r, 0));

const ROLES = { ok: true, versions: {}, qubit_fields: [], pulses: {}, macros_pair: {}, macros_qubit: {}, roots: {} };
const P = (q, n) => ({ kind: 'pulse', target: 'qubits.' + q + '.z.operations', name: n, 'class': 'lab.P', fields: {} });
const C = (n) => ({ kind: 'pulse', target: 'qubit_pairs.q1-2.coupler.operations', name: n, 'class': 'lab.P', fields: {} });
const G = (n, f) => ({ kind: 'macro', target: 'qubit_pairs.q1-2.macros', name: n, 'class': 'lab.G', fields: f });
const ptr = (q, n) => '#/qubits/' + q + '/z/operations/' + n;

async function world(adds, answer) {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="pt"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(/lint/.test(String(url)) ? { ok: true, results: [] } : ROLES) });
  w.asked = [];
  w.confirm = (t) => { w.asked.push(t); return answer !== false; };
  w.undo = [];
  w._wizUndo = { pushRestore: (label, fn) => w.undo.push(fn) };
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  const spec = { qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], pair_gate: 'cz_tunable',
    lines: [{ element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }, { element: 'q1-q2', line: 'coupler' }],
    populate: { pairs: { 'q1-q2': { cz_variant: 'unipolar' } } }, additions: adds };
  const ctx = { spec: () => spec, changed: () => {} };
  w.document.getElementById('pt').innerHTML = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>x</th></tr></thead><tbody>' +
    '<tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td></td></tr></tbody></table>';
  w.GenAdditions.mount(w.document.getElementById('host'), ctx);
  w.GenAdditions.decoratePairTable(w.document.getElementById('gen-pop-tbl-pairs'), ctx);
  await tick(); await tick();
  w.rm = (n) => w.document.querySelector('.gen-gate-rm[data-gate="' + n + '"]').click();
  w.spec = spec;
  return w;
}
const names = (w) => (w.spec.additions || []).map(a => a.name);

(async () => {
  let w = await world([P('q1', 'other'), P('q2', 'cz_a_pulse'), G('cz_a', { flux_pulse_qubit: ptr('q2', 'cz_a_pulse') })]);
  w.rm('cz_a');
  ok(/1 pulse it plays: cz_a_pulse/.test(w.asked[0] || ''), 'D1 text names 1 pulse: ' + w.asked[0]);
  ok(J(names(w)) === J(['other']), 'D1 gate and its pulse gone, the unrelated pulse stays: ' + names(w));

  w = await world([P('q1', 'h'), P('q2', 'l'), C('c'),
    G('cz_bm', { flux_pulse_high: ptr('q1', 'h'), flux_pulse_low: ptr('q2', 'l'),
                 coupler_flux_pulse: '#/qubit_pairs/q1-2/coupler/operations/c', wait: 4 })]);
  w.rm('cz_bm');
  ok(/3 pulses it plays: h, l, c/.test(w.asked[0] || ''), 'D2 text: ' + w.asked[0]);
  ok(w.spec.additions === undefined, 'D2 all four gone: ' + names(w));

  w = await world([P('q2', 'shared'), P('q1', 'own'), G('cz_a', { flux_pulse_qubit: ptr('q2', 'shared'), x: [ptr('q1', 'own')] }),
                   G('cz_b', { flux_pulse_qubit: ptr('q2', 'shared') })]);
  w.rm('cz_a');
  ok(/1 pulse it plays: own\./.test(w.asked[0] || ''), 'D3 the shared pulse is not counted: ' + w.asked[0]);
  ok(J(names(w)) === J(['shared', 'cz_b']), 'D3 the shared pulse stays with cz_b: ' + names(w));

  w = await world([P('q2', 'p'), G('cz_a', { flux_pulse_qubit: ptr('q2', 'p') })], false);
  w.rm('cz_a');
  ok(J(names(w)) === J(['p', 'cz_a']) && w.undo.length === 0, 'D4 Cancel removes nothing, pushes nothing');

  w = await world([P('q1', 'other'), G('cz_a', { flux_pulse_qubit: '#/qubits/q2/z/operations/built_by_the_build' })]);
  w.rm('cz_a');
  ok(/0 pulses/.test(w.asked[0] || ''), 'D5 text says 0 pulses: ' + w.asked[0]);
  ok(J(names(w)) === J(['other']), 'D5 only the gate goes');

  const before = [P('q2', 'p'), G('cz_a', { flux_pulse_qubit: ptr('q2', 'p') }),
    { kind: 'default_gate', target: 'qubit_pairs.q1-2.macros', name: 'cz', to: 'cz_a' }];
  w = await world(JSON.parse(J(before)));
  w.rm('cz_a');
  ok(w.spec.additions === undefined && w.undo.length === 1, 'D6 removed as one step');
  w.undo.pop()();
  ok(J(w.spec.additions) === J(before), 'D6 one undo brings gate, pulse and default back: ' + J(w.spec.additions));

  console.log((fails ? 'FAILED' : 'OK') + ' gen_additions_gate_remove_selfcheck: ' + asserts + ' assertions, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
