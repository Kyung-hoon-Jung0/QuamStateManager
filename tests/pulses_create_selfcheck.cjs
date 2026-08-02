// Env-aware add-pulse form JS (r15, docs/71 §2) — loads the REAL pulses.js
// in jsdom against a hand-built create-form DOM and pins:
//  - createTypeChanged fills the HIDDEN qclass input + the visible display
//    (users never type class paths) and the "env" provenance hint;
//  - env-only classes suppress the preview and show the no-transcription
//    note; switching back restores the plot area;
//  - options whose class the selected env can NOT import are marked;
//  - submitting such a class is PREVENTED until the explicit confirm, after
//    which the request re-fires with force=1 (never-silent);
//  - envStripProbe is exported for the strip's "Probe now".
//
// Run: node tests/pulses_create_selfcheck.cjs   (needs jsdom)
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
const PULSES_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'pulses.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

const CATALOG = {
  SquarePulse: {
    label: 'Square', group: 'Control', doc: 'flat', iq: 'never',
    length_mode: 'explicit', channels: ['xy', 'z', 'resonator'],
    verify: 'env', qclass: 'quam.components.pulses.SquarePulse',
    qclass_how: 'env',
    params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
               default: 0.1, unit: 'V', synth: true, required: true },
             { name: 'length', label: 'Length', kind: 'int', default: 100,
               unit: 'ns', synth: true, required: true }]
  },
  ErfSquarePulse: {
    label: 'Erf square', group: 'Flux / Bipolar', doc: 'erf', iq: 'never',
    length_mode: 'inferred', channels: ['z'],
    verify: 'missing', qclass: 'quam.components.pulses.ErfSquarePulse',
    qclass_how: 'catalog',
    params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
               default: 0.1, unit: 'V', synth: true, required: true }]
  },
  CosineBipolarPulse: {
    label: 'CosineBipolarPulse', group: 'From environment', doc: 'env class',
    iq: 'never', length_mode: 'explicit', channels: ['xy', 'z', 'resonator'],
    verify: 'env', env_only: true,
    qclass: 'quam_builder.architecture.superconducting.components.pulses.CosineBipolarPulse',
    qclass_how: 'env',
    params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
               default: null, unit: '', synth: true, required: true }]
  }
};

const HTML =
  '<div id="pulse-create-root">' +
  '<form class="pulse-create-form">' +
  '  <select name="pulse_type" id="pulse-create-type">' +
  '    <option value="SquarePulse">Square</option>' +
  '    <option value="ErfSquarePulse">Erf square</option>' +
  '    <option value="CosineBipolarPulse">CosineBipolarPulse</option>' +
  '  </select>' +
  '  <p id="pulse-create-hint"></p>' +
  '  <code id="pulse-create-qclass-display"></code>' +
  '  <input type="hidden" name="qclass" id="pulse-create-qclass">' +
  '  <p id="pulse-create-qclass-hint"></p>' +
  '  <div id="pulse-create-fields"></div>' +
  '  <div class="pulse-plot-bar"><span class="pulse-synth-err" hidden></span></div>' +
  '  <div id="pulse-create-plot"></div>' +
  '</form>' +
  '<script id="pulse-catalog-data" type="application/json">' +
  JSON.stringify(CATALOG) + '</scr' + 'ipt>' +
  '<script id="pulse-existing-data" type="application/json">{}</scr' + 'ipt>' +
  '<script id="pulse-pairs-data" type="application/json">{}</scr' + 'ipt>' +
  '<script id="pulse-pair-channels-data" type="application/json">{}</scr' + 'ipt>' +
  '</div>';

