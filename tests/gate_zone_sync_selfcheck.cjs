/* jsdom selfcheck under the REAL bundled htmx: presses inside the pair's CZ gate zone and the
 * zone's own refresh never lose each other, and nothing is aborted.
 *   A  refresh out, then Delete: both reach the server; the refresh answer is not painted while
 *      the press is out; the press answer is painted; one refresh follows the press (it was held)
 *   P  one press at a time: while a press is out the zone's other press buttons are disabled
 *      (Edit stays free), a press tried anyway sends nothing, the buttons come back after the
 *      answer, a button the server rendered disabled stays disabled, focus stays on the press
 *   S  a press whose answer did not land (5xx, abort) is followed by exactly one refresh
 *   M  a press answer painted into the zone (list refusal, success with a warning, the delete
 *      confirm's slot answer, a refused delete): no refresh, the message stands; with a trigger
 *      held during the press: one refresh, the message stands in the same gate's slot of the
 *      new zone, or in the zone-level slot when the row is gone
 *   D  the press answer arrives after its zone was replaced: one refresh; the zone rendered
 *      during the press is disabled and restored at the settle
 *   F  focus inside a replaced zone goes to the same control, else the first enabled button
 *   Q  a refresh trigger while a refresh is out runs once more after the first answers
 *   B  a refresh trigger while a press is out: one refresh in total after the press
 *   T  two presses: the second cannot be sent while the first is out
 *   C  Edit values and an inspector navigation: whichever is pressed later paints; an older
 *      navigation GET still out is the one request given up (the pane's own rule)
 *   N  only press answers (the server's data-press-answer mark, slot answers) are carried, only
 *      into the refresh their own press owed, and once: chip state notices on screen always
 *      equal the server's count, a later refresh of any cause or a refresh nobody owed shows
 *      the server's render alone, a fresh answer of the same gate wins, a delete notice
 *      never lands on a later gate of the same name, an owed refresh that fails ends the debt,
 *      and a zone-level notice standing at a later press's start is not carried again
 * Run: node tests/gate_zone_sync_selfcheck.cjs <fixture.json>
 *      (driven by tests/test_gate_status_routes.py, which renders the zone and the answers)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const HTMX = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
const GATE = fs.readFileSync(path.join(STATIC, 'gate-status.js'), 'utf8');
const FIX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

let n = 0;
function ok(cond, msg) { if (!cond) { console.error('FAIL - ' + msg); process.exit(1); } n++; console.log('ok - ' + msg); }

const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

// the mock server, installed in the page's realm: every request waits until the test answers it
const MOCK = `(function () {
  window.__reqs = []; window.__aborted = 0; window.__errors = [];
  var cerr = console.error;
  console.error = function () { window.__errors.push(Array.prototype.join.call(arguments, ' ')); };
  function Target() { this._l = {}; }
  Target.prototype.addEventListener = function (t, f) { (this._l[t] = this._l[t] || []).push(f); };
  Target.prototype._fire = function (t) { (this._l[t] || []).forEach(function (f) { f({type: t}); }); };
  function X() { Target.call(this); this.upload = new Target(); this._h = {}; this.status = 0; this.readyState = 0; }
  X.prototype = Object.create(Target.prototype);
  X.prototype.open = function (m, u) { this.method = m; this.url = u; };
  X.prototype.overrideMimeType = function () {};
  X.prototype.setRequestHeader = function () {};
  X.prototype.send = function (b) { this.body = b; window.__reqs.push(this); };
  X.prototype.abort = function () { window.__aborted++; if (this.onabort) this.onabort(); this._fire('loadend'); };
  X.prototype.getAllResponseHeaders = function () {
    var h = this._h; return Object.keys(h).map(function (k) { return k + ': ' + h[k]; }).join('\\r\\n'); };
  X.prototype.getResponseHeader = function (k) {
    for (var key in this._h) if (key.toLowerCase() === k.toLowerCase()) return this._h[key]; return null; };
  X.prototype.respond = function (body, headers, status) {
    this._h = headers || {}; this.status = status || 200; this.readyState = 4;
    this.response = this.responseText = body; this.responseURL = 'http://localhost' + this.url;
    this.onload(); this._fire('load'); this._fire('loadend');
  };
  window.XMLHttpRequest = X;
})();`;

// only the headers htmx acts on (the answer's own HX-Trigger events are not part of this check)
function hx(headers) {
  const out = {};
  for (const k of Object.keys(headers || {})) if (/^HX-(Retarget|Reswap)$/i.test(k)) out[k] = headers[k];
  return out;
}

async function page() {
  const html = '<!doctype html><html><head></head><body>'
    + '<a id="nav" hx-get="/pair/q1-q2" hx-target="#inspector-pane" hx-sync="#inspector-pane:queue last">nav</a>'
    + '<div id="inspector-pane" hx-sync="this:replace">' + FIX.zone + '</div></body></html>';
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  w.eval(XPATH_SHIM);
  w.eval(MOCK);
  w.eval(HTMX);
  w.eval(GATE);
  w.eval('htmx.config.defaultSettleDelay = 0; htmx.process(document.body);');
  w.__fix = FIX;
  return w;
}

async function until(w, cond, what) {
  for (let i = 0; i < 2000; i++) {
    if (w.eval(cond)) return;
    await new Promise((r) => w.setTimeout(r, 1));
  }
  ok(false, 'timed out waiting for ' + what);
}
// let every queued task run (htmx settles, our load handlers)
async function drain(w) { for (let i = 0; i < 20; i++) await new Promise((r) => w.setTimeout(r, 0)); }

function clean(w, tag) {
  ok(w.eval('__aborted') === 0, tag + ': no request was aborted');
  ok(!w.eval('__errors').some((e) => /abort/i.test(e)), tag + ': no abort line on the console');
}

const LIST = /\/pulse\/cz-gate\/list/;
const lists = (w) => w.eval('__reqs').filter((r) => LIST.test(r.url)).length;
const btn = (gate, cls) => '.gate-status[data-gate="' + gate + '"] .' + cls;
const q = (w, sel) => w.document.querySelector(sel);

(async function main() {
  // A: refresh first, press second (the stale window), both answer orders
  for (const order of ['refresh-first', 'press-first']) {
    const w = await page();
    const tag = 'A ' + order;
    w.eval('document.dispatchEvent(new CustomEvent("stateRestored"))');
    await until(w, '__reqs.length === 1', 'the refresh request');
    ok(LIST.test(w.eval('__reqs[0].url')), tag + ': the refresh reached the server');
    q(w, btn('cz_sq', 'gate-status-delete')).click();
    await until(w, '__reqs.length === 2', 'the press request');
    ok(/delete-offer/.test(w.eval('__reqs[1].url')), tag + ': the Delete press reached the server');
    w.__h = hx(FIX.gone.headers);
    if (order === 'refresh-first') {
      w.eval('__reqs[0].respond(window.__fix.list.body, {})');
      await drain(w);
      ok(!!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the refresh is not painted while the press is out');
      w.eval('__reqs[1].respond(window.__fix.gone.body, window.__h)');
    } else {
      w.eval('__reqs[1].respond(window.__fix.gone.body, window.__h)');
      await drain(w);
      ok(w.document.body.textContent.includes('This gate is gone.'), tag + ': the press answer "This gate is gone" is shown');
      w.eval('__reqs[0].respond(window.__fix.list.body, {})');
    }
    await until(w, '__reqs.length === 3', 'the refresh after the press');
    ok(LIST.test(w.eval('__reqs[2].url')), tag + ': one refresh follows the press');
    w.eval('__reqs[2].respond(window.__fix.list.body, {})');
    await drain(w);
    ok(!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the deleted gate has no row');
    ok(w.document.querySelectorAll('.gate-status-list').length === 1, tag + ': one zone');
    ok(w.eval('__reqs.length') === 3, tag + ': nothing more is sent');
    clean(w, tag);
  }

  // P: one press at a time; buttons restored when the zone was not re-rendered
  {
    const w = await page();
    const tag = 'P';
    w.eval('(function () { var b = document.createElement("button"); b.className = "srv-off"; b.disabled = true;'
      + ' b.setAttribute("hx-post", "/x"); document.querySelector(".gate-status-actions").appendChild(b); htmx.process(b); })()');
    const press = q(w, btn('cz_sq', 'gate-status-delete'));
    press.focus();
    press.click();
    await until(w, '__reqs.length === 1', 'the press request');
    const others = Array.from(w.document.querySelectorAll('.gate-status-list button[hx-get], .gate-status-list button[hx-post]'))
      .filter((b) => b !== press && !b.classList.contains('gate-status-edit'));
    ok(others.length >= 3 && others.every((b) => b.disabled), tag + ': every other press button is disabled (' + others.length + ')');
    ok(!press.disabled, tag + ': the pressed button itself is not disabled');
    ok(Array.from(w.document.querySelectorAll('.gate-status-edit')).every((b) => !b.disabled), tag + ': Edit values stays free');
    const sib = q(w, btn('cz_two', 'gate-status-delete'));
    sib.click();
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': a click on a disabled sibling sends nothing');
    sib.disabled = false;
    sib.click();
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': a sibling press tried anyway is refused before it is sent');
    sib.disabled = true;
    w.eval('__reqs[0].respond("<p id=\\"slotans\\">answer</p>", {})');
    await until(w, '!!document.getElementById("slotans")', 'the slot answer');
    ok(others.filter((b) => !b.classList.contains('srv-off')).every((b) => !b.disabled), tag + ': the disabled buttons are enabled again');
    ok(q(w, '.srv-off').disabled, tag + ': a button the server rendered disabled stays disabled');
    ok(w.document.activeElement === press, tag + ': the focus stays on the pressed button');
    clean(w, tag);
  }

  // S: a press whose answer did not land on screen is followed by exactly one refresh
  const kinds = [
    ['5xx answer', (w) => w.eval('__reqs[0].respond("boom", {}, 500)')],
    ['aborted', (w) => w.eval('__reqs[0].abort()')],
  ];
  for (const [kind, answer] of kinds) {
    const w = await page();
    const tag = 'S ' + kind;
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the press request');
    answer(w);
    await until(w, '__reqs.length === 2', 'the refresh after the press (' + kind + ')');
    await drain(w);
    ok(lists(w) === 1 && w.eval('__reqs.length') === 2, tag + ': exactly one refresh request');
  }

  // M: a press answer painted into the zone: no refresh, its message stands; with a refresh
  // trigger held during the press: one refresh, and the message stands in the new zone
  const answer = (w, i, a) => { w.__h = hx(a.headers); w.__b = a.body; w.eval('__reqs[' + i + '].respond(window.__b, window.__h)'); };
  async function offerThenConfirm(w, tag) {
    q(w, btn('cz_two', 'gate-status-delete')).click();
    await until(w, '__reqs.length === 1', 'the Delete press');
    answer(w, 0, FIX.offer);
    await until(w, '!!document.querySelector(".gate-delete-confirm")', 'the delete offer');
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': the painted offer asks no refresh');
    q(w, '.gate-delete-confirm').click();
    await until(w, '__reqs.length === 2', 'the confirm press');
    return 1;
  }
  const presses = [
    // kind, how to press (returns the index of the last press), its answer, the sentence, the row it stands on
    ['list refusal', async (w) => { q(w, btn('cz_sq', 'gate-status-activate')).click(); await until(w, '__reqs.length === 1', 'the press'); return 0; },
      FIX.refuse, 'already the active CZ gate', 'cz_two'],
    ['success with warning', async (w) => { q(w, btn('cz_sq', 'gate-status-activate')).click(); await until(w, '__reqs.length === 1', 'the press'); return 0; },
      FIX.warn, 'could not be saved', 'cz_sq'],
    ['delete confirm slot answer', offerThenConfirm, FIX.slotfail, 'is the active CZ gate of pair', 'cz_two'],
    ['refused delete', offerThenConfirm, FIX.delrefuse, 'is the active CZ gate of pair', 'cz_two'],
  ];
  for (const [kind, press, ans, says, gate] of presses) {
    for (const hold of [false, true]) {
      const w = await page();
      const tag = 'M ' + kind + (hold ? ' + trigger held' : '');
      const i = await press(w, tag);
      if (hold) {
        w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
        await drain(w);
      }
      answer(w, i, ans);
      await drain(w);
      ok(w.document.body.textContent.includes(says), tag + ': the message is painted');
      if (!hold) {
        ok(lists(w) === 0 && w.eval('__reqs.length') === i + 1, tag + ': no refresh request follows');
        ok(w.document.body.textContent.includes(says), tag + ': the message stands');
        clean(w, tag);
        continue;
      }
      await until(w, '__reqs.length === ' + (i + 2), 'the held refresh');
      ok(lists(w) === 1, tag + ': one refresh');
      answer(w, i + 1, FIX.full);
      await drain(w);
      ok(w.eval('__reqs.length') === i + 2, tag + ': and only one');
      const slot = q(w, '.gate-status[data-gate="' + gate + '"] .gate-status-slot');
      ok(slot && slot.textContent.includes(says), tag + ': the message stands in the same gate\'s slot of the new zone');
      ok(w.document.querySelectorAll('.gate-status-list').length === 1, tag + ': one zone');
      clean(w, tag);
    }
  }
  // M row gone: the held refresh answers a list without the message's gate
  {
    const w = await page();
    const tag = 'M row gone';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the press');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, 0, FIX.warn);
    await until(w, '__reqs.length === 2', 'the held refresh');
    answer(w, 1, FIX.list);
    await drain(w);
    ok(!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the row is gone');
    const zs = q(w, '.gate-status-list [data-gate-zone-slot]');
    ok(zs && zs.textContent.includes('could not be saved'), tag + ': the message stands in the zone-level slot');
    clean(w, tag);
  }

  // D: the press answer arrives after its zone was replaced: one refresh; the zone rendered
  // during the press is disabled like the first one and restored at the settle
  {
    const w = await page();
    const tag = 'D detached';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the press');
    w.__b = FIX.full.body;
    w.eval('htmx.swap(document.querySelector(".gate-status-list"), window.__b, {swapStyle: "outerHTML"})');
    await drain(w);
    const fresh = () => Array.from(w.document.querySelectorAll('.gate-status-list button[hx-get], .gate-status-list button[hx-post]'))
      .filter((b) => !b.classList.contains('gate-status-edit'));
    ok(fresh().length >= 3 && fresh().every((b) => b.disabled), tag + ': the zone rendered during the press is disabled (' + fresh().length + ')');
    answer(w, 0, FIX.activate);
    await until(w, '__reqs.length === 2', 'the refresh after a detached answer');
    ok(lists(w) === 1, tag + ': one refresh');
    ok(fresh().every((b) => !b.disabled), tag + ': its buttons are enabled at the settle');
    answer(w, 1, FIX.full);
    await drain(w);
    ok(w.eval('__reqs.length') === 2, tag + ': and only one');
    clean(w, tag);
  }

  // F: focus inside a zone a refresh replaces goes to the same control, else (the row is gone) to the next
  // row's first enabled button, never to the body
  for (const [kind, gate, ans, twin] of [['twin', 'cz_two', FIX.full, true], ['twin gone', 'cz_sq', FIX.list, false]]) {
    const w = await page();
    const tag = 'F ' + kind;
    q(w, btn(gate, 'gate-status-delete')).focus();
    w.eval('document.dispatchEvent(new CustomEvent("stateRestored"))');
    await until(w, '__reqs.length === 1', 'the refresh');
    answer(w, 0, ans);
    await drain(w);
    const a = w.document.activeElement;
    ok(a && a !== w.document.body && !!a.closest('.gate-status-list'), tag + ': focus is in the new zone, not on the body');
    if (twin) ok(a === q(w, btn(gate, 'gate-status-delete')), tag + ': on the same control of the same gate');
    else ok(a === w.document.querySelector('.gate-status[data-gate="cz_two"] button:not([disabled])'), tag + ': on the first enabled button of the next row');
  }
  {
    const w = await page();
    const tag = 'F list answer';
    const b = q(w, btn('cz_sq', 'gate-status-activate'));
    b.focus();
    b.click();
    await until(w, '__reqs.length === 1', 'the press');
    answer(w, 0, FIX.refuse);
    await drain(w);
    const a = w.document.activeElement;
    ok(a === q(w, btn('cz_sq', 'gate-status-activate')), tag + ': focus on the pressed control of the new zone');
    ok(!a.disabled, tag + ': and it is enabled');
  }

  // Q: a second refresh trigger while a refresh is out
  {
    const w = await page();
    const tag = 'Q';
    w.eval('document.dispatchEvent(new CustomEvent("stateRestored"))');
    await until(w, '__reqs.length === 1', 'the first refresh');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': the second trigger waits while a refresh is out');
    w.eval('__reqs[0].respond(window.__fix.list.body, {})');
    await until(w, '__reqs.length === 2', 'the second refresh');
    ok(LIST.test(w.eval('__reqs[1].url')), tag + ': the second refresh is sent after the first answers');
    w.eval('__reqs[1].respond(window.__fix.list.body, {})');
    await drain(w);
    ok(w.eval('__reqs.length') === 2, tag + ': and only once');
    clean(w, tag);
  }

  // B: press first, refresh trigger while it is out: one refresh in total
  {
    const w = await page();
    const tag = 'B';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the press request');
    ok(/make-active/.test(w.eval('__reqs[0].url')) && w.eval('__reqs[0].method') === 'POST', tag + ': the Make active POST reached the server');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': the refresh waits while the press is out');
    w.__h = hx(FIX.activate.headers);
    w.eval('__reqs[0].respond(window.__fix.activate.body, window.__h)');
    await until(w, '__reqs.length === 2', 'the held refresh');
    const row = q(w, '.gate-status[data-gate="cz_sq"]');
    ok(row && !row.querySelector('.gate-status-activate'), tag + ': the press answer (cz_sq active) is painted');
    ok(LIST.test(w.eval('__reqs[1].url')), tag + ': the held refresh reached the server afterwards');
    w.eval('__reqs[1].respond(window.__fix.list.body, {})');
    await drain(w);
    ok(!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the refresh answer is painted');
    ok(w.eval('__reqs.length') === 2, tag + ': exactly one refresh');
    clean(w, tag);
  }

  // T: two presses: Make active out, then Delete on another row
  {
    const w = await page();
    const tag = 'T';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the first press');
    const del = q(w, btn('cz_two', 'gate-status-delete'));
    del.click();
    del.disabled = false;
    del.click();
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': the second press cannot be sent while the first is out');
    clean(w, tag);
  }

  // C: Edit values and an inspector navigation share the pane's slot: the later press paints
  {
    const w = await page();
    const tag = 'C nav-then-edit';
    w.eval('document.getElementById("nav").click()');
    await until(w, '__reqs.length === 1', 'the navigation');
    w.eval('document.querySelector(\'.gate-status[data-gate="cz_sq"] .gate-status-edit\').click()');
    await until(w, '__reqs.length === 2', 'the Edit request');
    ok(/\/pulse\/detail/.test(w.eval('__reqs[1].url')), tag + ': Edit reached the server');
    ok(w.eval('__aborted') === 1 && w.eval('__reqs[0].method') === 'GET', tag + ': only the older navigation (a GET) was given up');
    w.eval('__reqs[1].respond("<p id=\\"editpage\\">edit</p>", {})');
    await drain(w);
    ok(!!w.document.getElementById('editpage'), tag + ': the Edit answer is painted');
  }
  {
    const w = await page();
    const tag = 'C edit-then-nav';
    w.eval('document.querySelector(\'.gate-status[data-gate="cz_sq"] .gate-status-edit\').click()');
    await until(w, '__reqs.length === 1', 'the Edit request');
    w.eval('document.getElementById("nav").click()');
    await drain(w);
    ok(w.eval('__reqs.length') === 1, tag + ': the later navigation waits behind Edit');
    w.eval('__reqs[0].respond("<p id=\\"editpage\\">edit</p>", {})');
    await until(w, '__reqs.length === 2', 'the navigation');
    w.eval('__reqs[1].respond("<p id=\\"navpage\\">nav</p>", {})');
    await drain(w);
    ok(!!w.document.getElementById('navpage') && !w.document.getElementById('editpage'), tag + ': the later navigation paints last');
    clean(w, tag);
  }

  // N: what a refresh carries
  const count = (w, sel) => w.document.querySelectorAll('.gate-status-list ' + sel).length;
  const times = (w, text) => w.document.querySelector('.gate-status-list').textContent.split(text).length - 1;
  async function refresh(w, a) {
    const i = w.eval('__reqs.length');
    w.eval('document.dispatchEvent(new CustomEvent("stateRestored"))');
    await until(w, '__reqs.length === ' + (i + 1), 'a refresh');
    answer(w, i, a);
    await drain(w);
  }
  const srv = (body, sel) => new JSDOM(body).window.document.querySelectorAll(sel).length;
  const STATE = [['the Re-check notice', '[data-recheck-notice]'], ['a row class-file line', '[data-status-key="needs"]'],
                 ['any zone notice', '.gate-status-notice']];
  {
    const w = await page();
    for (const [kind, sel] of STATE) ok(srv(FIX.noticed.body, sel) >= 1, 'N state kind ' + kind + ' is in the server answer');
    for (let k = 1; k <= 3; k++) {
      await refresh(w, FIX.noticed);
      for (const [kind, sel] of STATE) {
        ok(count(w, sel) === srv(FIX.noticed.body, sel), 'N refresh ' + k + ': ' + kind + ' count equals the server\'s (' + count(w, sel) + ')');
      }
      ok(count(w, '[data-gate-zone-slot] > *') === 0, 'N refresh ' + k + ': nothing is carried into the zone-level slot');
    }
    await refresh(w, FIX.full);
    for (const [kind, sel] of STATE) ok(count(w, sel) === 0, 'N a refresh without state notices: no ' + kind + ' is left');
    clean(w, 'N state');
  }
  // a press whose answer is out while a refresh trigger comes: that refresh is owed by the press
  async function pressHeld(w, tag, how, on, cls, ans, owedAnswer) {
    let i = w.eval('__reqs.length');
    if (how === 'offer') i = await offerThenConfirm(w, tag);
    else { q(w, btn(on, cls)).click(); await until(w, '__reqs.length === ' + (i + 1), 'the press'); }
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, i, ans);
    await until(w, '__reqs.length === ' + (i + 2), 'the owed refresh');
    answer(w, i + 1, owedAnswer);
    await drain(w);
  }
  // every press-answer kind: shown once after its own owed refresh, gone after the next refresh
  const carried = [
    ['row refusal note', 0, FIX.refuse, 'already the active CZ gate', 'cz_two', 'gate-status-activate', 'cz_sq'],
    ['row warning note', 0, FIX.warn, 'could not be saved', 'cz_sq', 'gate-status-activate', 'cz_sq'],
    ['delete confirm slot answer', 'offer', FIX.slotfail, 'is the active CZ gate of pair', 'cz_two'],
    ['zone notice of a delete press', 0, FIX.deleted, 'cz_sq deleted', null, 'gate-status-delete', 'cz_sq'],
    ['zone notice of a gone gate', 0, FIX.gone, 'This gate is gone.', null, 'gate-status-delete', 'cz_sq'],
  ];
  for (const [kind, how, ans, says, gate, cls, on] of carried) {
    for (const cause of ['stateRestored', 'cellsReverted']) {
      const w = await page();
      const tag = 'N carry ' + kind + ', then ' + cause;
      await pressHeld(w, tag, how, on, cls, ans, FIX.full);
      ok(times(w, says) === 1, tag + ': shown exactly once after its owed refresh');
      const where = gate ? q(w, '.gate-status[data-gate="' + gate + '"] .gate-status-slot') : q(w, '[data-gate-zone-slot]');
      ok(where && where.textContent.includes(says), tag + ': in ' + (gate ? 'the gate\'s slot' : 'the zone-level slot'));
      for (let k = 1; k <= 3; k++) {
        const j = w.eval('__reqs.length');
        w.eval('document.dispatchEvent(new CustomEvent("' + cause + '"))');
        await until(w, '__reqs.length === ' + (j + 1), 'a later refresh');
        answer(w, j, FIX.full);
        await drain(w);
        ok(times(w, says) === 0, tag + ': gone after later refresh ' + k);
        ok(count(w, '[data-gate-zone-slot] > *') === 0, tag + ': nothing in the zone-level slot after later refresh ' + k);
      }
      clean(w, tag);
    }
  }
  // a refresh nobody owed carries nothing: the press answer painted, then a plain refresh
  for (const [kind, how, ans, says, gate, cls, on] of carried) {
    const w = await page();
    const tag = 'N not owed ' + kind;
    let i = 0;
    if (how === 'offer') i = await offerThenConfirm(w, tag);
    else { q(w, btn(on, cls)).click(); await until(w, '__reqs.length === 1', 'the press'); }
    answer(w, i, ans);
    await drain(w);
    ok(times(w, says) === 1, tag + ': painted once');
    await refresh(w, FIX.full);
    ok(times(w, says) === 0, tag + ': a refresh no press owed carries nothing');
    ok(count(w, '[data-gate-zone-slot] > *') === 0 && count(w, '.gate-status-slot > *') === 0, tag + ': every slot is empty');
    clean(w, tag);
  }
  // two presses in a row: each owed refresh shows its own answer, not the earlier one
  {
    const w = await page();
    const tag = 'N two presses';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-activate', FIX.refuse, FIX.full);
    ok(times(w, 'already the active CZ gate') === 1, tag + ': the first answer after its owed refresh');
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-activate', FIX.warn, FIX.full);
    ok(times(w, 'could not be saved') === 1, tag + ': the second answer after its owed refresh');
    ok(times(w, 'already the active CZ gate') === 0, tag + ': the first answer is not carried again');
    clean(w, tag);
  }
  // a press answer painted in the zone, then a second press that owes a refresh: nothing of the first
  {
    const w = await page();
    const tag = 'N painted, then owed';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the first press');
    answer(w, 0, FIX.refuse);
    await drain(w);
    ok(times(w, 'already the active CZ gate') === 1, tag + ': the first answer is painted');
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 2', 'the second press');
    w.eval('__reqs[1].respond("", {}, 409)');          // answered without a paint: it owes a refresh
    await until(w, '__reqs.length === 3', 'the owed refresh');
    answer(w, 2, FIX.full);
    await drain(w);
    ok(times(w, 'already the active CZ gate') === 0, tag + ': the earlier painted answer is not carried');
    clean(w, tag);
  }
  // state notices over owed refreshes: never carried, counts equal the server's
  {
    const w = await page();
    const tag = 'N state, owed';
    for (let k = 1; k <= 3; k++) {
      await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-activate', FIX.noticed, FIX.noticed);
      for (const [kind, sel] of STATE) {
        ok(count(w, sel) === srv(FIX.noticed.body, sel), tag + ' ' + k + ': ' + kind + ' count as on the server (' + count(w, sel) + ')');
      }
    }
    clean(w, tag);
  }
  // the owed refresh renders the same answer again: exactly one, the server's
  {
    const w = await page();
    const tag = 'N fresh wins';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-activate', FIX.refuse, FIX.refuse);
    ok(times(w, 'already the active CZ gate') === 1, tag + ': exactly one');
    ok(q(w, '.gate-status[data-gate="cz_two"] .gate-status-slot').children.length === 0, tag + ': the carried one is dropped');
    clean(w, tag);
  }
  {
    const w = await page();
    const tag = 'N fresh wins, zone level';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-delete', FIX.gone, FIX.gone);
    ok(times(w, 'This gate is gone.') === 1, tag + ': exactly one');
    ok(count(w, '[data-gate-zone-slot] > *') === 0, tag + ': the carried one is dropped');
    clean(w, tag);
  }
  // a delete notice, then the undo brings the gate back: shown once, then gone, never on the row
  {
    const w = await page();
    const tag = 'N same name';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-delete', FIX.deleted, FIX.list);
    ok(!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the row is gone');
    ok(times(w, 'cz_sq deleted') === 1, tag + ': the delete notice is shown once');
    const j = w.eval('__reqs.length');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await until(w, '__reqs.length === ' + (j + 1), 'the undo refresh');
    answer(w, j, FIX.full);
    await drain(w);
    ok(!!q(w, '.gate-status[data-gate="cz_sq"]'), tag + ': the gate is back');
    ok(times(w, 'cz_sq deleted') === 0, tag + ': the delete notice is gone');
    ok(!q(w, '.gate-status[data-gate="cz_sq"]').textContent.includes('deleted'), tag + ': and never on the new row');
    clean(w, tag);
  }
  // the owed refresh fails (error answer, abort): the debt ends with it, the answer stays on the
  // unreplaced zone, and the next refresh nobody owed shows the server's render alone
  for (const fail of ['500', 'abort']) {
    const w = await page();
    const tag = 'N owed refresh fails (' + fail + ')';
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'the press');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, 0, FIX.refuse);
    await until(w, '__reqs.length === 2', 'the owed refresh');
    if (fail === 'abort') w.eval('__reqs[1].abort()');
    else w.eval('__reqs[1].respond("", {}, 500)');
    await drain(w);
    ok(times(w, 'already the active CZ gate') === 1, tag + ': the answer is still readable on the old zone');
    ok(q(w, btn('cz_sq', 'gate-status-activate')) && !q(w, btn('cz_sq', 'gate-status-activate')).disabled, tag + ': buttons are enabled again');
    const j = w.eval('__reqs.length');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await until(w, '__reqs.length === ' + (j + 1), 'the un-owed refresh');
    answer(w, j, FIX.full);
    await drain(w);
    ok(times(w, 'already the active CZ gate') === 0, tag + ': a later refresh nobody owed carries nothing');
    ok(count(w, '[data-gate-zone-slot] > *') === 0 && count(w, '.gate-status-slot > *') === 0, tag + ': every slot is empty');
    if (fail === '500') clean(w, tag);
  }
  // a delete notice in the zone-level slot, then a row-slot press with an owed refresh:
  // the notice was set aside at that press's start and is not carried again
  {
    const w = await page();
    const tag = 'N zone notice, then a row press';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-delete', FIX.deleted, FIX.full);
    ok(times(w, 'cz_sq deleted') === 1, tag + ': the delete notice is shown once');
    q(w, btn('cz_two', 'gate-status-delete')).click();
    await until(w, '__reqs.length === 3', 'the Delete press');
    answer(w, 2, FIX.offer);
    await until(w, '!!document.querySelector(".gate-delete-confirm")', 'the delete offer');
    await drain(w);
    q(w, '.gate-delete-confirm').click();
    await until(w, '__reqs.length === 4', 'the confirm press');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, 3, FIX.slotfail);
    await until(w, '__reqs.length === 5', 'the owed refresh');
    answer(w, 4, FIX.full);
    await drain(w);
    ok(times(w, 'is the active CZ gate of pair') === 1, tag + ': the slot answer is shown once');
    ok(times(w, 'cz_sq deleted') === 0, tag + ': the old delete notice is shown 0 times');
    clean(w, tag);
  }
  // "is now active", then a refresh where the gate offers Make active again: gone
  {
    const w = await page();
    const tag = 'N now active';
    await pressHeld(w, tag, 0, 'cz_sq', 'gate-status-activate', FIX.warn, FIX.full);
    ok(times(w, 'could not be saved') === 1, tag + ': the answer stands after its owed refresh');
    await refresh(w, FIX.full);
    ok(!!q(w, btn('cz_sq', 'gate-status-activate')), tag + ': the row offers Make active');
    ok(times(w, 'is now active') === 0 && times(w, 'could not be saved') === 0, tag + ': the answer is gone');
    clean(w, tag);
  }
  // each owed refresh debt has its own number: two presses on the same pair put two owed refreshes
  // out at once; whichever answers first, the second answer survives until ITS refresh ends
  for (const order of ['older first', 'newer first']) {
    const w = await page();
    const tag = 'N two owed debts, ' + order;
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 1', 'press A');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, 0, FIX.refuse);
    await until(w, '__reqs.length === 2', 'owed refresh of press A');
    q(w, btn('cz_sq', 'gate-status-activate')).click();
    await until(w, '__reqs.length === 3', 'press B');
    w.eval('document.dispatchEvent(new CustomEvent("cellsReverted"))');
    await drain(w);
    answer(w, 2, FIX.warn);
    await until(w, '__reqs.length === 4', 'owed refresh of press B');
    const first = order === 'older first' ? 1 : 3, second = order === 'older first' ? 3 : 1;
    answer(w, first, FIX.full);
    await drain(w);
    ok(times(w, 'could not be saved') === 1, tag + ': the second answer stands after the first refresh to end');
    ok(times(w, 'already the active CZ gate') === 0, tag + ': the first answer is not carried by the other debt');
    answer(w, second, FIX.full);
    await drain(w);
    ok(times(w, 'could not be saved') === 1, tag + ': the second answer stands after its own owed refresh');
    ok(times(w, 'already the active CZ gate') === 0, tag + ': the first answer is not shown again');
    await refresh(w, FIX.full);
    ok(times(w, 'could not be saved') === 0, tag + ': a later refresh nobody owed carries nothing');
    clean(w, tag);
  }
  console.log('ALL OK (' + n + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('FAIL - ' + (e && e.stack || e)); process.exit(1); });
