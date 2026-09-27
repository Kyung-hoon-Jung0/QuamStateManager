/* QA round (agents menu), on a 30-qubit chip: "Write to chip" took 5-15 s at
 * the door, and meanwhile the card still offered Reject -- a Reject then
 * answered "no pending approval". And Cancel on Reject's note prompt REJECTED
 * the approval anyway (prompt() -> null -> "" -> POST). Real agent.js under jsdom.
 * Run: node tests/agent_approval_busy_selfcheck.cjs */
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
const ap = (id) => ({ id, kind: 'writes', node: '05_power_rabi', targets: ['q1'], created: now, why_held: 'ask-writes',
  writes: [{ path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude', old: 0.1, new: 0.11 }, { path: 'qubits.q1.T1', old: 1e-5, new: 2e-5 }] });
let feed = { ok: true, chip: 'C', last: 0, agent_seq: 1, cards: [], session: {}, file: {}, now: { state: 'waiting', waiting: 2 },
  live: { approvals: [ap('ap-1'), ap('ap-2')] } };
const calls = [];
let release = null;                       // the approve door answers only when the test says so
global.fetch = window.fetch = function (url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url: String(url), body });
  if (/\/approve$/.test(url)) return new Promise(res => { release = () => res({ status: 409, json: () => Promise.resolve({ ok: false, error: 'stale_live: take live first' }) }); });
  let b = { ok: true };
  if (/\/chat\/cards/.test(url)) b = feed;
  else if (/\/chat\/backends/.test(url)) b = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: () => Promise.resolve(b) });
};
let promptAnswer = null;
window.prompt = () => promptAnswer;
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
(async () => {
  const P = window.AgentPanel;
  await tick(60);
  const card = (id) => document.querySelector('[data-card="approval:' + id + '"]');
  ok(card('ap-1') && card('ap-2'), 'both approval cards render');

  // Cancel on the note prompt is not a rejection
  promptAnswer = null;
  P.reject('ap-2'); await tick();
  ok(!calls.some(c => /ap-2\/reject/.test(c.url)), 'Cancel on the prompt posts nothing');
  promptAnswer = '';
  P.reject('ap-2'); await tick();
  ok(calls.some(c => /ap-2\/reject/.test(c.url) && c.body.note === ''), 'OK with an empty note still rejects');

  // a person edits a value, presses Write to chip; the door is slow
  const c1 = card('ap-1');
  c1.querySelector('.ag-ap-new[data-i="0"]').value = '0.123';
  P.approve('ap-1', c1.querySelector('.ag-approve'));
  await tick();
  const apBtn = card('ap-1').querySelector('.ag-approve');
  ok(apBtn.disabled && /writing to the chip/.test(apBtn.textContent), 'in flight: the button says it is writing: ' + apBtn.textContent);
  ok(card('ap-1').querySelector('.ag-reject').hidden, 'in flight: Reject is not offered');
  const n = calls.filter(c => /ap-1\/approve/.test(c.url)).length;
  P.approve('ap-1', apBtn); promptAnswer = 'x'; P.reject('ap-1'); await tick();
  ok(calls.filter(c => /ap-1\/approve/.test(c.url)).length === n && !calls.some(c => /ap-1\/reject/.test(c.url)), 'a second press while in flight posts nothing');
  // a poll repaints the feed while the door is still busy
  feed = Object.assign({}, feed, { agent_seq: 2 });
  await P.poll(true); await tick();
  ok(card('ap-1').querySelector('.ag-approve').disabled && card('ap-1').querySelector('.ag-reject').hidden, 'a poll mid-flight keeps the card busy');
  ok(card('ap-1').querySelector('.ag-ap-new[data-i="0"]').value === '0.123', 'and keeps the value the person typed');
  // a poll that CHANGES the card (fresh markup) mid-flight: still busy
  const changed = ap('ap-1'); changed.reason = 'the agent added a reason';
  feed = Object.assign({}, feed, { agent_seq: 3, live: { approvals: [changed, ap('ap-2')] } });
  await P.poll(true); await tick();
  ok(/the agent added a reason/.test(card('ap-1').textContent), 'precondition: the card was re-rendered');
  ok(card('ap-1').querySelector('.ag-approve').disabled && card('ap-1').querySelector('.ag-reject').hidden, 'a re-render mid-flight is still busy');
  card('ap-1').querySelector('.ag-ap-new[data-i="0"]').value = '0.123';   // (a re-render rebuilds rows -- pre-existing)
  // a change that arrives while the person is IN the card waits; it lands on focusout as fresh markup: still busy
  const held = card('ap-1').querySelector('.ag-ap-new[data-i="1"]');
  held.focus();
  const changed2 = ap('ap-1'); changed2.reason = 'a second reason while typing';
  feed = Object.assign({}, feed, { agent_seq: 4, live: { approvals: [changed2, ap('ap-2')] } });
  await P.poll(true); await tick();
  ok(!/a second reason while typing/.test(card('ap-1').textContent), 'precondition: the change waits while the person is in the card');
  held.blur(); await tick(40);
  ok(/a second reason while typing/.test(card('ap-1').textContent), 'precondition: it landed on focusout');
  ok(card('ap-1').querySelector('.ag-approve').disabled && card('ap-1').querySelector('.ag-reject').hidden, 'a caught-up card mid-flight is still busy');
  card('ap-1').querySelector('.ag-ap-new[data-i="0"]').value = '0.123';
  // the door refuses: the card comes back, with the person's value
  release(); await tick(40);
  const after = card('ap-1');
  ok(!after.querySelector('.ag-approve').disabled && /Write to chip/.test(after.querySelector('.ag-approve').textContent) && !after.querySelector('.ag-reject').hidden,
     'a refused write gives the card back');
  ok(after.querySelector('.ag-ap-new[data-i="0"]').value === '0.123', 'with the typed value still in it');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
