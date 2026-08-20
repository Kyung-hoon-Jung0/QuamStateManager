/* docs/124 M-16/M-17 — the Explorer scroll restore's abort discipline.
 *
 * Two executed defects in the docs/122 retry mechanism:
 *   M-16  one shared abort boolean: arming restore B reset it and RESURRECTED
 *         restore A's already-aborted 1400/2400ms timers, which yanked the
 *         user to A's stale target (measured: four ping-pong yanks over 2s).
 *         Fixed with per-restore state + a generation counter (a superseded
 *         restore's timers never write, aborted or not).
 *   M-17  the abort listened for wheel/touchmove/PageUp·PageDown·Home·End
 *         only. A scrollbar track click or thumb drag reaches the page as
 *         NOTHING but a mousedown on the scroller element itself, and
 *         ArrowUp/ArrowDown/Space scroll too — all invisible, all yanked back
 *         by the next retry. Fixed by widening the event set; deliberately
 *         NOT a raw 'scroll' listener, because the browser fires the same
 *         event when it CLAMPS scrollTop while the filter settles — that
 *         would abort the very restore the retries exist for.
 *
 * Drives the REAL app.js under jsdom through real htmx:beforeSwap/afterSwap
 * events (the PaneState soft-capture path); scrollTop writes are ledgered via
 * an instrumented property, so verdicts are event logs, not impressions.
 *
 * Run: node tests/scroll_abort_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="pending-tray" data-seq="1"></div>' +
    '<div id="table-pane">' +
    '<div id="explorer-tree-state" class="json-tree"><div class="tree-node">row</div></div>' +
    '<div id="explorer-tree-wiring" style="display:none"></div>' +
    '<input id="tree-search-box" type="search">' +
    '</div></body></html>', { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator',
    { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
global.fetch = () => new Promise(() => {});
Object.defineProperty(window, 'fetch',
    { value: global.fetch, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

// listener accounting: wrap BEFORE app.js evaluates
const live = { wheel: new Set(), touchmove: new Set(), mousedown: new Set() };
const origAdd = window.addEventListener.bind(window);
const origRemove = window.removeEventListener.bind(window);
window.addEventListener = function (type, fn, opts) {
    if (live[type]) live[type].add(fn);
    return origAdd(type, fn, opts);
};
window.removeEventListener = function (type, fn, opts) {
    if (live[type]) live[type].delete(fn);
    return origRemove(type, fn, opts);
};

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate: ' + e.message);
    process.exit(1);
}

// app.js registers its own PERMANENT window listeners (one lives on
// mousedown); only the abort listeners' delta is under test.
const base = { wheel: live.wheel.size, touchmove: live.touchmove.size,
               mousedown: live.mousedown.size };

const el = window.document.getElementById('explorer-tree-state');
let _st = 0;
const writes = [];
let T0 = Date.now();
Object.defineProperty(el, 'scrollTop', {
    get() { return _st; },
    set(v) { writes.push({ t: Date.now() - T0, v: v }); _st = v; },
    configurable: true,
});

const doc = window.document;
function pane() { return doc.getElementById('table-pane'); }
// Same-route rebuild: beforeSwap captures the user's current scroll, the swap
// resets it to 0, afterSwap arms the retried restore toward the captured value.
function swapExplorer(scrollAtCapture) {
    _st = scrollAtCapture;
    const before = new window.CustomEvent('htmx:beforeSwap', {
        cancelable: true,
        detail: { shouldSwap: true, pathInfo: { finalRequestPath: '/explorer' } },
    });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    _st = 0;
    const after = new window.CustomEvent('htmx:afterSwap', {
        detail: { pathInfo: { finalRequestPath: '/explorer' } },
    });
    Object.defineProperty(after, 'target', { value: pane() });
    doc.dispatchEvent(after);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wrote = (v, since) => writes.some((w) => w.v === v && w.t >= (since || 0));

(async () => {
    T0 = Date.now();

    // ── A/B: wheel abort + no zombie resurrection (M-16) ───────────────────
    swapExplorer(420);
    await sleep(350);
    ok(wrote(420), 'baseline: restore A wrote its 420 target (retry #1)');
    window.dispatchEvent(new window.Event('wheel'));      // user takes over
    _st = 1000;
    const tB = Date.now() - T0;
    swapExplorer(1000);                                   // restore B arms
    await sleep(3000);
    const zombie = writes.filter((w) => w.t > tB + 30 && w.v === 420);
    ok(zombie.length === 0,
       'M-16: arming B never resurrects A\'s aborted timers (zombie 420 writes: '
       + zombie.length + ')');
    ok(wrote(1000, tB), 'and B\'s own restore completed (final=' + _st + ')');

    // ── ArrowDown aborts; typing arrows in the search box does not (M-17) ──
    swapExplorer(500);
    doc.body.dispatchEvent(new window.KeyboardEvent('keydown',
        { key: 'ArrowDown', bubbles: true }));
    await sleep(2800);
    ok(!wrote(500), 'M-17: ArrowDown scrolling aborts every retry (500 never written)');

    swapExplorer(600);
    doc.getElementById('tree-search-box').dispatchEvent(new window.KeyboardEvent('keydown',
        { key: 'ArrowDown', bubbles: true }));
    await sleep(400);
    ok(wrote(600), 'arrow keys INSIDE an input are typing, not scrolling — restore proceeds');

    // ── scrollbar interaction: mousedown on the scroller itself (M-17) ─────
    swapExplorer(700);
    el.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    await sleep(2800);
    ok(!wrote(700), 'M-17: mousedown on the scroller (a track click / thumb drag) aborts (700 never written)');

    swapExplorer(800);
    el.querySelector('.tree-node').dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    await sleep(400);
    ok(wrote(800), 'a mousedown on a ROW is a click, not a scroll — restore proceeds');

    // ── listener hygiene ───────────────────────────────────────────────────
    // Generous: on a KEEP route an afterSwap can arm a SECOND restore slightly
    // later (PaneState's verify-restore path), whose own 2600ms window must
    // also close before this asserts.
    await sleep(4200);
    // wheel/touchmove belong ONLY to the abort mechanism — their delta must be
    // zero. mousedown is shared: some other app.js path lazily registers ONE
    // window mousedown singleton during the first swaps, so the pin for it is
    // NO GROWTH per restore, measured over two further aborted restores.
    ok(live.wheel.size === base.wheel && live.touchmove.size === base.touchmove,
       'all wheel/touchmove abort listeners removed after their windows closed (delta: wheel='
       + (live.wheel.size - base.wheel) + ' touchmove=' + (live.touchmove.size - base.touchmove) + ')');
    const mdBefore = live.mousedown.size;
    swapExplorer(910);
    window.dispatchEvent(new window.Event('wheel'));
    swapExplorer(920);
    window.dispatchEvent(new window.Event('wheel'));
    await sleep(2800);
    ok(live.mousedown.size <= mdBefore,
       'mousedown abort listeners do not accumulate across restores ('
       + mdBefore + ' -> ' + live.mousedown.size + ')');

    if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
    console.log('all checks passed');
    process.exit(0);
})().catch((e) => { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
