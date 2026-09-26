/* QA round (agents menu): a name typed with non-ASCII letters (the header it
 * rides must be ISO-8859-1) was stripped to nothing in storage while the box
 * kept showing it -- every door then recorded a plain "human" under a box that
 * named someone. The box now says so where it is typed. Real agent.js under jsdom.
 * Run: node tests/agent_actor_box_selfcheck.cjs */
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
global.fetch = window.fetch = function (url) {
  let body = { ok: true, cards: [], live: {}, session: {}, file: {}, now: { state: 'idle' } };
  if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
(async () => {
  await tick(40);
  const box = document.querySelector('#agent-home .ag-actor');
  // the markup's inline handler, bridged: jsdom does not run inline on* attributes here
  const handler = box.getAttribute('oninput');
  ok(/AgentPanel\.setActor\(this\.value, this\)/.test(handler), 'the box hands itself to setActor');
  const type = (v) => { box.value = v; window.AgentPanel.setActor(box.value, box); };
  type('정경훈');
  ok(localStorage.getItem('quam_actor_name') === '', 'precondition: storage holds nothing for a Hangul name');
  ok(!box.checkValidity() && /English letters only/.test(box.validationMessage) && /human/.test(box.validationMessage),
     'the box says it will record a plain human: ' + box.validationMessage);
  ok(box.classList.contains('ag-actor-bad'), 'and is marked');
  type('Park 정');
  ok(/“Park”/.test(box.validationMessage), 'a partly-ASCII name says what IS recorded: ' + box.validationMessage);
  type('Park');
  ok(box.checkValidity() && !box.classList.contains('ag-actor-bad') && localStorage.getItem('quam_actor_name') === 'Park',
     'a clean name clears the mark');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
