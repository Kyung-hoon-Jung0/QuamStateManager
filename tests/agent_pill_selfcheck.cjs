/* docs/173 S3 -- agent-pill.js against the real file under jsdom.
 * Pins: describe() renders each state's text in the precedence's vocabulary,
 * the reservation line, render() sets the state class + data attribute, a
 * live-wake event with a NEW agent_seq re-fetches and an unchanged one does
 * not, the safety refresh is force. Run: node tests/agent_pill_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><ul><li id="agent-pill" class="agent-pill agent-idle" hidden data-state="idle">'
  + '<a class="agent-pill-link"><span class="agent-pill-dot"></span><span class="agent-pill-text">Agent</span><span class="agent-pill-res" hidden></span></a></li></ul></body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
let payload = { ok: true, seq: 1, state: 'idle', mode: 'ask-writes' };
const fetches = [];
global.fetch = window.fetch = function (url) {
  fetches.push(url);
  return Promise.resolve({ json: function () { return Promise.resolve(payload); } });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
const tick = () => new Promise(r => setTimeout(r, 10));
const now = Date.now() / 1000;

(async () => {
  const D = window.AgentPill.describe;
  ok(D({ state: 'idle' }).text === 'Agent', 'idle is a bare Agent');
  ok(/thinking · by_claude/.test(D({ state: 'between', session: { backend: 'claude' } }).text), 'between names the backend');
  const r = D({ state: 'running', running: { node: '05_power_rabi', since: now - 130, backend: 'codex', typical_s: 240 }, mode: 'auto' });
  ok(/^Agent: 05_power_rabi · 2m · by_codex · auto$/.test(r.text), 'running: node, elapsed, backend, mode: ' + r.text);
  ok(/usually ~4 min/.test(r.title), 'running carries the typical duration');
  ok(/no sign of life 20m/.test(D({ state: 'stalled', last: { ts: now - 1200 } }).text), 'stalled says how long');
  ok(D({ state: 'failed', failures_today: 2 }).text === 'Agent: ✗ 2 today', 'failed counts');
  ok(D({ state: 'waiting', waiting: 3 }).text === 'Agent: waiting for approval (3)', 'waiting counts');
  ok(D({ state: 'limited', limited_resets: '14:50' }).text === 'Agent: limited · resets 14:50', 'limited names the reset');
  ok(/human ran 12_T1 · 5m ago/.test(D({ state: 'human-ran', human_ran: { node: '12_T1', ts: now - 300 } }).text), 'human-ran is past tense');
  const res = D({ state: 'between', session: { owner: '이OO', until: now + 3600, stopped: true } });
  ok(/reserved by 이OO until \d\d:\d\d · STOPPED/.test(res.title), 'the reservation and a Stop show in the title: ' + res.title);

  window.AgentPill.render({ state: 'running', running: { node: 'x', since: now }, session: { owner: '김OO', until: now + 60 } });
  const el = document.getElementById('agent-pill');
  ok(el.className === 'agent-pill agent-running' && el.getAttribute('data-state') === 'running' && el.hidden === false, 'render sets class, data-state, unhides');
  ok(el.querySelector('.agent-pill-res').hidden === false && /김OO → \d\d:\d\d/.test(el.querySelector('.agent-pill-res').textContent), 'the reservation line renders');
  window.AgentPill.render({ state: 'idle' });
  ok(el.querySelector('.agent-pill-res').hidden === true, 'no session, no reservation line');

  // a live-wake wake with a new agent_seq re-fetches; the same seq does not
  fetches.length = 0;
  payload = { ok: true, seq: 7, state: 'between', session: { backend: 'claude' } };
  document.dispatchEvent(new window.CustomEvent('sm:runs-changed', { detail: { tick: 3, agent_seq: 7 } }));
  await tick();
  ok(fetches.length === 1 && el.getAttribute('data-state') === 'between', 'a wake with a new seq re-fetches and renders');
  document.dispatchEvent(new window.CustomEvent('sm:runs-changed', { detail: { tick: 4, agent_seq: 7 } }));
  await tick();
  ok(fetches.length === 1, 'the same seq does not re-fetch');
  document.dispatchEvent(new window.CustomEvent('sm:runs-changed', { detail: { tick: 5 } }));
  await tick();
  ok(fetches.length === 2, 'a wake without a seq (a run folder) re-fetches');

  console.log(fails ? ('FAILED ' + fails) : ('all checks passed (' + passes + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
