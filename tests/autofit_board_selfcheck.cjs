/* Auto Calibrate plan bar + board, driven against the REAL autofit.js.
 *  1. a Backend change keeps the Targets selection
 *  2. an empty selection on the real backend does not start: it asks, and the
 *     confirm button sends all_targets
 *  3. the board says what the review-mode restore did (restored / kept /
 *     not restored)
 * Run: node tests/autofit_board_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const ROOT = path.join(__dirname, '..', 'quam_state_manager', 'web');
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const ready = { qubits: ['q1', 'q2', 'q3'], qubit_pairs: ['q2-q1'] };
const dom = new JSDOM('<!doctype html><html><body>'
  + "<div id='autofit-readiness' data-readiness='" + JSON.stringify(ready) + "'></div>"
  + '<select id="autofit-preset"><option value="1q">1q</option></select>'
  + '<select id="autofit-autonomy"><option value="review">review</option></select>'
  + '<select id="autofit-backend"><option value="sim">sim</option><option value="real">real</option></select>'
  + '<select id="autofit-targets" multiple></select>'
  + '<div id="autofit-resolution" hidden></div><p id="autofit-planbar-msg"></p>'
  + '<button id="autofit-run"></button><button id="autofit-abort"></button>'
  + '<div id="autofit-board"></div><div id="autofit-review"></div>'
  + '</body></html>', { url: 'http://localhost/autofit', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.navigator = window.navigator; global.location = window.location;
const calls = [];
let status = { active: false, state: null };
global.fetch = function (url, opts) {
  calls.push({ url: url, opts: opts || {} });
  let body = {}, st = 200;
  if (url === '/autofit/status') body = status;
  else if (url === '/autofit/resolve') body = { ok: true, resolution: {}, plan: { steps: [] } };
  else if (url === '/autofit/start') body = { ok: true, backend: 'real' };
  return Promise.resolve({ status: st, json: () => Promise.resolve(body) });
};
window.fetch = global.fetch;
window.setInterval = () => 0; global.setInterval = window.setInterval;
vm.runInThisContext(fs.readFileSync(path.join(ROOT, 'static', 'autofit.js'), 'utf8'), { filename: 'autofit.js' });
const $ = (id) => document.getElementById(id);
const tick = () => new Promise(r => setTimeout(r, 10));
const picked = () => Array.from($('autofit-targets').selectedOptions).map(o => o.value).join(',');

(async () => {
  window.autofitInit();
  await tick();
  $('autofit-targets').options[2].selected = true;      // q3
  $('autofit-backend').value = 'real';
  $('autofit-backend').onchange();
  await tick();
  ok(picked() === 'q3', 'a Backend change keeps the picked target, got ' + picked());

  // empty selection on hardware: asks, sends nothing
  $('autofit-targets').options[2].selected = false;
  calls.length = 0;
  window.autofitRun();
  await tick();
  ok(!calls.some(c => c.url === '/autofit/start'), 'empty selection on the real chip does not start');
  ok(/all 3 qubits/.test($('autofit-planbar-msg').textContent), 'it names the count: ' + $('autofit-planbar-msg').textContent);
  window.autofitRunAll();
  await tick();
  const start = calls.find(c => c.url === '/autofit/start');
  ok(start && JSON.parse(start.opts.body).all_targets === true, 'the confirm button sends all_targets');
  // a picked target needs no confirmation
  $('autofit-targets').options[0].selected = true;
  calls.length = 0;
  window.autofitRun();
  await tick();
  const s2 = calls.find(c => c.url === '/autofit/start');
  ok(s2 && JSON.parse(s2.opts.body).targets.join() === 'q1' && !('all_targets' in JSON.parse(s2.opts.body)),
     'a picked target starts directly');

  // board restore line
  const base = { plan: { steps: [{ id: 's1' }] }, targets: ['q1'], board: {}, current: null, review_queue: [], autonomy: 'review' };
  async function board(st) {
    status = { active: false, state: Object.assign({}, base, st) };
    window.autofitInit();
    await tick();
    return $('autofit-board').textContent;
  }
  let t = await board({ status: 'aborted', restore: { status: 'restored', restored: 42, kept: [], unrestorable: [] } });
  ok(/restored to its pre-plan values \(42 values put back\)/.test(t), 'restored: ' + t);
  t = await board({ status: 'done', restore: { status: 'partial', restored: 3, kept: ['qubits.q1.f_01'], unrestorable: [] } });
  ok(/partly restored/.test(t) && /qubits\.q1\.f_01/.test(t), 'partial names the kept path: ' + t);
  t = await board({ status: 'failed' });
  ok(/not restored/i.test(t), 'no restore record says so: ' + t);
  t = await board({ status: 'failed', restore: { status: 'pending', restored: 0, kept: [], unrestorable: [] } });
  ok(/did not finish/.test(t) && /State logs/.test(t), 'a restore that died says so: ' + t);
  t = await board({ status: 'done', autonomy: 'full' });
  ok(!/restored/i.test(t), 'full autonomy has no restore line');

  console.log(fails ? ('FAILED ' + fails) : ('all checks passed (' + passes + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
