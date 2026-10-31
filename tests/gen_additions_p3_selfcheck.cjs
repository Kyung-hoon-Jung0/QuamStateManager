// Lab additions (phase 3) -- macros, the CZ group and the P2 UX fixes, EXECUTED
// under jsdom.
//
//  M1  owners() lists every qubit and every pair, a pair by the builder's id (q1-2, qA1-B2)
//  M2  "+ Add macro" on a pair offers pair-macro classes only; an option reads the class name and origin, the path on hover
//  M3  a pulse field of a macro offers the spec's pulse additions on that pair's qubits
//  M4  Add writes a macro addition on qubit_pairs.<id>.macros
//  E1  clicking an addition reopens its form prefilled; Save replaces it in place
//  C1  CZ group (two-flux): one pulse on the control z, one on the target z; required numbers start empty
//  C2  CZ group refuses while a required field is empty, then writes pulses first, then the macro with pointers
//  C3  CZ group (single mover): the moving-qubit choice is written to populate.pairs.<long id>.moving_qubit
//  C4  CZ group (both movers): high / low follow the populate frequencies
//  U3  a '#./inferred_*' default says on hover that it is inferred from the shape
//  O1  outcome(): ok / mixed / refused / none; the result head is a warn / danger verdict for mixed / refused
//  V1  renderReview lists additions in placement order, refusals with their reason
//
// Run: node tests/gen_additions_p3_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }

const F = (raw, has_default, kind, default_repr) => ({ raw, has_default: !!has_default, kind: kind || 'plain', default_repr });
const CZF = {
  flux_pulse_qubit: F('Union[Pulse, str]', false, 'pointer_pulse'),
  coupler_flux_pulse: F('Pulse', true, 'pointer_pulse', 'None'),
  phase_shift_control: F('float', true, 'plain', '0.0'),
};
const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' }, qubit_fields: ['anharmonicity'],
  pulses: {
    'pkg.pulses.FlatTopGaussianPulse': { origin: 'official', fields: {
      length: F('int'), amplitude: F('float'), flat_length: F('int') } },
    'lab_pkg.flux.LabFluxPulse': { origin: 'lab', fields: {
      amplitude: F('float'), flat_length: F('int'),
      length: F('int', true, 'plain', "'#./inferred_length'") } },
  },
  macros_pair: {
    'pkg.gates.CZGate': { origin: 'official', doc: 'A CZ gate.', fields: CZF,
      cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true },
           { field: 'coupler_flux_pulse', on: 'coupler', required: false }] },
    'lab_pkg.gates.TwoFluxGate': { origin: 'lab', fields: Object.assign({}, CZF,
      { flux_pulse_target: F('Union[Pulse, str, None]', true, 'pointer_pulse', 'None') }),
      cz: [{ field: 'flux_pulse_qubit', on: 'control', required: true },
           { field: 'flux_pulse_target', on: 'target', required: false },
           { field: 'coupler_flux_pulse', on: 'coupler', required: false }] },
    'lab_pkg.gates.BothGate': { origin: 'lab', fields: {
      flux_pulse_high: F('Pulse', false, 'pointer_pulse'), flux_pulse_low: F('Pulse', false, 'pointer_pulse') },
      cz: [{ field: 'flux_pulse_high', on: 'high', required: true }, { field: 'flux_pulse_low', on: 'low', required: true }] },
  },
  macros_qubit: { 'pkg.macros.MeasureMacro': { origin: 'official', fields: {}, cz: null } },
};

function world() {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="rev"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = () => Promise.resolve({ json: () => Promise.resolve(ROLES) });
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  return w;
}
const SPEC = () => ({
  qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']],
  lines: [{ element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }, { element: 'q1-q2', line: 'coupler' }],
  populate: { qubit: { q1: { RF_freq: 4.8e9 }, q2: { RF_freq: 5.1e9 } } },
});
const tick = () => new Promise(r => setTimeout(r, 0));
const change = (w, sel, v) => { sel.value = v; sel.dispatchEvent(new w.Event('change')); };

