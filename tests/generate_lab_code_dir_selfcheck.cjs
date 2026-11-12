'use strict';
// The per-env lab code folder in the Generate wizard: picking an env loads its
// stored folder, "Check and save" posts it and says where quam_config imports
// from, a refusal names the reason, the probe runs again after a change, the
// review step shows the folder, and the lab-gate reason points at the field.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
let JSDOM;
try { ({JSDOM} = require('jsdom')); } catch (_) { process.exit(2); }
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
let js = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/generate.js'), 'utf8');
js = js.replace('_test: {', '_test: { sync: syncLineTypeToggles, select: applySelection, review: enterReviewStep,');
const dom = new JSDOM('<body>' + html + '</body>', {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) { return String(s || ''); }};
w.armPlainResize = function () {};
w.renderInstrumentWiring = function () {};
const calls = [];
let stored = {'env-a': 'C:/lab/checkout'};
let postAnswer = null;
const reply = body => Promise.resolve({json: () => Promise.resolve(body)});
w.fetch = function (url, opts) {
  calls.push({url, body: opts && opts.body ? JSON.parse(opts.body) : null});
  if (url.startsWith('/generate/lab-code-dir?python=')) {
    const py = decodeURIComponent(url.split('=')[1]);
    return reply({python: py, folder: stored[py] || null});
  }
  if (url === '/generate/lab-code-dir') return reply(postAnswer);
  if (url === '/generate/capabilities') return reply({report: {inventory: []}, roots: []});
  return new Promise(() => {});
};
w.eval(js);
const tick = () => new Promise(resolve => w.setTimeout(resolve, 0));
(async function () {
  await tick();
  const g = w.QuamGen, t = g._test, s = g.state;
  const input = w.document.getElementById('gen-lab-code-dir');
  const status = w.document.getElementById('gen-lab-code-dir-status');
  assert(input && status, 'the field is in step 1');

  // picking an env loads ITS folder; another env starts blank
  t.select('env-a');
  await tick(); await tick();
  assert.strictEqual(input.value, 'C:/lab/checkout', 'the stored folder of env-a is shown');
  assert.strictEqual(s.labCodeDir, 'C:/lab/checkout');
  t.select('env-b');
  await tick(); await tick();
  assert.strictEqual(input.value, '', 'env-b has no folder');

  // a good folder: saved, the resolved quam_config file is shown, the probe runs again
  input.value = 'C:/other';
  postAnswer = {ok: true, folder: 'C:/other', quam_config_file: 'C:/other/quam_config/__init__.py'};
  t.sync();                       // env-b already probed: only a folder change asks again
  await tick(); await tick();
  t.sync();
  const nCaps = calls.filter(c => c.url === '/generate/capabilities').length;
  await g.saveLabCodeDir();
  await tick(); await tick();
  const post = calls.filter(c => c.url === '/generate/lab-code-dir').pop();
  assert.deepStrictEqual(post.body, {python: 'env-b', folder: 'C:/other'}, 'posts this env and folder');
  assert(status.textContent.includes('C:/other/quam_config/__init__.py'), status.textContent);
  assert.strictEqual(s.labCodeDir, 'C:/other');
  assert(calls.filter(c => c.url === '/generate/capabilities').length > nCaps, 'the probe runs again');

  // a refusal: the reason is shown and the stored value is not changed
  input.value = 'C:/nothing';
  postAnswer = {ok: false, error: 'No such folder: C:/nothing'};
  await g.saveLabCodeDir();
  await tick();
  assert(status.textContent.includes('No such folder: C:/nothing'), status.textContent);
  assert.strictEqual(s.labCodeDir, 'C:/other', 'a refused folder is not taken');

  // the review step names the folder
  s.spec.qubits = ['q1'];
  t.review();
  const review = w.document.getElementById('gen-review').textContent;
  assert(review.includes('Lab code folder') && review.includes('C:/other'), 'review shows it');

  // the lab gate's disabled reason points at the field
  s.spec.qubit_pairs = [['q1', 'q2']];
  s.spec.qubits = ['q1', 'q2'];
  s.spec.instruments.controllers = [{con: 1, fems: [{slot: 1, fem: 'mw'}, {slot: 5, fem: 'lf'}]}];
  t.sync();
  await tick(); await tick();
  const opt = w.document.querySelector('#gen-chip-arch option[value="fixed_tunable_coupler_lab"]');
  assert(opt && opt.title.includes('Lab code folder in step 1'), 'reason points at the field: ' + (opt && opt.title));
  dom.window.close();
  console.log('lab code dir selfcheck passed');
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
