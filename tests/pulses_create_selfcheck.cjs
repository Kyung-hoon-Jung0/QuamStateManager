// Env-aware add-pulse form JS (r15, docs/71 §2) — loads the REAL pulses.js
// in jsdom against a hand-built create-form DOM and pins:
//  - createTypeChanged fills the HIDDEN qclass input + the visible display
//    (users never type class paths) and the "env" provenance hint;
//  - env-only classes show the no-transcription note and a "draw with the
//    class's own code" button instead of an automatic preview (docs/2xx
//    adaptive pulses); the button posts qclass + the typed values to
//    /api/pulse/lab-waveform and draws the answer; switching back to a
//    synthesized class removes the button;
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
    label: 'CosineBipolarPulse', group: 'From environment',
    // the REAL sentence env_creatable_specs puts on these (docs/190 F47) --
    // a stub here made P3 pass against a note the product never renders
    doc: 'Discovered in the selected environment — SM has no waveform ' +
         'transcription for this class, so the preview is drawn by the ' +
         'class\'s own code in that environment. Fields come from the env’s ' +
         'own dataclass schema.',
    iq: 'never', length_mode: 'explicit', channels: ['xy', 'z', 'resonator'],
    verify: 'env', env_only: true,
    qclass: 'quam_builder.architecture.superconducting.components.pulses.CosineBipolarPulse',
    qclass_how: 'env',
    params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
               default: null, unit: '', synth: true, required: true }]
  },
  DragCosinePulse: {
    label: 'Drag cosine', group: 'Control', doc: 'xy-only', iq: 'always',
    length_mode: 'explicit', channels: ['xy'],
    verify: 'env', qclass: 'quam.components.pulses.DragCosinePulse',
    qclass_how: 'env',
    params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
               default: 0.1, unit: 'V', synth: true, required: true }]
  }
};

const PAIRS_INFO = {
  'q1-q2': {                                    // stored control = LOWER f
    control: 'q1', target: 'q2',
    f_control: 4.8e9, f_target: 5.1e9, orient_ok: false,
    gates: {
      cz_unipolar: {
        slots: {
          flux_pulse_qubit: { state: 'held', 'class': 'SNZPulse',
                              path: 'qubit_pairs.q1-q2.macros.cz_unipolar.flux_pulse_qubit' },
          coupler_flux_pulse: { state: 'empty', 'class': null, path: null }
        }
      }
    },
    new_gates: ['cz_unipolar', 'cz_snz']
  },
  'q2-q1': {
    control: 'q2', target: 'q1',
    f_control: 5.1e9, f_target: 4.8e9, orient_ok: true,
    gates: {}, new_gates: ['cz_unipolar']
  }
};
const GATE_DEFS = {
  cz_unipolar: { label: 'CZ Unipolar (square pulse)', has_coupler_slot: true },
  cz_snz: { label: 'CZ SNZ', has_coupler_slot: false }
};

const HTML =
  '<div id="pulse-create-root">' +
  '<form class="pulse-create-form">' +
  '  <input type="radio" name="target_kind" value="qubit" checked>' +
  '  <input type="radio" name="target_kind" value="pair">' +
  '  <span data-target-kind="pair" hidden>' +
  '    <select name="pair" id="pulse-create-pair">' +
  '      <option>q1-q2</option><option>q2-q1</option>' +
  '    </select>' +
  '    <select name="gate" id="pulse-create-gate"></select>' +
  '    <select name="slot" id="pulse-create-slot"></select>' +
  '    <input type="text" name="new_gate_name" id="pulse-create-newgate-name" hidden>' +
  '  </span>' +
  '  <p id="pulse-create-pairline" hidden></p>' +
  '  <p id="pulse-create-slotnote" hidden></p>' +
  '  <select name="pulse_type" id="pulse-create-type">' +
  '    <option value="SquarePulse">Square</option>' +
  '    <option value="ErfSquarePulse">Erf square</option>' +
  '    <option value="CosineBipolarPulse">CosineBipolarPulse</option>' +
  '    <option value="DragCosinePulse">Drag cosine</option>' +
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
  '<script id="pulse-pairs-info-data" type="application/json">' +
  JSON.stringify(PAIRS_INFO) + '</scr' + 'ipt>' +
  '<script id="pulse-gate-defs-data" type="application/json">' +
  JSON.stringify(GATE_DEFS) + '</scr' + 'ipt>' +
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

// P3: env-only class → no automatic preview, a lab-draw button + note; back → gone
typeSel.value = 'CosineBipolarPulse';
P.createTypeChanged(typeSel);
ok(doc.getElementById('pulse-create-plot').hidden === false, 'P3: plot area kept');
ok(doc.getElementById('pulse-create-plot').classList.contains('pulse-plot-empty'),
   'P3: plot shown empty until drawn');
