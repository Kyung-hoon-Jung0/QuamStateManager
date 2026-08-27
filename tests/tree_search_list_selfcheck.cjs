/* jsdom selfcheck for the Json-tree search result LIST (2026-08-28).
 * A broad query used to materialise every matching subtree (1,384 rows for
 * "amplitude" on a real 20Q chip: ~430 ms of DOM creation + 330 ms of
 * style/layout). Past the cap the matches are LISTED instead. Pins:
 *   1. below the cap: the classic in-tree highlight, no list
 *   2. above the cap: a result list with the true match count, the tree
 *      left un-materialised; a row click removes the list and jumps to that
 *      one path through the explorer's own navigation
 *   3. clearing the query removes the list
 * Run: node tests/tree_search_list_selfcheck.cjs   (driven by tests/test_tree_search_list.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div class="explorer-pane"><div id="explorer-tree-state" class="json-tree"></div></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.__treeSearchMaterializeMax = 20;          // the cap, small for the fixture
window.openConfigManual = function () {};        // manual.js is what the ? rows call (loaded after app.js in the shell)
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
global.SearchQuery = window.SearchQuery;

// 6 qubits x 10 "amp_*" leaves = 60 matches for "amp"; 1 match for "zeta"
const DATA = { qubits: {} };
for (let q = 1; q <= 6; q++) {
    const leaves = { zeta_only: q === 3 ? 7 : undefined };
    for (let i = 0; i < 10; i++) leaves['amp_' + i] = 0.1 * i;
    if (q !== 3) delete leaves.zeta_only;
    DATA.qubits['q' + q] = leaves;
}
const d = window.document;
const c = d.getElementById('explorer-tree-state');
const nav = [];
window._navigateToExplorerPath = function (p) { nav.push(p); };
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1, crud: true });
const rendered = () => c.querySelectorAll('.tree-node').length;
// the ? (key-help) rows belong to LIVE state trees (crud renders) only
ok(c.querySelectorAll('.tree-help').length > 0, 'a crud (live state) tree carries the ? rows');
const ro = d.createElement('div'); ro.id = 'ro-tree'; ro.className = 'json-tree'; d.body.appendChild(ro);
window.renderJsonTree('ro-tree', { a: { b: 1 } }, { defaultDepth: 2 });
ok(ro.querySelectorAll('.tree-help').length === 0, 'a non-crud tree (compare view, inspector subtree) carries none');
const before = rendered();

// 1. below the cap: classic highlight
window.jsonTreeSearch('explorer-tree-state', 'zeta');
setTimeout(function () {
    ok(!d.querySelector('.tree-search-results'), 'a narrow query renders no result list');
    ok(c.querySelectorAll('.tree-highlight').length === 1, 'and highlights its one match in the tree');
    // 2. above the cap: the list
    window.jsonTreeSearch('explorer-tree-state', 'amp');
    setTimeout(function () {
        const list = d.querySelector('.tree-search-results');
        ok(!!list, 'a broad query renders the result list instead of expanding');
        ok(/60 matches/.test(list.textContent), 'with the true match count (' + (list.textContent.match(/\d+ matches/) || [''])[0] + ')');
        ok(list.querySelectorAll('.tsr-row').length === 60, 'one row per match');
        ok(c.querySelectorAll('.tree-highlight').length === 0, 'nothing was materialised/highlighted in the tree');
        const visibleNodes = Array.prototype.filter.call(c.querySelectorAll('.tree-node'), (n) => !n.classList.contains('tree-search-hidden')).length;
        ok(visibleNodes === 0, 'the tree itself is hidden behind the list');
        ok(/qubits\.q1\.amp_0/.test(list.textContent) && /0\.1/.test(list.textContent), 'rows carry the path and the value');
        list.querySelector('.tsr-row[data-path="qubits.q2.amp_3"]').click();
        ok(nav[0] === 'qubits.q2.amp_3', 'a row click jumps to that ONE path via the explorer navigation');
        ok(!d.querySelector('.tree-search-results'), 'and removes the list');
        // 3. clearing
        window.jsonTreeSearch('explorer-tree-state', 'amp_');   // a NEW query (the same one is deduped)
        setTimeout(function () {
            ok(!!d.querySelector('.tree-search-results'), 'list back for the next broad query');
            window.jsonTreeSearch('explorer-tree-state', '');
            setTimeout(function () {
                ok(!d.querySelector('.tree-search-results'), 'clearing the query removes the list');
                ok(rendered() >= before, 'the tree is intact');
                process.exit(fails ? 1 : 0);
            }, 300);
        }, 300);
    }, 300);
}, 300);
