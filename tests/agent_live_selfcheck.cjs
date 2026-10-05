/* docs/289: the live box -- what the agent's CLI is doing while a person waits.
 * Real agent.js under jsdom; /api/agent/chat/live is stubbed with the shapes
 * the server sends (core/agent_live.py snapshot + id/kind/alive).
 * Run: node tests/agent_live_selfcheck.cjs */
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
const T = Date.now() / 1000;
let feed = { ok: true, chip: 'C', chip_key: 'C-1', last: 0, agent_seq: 1, cards: [], session: null, file: {}, now: { state: 'idle' }, live: {} };
let live = { ok: true, now: T, items: [] };
const calls = [];
global.fetch = window.fetch = function (url) {
  calls.push(url);
  let body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/live/.test(url)) body = Object.assign({}, live, { now: Date.now() / 1000 });
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const P = window.AgentPanel;
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
const step = (tool, ok_, dt, detail) => ({ id: 't-' + tool + dt, tool, detail: detail || '', t0: T - 6 + dt, t1: ok_ === null ? null : T - 6 + dt + 0.4, ok: ok_ });
function item(over) {
  return Object.assign({ id: 'ask-1', kind: 'ask', backend: 'claude', alive: true, phase: 'tool', tool: 'state_get',
    detail: 'path=qubits.q1.T1', started: T - 7, turn_started: T - 7, phase_since: T - 1, ended: null, partial: true, text: '',
    steps: [step('ToolSearch', true, 0, 'loads state_get'), step('state_search', true, 0.5, 'query=T1'), step('sm_status', true, 1),
            step('runs', true, 1.5, 'n=3'), step('run', true, 2, 'run_id=4'), step('run', true, 2.5, 'run_id=3'),
            step('check_fit', true, 3, 'run_id=4'), step('state_get', null, 3.5, 'path=qubits.q1.T1')],
    lines: [[T - 7, 'you', 'what is T1?'], [T - 6.5, 'init', 'claude started'], [T - 2, 'call', 'state_get path=qubits.q1.T1']],
    n_lines: 3, seq: 5 }, over || {});
}
(async () => {
  await tick(80);
  const box = () => document.querySelector('#agent-home .ag-live');
  ok(!!box(), 'the mount has a live box');
  const composer = document.querySelector('#agent-home .ag-composer');
  ok(box() && box().nextElementSibling === composer && box().previousElementSibling.classList.contains('ag-cards'),
     'it sits between the feed and the composer (in view while the feed scrolls)');
  ok(box().hidden, 'nothing in flight: the box is hidden');

  live.items = [item()];
  P.liveKick();
  await tick(60);
  const el = () => box().querySelector('[data-live="ask-1"]');
  ok(!box().hidden && el(), 'a question in flight shows');
  const head = el().querySelector('.ag-live-head').textContent;
  ok(/claude/.test(head) && /Running state_get…/.test(head) && /question/.test(head), 'the head says who and what: ' + head);
  ok(/0:0[6-8]/.test(el().querySelector('.ag-live-clock').textContent), 'a clock since Send: ' + el().querySelector('.ag-live-clock').textContent);
  const lis = el().querySelectorAll('.ag-live-steps li');
  ok(lis.length === 7 && /\+ 2 earlier tool calls/.test(lis[0].textContent), 'the last 6 calls, the rest as one line (' + lis.length + ')');
  const last = lis[lis.length - 1];
  ok(last.classList.contains('ag-live-run') && /state_get/.test(last.textContent) && /path=qubits\.q1\.T1/.test(last.textContent),
     'the running call is marked running, with its input');
  ok(lis[1].classList.contains('ag-live-ok') && /0\.4 s/.test(lis[1].textContent), 'a finished call says how long it took');
  const term = el().querySelector('details.ag-live-term');
  ok(term && !term.open && /Terminal/.test(term.querySelector('summary').textContent) && /3 lines/.test(term.textContent),
     'the terminal is one click away, closed');
  ok(/state_get path=qubits\.q1\.T1/.test(term.querySelector('pre').textContent) && term.querySelector('.ag-term-call'),
     'it holds the log lines with their kind');

  // the person opens the terminal; new output arrives; it stays open
  term.open = true;
  term.dispatchEvent(new window.Event('toggle'));
  live.items = [item({ seq: 6, n_lines: 4, lines: item().lines.concat([[T, 'ok', 'state_get · 0.4 s → 4.2e-05']]) })];
  P._live.inflight = false;
  P.liveKick();
  await tick(1100);
  const term2 = el().querySelector('details.ag-live-term');
  ok(term2 && term2.open && /4 lines/.test(term2.textContent) && /4\.2e-05/.test(term2.textContent),
     'new output re-renders it and an open terminal stays open');

  // words for the other phases
  live.items = [item({ phase: 'waiting', tool: null, detail: '', seq: 7 })];
  await tick(1100);
  ok(/Working…/.test(el().querySelector('.ag-live-what').textContent), 'between events it says Working, never "waiting" (read as stuck)');
  live.items = [item({ phase: 'writing', tool: null, text: 'T1 of q1 is 42 us', seq: 8 })];
  await tick(1100);
  ok(/Writing the answer…/.test(el().textContent) && /T1 of q1 is 42 us/.test(el().querySelector('.ag-live-text').textContent),
     'while it writes, the answer so far shows');

  // it finishes: the feed is fetched right away (the answer card), and the box goes
  const before = calls.filter(u => /\/chat\/cards/.test(u)).length;
  live.items = [item({ phase: 'done', tool: null, alive: false, ended: Date.now() / 1000, seq: 9 })];
  await tick(1100);
  ok(el() && el().classList.contains('ag-live-done') && /Answered/.test(el().textContent), 'the finished state is shown');
  ok(calls.filter(u => /\/chat\/cards/.test(u)).length > before, 'a finished item fetches the feed at once (its answer card)');
  live.items = [];
  await tick(2300);
  ok(box().hidden && !box().querySelector('[data-live]'), 'nothing left in flight: the box goes');
  ok(P._live.timer === null, 'and the once-a-second poll stops');

  // a turn the page did not start (another window) is picked up from the feed's busy flag
  feed = Object.assign({}, feed, { session: { busy: true, alive: true, backend: 'claude' } });
  live.items = [item({ id: 'session', kind: 'task', seq: 1 })];
  await P.poll(true);
  await tick(60);
  ok(P._live.timer !== null && box().querySelector('[data-live="session"]') && /task/.test(box().textContent),
     'a busy session starts the live poll by itself');
  feed = Object.assign({}, feed, { session: null });
  live.items = [];
  await tick(2300);
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
