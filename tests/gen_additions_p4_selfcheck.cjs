// Lab additions (phase 4) -- the readout and pulsed-TWPA presets, EXECUTED under jsdom.
//
//  R1  "+ Readout…" sits on resonator channels only; its class list is readout pulse classes only
//  R2  quam_builder with a MeasureMacro: Add writes the pulse, then a measure macro whose pulse points at it
//  R3  quam_builder without one: no macro checkbox, the reason is shown, Add writes the pulse only
//  R4  an array field typed as a JSON list is stored as a list
//  T1  "+ Pulsed mode…" is offered only on a chip with a TWPA
//  T2  Add writes field additions: the extras flag, the pump_ length given, active_twpa_names when declared
//  T3  a root that does not declare active_twpa_names: no such field is written and the line says why
//  T4  blank pump values keep the builder's (no field written); a second Add replaces, never duplicates
//  F1  renderResult / renderReview read a field entry as "path = value (field)"
//
// Run: node tests/gen_additions_p4_selfcheck.cjs   (needs jsdom)
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
const MM = 'pkg.gates.MeasureMacro';
function roles(withMacro, declared) {
  return {
    ok: true, versions: { quam_builder: withMacro ? '0.6.0' : '0.4.0' }, qubit_fields: [],
    pulses: {
      'pkg.pulses.SquareReadoutPulse': { origin: 'official', readout: true, fields: { length: F('int'), amplitude: F('float') } },
      'lab_pkg.ro.WeightsReadoutPulse': { origin: 'lab', readout: true, fields: {
        length: F('int'), amplitude: F('float'), weights_real: F('List[float]', true, 'plain', 'None') } },
      'pkg.pulses.SquarePulse': { origin: 'official', readout: false, fields: { length: F('int'), amplitude: F('float') } },
    },
    macros_pair: {},
    macros_qubit: withMacro ? { [MM]: { origin: 'official', fields: { pulse: F('Union[ReadoutPulse, str]', true, 'pointer_pulse') } } } : {},
    readout: withMacro ? { mode: 'pulse_and_macro', reason: 'quam_builder 0.6.0 provides MeasureMacro' }
                       : { mode: 'pulse_only', reason: 'quam_builder 0.4.0 has no transmon MeasureMacro' },
    measure_macro: MM,
    roots: { 'pkg.qpu.Root': { origin: 'official', extras: true, active_twpa_names: declared } },
  };
}

function world(r) {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="rev"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = () => Promise.resolve({ json: () => Promise.resolve(r) });
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  return w;
}
const SPEC = (twpa) => ({
  qubits: ['q1'], qubit_pairs: [], twpas: twpa ? ['twpa1'] : [],
  lines: [{ element: 'q1', line: 'resonator' }, { element: 'q1', line: 'drive' }],
});
const tick = () => new Promise(r => setTimeout(r, 0));
const change = (w, sel, v) => { sel.value = v; sel.dispatchEvent(new w.Event('change')); };

async function mounted(r, spec) {
  const w = world(r);
  const host = w.document.getElementById('host');
  const ctx = { spec: () => spec, changed: () => {} };
  w.GenAdditions.mount(host, ctx);
  await tick(); await tick();
  return { w, host, ctx };
}

