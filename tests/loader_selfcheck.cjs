/* docs/146 -- the centered loader covers the wait it exists for.
 * Pins, against the real app.js:
 *  1. a slow-route request shows the loader after the grace period
 *  2. a CONCURRENT non-slow response (poll, tray) does NOT douse it --
 *     the old global hide is why the customer never saw the popup
 *  3. the slow response's arrival does NOT hide it either -- the swap +
 *     render the user is waiting for comes after; it drops only after
 *     afterSettle + a double-rAF (first painted frame of the new pane)
 *  4. an error hides it immediately
 *  5. markup/CSS: compositor spinner (transform keyframes -- keeps moving
 *     while the main thread is blocked) + the please-wait line
 * Run: node tests/loader_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
let passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; }
                    else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body>'
    + '<div id="table-pane"></div>'
    + '<div id="quam-loader" class="quam-loader"><div class="quam-loader-spinner"></div>'
    + '<div class="quam-loader-text"><span>Q</span></div>'
    + '<div class="quam-loader-sub">Please wait</div>'
    + '<div id="quam-loader-progress"></div></div>'
    + '</body></html>', { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 5); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.eval("fetch = window.fetch = function(){ return new Promise(function(){}); };");
global.fetch = window.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
const loader = d.getElementById('quam-loader');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function fire(name, path) {
    d.dispatchEvent(new window.CustomEvent(name, { detail: { requestConfig: { path: path } } }));
}
// A cancellable one, so a canceller can actually cancel it (below).
function fireCancelable(name, path) {
    return d.dispatchEvent(new window.CustomEvent(
        name, { detail: { requestConfig: { path: path } }, cancelable: true }));
}

(async () => {
    // 1. slow request -> visible after the grace period
    fire('htmx:beforeRequest', '/bulk');
    ok(!loader.classList.contains('visible'), 'not shown instantly (grace period)');
    await sleep(140);
    ok(loader.classList.contains('visible'), 'shown after the grace period');

    // 2. a concurrent NON-slow response must not douse it (the poll bug)
    fire('htmx:afterRequest', '/state/tray');
    fire('htmx:afterRequest', '/datasets-poll-not-listed/changes');
    await sleep(30);
    ok(loader.classList.contains('visible'), 'a concurrent poll/tray response does NOT hide it');

    // 3. the slow response's ARRIVAL does not hide it; afterSettle + paint does
    fire('htmx:afterRequest', '/bulk');
    await sleep(30);
    ok(loader.classList.contains('visible'), 'still visible after the response arrives (swap/render pending)');
    d.dispatchEvent(new window.CustomEvent('htmx:afterSettle', { detail: {} }));
    await sleep(60);   // double-rAF (stubbed at 5 ms each)
    ok(!loader.classList.contains('visible'), 'hidden one painted frame after settle');

    // 3b. docs/163: THE FRAME MAY NEVER COME.
    // The harness stubs rAF as `setTimeout(f, 5)`, so it always fires and the
    // assertion above could never fail -- the state the bug lives in was
    // unreachable from this fixture. In a hidden or occluded window Chrome
    // runs NO animation frames at all (measured in real Chrome:
    // `document.hidden === true`, zero frames in 3 s), and switching windows
    // is exactly what a person does while waiting for a slow page. The popup
    // then sat over a finished grid until SAFETY_HIDE_MS -- 45 seconds of
    // "Please wait a moment..." on a page that was ready. Hiding must PREFER
    // the frame and never depend on it.
    {
        const realRaf = window.requestAnimationFrame;
        window.requestAnimationFrame = function () { /* a hidden window */ };
        global.requestAnimationFrame = window.requestAnimationFrame;
        fire('htmx:beforeRequest', '/bulk');
        await sleep(140);
        ok(loader.classList.contains('visible'), 'hidden-window: a slow request still shows it');
        fire('htmx:afterRequest', '/bulk');
        d.dispatchEvent(new window.CustomEvent('htmx:afterSettle', { detail: {} }));
        await sleep(60);
        ok(loader.classList.contains('visible'),
           'hidden-window: not hidden before the fallback is due (the frame is still preferred)');
        await sleep(300);
        ok(!loader.classList.contains('visible'),
           'hidden-window: hidden by the timeout fallback, NOT left for the 45 s safety');
        window.requestAnimationFrame = realRaf;
        global.requestAnimationFrame = realRaf;
    }

    // 3c. docs/163: a timer that outlives its own request may not show the
    // popup. `/datasets` is in SLOW_PREFIXES and its poll runs every 5 s, so a
    // poll that answers inside the 80 ms grace arms a show timer that only
    // `hide()` would clear -- and in a hidden window `hide()` is the thing
    // that never runs. The popup then appears for a request that finished
    // 50 ms ago and stays for the full safety window. Showing must be a
    // function of what is in flight, not of a timer that was once armed.
    {
        const realRaf = window.requestAnimationFrame;
        window.requestAnimationFrame = function () { /* a hidden window */ };
        global.requestAnimationFrame = window.requestAnimationFrame;
        fire('htmx:beforeRequest', '/datasets');       // arms the 80 ms show timer
        fire('htmx:afterRequest', '/datasets');        // ...answers inside the grace
        await sleep(150);                              // past the grace, before the fallback
        ok(!loader.classList.contains('visible'),
           'a request that finished inside the grace period never shows the popup');
        await sleep(250);
        ok(!loader.classList.contains('visible'), 'and it stays hidden');
        window.requestAnimationFrame = realRaf;
        global.requestAnimationFrame = realRaf;
    }

    // 3d. docs/163: a CANCELLED request is not an in-flight request. Capture
    // phase runs before every bubble listener, so this cancels it the way
    // PaneState's keep-route interceptor does.
    {
        const cancel = (e) => e.preventDefault();
        d.addEventListener('htmx:beforeRequest', cancel, true);
        fireCancelable('htmx:beforeRequest', '/bulk');
        d.removeEventListener('htmx:beforeRequest', cancel, true);
        await sleep(150);
        ok(!loader.classList.contains('visible'),
           'a cancelled request never shows the popup (nothing was ever sent)');
        window._slowLoaderHide();
    }

    // 4. error hides immediately
    fire('htmx:beforeRequest', '/param-history');
    await sleep(140);
    ok(loader.classList.contains('visible'), 'second slow request shows again');
    fire('htmx:responseError', '/param-history');
    ok(!loader.classList.contains('visible'), 'an error hides it immediately');

    // 5. markup + CSS contracts
    const base = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
    ok(base.indexOf('quam-loader-spinner') > -1 && /quam-loader-sub[^>]*>Please wait a moment/.test(base),
       'base.html carries the spinner + the please-wait line');
    const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
    ok(/@keyframes quam-loader-spin \{ to \{ transform: rotate\(360deg\); \} \}/.test(css),
       'the ring spins via a compositor TRANSFORM animation (survives main-thread jank)');
    ok(/prefers-reduced-motion[^}]*\{\s*\n?\s*\.quam-loader-spinner \{ animation: none; \}/.test(css)
       || /\.quam-loader-spinner \{ animation: none; \}/.test(css),
       'reduced-motion users get a static ring');

    console.log(fails ? ('FAILED: ' + fails) : ('ALL OK (' + passes + ' assertions)'));
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.message); process.exit(1); });
