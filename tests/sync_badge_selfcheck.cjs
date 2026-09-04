// docs/167 — the sync pill carries the notifications, as a STATE.
//
// The user's directive was mostly a request to DELETE something: a card was
// already popping once per detected run, on every page, with a 7-second
// auto-dismiss. What is pinned here is that a chip replaced it and that the
// chip cannot lie:
//   - nothing pending renders exactly the markup that shipped before (the chip
//     is lazily CREATED, never a hidden element)
//   - a hundred runs are one chip reading a count, not a hundred anythings
//   - the chip survives the tray's OOB swap, on BOTH swap paths
//   - the chip is a SIBLING of the pill, so its click is its own
//   - `run done` fires on a terminal status and never on `paused`
//
// Run: node tests/sync_badge_selfcheck.cjs   (needs jsdom)
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
const BADGE_JS = fs.readFileSync(path.join(STATIC, 'sync-badge.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const TRAY = '<div id="pending-tray" class="pending-tray" data-change-count="0">'
  + '<button type="button" class="state-status-badge state-status-synced" onclick="openReview()">'
  + '<span class="state-status-dot">&#9679;</span>Synced</button></div>';

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + TRAY + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  new win.Function(BADGE_JS).call(win);
  return win;
}
const chip = (win) => win.document.querySelector('.state-status-notice');

// ───────────────────────────────────────────────────────────────────────────
// 1. Nothing pending is byte-identical to before
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  ok(!!win.SyncBadge, 'the module installs itself');
  ok(chip(win) === null, 'with nothing pending, no chip exists at all');
  ok(win.document.getElementById('pending-tray').children.length === 1,
     'the tray holds exactly the pill it shipped with');
  win.SyncBadge.render();
  ok(chip(win) === null, 'and rendering again creates nothing');
}

// ───────────────────────────────────────────────────────────────────────────
// 2. A hundred runs are ONE chip carrying a count
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  win.SyncBadge.note('new', { count: 1 });
  ok(chip(win) && /1 new/.test(chip(win).textContent), 'one run reads "1 new"');
  for (let i = 2; i <= 100; i++) win.SyncBadge.note('new', { count: i });
  ok(win.document.querySelectorAll('.state-status-notice').length === 1,
     'a hundred announcements are ONE chip, not a hundred');
  ok(/100 new/.test(chip(win).textContent),
     'reading the count, not the last event (got "' + chip(win).textContent + '")');

  // an unknown count is honest rather than fabricated
  win.SyncBadge.clear();
  win.SyncBadge.note('new', { count: null });
  ok(chip(win).textContent === 'new runs',
     'with no count from the server the chip says "new runs", never "0 new" or "1 new"');
}

// ───────────────────────────────────────────────────────────────────────────
// 3. The chip is the pill's SIBLING, so its click is its own
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  win.SyncBadge.note('new', { count: 3 });
  const c = chip(win);
  ok(c.tagName === 'BUTTON', 'it is a real control');
  ok(c.closest('.state-status-badge') === null,
     'and NOT inside the status pill, whose click opens the Review tray');
  ok(c.previousElementSibling && c.previousElementSibling.classList.contains('state-status-badge'),
     'it sits immediately after the pill');

  let acked = null, opened = false;
  win.SyncBadge.onAck('new', function (p) { acked = p; opened = true; });
  c.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(opened && acked && acked.count === 3,
     'clicking hands the payload to the acknowledge handler');
  ok(chip(win) === null, 'and the chip goes away');
}

// ───────────────────────────────────────────────────────────────────────────
// 4. It survives the tray swap — on BOTH paths
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  win.SyncBadge.note('new', { count: 7 });
  ok(!!chip(win), 'precondition: the chip is up');

  // (a) the hand-rolled outerHTML replace in _swapPendingTray, which does not
  //     fire htmx:afterSwap — the reason _restoreTrayState exists at all
  win.document.getElementById('pending-tray').outerHTML = TRAY;
  ok(chip(win) === null, 'the swap destroyed it, as it destroys everything else');
  win.document.dispatchEvent(new win.CustomEvent('sm:tray-swapped'));
  ok(!!chip(win) && /7 new/.test(chip(win).textContent),
     'and the swap event brings it back with its count intact');

  // (b) htmx's own swap
  win.document.getElementById('pending-tray').outerHTML = TRAY;
  const ev = new win.CustomEvent('htmx:afterSwap', { bubbles: true });
  Object.defineProperty(ev, 'target', { value: win.document.getElementById('pending-tray') });
  win.document.body.dispatchEvent(ev);
  ok(!!chip(win) && /7 new/.test(chip(win).textContent),
     'the htmx path restores it too');
}

// ───────────────────────────────────────────────────────────────────────────
// 5. Precedence: a finished queue outranks new runs (one chip at a time)
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  win.SyncBadge.note('new', { count: 4 });
  win.SyncBadge.note('rundone', {});
  ok(win.document.querySelectorAll('.state-status-notice').length === 1,
     'still exactly one chip');
  ok(chip(win).textContent === 'run done',
     'the queue finishing outranks the runs it produced');
  win.SyncBadge.clear('rundone');
  ok(/4 new/.test(chip(win).textContent),
     'and clearing it falls back to the runs, which were never lost');
}

// ───────────────────────────────────────────────────────────────────────────
// 6. Only the two designed kinds can reach the screen
//    (asserted at RUNTIME: the two that were cut are explained in the module's
//    own comments, so a source search finds the words and proves nothing)
// ───────────────────────────────────────────────────────────────────────────
{
  const win = world();
  win.SyncBadge.note('whatever', { count: 9 });
  ok(chip(win) === null, 'a kind the module does not know renders nothing');

  // `live changed` was cut because /state/drift cannot keep the flag true on a
  // dirty context; `needs_human` because the engine flag has no clearing path.
  ['drift', 'live_changed', 'needs_human', 'error', 'autofit'].forEach(function (k) {
    win.SyncBadge.clear();
    win.SyncBadge.note(k, {});
    ok(chip(win) === null, 'a cut kind ("' + k + '") reaches the screen through nothing');
  });

  // and the two that DID ship still do
  win.SyncBadge.clear();
  win.SyncBadge.note('new', { count: 2 });
  ok(!!chip(win), 'while "new" does');
  win.SyncBadge.clear();
  win.SyncBadge.note('rundone', {});
  ok(!!chip(win), 'and so does "rundone"');
}

// ───────────────────────────────────────────────────────────────────────────
// 7. With no tray on the page (a chip-less landing) nothing throws
// ───────────────────────────────────────────────────────────────────────────
{
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  new win.Function(BADGE_JS).call(win);
  let threw = false;
  try { win.SyncBadge.note('new', { count: 2 }); } catch (e) { threw = true; }
  ok(!threw, 'announcing with no tray on the page does not throw');
  ok(win.document.querySelector('.state-status-notice') === null, 'and renders nothing');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed (' + asserts + ' assertions)');
