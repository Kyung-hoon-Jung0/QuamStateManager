// gl-tmz P2: a create-form field whose default depends on the target qubit.
// A DRAG pulse's anharmonicity is pre-filled with the value the server names
// for the selected qubit (the pointer its DRAG pulses use, or its own
// anharmonicity), follows a qubit change, never overwrites what the user
// typed, and falls back to the catalog default with a note.
//
// Run: node tests/pulses_qubit_default_selfcheck.cjs   (needs jsdom)
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

const PULSES_JS = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager',
  'web', 'static', 'pulses.js'), 'utf8');

let fails = 0;
let n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const CATALOG = {
  DragCosinePulse: {
    label: 'Drag cosine', group: 'Control', doc: 'xy', iq: 'always',
    length_mode: 'explicit', channels: ['xy'], verify: 'env',
    qclass: 'quam.components.pulses.DragCosinePulse', qclass_how: 'env',
    params: [
      { name: 'amplitude', label: 'Amplitude', kind: 'float', default: 0.1,
        unit: 'V', synth: true, required: true },
      { name: 'anharmonicity', label: 'Anharmonicity', kind: 'float',
        default: -220e6, unit: 'Hz', synth: true, required: true }]
  }
};
const DEFAULTS = {
  q1: { anharmonicity: { value: '#/qubits/q1/anharmonicity', note: 'from DRAG' } },
  q2: { anharmonicity: { value: '#/qubits/q2/anharmonicity', note: 'own' } },
  q3: { anharmonicity: { value: null, note: 'placeholder' } }
};

function js(id, v) {
  return '<script id="' + id + '" type="application/json">' + JSON.stringify(v) +
    '</scr' + 'ipt>';
}
const HTML =
  '<div id="pulse-create-root"><form class="pulse-create-form">' +
  '<input type="radio" name="target_kind" value="qubit" checked>' +
  '<select name="qubit"><option>q1</option><option>q2</option><option>q3</option></select>' +
  '<select name="channel"><option>xy</option></select>' +
  '<select name="pulse_type" id="pulse-create-type">' +
  '<option value="DragCosinePulse">Drag cosine</option></select>' +
  '<p id="pulse-create-hint"></p><code id="pulse-create-qclass-display"></code>' +
  '<input type="hidden" name="qclass" id="pulse-create-qclass">' +
  '<p id="pulse-create-qclass-hint"></p><div id="pulse-create-fields"></div>' +
  '<div class="pulse-plot-bar"><span class="pulse-synth-err" hidden></span></div>' +
  '<div id="pulse-create-plot"></div></form>' +
  js('pulse-catalog-data', CATALOG) + js('pulse-existing-data', {}) +
  js('pulse-qubit-defaults-data', DEFAULTS) + js('pulse-pairs-data', {}) +
  js('pulse-pair-channels-data', {}) + js('pulse-pairs-info-data', {}) +
  js('pulse-gate-defs-data', {}) + '</div>';

const dom = new JSDOM('<!DOCTYPE html><html><body>' + HTML + '</body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
const doc = win.document;
win.fetch = function () { return new win.Promise(function () {}); };
win._debounce = function (key, fn) { fn(); };
win.htmx = { trigger: function () {} };
new win.Function(PULSES_JS).call(win);
win.PulsesPage.initCreate();

const qsel = doc.querySelector('select[name="qubit"]');
function anh() { return doc.querySelector('#pulse-create-fields input[name="anharmonicity"]'); }
function note() { return anh().parentNode.querySelector('[data-field-note]'); }
function pick(q) {
  qsel.value = q;
  qsel.dispatchEvent(new win.Event('change', { bubbles: true }));
}

ok(anh() && anh().value === '#/qubits/q1/anharmonicity', 'D1: q1 default is its DRAG pointer');
ok(note() && note().textContent === 'from DRAG', 'D1: the note says where it came from');
ok(doc.querySelector('#pulse-create-fields input[name="amplitude"]').value === '0.1',
   'D1: other fields keep the catalog default');
pick('q2');
ok(anh().value === '#/qubits/q2/anharmonicity', 'D2: a qubit change follows');
pick('q3');
ok(anh().value === '-220000000', 'D3: no qubit value -> the catalog default');
ok(note().textContent === 'placeholder', 'D3: ... with the placeholder note');
pick('q1');
anh().value = '123';
anh().dispatchEvent(new win.Event('input', { bubbles: true }));
pick('q2');
ok(anh().value === '123', 'D4: what the user typed is kept across a qubit change');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('pulses_qubit_default_selfcheck: OK (' + n + ' assertions)');
