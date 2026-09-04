// docs/167 — the new-run poller, against the REAL app.js under jsdom.
//
// This exists for ONE defect a design review found: the count could never
// accumulate. The poller had a single `_lastSeenStamp` doing two jobs — the
// DETECTION baseline (which must advance on every detection, or "strictly
// newer" stops working) and the ACKNOWLEDGED baseline (which must NOT, or the
// server's "how many since" answer resets to zero on every poll and the chip
// can only ever read "1 new"). That is the requirement the whole feature
// exists for, so it is pinned by driving the real poller against a fake
// server rather than by reading the source.
//
// Also pinned: the card no longer fires per run (the user's actual complaint).
//
// Run: node tests/newrun_poll_selfcheck.cjs   (needs jsdom)
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
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const BADGE_JS = fs.readFileSync(path.join(STATIC, 'sync-badge.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const TRAY = '<div id="pending-tray" class="pending-tray" data-change-count="0">'
  + '<button type="button" class="state-status-badge state-status-synced">'
  + '<span class="state-status-dot">&#9679;</span>Synced</button></div>';
// The poller's arming gate is `#new-run-popup`; the element stays, because the
// card is still what the chip OPENS.
const HTML = TRAY
  + '<div id="new-run-popup" class="new-run-popup" style="display:none">'
  + '<div class="new-run-popup-card"><div id="new-run-popup-id"></div>'
  + '<div id="new-run-popup-exp"></div><div id="new-run-popup-qubits"></div>'
  + '<div id="new-run-popup-time"></div></div></div>'
  + '<div id="table-pane"></div><div id="inspector-pane"></div>';

function mkStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null),
           setItem: (k, v) => m.set(k, String(v)),
           removeItem: (k) => m.delete(k), clear: () => m.clear() };
}

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + HTML + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;                       // docs/125 standing rule
  // Node 24 makes `globalThis.navigator` a getter-only accessor, so it is
  // defined rather than assigned (the harness must not die on the bridge).
  try { Object.defineProperty(global, 'navigator', { value: win.navigator, configurable: true }); } catch (e) {}
  try { Object.defineProperty(global, 'location', { value: win.location, configurable: true }); } catch (e) {}
  // jsdom's own storages are getter-only on the window, so the harness
  // REDEFINES rather than assigns; app.js reads them bare in places.
  ['localStorage', 'sessionStorage'].forEach(function (k) {
    const st = mkStorage();
    try { Object.defineProperty(win, k, { value: st, configurable: true }); } catch (e) {}
    try { Object.defineProperty(global, k, { value: st, configurable: true }); } catch (e) {}
  });
  global.requestAnimationFrame = win.requestAnimationFrame = (f) => setTimeout(f, 0);
  global.MutationObserver = win.MutationObserver;
  global.IntersectionObserver = win.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
  global.ResizeObserver = win.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
  win.Element.prototype.getClientRects = function () { return [{}]; };
  win.htmx = { ajax() { return Promise.resolve(); }, trigger() {}, process() {},
               on() {}, off() {}, config: {} };

  win.urls = [];
  win.answer = { uid: null };
  // Installed INSIDE the realm: a Node-realm `window.fetch =` never reaches the
  // realm's bare `fetch` (the docs/144 harness rule).
  win.__nextAnswer = function () { return win.answer; };
  win.__recordUrl = function (u) { win.urls.push(String(u)); };
  win.eval("window.fetch = function (u) {"
    + " window.__recordUrl(u);"
    + " return Promise.resolve({ ok: true, status: 200,"
    + "   json: function () { return Promise.resolve(window.__nextAnswer()); } });"
    + "};");

  new win.Function(BADGE_JS).call(win);
  new win.Function(APP_JS).call(win);
  return win;
}

const chip = (win) => win.document.querySelector('.state-status-notice');
// _showNewRunPopup shows the card by CLEARING display (back to the
// stylesheet's own value), not by setting a value — so "shown" is "not none".
const cardShown = (win) => {
  const el = win.document.getElementById('new-run-popup');
  return !!el && el.style.display !== 'none';
};
const settle = () => new Promise((r) => setTimeout(r, 0));

