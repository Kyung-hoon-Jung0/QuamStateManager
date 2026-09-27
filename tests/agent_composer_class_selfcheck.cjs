/* QA agents round (3) -- body.ag-composer-on follows the composer.
 * The toast sink (#status-bar) sits above an on-screen agent composer. That was a
 * `body:has(...)` rule, which made every DOM mutation re-match the whole page
 * (/bulk big30x: 118-166 ms per insert+layout). agent.js now keeps a body class;
 * this pins that the class follows: home mount, navigation away (afterSwap),
 * Back (historyRestore), float show / hide via toggleFloat, and a hide done by
 * anyone else directly on the popover's class (the attribute observer).
 * Run: node tests/agent_composer_class_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const POP = '<div id="agent-popover" class="agent-popover agent-hidden"><div class="agent-header"></div><div class="agent-body"></div></div>';
const dom = new JSDOM('<!doctype html><html><body><main id="table-pane"><div id="agent-home" class="agent-home"></div></main>' + POP + '</body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
// bridge every global agent.js reads bare (CLAUDE.md harness rule)
global.MutationObserver = window.MutationObserver;
global.requestAnimationFrame = window.requestAnimationFrame.bind(window);
global.HTMLElement = window.HTMLElement;
const feed = { ok: true, chip: 'PJ', last: 0, agent_seq: 0, qubits: 1, cards: [], live: { plans: [], runs: [], approvals: [] },
  session: null, file: { owner: null, armed: false, stopped: false }, now: { state: 'idle', events_today: 0, failures_today: 0, waiting: 0 } };
global.fetch = window.fetch = function (url) {
  let body = { ok: true };
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'PJ', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 30));
const on = () => document.body.classList.contains('ag-composer-on');
const pop = document.getElementById('agent-popover');
const pane = document.getElementById('table-pane');

(async () => {
  window.AgentPanel.init();
  await tick();
  ok(on(), 'the Agent home mounted in #table-pane: class on');

  // navigate away: htmx swaps the pane, then fires afterSwap
  pane.innerHTML = '<table id="bulk"><tr><td>x</td></tr></table>';
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap'));
  await tick();
  ok(!on(), 'swapped away from the home: class off');

  // float shown / hidden through the button
  window.toggleAgentPanel();
  ok(!pop.classList.contains('agent-hidden') && on(), 'float shown: class on (synchronously)');
  window.toggleAgentPanel();
  await tick();
  ok(pop.classList.contains('agent-hidden') && !on(), 'float hidden by its toggle: class off');

  // a hide done directly on the class (not through toggleFloat) is still followed
  window.toggleAgentPanel();
  ok(on(), 'float reopened: class on');
  pop.classList.add('agent-hidden');
  await tick();
  ok(!on(), 'float hidden by someone else on the class attribute: class off (observer)');
  pop.classList.remove('agent-hidden');
  await tick();
  ok(on(), 'float shown by someone else on the class attribute: class on (observer)');
  pop.classList.add('agent-hidden');
  await tick();

  // Back to the home: historyRestore puts it back outside every swap hook
  pane.innerHTML = '<div id="agent-home" class="agent-home"></div>';
  document.dispatchEvent(new window.CustomEvent('htmx:historyRestore'));
  await tick();
  ok(on(), 'Back restored the home: class on');

  // the float stands down on the home page, but the home keeps the class on
  pop.classList.remove('agent-hidden');
  window.AgentPanel.init();
  await tick();
  ok(pop.classList.contains('agent-hidden') && on(), 'float stands down on the home; home keeps the class on');

  // a home that is NOT in #table-pane (same scope as the old :has rule) does not count
  pane.innerHTML = '';
  const other = document.createElement('div'); other.id = 'agent-home'; other.className = 'agent-home';
  document.body.appendChild(other);
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap'));
  await tick();
  ok(!on(), 'a home outside #table-pane: class off (scope of the old rule kept)');

  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
