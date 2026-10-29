'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
let JSDOM;
try { ({JSDOM} = require('jsdom')); } catch (_) { process.exit(2); }
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
let js = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/generate.js'), 'utf8');
// Expose the shipped functions; assertions execute their actual bodies.
js = js.replace('_test: {', '_test: { sync: syncLineTypeToggles, derive: deriveLines, guards: stepGuards, arch: applyChipArch,');
const dom = new JSDOM('<body>' + html + '</body>', {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) { return String(s || ''); }};
w.armPlainResize = function () {};
w.renderInstrumentWiring = function () {};
let available = false;
w.fetch = function (url) {
  if (url === '/generate/capabilities') return Promise.resolve({json: () => Promise.resolve({
    report: {inventory: [{id: 'pair.coupler_cz', available}]}
  })});
  return new Promise(() => {});
};
w.eval(js);
(async function () {
  // Let the DOM ready hook finish before setting the fixture.
  await new Promise(resolve => w.setTimeout(resolve, 0));
  const g = w.QuamGen, t = g._test, s = g.state;
  s.spec.qubits = ['q1', 'q2'];
  s.spec.qubit_pairs = [['q1', 'q2']];
  s.spec.instruments.controllers = [{con: 1, fems: [{slot: 1, fem: 'mw'}, {slot: 5, fem: 'lf'}]}];
  s.env = 'without-extension';
  t.sync();
  await new Promise(resolve => w.setTimeout(resolve, 0));
  let option = w.document.querySelector('#gen-chip-arch option[value="fixed_tunable_coupler"]');
  assert(option, 'architecture offered');
  assert(option.disabled, 'missing extension disables A4');
  assert(option.title.includes('needs the qsm_quam_ext package in this environment'), 'blocker text');
  t.arch('fixed_tunable_coupler');
  assert(t.guards[4]().includes('needs the qsm_quam_ext'), 'stale draft cannot continue');
  available = true;
  s.env = 'with-extension';
  t.sync();
  await new Promise(resolve => w.setTimeout(resolve, 0));
  assert(!option.disabled, 'extension enables A4');
  t.arch('fixed_tunable_coupler');
  assert.strictEqual(s.qubitFlux, false);
  assert.strictEqual(s.couplerFlux, true);
  assert.strictEqual(s.spec.pair_gate, 'cz_coupler');
  assert(!s.spec.lines.some(l => l.line === 'flux'), 'no qubit flux');
  assert(s.spec.lines.some(l => l.line === 'coupler' && l.element === 'q1-q2'), 'coupler line emitted');
  assert.strictEqual(t.guards[4](), null, 'A4 passes guard');
  s.qubitFlux = false;
  s.pairGate = 'cz_tunable';
  assert(t.guards[4]().includes('needs a qubit flux line'), 'official CZ stale draft still refused');
  dom.window.close();
  console.log('coupler selfcheck passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