ok(!!doc.getElementById('pulse-create-labdraw'), 'P3: lab-draw button offered');
const note = doc.getElementById('pulse-create-envnote');
ok(!!note && /no waveform transcription/.test(note.textContent),
   'P3: no-preview note shown');
typeSel.value = 'SquarePulse';
P.createTypeChanged(typeSel);
ok(doc.getElementById('pulse-create-plot').hidden === false, 'P3: plot restored');
ok(!doc.getElementById('pulse-create-envnote'), 'P3: note removed');
ok(!doc.getElementById('pulse-create-labdraw'), 'P3: lab-draw button removed');

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

/* -- r15 CZ-first pair flow (docs/71 §3) ------------------------------ */

const pairRadio = root.querySelector('input[name="target_kind"][value="pair"]');
pairRadio.checked = true;
P.createTargetKind(pairRadio);

// P6: freq/orientation line — the bad pair warns, the good one doesn't
const line = doc.getElementById('pulse-create-pairline');
ok(line.hidden === false, 'P6: pair line shown in pair mode');
ok(/control q1 \(4\.800 GHz\)/.test(line.textContent) &&
   /target q2 \(5\.100 GHz\)/.test(line.textContent),
   'P6: frequencies inline (got "' + line.textContent + '")');
ok(/LOWER-frequency/.test(line.textContent) &&
   line.classList.contains('pulse-pair-line-warn'),
   'P6: orientation warning on the bad pair');
const pairSel = doc.getElementById('pulse-create-pair');
pairSel.value = 'q2-q1';
P.createPairSelected(pairSel);
ok(!/LOWER-frequency/.test(line.textContent) &&
   !line.classList.contains('pulse-pair-line-warn'),
   'P6: no warning on the higher-f-control pair');

// P7: gate select = existing macros + "+ new" entries; slots per gate
pairSel.value = 'q1-q2';
P.createPairSelected(pairSel);
const gateSel = doc.getElementById('pulse-create-gate');
const gateVals = Array.prototype.map.call(gateSel.options, function (o) { return o.value; });
ok(gateVals.indexOf('cz_unipolar') !== -1, 'P7: existing gate listed');
ok(gateVals.indexOf('__new__:cz_snz') !== -1, 'P7: + new variant listed');
gateSel.value = 'cz_unipolar';
P.createGateSelected(gateSel);
const slotSel = doc.getElementById('pulse-create-slot');
ok(slotSel.options[0].value === 'flux_pulse_qubit' &&
   slotSel.options[0].disabled === true &&
   /holds SNZPulse/.test(slotSel.options[0].textContent),
   'P7: held slot disabled + labelled');
ok(slotSel.options[1].disabled === false, 'P7: empty slot enabled');
ok(slotSel.value === 'coupler_flux_pulse',
   'P7: selection lands on the first EMPTY slot');

// P8: held-slot edit-instead note (deep link), cleared on empty slot
slotSel.value = 'flux_pulse_qubit';
P.createSlotSelected(slotSel);
const slotNote = doc.getElementById('pulse-create-slotnote');
ok(slotNote.hidden === false && /holds SNZPulse/.test(slotNote.textContent) &&
   slotNote.querySelector('a'), 'P8: edit-instead note with link');
slotSel.value = 'coupler_flux_pulse';
P.createSlotSelected(slotSel);
ok(slotNote.hidden === true, 'P8: note hidden on an empty slot');

// P9: "+ new gate" reveals the name input; qubit-only variant has no
// coupler slot; the name input hides again on an existing gate
gateSel.value = '__new__:cz_snz';
P.createGateSelected(gateSel);
const nameInput = doc.getElementById('pulse-create-newgate-name');
ok(nameInput.hidden === false && nameInput.required === true,
   'P9: new-gate name input revealed + required');
ok(Array.prototype.map.call(slotSel.options, function (o) { return o.value; })
     .join(',') === 'flux_pulse_qubit',
   'P9: qubit-only variant offers no coupler slot');
gateSel.value = 'cz_unipolar';
P.createGateSelected(gateSel);
ok(nameInput.hidden === true, 'P9: name input hidden for existing gates');

// P10: pair mode narrows the type list to z-capable + env-only classes
const dragOpt = typeSel.querySelector('option[value="DragCosinePulse"]');
const cbpOpt = typeSel.querySelector('option[value="CosineBipolarPulse"]');
ok(dragOpt.hidden === true && dragOpt.disabled === true,
   'P10: xy-only class hidden in pair mode');
