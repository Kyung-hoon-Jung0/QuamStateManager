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
// docs/268: elapsed test time must not depend on how often a loaded CPU lets
// Node run. Both realms share this clock; callbacks run at their due time,
// including retries scheduled by earlier callbacks during the same advance.
// setImmediate yields to jsdom/Promise microtasks without waiting on a timer.
const settle = () => new Promise(resolve => setImmediate(resolve));
function controlledTime(w) {
  let now = 0, nextId = 1;
  const timers = new Map(), restore = [];
  const epoch = 1700000000000;
  function replace(object, key, value) {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    Object.defineProperty(object, key, { configurable: true, writable: true, value });
    restore.push(() => {
      if (descriptor) Object.defineProperty(object, key, descriptor);
      else delete object[key];
    });
  }
  for (const realm of [globalThis, w]) {
    replace(realm, 'setTimeout', (callback, delay, ...args) => {
      if (typeof callback !== 'function') throw new TypeError('Expected a timer callback');
      const id = nextId++;
      timers.set(id, { at: now + Math.max(1, Number(delay) || 0),
                       run: () => callback.apply(realm, args) });
      return id;
    });
    replace(realm, 'clearTimeout', id => timers.delete(id));
    replace(realm.Date, 'now', () => epoch + now);
    replace(realm.performance, 'now', () => now);
  }
  return {
    async advance(ms) {
      const target = now + ms;
      await settle();
      let callbacks = 0;
      while (true) {
        let next;
        for (const [id, timer] of timers) {
          if (timer.at <= target && (!next || timer.at < next.timer.at)) {
            next = { id, timer };
          }
        }
        if (!next) break;
        if (++callbacks > 10000) throw new Error('Timer callback loop did not yield');
        now = next.timer.at;
        timers.delete(next.id);
        next.timer.run();
        await settle();
      }
      now = target;
    },
    close() {
      timers.clear();
      for (const undo of restore.reverse()) undo();
    },
  };
}

let closeWorld;

function world() {
  if (closeWorld) closeWorld();
  const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  const clock = controlledTime(w);
  closeWorld = () => { clock.close(); dom.window.close(); };
  const state = { sync: [], toasts: [] };
  w.htmx = { ajax: function () { return new Promise(function () {}); },
             trigger: function () {} };
  w.doStateSync = function (mode, forced, ackUnseen, expectChip) {
    state.sync.push({ mode: mode, forced: forced, ackUnseen: ackUnseen, chip: expectChip });
  };
  w.showToast = function (m, l) { state.toasts.push({ m: String(m), l: l }); };
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  return { w: w, state: state, clock: clock };
}

function fire(w, name, detail) {
  w.document.dispatchEvent(new w.CustomEvent(name, { detail: detail, bubbles: true }));
}

