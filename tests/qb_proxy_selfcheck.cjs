/* qb-proxy.js under jsdom: the Open-in-QSM links and the WebSocket rewrite.
 * Driven by tests/test_qualibrate_proxy.py::test_the_script_selfcheck.
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('jsdom not installed'); process.exit(0); }

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'qb-proxy.js'), 'utf8');
let n = 0, fails = 0;
function ok(c, msg) { n++; if (!c) { fails++; console.log('FAIL ' + msg); } }

function card(id, extra) {
  return '<div class="ListCard-module__listCard x"><span class="ListCard-module__title">node</span>' +
    '<div class="ListCard-module__description"><div class="ExecutionCard-module__executionMetaItem"><span>' + id +
    '</span></div></div>' + (extra || '') + '</div>';
}
function world(body, opts) {
  opts = opts || {};
  const dom = new JSDOM('<!doctype html><html><head></head><body>' + body + '</body></html>',
    { url: 'http://127.0.0.1:5491/qualibrate-ui/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  const made = [];
  w.WebSocket = function (url, p) { made.push([url, p]); this.url = url; };
  w.WebSocket.OPEN = 1;
  w.open = function (u, name) { made.push(['open', u, name]); };
  const s = w.document.createElement('script');
  s.setAttribute('data-ws', opts.ws === undefined ? 'ws://127.0.0.1:8101/' : opts.ws);
  s.setAttribute('data-prefix', '/qualibrate-ui/');
  w.document.head.appendChild(s);
  Object.defineProperty(w.document, 'currentScript', { configurable: true, get: () => s });
  w.eval(SRC);
  return { w, made };
}
const TL = (inner) => '<div class="SnapshotsTimeline-module__wrapper">' + inner + '</div>';
const links = (w) => [...w.document.querySelectorAll('[data-qsm-open]')];

// A. one link per card, pointing at its own run
{
  const { w } = world(TL(card('#25') + card('#24')));
  w.__qsmProxy.pass();
  const L = links(w);
  ok(L.length === 2, 'two cards, two links');
  ok(L[0].getAttribute('href') === '/qualibrate-run/25' && L[1].getAttribute('href') === '/qualibrate-run/24', 'hrefs follow the cards');
  ok(L[0].textContent === 'Open in QSM', 'the label');
  w.__qsmProxy.pass();
  ok(links(w).length === 2, 'a second pass adds nothing');
}
// B. React reuses a card for another run: the link follows
{
  const { w } = world(TL(card('#25')));
  w.__qsmProxy.pass();
  w.document.querySelector('[class*=executionMetaItem] span').textContent = '#7';
  w.__qsmProxy.pass();
  const L = links(w);
  ok(L.length === 1 && L[0].getAttribute('href') === '/qualibrate-run/7', 'a reused card updates its link');
  w.document.querySelector('[class*=executionMetaItem] span').textContent = 'running';
  w.__qsmProxy.pass();
  ok(links(w).length === 0, 'a card that lost its number loses the link');
}
// C. markup that is not QUAlibrate's run list gets nothing
{
  const { w } = world(card('#25') + TL('<div class="Other">#3</div>') +
    TL(card('#5<b>x</b>')) + TL(card('# 9')) + TL(card('#1234567890')));
  w.__qsmProxy.pass();
  ok(links(w).length === 0, 'outside the timeline, odd ids, or no card: nothing added');
}
// D. a nested card's number is not its parent's
{
  const { w } = world(TL(card('#30', card('#31'))));
  w.__qsmProxy.pass();
  const hrefs = links(w).map(a => a.getAttribute('href')).sort();
  ok(hrefs.join() === '/qualibrate-run/30,/qualibrate-run/31', 'parent and child each get their own: ' + hrefs);
}
// D2. two ids in one card: which run is unknown, so no link
{
  const { w } = world(TL('<div class="ListCard-module__listCard"><div class="ExecutionCard-module__executionMetaItem"><span>#4</span></div>' +
    '<div class="ExecutionCard-module__executionMetaItem"><span>#5</span></div></div>'));
  w.__qsmProxy.pass();
  ok(links(w).length === 0, 'two numbers in one card: nothing added');
}
// E. a click opens one named tab and does not reach the card
{
  const { w, made } = world(TL(card('#25')));
  w.__qsmProxy.pass();
  let reached = false;
  w.document.querySelector('[class*=listCard]').addEventListener('click', () => { reached = true; });
  links(w)[0].dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(!reached, 'the card does not see the click');
  ok(made.some(m => m[0] === 'open' && m[1] === '/qualibrate-run/25' && m[2] === 'qsm-run'), 'one named tab');
}
// F. framed: the parent is asked, no tab opens
{
  const { w, made } = world(TL(card('#25')));
  const msgs = [];
  Object.defineProperty(w, 'parent', { configurable: true, value: { postMessage: (d, o) => msgs.push([d, o]) } });
  w.__qsmProxy.pass();
  links(w)[0].dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(msgs.length === 1 && msgs[0][0].type === 'qsm-open-run' && msgs[0][0].href === '/qualibrate-run/25', 'the workbench is asked');
  ok(!made.some(m => m[0] === 'open'), 'no tab when framed');
}
// G. WebSockets under the prefix go to QUAlibrate; others are untouched
{
  const { w, made } = world('');
  new w.WebSocket('ws://127.0.0.1:5491/qualibrate-ui/execution/ws/run_status');
  new w.WebSocket('ws://127.0.0.1:5491/other/ws');
  new w.WebSocket('ws://evil.example/qualibrate-ui/x', ['p']);
  ok(made[0][0] === 'ws://127.0.0.1:8101/execution/ws/run_status', 'prefix rewritten: ' + made[0][0]);
  ok(made[1][0] === 'ws://127.0.0.1:5491/other/ws', 'other path untouched');
  ok(made[2][0] === 'ws://evil.example/qualibrate-ui/x' && made[2][1][0] === 'p', 'other host untouched');
  ok(w.WebSocket.OPEN === 1, 'constants kept');
}
// H. a bad data-ws leaves WebSocket native
{
  const { w, made } = world('', { ws: 'javascript:alert(1)' });
  new w.WebSocket('ws://127.0.0.1:5491/qualibrate-ui/x');
  ok(made[0][0] === 'ws://127.0.0.1:5491/qualibrate-ui/x', 'no rewrite without a valid target');
}
// I. the observer runs without being called
{
  const { w } = world(TL(''));
  w.document.querySelector('[class*=SnapshotsTimeline]').innerHTML = card('#3');
  setTimeout(() => {
    ok(links(w).length === 1, 'the observer adds the link on its own');
    console.log(fails ? `FAILED ${fails} of ${n} assertions` : `ok (${n} assertions)`);
    process.exit(fails ? 1 : 0);
  }, 200);
}
