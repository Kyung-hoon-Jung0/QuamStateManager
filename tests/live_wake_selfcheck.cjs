// docs/141 §4p — live-wake.js under jsdom: ONE long-poll in flight at a time;
// a `changed` answer wakes the consumers (sm:runs-changed + DatasetVirtual
// .pollNow) — except the very first answer, which only adopts the server's
// tick; an unchanged answer wakes nobody and goes straight back to waiting;
// a failure backs off exponentially; a hidden tab aborts the wait and stops;
// a visible tab resumes; the popup poll's in-flight guard runs a wake that
// arrived mid-flight exactly once more.
//
// Run: node tests/live_wake_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const LIVE_WAKE = fs.readFileSync(path.join(STATIC, 'live-wake.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win._log = { urls: [], events: [], pollNow: 0, aborted: 0 };
  win._answers = [];                 // queued {tick, changed} answers; a function = custom
  win._pending = [];                 // unresolved requests: {resolve, reject, signal}
  win.DatasetVirtual = { pollNow: function () { win._log.pollNow++; return true; } };
  win.document.addEventListener('sm:runs-changed', function (e) { win._log.events.push(e.detail && e.detail.tick); });
  win.fetch = function (url, opts) {
    win._log.urls.push(url);
    return new Promise(function (resolve, reject) {
      const req = { resolve: resolve, reject: reject };
      if (opts && opts.signal) opts.signal.addEventListener('abort', function () { win._log.aborted++; reject(new Error('aborted')); });
      const a = win._answers.shift();
      if (a === undefined) { win._pending.push(req); return; }          // held open (a real long-poll)
      if (a === 'fail') { reject(new Error('network')); return; }
      resolve({ ok: true, json: function () { return Promise.resolve(a); } });
    });
  };
  new win.Function(LIVE_WAKE).call(win);
  return win;
}
function answer(win, a) {
  const req = win._pending.shift();
  if (!req) return false;
  if (a === 'fail') req.reject(new Error('network')); else req.resolve({ ok: true, json: function () { return Promise.resolve(a); } });
  return true;
}

async function main() {
  let win = world();
  await tick(10);
  ok(win._log.urls.length === 1 && /\/datasets\/wait\?since=-1&timeout=25$/.test(win._log.urls[0]),
     'the page opens ONE handshake wait with since=-1 (' + win._log.urls[0] + ')');
  ok(win.LiveWake.state().inFlight === true, 'and it is in flight');
  // a visibility "visible" while a wait is open must not open a second one
  win.document.dispatchEvent(new win.Event('visibilitychange'));
  await tick(10);
  ok(win._log.urls.length === 1 && win._pending.length === 1, 'a visible event while in flight opens no second wait');
  // the handshake answer adopts the tick, wakes nobody (the page just polled)
  answer(win, { tick: 7, changed: false });
  await tick(20);
  ok(win.LiveWake.state().tick === 7 && win._log.events.length === 0 && win._log.pollNow === 0,
     'the handshake answer only adopts the server tick (7), no wake');
  ok(win._log.urls.length === 2 && /since=7&/.test(win._log.urls[1]), 'and goes straight back to waiting with since=7');
  // even a handshake that claims a change wakes nobody (the page has just polled)
  let w2 = world();
  await tick(10);
  answer(w2, { tick: 3, changed: true });
  await tick(20);
  ok(w2.LiveWake.state().tick === 3 && w2._log.events.length === 0, 'a handshake never wakes, whatever it claims');
  w2.LiveWake.stop();
  // an unchanged answer (the 25 s timeout) wakes nobody
  answer(win, { tick: 7, changed: false });
  await tick(20);
  ok(win._log.events.length === 0 && win._log.pollNow === 0 && win._log.urls.length === 3, 'an unchanged answer wakes nobody and re-waits');
  // a change wakes both consumers once
  answer(win, { tick: 8, changed: true });
  await tick(20);
  ok(win._log.events.join(',') === '8' && win._log.pollNow === 1, 'a change dispatches sm:runs-changed once and calls DatasetVirtual.pollNow once');
  ok(win.LiveWake.state().wakes === 1 && win._log.urls.length === 4 && /since=8&/.test(win._log.urls[3]), 'then waits again with the new tick');
  ok(win._pending.length === 1 && win.LiveWake.state().inFlight, 'never two requests in flight');

  // failure: backoff 1 s, 2 s, ...
  answer(win, 'fail');
  await tick(30);
  ok(win.LiveWake.state().failures === 1 && win._log.urls.length === 4, 'a failure does not retry immediately');
  await tick(1100);
  ok(win._log.urls.length === 5, 'it retries after ~1 s');
  answer(win, 'fail');
  await tick(1100);
  ok(win._log.urls.length === 5 && win.LiveWake.state().failures === 2, 'the second failure waits ~2 s (not yet retried at 1.1 s)');
  await tick(1100);
  ok(win._log.urls.length === 6, 'and retries at ~2 s');
  answer(win, { tick: 8, changed: false });
  await tick(20);
  ok(win.LiveWake.state().failures === 0 && win._log.urls.length === 7, 'a good answer resets the backoff');

  // hidden: abort the open wait, no new request; visible: resume
  Object.defineProperty(win.document, 'hidden', { value: true, configurable: true });
  win.document.dispatchEvent(new win.Event('visibilitychange'));
  await tick(20);
  ok(win._log.aborted === 1 && win._log.urls.length === 7 && !win.LiveWake.state().inFlight, 'hiding the tab aborts the wait and opens no new one');
  await tick(1200);
  ok(win._log.urls.length === 7 && win.LiveWake.state().failures === 0, 'an abort on hide is not a failure and nothing is retried while hidden');
  Object.defineProperty(win.document, 'hidden', { value: false, configurable: true });
  win.document.dispatchEvent(new win.Event('visibilitychange'));
  await tick(20);
  ok(win._log.urls.length === 8 && win.LiveWake.state().inFlight, 'showing the tab resumes the wait');
  win.LiveWake.stop();
  await tick(20);
  ok(win.LiveWake.state().stopped && win._log.aborted === 2, 'stop() aborts the open wait');
  await tick(1200);
  ok(win._log.urls.length === 8, 'and nothing restarts after stop()');

  // the popup poll's in-flight guard (app.js) — pinned on the real module
  const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
  const i = APP.indexOf('function pollForNewRuns() {');
  const body = APP.slice(i, i + 3000);
  ok(/if \(_inFlight\) \{ _wakeAgain = true; return; \}/.test(body) && /\.finally\(function\(\) \{\s*_inFlight = false;/.test(body),
     'the popup poll runs a wake that arrived mid-flight once more, never in parallel');
  ok(APP.indexOf('document.addEventListener("sm:runs-changed", function() {') > 0, 'the popup poll listens to sm:runs-changed');

  console.log(fails ? ('FAILED ' + fails) : 'live_wake_selfcheck: all ok');
  process.exit(fails ? 1 : 0);
}
main().catch(function (e) { console.error('ERR', e && e.stack || e); process.exit(1); });
