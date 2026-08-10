/* jsdom selfcheck for PaneState (docs/110 #10-A) — a tab keeps its state.
 *
 * Pins:
 *  1. leaving a KEEP route parks the pane DOM; returning with the SAME
 *     seq+chip cancels the request and re-attaches it (search text, expanded
 *     <details>, everything survives);
 *  2. a moved mutation_seq (the tray's data-seq beacon) forces a REFETCH —
 *     never a stale restore — and the SOFT tier re-applies the search text
 *     over the fresh DOM (input value + a re-dispatched 'input');
 *  3. a chip switch forces a refetch;
 *  4. stateRestored (wholesale replace) clears every parked pane;
 *  5. same-route requests (chain tabs / pagination) are never intercepted.
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
global.fetch = () => new Promise(() => {});
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

function nav(pathTo) {
    // CustomEvent.detail is constructor-only (readonly afterwards) — real
    // htmx builds its events the same way.
    const ev = new window.CustomEvent('htmx:beforeRequest', {
        cancelable: true,
        detail: { requestConfig: { verb: 'get', path: pathTo }, target: pane() },
    });
    doc.dispatchEvent(ev);
    return ev;
}
function swapDone(pathTo) {
    const ev = new window.CustomEvent('htmx:afterSwap', {
        detail: { pathInfo: { finalRequestPath: pathTo } },
    });
    Object.defineProperty(ev, 'target', { value: pane() });
    doc.dispatchEvent(ev);
}

// ── 1. park + fresh restore ─────────────────────────────────────────────────
let ev = nav('/bulk');
ok(!ev.defaultPrevented, 'leaving /explorer lets the /bulk request run');
ok(pane().children.length === 0, 'the explorer DOM was parked out of the pane');
pane().innerHTML = '<div id="bulk-stub">bulk</div>';   // the server swap
swapDone('/bulk');

ev = nav('/explorer');
ok(ev.defaultPrevented, 'returning with the same seq+chip cancels the refetch');
const inp = doc.getElementById('explorer-search');
ok(!!inp && inp.value === 'f_01', 'the search text survived the round trip');
ok(!!doc.getElementById('sec-a') && doc.getElementById('sec-a').open,
   'the expanded section survived the round trip');

// ── 5. same-route refresh is never intercepted ─────────────────────────────
ev = nav('/explorer?depth=3');
ok(!ev.defaultPrevented, 'a same-route request (refresh/filter) is not intercepted');

// ── 2. stale seq ⇒ refetch + SOFT re-apply ─────────────────────────────────
ev = nav('/bulk');                              // park explorer again (seq 7)
pane().innerHTML = '<div id="bulk-stub">bulk</div>';
swapDone('/bulk');
doc.getElementById('pending-tray').setAttribute('data-seq', '9');   // an edit happened
ev = nav('/explorer');
ok(!ev.defaultPrevented, 'a moved mutation_seq forces a REFETCH (never stale restore)');
// the fresh server swap arrives WITHOUT the query…
pane().innerHTML = '<input type="search" id="explorer-search" class="tree-search" value="">';
let inputFired = 0;
pane().querySelector('#explorer-search').addEventListener('input', () => inputFired++);
swapDone('/explorer');
ok(pane().querySelector('#explorer-search').value === 'f_01',
   'SOFT tier re-applied the search text over the fresh DOM');
ok(inputFired === 1, 'the re-applied query re-dispatched input (filter re-runs)');

// ── 3. chip switch ⇒ refetch ───────────────────────────────────────────────
doc.getElementById('pending-tray').setAttribute('data-seq', '9');
ev = nav('/bulk');                               // park explorer (seq 9, chipA)
pane().innerHTML = '<div>bulk</div>';
swapDone('/bulk');
window.__chipToken = 'chipB';
ev = nav('/explorer');
ok(!ev.defaultPrevented, 'a chip switch forces a refetch');
pane().innerHTML = '<div>fresh explorer</div>';
swapDone('/explorer');

// ── 4. stateRestored clears the stash ──────────────────────────────────────
window.__chipToken = 'chipB';
ev = nav('/bulk');                               // park explorer
pane().innerHTML = '<div>bulk</div>';
swapDone('/bulk');
ok(Object.keys(window.PaneState._stash()).length === 1, 'explorer is parked');
doc.dispatchEvent(new window.CustomEvent('stateRestored'));
ok(Object.keys(window.PaneState._stash()).length === 0,
   'stateRestored (wholesale replace) clears every parked pane');

process.exit(fails ? 1 : 0);