(async function () {
  // ─────────────────────────────────────────────────────────────────────────
  // 1. The first poll is a baseline: runs that already existed are not "new"
  // ─────────────────────────────────────────────────────────────────────────
  {
    const win = world();
    ok(!!win.__newRunPoll, 'the poller exposes its seam');
    win.__newRunPoll.reset();
    win.answer = { uid: 'f:10', run_id: 10, date: '2026-01-01', time: '10:00:00' };
    win.__newRunPoll.poll(); await settle(); await settle();

    ok(chip(win) === null, 'the first poll announces nothing');
    ok(!cardShown(win), 'and pops no card');
    const st = win.__newRunPoll.stamps();
    ok(st.seen === '2026-01-01 10:00:00' && st.ack === st.seen,
       'both baselines are seeded from it (' + JSON.stringify(st) + ')');
    ok(win.urls[0] === '/datasets/poll',
       'the first request carries no since — there is nothing to be since of');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 2. THE DEFECT: the acknowledged baseline must not move on detection
  // ─────────────────────────────────────────────────────────────────────────
  {
    const win = world();
    win.__newRunPoll.reset();
    win.answer = { uid: 'f:10', run_id: 10, date: '2026-01-01', time: '10:00:00' };
    win.__newRunPoll.poll(); await settle(); await settle();

    // three separate runs land, one per poll, each carrying the server's
    // running count from the stamp the user last acknowledged
    for (let i = 1; i <= 3; i++) {
      win.answer = { uid: 'f:1' + i, run_id: 10 + i, date: '2026-01-01',
                     time: '1' + i + ':00:00', new_count: i };
      win.__newRunPoll.poll(); await settle(); await settle();
    }

    const st = win.__newRunPoll.stamps();
    ok(st.seen === '2026-01-01 13:00:00',
       'the DETECTION baseline followed the newest run (' + st.seen + ')');
    ok(st.ack === '2026-01-01 10:00:00',
       'the ACKNOWLEDGED baseline did NOT move — this is the whole defect '
       + '(got ' + st.ack + ')');

    const last = win.urls[win.urls.length - 1];
    ok(/since_date=2026-01-01/.test(last) && /since_time=10%3A00%3A00/.test(last),
       'so every poll asks "how many since the run they last looked at" ('
       + last + ')');
    ok(chip(win) && /3 new/.test(chip(win).textContent),
       'and the chip accumulates to 3, not back to 1 (got "'
       + (chip(win) && chip(win).textContent) + '")');
    ok(win.document.querySelectorAll('.state-status-notice').length === 1,
       'three runs are ONE chip');
    ok(!cardShown(win), 'and no card was pushed at any point');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 3. Acknowledging moves the stamp forward and opens the card ON DEMAND
  // ─────────────────────────────────────────────────────────────────────────
  {
    const win = world();
    win.__newRunPoll.reset();
    win.answer = { uid: 'f:10', run_id: 10, date: '2026-01-01', time: '10:00:00' };
    win.__newRunPoll.poll(); await settle(); await settle();
    win.answer = { uid: 'f:11', run_id: 11, date: '2026-01-01', time: '11:00:00', new_count: 1 };
    win.__newRunPoll.poll(); await settle(); await settle();
    ok(!!chip(win), 'precondition: the chip is up');

    chip(win).dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(chip(win) === null, 'clicking clears it');
    ok(win.__newRunPoll.stamps().ack === '2026-01-01 11:00:00',
       'the acknowledged baseline catches up to what was detected');
    ok(cardShown(win), 'and the card opens — the same information, pulled');
    ok(win.document.getElementById('new-run-popup-id').textContent.indexOf('11') >= 0,
       'showing the run that was announced');

    // the next poll counts from the new stamp
    win.urls.length = 0;
    win.answer = { uid: 'f:12', run_id: 12, date: '2026-01-01', time: '12:00:00', new_count: 1 };
    win.__newRunPoll.poll(); await settle(); await settle();
    ok(/since_time=11%3A00%3A00/.test(win.urls[0]),
       'the next poll asks from there (' + win.urls[0] + ')');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 4. A folder flip that surfaces an OLDER run announces nothing
  // ─────────────────────────────────────────────────────────────────────────
  {
    const win = world();
    win.__newRunPoll.reset();
    win.answer = { uid: 'a:10', run_id: 10, date: '2026-01-02', time: '10:00:00' };
    win.__newRunPoll.poll(); await settle(); await settle();
    win.answer = { uid: 'b:99', run_id: 99, date: '2026-01-01', time: '09:00:00', new_count: 0 };
    win.__newRunPoll.poll(); await settle(); await settle();
    ok(chip(win) === null,
       'a different folder becoming active is not a new experiment');
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 5. No count from the server is a chip with no number, never a made-up one
  // ─────────────────────────────────────────────────────────────────────────
  {
    const win = world();
    win.__newRunPoll.reset();
    win.answer = { uid: 'f:10', run_id: 10, date: '2026-01-01', time: '10:00:00' };
    win.__newRunPoll.poll(); await settle(); await settle();
    win.answer = { uid: 'f:11', run_id: 11, date: '2026-01-01', time: '11:00:00' };  // no new_count
    win.__newRunPoll.poll(); await settle(); await settle();
    ok(chip(win) && chip(win).textContent === 'new runs',
       'the chip says "new runs" (got "' + (chip(win) && chip(win).textContent) + '")');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ' assertions)');
  // app.js arms several self-rescheduling polls (new runs at 60 s, drift at
  // 5 s, …). They keep node's event loop alive forever, so the harness leaves
  // deliberately rather than hanging with every check green.
  process.exit(0);
})();
