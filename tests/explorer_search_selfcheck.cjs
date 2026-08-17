/* jsdom selfcheck for docs/122 item 2 — the Json Tree View keeps its search.
 *
 * Customer report: the search resets by itself, and turning "view diff" on
 * leaves it broken. Measured on the real 20-qubit chip before the fix: with
 * `amplitude` in the box, turning live diff ON took the tree to 189 visible
 * rows of which 189 did NOT match the query, the box still reading `amplitude`;
 * re-typing the same value restored the filter WITHOUT leaving diff mode, which
 * is what proved the search itself was fine and simply never called.
 *
 * The pins here are the contract, not the incident:
 *   1. renderJsonTree clears _lastSearchQuery -- so every caller owns the
 *      re-apply, and explorerLiveDiff must be one of them
 *   2. expansion survives a rebuild, addressed by dot-path and BOUNDED
 *   3. the expansion restore is depth-ordered (a child cannot be reached before
 *      its parent exists) and idempotent
 *   4. PaneState's SOFT capture for /explorer carries tab + expansion + scroll,
 *      not just the search text
 *
 * Run: node tests/explorer_search_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
// Node 24 defines globalThis.navigator as a getter-only accessor. The older
// selfchecks assign it and get away with it because they are sloppy-mode; this
// file is 'use strict', where the same assignment THROWS. defineProperty is the
// form that works in both.
Object.defineProperty(global, 'navigator',
                      { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// Same getter-only story as `navigator` above, on both objects. Worth recording:
// the older selfchecks assign these in sloppy mode, where the assignment is a
// silent no-op — so their `window.localStorage` is jsdom's real one, not the
// stub they think they installed.
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
global.fetch = () => new Promise(() => {});
Object.defineProperty(window, 'fetch',
                      { value: global.fetch, configurable: true, writable: true });
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
const settle = () => new Promise((r) => setTimeout(r, 320));

const DATA = {
    qubits: {
        q1: { xy: { amplitude: 0.1, length: 40 }, f_01: 4.3e9 },
        q2: { xy: { amplitude: 0.2, length: 40 }, f_01: 4.6e9 },
    },
    wiring: { a: 1 },
};

(async function main() {

// ── 0. the property every caller depends on ────────────────────────────────
const host = window.document.createElement('div');
host.id = 'explorer-tree-state';
window.document.body.appendChild(host);
const wiringHost = window.document.createElement('div');
wiringHost.id = 'explorer-tree-wiring';
window.document.body.appendChild(wiringHost);

window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1 });
ok(host._treeData === DATA, 'renderJsonTree stashes the source object');
ok(host._lastSearchQuery === undefined,
   'renderJsonTree CLEARS _lastSearchQuery — a rebuild owns no search, so every '
   + 'caller owes a re-apply (this is why explorerLiveDiff had to be fixed)');

// ── 1. expansion is capturable by path and restorable ──────────────────────
window.jsonTreeExpandAll('explorer-tree-state');
const allExpanded = window.jsonTreeExpandedPaths('explorer-tree-state');
ok(allExpanded.length > 0, 'jsonTreeExpandedPaths reports expanded nodes ('
   + allExpanded.length + ')');
ok(allExpanded.every((p) => typeof p === 'string' && p.length),
   'every recorded handle is a dot-path, never a DOM index');
ok(allExpanded.indexOf('qubits') >= 0 && allExpanded.indexOf('qubits.q1') >= 0,
   'nested paths are recorded, not just top level');

// rebuild from scratch: this is exactly what an /explorer refetch does
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1 });
ok(window.jsonTreeExpandedPaths('explorer-tree-state').length < allExpanded.length,
   'a rebuild collapses the tree (the loss the customer reported)');

const restored = window.jsonTreeSetExpanded('explorer-tree-state', allExpanded);
ok(restored > 0, 'jsonTreeSetExpanded re-opens nodes (' + restored + ')');
const after = window.jsonTreeExpandedPaths('explorer-tree-state');
ok(allExpanded.every((p) => after.indexOf(p) >= 0),
   'every previously expanded path is expanded again');

// ── 2. depth order is what makes ONE pass enough ───────────────────────────
// A child node does not exist in the DOM until its parent is expanded, so a
// deepest-first restore would silently drop the deep half.
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1 });
const reversed = allExpanded.slice().sort((a, b) => b.length - a.length);
window.jsonTreeSetExpanded('explorer-tree-state', reversed);
const afterRev = window.jsonTreeExpandedPaths('explorer-tree-state');
ok(allExpanded.every((p) => afterRev.indexOf(p) >= 0),
   'restore is order-independent — it sorts shallowest-first itself');

// idempotent: restoring twice must not toggle anything closed again
const n1 = window.jsonTreeExpandedPaths('explorer-tree-state').length;
window.jsonTreeSetExpanded('explorer-tree-state', allExpanded);
ok(window.jsonTreeExpandedPaths('explorer-tree-state').length === n1,
   'restoring an already-expanded set is a no-op, never a toggle');

// ── 3. the capture is bounded ──────────────────────────────────────────────
// A fully expanded real chip is ~7,800 rows; an unbounded capture+restore would
// cost more than the rebuild it repairs.
ok(/_EXPAND_CAP\s*=\s*\d+/.test(src), 'the expansion capture declares a cap');
const cap = Number((src.match(/_EXPAND_CAP\s*=\s*(\d+)/) || [])[1]);
ok(cap > 0 && cap <= 5000, 'the cap is a real bound (' + cap + ')');

// ── 4. explorerLiveDiff re-applies the search ──────────────────────────────
// Pinned at the source level: the ON branch must reach the re-apply after it
// re-renders. A behavioural pin would need the whole /state/live-diff round
// trip, which is what the browser probe does; this one guarantees the call site
// cannot be deleted silently.
const diffFn = String(window.explorerLiveDiff);
ok(/_explorerReapplySearch\s*\(/.test(src),
   'a re-apply helper exists and is called');
// An ORDER contract, not a character budget: the re-apply must run after the
// incoming rows are tagged (so the filter judges the rows the user will see)
// and before the toggle is marked active (so no frame exists in which the box
// shows a query the tree is not honouring).
const iTag = src.indexOf('_autoExpandAndTag("explorer-tree-state"');
const iApply = src.indexOf('_explorerReapplySearch()', iTag);
const iArm = src.indexOf('_explorerLiveDiffOn = true', iTag);
ok(iTag > 0 && iApply > iTag, 'the re-apply runs AFTER the incoming rows are tagged');
ok(iArm > 0 && iApply < iArm,
   'the re-apply runs BEFORE the toggle is armed — no frame where the box lies');
ok(typeof window.explorerSearch === 'function',
   'the search box has ONE entry point (explorerSearch), so the filter and the '
   + 'diff bar cannot disagree about what is on screen');
ok(diffFn.indexOf('_softRefreshLiveSurface') > 0,
   'turning diff OFF still refetches (accepted values made the client copy stale)');

// ── 5. PaneState's SOFT capture carries more than the text ─────────────────
ok(/_captureSoft\(p,\s*route\)/.test(src) && /_captureSoft\(pane\(\),\s*_cur\)/.test(src),
   'both capture sites pass the route, so /explorer can be recognised');
ok(/route === '\/explorer'/.test(src) && /_captureExplorer\(\)/.test(src),
   'the /explorer capture is route-gated, not applied to every SOFT surface');
['tab:', 'expanded:', 'scroll:', 'pane:'].forEach(function (k) {
    ok(src.indexOf(k) > 0, 'the capture records ' + k.replace(':', ''));
});
ok(/jsonTreeSetExpanded\(id, d\.expanded/.test(src),
   'the restore feeds the recorded paths back through the public helper');

process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