ok(cbpOpt.hidden === false, 'P10: env-only class stays visible');
const qubitRadio = root.querySelector('input[name="target_kind"][value="qubit"]');
qubitRadio.checked = true;
P.createTargetKind(qubitRadio);
ok(dragOpt.hidden === false, 'P10: filter lifted outside pair mode');
ok(line.hidden === true, 'P10: pair line hidden outside pair mode');

// P11 (docs/190 F48): a gate name the pair already carries is refused AS THE
// USER TYPES. It was free text with a pattern and nothing else, so the only
// answer came after the press -- from a server that already knew, and from a
// select right above the box that already listed the taken names.
const pairSel2 = doc.getElementById('pulse-create-pair');
pairSel2.value = 'q1-q2';
P.createPairSelected(pairSel2);
gateSel.value = '__new__:cz_snz';
P.createGateSelected(gateSel);
const gname = doc.getElementById('pulse-create-newgate-name');

gname.value = 'cz_unipolar';                 // q1-q2 already has this macro
P.createValidateGateName();
ok(/already exists on q1-q2/.test(gname.validationMessage),
   'P11: a taken gate name is refused as you type');
ok(gname.getAttribute('aria-invalid') === 'true', 'P11: and marked invalid');

gname.value = 'cz_brand_new';
P.createValidateGateName();
ok(gname.validationMessage === '', 'P11: a free name is accepted');
ok(gname.getAttribute('aria-invalid') === 'false', 'P11: and marked valid');

// the check is PER PAIR: q2-q1 carries no macros, so the same word is free
pairSel2.value = 'q2-q1';
P.createPairSelected(pairSel2);
gateSel.value = '__new__:cz_unipolar';
P.createGateSelected(gateSel);
gname.value = 'cz_unipolar';
P.createValidateGateName();
ok(gname.validationMessage === '',
   'P11: the same name is free on a pair that does not carry it');

// an EXISTING gate is chosen, not named -- the hidden box must never refuse
pairSel2.value = 'q1-q2';
P.createPairSelected(pairSel2);
gateSel.value = 'cz_unipolar';
P.createGateSelected(gateSel);
ok(gname.hidden === true && gname.validationMessage === '',
   'P11: a hidden name box blocks nothing');

// P12: the WIRING, not just the function. Choosing the "+ new" gate has to
// re-judge a name that is already typed -- a validator nothing calls is this
// project's recurring failure (docs/141 §4af), and the first version of the
// pin above called it by hand and so proved nothing about the call site.
gname.value = 'cz_unipolar';
gateSel.value = '__new__:cz_snz';
P.createGateSelected(gateSel);                 // no hand-call below this line
ok(/already exists on q1-q2/.test(gname.validationMessage),
   'P12: choosing "+ new" re-judges a name already in the box');

// and switching PAIRS re-judges it too -- q2-q1 carries no macros
pairSel2.value = 'q2-q1';
P.createPairSelected(pairSel2);
gateSel.value = '__new__:cz_unipolar';
P.createGateSelected(gateSel);
ok(gname.validationMessage === '',
   'P12: switching pair re-judges the same word');

// P13 (docs/190 F47): two kinds of class reach the no-preview note now -- one
// the selected ENVIRONMENT has and one THIS CHIP declares (the lab's own) --
// and "where did this come from" has a different answer for each. The spec
// carries its own sentence; the env wording is the fallback for an entry that
// predates the field.
root._catalog.LabOwnPulse = {
  label: 'LabOwnPulse', group: 'From this chip',
  doc: 'Declared by this chip and defined in your own package — no preview.',
  iq: 'never', length_mode: 'inferred', channels: ['xy', 'z', 'resonator'],
  verify: 'env', env_only: true, qclass: 'quam_config.two_flux.LabOwnPulse',
  qclass_how: 'env',
  params: [{ name: 'amplitude', label: 'Amplitude', kind: 'float',
             default: null, unit: '', synth: true, required: true }]
};
var labOpt = doc.createElement('option');
labOpt.value = 'LabOwnPulse'; labOpt.textContent = 'LabOwnPulse';
typeSel.appendChild(labOpt);
typeSel.value = 'LabOwnPulse';
P.createTypeChanged(typeSel);
var note2 = doc.getElementById('pulse-create-envnote');
ok(!!note2 && /Declared by this chip/.test(note2.textContent),
   'P13: a chip class says it came from the chip');
ok(!!doc.getElementById('pulse-create-labdraw'),
   'P13: and offers its own code to draw it');

