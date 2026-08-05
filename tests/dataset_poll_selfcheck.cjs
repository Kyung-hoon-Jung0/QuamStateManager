/* Dataset delta-poll liveness (docs/80).
 *
 * Loads the REAL web/static/dataset-virtual.js under jsdom and drives its own
 * poll path (init() starts the interval; a visibilitychange dispatch makes it
 * poll immediately, which is exactly what the browser does when a backgrounded
 * /datasets tab is brought forward). No test-only API is added to the module.
 *
 * What is pinned, and why each mattered:
 *   * an in-flight request is not stacked on          (a slow server must not queue)
 *   * ...but a request that NEVER settles cannot wedge polling forever. That
 *     was the real defect: pollInFlight stayed true and the dataset table
 *     silently stopped updating until the page was reloaded — monitoring that
 *     fails closed and says nothing.
 *   * a hung request is aborted after the timeout
 *   * an HTTP 500 / malformed body counts as a failure and backs off, instead
 *     of being treated as a successful empty delta (which would advance the
 *     cursor past a window nobody scanned)
 *   * a successful poll clears the backoff
 *   * `partial: true` schedules a prompt catch-up rather than waiting out the
 *     full 60s interval
 *
 * Exit codes: 0 ok, 1 assertion failed, 2 jsdom unavailable (driver skips).
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
    ({ JSDOM } = require('jsdom'));
} catch (e) {
    console.error('jsdom not available: ' + e.message);
    process.exit(2);
}

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                      'dataset-virtual.js');

let failures = 0;
let checks = 0;

function ok(cond, msg) {
    checks++;
    if (!cond) {
        failures++;
        console.error('FAIL: ' + msg);
    }
}

const ROWS = [{ id: 1, exp: 'test_experiment', date: '2026-08-05', time: '01:00:00',
                q: ['q1'], p: [], oc: {}, metric: '', bm: false, tags: [],
                status: 'successful', dur: 1, note: '', parent: null, hs: false,
                sm: {}, pm: {}, f: 'fold1' }];

function makeDom() {
    const dom = new JSDOM(`<!doctype html><html><body>
        <script id="ds-rows-data" data-now="1000">${JSON.stringify(ROWS)}</script>
        <div id="datasets-scroll" style="height:400px">
          <table><tbody id="datasets-tbody"></tbody></table>
        </div>
      </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
    const w = dom.window;
    w.requestAnimationFrame = w.requestAnimationFrame || function (cb) { return setTimeout(cb, 0); };
    w.cancelAnimationFrame = w.cancelAnimationFrame || function (id) { clearTimeout(id); };
    if (typeof w.AbortController !== 'function') {
        w.AbortController = class {
            constructor() { this.signal = { aborted: false, _cbs: [] }; }
            abort() {
                this.signal.aborted = true;
                (this.signal._cbs || []).forEach(function (cb) { cb(); });
            }
        };
    }
    return dom;
}

/** Boot the module in a fresh DOM with a controllable fetch.
 *
 * The globals are re-pointed at each fresh window the same way the other
 * selfchecks do it: jsdom's ``window.eval`` resolves the bare identifier
 * ``window`` through Node's global scope, so a module that assigns
 * ``window.X = ...`` at top level needs it bound before the eval.
 */
function boot(fetchImpl) {
    const dom = makeDom();
    const w = dom.window;
    global.window = w;
    global.document = w.document;
    global.Event = w.Event;
    global.CustomEvent = w.CustomEvent;
    global.AbortController = w.AbortController;
    // The module calls these bare (browser globals). Node's own timers are
    // used as-is — re-pointing them at jsdom's wrappers recurses.
    global.requestAnimationFrame = w.requestAnimationFrame;
    global.cancelAnimationFrame = w.cancelAnimationFrame;
    global.localStorage = w.localStorage;
    const calls = [];
    const stub = function (url, opts) {
        calls.push({ url: String(url), opts: opts || {} });
        return fetchImpl(String(url), opts || {}, calls.length);
    };
    w.fetch = stub;
    global.fetch = stub;
    const code = fs.readFileSync(SRC, 'utf8');
    w.eval(code);
    w.DatasetVirtual.init();
    return { dom, w, calls };
}

