// Lab additions (phase 5) -- ONE list of 2Q gates per pair, EXECUTED under jsdom.
//
//  G1  the Gates column lists what the build makes (per pair gate + cz_variant) and what is added
//  G2  the Default select writes ONE default_gate addition (macros.cz -> gate); "none" removes it
//  G3  an added gate may not take a builder name or the default alias (CZ form and macro form)
//  G4  removing an added gate also removes a default that named it
//  G5  the rule is stated in one line above the table
//  G6  changing the row's CZ variant redraws its gates
//  G7  a tunable-coupler chip: the build's own cz alias marks cz_coupler as default
//  G8  review / result read a default_gate entry as "target.cz -> gate (default gate)"
//  U2  the pulsed-TWPA pump fields prefill nothing and say a blank keeps the builder's
//
// Run: node tests/gen_additions_p5_selfcheck.cjs   (needs jsdom)
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
const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' }, qubit_fields: [],
  pulses: { 'pkg.pulses.SquarePulse': { origin: 'official', fields: { length: F('int'), amplitude: F('float') } } },
  macros_pair: {
    'pkg.gates.CZGate': { origin: 'official', fields: { flux_pulse_qubit: F('Union[Pulse, str]', false, 'pointer_pulse') },
      cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true }] },
  },
  macros_qubit: {}, roots: {},
};

function world() {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="rev"></div><div id="pt"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = () => Promise.resolve({ json: () => Promise.resolve(ROLES) });
  w.eval(JS);
  return w;
}
const TABLE = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>CZ variant</th></tr></thead><tbody>' +
  '<tr class="gen-pop-setall"><td>Set all</td><td></td></tr>' +
  '<tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td><select class="v"><option>all</option><option>SNZ</option></select></td></tr></tbody></table>';
const SPEC = (gate) => ({
  qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], pair_gate: gate || 'cz_tunable', twpas: ['twpa1'],
  lines: [{ element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }],
  populate: { pairs: { 'q1-q2': { cz_variant: 'all' } } },
});
const tick = () => new Promise(r => setTimeout(r, 0));
const change = (w, sel, v) => { sel.value = v; sel.dispatchEvent(new w.Event('change', { bubbles: true })); };

async function setup(gate) {
  const w = world();
  const spec = SPEC(gate);
  const ctx = { spec: () => spec, changed: () => {} };
  w.document.getElementById('pt').innerHTML = TABLE;
  w.GenAdditions.mount(w.document.getElementById('host'), ctx);
  w.GenAdditions.decoratePairTable(w.document.getElementById('gen-pop-tbl-pairs'), ctx);
  await tick(); await tick();
  const row = () => w.document.querySelector('#gen-pop-tbl-pairs tr[data-rid="q1-q2"]');
  const gates = () => Array.from(row().querySelectorAll('.gen-gate')).map(g => g.getAttribute('data-gate'));
  return { w, spec, ctx, row, gates, GA: w.GenAdditions };
}

