/* jsdom selfcheck for PaneState v2 (docs/110 #10-A) — a tab keeps its state.
 *
 * v2 contract (post-audit): PaneState NEVER cancels htmx — navigation,
 * history snapshot and URL push all run normally. Park happens at
 * htmx:beforeSwap (post-snapshot, real swaps only — a failed request never
 * parks); restore happens at htmx:afterSwap by REPLACING the fresh server
 * render with the parked DOM when the seq+chip gate passes; stale/chip-moved
 * copies drop and the SOFT tier re-applies the query. popstate /
 * htmx:historyRestore clear the stash AND re-sync the route.
 *
 * Run: node tests/pane_state_selfcheck.cjs  (driven by tests/test_pane_state.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/explorer', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.history = window.history;   // the skip path pushes the URL itself
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => new Promise(() => {});   // the verify probe never resolves — fine
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const doc = window.document;
doc.body.innerHTML =
    '<div id="pending-tray" data-seq="7"></div>' +
    '<div id="table-pane">' +
    '  <input type="search" id="explorer-search" class="tree-search" value="f_01">' +
    '  <details id="sec-a" open><summary>qubits</summary><div>tree…</div></details>' +
    '</div>';
window.__chipToken = 'chipA';

const pane = () => doc.getElementById('table-pane');

// Simulate a full htmx nav: beforeSwap (park side) → server swap → afterSwap.
function swapTo(pathTo, freshHtml) {
    const before = new window.CustomEvent('htmx:beforeSwap', {
        cancelable: true,
        detail: { shouldSwap: true, pathInfo: { finalRequestPath: pathTo } },
    });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    pane().innerHTML = freshHtml;              // the htmx swap
    const after = new window.CustomEvent('htmx:afterSwap', {
        detail: { pathInfo: { finalRequestPath: pathTo } },
    });
    Object.defineProperty(after, 'target', { value: pane() });
    doc.dispatchEvent(after);
}

// ── 1. park at beforeSwap + fresh restore at afterSwap ─────────────────────
swapTo('/bulk', '<div id="bulk-stub">bulk</div>');
ok(!!doc.getElementById('bulk-stub'), 'navigating away swaps normally (htmx untouched)');
ok(Object.keys(window.PaneState._stash()).length === 1, 'the explorer DOM was parked');
swapTo('/explorer', '<div id="fresh-explorer">fresh server render</div>');
ok(!doc.getElementById('fresh-explorer'),
   'returning replaces the fresh render with the parked DOM');
const inp = doc.getElementById('explorer-search');
ok(!!inp && inp.value === 'f_01', 'the search text survived the round trip');
ok(!!doc.getElementById('sec-a') && doc.getElementById('sec-a').open,
   'the expanded section survived the round trip');
ok(window.PaneState._cur() === '/explorer', 'the current route tracks the swap');

// ── 2. a failed request never parks (no beforeSwap ⇒ pane intact) ──────────
// (nothing to simulate — the pins above prove parking only rides beforeSwap;
// assert the negative: an errored swap event with shouldSwap=false is ignored)
{
    const before = new window.CustomEvent('htmx:beforeSwap', {
        cancelable: true,
        detail: { shouldSwap: false, pathInfo: { finalRequestPath: '/bulk' } },
    });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    ok(!!doc.getElementById('explorer-search'),
       'shouldSwap=false (error path) never parks — the pane stays intact');
}

// ── 2b. a swap VETOED upstream never parks (QA F1) ─────────────────────────
// The Live-Edit leave guard listens on document.body and, on Cancel, calls
// ONLY preventDefault. htmx then aborts the swap (no afterSwap ever comes), so
// parking here detached the grid and left the pane blank under /bulk. The
// event must be dispatched ON the pane so body-before-document order is real
// (swapTo() dispatches on document and would skip the body listener).
{
    const kidsBefore = pane().children.length;
    const stashBefore = Object.keys(window.PaneState._stash()).sort().join(',');
    const veto = (ev) => {
        if (ev.detail && ev.detail.target && ev.detail.target.id === 'table-pane') ev.preventDefault();
    };
    doc.body.addEventListener('htmx:beforeSwap', veto);
    const before = new window.CustomEvent('htmx:beforeSwap', {
        bubbles: true, cancelable: true,
        detail: { shouldSwap: true, target: pane(), pathInfo: { finalRequestPath: '/help' } },
    });
    const proceed = pane().dispatchEvent(before);
    doc.body.removeEventListener('htmx:beforeSwap', veto);
    ok(proceed === false, 'fixture: the body guard really vetoed the swap');
    ok(pane().children.length === kidsBefore && kidsBefore > 0
       && !!doc.getElementById('explorer-search'),
       'a vetoed swap never parks — the pane keeps its content');
    ok(Object.keys(window.PaneState._stash()).sort().join(',') === stashBefore,
       'a vetoed swap leaves the stash untouched');
    ok(window.PaneState._cur() === '/explorer', 'a vetoed swap keeps the current route');
}

// ── 3. stale seq ⇒ the fresh swap WINS + SOFT re-applies the query ─────────
swapTo('/bulk', '<div id="bulk-stub">bulk</div>');        // park explorer (seq 7)
doc.getElementById('pending-tray').setAttribute('data-seq', '9');   // an edit
let inputFired = 0;
swapTo('/explorer',
       '<input type="search" id="explorer-search" class="tree-search" value="">');
const fresh = doc.getElementById('explorer-search');
fresh.addEventListener('input', () => inputFired++);
// _reapplySoft ran synchronously inside afterSwap — value already applied:
ok(fresh.value === 'f_01', 'stale copy dropped; SOFT re-applied the query on the fresh DOM');
ok(!window.PaneState._stash()['/explorer'], 'the stale parked copy was dropped');

// ── 4. chip switch ⇒ fresh swap wins ───────────────────────────────────────
swapTo('/bulk', '<div>bulk</div>');                       // park explorer (seq 9)
window.__chipToken = 'chipB';
swapTo('/explorer', '<div id="fresh2">fresh</div>');
ok(!!doc.getElementById('fresh2'), 'a chip switch keeps the fresh render');

// docs/190 F26: the Pulses filter is search text + channel tab + owner pick.
// A sidebar re-click renders fresh defaults; SOFT used to put the text back
// (and only the text), so the XY tab silently reverted to All.
{
    const PULSES = (q, ch, owner) =>
        '<div class="table-filter"><input type="search" name="q" id="pulses-q" value="' + q + '"></div>' +
        '<nav id="pulse-channel-tabs"><ul>' +
        '<li><a hx-get="/pulses?rows=1&per_page=50" class="' + (ch === '' ? 'active' : '') + '">All</a></li>' +
        '<li><a hx-get="/pulses?rows=1&channel=xy&per_page=50" class="' + (ch === 'xy' ? 'active' : '') + '">XY</a></li>' +
        '</ul></nav>' +
        '<input type="hidden" id="pulses-owner-pick" value="' + owner + '">' +
        '<div id="pulses-rows-wrap" hx-get="/pulses?rows=1&per_page=50"></div>';
    doc.getElementById('pending-tray').setAttribute('data-seq', '11');
    swapTo('/pulses', PULSES('x180', 'xy', 'q2'));
    doc.getElementById('pending-tray').setAttribute('data-seq', '12');   // an edit: SOFT tier
    swapTo('/bulk', '<div>bulk</div>');
    const rowsCalls = [];
    window.htmx.ajax = (verb, path, opts) => { rowsCalls.push(path); return Promise.resolve(); };
    let inputs = 0;
    swapTo('/pulses', PULSES('', '', ''));
    const q = doc.getElementById('pulses-q');
    ok(q.value === 'x180', 'SOFT: the search text came back on the fresh DOM');
    const active = doc.querySelector('#pulse-channel-tabs a.active');
    ok(!!active && /channel=xy/.test(active.getAttribute('hx-get')),
       'SOFT: the XY channel tab came back too (was silently reset to All)');
    ok(doc.getElementById('pulses-owner-pick').value === 'q2', 'SOFT: the owner pick came back');
    ok(rowsCalls.length === 1, 'the rows are refetched once (the search box fetches on keyup, not on a dispatched input)');

    // no text to restore -> the tab alone must still refetch the rows once
    swapTo('/bulk', '<div>bulk</div>');
    doc.getElementById('pending-tray').setAttribute('data-seq', '13');
    swapTo('/pulses', PULSES('', '', ''));
    // establish a text-less filter: tab Z active, then leave and come back
    pane().querySelector('#pulse-channel-tabs ul').insertAdjacentHTML('beforeend',
        '<li><a hx-get="/pulses?rows=1&channel=z&per_page=50" class="">Z</a></li>');
    pane().querySelectorAll('#pulse-channel-tabs a').forEach(a => a.classList.remove('active'));
    pane().querySelector('a[hx-get*="channel=z"]').classList.add('active');
    pane().querySelector('#pulses-q').value = '';            // the user cleared the box
    pane().querySelector('#pulses-owner-pick').value = '';   // and the owner pick
    doc.getElementById('pending-tray').setAttribute('data-seq', '14');
    swapTo('/bulk', '<div>bulk</div>');
    doc.getElementById('pending-tray').setAttribute('data-seq', '15');   // an edit: SOFT, not KEEP
    rowsCalls.length = 0;
    swapTo('/pulses', PULSES('', '', '') .replace('</ul>', '<li><a hx-get="/pulses?rows=1&channel=z&per_page=50" class="">Z</a></li></ul>'));
    ok(rowsCalls.length === 1 && /[/]pulses[?]rows=1/.test(rowsCalls[0]),
       'a restored tab with no search text refetches the rows exactly once (got ' + JSON.stringify(rowsCalls) + ')');
    const z = doc.querySelector('#pulse-channel-tabs a.active');
    ok(!!z && /channel=z/.test(z.getAttribute('hx-get')), 'the Z tab is active on the fresh DOM');
    // leave the harness where the next section expects it: on /explorer
    window.htmx.ajax = () => Promise.resolve();
    swapTo('/explorer', '<div id="fresh-exp-after-pulses">fresh</div>');
}

// ── 5. same-route refresh only refreshes the SOFT capture, never parks ─────
pane().innerHTML = '<input type="search" id="explorer-search" class="tree-search" value="qA5">';
{
    const before = new window.CustomEvent('htmx:beforeSwap', {
        cancelable: true,
        detail: { shouldSwap: true, pathInfo: { finalRequestPath: '/explorer?depth=3' } },
    });
    Object.defineProperty(before, 'target', { value: pane() });
    const sizeBefore = Object.keys(window.PaneState._stash()).length;
    doc.dispatchEvent(before);
    ok(Object.keys(window.PaneState._stash()).length === sizeBefore,
       'a same-route request never parks');
    const cap = window.PaneState._soft()['/explorer'];
    ok(cap && cap.inputs.some(i => i.value === 'qA5'),
       'a same-route refresh recaptures the CURRENT query (a cleared box stays cleared)');
}

// ── 6. popstate / historyRestore clear the stash AND re-sync the route ─────
swapTo('/bulk', '<div>bulk</div>');                       // parks explorer again
ok(Object.keys(window.PaneState._stash()).length === 1, 'parked before Back');
window.dispatchEvent(new window.CustomEvent('popstate'));
ok(Object.keys(window.PaneState._stash()).length === 0, 'Back clears the stash');
ok(window.PaneState._cur() === window.location.pathname,
   'Back re-syncs the current route (the v1 wrong-DOM-under-/explorer bug)');

// ── 7. stateRestored (wholesale replace) clears every parked pane ──────────
window.PaneState.clear();
swapTo('/explorer', '<div>x</div>');
swapTo('/bulk', '<div>bulk</div>');
ok(Object.keys(window.PaneState._stash()).length === 1, 'parked');
doc.dispatchEvent(new window.CustomEvent('stateRestored'));
ok(Object.keys(window.PaneState._stash()).length === 0,
   'stateRestored clears every parked pane');

// -- 8. docs/139 fix 1: a fresh parked KEEP copy skips the fetch entirely --
// The v2 doctrine amendment: the request is cancelled at htmx:beforeRequest
// and the parked DOM restored synchronously; a stale copy or a same-route
// click still fetches normally, so the amendment never widens.
function requestTo(route) {
    const evt = new window.CustomEvent('htmx:beforeRequest', {
        cancelable: true,
        detail: { target: pane(), requestConfig: { verb: 'get', path: route },
                  pathInfo: { finalRequestPath: route } },
    });
    doc.dispatchEvent(evt);
    return evt;
}
window.PaneState.clear();
swapTo('/bulk', '<div id="bulk-live">bulk grid</div>');
swapTo('/explorer',
       '<input type="search" id="explorer-search" class="tree-search" value="zz">');
// parked: the bulk grid. Returning to it must not cost a request.
let req = requestTo('/bulk');
ok(req.defaultPrevented, 'skip-fetch: the request to a fresh KEEP copy is cancelled');
ok(!!doc.getElementById('bulk-live'),
   'skip-fetch: the bulk grid is restored synchronously, no fetch (docs/139)');
ok(window.PaneState._cur() === '/bulk', 'skip-fetch: the route tracked the skip');
ok(window.location.pathname === '/bulk', 'skip-fetch: the URL was pushed');
ok(!!window.PaneState._stash()['/explorer'],
   'skip-fetch: the outgoing pane was parked on the way');
req = requestTo('/explorer');
ok(req.defaultPrevented, 'skip-fetch: /explorer rides the same amendment');
ok(!!doc.getElementById('explorer-search'),
   'skip-fetch: the explorer came back with its search text');
doc.getElementById('pending-tray').setAttribute('data-seq', '11');   // an edit
req = requestTo('/bulk');
ok(!req.defaultPrevented, 'skip-fetch: a STALE copy fetches normally');
req = requestTo('/explorer');
ok(!req.defaultPrevented,
   'skip-fetch: a same-route click is a deliberate refresh and always fetches');

// A cancelled request must reach NO downstream listener: NavProgress counts
// beforeRequest and only clears on a terminal xhr event that never comes, so
// its brand progress bar ticked forever and polled /api/progress every 350ms
// (customer-reported at 154 s). Registered-first + stopImmediatePropagation
// is what keeps the cancel truthful to the rest of the app.
{
    let downstream = 0;
    document.addEventListener('htmx:beforeRequest', () => downstream++);
    window.PaneState.clear();
    swapTo('/bulk', '<div id="bulk-live2">grid</div>');
    swapTo('/explorer', '<div>x</div>');
    downstream = 0;
    const r = requestTo('/bulk');
    ok(r.defaultPrevented, 'cancel-propagation: the skip still cancels');
    ok(downstream === 0,
       'a cancelled nav reaches NO downstream beforeRequest listener (got '
       + downstream + ')');
    // a NORMAL (non-skip) nav must still reach them
    downstream = 0;
    requestTo('/param-history');
    ok(downstream === 1, 'a real nav still reaches downstream listeners');
}

// -- 8b. docs/141 4ae (CRITICAL): park reads the offsets BEFORE detaching --
// _park moved every child out of the pane and THEN read scrollTop/scrollLeft.
// An element with nothing left to overflow is clamped to 0,0 by the browser,
// so the capture was 0 every time: measured in real Chrome on the 20Q chip
// (247 columns, 53,000 px wide) as parked {3000,400} -> captured {0,0} ->
// restored {0,0}, i.e. EVERY keep-route return landed in the top-left corner
// and 4ac's scrollX was a no-op from the day it was added.
// jsdom has no layout and answers the stored number whether the element has
// children or not, so the pane is given the browser's rule here — without
// this emulation the pin below passes on the broken code too.
function fakeScroll(el) {
    var st = 0, sl = 0, writes = [];
    Object.defineProperty(el, 'scrollTop', {
        configurable: true,
        get: function () { return el.firstChild ? st : 0; },
        set: function (v) { st = el.firstChild ? (Number(v) || 0) : 0; writes.push('top:' + st); },
    });
    Object.defineProperty(el, 'scrollLeft', {
        configurable: true,
        get: function () { return el.firstChild ? sl : 0; },
        set: function (v) { sl = el.firstChild ? (Number(v) || 0) : 0; writes.push('left:' + sl); },
    });
    return writes;
}
{
    // the emulation itself, on a scratch node: emptied ⇒ 0 (the Chrome rule)
    const probe = doc.createElement('div');
    probe.innerHTML = '<i>x</i>';
    fakeScroll(probe);
    probe.scrollTop = 400; probe.scrollLeft = 3000;
    const probeBefore = probe.scrollTop + '/' + probe.scrollLeft;
    while (probe.firstChild) probe.removeChild(probe.firstChild);
    ok(probeBefore === '400/3000' && probe.scrollTop === 0 && probe.scrollLeft === 0,
       'scroll-restore: the harness pane clamps to 0,0 when emptied, like a browser');

    const p0 = pane();
    const writes = fakeScroll(p0);
    window.PaneState.clear();
    swapTo('/bulk', '<div id="bulk-scrolled">grid</div>');
    p0.scrollTop = 400; p0.scrollLeft = 3000;          // the user scrolls the grid
    swapTo('/explorer', '<div id="ex-fresh">explorer</div>');   // park /bulk
    const parked = window.PaneState._stash()['/bulk'];
    ok(!!parked && parked.scroll === 400 && parked.scrollX === 3000,
       'park captures the pane offsets BEFORE the children are detached (got '
       + JSON.stringify(parked ? { scroll: parked.scroll, scrollX: parked.scrollX } : null)
       + ', want {scroll:400,scrollX:3000})');
    let atRestore = null;
    const onRestored = function () {
        atRestore = { st: p0.scrollTop, sl: p0.scrollLeft, writes: writes.length };
    };
    doc.addEventListener('paneRestored', onRestored);
    writes.length = 0;
    swapTo('/bulk', '<div id="fresh-bulk">fresh server render</div>');
    doc.removeEventListener('paneRestored', onRestored);
    ok(!doc.getElementById('fresh-bulk'), 'scroll-restore: the parked grid came back');
    ok(writes.indexOf('top:400') >= 0 && writes.indexOf('left:3000') >= 0,
       'restore WRITES both offsets back onto the pane (got ' + JSON.stringify(writes) + ')');
    ok(p0.scrollTop === 400 && p0.scrollLeft === 3000,
       'the pane lands where the user left it (got ' + p0.scrollTop + '/' + p0.scrollLeft + ')');
    // docs/141 4ac: bulk-edit.js re-derives the toolbar rows' translateX from
    // #table-pane.scrollLeft inside this very handler, so the sideways
    // position has to be on the pane BEFORE the event, not after.
    ok(!!atRestore && atRestore.sl === 3000 && atRestore.st === 400,
       'both offsets are already in place when paneRestored fires (got '
       + JSON.stringify(atRestore) + ')');
}

// -- 8c. QA F-15: a page you OPEN starts at the top --------------------------
// #table-pane is persistent, so a new route kept the outgoing page's
// scrollTop, clamped to its own maximum: Diagnostics opened from a scrolled
// Chip Status (tile, warnings chip, sidebar) showed its bottom, the title
// above the fold. Measured live: 860 -> 298 (the Diagnostics maximum).
{
    const p0 = pane();              // still carries 8b's browser-rule emulation
    window.PaneState.clear();
    swapTo('/topology', '<div id="cs-long">chip status</div>');
    p0.scrollTop = 860; p0.scrollLeft = 40;             // the user scrolled Chip Status
    swapTo('/diagnostics', '<div id="diag-page">diagnostics</div>');
    ok(p0.scrollTop === 0 && p0.scrollLeft === 0,
       'F-15: a NEW route opens at the top (got ' + p0.scrollTop + '/' + p0.scrollLeft + ')');
    // a same-route refresh (the findings self-refresh, pagination) keeps its place
    p0.scrollTop = 200;
    swapTo('/diagnostics', '<div id="diag-page2">diagnostics again</div>');
    ok(p0.scrollTop === 200, 'F-15: a same-route refresh keeps its place (got ' + p0.scrollTop + ')');
    // a POST answering its own page (Datasets' Rescan -> /datasets/rescan)
    swapTo('/datasets', '<div>datasets</div>');
    p0.scrollTop = 500;
    const before = new window.CustomEvent('htmx:beforeSwap', { cancelable: true,
        detail: { shouldSwap: true, requestConfig: { verb: 'post' },
                  pathInfo: { finalRequestPath: '/datasets/rescan' } } });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    pane().innerHTML = '<div>rescanned table</div>';
    const after = new window.CustomEvent('htmx:afterSwap', {
        detail: { requestConfig: { verb: 'post' },
                  pathInfo: { finalRequestPath: '/datasets/rescan' } } });
    Object.defineProperty(after, 'target', { value: pane() });
    doc.dispatchEvent(after);
    ok(p0.scrollTop === 500, 'F-15: a POST answering its own page keeps its place (got '
       + p0.scrollTop + ')');
    window.PaneState.clear();
}

// -- 9. Back after a skip-nav: content-route mismatch refetches (docs/139) --
// Measured live before the fix: the skip pushes URLs htmx has no snapshot
// for, so Back left /bulk's grid standing under /explorer -- not blank, so
// the old empty-pane-only fallback never fired.
setTimeout(() => {   // let earlier scenarios' 60ms fallback timers drain first
    const calls = [];
    window.htmx.ajax = (verb, path) => { calls.push(path); return Promise.resolve(); };
    // pane holds /bulk content (stamped); the URL is somewhere else
    pane().setAttribute('data-pane-route', '/bulk');
    window.history.pushState({}, '', '/explorer');
    window.dispatchEvent(new window.CustomEvent('popstate'));
    setTimeout(() => {
        ok(calls.length === 1 && calls[0].indexOf('/explorer') === 0,
           'Back with a route-mismatched pane refetches the URL route (got '
           + JSON.stringify(calls) + ')');
        // an unstamped pane (full page load) with content is left alone
        const calls2 = [];
        window.htmx.ajax = (verb, path) => { calls2.push(path); return Promise.resolve(); };
        pane().removeAttribute('data-pane-route');
        pane().innerHTML = '<div>server-rendered</div>';
        window.dispatchEvent(new window.CustomEvent('popstate'));
        setTimeout(() => {
            // (QA diagnostics-r2-07 narrowed this to ROUTE identity: the
            // freshness probe below may still refetch it when the server's
            // seq moved -- here the probe's fetch never resolves.)
            ok(calls2.length === 0, 'an unstamped full-load pane is never refetched for its route');
            section10();
        }, 90);
    }, 90);
}, 80);

// -- 10. QA diagnostics-r2-07: Back restores a snapshot that is BEHIND --
// htmx's history snapshot is the whole body as the user left it: the tray's
// data-seq/count and the pane's values. Measured live: Back to /diagnostics
// after an edit on /bulk showed the old tray count and a fix confirm quoting
// the old value. The restored tray's seq vs the server's decides.
function section10() {
    const trayHtml = (seq) => '<div id="pending-tray" data-seq="' + seq + '"></div>';
    const probe = (seq) => {
        const got = [];
        // app.js runs in the Node realm here, so bare `fetch` is global.fetch
        global.fetch = window.fetch = (url) => {
            got.push(String(url));
            return Promise.resolve({ ok: true, text: () => Promise.resolve(trayHtml(seq)) });
        };
        return got;
    };
    const calls = [];
    window.htmx.ajax = (verb, p, opts) => { calls.push({ p: p, opts: opts || {} }); return Promise.resolve(); };
    let diagChanged = 0;
    const realDiag = window._diagChanged;
    window._diagChanged = () => { diagChanged++; };
    doc.getElementById('pending-tray').setAttribute('data-seq', '7');
    pane().removeAttribute('data-pane-route');
    pane().innerHTML = '<div>restored /diagnostics snapshot</div>';
    window.history.pushState({}, '', '/diagnostics');
    const fetched = probe('9');                            // the server moved on
    window.dispatchEvent(new window.CustomEvent('popstate'));
    doc.body.dispatchEvent(new window.CustomEvent('htmx:historyRestore', { bubbles: true }));
    setTimeout(() => {
        ok(fetched.filter((u) => u.indexOf('/state/tray') === 0).length === 1,
           'Back: ONE server probe of the tray for popstate + historyRestore (got '
           + JSON.stringify(fetched) + ')');
        const tray = calls.filter((c) => c.p === '/state/tray');
        const paneRe = calls.filter((c) => c.p.indexOf('/diagnostics') === 0);
        ok(tray.length === 1 && tray[0].opts.target === '#pending-tray'
           && tray[0].opts.swap === 'outerHTML',
           'a restored tray behind the server is re-rendered from it (got ' + JSON.stringify(calls) + ')');
        ok(paneRe.length === 1 && paneRe[0].opts.target === '#table-pane',
           'a restored pane behind the server is refetched, stamped or not (got '
           + JSON.stringify(calls) + ')');
        ok(diagChanged === 1, 'the badge + banner re-lint through the one announcer');
        // equal seq: the snapshot is current -- nothing moves
        calls.length = 0; diagChanged = 0;
        window.history.pushState({}, '', '/pulses');
        const fetched2 = probe('7');
        window.dispatchEvent(new window.CustomEvent('popstate'));
        setTimeout(() => {
            ok(fetched2.length === 1 && calls.length === 0 && diagChanged === 0,
               'a restored snapshot at the server seq is left alone (got '
               + JSON.stringify({ fetched: fetched2, calls: calls, diagChanged: diagChanged }) + ')');
            window._diagChanged = realDiag;
            process.exit(fails ? 1 : 0);
        }, 150);
    }, 150);
}
