// Golden A: what the Generate wizard's add forms write, EXECUTED under jsdom.
//
// Every add path of gen-additions.js (+ Add pulse, + Add macro, + CZ… with a
// moving qubit / high-low roles / the optional coupler pulse, + Readout…,
// + Pulsed mode…, the Default select) is driven on three chip shapes, and the
// resulting spec.additions plus spec.populate.pairs is printed as JSON after
// every step. tests/test_add_paths_golden.py compares it with
// tests/golden/add_paths_a.json, which later refactors must keep identical.
//
// Run: node tests/add_paths_golden_a.cjs   (needs jsdom; prints JSON)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');

const F = (raw, has_default, kind, default_repr) => ({ raw, has_default: !!has_default, kind: kind || 'plain', default_repr });
const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' }, qubit_fields: ['f_01'],
  pulses: {
    'pkg.pulses.SquarePulse': { origin: 'official', fields: { length: F('int'), amplitude: F('float') } },
    'lab.pulses.FluxPulse': { origin: 'lab', fields: { length: F('int', true, 'plain', '40'), amplitude: F('float') } },
    'pkg.pulses.SquareReadoutPulse': { origin: 'official', readout: true,
      fields: { length: F('int'), amplitude: F('float'), threshold: F('float', true, 'plain', '0.0') } },
  },
  macros_pair: {
    'pkg.gates.CZGate': { origin: 'official', fields: { flux_pulse_qubit: F('Union[Pulse, str]', false, 'pointer_pulse'),
      phase_shift_control: F('float', true, 'plain', '0.0') },
      cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true }] },
    'lab.gates.BothMoversGate': { origin: 'lab', default_name: 'cz_bm',
      fields: { flux_pulse_high: F('Union[Pulse, str]', false, 'pointer_pulse'),
                flux_pulse_low: F('Union[Pulse, str]', false, 'pointer_pulse'),
                coupler_flux_pulse: F('Union[Pulse, str]', true, 'pointer_pulse', 'None'),
                wait: F('int', true, 'plain', '4') },
      cz: [{ field: 'flux_pulse_high', on: 'high', required: true },
           { field: 'flux_pulse_low', on: 'low', required: true },
           { field: 'coupler_flux_pulse', on: 'coupler', required: false }] },
    'pkg.gates.CRGate': { origin: 'official', fields: { qc_correction_phase: F('float', true, 'plain', '0.0') } },
  },
  macros_qubit: {
    'pkg.macros.MeasureMacro': { origin: 'official', fields: { pulse: F('Union[Pulse, str]', false, 'pointer_pulse') } },
    'lab.macros.ResetMacro': { origin: 'lab', fields: { pi_pulse: F('Union[Pulse, str]', false, 'pointer_pulse'), rounds: F('int', true, 'plain', '1') } },
  },
  measure_macro: 'pkg.macros.MeasureMacro',
  readout: { mode: 'pulse_and_macro' },
  roots: { 'pkg.root.Quam': { origin: 'official', extras: true, active_twpa_names: true } },
};

const LINES = (qs, extra) => qs.flatMap(q => ['drive', 'flux', 'resonator'].map(l => ({ element: q, line: l }))).concat(extra || []);
const SHAPES = {
  // three qubits in a chain, a coupler on the first pair, RF frequencies only for q1/q2
  chain3_coupler: () => ({
    qubits: ['q1', 'q2', 'q3'], qubit_pairs: [['q1', 'q2'], ['q2', 'q3']], pair_gate: 'cz_tunable',
    twpas: ['twpa1'], lines: LINES(['q1', 'q2', 'q3'], [{ element: 'q1-q2', line: 'coupler' }]),
    populate: { qubit: { q1: { RF_freq: 5.1e9 }, q2: { RF_freq: 4.8e9 } },
                pairs: { 'q1-q2': { cz_variant: 'all' }, 'q2-q3': { cz_variant: 'all' } } },
  }),
  // two qubits, no coupler, no frequencies: the roles are chosen in the form
  pair2_plain: () => ({
    qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], pair_gate: 'cz_tunable',
    lines: LINES(['q1', 'q2']), populate: { pairs: { 'q1-q2': { cz_variant: 'unipolar' } } },
  }),
  // letter-named qubits with a coupler and frequencies where the target is higher
  lettered_coupler: () => ({
    qubits: ['qA1', 'qB2'], qubit_pairs: [['qA1', 'qB2']], pair_gate: 'cz_coupler',
    lines: LINES(['qA1', 'qB2'], [{ element: 'qA1-qB2', line: 'coupler' }]),
    populate: { qubit: { qA1: { RF_freq: 4.5e9 }, qB2: { RF_freq: 5.0e9 } }, pairs: {} },
  }),
};