(async () => {
  // G1 + G5
  let s = await setup();
  ok(JSON.stringify(s.gates()) === JSON.stringify(['cz_unipolar', 'cz_flattop', 'cz_bipolar', 'cz_SNZ', 'cz_flattop_erf']), 'G1 builder gates ' + s.gates());
  ok(s.w.document.querySelector('#gen-pop-tbl-pairs thead .gen-gates-th') && s.w.document.querySelectorAll('#gen-pop-tbl-pairs thead th').length === 3, 'G1 one Gates header');
  const rule = s.w.document.querySelector('#pt .gen-gates-rule');
  ok(rule && /macros\.cz/.test(rule.textContent) && /cz_unipolar/.test(rule.textContent) && /never share a name/.test(rule.textContent), 'G5 rule line');
  ok(s.w.document.querySelectorAll('#pt .gen-gates-rule').length === 1, 'G5 stated once');

  // G6 variant change
  s.spec.populate.pairs['q1-q2'].cz_variant = 'SNZ';
  change(s.w, s.row().querySelector('select.v'), 'SNZ');
  await tick();
  ok(JSON.stringify(s.gates()) === JSON.stringify(['cz_SNZ']), 'G6 variant redraws ' + s.gates());

  // G3 CZ form refuses a builder name
  s.row().querySelector('.gen-add-czbtn').click();
  const form = () => s.w.document.querySelector('tr.gen-gates-formrow .gen-add-cz');
  ok(!!form(), 'G3 CZ form opens under the row');
  change(s.w, form().querySelector('select.gen-add-cz-class'), 'pkg.gates.CZGate');
  form().querySelector('.gen-add-name').value = 'cz_SNZ';
  change(s.w, form().querySelector('select.gen-add-cz-pclass'), 'pkg.pulses.SquarePulse');
  form().querySelector('input[data-field="length"]').value = '40';
  form().querySelector('input[data-field="amplitude"]').value = '0.1';
  form().querySelector('.gen-add-ok').click();
  ok(!s.spec.additions && /already makes a gate named cz_SNZ/.test(form().querySelector('.gen-add-msg').textContent), 'G3 builder name refused');
  form().querySelector('.gen-add-name').value = 'cz_mine';
  form().querySelector('.gen-add-ok').click();
  ok(s.spec.additions && s.spec.additions.filter(a => a.kind === 'macro').length === 1, 'G3 a free name is added');
  ok(JSON.stringify(s.gates()) === JSON.stringify(['cz_SNZ', 'cz_mine']), 'G1 added gate listed in the row ' + s.gates());

  // G2 default
  const dsel = () => s.row().querySelector('select.gen-gate-default-sel');
  ok(Array.from(dsel().options).map(o => o.value).join() === ',cz_SNZ,cz_mine', 'G2 options ' + Array.from(dsel().options).map(o => o.value));
  change(s.w, dsel(), 'cz_mine');
  let d = s.spec.additions.filter(a => a.kind === 'default_gate');
  ok(d.length === 1 && d[0].target === 'qubit_pairs.q1-2.macros' && d[0].name === 'cz' && d[0].to === 'cz_mine', 'G2 one default written');
  ok(s.row().querySelector('.gen-gate-default').getAttribute('data-gate') === 'cz_mine', 'G2 star on the default');
  change(s.w, dsel(), 'cz_SNZ');
  d = s.spec.additions.filter(a => a.kind === 'default_gate');
  ok(d.length === 1 && d[0].to === 'cz_SNZ', 'G2 replaced, never two');
  // G3 the macro form refuses the alias name
  ok(/default-gate alias/.test(s.GA.gateNameClash(s.spec, 'qubit_pairs.q1-2.macros', 'cz', -1) || ''), 'G3 alias name refused');
  change(s.w, dsel(), '');
  ok(!s.spec.additions.some(a => a.kind === 'default_gate'), 'G2 none removes it');

  // G4 removing the gate removes its default
  change(s.w, dsel(), 'cz_mine');
  // an added gate is listed (and removed) once: in the Qubit pairs table
  const rm = s.row().querySelector('.gen-gate-rm[data-gate="cz_mine"]');
  rm.click();
  ok(!(s.spec.additions || []).some(a => a.kind === 'default_gate' || a.kind === 'macro'), 'G4 default goes with its gate');
  ok(JSON.stringify(s.gates()) === JSON.stringify(['cz_SNZ']), 'G4 row redrawn ' + s.gates());

  // G7 coupler chip
  s = await setup('cz_coupler');
  ok(JSON.stringify(s.gates()) === JSON.stringify(['cz_coupler']), 'G7 coupler gates ' + s.gates());
  ok(s.row().querySelector('.gen-gate-default').getAttribute('data-gate') === 'cz_coupler'
     && /the build's \(cz_coupler\)/.test(s.row().querySelector('select.gen-gate-default-sel').options[0].textContent), 'G7 the build marks its default');
  ok(/already makes a gate named cz/.test(s.GA.gateNameClash(s.spec, 'qubit_pairs.q1-2.macros', 'cz', -1) || ''), 'G7 cz is the build\'s on a coupler chip');

  // G8
  const rev = s.w.document.getElementById('rev');
  s.GA.renderReview(rev, null, { additions: [{ kind: 'default_gate', target: 'qubit_pairs.q1-2.macros', name: 'cz', to: 'cz_x' }] });
  ok(/qubit_pairs\.q1-2\.macros\.cz → cz_x \(default gate\)/.test(rev.textContent), 'G8 review ' + rev.textContent);
  const res = s.GA.renderResult({ added: [{ kind: 'default_gate', target: 'qubit_pairs.q1-2.macros', name: 'cz', to: 'cz_x' }], refused: [] });
  ok(/✓ qubit_pairs\.q1-2\.macros\.cz → cz_x/.test(res.textContent), 'G8 result');

  // U2
  const tw = s.w.document.querySelector('.gen-add-twbtn');
  tw.click();
  const len = s.w.document.querySelector('.gen-add-tw-len');
  ok(len && len.value === '' && /builder/.test(len.placeholder), 'U2 nothing prefilled ' + (len && len.placeholder));

  // G9 a re-generate says where its additions came from
  const s9 = await setup();
  s9.spec.additions = [{ kind: 'field', target: 'extras.x', value: 1, env: { python: 'p', versions: { quam_builder: '0.6.0' } } }];
  s9.GA.mount(s9.w.document.getElementById('host'), s9.ctx);
  const car = s9.w.document.querySelector('.gen-add-carried');
  ok(car && /1 addition carried from this chip's own build record \(recorded with quam_builder 0\.6\.0\)/.test(car.textContent), 'G9 carried line');
  s9.spec.additions = [{ kind: 'field', target: 'extras.x', value: 1 }];
  s9.GA.mount(s9.w.document.getElementById('host'), s9.ctx);
  ok(!s9.w.document.querySelector('.gen-add-carried'), 'G9 not on a fresh spec');

  console.log((fails ? 'FAILED ' : 'ok ') + `(${asserts} assertions, ${fails} failed)`);
  process.exit(fails ? 1 : 0);
})();
