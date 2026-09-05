/* Opening a node the SEARCH kept must show that node's values.
 *
 * Customer, 2026-09-05: "search `port`, then press the arrow on
 * ports.analog_outputs.con1.7.1 and NOTHING happens. 아주 bad UX." Reproduced in
 * real Chrome on the 20-qubit chip before the fix:
 *
 *   childrenInDom: 16   computedDisplayOfFirstChild: "none"
 *   arrow ▼ -> click ▶ -> click ▼      childRowsPainted: 0, 0, 0
 *
 * The search HIDES non-matching rows with a per-node class and renders every
 * KEPT node expanded; `_toggleNode` only flips the wrapper and never clears
 * that class. So the arrow reads open over children that are all display:none,
 * and the click that looks like "expand" is really a collapse of something
 * already invisible.
 *
 * The rule pinned here: while a search is filtering, the arrow on a kept row
 * means "show me this row's values" -- match or not -- one level per click.
 *
 * EXECUTED, not grepped: a source-only pin for this would survive `if (false)`.
 *
 * Run: node tests/tree_expand_search_selfcheck.cjs   (needs jsdom)
 */
'use strict';
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM(
    '<!doctype html><html><body><div class="explorer-pane">'
    + '<input type="search" id="explorer-search">'
    + '<div id="explorer-tree-state" class="json-tree"></div></div></body></html>',
    { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
// docs/125's standing rule: the Node realm exposes NO window property as a
// bare global, and app.js reads several of them bare -- a miss throws rather
// than degrading. Same bridge list as tree_edit_literal_selfcheck.cjs.
global.window = window;
global.document = window.document;
global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event;
global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
global.MutationObserver = window.MutationObserver;
Object.defineProperty(global, 'navigator',
    { value: window.navigator, configurable: true });
Object.defineProperty(global, 'location',
    { value: window.location, configurable: true });
// jsdom's window.localStorage is getter-only, so assigning it throws under
// 'use strict' (the older selfchecks get away with it in sloppy mode).
const _fakeStore = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.localStorage = _fakeStore;
global.sessionStorage = _fakeStore;
Object.defineProperty(window, 'localStorage', { value: _fakeStore, configurable: true });
Object.defineProperty(window, 'sessionStorage', { value: _fakeStore, configurable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
window.eval('fetch = window.fetch = function () {'
    + ' return Promise.resolve({ json: function () {'
    + '   return Promise.resolve({ expected: {} }); } }); };');
global.fetch = window.fetch;
window.showToast = () => {};

// The real chip reaches this state through the materialise CAP: `port` matched
// 1,267 paths, the first 150 were kept, and everything past that got the
// hidden class -- including every child of the node the customer clicked. The
// app exposes that limit as an override precisely so a small fixture can drive
// the same code path; 5 puts the boundary just under the target's children.
// Set BEFORE app.js is evaluated: the constant is read at module scope.
window.__treeSearchMaterializeMax = 5;

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

// The customer's shape: a container whose OWN key matches the query, holding
// children that do NOT. `port` matches `ports` and every descendant path, so
// the leaf names below are what the filter has to decide about.
window.renderJsonTree('explorer-tree-state', {
    ports: {
        analog_outputs: {
            con1: {
                7: {
                    1: {
                        offset: 0.0,
                        delay: 0,
                        // a nested CONTAINER with real children: the
                        // reveal must stop at one level, and an empty {} could
                        // not tell the difference
                        crosstalk: { con1_2: 0.01, con1_3: 0.02, con1_4: 0.03 },
                        feedforward_filter: [],
                        feedback_filter: [],
                        shareable: false,
                    },
                },
            },
        },
    },
    qubits: { q1: { f_01: 4.5e9 } },
}, { defaultDepth: 8, crud: true });

const d = window.document;
const TARGET = 'ports.analog_outputs.con1.7.1';

(async () => {
    window.jsonTreeSearch('explorer-tree-state', 'port');
    await new Promise((r) => setTimeout(r, 320));   // past the 200ms debounce

    const node = d.querySelector('.tree-node[data-path="' + TARGET + '"]');
    ok(!!node, 'the searched-for container is in the tree');
    if (!node) { console.error('FAILED: ' + fails); process.exit(1); }

    const kids = node.querySelector(':scope > .tree-children');
    const toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
    ok(!!kids && !!toggle, 'it has a children wrapper and an arrow');

    const hiddenCount = () =>
        node.querySelectorAll(':scope > .tree-children > .tree-search-hidden').length;
    const shownCount = () => {
        let n = 0;
        for (const c of kids.children) {
            if (!c.classList.contains('tree-search-hidden')) n++;
        }
        return n;
    };

    const total = kids.children.length;
    ok(total > 0, 'and real children (' + total + ')');
    // This is the customer's state: children present, every one filtered out.
    ok(hiddenCount() === total,
       'the search filtered every child out (' + hiddenCount() + ' of ' + total + ')');

    // ── the gesture ───────────────────────────────────────────────────────
    // Collapse first. Search renders a KEPT node expanded, so clicking it while
    // it is already open cannot tell "the reveal opened the wrapper" from "the
    // wrapper was open all along" -- which is exactly how the first version of
    // this pin passed against a mutation that never opened anything.
    kids.style.display = 'none';
    toggle.textContent = '\u25B6';
    toggle.classList.add('collapsed');
    toggle.classList.remove('expanded');
    ok(kids.style.display === 'none', 'the node starts collapsed for this check');

    toggle.click();
    ok(shownCount() === total,
       'ONE click shows them all, matching or not (' + shownCount() + ' of ' + total + ')');
    ok(hiddenCount() === 0, 'and none is left hidden');
    ok(toggle.textContent === '▼', 'the arrow reads open');
    ok(kids.style.display !== 'none', 'and the wrapper is open');
    ok(node.querySelectorAll(':scope > .tree-children > .tree-search-revealed').length === total,
       'the revealed rows are MARKED revealed, so they cannot be read as hits');

    // ── and it is still an arrow ──────────────────────────────────────────
    toggle.click();
    ok(kids.style.display === 'none' && toggle.textContent === '▶',
       'a second click collapses, like any other node');
    toggle.click();
    ok(kids.style.display !== 'none' && toggle.textContent === '▼',
       'and a third opens again');

    // ── the search itself is untouched ────────────────────────────────────
    ok(d.querySelectorAll('.tree-highlight').length > 0,
       'the search highlight survives the reveal');
    const unrelated = d.querySelector('.tree-node[data-path="qubits"]');
    ok(!!unrelated && unrelated.classList.contains('tree-search-hidden'),
       'and a branch the query never matched is still filtered out');

    // ── one level per click ───────────────────────────────────────────────
    // A revealed CONTAINER keeps its own hidden set, so the reveal cannot cost
    // a whole subtree on one press.
    const nested = d.querySelector('.tree-node[data-path="ports.analog_outputs.con1.7.1.crosstalk"]');
    ok(!!nested, 'the fixture has a nested container to check the depth against');
    const nk = nested && nested.querySelector(':scope > .tree-children');
    const ntog = nested && nested.querySelector(':scope > .tree-row > .tree-toggle');
    const grandkidsShown = () => {
        if (!nk) return 0;
        let n = 0;
        for (const c of nk.children) {
            if (!c.classList.contains('tree-search-hidden')) n++;
        }
        return n;
    };
    // The press above revealed ONE level. The grandchildren must not have come
    // with it -- either they are not built yet (the node is lazy) or they are
    // still filtered out. A subtree reveal would have shown them.
    ok(grandkidsShown() === 0,
       'one press does not descend: no grandchild is showing yet ('
       + grandkidsShown() + ')');
    // ...and the chain works: pressing the nested row opens that level.
    ok(!!ntog, 'the nested container has its own arrow');
    if (ntog) {
        ntog.click();
        ok(grandkidsShown() > 0,
           'pressing the nested row opens ITS level (' + grandkidsShown() + ')');
    }

    // ── the muted step is a contract, not decoration ──────────────────────
    const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
    ok(/\.tree-search-revealed\s*>\s*\.tree-row\s*\{[^}]*opacity/.test(css),
       'a revealed row is visually distinguished from a match');

    // ── the depth rule, where it is decidable ─────────────────────────────
    // Above, the grandchildren are LAZY: a mutation that revealed every hidden
    // DESCENDANT would change nothing, because there are no descendant
    // elements yet. Materialise the whole tree first, then search: now the
    // grandchildren exist and are hidden, and one press must still not reach
    // them.
    window.renderJsonTree('explorer-tree-state', {
        ports: { analog_outputs: { con1: { 7: { 1: {
            offset: 0.0,
            crosstalk: { con1_2: 0.01, con1_3: 0.02, con1_4: 0.03 },
        } } } } },
        qubits: { q1: { f_01: 4.5e9 } },
    }, { defaultDepth: 99, crud: true });
    window.jsonTreeExpandAll && window.jsonTreeExpandAll('explorer-tree-state');
    await new Promise((r) => setTimeout(r, 60));

    const deepNested = d.querySelector(
        '.tree-node[data-path="ports.analog_outputs.con1.7.1.crosstalk"]');
    const deepKids = deepNested && deepNested.querySelector(':scope > .tree-children');
    ok(!!deepKids && deepKids.children.length === 3,
       'the grandchildren are materialised before the search ('
       + (deepKids ? deepKids.children.length : 0) + ')');

    window.jsonTreeSearch('explorer-tree-state', 'port');
    await new Promise((r) => setTimeout(r, 320));

    const outer = d.querySelector('.tree-node[data-path="' + TARGET + '"]');
    const outerTog = outer && outer.querySelector(':scope > .tree-row > .tree-toggle');
    const deepHidden = () => deepNested
        ? deepNested.querySelectorAll(':scope > .tree-children > .tree-search-hidden').length
        : -1;
    ok(deepHidden() === 3,
       'and the search hid all three of them (' + deepHidden() + ')');

    if (outerTog) {
        outerTog.click();
        ok(deepHidden() === 3,
           'one press on the OUTER row leaves the grandchildren filtered -- one '
           + 'level per click, so a press can never flatten a subtree ('
           + deepHidden() + ' still hidden)');
    }

    console.log(fails ? ('FAILED: ' + fails) : ('ALL OK (' + asserts + ' assertions)'));
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.stack); process.exit(1); });
