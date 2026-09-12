/* docs/187 review R8 — the client half, EXECUTED.
 *
 * The four pins that guarded it were greps over fixed character windows of
 * auto-apply.js. The review's verdict was blunt and right: "the four client
 * pins are greps, so the 'handler that never runs' they claim to guard cannot
 * fail", and `test_the_wait_is_bounded` passed with an unbounded wait. Adding a
 * comment to the source was enough to break two of them, which is the other
 * half of the same problem.
 *
 * So this dispatches the real events at the real file and watches what it does.
 *
 * Standing harness rule (docs/125): a Node-realm assignment does not reach the
 * jsdom realm's bare globals -- everything the code under test reads is
 * installed on `w` (the window), and the file is eval'd IN that realm.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                      'auto-apply.js');

const HTML = '<!doctype html><html><body>'
  + '<div id="status-bar"></div>'
  + '<li id="topbar-tray-slot">'
  + '<div id="pending-tray" data-change-count="1" data-working-dirty="0"'
  + ' data-auto-apply="1" data-seq="1"></div>'
  + '</li></body></html>';

let failures = 0, asserts = 0;
function check(name, cond, detail) {
  asserts++;
  if (!cond) { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

function world() {
  const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  const state = { sync: [], toasts: [] };
  w.htmx = { ajax: function () { return new Promise(function () {}); },
             trigger: function () {} };
  w.doStateSync = function (mode, forced, ackUnseen, expectChip) {
    state.sync.push({ mode: mode, forced: forced, ackUnseen: ackUnseen, chip: expectChip });
  };
  w.showToast = function (m, l) { state.toasts.push({ m: String(m), l: l }); };
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  return { w: w, state: state };
}

function fire(w, name, detail) {
  w.document.dispatchEvent(new w.CustomEvent(name, { detail: detail, bubbles: true }));
}

(async function () {
  /* ── A. the merge signal presses the door ───────────────────────────── */
  {
    const { w, state } = world();
    w._applyInFlight = false;
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-A' });
    await sleep(30);
    check('A1 a free latch presses the merge door at once', state.sync.length === 1,
          JSON.stringify(state.sync));
    check('A2 …through doStateSync in apply mode',
          state.sync[0] && state.sync[0].mode === 'apply', JSON.stringify(state.sync[0]));
    check('A3 …carrying the chip the SIGNAL named',
          state.sync[0] && state.sync[0].chip === 'CHIP-A', JSON.stringify(state.sync[0]));
    check('A4 …and it does not force, or acknowledge unseen edits, on its own',
          state.sync[0] && !state.sync[0].forced && !state.sync[0].ackUnseen,
          JSON.stringify(state.sync[0]));
  }

  /* ── B. it WAITS for the shared latch, then presses ─────────────────── */
  {
    const { w, state } = world();
    w._applyInFlight = true;                 // a flush is still settling
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-B' });
    await sleep(200);
    check('B1 a held latch defers the press', state.sync.length === 0,
          'pressed while another write was in flight: ' + JSON.stringify(state.sync));
    w._applyInFlight = false;                // the flush finished
    await sleep(200);
    check('B2 …and it presses once the latch clears', state.sync.length === 1,
          JSON.stringify(state.sync));
    check('B3 …still with the signal-named chip',
          state.sync[0] && state.sync[0].chip === 'CHIP-B', JSON.stringify(state.sync[0]));
  }

  /* ── C. the wait is BOUNDED (the pin that passed with an unbounded one) */
  {
    const { w, state } = world();
    w._applyInFlight = true;                 // never clears
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-C' });
    // 40 tries x 50ms ~= 2s. Well past it, the attempt must be abandoned --
    // and must NOT have pressed, since the latch is still held.
    await sleep(2600);
    check('C1 an unclearable latch never presses', state.sync.length === 0,
          JSON.stringify(state.sync));
    w._applyInFlight = false;
    await sleep(300);
    check('C2 …and it has GIVEN UP rather than pressing 2.5s late',
          state.sync.length === 0,
          'a timer was still alive after the bound: ' + JSON.stringify(state.sync));
  }

  /* ── D. a merge signal with no chip still works (nothing to pin) ────── */
  {
    const { w, state } = world();
    w._applyInFlight = false;
    fire(w, 'autoSyncMerge', {});
    await sleep(30);
    check('D1 a signal naming no chip still presses', state.sync.length === 1,
          JSON.stringify(state.sync));
    check('D2 …and sends no token rather than a bogus one',
          state.sync[0] && !state.sync[0].chip, JSON.stringify(state.sync[0]));
  }

  /* ── E. the replace-pull warning ────────────────────────────────────── */
  {
    const { w, state } = world();
    fire(w, 'autoSyncPulled', { replaced: false, count: 0 });
    await sleep(20);
    check('E1 a pull that replaced nothing says nothing', state.toasts.length === 0,
          JSON.stringify(state.toasts));

    fire(w, 'autoSyncPulled', { replaced: true, count: 3 });
    await sleep(20);
    check('E2 a replace warns', state.toasts.length === 1, JSON.stringify(state.toasts));
    const m = (state.toasts[0] || {}).m || '';
    check('E3 …naming how many were lost', /\b3 unapplied edits\b/.test(m), m);
    check('E4 …saying they are not recoverable', /not recoverable/.test(m), m);
    check('E5 …naming the setting that prevents it', /Untick/.test(m), m);
    check('E6 …and NOT offering State History, which cannot help',
          !/State History/.test(m), m);
    check('E7 …at warning level', (state.toasts[0] || {}).l === 'warning',
          JSON.stringify(state.toasts[0]));
  }

  /* ── F. singular/plural, because a count of 1 is the common case ────── */
  {
    const { w, state } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 1 });
    await sleep(20);
    const m = (state.toasts[0] || {}).m || '';
    check('F1 one edit reads as singular', /\b1 unapplied edit\b/.test(m)
          && !/1 unapplied edits/.test(m), m);
  }

  /* ── G. a replace with no count still says something true ──────────── */
  {
    const { w, state } = world();
    fire(w, 'autoSyncPulled', { replaced: true });
    await sleep(20);
    const m = (state.toasts[0] || {}).m || '';
    check('G1 a countless replace still warns', state.toasts.length === 1, m);
    check('G2 …without claiming a number it does not have',
          /your unapplied edits/.test(m) && !/\b0 unapplied/.test(m), m);
  }

  if (failures) {
    console.error(failures + ' FAILED of ' + asserts);
    process.exit(1);
  }
  console.log('all checks passed (' + asserts + ' assertions)');
})();
