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
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
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
ok(Object.keys(window.PaneState._stash()).length === 0, 'the stale parked copy was dropped');

// ── 4. chip switch ⇒ fresh swap wins ───────────────────────────────────────
swapTo('/bulk', '<div>bulk</div>');                       // park explorer (seq 9)
window.__chipToken = 'chipB';
swapTo('/explorer', '<div id="fresh2">fresh</div>');
ok(!!doc.getElementById('fresh2'), 'a chip switch keeps the fresh render');

// ── 5. same-route refresh only refreshes the SOFT capture, never parks ─────
pane().innerHTML = '<input type="search" id="explorer-search" class="tree-search" value="qA5">';
{
    const before = new window.CustomEvent('htmx:beforeSwap', {
        cancelable: true,
        detail: { shouldSwap: true, pathInfo: { finalRequestPath: '/explorer?depth=3' } },
    });
    Object.defineProperty(before, 'target', { value: pane() });
    doc.dispatchEvent(before);
    ok(Object.keys(window.PaneState._stash()).length === 0,
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

process.exit(fails ? 1 : 0);