// a class with no doc of its own keeps the env sentence
typeSel.value = 'CosineBipolarPulse';
P.createTypeChanged(typeSel);
var envDoc = root._catalog.CosineBipolarPulse.doc;
root._catalog.CosineBipolarPulse.doc = '';
typeSel.value = 'SquarePulse'; P.createTypeChanged(typeSel);
typeSel.value = 'CosineBipolarPulse'; P.createTypeChanged(typeSel);
var note3 = doc.getElementById('pulse-create-envnote');
ok(!!note3 && /Discovered in the selected environment/.test(note3.textContent),
   'P13: an entry with no doc keeps the env wording');
root._catalog.CosineBipolarPulse.doc = envDoc;

// P14 (docs/2xx): the button asks the CLASS ITSELF -- qclass + the values in
// the form, to the lab route -- and draws what comes back, labelled as such.
typeSel.value = 'LabOwnPulse'; P.createTypeChanged(typeSel);
var amp = doc.querySelector('#pulse-create-fields input[name="amplitude"]');
if (amp) amp.value = '0.25';
var sent = null, drawn = null;
win.fetch = function (url, opts) {
  sent = { url: url, body: JSON.parse((opts && opts.body) || '{}') };
  return win.Promise.resolve({ json: function () { return win.Promise.resolve({
    ok: true, results: [{ ok: true, warnings: [],
      plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.25, 0] }] } }] }); } });
};
win._plotlyRender = function (id, data) { drawn = { id: id, data: data }; return null; };
var bar = root.querySelector('.pulse-plot-bar');
var lbl = doc.createElement('span'); lbl.className = 'pulse-plot-label'; bar.appendChild(lbl);
doc.getElementById('pulse-create-labdraw').click();
setTimeout(function () {
  ok(sent && sent.url === '/api/pulse/lab-waveform', 'P14: posts to the lab route');
  ok(sent && sent.body.qclass === 'quam_config.two_flux.LabOwnPulse', 'P14: names the class');
  ok(sent && sent.body.params && sent.body.params.amplitude === '0.25', 'P14: sends the typed values');
  ok(drawn && drawn.id === 'pulse-create-plot' && drawn.data.length === 1, 'P14: draws the answer');
  ok(/class's own code/.test(lbl.textContent), 'P14: labelled as the class\'s own code');
  ok(!doc.getElementById('pulse-create-plot').classList.contains('pulse-plot-empty'), 'P14: plot no longer empty');
  p15();
}, 20);

// P15 (docs/2xx verifier round): a detail whose class schema predates a lab
// edit polls /pulse/schema-status while SM re-reads the class, then
// re-renders itself -- but never over an uncommitted edit (it says so).
function p15() {
  function mkRoot(dirty) {
    var r = doc.createElement('div'); r.id = 'pulse-detail-root';
    r.setAttribute('data-pulse-path', 'qubits.q1.z.operations.cz');
    r.innerHTML = '<span data-schema-stale="code">stale</span>' +
      '<input data-param="amplitude" data-committed="0.2" value="' + (dirty ? '0.3' : '0.2') + '">';
    doc.body.appendChild(r);
    r._sections = [{ el: r, path: 'qubits.q1.z.operations.cz' }];
    return r;
  }
  var answers = [{ ok: true, stale: true, failed: false }, { ok: true, stale: false, failed: false }];
  var asked = [], reloads = [];
  win.fetch = function (url) {
    asked.push(url);
    var a = answers.length > 1 ? answers.shift() : answers[0];
    return win.Promise.resolve({ json: function () { return win.Promise.resolve(a); } });
  };
  win.htmx = { ajax: function (m, url, o) { reloads.push({ url: url, target: o && o.target }); return null; } };
  var clean = mkRoot(false);
  P._pollSchema(clean, 0, 1);
  setTimeout(function () {
    ok(asked.length >= 2 && asked[0] === '/pulse/schema-status', 'P15: polls the schema status');
    ok(reloads.length === 1 && /\/pulse\/detail\?path=qubits\.q1\.z\.operations\.cz/.test(reloads[0].url)
       && reloads[0].target === '#inspector-pane', 'P15: re-renders the view once fresh');
    clean.remove();
    var dirty = mkRoot(true);
    reloads = [];
    P._pollSchema(dirty, 0, 1);
    setTimeout(function () {
      ok(reloads.length === 0, 'P15: never re-renders over an uncommitted edit');
      ok(/reopen this pulse/.test(dirty.querySelector('[data-schema-stale]').textContent),
         'P15: says to reopen instead');
      if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
      console.log('ALL OK pulses_create_selfcheck');
      process.exit(0);
    }, 60);
  }, 60);
}
