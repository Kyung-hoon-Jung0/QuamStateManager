/* QA round (agents menu): the plan card's count is the server's TOTAL, not the
 * number of rows it chose to list (<= 60). A 40-qubit /run said "60 value(s)
 * may change" when 80 would. Real agent.js under jsdom.
 * Run: node tests/agent_plan_count_selfcheck.cjs */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
const dom = new JSDOM('<!doctype html><html><body><div id="agent-home"></div></body></html>', { url: 'http://localhost/agent', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
const now = Date.now() / 1000;
const rows = (n) => Array.from({ length: n }, (_, i) => ({ target: 'q' + (i + 1), path: 'qubits.q' + (i + 1) + '.xy.operations.x180_DragCosine.amplitude', now: 0.1, label: 'pi amplitude' }));
function plan(id, list, total) {
  const p = { id, title: '/run 05_power_rabi', status: 'draft', source: 'run_cmd', created_by: 'human', created: now, mode: 'ask-writes',
    steps: [{ i: 0, node: '05_power_rabi', targets: ['q1'], status: 'pending' }], counts: { total: 1, pending: 1 }, may_change: list };
  if (total !== undefined) p.may_change_total = total;
  return p;
}
let feed = { ok: true, chip: 'C', last: 0, agent_seq: 1, cards: [], session: {}, file: {}, now: { state: 'idle' },
  live: { plans: [plan('pl-big', rows(60), 80), plan('pl-small', rows(4), 4), plan('pl-old', rows(3))] } };
global.fetch = window.fetch = function (url) {
  let body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
(async () => {
  await tick(60);
  const card = (id) => document.querySelector('[data-card="plan:' + id + '"]');
  const big = card('pl-big');
  ok(big, 'the capped plan renders');
  ok(/Start — 80 value\(s\) may change/.test(big.textContent), 'Start names the TOTAL (80), not the 60 listed: ' + (big.querySelector('.ag-start') || {}).textContent);
  ok(/values that may change \(80, first 60 shown\)/.test(big.querySelector('summary').textContent), 'the list says it is the first 60 of 80');
  ok(big.querySelectorAll('.ag-may li').length === 60, 'the list itself stays at 60 rows');
  ok(/Start — 4 value\(s\) may change/.test(card('pl-small').textContent) && /may change \(4\)</.test(card('pl-small').innerHTML), 'an uncapped plan reads as before');
  ok(/Start — 3 value\(s\) may change/.test(card('pl-old').textContent), 'a server without the total falls back to the rows');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
