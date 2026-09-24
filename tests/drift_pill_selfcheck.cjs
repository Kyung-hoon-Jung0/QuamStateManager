/* QA review of regenerate-r2-36 -- the every-page /state/drift poll repaints a
 * pill that still claims "Synced" once the server says the live chip moved.
 *
 * The server judges an outside write on the very next poll, but only a full
 * render repainted the pill: an open page kept reading "● Synced" right above
 * a Re-generate result saying the live chip's files had changed. Driven
 * through the REAL app.js under jsdom:
 *   - synced pill + live_diverged:true  -> ONE GET /state/tray into #pending-tray
 *   - live_diverged false/absent        -> nothing
 *   - a dirty / drifted / archive pill  -> nothing (their words already hold)
 *   - a refresh in flight               -> no second request
 *   - an Auto-Sync pull / apply in flight -> nothing (it repaints the tray)
 *
 * Run: node tests/drift_pill_selfcheck.cjs   (needs jsdom)
 * Sloppy mode on purpose, like the other app.js harnesses: a getter-only
 * global (navigator, window.localStorage) just keeps its own value.
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/regenerate', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
try {   // node >= 21 ships a getter-only global navigator
    Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true });
} catch (e) {}
global.location = window.location;
global.history = window.history;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

// /state/drift answers whatever the scenario set; everything else never settles
let driftPayload = { ok: true, tracked: false, count: 0 };
global.fetch = (url) => {
    if (String(url).indexOf('/state/drift') === 0) {
        const body = driftPayload;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }
    return new Promise(() => {});
};
window.fetch = global.fetch;

const calls = [];
let ajaxResult = () => Promise.resolve();
window.htmx = {
    ajax: (method, url, opts) => { calls.push({ method, url, opts }); return ajaxResult(); },
    trigger: () => {}, process: () => {},
};
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }

function tray(cls) {
    window.document.body.innerHTML = cls === null ? '' :
        '<div id="pending-tray" class="pending-tray"><button type="button" '
        + 'class="state-status-badge ' + cls + '">Synced</button></div>';
}
async function poll(payload) {
    driftPayload = payload;
    window._pollDrift();
    await settle();
}
const trayGets = () => calls.filter((c) => c.url === '/state/tray');

(async () => {
    await settle();                                   // the on-load poll
    ok(typeof window._pollDrift === 'function', 'the drift poll is exposed');
    ok(typeof window._onDriftLiveDiverged === 'function', 'the pill check is exposed');

    // 1. the reported state: a synced pill, a server that says diverged
    tray('state-status-synced');
    calls.length = 0;
    await poll({ ok: true, tracked: false, count: 0, live_diverged: true });
    ok(trayGets().length === 1, 'a synced pill under a diverged live re-renders the tray once (got '
       + trayGets().length + ')');
    const c = trayGets()[0] || { opts: {} };
    ok(c.method === 'GET' && c.opts.target === '#pending-tray' && c.opts.swap === 'outerHTML',
       'as the tray fragment, swapped outerHTML into #pending-tray');

    // 2. nothing moved: nothing requested
    for (const p of [{ ok: true, live_diverged: false }, { ok: true }, null]) {
        calls.length = 0;
        await poll(p);
        ok(trayGets().length === 0, 'no divergence -> no request (' + JSON.stringify(p) + ')');
    }

    // 3. a pill that does not claim Synced is already telling the truth
    for (const cls of ['state-status-dirty', 'state-status-drifted', 'state-status-archive']) {
        tray(cls);
        calls.length = 0;
        await poll({ ok: true, live_diverged: true });
        ok(trayGets().length === 0, cls + ' is left alone');
    }
    // an Auto-Sync pull (or an apply) in flight repaints the tray itself
    tray('state-status-synced');
    calls.length = 0;
    await poll({ ok: true, live_diverged: true, auto_pull: true });
    ok(trayGets().length === 0, 'an armed pull owns the tray repaint (got ' + trayGets().length + ')');
    window._applyInFlight = true;
    await poll({ ok: true, live_diverged: true });
    ok(trayGets().length === 0, 'so does an apply in flight');
    window._applyInFlight = false;
    tray(null);
    calls.length = 0;
    await poll({ ok: true, live_diverged: true });
    ok(trayGets().length === 0, 'no tray on the page -> nothing');

    // 4. one refresh at a time
    tray('state-status-synced');
    calls.length = 0;
    let release;
    ajaxResult = () => new Promise((r) => { release = r; });
    await poll({ ok: true, live_diverged: true });
    await poll({ ok: true, live_diverged: true });
    ok(trayGets().length === 1, 'a refresh in flight is not doubled (got ' + trayGets().length + ')');
    release();
    await settle();
    ajaxResult = () => Promise.resolve();
    await poll({ ok: true, live_diverged: true });
    ok(trayGets().length === 2, 'and once it settles the next poll may ask again');

    // 5. once the swap landed (the tray now reads "Live chip moved") it stops
    tray('state-status-drifted');
    calls.length = 0;
    await poll({ ok: true, live_diverged: true });
    ok(trayGets().length === 0, 'the repainted pill is not re-requested every poll');

    if (fails) { console.error(fails + ' failure(s) of ' + asserts); process.exit(1); }
    console.log('drift_pill_selfcheck: all ' + asserts + ' assertions passed');
    process.exit(0);
})().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
