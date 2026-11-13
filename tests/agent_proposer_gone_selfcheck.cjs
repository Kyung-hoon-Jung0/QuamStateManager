/* A plan whose terminal proposer exited is never handed to another agent on its own: Start
 * answered 409 proposer_gone puts one sentence and two buttons on the card; "Let the in-app
 * agent drive" re-POSTs Start with driver "app", "Wait for the terminal agent" clears the note
 * and posts nothing. A running plan's refusal line goes away when the server clears it, and a
 * plan with no refusal renders no line at all.
 * Run: node tests/agent_proposer_gone_selfcheck.cjs */
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
const now = Date.now() / 1000;
const step = { i: 0, node: '05_power_rabi', targets: ['q1'], status: 'pending' };
const draft = { id: 'pl-1', title: 'terminal plan', status: 'draft', source: 'agent', created_by: 'by_claude', created: now - 30,
                mode: 'ask-writes', steps: [step], counts: { total: 1, pending: 1 }, may_change: [] };
const running = { id: 'pl-2', title: 'running plan', status: 'running', source: 'agent', created_by: 'by_claude', created: now - 20,
                  mode: 'ask-writes', steps: [Object.assign({}, step, { i: 0 })], counts: { total: 1, pending: 1 }, may_change: [],
                  refusal: { code: 'awaiting_approval', how: 'writes wait', step: 0, at: now - 5 } };
const quiet = { id: 'pl-3', title: 'quiet plan', status: 'running', source: 'agent', created_by: 'by_claude', created: now - 10,
                mode: 'ask-writes', steps: [Object.assign({}, step)], counts: { total: 1, pending: 1 }, may_change: [] };
let feed = {
  ok: true, chip: 'chip-a', last: 0, agent_seq: 1, qubits: 2, cards: [],
  live: { plans: [draft, running, quiet], runs: [], approvals: [] },
  session: { alive: false, busy: false, backend: 'claude', ended: null },
  file: { owner: null, backend: 'claude', armed: false, stopped: false },
  now: { state: 'idle', session: { backend: 'claude' }, events_today: 0, failures_today: 0, waiting: 0, mode: 'ask-writes' }
};
const calls = [];
let startAnswer = { status: 409, body: { ok: false, refused: 'proposer_gone', error: 'by_claude in a terminal, which proposed this plan, has exited.' } };
global.fetch = window.fetch = function (url, opts) {
  calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
  let status = 200, body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'chip-a', default: 'claude', backends: { claude: { found: true, version: '1' } } };
  else if (/\/plans\/pl-1\/start$/.test(url)) { status = startAnswer.status; body = startAnswer.body; }
  return Promise.resolve({ status: status, json: function () { return Promise.resolve(body); } });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));
const card = (id) => document.querySelector('[data-card="plan:' + id + '"]');

(async () => {
  const P = window.AgentPanel;
  P.init();
  await tick(40);
  ok(card('pl-1') && !card('pl-1').querySelector('.ag-err'), 'a draft plan with no refusal and no start error renders no note');
  ok(card('pl-3') && !card('pl-3').querySelector('.ag-err'), 'a running plan with no refusal renders no refusal line');
  ok(/run refused \(step 0\)/.test(card('pl-2').textContent), 'a running plan with a refusal shows it');

  calls.length = 0;
  P.startPlan('pl-1');
  await tick(40);
  const starts = calls.filter(c => /\/plans\/pl-1\/start$/.test(c.url));
  ok(starts.length === 1 && !starts[0].body.driver, 'the first Start names no driver');
  const note = card('pl-1').querySelector('.ag-proposer-gone');
  ok(note && /terminal agent that proposed this plan has exited/.test(note.textContent), 'the card says the proposer exited');
  ok(note && note.querySelector('.ag-drive-app') && /Let the in-app agent drive/.test(note.querySelector('.ag-drive-app').textContent),
     'a button lets the in-app agent drive');
  ok(note && note.querySelector('.ag-wait-term') && /Wait for the terminal agent/.test(note.querySelector('.ag-wait-term').textContent),
     'a button waits for the terminal agent');

  calls.length = 0;
  P.waitTerminal('pl-1');
  await tick(20);
  ok(!calls.some(c => c.method === 'POST'), 'waiting posts nothing');
  ok(!card('pl-1').querySelector('.ag-proposer-gone'), 'waiting clears the note');

  P.startPlan('pl-1');
  await tick(40);
  startAnswer = { status: 200, body: { ok: true, driver: { kind: 'app', backend: 'claude' } } };
  calls.length = 0;
  P.driveInApp('pl-1');
  await tick(40);
  const again = calls.filter(c => /\/plans\/pl-1\/start$/.test(c.url));
  ok(again.length === 1 && again[0].body.driver === 'app', 'letting the in-app agent drive re-POSTs Start with driver "app"');
  ok(!card('pl-1').querySelector('.ag-proposer-gone'), 'the note goes once the Start went through');

  // the server cleared the refusal (approval settled, or a step started): the line goes
  feed = JSON.parse(JSON.stringify(feed));
  feed.agent_seq = 2;
  feed.live.plans[1].refusal = null;
  P.poll(true);
  await tick(40);
  ok(!/run refused/.test(card('pl-2').textContent), 'a cleared refusal leaves the card');

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('agent_proposer_gone_selfcheck: all checks passed (' + passes + ' assertions)');
  process.exit(0);
})();