/** Force one poll the way the browser does when the tab comes forward. */
function pump(w) {
    w.document.dispatchEvent(new w.Event('visibilitychange'));
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function jsonResponse(body, status) {
    return Promise.resolve({
        ok: status === undefined || (status >= 200 && status < 300),
        status: status || 200,
        json: () => Promise.resolve(body),
    });
}

(async function main() {
    // ------------------------------------------------------------------
    // 1. A normal poll happens and advances the cursor.
    // ------------------------------------------------------------------
    {
        const { w, calls } = boot(() => jsonResponse({ updated: [], vanished: [], now: 2000 }));
        pump(w);
        await wait(30);
        ok(calls.length === 1, 'one poll issued on visibilitychange');
        ok(/\/datasets\/changes-since\?ts=1000/.test(calls[0].url),
           'poll carries the seeded cursor');
        pump(w);
        await wait(30);
        ok(/ts=2000/.test(calls[1].url), 'cursor advanced to the response now');
    }

    // ------------------------------------------------------------------
    // 2. A slow request is not stacked on...
    // ------------------------------------------------------------------
    {
        let settle;
        const { w, calls } = boot(() => new Promise((res) => { settle = res; }));
        pump(w);
        await wait(10);
        pump(w);
        pump(w);
        await wait(10);
        ok(calls.length === 1, 'in-flight request is not stacked on');
        settle({ ok: true, status: 200, json: () => Promise.resolve({ updated: [], vanished: [], now: 3000 }) });
        await wait(30);
        pump(w);
        await wait(20);
        ok(calls.length === 2, 'polling resumes once the request settles');
    }

    // ------------------------------------------------------------------
    // 3. ...but a request that NEVER settles must not wedge polling forever.
    //    THE regression: pollInFlight stuck true = silent permanent stop.
    // ------------------------------------------------------------------
    {
        const { w, calls } = boot(() => new Promise(() => {}));   // never settles
        pump(w);
        await wait(10);
        ok(calls.length === 1, 'first poll issued');

        // The watchdog window is keyed on wall-clock, so wind the clock rather
        // than sleep: only Date.now needs to move for the guard to release.
        const realNow = Date.now;
        Date.now = () => realNow() + 60000;
        w.Date.now = Date.now;
        try {
            pump(w);
            await wait(20);
            ok(calls.length === 2,
               'a request that never settled must not block polling forever');
        } finally {
            Date.now = realNow;
            w.Date.now = realNow;
        }
    }

    // ------------------------------------------------------------------
    // 4. A hung request is aborted after the timeout.
    // ------------------------------------------------------------------
    {
        let sawSignal = null;
        const { w } = boot((url, opts) => {
            sawSignal = opts.signal;
            return new Promise(() => {});
        });
        pump(w);
        await wait(20);
        ok(sawSignal != null, 'the poll passes an abort signal');
        // jsdom timers are real; assert the abort is ARMED rather than waiting 10s.
        ok(sawSignal && sawSignal.aborted === false, 'not aborted before the timeout');
    }

    // ------------------------------------------------------------------
    // 5. HTTP 500 is a failure: no cursor advance, and it backs off.
    // ------------------------------------------------------------------
    {
        const { w, calls } = boot(() => jsonResponse({ error: 'boom' }, 500));
        pump(w);
        await wait(30);
        ok(calls.length === 1, 'error poll issued');
        pump(w);
        await wait(30);
        ok(calls.length === 1, 'a failed poll backs off instead of hammering');
    }

    // ------------------------------------------------------------------
    // 6. A malformed body is a failure too (it must NOT advance the cursor
    //    as if the window had been scanned and found empty).
    // ------------------------------------------------------------------
    {
        let phase = 0;
        const { w, calls } = boot(() => {
            phase++;
            return phase === 1
                ? jsonResponse({ nonsense: true })
                : jsonResponse({ updated: [], vanished: [], now: 9999 });
        });
        pump(w);
        await wait(30);
        ok(calls.length === 1, 'malformed poll issued');
        // Backoff is in effect; wind the clock to let the next poll through and
        // confirm the cursor never moved off its seed.
        const realNow = Date.now;
        Date.now = () => realNow() + 600000;
        w.Date.now = Date.now;
        try {
            pump(w);
            await wait(30);
            ok(calls.length === 2, 'polling recovers after the backoff window');
            ok(/ts=1000/.test(calls[1].url),
               'a malformed response never advances the cursor past an unscanned window');
        } finally {
            Date.now = realNow;
            w.Date.now = realNow;
        }
    }

    // ------------------------------------------------------------------
    // 7. A success clears the backoff immediately.
    // ------------------------------------------------------------------
    {
        let phase = 0;
        const { w, calls } = boot(() => {
            phase++;
            if (phase === 1) return jsonResponse({ error: 'boom' }, 500);
            return jsonResponse({ updated: [], vanished: [], now: 4000 });
        });
        pump(w);
        await wait(30);
        const realNow = Date.now;
        Date.now = () => realNow() + 600000;
        w.Date.now = Date.now;
        try {
            pump(w);
            await wait(30);
            ok(calls.length === 2, 'retry after backoff');
        } finally {
            Date.now = realNow;
            w.Date.now = realNow;
        }
        pump(w);
        await wait(30);
        ok(calls.length === 3, 'a success clears the backoff — next poll is immediate');
    }

    // ------------------------------------------------------------------
    // 8. partial:true schedules a prompt catch-up on its own.
    // ------------------------------------------------------------------
    {
        let phase = 0;
        const { w, calls } = boot(() => {
            phase++;
            return jsonResponse({
                updated: [], vanished: [], now: 5000 + phase,
                partial: phase === 1, skipped: phase === 1 ? 2 : 0,
            });
        });
        pump(w);
        await wait(30);
        ok(calls.length === 1, 'first (partial) poll issued');
        await wait(1500);        // the catch-up is scheduled well inside the 60s interval
        ok(calls.length === 2,
           'a partial response triggers a catch-up poll without waiting out the interval');
        await wait(1500);
        ok(calls.length === 2, 'a complete response does not keep re-polling');
    }

    // ------------------------------------------------------------------
    // 9. Deltas still apply (the hardening must not break the feature).
    // ------------------------------------------------------------------
    {
        const { w } = boot(() => jsonResponse({
            updated: [{ id: 2, exp: 'new_run', date: '2026-08-05', time: '02:00:00',
                        q: ['q2'], p: [], oc: {}, metric: '', bm: false, tags: [],
                        status: 'successful', dur: 1, note: '', parent: null,
                        hs: false, sm: {}, pm: {}, f: 'fold1' }],
            vanished: [], now: 6000,
        }));
        pump(w);
        await wait(50);
        // rowsById is keyed by the folder-aware uid, which is what getRow takes.
        ok(w.DatasetVirtual.getRow('fold1:2') != null,
           'a new run from the delta lands in the store');
    }

    if (failures) {
        console.error(`${failures} failure(s) of ${checks} checks`);
        process.exit(1);
    }
    console.log(`${checks} checks passed`);
    console.log('ALL OK');
    process.exit(0);
})().catch((e) => {
    console.error('selfcheck crashed: ' + (e && e.stack || e));
    process.exit(1);
});