(async () => {
  const w = world();
  const GA = w.GenAdditions;
  const spec = SPEC();
  let saves = 0;
  const host = w.document.getElementById('host');
  const ctx = { spec: () => spec, changed: () => { saves++; } };
  GA.mount(host, ctx);
  await tick(); await tick();
  // M1
  const ow = GA.owners(SPEC()).map(o => o.target);
  ok(JSON.stringify(ow) === JSON.stringify(['qubits.q1.macros', 'qubits.q2.macros', 'qubit_pairs.q1-2.macros']), 'M1 ' + ow);
  ok(GA.pairId('qA1', 'qB2') === 'qA1-B2' && GA.pairId('q10', 'q11') === 'q10-11', 'M1 builder id');

  const prow = () => host.querySelector('.gen-add-owner[data-target="qubit_pairs.q1-2.macros"]');
  ok(!!prow(), 'M2 pair macro row');
  // P5: "+ CZ…" is a row action of the Populate pair table, not of the macro list
  ok(!prow().querySelector('.gen-add-czbtn'), 'P5 no CZ button in the macro list');
  w.document.body.insertAdjacentHTML('beforeend', '<div id="pt"><table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>CZ amp</th></tr></thead>' +
    '<tbody><tr class="gen-pop-setall"><td>Set all</td><td></td></tr><tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td></td></tr></tbody></table></div>');
  GA.decoratePairTable(w.document.getElementById('gen-pop-tbl-pairs'), ctx);
  const ptab = () => w.document.getElementById('gen-pop-tbl-pairs');

  // a pulse addition first (for the pointer picker)
  const zrow = host.querySelector('.gen-add-ch[data-target="qubits.q1.z.operations"]');
  zrow.querySelector('.gen-add-btn').click();
  const ps = zrow.querySelector('select.gen-add-class');
  const opt = Array.from(ps.querySelectorAll('option')).find(o => o.value === 'lab_pkg.flux.LabFluxPulse');
  ok(opt && opt.textContent === 'LabFluxPulse · lab' && /^lab_pkg\.flux\.LabFluxPulse/.test(opt.title), 'U4 option text + path on hover: ' + (opt && opt.textContent));
  change(w, ps, 'lab_pkg.flux.LabFluxPulse');
  const len = zrow.querySelector('input[data-field="length"]');
  ok(len && /Inferred from the shape/.test(len.title) && zrow.querySelector('.gen-add-hint'), 'U3 inferred hover');
  zrow.querySelector('.gen-add-name').value = 'p1';
  zrow.querySelector('input[data-field="amplitude"]').value = '0.1';
  zrow.querySelector('input[data-field="flat_length"]').value = '20';
  zrow.querySelector('.gen-add-ok').click();
  await tick(); await tick();
  ok(spec.additions && spec.additions.length === 1 && !('length' in spec.additions[0].fields), 'U3 inferred default left to the class');

  // M2/M3/M4
  prow().querySelector('.gen-add-mbtn').click();
  const ms = prow().querySelector('select.gen-add-class');
  const vals = Array.from(ms.querySelectorAll('option')).map(o => o.value).filter(Boolean).sort();
  ok(JSON.stringify(vals) === JSON.stringify(['lab_pkg.gates.BothGate', 'lab_pkg.gates.TwoFluxGate', 'pkg.gates.CZGate']), 'M2 pair classes only ' + vals);
  change(w, ms, 'pkg.gates.CZGate');
  const fq = prow().querySelector('input[data-field="flux_pulse_qubit"]');
  const dl = fq && fq.getAttribute('list') && w.document.getElementById(fq.getAttribute('list'));
  const cands = dl ? Array.from(dl.querySelectorAll('option')).map(o => o.value) : [];
  ok(cands.indexOf('#/qubits/q1/z/operations/p1') >= 0, 'M3 pulse addition offered ' + cands);
  prow().querySelector('.gen-add-name').value = 'cz_hand';
  fq.value = '#/qubits/q1/z/operations/p1';
  prow().querySelector('.gen-add-ok').click();
  await tick(); await tick();
  const m = spec.additions[1];
  ok(m && m.kind === 'macro' && m.target === 'qubit_pairs.q1-2.macros' && m['class'] === 'pkg.gates.CZGate'
     && m.fields.flux_pulse_qubit === '#/qubits/q1/z/operations/p1' && !('phase_shift_control' in m.fields), 'M4 macro written');

  // E1 edit
  // an added pair gate is edited where it is listed: its chip in the Qubit pairs table
  const edit = ptab().querySelector('.gen-gate-added[data-gate="cz_hand"]');
  ok(edit && /cz_hand/.test(edit.textContent), 'E1 edit button');
  edit.click();
  const fr = () => ptab().querySelector('.gen-gates-formrow');
  const ef = fr().querySelector('input[data-field="phase_shift_control"]');
  ok(fr().querySelector('.gen-add-ok').textContent === 'Save' && fr().querySelector('.gen-add-name').value === 'cz_hand'
     && fr().querySelector('input[data-field="flux_pulse_qubit"]').value === '#/qubits/q1/z/operations/p1', 'E1 prefilled');
  ef.value = '0.25';
  fr().querySelector('.gen-add-ok').click();
  await tick(); await tick();
  ok(spec.additions.length === 2 && spec.additions[1].fields.phase_shift_control === 0.25, 'E1 replaced in place');

  // C1/C2 two-flux group
  ptab().querySelector('.gen-add-czbtn').click();
  const cs = ptab().querySelector('select.gen-add-cz-class');
  change(w, cs, 'lab_pkg.gates.TwoFluxGate');
  const secs = Array.from(ptab().querySelectorAll('.gen-add-cz-pulse')).map(s => s.getAttribute('data-field'));
  ok(JSON.stringify(secs) === JSON.stringify(['flux_pulse_qubit', 'flux_pulse_target', 'coupler_flux_pulse']), 'C1 sections ' + secs);
  const where = Array.from(ptab().querySelectorAll('.gen-add-cz-where')).map(e => e.textContent);
  ok(where[0] === 'q1 z' && where[1] === 'q2 z', 'C1 control / target ' + where);
  const pcs = ptab().querySelectorAll('select.gen-add-cz-pclass');
  change(w, pcs[0], 'pkg.pulses.FlatTopGaussianPulse');
  change(w, pcs[1], 'pkg.pulses.FlatTopGaussianPulse');
  const amps = ptab().querySelectorAll('.gen-add-cz-pulse input[data-field="amplitude"]');
  ok(amps.length === 2 && amps[0].value === '' && amps[1].value === '', 'C1 amplitudes start empty');
  const n0 = spec.additions.length;
  ptab().querySelector('.gen-add-cz .gen-add-ok').click();
  await tick(); await tick();
  ok(spec.additions.length === n0 && /requires/.test(ptab().querySelector('.gen-add-cz .gen-add-msg').textContent), 'C2 refused while empty');
  Array.from(ptab().querySelectorAll('.gen-add-cz-pulse')).slice(0, 2).forEach(function (sec, i) {
    sec.querySelector('input[data-field="length"]').value = '48';
    sec.querySelector('input[data-field="amplitude"]').value = i ? '0.05' : '0.1';
    sec.querySelector('input[data-field="flat_length"]').value = '32';
  });
  ptab().querySelector('.gen-add-cz .gen-add-ok').click();
  await tick(); await tick();
  const g = spec.additions.slice(n0);
  ok(g.length === 3 && g[0].kind === 'pulse' && g[1].kind === 'pulse' && g[2].kind === 'macro', 'C2 pulses then macro');
  ok(g[0].target === 'qubits.q1.z.operations' && g[1].target === 'qubits.q2.z.operations', 'C2 pulse targets');
  ok(g[2].fields.flux_pulse_qubit === '#/qubits/q1/z/operations/' + g[0].name
     && g[2].fields.flux_pulse_target === '#/qubits/q2/z/operations/' + g[1].name, 'C2 macro points at its pulses');
  ok(!('coupler_flux_pulse' in g[2].fields), 'C2 optional coupler pulse not added unless asked');

  // C3 single mover
  ptab().querySelector('.gen-add-czbtn').click();
  change(w, ptab().querySelector('select.gen-add-cz-class'), 'pkg.gates.CZGate');
  const on = ptab().querySelector('select.gen-add-cz-on');
  ok(!!on, 'C3 moving choice');
  on.value = 'q2';
  change(w, ptab().querySelector('select.gen-add-cz-pclass'), 'pkg.pulses.FlatTopGaussianPulse');
  const sec = ptab().querySelector('.gen-add-cz-pulse');
  sec.querySelector('input[data-field="length"]').value = '40';
  sec.querySelector('input[data-field="amplitude"]').value = '0.1';
  sec.querySelector('input[data-field="flat_length"]').value = '20';
  ptab().querySelector('.gen-add-cz .gen-add-ok').click();
  await tick(); await tick();
  const last = spec.additions[spec.additions.length - 1];
  ok(last.kind === 'macro' && /^#\/qubits\/q2\/z\/operations\//.test(last.fields.flux_pulse_qubit), 'C3 pulse on the chosen qubit');
  ok(spec.populate.pairs && spec.populate.pairs['q1-q2'].moving_qubit === 'target', 'C3 moving qubit written');

  // C4 both movers: q2 is higher (5.1 GHz)
  ptab().querySelector('.gen-add-czbtn').click();
  change(w, ptab().querySelector('select.gen-add-cz-class'), 'lab_pkg.gates.BothGate');
  const w4 = Array.from(ptab().querySelectorAll('.gen-add-cz-where')).map(e => e.textContent);
  ok(w4[0] === 'q2 z (higher f_01)' && w4[1] === 'q1 z (lower f_01)', 'C4 high/low from frequencies ' + w4);

  // O1
  ok(GA.outcome({ added: [1], refused: [] }) === 'ok' && GA.outcome({ added: [1], refused: [1] }) === 'mixed'
     && GA.outcome({ added: [], refused: [1] }) === 'refused' && GA.outcome({}) === 'none', 'O1 outcome');
  const mixed = GA.renderResult({ added: [{ path: 'a.b', class: 'x.Y', origin: 'lab' }], refused: [{ target: 't', name: 'n', class: 'x.Y', reason: 'r' }] });
  ok(mixed.classList.contains('gen-add-result-mixed') && !!mixed.querySelector('.gen-add-result-head.ui-verdict--warn') && /refused/.test(mixed.textContent), 'O1 mixed head: a warn verdict that says refused');
  const bad = GA.renderResult({ added: [], refused: [{ target: 't', name: 'n', class: 'x.Y', reason: 'r' }] });
  ok(bad.classList.contains('gen-add-result-refused') && !!bad.querySelector('.gen-add-result-head.ui-verdict--danger') && /refused/.test(bad.textContent), 'O1 refused head: a danger verdict that says refused');

  // V1
  const rev = w.document.getElementById('rev');
  const r = GA.renderReview(rev, { manifest: true, items: [
    { index: 1, kind: 'pulse', target: 'qubits.q1.z.operations', name: 'p', class: 'x.P', origin: 'official' },
    { index: 0, kind: 'macro', target: 'qubit_pairs.q1-2.macros', name: 'm', class: 'x.M', origin: 'lab', reason: 'is a qubit macro' }] }, spec);
  const lis = Array.from(rev.querySelectorAll('li')).map(l => l.textContent);
  ok(r && r.count === 2 && r.refused === 1 && /^pulse qubits/.test(lis[0]) && /^✗ macro .*is a qubit macro/.test(lis[1]), 'V1 review list ' + lis);
  ok(/1 will be refused/.test(rev.textContent), 'V1 count of refusals');
  ok(saves >= 5, 'drafts saved ' + saves);

  console.log((fails ? 'FAILED ' : 'ok ') + `(${asserts} assertions, ${fails} failed)`);
  process.exit(fails ? 1 : 0);
})();
