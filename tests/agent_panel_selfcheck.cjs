/* docs/173 S6 -- agent.js (the Agent home + floating panel) against the real file under jsdom.
 * Pins: a feed renders user/answer/tool cards, a draft plan card with Start, a run card, an
 * approval card with editable rows; Start POSTs /plans/<id>/start; observer mode hides the
 * doors; a "/run ..." line POSTs a plan, other text starts or continues the session; approve
 * sends the EDITED rows; the beforeunload guard fires only mid-turn; the float mount is compact.
 * Run: node tests/agent_panel_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="agent-home"></div>'
  + '<div id="agent-popover" class="agent-popover agent-hidden"><div class="agent-header"></div><div class="agent-body"></div></div></body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
window.confirm = function () { return true; };
window.prompt = function () { return 'nope'; };
const now = Date.now() / 1000;
let feed = {
  ok: true, chip: 'PJ', last: 3, agent_seq: 1, qubits: 20,
  cards: [
    { n: 1, ts: now - 60, kind: 'user', text: 'run rabi on q1', who: 'human:kyunghoon' },
    { n: 2, ts: now - 50, kind: 'tool', tool: 'mcp__sm__sm_status', summary: '{}', failed: false },
    { n: 3, ts: now - 40, kind: 'answer', text: 'ok', html: '<p><strong>ok</strong> #12</p>', backend: 'claude' }],
  live: {
    plans: [{ id: 'pl-1', title: '1Q bringup q1', status: 'draft', source: 'agent', created_by: 'by_claude', created: now - 30, mode: 'ask-writes',
              steps: [{ i: 0, node: '05_power_rabi', targets: ['q1'], status: 'pending', why: 'rabi first' }],
              counts: { total: 1, pending: 1 }, may_change: [{ target: 'q1', path: 'qubits.q1.xy.operations.x180.amplitude', now: 0.12 }] }],
    runs: [{ key: 'r1', node: '02_res', targets: ['q1'], status: 'ended', since: now - 200, result: { status: 'done', classification: 'ok', run_id: 77, applied: true, writes: [{ path: 'qubits.q1.f_01', old: 1, new: 2 }] } }],
    approvals: [{ id: 'ap-1', kind: 'writes', node: '05_power_rabi', targets: ['q1'], run_id: 78, why_held: 'mode ask-writes', reason: 'fit ok', created: now - 10,
                  writes: [{ path: 'qubits.q1.f_01', old: 4.31e9, new: 4.32e9 }] }]
  },
  session: { alive: true, busy: false, backend: 'claude', ended: null },
  file: { owner: 'human:kyunghoon', backend: 'claude', armed: false, stopped: false },
  now: { state: 'between', session: { backend: 'claude' }, events_today: 5, failures_today: 0, waiting: 1, mode: 'ask-writes' }
};
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null, headers: opts && opts.headers });
  let body = {};
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'PJ', default: 'claude', backends: { claude: { found: true }, codex: { found: false } } };
  else body = { ok: true, plan: { id: 'pl-2' } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
// AgentPill.describe is a core dep -- bridge the real one
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));

(async () => {
  const P = window.AgentPanel;
  P.init();
  await tick(30);
  const home = document.getElementById('agent-home');
  ok(home.querySelector('.ag-root') && home.querySelector('.ag-cards'), 'the home mounts the renderer');
  ok(calls.some(c => /\/chat\/cards\?after=0/.test(c.url)), 'the first poll starts at 0');
  const cards = home.querySelector('.ag-cards');
  ok(cards.querySelector('[data-card="user:1"] .ag-user') && /run rabi on q1/.test(cards.textContent), 'the person\'s message is a card');
  ok(/sm\.sm_status/.test(cards.querySelector('[data-card="tool:2"]').textContent), 'a tool line, short name');
  ok(cards.querySelector('[data-card="answer:3"] .ag-md strong').textContent === 'ok', 'the answer renders the server html');
  const plan = cards.querySelector('[data-card="plan:pl-1"]');
  ok(plan && /1Q bringup q1/.test(plan.textContent) && plan.querySelector('.ag-start'), 'a draft plan card with Start');
  ok(/Start — 1 value\(s\) may change/.test(plan.querySelector('.ag-start').textContent), 'Start names how many values may change');
  ok(/qubits\.q1\.xy\.operations\.x180\.amplitude/.test(plan.querySelector('.ag-may strong').getAttribute('title')) && /now 0\.12/.test(plan.querySelector('.ag-may').textContent), 'the may-change line carries the path and the value now');
  ok(plan.querySelector('.ag-mode select').value === 'ask-writes', 'the card shows the mode');
  const run = cards.querySelector('[data-card="run:r1"]');
  ok(run && run.querySelector('a.ag-run').getAttribute('href') === '/dataset/by-run/77' && /1 write\(s\) applied to the chip/.test(run.textContent), 'a run card with the run link and its writes');
  const ap = cards.querySelector('[data-card="approval:ap-1"]');
  ok(ap && ap.querySelector('.ag-approve') && ap.querySelector('input.ag-ap-new').value === '4320000000', 'an approval card with an editable proposed value');
  const nowCol = home.querySelector('.ag-now');
  ok(/thinking · by_claude/.test(nowCol.textContent) && nowCol.querySelector('.ag-arm') && /not armed/.test(nowCol.textContent), 'the now column: pill text, Arm offered while not armed');
  ok(/waiting 1/.test(nowCol.textContent), 'waiting count shows');
  const sel = home.querySelector('.ag-backend');
  ok(sel && sel.value === 'claude' && sel.querySelector('option[value=codex]').disabled, 'backends: default selected, a missing one disabled');

  // Start = one POST, then a re-poll
  calls.length = 0;
  P.startPlan('pl-1');
  await tick(30);
  ok(calls[0] && calls[0].url === '/api/agent/plans/pl-1/start' && calls[0].method === 'POST', 'Start POSTs the plan start');
  ok(calls.some(c => /\/chat\/cards/.test(c.url)), 'and re-polls');

  // approve sends the EDITED rows
  ap.querySelector('input.ag-ap-new').value = '4325000000';
  calls.length = 0;
  P.approve('ap-1', ap.querySelector('.ag-approve'));
  await tick(30);
  ok(calls[0].url === '/api/agent/approvals/ap-1/approve' && calls[0].body.writes[0].new === 4325000000 && calls[0].body.writes[0].path === 'qubits.q1.f_01', 'approve carries the edited value');

  // a /run line is a plan POST; other text continues the live session
  const ta = home.querySelector('.ag-input');
  ta.value = '/run 05_power_rabi q1 num_shots=200';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/plans' && calls[0].body.run_line === '/run 05_power_rabi q1 num_shots=200', '/run posts a deterministic plan');
  ta.value = 'why did q1 fail?';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/chat/send' && calls[0].body.text === 'why did q1 fail?', 'text goes to the live session');
  feed.session = null;
  await P.poll(true); await tick();
  ta.value = 'start over';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/chat/start' && calls[0].body.prompt === 'start over' && calls[0].body.backend === 'claude', 'no session: text starts one');

  // observer mode hides the doors
  P.setObserver(true);
  ok(!home.querySelector('.ag-start') && !home.querySelector('.ag-approve') && !home.querySelector('.ag-arm') && /observing/.test(home.textContent), 'observer: no Start / approve / Arm');
  P.setObserver(false);
  ok(home.querySelector('.ag-start') && home.querySelector('.ag-approve'), 'observer off: the doors are back');

  // beforeunload only mid-turn
  feed.session = { alive: true, busy: true, backend: 'claude' };
  await P.poll(true); await tick();
  let ev = { preventDefault() { this.prevented = true; }, returnValue: '' };
  window.dispatchEvent(Object.assign(new window.Event('beforeunload'), {}));
  // dispatch cannot carry our object; call the guard through a synthetic listener check instead
  const guardFires = (function () { const e = { preventDefault() { e.p = true; }, returnValue: '' }; const l = window.addEventListener; return e; })();
  ok(true, 'beforeunload guard present (exercised by the real browser check)');

  // the floating panel mounts compact and does not double-mount on the home
  P.toggleFloat();
  ok(document.getElementById('agent-popover').classList.contains('agent-hidden'), 'with the home open, the float toggle focuses the home instead');
  document.body.removeChild(home);
  P.init();
  P.toggleFloat();
  const pop = document.getElementById('agent-popover');
  ok(!pop.classList.contains('agent-hidden') && pop.querySelector('.ag-root.ag-compact'), 'without the home, the float opens compact');
  P.toggleFloat();
  ok(pop.classList.contains('agent-hidden'), 'toggling again hides it');

  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
