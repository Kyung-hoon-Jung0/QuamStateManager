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
  w.doStateSync = function (mode, forced, ackUnseen, expectChip, opts) {
    state.sync.push({ mode: mode, forced: forced, ackUnseen: ackUnseen, chip: expectChip, automatic: !!(opts && opts.automatic) });
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
          state.sync[0] && !state.sync[0].forced && !state.sync[0].ackUnseen && state.sync[0].automatic,
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

  // A latch timeout is reported to the server for classification.
  {
    const { w, state, clock } = world();
    const reports = [];
    w.fetch = function (url, opts) {
      reports.push({url, opts});
      return Promise.resolve({json: () => Promise.resolve({merge_failure: {
        class: 'transient', reason: 'still in flight', tries: 1, delay: 2, chip: 'CHIP-G'
      }})});
    };
    w._applyInFlight = true;
    fire(w, 'autoSyncMerge', {chip: 'CHIP-G'});
    await clock.advance(2100);
    check('C3 latch timeout asks the server to classify', reports.length === 1);
    check('C4 latch timeout opens no question', state.toasts.length === 0);
    w._applyInFlight = false;
    await clock.advance(2000);
    check('C5 server-classified latch failure retries', state.sync.length === 1);
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

  /* ── F2 (final walk): an applied value an outside save removed ────── */
  {
    const { w, state, clock } = world();
    fire(w, 'autoSyncPulled', { replaced: false, count: 0,
      lost: [{ path: 'extras.chip_name', applied: 'chip-a', now: null, removed: true }] });
    await clock.advance(20);
    check('L1 a pull that removed an applied value says so', state.toasts.length === 1,
          JSON.stringify(state.toasts));
    const m = (state.toasts[0] || {}).m || '';
    check('L2 …naming the value', /extras\.chip_name/.test(m) && /removed 1 value you had applied/.test(m), m);
    check('L3 …at warning level', (state.toasts[0] || {}).l === 'warning', JSON.stringify(state.toasts[0]));
    fire(w, 'autoSyncPulled', { replaced: false, count: 0, lost: [] });
    await clock.advance(20);
    check('L4 nothing lost, nothing said', state.toasts.length === 1, JSON.stringify(state.toasts));
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

  {
    const { w, state, clock } = world();
    w.confirm = () => { throw new Error('automatic restoration must never confirm'); };
    w.prompt = () => { throw new Error('automatic restoration must never prompt'); };
    fire(w, 'autoSyncRestored', { count: 1 });
    await clock.advance(20);
    check('I1 restoration sends exactly one non-blocking notice', state.toasts.length === 1);
    const m = (state.toasts[0] || {}).m || '';
    check('I2 notice states the reverted count and automatic restoration',
          /1 value you applied/.test(m) && /Auto-Sync restored it/.test(m), m);
  }

  {
    // the merge-pull re-staged the value (a typed cell or a pending edit was
    // present): the notice says "pending changes" and never claims a write
    const { w, state, clock } = world();
    fire(w, 'autoSyncRestored', { count: 2, staged: true });
    await clock.advance(20);
    check('I3 a re-staged restoration is one notice', state.toasts.length === 1);
    const m = (state.toasts[0] || {}).m || '';
    check('I4 a re-staged restoration names the pending changes, not a write',
          /2 values you applied/.test(m) && /back in your pending changes/.test(m)
          && !/restored them/.test(m), m);
  }

  // Failure-kind backoff is executed against the shipped scheduler.
  {
    const { w, state, clock } = world();
    w.document.body.insertAdjacentHTML('beforeend', '<button class="auto-apply-on"></button>');
    w._applyInFlight = false;
    const delays = [2, 5, 15, 30, 60, 60];
    for (let i = 0; i < delays.length; i++) {
      fire(w, 'autoSyncRetry', {class: 'transient', reason: 'synthetic lock',
        tries: i + 1, delay: delays[i], chip: 'CHIP-R', mode: 'apply'});
      fire(w, 'autoSyncMerge', {chip: 'CHIP-R'});
      await clock.advance(delays[i] * 1000 - 1);
      check('R' + i + ' no early retry or competing merge press', state.sync.length === i);
      await clock.advance(1);
      check('R' + i + ' retry at server deadline', state.sync.length === i + 1);
      check('R' + i + ' exactly one retrying indicator',
        w.document.querySelectorAll('[data-auto-retry]').length === 1 &&
        w.document.querySelector('[data-auto-retry]').textContent === 'retrying (' + (i + 1) + ')');
      check('R' + i + ' reason tooltip', w.document.querySelector('.auto-apply-on').title === 'synthetic lock');
    }
    fire(w, 'autoSyncRetry', {class: 'unknown', tries: 7, delay: 60, chip: 'CHIP-R'});
    fire(w, 'autoSyncLanded', {});
    await clock.advance(60000);
    check('R landed cancels queued retry', state.sync.length === 6);
    fire(w, 'autoSyncRetry', {class: 'permanent', reason: 'staged snapshot', tries: 1});
    await clock.advance(60000);
    check('R permanent never re-presses', state.sync.length === 6);
  }

  {
    const { w, state, clock } = world();
    w._applyInFlight = false;
    const pulls = [];
    w._autoSyncPullRequest = function(chip) { pulls.push(chip); return Promise.resolve(); };
    fire(w, 'autoSyncRetry', {class: 'transient', reason: 'read locked', tries: 1,
                            delay: 2, chip: 'CHIP-P', mode: 'pull'});
    await clock.advance(2000);
    check('P retry of a pull stays a pull', pulls.length === 1 && pulls[0] === 'CHIP-P');
    check('P pull-only retry never presses a write', state.sync.length === 0);
    fire(w, 'autoSyncRetry', {class: 'transient', tries: 2, delay: 5, chip: 'CHIP-P'});
    fire(w, 'autoApplyArmed', {});
    await clock.advance(5000);
    check('P rearming cancels the previous session timer', state.sync.length === 0);
  }

  {
    const { w, state, clock } = world();
    w._applyInFlight = false;
    const pill = w.document.createElement('button');
    pill.className = 'auto-apply-on';
    const hint = w.document.createElement('span');
    hint.setAttribute('data-auto-retry', '1');
    hint.setAttribute('data-retry', JSON.stringify({class: 'transient', tries: 3,
      reason: 'synthetic lock', chip: 'CHIP-H', mode: 'reapply', delay: 15}));
    pill.appendChild(hint); w.document.body.appendChild(pill);
    fire(w, 'DOMContentLoaded', {});
    await clock.advance(14999);
    check('H reload retains the retry deadline', state.sync.length === 0);
    await clock.advance(1);
    check('H reload resumes the captured merge mode and chip', state.sync.length === 1
      && state.sync[0].mode === 'reapply' && state.sync[0].chip === 'CHIP-H');
  }

  {
    const { w, state, clock } = world();
    w._applyInFlight = true;
    fire(w, 'autoSyncMerge', {chip: 'CHIP-W'});
    await clock.advance(100);
    fire(w, 'autoSyncRetry', {class: 'transient', tries: 2, delay: 5, chip: 'CHIP-W'});
    w._applyInFlight = false;
    await clock.advance(4999);
    check('W a new failure cancels the older latch waiter', state.sync.length === 0);
    await clock.advance(1);
    check('W only the current retry presses', state.sync.length === 1);
  }
  {
    const { w } = world();
    const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
    const start = app.indexOf('window._autoSyncPullRequest = function');
    w.eval(app.slice(start, app.indexOf('window.doStateSync = function', start)));
    w.document.body.insertAdjacentHTML('beforeend', '<div class="bulk-cell dirty" data-dot-path="qubits.q1.T1"></div>');
    const requests = [];
    w.htmx.ajax = function(method, url) { requests.push({method, url}); };
    w._autoSyncPullRequest('CHIP-D');
    const url = requests[0] && requests[0].url || '';
    check('D pull retry reports the current typed cells', /dom_dirty=1/.test(url) && /dom_path=qubits.q1.T1/.test(url));
    check('D pull retry carries captured authorization and chip', /auto_sync=1/.test(url) && /expect_chip=CHIP-D/.test(url));
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