function world() {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="pt"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(/lint/.test(String(url)) ? { ok: true, results: [] } : ROLES) });
  w.confirm = () => true;
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  return w;
}
function table(spec) {
  const rows = spec.qubit_pairs.map(p => '<tr data-group="pairs" data-rid="' + p[0] + '-' + p[1] + '"><td>' + p[0] + '-' + p[1] + '</td><td></td></tr>').join('');
  return '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>x</th></tr></thead><tbody>' +
    '<tr class="gen-pop-setall"><td>Set all</td><td></td></tr>' + rows + '</tbody></table>';
}
const tick = () => new Promise(r => setTimeout(r, 0));
const change = (w, sel, v) => { sel.value = v; sel.dispatchEvent(new w.Event('change', { bubbles: true })); };
const setv = (inp, v) => { inp.value = v; };

async function run(shapeName) {
  const w = world();
  const spec = SHAPES[shapeName]();
  const ctx = { spec: () => spec, changed: () => {} };
  const d = w.document;
  d.getElementById('pt').innerHTML = table(spec);
  const GA = w.GenAdditions;
  GA.mount(d.getElementById('host'), ctx);
  GA.decoratePairTable(d.getElementById('gen-pop-tbl-pairs'), ctx);
  await tick(); await tick();
  const steps = [];
  const snap = (label, extra) => steps.push(Object.assign({ step: label,
    additions: JSON.parse(JSON.stringify(spec.additions || null)),
    populate_pairs: JSON.parse(JSON.stringify((spec.populate || {}).pairs || null)) }, extra || {}));
  const msgOf = (f) => (f.querySelector('.gen-add-msg') || {}).textContent || '';
  const q0 = spec.qubits[0], q1 = spec.qubits[1];

  // 1. + Add pulse on the first qubit's xy channel (a required field typed, a default left alone)
  let ch = d.querySelector('.gen-add-ch[data-target="qubits.' + q0 + '.xy.operations"]');
  ch.querySelector('.gen-add-btn').click();
  let f = ch.querySelector('.gen-add-form');
  change(w, f.querySelector('select.gen-add-class'), 'lab.pulses.FluxPulse');
  setv(f.querySelector('.gen-add-name'), 'x_lab');
  setv(f.querySelector('input[data-field="amplitude"]'), '0.25');
  f.querySelector('.gen-add-ok').click();
  await tick(); await tick();
  snap('pulse_xy', { msg: msgOf(f) });

  // 2. + Add macro on the qubit (a pointer field from the offered list)
  let ow = d.querySelector('.gen-add-owner[data-target="qubits.' + q0 + '.macros"]');
  ow.querySelector('.gen-add-mbtn').click();
  f = ow.querySelector('.gen-add-form');
  change(w, f.querySelector('select.gen-add-class'), 'lab.macros.ResetMacro');
  setv(f.querySelector('.gen-add-name'), 'reset_lab');
  const pin = f.querySelector('input[data-field="pi_pulse"]');
  const dl = pin.getAttribute('list') ? d.getElementById(pin.getAttribute('list')) : null;
  setv(pin, dl && dl.options.length ? dl.options[0].value : '#/qubits/' + q0 + '/xy/operations/x_lab');
  setv(f.querySelector('input[data-field="rounds"]'), '3');
  f.querySelector('.gen-add-ok').click();
  await tick(); await tick();
  snap('macro_qubit', { offered: dl ? Array.from(dl.options).map(o => o.value) : [] });

  // 3. + Readout… on the first qubit's resonator, with the measure macro
  ch = d.querySelector('.gen-add-ch[data-target="qubits.' + q0 + '.resonator.operations"]');
  ch.querySelector('.gen-add-robtn').click();
  f = ch.querySelector('.gen-add-ro');
  change(w, f.querySelector('select.gen-add-ro-class'), 'pkg.pulses.SquareReadoutPulse');
  setv(f.querySelector('input[data-field="length"]'), '1000');
  setv(f.querySelector('input[data-field="amplitude"]'), '0.05');
  f.querySelector('.gen-add-ok').click();
  await tick(); await tick();
  snap('readout', { msg: msgOf(f) });

  // 4. + CZ… (moving qubit chosen in the form) on every pair
  const rows = () => Array.from(d.querySelectorAll('#gen-pop-tbl-pairs tbody tr[data-rid]')).filter(r => !r.classList.contains('gen-gates-formrow'));
  for (const [k, r] of rows().entries()) {
    const rid = r.getAttribute('data-rid');
    r.querySelector('.gen-add-czbtn').click();
    const form = d.querySelector('tr.gen-gates-formrow[data-rid="' + rid + '"] .gen-add-cz');
    change(w, form.querySelector('select.gen-add-cz-class'), 'pkg.gates.CZGate');
    setv(form.querySelector('.gen-add-name'), 'cz_mine');
    const sec = form.querySelector('.gen-add-cz-pulse[data-field="flux_pulse_qubit"]');
    if (k % 2 === 1 && sec.querySelector('select.gen-add-cz-on')) change(w, sec.querySelector('select.gen-add-cz-on'), sec.querySelectorAll('select.gen-add-cz-on option')[1].value);
    change(w, sec.querySelector('select.gen-add-cz-pclass'), 'pkg.pulses.SquarePulse');
    setv(sec.querySelector('input[data-field="length"]'), '60');
    setv(sec.querySelector('input[data-field="amplitude"]'), '0.12');
    form.querySelector('.gen-add-ok').click();
  await tick(); await tick();
    snap('cz_moving_' + rid, { msg: msgOf(form) });
  }

  // 5. + CZ… with the high/low roles (and the optional coupler pulse where the pair has one)
  for (const r of rows()) {
    const rid = r.getAttribute('data-rid');
    r.querySelector('.gen-add-czbtn').click();
    const form = d.querySelector('tr.gen-gates-formrow[data-rid="' + rid + '"] .gen-add-cz');
    change(w, form.querySelector('select.gen-add-cz-class'), 'lab.gates.BothMoversGate');
    const where = {};
    for (const fld of ['flux_pulse_high', 'flux_pulse_low', 'coupler_flux_pulse']) {
      const sec = form.querySelector('.gen-add-cz-pulse[data-field="' + fld + '"]');
      if (!sec) continue;
      const use = sec.querySelector('input.gen-add-cz-use');
      if (use) { use.checked = true; use.dispatchEvent(new w.Event('change', { bubbles: true })); }
      const on = sec.querySelector('select.gen-add-cz-on');
      where[fld] = on ? 'chosen:' + on.value : (sec.querySelector('.gen-add-cz-where') || {}).textContent;
      change(w, sec.querySelector('select.gen-add-cz-pclass'), 'lab.pulses.FluxPulse');
      setv(sec.querySelector('input[data-field="amplitude"]'), fld === 'coupler_flux_pulse' ? '0.3' : '0.07');
    }
    setv(form.querySelector('.gen-add-cz-macro input[data-field="wait"]'), '8');
    form.querySelector('.gen-add-ok').click();
  await tick(); await tick();
    snap('cz_highlow_' + rid, { msg: msgOf(form), where: where });
  }

  // 6. the Default select on the first pair names the added gate
  const r0 = rows()[0];
  change(w, r0.querySelector('select.gen-gate-default-sel'), 'cz_mine');
  snap('default_gate', { options: Array.from(rows()[0].querySelector('select.gen-gate-default-sel').options).map(o => o.value) });

  // 7. a refused name writes nothing
  r0.querySelector('.gen-add-czbtn').click();
  const rf = d.querySelector('tr.gen-gates-formrow[data-rid="' + r0.getAttribute('data-rid') + '"] .gen-add-cz');
  change(w, rf.querySelector('select.gen-add-cz-class'), 'pkg.gates.CZGate');
  setv(rf.querySelector('.gen-add-name'), 'cz_mine');
  const rsec = rf.querySelector('.gen-add-cz-pulse[data-field="flux_pulse_qubit"]');
  change(w, rsec.querySelector('select.gen-add-cz-pclass'), 'pkg.pulses.SquarePulse');
  setv(rsec.querySelector('input[data-field="length"]'), '60');
  setv(rsec.querySelector('input[data-field="amplitude"]'), '0.12');
  rf.querySelector('.gen-add-ok').click();
  await tick(); await tick();
  snap('cz_refused', { msg: msgOf(rf) });

  // 8. + Pulsed mode… on a TWPA (only where the chip has one)
  const tw = d.querySelector('.gen-add-twbtn');
  if (tw) {
    tw.click();
    const tf = d.querySelector('.gen-add-twpa');
    setv(tf.querySelector('.gen-add-tw-len'), '200');
    tf.querySelector('.gen-add-ok').click();
  await tick(); await tick();
    snap('twpa_pulsed', { msg: msgOf(tf) });
  }
  void q1;
  return steps;
}

(async () => {
  const out = {};
  for (const name of Object.keys(SHAPES)) out[name] = await run(name);
  process.stdout.write(JSON.stringify(out, null, 1) + '\n');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
