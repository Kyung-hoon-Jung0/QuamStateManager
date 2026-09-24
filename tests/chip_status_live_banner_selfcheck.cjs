/* QA chipstatus-r2-03 — dismissing "Live chip state changed on disk" silences
 * only the change the user saw.
 *
 * Measured on the customer chip copy: rewrite state.json (q3 T1), the banner
 * appears in ~3.4 s; press ✕; rewrite it again with a DIFFERENT value and wait
 * 14 s -- nothing. Live stays diverged after a ✕, so `dismissed` never reset
 * (it reset only on changed === false) and every later external write was
 * silent for as long as the page stayed open.
 *
 * Pins (the REAL chip-status.js under jsdom, on a fake clock):
 *  B1  a live change shows the banner after the debounce;
 *  B2  ✕, then the SAME write on every later poll: it stays hidden;
 *  B3  a NEWER write (the live mtime pair moved) shows it again;
 *  B4  ✕ pressed while a show is already scheduled: the ✕ wins;
 *  B5  one banner per write: polls that see the same write do not re-show it
 *      (each show re-reads live CONTENT for the marks — it used to run every 3 s);
 *  B6  "Review changes" dismisses the same way and opens the review;
 *  B7  live back at the sync point hides it, and the next change prompts.
 *
 * Run: node tests/chip_status_live_banner_selfcheck.cjs
 *      (driven by tests/test_chip_status_live_marks.py)
 */
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
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard"></div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=overview' });
const win = dom.window;

// A fake clock: the banner's 3 s poll and 2 s debounce run on it, so the
// sequence is exact and fast. chip-status.js calls these bare, which reaches
// the window's own properties.
let now = 0, nextId = 1, timers = [];
win.setTimeout = function (fn, ms) { const id = nextId++; timers.push({ id: id, at: now + (ms || 0), fn: fn, every: 0 }); return id; };
win.setInterval = function (fn, ms) { const id = nextId++; timers.push({ id: id, at: now + ms, fn: fn, every: ms }); return id; };
win.clearTimeout = win.clearInterval = function (id) { timers = timers.filter(function (t) { return t.id !== id; }); };
function flush() { return new Promise(function (r) { setImmediate(r); }); }
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    timers.sort(function (a, b) { return a.at - b.at; });
    const t = timers[0];
    if (!t || t.at > end) break;
    now = t.at;
    if (t.every) t.at += t.every; else timers.shift();
    t.fn();
    await flush(); await flush();
  }
  now = end;
  await flush();
}

win.UI_CONFIG = { topoLivePollInterval: 3 };
win.htmx = { process: function () {}, ajax: function () {} };
let reviewOpened = 0;
win.openReview = function () { reviewOpened++; };
let live = { changed: true, state_mtime: 100.5, wiring_mtime: 50.25 };
let diffReads = 0;
function reply(body) { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } }); }
win.fetch = function (url) {
  const p = String(url).split('?')[0];
  if (p === '/api/topology-mtime') return reply(JSON.parse(JSON.stringify(live)));
  if (p === '/state/live-diff') { diffReads++; return reply({ ok: true, total: 0, entries: [] }); }
  return new Promise(function () {});
};
new win.Function(read('chip-status.js')).call(win);

const doc = win.document;
function shown() {
  const b = doc.querySelector('.topo-change-banner');
  return !!(b && b.isConnected && b.style.display !== 'none');
}
function press(sel) { doc.querySelector(sel).dispatchEvent(new win.MouseEvent('click', { bubbles: true })); }

(async function () {
  win.ChipStatus.liveDetection();              // polls at t=0, then every 3 s
  await flush();
  await advance(2100);
  ok(shown(), 'B1: a live change shows the banner after the debounce');

  press('.topo-change-banner-dismiss');
  ok(!shown(), 'B2a: ✕ hides it');
  await advance(6500);                          // two more polls, same write
  ok(!shown(), 'B2b: …and the same write on later polls keeps it hidden');

  live = { changed: true, state_mtime: 131.75, wiring_mtime: 50.25 };   // a newer write
  await advance(3000 + 2100);
  ok(shown(), 'B3: a NEWER write shows the banner again');

  live = { changed: true, state_mtime: 140, wiring_mtime: 50.25 };
  // step to just past the next poll: it saw the newer write and scheduled a show
  const nextPoll = timers.filter(function (t) { return t.every; })[0].at;
  await advance(nextPoll - now + 100);
  ok(timers.some(function (t) { return !t.every; }), 'B4a: (a show is scheduled for the newer write)');
  press('.topo-change-banner-dismiss');
  await advance(2500);
  ok(!shown(), 'B4b: ✕ pressed while a show was pending wins over it');
  await advance(6000);
  ok(!shown(), 'B4c: …and later polls of that same write stay quiet');

  live = { changed: true, state_mtime: 155, wiring_mtime: 50.25 };
  await advance(3000 + 2100);
  ok(shown(), 'B5a: the next write prompts');
  const reads = diffReads;
  await advance(9000);                          // three polls, same write, banner up
  ok(shown() && diffReads === reads,
    'B5b: one banner per write — polls of the same write re-read live content ' + (diffReads - reads) + ' time(s)');

  press('.topo-change-banner-btn');
  ok(!shown() && reviewOpened === 1, 'B6a: "Review changes" hides it and opens the review');
  await advance(6000);
  ok(!shown(), 'B6b: …and dismisses that write like ✕ does');

  live = { changed: false, state_mtime: 155, wiring_mtime: 50.25 };
  await advance(3000);
  ok(!shown(), 'B7a: live back at the sync point: no banner');
  live = { changed: true, state_mtime: 170, wiring_mtime: 50.25 };
  await advance(3000 + 2100);
  ok(shown(), 'B7b: …and the next change prompts');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_live_banner_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
