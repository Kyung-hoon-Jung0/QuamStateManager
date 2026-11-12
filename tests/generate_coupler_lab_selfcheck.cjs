'use strict';
// The lab layout of the coupler-only CZ in the wizard (decisions D-2, B-4):
// offered only when the env has the lab gate, disabled with the reason
// otherwise, builds cz_coupler_only with no qubit flux line, pre-fills the
// lab root and takes back only a root it filled itself.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
let JSDOM;
try { ({JSDOM} = require('jsdom')); } catch (_) { process.exit(2); }
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
let js = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/generate.js'), 'utf8');
js = js.replace('_test: {', '_test: { sync: syncLineTypeToggles, derive: deriveLines, guards: stepGuards, arch: applyChipArch, cols: pairPopCols,');
const dom = new JSDOM('<body>' + html + '</body>', {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) { return String(s || ''); }};
w.armPlainResize = function () {};
w.renderInstrumentWiring = function () {};
let lab = false;
const LAB_ROOT = 'quam_config.my_quam.Quam';
w.fetch = function (url) {
  if (url === '/generate/capabilities') return Promise.resolve({json: () => Promise.resolve({
    report: {inventory: [{id: 'pair.coupler_cz', available: true}, {id: 'pair.coupler_cz_lab', available: lab}]},
    roots: [{path: 'quam_builder.architecture.superconducting.qpu.FluxTunableQuam', importable: true, fixed_root: false},
            {path: LAB_ROOT, importable: true, fixed_root: false}],
  })});
  return new Promise(() => {});
};
w.eval(js);
const tick = () => new Promise(resolve => w.setTimeout(resolve, 0));
(async function () {
  await tick();
  const g = w.QuamGen, t = g._test, s = g.state;
  s.spec.qubits = ['q1', 'q2'];
  s.spec.qubit_pairs = [['q1', 'q2']];
  s.spec.instruments.controllers = [{con: 1, fems: [{slot: 1, fem: 'mw'}, {slot: 5, fem: 'lf'}]}];
  s.env = 'env-without-lab';
  t.sync();
  await tick();
  const opt = w.document.querySelector('#gen-chip-arch option[value="fixed_tunable_coupler_lab"]');
  assert(opt, 'the lab layout is offered');
  assert(opt.disabled, 'without the lab gate it is disabled');
  assert(opt.title.includes('coupler_cz_gate.CouplerCZGate'), 'and says what is missing: ' + opt.title);
  const radio = w.document.querySelector('#gen-arch-radios input[value="fixed_tunable_coupler_lab"]');
  assert(radio && radio.disabled, 'its radio is shown, disabled (not hidden)');
  t.arch('fixed_tunable_coupler_lab');
  assert(t.guards[4]().includes('coupler_cz_gate'), 'a stale draft cannot continue: ' + t.guards[4]());

  lab = true;
  s.env = 'env-with-lab';
  t.sync();
  await tick();
  assert(!opt.disabled, 'the lab gate enables it');
  delete s.spec.quam_class;
  t.arch('fixed_tunable_coupler_lab');
  assert.strictEqual(s.spec.pair_gate, 'cz_coupler_only');
  assert.strictEqual(s.qubitFlux, false);
  assert(!s.spec.lines.some(l => l.line === 'flux'), 'no qubit flux line');
  assert(s.spec.lines.some(l => l.line === 'coupler' && l.element === 'q1-q2'), 'a coupler line per pair');
  assert.strictEqual(t.guards[4](), null, 'the lab layout passes the step guard');
  assert.strictEqual(s.spec.quam_class, LAB_ROOT, 'the lab root is pre-filled, never the stock one');
  assert(w.document.getElementById('gen-chip-arch-note').textContent.includes('cz_coupler_only'),
         'the note says what the chip holds');

  // the pair table for the lab gate: shape + SNZ parameters, no moving qubit, no interaction offset
  const cols = t.cols('cz_coupler_only').map(c => c.field);
  assert(cols.includes('coupler_pulse_shape') && cols.includes('snz_padding') && cols.includes('coupler_amplitude')
         && cols.includes('coupler_cz_length'), cols.join());
  assert(!cols.includes('cz_interaction_duration') && !cols.includes('cz_amplitude'), 'no pre-filled cells: ' + cols.join());
  assert(!cols.includes('moving_qubit') && !cols.includes('coupler_interaction_offset'), cols.join());
  assert(w.document.getElementById('gen-chip-arch').options.length >= 5, 'both coupler options');
  // leaving the layout takes back the root it filled itself
  t.arch('flux_tunable_coupler');
  assert.strictEqual(s.spec.quam_class, undefined, 'an auto-filled root is taken back');
  // a root the user picked is never touched
  s.spec.quam_class = 'somewhere.else.Quam';
  s.quamClassAuto = null;
  t.arch('fixed_tunable_coupler_lab');
  assert.strictEqual(s.spec.quam_class, 'somewhere.else.Quam', 'a picked root is kept');
  t.arch('flux_tunable_coupler');
  assert.strictEqual(s.spec.quam_class, 'somewhere.else.Quam', 'and kept when leaving');

  // the extension layout is unchanged
  t.arch('fixed_tunable_coupler');
  assert.strictEqual(s.spec.pair_gate, 'cz_coupler');
  // the two options say which gate and macro they build
  const ext = w.document.querySelector('#gen-chip-arch option[value="fixed_tunable_coupler"]');
  assert(ext.textContent.includes('QSM extension gate (cz_coupler)'), ext.textContent);
  assert(opt.textContent.includes("your lab's gate (cz_coupler_only)"), opt.textContent);

  // D-2: a NEW chip on the coupler-only architecture gets the lab's gate when the env has both
  s.archPicked = false; s.mode = 'generate';
  t.arch('fixed_tunable_coupler');
  s.env = 'env-with-both';
  t.sync();
  await tick();
  assert.strictEqual(s.spec.pair_gate, 'cz_coupler_only', 'a new chip defaults to the lab gate');
  // a chip somebody set (a pick, a draft, a re-generate) keeps its own
  s.archPicked = true;
  t.arch('fixed_tunable_coupler');
  s.env = 'env-with-both-again';
  t.sync();
  await tick();
  assert.strictEqual(s.spec.pair_gate, 'cz_coupler', 'a set chip keeps the extension gate');
  dom.window.close();
  console.log('coupler lab selfcheck passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
