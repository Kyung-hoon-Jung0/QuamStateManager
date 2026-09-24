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
 *     full poll interval
 *
 * Arrival visibility (docs/104 #3):
 *   * the DEFAULT poll interval is 15s (a run must not take a minute to
 *     appear); `datasetPollInterval` pins it, and a legacy
 *     `autoRefreshInterval` tuned away from its shipped 60 still wins
 *   * a delta HELD while the user is active raises the "N new runs ↑" pill
 *     (the hold itself is unchanged — that part was always correct)
 *   * clicking the pill applies the held delta immediately and the count clears
 *   * rows the delta inserts render with the ds-row-new flash class
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

// The true Node setInterval, captured ONCE — each boot() installs a recording
// pass-through over it (never over a previous boot's recorder).
const NODE_SET_INTERVAL = global.setInterval;

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

function makeDom(page) {
    // `page` (QA F2): the real page shape -- #table-pane (the one scroller)
    // around .datasets-page with a server digest band, and its own rows.
    const inner = page
        ? `<div id="table-pane"><div class="datasets-page">
             <div class="ds-digest-band">${page.band}</div>
             <script id="ds-rows-data" data-now="1000">${JSON.stringify(page.rows)}</script>
             <div id="datasets-scroll"><table><thead id="datasets-thead"></thead>
               <tbody id="datasets-tbody"></tbody></table></div>
           </div></div>`
        : `<script id="ds-rows-data" data-now="1000">${JSON.stringify(ROWS)}</script>
        <div id="datasets-scroll" style="height:400px">
          <table><tbody id="datasets-tbody"></tbody></table>
        </div>`;
    const dom = new JSDOM(`<!doctype html><html><body>
        ${inner}
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
function boot(fetchImpl, uiConfig, page) {
    const dom = makeDom(page);
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
    if (uiConfig !== undefined) w.UI_CONFIG = uiConfig;   // before eval — init reads it
    // Record every setInterval delay so the poll-interval default is pinnable.
    // The module's bare `setInterval` resolves through NODE's global scope
    // (same reason the other globals above are re-pointed), so the recorder
    // must sit on `global` — always wrapping the true Node original.
    const intervals = [];
    const recInterval = function (fn, ms) { intervals.push(ms); return NODE_SET_INTERVAL(fn, ms); };
    w.setInterval = recInterval;
    global.setInterval = recInterval;
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
    return { dom, w, calls, intervals };
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
    // 8b. ...but the catch-up is BOUNDED. A workspace that can never be
    //     scanned inside the budget answers partial every time; unbounded,
    //     that turns a 60s poll into a 1.2s one, i.e. a 50x load increase
    //     applied exactly when the machine is already too slow.
    // ------------------------------------------------------------------
    {
        const { w, calls } = boot(() => jsonResponse({
            updated: [], vanished: [], now: 7000, partial: true, skipped: 3,
        }));
        pump(w);
        await wait(30);
        await wait(9000);          // well past 5 catch-ups at ~1.2s apart
        ok(calls.length <= 7,
           `a permanently-partial server must not be polled forever (got ${calls.length})`);
        ok(calls.length >= 3, 'some catch-up did happen');
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

    // ------------------------------------------------------------------
    // 10. Poll-interval default + config precedence (docs/104 #3).
    //     Default 15s; `datasetPollInterval` pins it; a legacy
    //     `autoRefreshInterval` tuned away from its shipped 60 still wins;
    //     the shipped 60 itself is NOT a choice and no longer pins the
    //     table to a minute.
    // ------------------------------------------------------------------
    {
        const a = boot(() => jsonResponse({ updated: [], vanished: [], now: 2000 }));
        ok(a.intervals.indexOf(5000) !== -1,
           `default dataset poll interval is 5s (docs/132) (got ${a.intervals})`);
        const b = boot(() => jsonResponse({ updated: [], vanished: [], now: 2000 }),
                       { autoRefreshInterval: 120 });
        ok(b.intervals.indexOf(120000) !== -1,
           'a lab-tuned autoRefreshInterval still wins over the 5s default');
        const c = boot(() => jsonResponse({ updated: [], vanished: [], now: 2000 }),
                       { autoRefreshInterval: 60 });
        ok(c.intervals.indexOf(5000) !== -1,
           'the shipped autoRefreshInterval=60 is not a choice — default 5s applies');
        const d = boot(() => jsonResponse({ updated: [], vanished: [], now: 2000 }),
                       { datasetPollInterval: 45, autoRefreshInterval: 120 });
        ok(d.intervals.indexOf(45000) !== -1,
           'datasetPollInterval outranks autoRefreshInterval for this poll');
    }

    // ------------------------------------------------------------------
    // 11. A delta HELD while the user is active raises the pill (the hold
    //     itself is the pinned docs/80 behaviour and stays); clicking the
    //     pill applies the held delta NOW and clears the count. The
    //     inserted row then renders with the flash class.
    // ------------------------------------------------------------------
    {
        const NEW_ROW = { id: 7, exp: 'fresh_run', date: '2026-08-10', time: '03:00:00',
                          q: ['q3'], p: [], oc: {}, metric: '', bm: false, tags: [],
                          status: 'successful', dur: 1, note: '', parent: null,
                          hs: false, sm: {}, pm: {}, f: 'fold1' };
        const { w } = boot(() => jsonResponse({ updated: [NEW_ROW], vanished: [], now: 8000 }));
        // Make the user "active" the way the app does: a scroll on the list.
        w.document.getElementById('datasets-scroll').dispatchEvent(new w.Event('scroll'));
        pump(w);
        await wait(40);
        ok(w.DatasetVirtual.getRow('fold1:7') == null,
           'the delta is HELD while the user is active (pinned hold unchanged)');
        const pill = w.document.getElementById('ds-new-pill');
        ok(pill != null, 'the arrival pill exists');
        ok(pill && pill.hidden === false, 'the pill is visible while a delta is held');
        ok(pill && /1 new run/.test(pill.textContent),
           `the pill counts the held new run (got "${pill && pill.textContent}")`);
        pill.click();
        ok(w.DatasetVirtual.getRow('fold1:7') != null,
           'clicking the pill applies the held delta immediately');
        ok(pill.hidden === true, 'clicking the pill clears + hides it');
        await wait(120);   // let the rAF render land
        const tbody = w.document.getElementById('datasets-tbody');
        const tr = tbody.querySelector('tr[data-id="fold1:7"]');
        ok(tr != null, 'the new run is rendered');
        ok(tr != null && tr.classList.contains('ds-row-new'),
           'a just-inserted row carries the ds-row-new flash class');
        const old = tbody.querySelector('tr[data-id="fold1:1"]');
        ok(old != null && !old.classList.contains('ds-row-new'),
           'pre-existing rows do not flash');
    }

    // ------------------------------------------------------------------
    // 12. An idle apply (no hold) still announces: pill shows, and the
    //     user reaching the top of the list acknowledges it (the pill's
    //     own click performs that same scroll-to-top).
    // ------------------------------------------------------------------
    {
        const NEW_ROW = { id: 9, exp: 'idle_run', date: '2026-08-10', time: '04:00:00',
                          q: ['q1'], p: [], oc: {}, metric: '', bm: false, tags: [],
                          status: 'successful', dur: 1, note: '', parent: null,
                          hs: false, sm: {}, pm: {}, f: 'fold1' };
        const { w } = boot(() => jsonResponse({ updated: [NEW_ROW], vanished: [], now: 9000 }));
        pump(w);
        await wait(40);
        ok(w.DatasetVirtual.getRow('fold1:9') != null, 'idle poll applies directly');
        const pill = w.document.getElementById('ds-new-pill');
        ok(pill && pill.hidden === false, 'an applied arrival still shows the pill');
        // The user scrolls (jsdom scrollTop stays 0 = at the top) → acknowledged.
        w.document.getElementById('datasets-scroll').dispatchEvent(new w.Event('scroll'));
        ok(pill.hidden === true, 'reaching the top of the list acknowledges applied arrivals');
    }

    // ------------------------------------------------------------------
    // 13. docs/170: a row that arrives OLDER than the newest one the table
    //     holds is the cold build catching up (the render is bounded now and
    //     the delta poll indexes the rest) -- it lands, but is never announced
    //     or flashed as a new run. A genuinely newer row in the same delta
    //     still is. And the render's "still indexing" note goes away on the
    //     first poll that reports a complete scan.
    // ------------------------------------------------------------------
    {
        const BACKFILL = { id: 0, exp: 'old_run', date: '2026-07-01', time: '09:00:00',
                           q: ['q1'], p: [], oc: {}, metric: '', bm: false, tags: [],
                           status: 'successful', dur: 1, note: '', parent: null,
                           hs: false, sm: {}, pm: {}, f: 'fold1' };
        const FRESH = { id: 11, exp: 'fresh_run', date: '2026-08-10', time: '05:00:00',
                        q: ['q1'], p: [], oc: {}, metric: '', bm: false, tags: [],
                        status: 'successful', dur: 1, note: '', parent: null,
                        hs: false, sm: {}, pm: {}, f: 'fold1' };
        const { w } = boot(() => jsonResponse({ updated: [BACKFILL, FRESH], vanished: [],
                                                now: 9500, partial: false }));
        const note = w.document.createElement('small');
        note.id = 'ds-scan-note';
        w.document.body.appendChild(note);
        pump(w);
        await wait(40);
        ok(w.DatasetVirtual.getRow('fold1:0') != null, 'the backfilled (older) run lands in the store');
        ok(w.DatasetVirtual.getRow('fold1:11') != null, 'and so does the genuinely new one');
        const pill = w.document.getElementById('ds-new-pill');
        ok(pill && pill.hidden === false && /1 new run/.test(pill.textContent),
           `only the newer run is announced (got "${pill && pill.textContent}")`);
        await wait(120);
        const tbody = w.document.getElementById('datasets-tbody');
        const oldTr = tbody.querySelector('tr[data-id="fold1:0"]');
        const newTr = tbody.querySelector('tr[data-id="fold1:11"]');
        ok(oldTr != null && !oldTr.classList.contains('ds-row-new'), 'the backfilled row does not flash');
        ok(newTr != null && newTr.classList.contains('ds-row-new'), 'the new row does');
        ok(note.hidden === true, 'a complete scan hides the "still indexing" note');
    }

    // ------------------------------------------------------------------
    // 14. QA F2: with several folders the default id sort interleaves them
    //     -- run ids are per-folder, so a young folder's run from TODAY
    //     sorts below an old folder's big ids. The pill must scroll to where
    //     the arrival actually sits (it used to set scrollTop 0), a scroll at
    //     the top must not acknowledge an arrival that is not on screen, and
    //     the digest band must follow the applied delta (it stayed on the
    //     page-load day).
    //     Geometry is stubbed (jsdom has no layout): #table-pane is 400 px
    //     high and the list starts 100 px down it.
    // ------------------------------------------------------------------
    {
        const mk = (f, id, date, time) => ({ id, exp: 'rabi', date, time, q: ['q1'], p: [],
            oc: {}, metric: '', bm: false, tags: [], status: 'successful', dur: 1, note: '',
            parent: null, hs: false, sm: {}, pm: {}, f });
        const rows = [];
        for (let i = 30; i >= 10; i--) rows.push(mk('kh', i, '2026-08-01', '01:00:00'));   // pos 0..20
        rows.push(mk('kr', 4, '2026-08-02', '02:00:00'));
        const BAND = '<span class="ds-digest-date">2026-08-02</span>'
            + '<span class="ds-digest-item">1 run</span><span class="ds-digest-ok">all OK</span>';
        const NEW_ROW = mk('kr', 5, '2026-08-20', '07:00:00');   // newest by far, id 5 -> pos 21
        function geometry(w) {
            const pane = w.document.getElementById('table-pane');
            const tb = w.document.getElementById('datasets-tbody');
            const g = { S: 0, writes: [] };
            Object.defineProperty(pane, 'scrollTop', { configurable: true,
                get: () => g.S, set: (v) => { g.S = v; g.writes.push(v); } });
            Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => 400 });
            pane.getBoundingClientRect = () => ({ top: 0, bottom: 400, left: 0, right: 900, width: 900, height: 400 });
            tb.getBoundingClientRect = () => ({ top: 100 - g.S, bottom: 100 - g.S + 736, left: 0, right: 900, width: 900, height: 736 });
            g.pane = pane; g.tb = tb;
            return g;
        }

        // (a) a HELD arrival: the pill click scrolls to it, and it renders
        {
            const { w } = boot(() => jsonResponse({ updated: [NEW_ROW], vanished: [], now: 8000 }),
                               undefined, { rows, band: BAND });
            const g = geometry(w);
            g.pane.dispatchEvent(new w.Event('scroll'));   // the user is active -> the delta is held
            pump(w);
            await wait(40);
            const pill = w.document.getElementById('ds-new-pill');
            ok(pill && pill.hidden === false && w.DatasetVirtual.getRow('kr:5') == null,
               'F2 fixture: the kr:5 arrival is held and announced');
            pill.click();
            // kr:5 sorts at list position 21 (ids 30..10 of kh come first).
            // One row of headroom: 100 + 21*32 - 32 = 740.
            ok(g.writes.length > 0 && g.writes[g.writes.length - 1] === 740,
               `the pill scrolls to where the arrival sorts (scrollTop ${g.writes[g.writes.length - 1]}, want 740 -- 0 was the bug)`);
            await wait(120);
            ok(g.tb.querySelector('tr[data-id="kr:5"]') != null,
               'the arrival row is rendered at the new scroll position');
        }

        // (b) an IDLE-applied arrival: the top of the list is not where it is
        {
            const { w } = boot(() => jsonResponse({ updated: [NEW_ROW], vanished: [], now: 8000 }),
                               undefined, { rows, band: BAND });
            const g = geometry(w);
            pump(w);
            await wait(40);
            ok(w.DatasetVirtual.getRow('kr:5') != null, 'F2 fixture: the idle poll applied kr:5');
            const pill = w.document.getElementById('ds-new-pill');
            const band = w.document.querySelector('.ds-digest-band');
            ok(pill && pill.hidden === false, 'the applied arrival is announced');
            ok(band.querySelector('.ds-digest-date').textContent === '2026-08-20'
               && band.querySelector('.ds-digest-item').textContent === '1 run',
               `the digest band follows the applied delta to the new day (got "${band.textContent}")`);
            ok(!band.querySelector('.ds-digest-filtered') && !band.hasAttribute('data-filtered'),
               'with no filter the recomputed band carries no "(filtered set)" suffix');
            g.S = 0;
            g.pane.dispatchEvent(new w.Event('scroll'));
            ok(pill.hidden === false,
               'a scroll at the TOP does not acknowledge an arrival that sorts at row 21');
            g.S = 740;
            g.pane.dispatchEvent(new w.Event('scroll'));
            ok(pill.hidden === true, 'scrolling to the arrival row acknowledges it');
        }

        // (d) an arrival already on screen from the very top keeps the old
        //     scroll-to-top (the header, digest and filters stay in view)
        {
            const TOP_ROW = mk('kr', 40, '2026-08-20', '08:00:00');   // id 40 > every kh id -> pos 0
            const { w } = boot(() => jsonResponse({ updated: [TOP_ROW], vanished: [], now: 8000 }),
                               undefined, { rows, band: BAND });
            const g = geometry(w);
            g.S = 500;                                     // the user is down the page
            g.pane.dispatchEvent(new w.Event('scroll'));   // active -> held
            pump(w);
            await wait(40);
            w.document.getElementById('ds-new-pill').click();
            ok(g.writes[g.writes.length - 1] === 0,
               `a row that shows from the top lands at scrollTop 0 (got ${g.writes[g.writes.length - 1]})`);
        }

        // (c) a filter still gets the FILTERED band (and its suffix)
        {
            const { w } = boot(() => jsonResponse({ updated: [NEW_ROW], vanished: [], now: 8000 }),
                               undefined, { rows, band: BAND });
            geometry(w);
            w.DatasetVirtual.toggleFolder('kh');
            pump(w);
            await wait(40);
            const band = w.document.querySelector('.ds-digest-band');
            ok(band.getAttribute('data-filtered') === '1' && !!band.querySelector('.ds-digest-filtered')
               && band.querySelector('.ds-digest-date').textContent === '2026-08-01',
               `a folder filter keeps the filtered digest over ITS rows (got "${band.textContent}")`);
            w.DatasetVirtual.toggleFolder('');
            ok(band.querySelector('.ds-digest-date').textContent === '2026-08-20' && !band.hasAttribute('data-filtered'),
               'clearing it shows the live band, not the stale server one');
        }
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