(async () => {
  // R1 + R2
  {
    const spec = SPEC(false);
    const { w, host } = await mounted(roles(true, true), spec);
    const ro = host.querySelectorAll('.gen-add-robtn');
    ok(ro.length === 1 && ro[0].closest('[data-target]').getAttribute('data-target') === 'qubits.q1.resonator.operations', 'R1 one readout button on the resonator');
    ro[0].click();
    const opts = Array.from(host.querySelectorAll('.gen-add-ro-class option')).map(o => o.value).filter(Boolean);
    ok(opts.length === 2 && opts.indexOf('pkg.pulses.SquarePulse') < 0, 'R1 readout classes only: ' + opts);
    change(w, host.querySelector('.gen-add-ro-class'), 'lab_pkg.ro.WeightsReadoutPulse');
    ok(host.querySelector('.gen-add-ro-name').value === 'readout_weights', 'R2 default name');
    host.querySelector('input[data-field="length"]').value = '400';
    host.querySelector('input[data-field="amplitude"]').value = '0.1';
    host.querySelector('input[data-field="weights_real"]').value = '[0.5, -0.25]';
    ok(!!host.querySelector('.gen-add-ro-use') && host.querySelector('.gen-add-ro-use').checked, 'R2 macro checkbox on');
    host.querySelector('.gen-add-ro .gen-add-ok').click();
  await tick(); await tick();
    const a = spec.additions || [];
    ok(a.length === 2 && a[0].kind === 'pulse' && a[0].target === 'qubits.q1.resonator.operations', 'R2 pulse first ' + JSON.stringify(a));
    ok(a[1] && a[1].kind === 'macro' && a[1].target === 'qubits.q1.macros' && a[1]['class'] === MM &&
       a[1].fields.pulse === '#/qubits/q1/resonator/operations/readout_weights', 'R2 macro points at the pulse');
    ok(Array.isArray(a[0].fields.weights_real) && a[0].fields.weights_real[1] === -0.25, 'R4 list stored as list');
    ok(!host.querySelector('.gen-add-twbtn'), 'T1 no TWPA, no pulsed preset');
  }
  // R3
  {
    const spec = SPEC(false);
    const r3 = roles(false, true);
    // a MeasureMacro class present but the version says pulse only: the version decides
    r3.macros_qubit = roles(true, true).macros_qubit;
    const { w, host } = await mounted(r3, spec);
    host.querySelector('.gen-add-robtn').click();
    ok(!host.querySelector('.gen-add-ro-use'), 'R3 no macro checkbox');
    const why = host.querySelector('.gen-add-ro-only');
    ok(why && /0\.4\.0 has no transmon MeasureMacro/.test(why.textContent), 'R3 reason shown');
    change(w, host.querySelector('.gen-add-ro-class'), 'pkg.pulses.SquareReadoutPulse');
    host.querySelector('input[data-field="length"]').value = '400';
    host.querySelector('input[data-field="amplitude"]').value = '0.1';
    host.querySelector('.gen-add-ro .gen-add-ok').click();
  await tick(); await tick();
    ok((spec.additions || []).length === 1 && spec.additions[0].kind === 'pulse', 'R3 pulse only');
  }
  // T1 + T2 + T4
  {
    const spec = SPEC(true);
    const { host } = await mounted(roles(true, true), spec);
    const b = host.querySelectorAll('.gen-add-twbtn');
    ok(b.length === 1, 'T1 pulsed preset offered on the TWPA');
    b[0].click();
    host.querySelector('.gen-add-tw-len').value = '20000';
    ok(host.querySelector('.gen-add-tw-use-active').checked, 'T2 active list on when the root declares it');
    host.querySelector('.gen-add-twpa .gen-add-ok').click();
  await tick(); await tick();
    const f = spec.additions || [];
    const by = {}; f.forEach(x => { by[x.target] = x; });
    ok(f.every(x => x.kind === 'field'), 'T2 all fields');
    ok(by['extras.twpa_pulse_mode'] && by['extras.twpa_pulse_mode'].value === true, 'T2 flag');
    ok(by['twpas.twpa1.pump_.operations.pump.length'] && by['twpas.twpa1.pump_.operations.pump.length'].value === 20000, 'T2 pump length');
    ok(!by['twpas.twpa1.pump_.operations.pump.amplitude'], 'T4 blank amplitude keeps the builder value');
    ok(by['active_twpa_names'] && JSON.stringify(by['active_twpa_names'].value) === '["twpa1"]', 'T2 active list');
    const rows = host.querySelectorAll('.gen-add-field-item');
    ok(rows.length === 3, 'T2 listed as fields: ' + rows.length);
    host.querySelector('.gen-add-twbtn').click();
    host.querySelector('.gen-add-tw-len').value = '1000';
    host.querySelector('.gen-add-twpa .gen-add-ok').click();
  await tick(); await tick();
    const lens = spec.additions.filter(x => x.target === 'twpas.twpa1.pump_.operations.pump.length');
    ok(lens.length === 1 && lens[0].value === 1000, 'T4 a second Add replaces');
    host.querySelector('.gen-add-twbtn').click();
    host.querySelector('.gen-add-tw-len').value = '12.5';
    host.querySelector('.gen-add-twpa .gen-add-ok').click();
  await tick(); await tick();
    ok(/whole number/.test(host.querySelector('.gen-add-twpa .gen-add-msg').textContent), 'T4 non-integer length refused');
  }
  // T1b: a spec TWPA given as {id} (the wizard's own and every reconstructed chip)
  {
    const spec = SPEC(false); spec.twpas = [{ id: 'twpa1' }];
    const { host } = await mounted(roles(true, true), spec);
    const it = host.querySelector('[data-twpa]');
    ok(it && it.getAttribute('data-twpa') === 'twpa1', 'T1b {id} twpa named by its id');
  }
  // T3
  {
    const spec = SPEC(true);
    const { host } = await mounted(roles(true, false), spec);
    host.querySelector('.gen-add-twbtn').click();
    ok(!host.querySelector('.gen-add-tw-use-active'), 'T3 no active-list control');
    ok(/does not declare it/.test((host.querySelector('.gen-add-tw-noactive') || {}).textContent || ''), 'T3 reason');
    host.querySelector('.gen-add-twpa .gen-add-ok').click();
  await tick(); await tick();
    ok(!(spec.additions || []).some(x => x.target === 'active_twpa_names'), 'T3 not written');
  }
  // F1
  {
    const w = world(roles(true, true));
    const GA = w.GenAdditions;
    const box = GA.renderResult({ added: [{ kind: 'field', path: 'extras.twpa_pulse_mode', value: true }],
                                  refused: [{ kind: 'field', target: '', name: 'active_twpa_names', value: ['t'], reason: 'not declared' }] });
    const t = box.textContent;
    ok(/✓ extras\.twpa_pulse_mode = true \(field\)/.test(t), 'F1 added field: ' + t);
    ok(/✗ active_twpa_names = \["t"\] \(field\) — not declared/.test(t), 'F1 refused root field: ' + t);
    const rev = w.document.getElementById('rev');
    GA.renderReview(rev, null, { additions: [{ kind: 'field', target: 'extras.twpa_pulse_mode', value: true }] });
    ok(/field extras\.twpa_pulse_mode = true/.test(rev.textContent), 'F1 review: ' + rev.textContent);
  }
  console.log((fails ? 'FAILED ' : 'ok ') + '(' + asserts + ' assertions, ' + fails + ' failed)');
  process.exit(fails ? 1 : 0);
})();