const dom = new JSDOM('<!DOCTYPE html><html><body>' + HTML + '</body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
const doc = win.document;
win.fetch = function () { return new win.Promise(function () {}); };
// app.js globals pulses.js leans on (not loaded in this harness)
win._debounce = function (key, fn) { fn(); };
let triggered = 0;
win.htmx = { trigger: function () { triggered++; } };
let confirmCalls = 0, confirmAnswer = false;
win.confirm = function () { confirmCalls++; return confirmAnswer; };

new win.Function(PULSES_JS).call(win);
const P = win.PulsesPage;
ok(typeof P.envStripProbe === 'function', 'P0: envStripProbe exported');

P.initCreate();
const typeSel = doc.getElementById('pulse-create-type');
const root = doc.getElementById('pulse-create-root');

// P1: default type (SquarePulse, how=env) — hidden + display filled, env hint
ok(doc.getElementById('pulse-create-qclass').value ===
   'quam.components.pulses.SquarePulse', 'P1: hidden qclass filled');
ok(doc.getElementById('pulse-create-qclass-display').textContent ===
   'quam.components.pulses.SquarePulse', 'P1: visible display filled');
ok(/verified by the selected environment/.test(
     doc.getElementById('pulse-create-qclass-hint').textContent),
   'P1: env provenance hint');

// P2: missing-in-env option is decorated
const erfOpt = typeSel.querySelector('option[value="ErfSquarePulse"]');
ok(/not in this env/.test(erfOpt.textContent), 'P2: missing option marked');
ok(erfOpt.classList.contains('pulse-opt-envmissing'), 'P2: missing option class');
const sqOpt = typeSel.querySelector('option[value="SquarePulse"]');
ok(!/not in this env/.test(sqOpt.textContent), 'P2: env-ok option unmarked');

// P3: env-only class → preview suppressed + note shown; back → restored
typeSel.value = 'CosineBipolarPulse';
P.createTypeChanged(typeSel);
ok(doc.getElementById('pulse-create-plot').hidden === true, 'P3: plot hidden');
const note = doc.getElementById('pulse-create-envnote');
ok(!!note && /no waveform transcription/.test(note.textContent),
   'P3: no-preview note shown');
typeSel.value = 'SquarePulse';
P.createTypeChanged(typeSel);
ok(doc.getElementById('pulse-create-plot').hidden === false, 'P3: plot restored');
ok(!doc.getElementById('pulse-create-envnote'), 'P3: note removed');

// P4: never-silent confirm on a missing-in-env class
const form = root.querySelector('form.pulse-create-form');
function fireConfigRequest() {
  const evt = new win.CustomEvent('htmx:configRequest',
    { bubbles: true, cancelable: true, detail: { parameters: {} } });
  form.dispatchEvent(evt);
  return evt;
}
typeSel.value = 'ErfSquarePulse';
P.createTypeChanged(typeSel);
ok(/NOT importable/.test(doc.getElementById('pulse-create-hint').textContent),
   'P4: hint warns before submit');
confirmAnswer = false;
let evt = fireConfigRequest();
ok(evt.defaultPrevented, 'P4: submit prevented pending confirm');
ok(confirmCalls === 1, 'P4: confirm asked');
ok(triggered === 0, 'P4: declined → no re-submit');
confirmAnswer = true;
evt = fireConfigRequest();
ok(evt.defaultPrevented && confirmCalls === 2 && triggered === 1,
   'P4: accepted → re-submit triggered');
evt = fireConfigRequest();                     // the htmx re-fire
ok(!evt.defaultPrevented, 'P4: re-fire passes through');
ok(evt.detail.parameters.force === '1', 'P4: re-fire carries force=1');
evt = fireConfigRequest();                     // one-shot: next asks again
ok(evt.defaultPrevented, 'P4: force token is one-shot');

// P5: env-ok class never confirms
typeSel.value = 'SquarePulse';
P.createTypeChanged(typeSel);
const before = confirmCalls;
evt = fireConfigRequest();
ok(!evt.defaultPrevented && confirmCalls === before, 'P5: env-ok submits freely');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK pulses_create_selfcheck');
process.exit(0);
