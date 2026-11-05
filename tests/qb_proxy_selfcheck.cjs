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
  w.Headers = Headers;
  w.fetch = function (input, init) { made.push(['fetch', String(input && input.url || input), init]); return Promise.resolve(); };
  w.XMLHttpRequest.prototype.send = function () { made.push(['xhr', this.__hdr || {}]); };
  w.XMLHttpRequest.prototype.setRequestHeader = function (k, v) { (this.__hdr = this.__hdr || {})[k] = v; };
  const s = w.document.createElement('script');
  s.setAttribute('data-ws', opts.ws === undefined ? 'ws://127.0.0.1:8101/' : opts.ws);
  s.setAttribute('data-prefix', '/qualibrate-ui/');
  if (opts.token !== null) s.setAttribute('data-token', opts.token || 'T0K');
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
// J. QUAlibrate's own calls carry the token; nothing else does
{
  const { w, made } = world('');
  const tokOf = (i) => { const init = made[i][2]; return init && init.headers ? new Headers(init.headers).get('X-QSM-Proxy-Token') : null; };
  w.fetch('api/snapshot/1');
  w.fetch('http://127.0.0.1:5491/qualibrate-ui/execution/submit/node', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  w.fetch('http://evil.example/qualibrate-ui/x');
  w.fetch('/field/edit', { method: 'POST' });
  ok(tokOf(0) === 'T0K', 'relative call carries the token');
  ok(tokOf(1) === 'T0K' && new Headers(made[1][2].headers).get('Content-Type') === 'application/json', 'absolute call keeps its headers and gains the token');
  ok(tokOf(2) === null, 'another host never gets the token');
  ok(tokOf(3) === null, 'an SM route outside the prefix never gets the token');
  const x = new w.XMLHttpRequest(); x.open('GET', 'execution/meta'); x.send();
  const y = new w.XMLHttpRequest(); y.open('GET', 'http://evil.example/qualibrate-ui/x'); y.send();
  const xs = made.filter(m => m[0] === 'xhr');
  ok(xs[0][1]['X-QSM-Proxy-Token'] === 'T0K' && !xs[1][1]['X-QSM-Proxy-Token'], 'XHR: ours carries it, foreign does not');
}
{
  const { w, made } = world('', { token: null });
  w.fetch('api/x');
  ok(!made[0][2], 'no token attribute: fetch untouched');
}
// K. QUAlibrate 1.5.1 title bar: the open run gets a link when it names the selected card
const FX = (f) => fs.readFileSync(path.join(__dirname, 'fixtures', 'qualibrate_ui', f), 'utf8');
{
  const { w } = world(FX('v1_5_1_history.html'));
  w.__qsmProxy.pass();
  const h = links(w).filter(a => a.getAttribute('data-qsm-where') === 'header');
  ok(h.length === 1 && h[0].getAttribute('href') === '/qualibrate-run/3', 'v1 title bar: one link to the open run');
  ok(h[0].previousElementSibling.tagName === 'H2' && !w.document.querySelector('h2 [data-qsm-open]'), 'v1 title bar: the link sits right after the title, not inside it');
  ok(links(w).filter(a => a.getAttribute('data-qsm-where') === 'card').length === 2, 'v1 cards keep theirs');
  // another card selected, title not yet updated: no header link (exact match or nothing)
  const cards = w.document.querySelectorAll('[class*=listCard]');
  cards[0].classList.remove('ListCard-module__selected'); cards[1].classList.add('ListCard-module__selected');
  w.__qsmProxy.pass();
  ok(links(w).filter(a => a.getAttribute('data-qsm-where') === 'header').length === 0, 'v1 title disagreeing with the selection: no link');
  w.document.querySelector('h2').firstChild.textContent = '2 t1';
  w.__qsmProxy.pass();
  const h2 = links(w).filter(a => a.getAttribute('data-qsm-where') === 'header');
  ok(h2.length === 1 && h2[0].getAttribute('href') === '/qualibrate-run/2', 'v1 title follows the selection');
  w.document.querySelector('h2').firstChild.textContent = 'Node Library';
  w.__qsmProxy.pass();
  ok(links(w).filter(a => a.getAttribute('data-qsm-where') === 'header').length === 0, 'v1 a title that is not a run: no link');
}
// L. QUAlibrate 2.0: each row of the runs table and the open run's header
{
  const { w, made } = world(FX('v2_0_0_results.html'));
  w.__qsmProxy.pass();
  const rows = links(w).filter(a => a.getAttribute('data-qsm-where') === 'row');
  ok(rows.map(a => a.getAttribute('href')).join() === '/qualibrate-run/3,/qualibrate-run/2', 'v2 rows: ' + rows.map(a => a.getAttribute('href')));
  ok(rows.every(a => /_idCell_/.test(a.parentElement.className)), 'v2 row link sits in the id cell');
  const hd = links(w).filter(a => a.getAttribute('data-qsm-where') === 'header');
  ok(hd.length === 1 && hd[0].getAttribute('href') === '/qualibrate-run/3', 'v2 header: one link');
  w.__qsmProxy.pass();
  ok(links(w).length === 3, 'v2 a second pass adds nothing');
  // the row navigates on press: the link's press and click must not reach it
  let pressed = 0;
  w.document.querySelector('tbody tr').addEventListener('mousedown', () => { pressed++; });
  w.document.querySelector('tbody tr').addEventListener('click', () => { pressed++; });
  rows[0].dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true }));
  rows[0].dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(pressed === 0, 'v2 the row sees neither press nor click');
  ok(made.some(m => m[0] === 'open' && m[1] === '/qualibrate-run/3'), 'v2 the click opens the run');
  // a row reused for another run follows; a header showing another run follows
  w.document.querySelector('tbody tr td div').textContent = '9';
  w.document.querySelector('[class*=_id_]').textContent = '#2';
  w.__qsmProxy.pass();
  const now = links(w).map(a => a.getAttribute('data-qsm-where') + a.getAttribute('href')).sort();
  ok(now.join() === 'header/qualibrate-run/2,row/qualibrate-run/2,row/qualibrate-run/9', 'v2 reused row and header follow: ' + now);
  // the header gone: its link goes
  const hdr = w.document.querySelector('[class*=_resultsContainer_]'); hdr.parentNode.removeChild(hdr);
  w.__qsmProxy.pass();
  ok(links(w).filter(a => a.getAttribute('data-qsm-where') === 'header').length === 0, 'v2 no header, no header link');
}
// M. 2.0 look-alikes get nothing: no id cell, two id cells, a non-number, a hash-less class, two ids in a header
{
  const row = (cells) => '<table><tbody><tr class="_tableRow_1xvzq_23">' + cells + '</tr></tbody></table>';
  const { w } = world(
    row('<td class="_tableCell_1xvzq_73">3</td>') +
    row('<td class="_idCell_1qftv_52">4</td><td class="_idCell_1qftv_52">5</td>') +
    row('<td class="_idCell_1qftv_52">#6</td>') +
    row('<td class="_idCell_1qftv_52">7 a</td>') +
    row('<td class="idCell">8</td>') +
    row('<td class="x_idCell_1qftv_52">10</td>') +
    '<div class="_header_19ahd_21"><span class="_id_1qyh6_28">#11</span><span class="_id_1qyh6_28">#12</span></div>' +
    '<div class="_other_19ahd_21"><span class="_id_1qyh6_28">#13</span></div>' +
    '<div class="_header_19ahd_21"><span class="_id_1qyh6_28">13</span></div>');
  w.__qsmProxy.pass();
  ok(links(w).length === 0, 'v2 look-alikes: nothing added, got ' + links(w).map(a => a.getAttribute('href')));
}
// N. an exception inside a pass never reaches QUAlibrate
{
  const { w } = world(FX('v2_0_0_results.html'));
  const orig = w.document.getElementsByTagName;
  w.document.getElementsByTagName = function () { throw new Error('boom'); };
  let threw = false;
  try { w.__qsmProxy.pass(); } catch (e) { threw = true; }
  w.document.getElementsByTagName = orig;
  ok(!threw, 'a throwing DOM call is swallowed');
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
