// Lab additions (phase 2) -- the wizard's "+ Add pulse" section and the build
// result panel, EXECUTED under jsdom (a source grep has survived `if (false)`
// in this project before).
//
//  A1  channels() lists xy / z / resonator per qubit line, and the coupler of a pair
//  A2  mount() renders one row per channel, with the class count
//  A3  the class select groups lab and official classes, labelled
//  A4  choosing a class generates its fields: required marked, defaults prefilled, doc as hover
//  A5  a field gets a pointer picker (datalist) with the target qubit's fields
//  A6  Add writes spec.additions, omits fields left at their default, and saves the draft
//  A7  a missing required field is refused in the form (no spec write)
//  A8  ✕ removes the addition; the last one removes the key (spec stays byte-identical)
//  R1  renderResult lists added and refused, refused in red with the reason
//  R2  an empty report says 0 added and nothing refused
//
// Run: node tests/gen_additions_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }

const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' },
  qubit_fields: ['anharmonicity', 'f_01'],
  pulses: {
    'pkg.pulses.DragCosinePulse': { origin: 'official', doc: 'A DRAG cosine pulse.', fields: {
      length: { raw: "<class 'int'>", kind: 'plain', has_default: false, doc: 'Samples.' },
      amplitude: { raw: "<class 'float'>", kind: 'plain', has_default: false },
      anharmonicity: { raw: "<class 'float'>", kind: 'plain', has_default: false, doc: 'Anharmonicity in Hz.' },
      detuning: { raw: "<class 'float'>", kind: 'plain', has_default: true, default_repr: '0.0' } } },
    'lab_pkg.flux.LabFluxPulse': { origin: 'lab', doc: 'A lab flux pulse.', fields: {
      amplitude: { raw: 'float', kind: 'plain', has_default: false },
      flat_length: { raw: 'int', kind: 'plain', has_default: false },
      padding: { raw: 'int', kind: 'plain', has_default: true, default_repr: '0' } } },
  },
};

function world() {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="res"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = () => Promise.resolve({ json: () => Promise.resolve(ROLES) });
  w.eval(JS);
  return w;
}
const SPEC = () => ({
  qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']],
  lines: [{ element: 'q1', line: 'drive' }, { element: 'q1', line: 'flux' }, { element: 'q2', line: 'resonator' },
          { element: 'q1-q2', line: 'coupler' }],
});
const tick = () => new Promise(r => setTimeout(r, 0));

(async () => {
  const w = world();
  const GA = w.GenAdditions;
  const chs = GA.channels(SPEC()).map(c => c.target);
  ok(JSON.stringify(chs) === JSON.stringify(['qubits.q1.xy.operations', 'qubits.q1.z.operations',
    'qubits.q2.resonator.operations', 'qubit_pairs.q1-2.coupler.operations']), 'A1 channels ' + chs);

  const spec = SPEC();
  let saves = 0;
  const host = w.document.getElementById('host');
  const ctx = { spec: () => spec, changed: () => { saves++; } };
  GA.mount(host, ctx);
  await tick(); await tick();
  ok(host.querySelectorAll('.gen-add-ch').length === 4, 'A2 one row per channel');
  ok(/1 official and 1 lab/.test(host.textContent), 'A2 class count');

  const zrow = host.querySelector('.gen-add-ch[data-target="qubits.q1.z.operations"]');
  zrow.querySelector('.gen-add-btn').click();
  const sel = zrow.querySelector('select.gen-add-class');
  const groups = Array.from(sel.querySelectorAll('optgroup')).map(g => g.label);
  ok(groups.join('|') === 'Lab classes|Official classes', 'A3 groups ' + groups);

  sel.value = 'lab_pkg.flux.LabFluxPulse';
  sel.dispatchEvent(new w.Event('change'));
  const inputs = zrow.querySelectorAll('input[data-field]');
  ok(inputs.length === 3, 'A4 three fields');
  ok(zrow.querySelectorAll('.gen-add-required').length === 2, 'A4 two required marked');
  const pad = zrow.querySelector('input[data-field="padding"]');
  ok(pad.value === '0', 'A4 default prefilled');
  ok(/Lab class/.test(zrow.textContent) && /A lab flux pulse/.test(zrow.textContent), 'A4 origin + doc shown');

  // A7 first: required missing
  zrow.querySelector('.gen-add-name').value = 'snz';
  zrow.querySelector('input[data-field="amplitude"]').value = '0.1';
  zrow.querySelector('.gen-add-ok').click();
  ok(!spec.additions, 'A7 no spec write when a required field is empty');
  ok(/Required: flat_length/.test(zrow.textContent), 'A7 says which');

  zrow.querySelector('input[data-field="flat_length"]').value = '20';
  zrow.querySelector('.gen-add-ok').click();
  ok(Array.isArray(spec.additions) && spec.additions.length === 1, 'A6 one addition');
  const a = spec.additions[0];
  ok(a.kind === 'pulse' && a.target === 'qubits.q1.z.operations' && a.name === 'snz'
     && a['class'] === 'lab_pkg.flux.LabFluxPulse', 'A6 shape');
  ok(a.fields.amplitude === 0.1 && a.fields.flat_length === 20 && !('padding' in a.fields), 'A6 values, default omitted');
  ok(saves === 1, 'A6 draft saved');

  // A5 pointer picker on an xy DragCosine
  const xy = host.querySelector('.gen-add-ch[data-target="qubits.q1.xy.operations"]');
  xy.querySelector('.gen-add-btn').click();
  const s2 = xy.querySelector('select.gen-add-class');
  s2.value = 'pkg.pulses.DragCosinePulse'; s2.dispatchEvent(new w.Event('change'));
  const an = xy.querySelector('input[data-field="anharmonicity"]');
  const dl = an.getAttribute('list') && w.document.getElementById(an.getAttribute('list'));
  const opts = dl ? Array.from(dl.querySelectorAll('option')).map(o => o.value) : [];
  ok(opts.indexOf('#/qubits/q1/anharmonicity') >= 0, 'A5 qubit field offered ' + opts);
  ok(/Anharmonicity in Hz/.test(an.title), 'A4 doc as hover');

  // A8 remove
  const rm = host.querySelector('.gen-add-ch[data-target="qubits.q1.z.operations"] .gen-add-rm');
  ok(!!rm, 'A8 remove button present');
  rm.click();
  ok(!('additions' in spec), 'A8 last removal drops the key');

  // R1/R2
  const node = GA.renderResult({
    added: [{ path: 'qubits.q1.z.operations.snz', class: 'lab_pkg.flux.LabFluxPulse', origin: 'lab' }],
    refused: [{ target: 'qubits.q2.z.operations', name: 'odd', class: 'lab_pkg.flux.LabFluxPulse',
                reason: 'the environment refused it: flat_length must be even' }],
    notes: [] });
  ok(/1 added, 1 refused/.test(node.textContent), 'R1 counts');
  const red = node.querySelector('.gen-add-refused');
  ok(red && /must be even/.test(red.textContent) && /d33|danger/.test(red.getAttribute('style') || ''), 'R1 refused in red with reason');
  ok(node.querySelector('.gen-add-ok-item') && /qubits\.q1\.z\.operations\.snz/.test(node.textContent), 'R1 added listed');
  const empty = GA.renderResult({ added: [], refused: [] });
  ok(/0 added/.test(empty.textContent) && !empty.querySelector('.gen-add-refused'), 'R2 empty');

  console.log((fails ? 'FAILED ' : 'ok ') + `(${asserts} assertions, ${fails} failed)`);
  process.exit(fails ? 1 : 0);
})();
