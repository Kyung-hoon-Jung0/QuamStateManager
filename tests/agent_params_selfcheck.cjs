/* docs/254 -- an approval is what the person saw. D-05 / D-15 / C-19: the
 * approval cards, the plan step rows and the run cards did not show params,
 * so "Allow run" for {load_data_id: 9} looked the same as one for
 * {num_shots: 100000}. C-04: after "Allow run" nothing said whether the agent
 * heard it, and the plan step said nothing about the request it waited on.
 * Real agent.js under jsdom.  Run: node tests/agent_params_selfcheck.cjs */
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
const toasts = [];
window.showToast = function (m, level) { toasts.push({ m: String(m), level: level }); };
const now = Date.now() / 1000;
const LONG = 'x'.repeat(80);
const runAp = { id: 'ap-run1', kind: 'run', node: '03_resonator_spectroscopy_single', targets: ['qA1'], created: now,
  why_held: 'mode ask-all', params: { load_data_id: 9 }, plan_id: 'pl-1', step: 0, writes: [] };
const runAp2 = { id: 'ap-run2', kind: 'run', node: '03_resonator_spectroscopy_single', targets: ['qA1'], created: now + 1,
  why_held: 'mode ask-all', params: { num_shots: 100000, label: LONG }, writes: [] };
const wrAp = { id: 'ap-wr1', kind: 'writes', node: '05_power_rabi', targets: ['qA2'], created: now + 2, why_held: 'mode ask-writes',
  params: {}, writes: [{ path: 'qubits.qA2.T1', old: 1e-5, new: 2e-5 }] };
const plan = { id: 'pl-1', title: 'replay plan', status: 'running', mode: 'ask-all', created: now - 10, source: 'agent', created_by: 'by_claude',
  counts: { total: 3, done: 0 }, may_change: [], may_change_total: 0,
  steps: [
    { i: 0, node: '03_resonator_spectroscopy_single', targets: ['qA1'], params: { load_data_id: 9 }, status: 'pending',
      request: { id: 'ap-run1', status: 'pending', decided_by: null, params: { load_data_id: 9 } } },
    { i: 1, node: '05_power_rabi', targets: ['qA2'], params: {}, status: 'pending',
      request: { id: 'ap-x', status: 'approved', decided_by: 'human:user-c', params: {} } },
    { i: 2, node: '06_ramsey', targets: ['qA3'], params: { num_shots: 100000 }, status: 'pending' }] };
const run = { key: 'k1', status: 'running', node: '06_ramsey', targets: ['qA3'], params: { num_shots: 250 }, since: now - 5, result: {} };
const feed = { ok: true, chip: 'C', chip_key: 'C-1', last: 0, agent_seq: 1, cards: [], session: {}, file: {}, now: { state: 'waiting', waiting: 3 },
  live: { approvals: [runAp, runAp2, wrAp], plans: [plan], runs: [run] } };
const calls = [];
let approveAnswer = { ok: true, agent_told: true };
global.fetch = window.fetch = function (url, opts) {
  calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
  let b = { ok: true };
  if (/\/approve$/.test(url)) b = approveAnswer;
  else if (/\/chat\/cards/.test(url)) b = feed;
  else if (/\/chat\/backends/.test(url)) b = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: () => Promise.resolve(b) });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ') : '');
(async () => {
  const P = window.AgentPanel;
  await tick(60);
  const card = (k) => document.querySelector('[data-card="' + k + '"]');
  ok(card('approval:ap-run1') && card('approval:ap-wr1') && card('plan:pl-1') && card('run:k1'), 'every card renders');

  // the run request says WHICH run: node, targets AND params, and the plan step it belongs to
  const r1 = txt(card('approval:ap-run1'));
  ok(/load_data_id=9/.test(r1), 'the run request shows its params: ' + r1.slice(0, 160));
  ok(/plan replay plan · step 0/.test(r1), 'and the plan step it belongs to');
  ok(/Allow covers exactly this run/.test(r1), 'and says Allow covers exactly this run');
  const r2 = txt(card('approval:ap-run2'));
  ok(/num_shots=100000/.test(r2), 'a value is shown as sent, never reformatted (100000, not 100 k)');
  const longEl = Array.from(card('approval:ap-run2').querySelectorAll('.ag-params code')).find(c => /^label=/.test(c.textContent));
  ok(longEl && longEl.textContent.length <= 'label='.length + 40 && /…$/.test(longEl.textContent), 'a long value is cut on the card');
  ok(/label=x{80}/.test(card('approval:ap-run2').querySelector('.ag-params').getAttribute('title')), 'and whole in the title');
  // a writes approval: the params of the run that proposed them, or that there were none
  ok(/node defaults/.test(txt(card('approval:ap-wr1'))), 'no overrides reads "node defaults"');

  // every plan step row says its params, and the request it waits on
  const rows = Array.from(card('plan:pl-1').querySelectorAll('.ag-step')).map(txt);
  ok(rows.length === 3, 'three step rows');
  ok(/load_data_id=9/.test(rows[0]) && /run request waiting for Allow/.test(rows[0]), 'step 0: params + "run request waiting for Allow": ' + rows[0]);
  ok(/node defaults/.test(rows[1]) && /allowed by human:user-c — the agent runs it next/.test(rows[1]), 'step 1: allowed, waiting for the agent: ' + rows[1]);
  ok(/num_shots=100000/.test(rows[2]) && !/request|allowed/.test(rows[2]), 'step 2: params, no request');

  // the run card says what it is running with
  ok(/num_shots=250/.test(txt(card('run:k1'))), 'the run card shows its params');

  // Allow run: the toast says whether the agent was told
  P.approve('ap-run1', card('approval:ap-run1').querySelector('.ag-approve'));
  await tick(40);
  ok(toasts.length && /the agent was told/.test(toasts[toasts.length - 1].m), 'told: ' + (toasts[toasts.length - 1] || {}).m);
  approveAnswer = { ok: true, agent_told: false };
  P.approve('ap-run2', card('approval:ap-run2').querySelector('.ag-approve'));
  await tick(40);
  const last = toasts[toasts.length - 1] || {};
  ok(/no agent conversation is open/.test(last.m) && /ap-run2/.test(last.m) && last.level === 'warning',
     'not told: says so, names the approval: ' + last.m);
  // docs/254 x docs/253: the server names who runs it (the plan's terminal driver) -- the toast says that
  approveAnswer = { ok: true, agent_told: false, told_note: 'the plan is driven by by_claude in a terminal, which runs it with approval ap-run1' };
  P.approve('ap-run1', card('approval:ap-run1').querySelector('.ag-approve'));
  await tick(40);
  const named = toasts[toasts.length - 1] || {};
  ok(/driven by by_claude in a terminal/.test(named.m) && !/no agent conversation/.test(named.m) && named.level === 'warning',
     'not told, driver known: the toast names the driver: ' + named.m);
  ok(calls.some(c => /ap-run1\/approve$/.test(c.url) && c.body && !c.body.writes), 'a run request posts no writes');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
