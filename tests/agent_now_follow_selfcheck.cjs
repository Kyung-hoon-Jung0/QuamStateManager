/* w7 final-QA P3a -- the Agent approval card's "now" column follows the
 * working copy at once, not at the next 30 s idle poll.
 *
 * The REAL agent.js + wc-moved.js under jsdom. The card's "now" is the value
 * SM holds now (the server reads it per poll). A Take live moves it without
 * any agent news; the tray's edit_seq moving (sm:wc-moved) must re-poll.
 * Run: node tests/agent_now_follow_selfcheck.cjs */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else console.log('ok - ' + m); }

const dom = new JSDOM('<!doctype html><html><body><div id="pending-tray" data-edit-seq="seq-A"></div>'
  + '<div id="agent-home"></div></body></html>', { url: 'http://localhost/agent', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
window.confirm = function () { return true; };
const now = Date.now() / 1000;
let T1 = 2.5e-5;
function feed() {
  return {
    ok: true, chip: 'C', last: 0, agent_seq: 7, qubits: 2, cards: [],
    live: {
      plans: [], runs: [],
      approvals: [{ id: 'ap-1', kind: 'writes', node: '17_T1', targets: ['q2'], run_id: 9, why_held: 'mode ask-writes',
                    created: now - 5, reason: 'fit ok',
                    writes: [{ path: 'qubits.q2.T1', old: 2.5e-5, new: 3.3e-5, now: T1, now_known: true }] }],
    },
    session: { alive: false, busy: false, backend: 'claude', ended: null },
    file: { owner: null, backend: 'claude', armed: false, stopped: false },
    now: { state: 'idle', session: null, events_today: 0, failures_today: 0, waiting: 1, mode: 'ask-writes' },
  };
}
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  calls.push(String(url));
  let body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = feed();
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'wc-moved.js'), 'utf8'), { filename: 'wc-moved.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));
const polls = () => calls.filter(u => /\/chat\/cards/.test(u)).length;
const nowCell = () => {
  const tr = document.querySelector('#agent-home [data-card="approval:ap-1"] .ag-ap-rows tbody tr');
  return tr ? tr.children[1].textContent : null;
};
function moveTray(seq) {
  const old = document.getElementById('pending-tray');
  const t = document.createElement('div');
  t.id = 'pending-tray'; t.setAttribute('data-edit-seq', seq);
  old.parentNode.replaceChild(t, old);
  document.dispatchEvent(new window.CustomEvent('sm:tray-swapped'));
}

(async () => {
  window.AgentPanel.init();
  await tick(60);
  const c0 = nowCell();
  ok(c0 && c0.indexOf('2.5') >= 0 && c0.indexOf('proposed from') < 0, 'the card shows now = the proposal\'s old value (' + c0 + ')');
  // an event that does not move the working copy: no extra poll
  const p0 = polls();
  document.body.dispatchEvent(new window.CustomEvent('liveDriftChanged', { bubbles: true }));
  await tick(200);
  ok(polls() === p0, 'no seq move: no extra poll');
  // Take live pulled T1 = 2.71828e-5 into the working copy
  T1 = 2.71828e-5;
  moveTray('seq-B');
  let t = Date.now(), c1 = null;
  while (Date.now() - t < 2000) { await tick(20); c1 = nowCell(); if (c1 && c1.indexOf('2.718') >= 0) break; }
  ok(polls() > p0, 'the moved seq re-polled the feed');
  ok(c1 && c1.indexOf('2.718') >= 0 && c1.indexOf('proposed from') >= 0,
     'the "now" column shows the pulled value at once, flagged against the proposal (' + c1 + ')');
  ok(Date.now() - t < 1000, 'followed within a second, not the 30 s idle poll');
  console.log(fails ? fails + ' FAIL' : 'all ok');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
