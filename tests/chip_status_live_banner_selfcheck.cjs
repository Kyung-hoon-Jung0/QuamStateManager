/* QA chipstatus-r2-03 — dismissing "Live chip state changed on disk" silences
 * only the change the user saw.
 *
 * Measured on the customer chip copy: rewrite state.json (q3 T1), the banner
 * appears in ~3.4 s; press ✕; rewrite it again with a DIFFERENT value and wait
 * 14 s -- nothing. Live stays diverged after a ✕, so `dismissed` never reset
 * (it reset only on changed === false) and every later external write was
 * silent for as long as the page stayed open.
 *
 * sync-ux 2026-09-25: the banner itself is gone (user decision: one status
 * control in the top bar). The pins below keep what this page still owes --
 * no banner, a poke to the control, and the live-marks read once per write
 * (the r2-03 "a newer write prompts again" rule now drives the marks).
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
  /* sync-ux 2026-09-25 (user decision 1): Chip Status' own "Live chip state
     changed on disk" banner is GONE -- the status control in the top bar says
     it on every page. Re-scoped from B1-B7 (banner show / ✕ / Review changes):
     what this page still owes is (R) no banner, (P) a poke to the control so it
     does not wait for its own 5 s poll, and (M) the live-marks read ONCE per
     write -- the B5 cost rule survives the banner. */
  let pokes = 0;
  win._pollDrift = function () { pokes++; };
  const marks = function () { return diffReads; };
  if (win.ChipStatus && !win.ChipStatus.liveDiff) {
    win.ChipStatus.liveDiff = { refresh: function () { diffReads++; } };
  }
  win.ChipStatus.liveDetection();              // polls at t=0, then every 3 s
  await flush();
  await advance(2100);
  ok(!shown(), 'R1: a live change inserts NO banner into the dashboard');
  ok(pokes === 1, 'P1: …it pokes the status control once (got ' + pokes + ')');
  const m1 = marks();
  ok(m1 >= 1, 'M1: …and refreshes the live marks');
  await advance(9000);                          // three polls, same write
  ok(pokes === 1 && marks() === m1,
    'M2: one refresh per write — polls of the same write re-read nothing (' + (marks() - m1) + ' extra)');

  live = { changed: true, state_mtime: 131.75, wiring_mtime: 50.25 };   // a newer write
  await advance(3000 + 2100);
  ok(pokes === 2 && marks() > m1, 'M3: a NEWER write pokes and refreshes again');
  ok(!shown(), 'R2: …still no banner');

  live = { changed: false, state_mtime: 131.75, wiring_mtime: 50.25 };
  await advance(3000);
  const m2 = marks();
  live = { changed: true, state_mtime: 170, wiring_mtime: 50.25 };
  await advance(3000 + 2100);
  ok(pokes === 3 && marks() > m2, 'M4: live back at the sync point, then a new change: refreshed again');

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_live_banner_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
