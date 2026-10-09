/* Verifier P3 (w7/agentsqa): another window switched the chip. The open Agent
 * home kept the old chip's name in its heading (set once at mount) and the old
 * chip's plan/approval cards -- one with an enabled "Write to chip" -- until
 * something happened to remove them. A new chip key starts the feed over.
 * Real agent.js under jsdom.
 * Run: node tests/agent_chip_switch_selfcheck.cjs */
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
const ap = { id: 'ap-old', kind: 'writes', node: '17_T1', targets: ['q7'], created: now, why_held: 'ask-writes',
  writes: [{ path: 'qubits.q7.T1', old: 4.2e-5, new: 3.3e-5, now: 5.55e-5, now_known: true },
           { path: 'qubits.q7.T2ramsey', old: 1e-5, new: 2e-5, now: 1e-5, now_known: true }] };
const plan = { id: 'pl-old', status: 'draft', title: 'power rabi on q1..q30', steps: [], created: now };
const feeds = {
  A: { ok: true, chip: 'LAB_QA_30Q', chip_key: 'LAB_QA_30Q-aaaa', qubits: 30, last: 3, agent_seq: 1,
       cards: [{ n: 3, kind: 'say', role: 'agent', text: 'hello from the 30Q chip', ts: now }],
       session: {}, file: {}, now: { state: 'waiting', waiting: 1 }, live: { approvals: [ap], plans: [plan], runs: [] } },
  B: { ok: true, chip: 'lab-F', chip_key: 'lab-F-bbbb', qubits: 5, last: 0, agent_seq: 2,
       cards: [], session: {}, file: {}, now: { state: 'idle' }, live: { approvals: [], plans: [], runs: [] } },
};
let cur = 'A';
const asked = [];
global.fetch = window.fetch = function (url) {
  let b = { ok: true };
  if (/\/chat\/cards/.test(url)) { asked.push(String(url)); b = feeds[cur]; }
  else if (/\/chat\/backends/.test(url)) b = { ok: true, chip: feeds[cur].chip, default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: () => Promise.resolve(b) });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 30));
(async () => {
  await tick(80);
  const head = () => document.querySelector('#agent-home .ag-chip').textContent + ' ' + document.querySelector('#agent-home .ag-qubits').textContent;
  const apCard = () => document.querySelector('[data-card="approval:ap-old"]');
  ok(/LAB_QA_30Q 30 qubits/.test(head()), 'precondition: the heading names the 30Q chip: ' + head());
  ok(!!apCard(), 'precondition: the 30Q approval card is on screen');
  ok(/hello from the 30Q chip/.test(document.querySelector('#agent-home .ag-cards').textContent), 'precondition: the 30Q chat card too');
  ok(!!document.querySelector('[data-card="plan:pl-old"]'), 'precondition: the 30Q plan card too');
  // verifier P3 (second): the "now" column is the value SM holds now, not the
  // value the proposal was made from; a difference is shown and flagged
  const cells = [...apCard().querySelectorAll('.ag-ap-rows tbody tr')].map(tr => tr.children[1]);
  ok(/5\.55/.test(cells[0].textContent) && /proposed from/.test(cells[0].textContent) && /4\.2/.test(cells[0].textContent)
     && cells[0].classList.contains('ag-ap-moved'), 'a moved leaf: now = what SM holds, flagged with what it was proposed from: ' + cells[0].textContent);
  ok(!/proposed from/.test(cells[1].textContent) && !cells[1].classList.contains('ag-ap-moved'), 'an unmoved leaf is not flagged: ' + cells[1].textContent);

  // another window opens the 5Q chip; the server moved agent_seq, LiveWake says so
  cur = 'B';
  document.dispatchEvent(new window.CustomEvent('sm:agent-changed', { detail: { agent_seq: 2 } }));
  await tick(120);
  ok(/lab-F 5 qubits/.test(head()), 'the heading names the chip now open: ' + head());
  ok(!apCard(), 'the old chip\'s approval card (with its Write to chip) is gone');
  ok(!/hello from the 30Q chip/.test(document.querySelector('#agent-home .ag-cards').textContent), 'the old chip\'s chat is gone');
  ok(!document.querySelector('[data-card="plan:pl-old"]'), 'the old chip\'s plan card is gone');
  ok(/after=0\b/.test(asked[asked.length - 1] || ''), 'the new chip\'s feed is read from the start: ' + asked[asked.length - 1]);
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
