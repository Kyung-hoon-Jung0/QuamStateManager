// Lab additions P2 fixes, EXECUTED under jsdom.
//  L1  every listed addition shows the server lint's reasons before Build
//  L2  the lint is debounced: back-to-back redraws make one round of requests
//  L3  a stale lint answer never paints over a newer one
//  D1  an added pair gate is listed once: in the Qubit pairs table, not in the macro list
// Run: node tests/gen_additions_p2fix_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const JS = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
const F = (raw, kind) => ({ raw, has_default: false, kind: kind || 'plain' });
const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' }, qubit_fields: [],
  pulses: { 'pkg.pulses.SquarePulse': { origin: 'official', fields: { length: F('int'), amplitude: F('float') } } },
  macros_pair: { 'pkg.gates.CZGate': { origin: 'official', fields: { flux_pulse_qubit: F('Union[Pulse, str]', 'pointer_pulse') },
    cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true }] } },
  macros_qubit: {}, roots: {},
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const TABLE = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>CZ variant</th></tr></thead><tbody>' +
  '<tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td><select class="v"><option>all</option></select></td></tr></tbody></table>';

function world(lintAnswer) {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="pt"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  const calls = [];
  w.fetch = (url, opt) => {
    if (String(url).indexOf('/generate/additions/lint') === 0) {
      const body = JSON.parse(opt.body);
      calls.push(body);
      const n = calls.length;
      const one = i => lintAnswer({ addition: body.spec.additions[i] }, n);
      return Promise.all(body.indices.map(one)).then(results =>
        ({ status: 200, json: () => Promise.resolve({ ok: true, results }) }));
    }
    if (String(url).indexOf('/generate/proposals') === 0)
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, items: [] }) });
    return Promise.resolve({ status: 200, json: () => Promise.resolve(ROLES) });
  };
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  return { w, calls };
}
const SPEC = () => ({
  qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], pair_gate: 'cz_tunable',
  lines: [{ element: 'q1', line: 'drive' }, { element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }],
  populate: { pairs: { 'q1-q2': { cz_variant: 'all' } } },
  additions: [
    { kind: 'pulse', target: 'qubits.q1.xy.operations', name: 'x_lab', class: 'pkg.pulses.SquarePulse', fields: {} },
    { kind: 'macro', target: 'qubit_pairs.q1-2.macros', name: 'cz_lab', class: 'pkg.gates.CZGate', fields: {} },
  ],
});

(async () => {
  const { w, calls } = world((b) => b.addition.name === 'x_lab'
    ? { ok: false, errors: ['field length is missing'], warnings: ['a card warning'] } : { ok: true, errors: [], warnings: [] });
  const spec = SPEC();
  const ctx = { spec: () => spec, changed: () => {} };
  const host = w.document.getElementById('host');
  w.document.getElementById('pt').innerHTML = TABLE;
  w.GenAdditions.mount(host, ctx);
  await sleep(5);
  w.GenAdditions.decoratePairTable(w.document.getElementById('gen-pop-tbl-pairs'), ctx);
  w.GenAdditions.mount(host, ctx);          // a second redraw inside the debounce window
  await sleep(5);
  ok(calls.length === 0, 'L2 nothing sent before the debounce delay');
  await sleep(w.GenAdditions.LINT_DELAY_MS + 50);
  ok(calls.length === 1, 'L2 ONE request for every listed addition, got ' + calls.length);
  ok(calls[0] && JSON.stringify(calls[0].indices) === '[0]', 'L1 the listed additions are named by index ' + JSON.stringify(calls[0] && calls[0].indices));
  const err = host.querySelector('.gen-add-lint .gen-add-lint-err');
  ok(err && /field length is missing/.test(err.textContent), 'L1 the lint error shows on the row');
  ok(host.querySelector('.gen-add-lint .gen-add-lint-warn'), 'L1 the lint warning shows on the row');

  // D1
  ok(!host.querySelector('.gen-add-macros .gen-add-item'), 'D1 the pair gate is not a macro-list row');
  ok(/listed in the Qubit pairs table/.test((host.querySelector('.gen-add-gates-there') || {}).textContent || ''),
     'D1 the macro list points at the pairs table');
  const gates = Array.from(w.document.querySelectorAll('#gen-pop-tbl-pairs .gen-gate'))
    .filter(g => g.getAttribute('data-gate') === 'cz_lab');
  ok(gates.length === 1, 'D1 the gate is listed once in the pairs table, got ' + gates.length);

  // L3 stale answer
  const W2 = world((b, n) => n === 1
    ? new Promise(r => setTimeout(() => r({ ok: false, errors: ['STALE'], warnings: [] }), 300))
    : { ok: true, errors: [], warnings: ['FRESH'] });
  const s2 = { spec: SPEC(), changed: () => {} };
  s2.spec.additions = [s2.spec.additions[0]];
  const h2 = W2.w.document.getElementById('host');
  W2.w.GenAdditions.mount(h2, { spec: () => s2.spec, changed: () => {} });
  await sleep(W2.w.GenAdditions.LINT_DELAY_MS + 30);
  W2.w.GenAdditions.mount(h2, { spec: () => s2.spec, changed: () => {} });
  await sleep(W2.w.GenAdditions.LINT_DELAY_MS + 400);
  ok(!/STALE/.test(h2.textContent), 'L3 a stale answer never paints');
  ok(/FRESH/.test(h2.textContent), 'L3 the newest answer paints');

  console.log((fails ? 'FAILED ' : 'ok ') + '(' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();