(async function () {
  try {
  /* ── A. the merge signal presses the door ───────────────────────────── */
  {
    const { w, state, clock } = world();
    w._applyInFlight = false;
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-A' });
    await clock.advance(30);
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
    const { w, state, clock } = world();
    w._applyInFlight = true;                 // a flush is still settling
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-B' });
    await clock.advance(200);
    check('B1 a held latch defers the press', state.sync.length === 0,
          'pressed while another write was in flight: ' + JSON.stringify(state.sync));
    w._applyInFlight = false;                // the flush finished
    await clock.advance(200);
    check('B2 …and it presses once the latch clears', state.sync.length === 1,
          JSON.stringify(state.sync));
    check('B3 …still with the signal-named chip',
          state.sync[0] && state.sync[0].chip === 'CHIP-B', JSON.stringify(state.sync[0]));
  }

  /* ── C. the wait is BOUNDED (the pin that passed with an unbounded one) */
  {
    const { w, state, clock } = world();
    w._applyInFlight = true;                 // never clears
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-C' });
    // 40 tries x 50ms ~= 2s. Well past it, the attempt must be abandoned --
    // and must NOT have pressed, since the latch is still held.
    await clock.advance(2600);
    check('C1 an unclearable latch never presses', state.sync.length === 0,
          JSON.stringify(state.sync));
    w._applyInFlight = false;
    await clock.advance(300);
    check('C2 …and it has GIVEN UP rather than pressing 2.5s late',
          state.sync.length === 0,
          'a timer was still alive after the bound: ' + JSON.stringify(state.sync));
  }

  /* ── C2b. review R9: giving up must not be SILENT ─────────────────── */
  {
    // The bound is right -- a timer must not outlive its purpose -- but the
    // server has already spent one of its three tries, and the tray is still
    // saying Auto-Sync is resolving this. Leaving that on screen while nothing
    // happens is the class of defect docs/187 exists to fix.
    const { w, state, clock } = world();
    w._applyInFlight = true;                 // never clears
    fire(w, 'autoSyncMerge', { tries: 1, chip: 'CHIP-G' });
    await clock.advance(2600);
    check('C3 abandoning the merge tells the user', state.toasts.length === 1,
          JSON.stringify(state.toasts));
    const m = (state.toasts[0] || {}).m || '';
    check('C4 …saying the edits are safe', /safe/.test(m), m);
    check('C5 …and naming what finishes it', /Pull & apply/.test(m), m);
  }

  /* ── D. a merge signal with no chip still works (nothing to pin) ────── */
  {
    const { w, state, clock } = world();
    w._applyInFlight = false;
    fire(w, 'autoSyncMerge', {});
    await clock.advance(30);
    check('D1 a signal naming no chip still presses', state.sync.length === 1,
          JSON.stringify(state.sync));
    check('D2 …and sends no token rather than a bogus one',
          state.sync[0] && !state.sync[0].chip, JSON.stringify(state.sync[0]));
  }

  /* ── E. the replace-pull warning ────────────────────────────────────── */
  {
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: false, count: 0 });
    await clock.advance(20);
    check('E1 a pull that replaced nothing says nothing', state.toasts.length === 0,
          JSON.stringify(state.toasts));

    fire(w, 'autoSyncPulled', { replaced: true, count: 3 });
    await clock.advance(20);
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
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 1 });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('F1 one edit reads as singular', /\b1 unapplied edit\b/.test(m)
          && !/1 unapplied edits/.test(m), m);
  }

  /* ── G. a replace with no count still says something true ──────────── */
  {
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('G1 a countless replace still warns', state.toasts.length === 1, m);
    check('G2 …without claiming a number it does not have',
          /your unapplied work/.test(m) && !/\b0 unapplied/.test(m), m);
  }

  /* ── H. review R7: a number must cover EVERYTHING it describes ─────── */
  {
    // change-log edits AND a saved-but-unapplied working state: saying
    // "1 unapplied edit" would understate a loss, which is worse than not
    // counting at all.
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 1, saved: true });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('H1 a count beside other lost work is not presented as the whole loss',
          /other unapplied work/.test(m), m);
    check('H2 …and the count it does give is still there', /1 unapplied edit/.test(m), m);
  }
  {
    // typed grid cells only this browser can see: the server's count is 0 and
    // a bare "0" or a bogus "1" would both be lies.
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 0, dom: true });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('H3 dom-only work is described without inventing a number',
          /your unapplied work/.test(m) && !/\b0 unapplied/.test(m), m);
  }
  {
    // a re-apply stash is a third kind the first cut ignored entirely
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 2, stash: 3 });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('H4 a stash counts as other lost work too',
          /other unapplied work/.test(m), m);
  }
  {
    // the clean case must stay clean: a count that IS the whole loss reads
    // as exactly that, with no hedging tacked on.
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: true, count: 4 });
    await clock.advance(20);
    const m = (state.toasts[0] || {}).m || '';
    check('H5 a complete count is stated plainly',
          /4 unapplied edits/.test(m) && !/other unapplied work/.test(m), m);
  }

  if (failures) {
    console.error(failures + ' FAILED of ' + asserts);
    process.exitCode = 1;
    return;
  }
  console.log('all checks passed (' + asserts + ' assertions)');
  } finally {
    if (closeWorld) closeWorld();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
