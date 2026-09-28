/* A virtual clock for a jsdom window (w8 chipplace).
 *
 * The Chip Status selfchecks used to wait on the wall clock -- `await
 * sleep(450)` for "250 ms debounce + fetch + a 20 ms swap + frames" -- and
 * failed whenever the machine was busy enough that the chain had not finished
 * in 450 real ms (chip_status_resume_selfcheck: 1 run in 11 on main, 4 in 12
 * with a dozen node processes beside it; always the same assertion, the
 * resumed offset read before the frame that puts it back had run).
 *
 * Here time only moves when the test says so. Every timer the page code can
 * reach -- setTimeout / setInterval / requestAnimationFrame and Date -- is
 * replaced on the window, and `advance(ms)` runs what falls due in order
 * (due time, then creation order), draining the microtask queue after each
 * one, so a promise chain started by a timer finishes before the next timer
 * fires. No wall-clock wait anywhere: the same test gives the same trace on
 * an idle machine and on a loaded one.
 *
 *   const clock = installClock(win);   // BEFORE the page scripts run
 *   await clock.advance(450);
 *
 * rAF is a 16 ms timer. The microtask drain is setImmediate, a turn of Node's
 * event loop (not a timed wait): every promise job queued so far runs first.
 */
'use strict';

function installClock(win, startMs) {
  let now = typeof startMs === 'number' ? startMs : 1.7e12;
  let seq = 0;
  const timers = new Map();   // id -> { at, fn, args, every, order }
  const errors = [];          // what a timer callback threw (a browser reports it and goes on)
  const hooks = [];           // run before every task (a harness's per-task cache drop)

  function add(fn, ms, args, every) {
    const id = ++seq;
    const d = Math.max(0, Number(ms) || 0);
    timers.set(id, { at: now + (every ? Math.max(1, d) : d), fn: fn, args: args,
                     every: every ? Math.max(1, d) : 0, order: id });
    return id;
  }
  win.setTimeout = function (fn, ms) { return add(fn, ms, Array.prototype.slice.call(arguments, 2), false); };
  win.setInterval = function (fn, ms) { return add(fn, ms, Array.prototype.slice.call(arguments, 2), true); };
  win.clearTimeout = win.clearInterval = function (id) { timers.delete(id); };
  win.requestAnimationFrame = function (fn) { return add(function () { fn(now); }, 16, [], false); };
  win.cancelAnimationFrame = function (id) { timers.delete(id); };

  const RealDate = win.Date;
  function VDate() {
    const a = Array.prototype.slice.call(arguments);
    if (!(this instanceof VDate)) return new RealDate(now).toString();
    return a.length ? new (Function.prototype.bind.apply(RealDate, [null].concat(a)))() : new RealDate(now);
  }
  VDate.prototype = RealDate.prototype;
  VDate.now = function () { return now; };
  VDate.parse = RealDate.parse;
  VDate.UTC = RealDate.UTC;
  win.Date = VDate;

  function turn() { return new Promise(function (r) { setImmediate(r); }); }
  async function drain() { for (let i = 0; i < 3; i++) await turn(); }

  function nextDue(end) {
    let best = null, bestId = null;
    for (const [id, t] of timers) {
      if (t.at > end) continue;
      if (!best || t.at < best.at || (t.at === best.at && t.order < best.order)) { best = t; bestId = id; }
    }
    return best ? { id: bestId, t: best } : null;
  }
  // run everything due within `ms` virtual milliseconds
  async function advance(ms) {
    const end = now + Math.max(0, ms || 0);
    await drain();
    for (let guard = 0; guard < 200000; guard++) {
      const n = nextDue(end);
      if (!n) break;
      now = n.t.at;
      if (n.t.every) { n.t.at = now + n.t.every; n.t.order = ++seq; } else timers.delete(n.id);
      for (let h = 0; h < hooks.length; h++) hooks[h]();
      try { n.t.fn.apply(win, n.t.args); } catch (e) { errors.push(String(e && e.stack || e)); }
      await drain();
    }
    now = end;
    await drain();
  }
  return {
    advance: advance,
    drain: drain,
    now: function () { return now; },
    pending: function () { return timers.size; },
    errors: function () { return errors.slice(); },
    beforeTask: function (fn) { hooks.push(fn); },
  };
}

module.exports = { installClock: installClock };
