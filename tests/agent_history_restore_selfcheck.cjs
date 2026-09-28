/* QA round (agents menu): a browser Back restores the page from htmx's BODY
 * snapshot, and the snapshot carries every attribute the scripts set -- so a
 * mount guard that reads `data-ag-mounted` / `data-as-mounted` saw "already
 * mounted" on a node the script had never touched, and the restored page was
 * a picture: the Agent home no longer polled (a sent line made no card), and
 * Agent setup rendered every later answer into its DETACHED old root.
 * This harness does what htmx does -- body.innerHTML = snapshot, then
 * `htmx:historyRestore` -- against the REAL agent.js and agent-setup.js.
 * Run: node tests/agent_history_restore_selfcheck.cjs */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const BODY = '<div id="table-pane"><div id="agent-home"></div><div id="as-body"></div></div>'
  + '<div id="agent-popover" class="agent-popover agent-hidden"><div class="agent-header"></div><div class="agent-body"></div></div>';
const dom = new JSDOM('<!doctype html><html><body>' + BODY + '</body></html>', { url: 'http://localhost/agent', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;

let setupTodo = ['journal'];
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  calls.push(String(url));
  let body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = { ok: true, chip: 'C', last: 1, agent_seq: 1, qubits: 30,
    cards: [{ n: 1, ts: Date.now() / 1000, kind: 'user', text: 'hello', who: 'human:a' }], live: {}, session: {}, file: {}, now: { state: 'idle' } };
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  else if (/\/api\/agent\/setup$/.test(url)) body = { ok: true, clis: { claude: { found: true } }, claude: {}, codex: {}, context: {}, record: {},
    journal: { root: 'R', configured: setupTodo.indexOf('journal') < 0, suggested: 'R' }, todo: setupTodo.slice(), global_simulate: false };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-setup.js'), 'utf8'), { filename: 'agent-setup.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
const P = window.AgentPanel, A = window.AgentSetup;
const liveHome = () => { const h = document.getElementById('agent-home'); return !!h && P._state.mounts.some(m => h.contains(m.root)); };

(async () => {
  await tick(40);
  ok(liveHome(), 'the home mounts on first load');
  ok(A._state.root === document.getElementById('as-body'), 'setup mounts on first load');

  // --- leave the page (an htmx swap replaces the pane), then Back (restore the snapshot)
  const snapshot = document.body.innerHTML;
  document.getElementById('table-pane').innerHTML = '<p>another page</p>';
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap'));
  ok(P._state.mounts.length === 0, 'leaving drops the home mount');
  document.body.innerHTML = snapshot;
  ok(document.getElementById('agent-home').getAttribute('data-ag-mounted') === '1',
     'precondition: the restored snapshot carries the mounted attribute');
  document.dispatchEvent(new window.CustomEvent('htmx:historyRestore'));
  await tick(40);
  ok(liveHome(), 'after Back the restored home is a LIVE mount (it polls and renders)');
  const n0 = calls.filter(u => /\/chat\/cards/.test(u)).length;
  await P.poll(true);
  ok(calls.filter(u => /\/chat\/cards/.test(u)).length === n0 + 1, 'a poll after Back reaches the server');
  ok(document.querySelectorAll('#agent-home .ag-root').length === 1, 'exactly one renderer in the restored home');

  // setup: a later load() must land in the VISIBLE root, not the detached one
  const vis = document.getElementById('as-body');
  ok(A._state.root === vis, 'after Back setup renders into the visible root');
  vis.innerHTML = 'STALE';
  setupTodo = [];
  await A.load(); await tick(20);
  ok(vis.innerHTML !== 'STALE' && /Journal/.test(vis.textContent), 'a press that re-loads setup re-renders what the person sees');

  // the same Back must not double-mount: an afterSwap on top is a no-op
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap'));
  await tick(20);
  ok(P._state.mounts.length === 1, 'afterSwap after restore keeps ONE mount');

  // the float: restored OPEN by a snapshot on a page without the home -> it is a picture, so hidden
  const pop = document.getElementById('agent-popover');
  document.getElementById('table-pane').innerHTML = '<p>pulses</p>';
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap'));
  P.toggleFloat(); await tick(40);
  ok(!pop.classList.contains('agent-hidden') && P._state.mounts.some(m => m.id === 'float'), 'the float opens live on another page');
  const snap2 = document.body.innerHTML;
  document.body.innerHTML = snap2;          // Back onto a snapshot with the float OPEN
  document.dispatchEvent(new window.CustomEvent('htmx:historyRestore'));
  await tick(20);
  const pop2 = document.getElementById('agent-popover');
  ok(pop2.classList.contains('agent-hidden'), 'a float restored open from a snapshot is put away, not shown dead');
  P.toggleFloat(); await tick(40);
  const fb = pop2.querySelector('.agent-body');
  ok(!pop2.classList.contains('agent-hidden') && P._state.mounts.some(m => fb.contains(m.root)), 'reopening it mounts a LIVE float');

  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
