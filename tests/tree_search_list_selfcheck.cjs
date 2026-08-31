/* jsdom selfcheck for the Json-tree search past the materialisation cap
 * (2026-08-28, revised the same day on user feedback: the tree STAYS a tree).
 * A broad query used to materialise every matching subtree (1,384 rows for
 * "amplitude" on a real 20Q chip: ~430 ms of DOM + 330 ms of style/layout);
 * the interim flat result list read as "the tree vanished". Pins:
 *   1. below the cap: the classic in-tree highlight, no notice
 *   2. above the cap: the tree is still a tree -- the first CAP matches (tree
 *      order) materialised + highlighted, the rest not built; a notice names
 *      the true count; "show all" materialises everything on the press
 *   3. clearing the query removes the notice
 *   4. two trees under ONE parent (state + wiring tabs share the search box):
 *      the notice lives INSIDE its tree and survives a tab round trip
 * Run: node tests/tree_search_list_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div class="explorer-pane"><div id="explorer-tree-state" class="json-tree"></div><div id="explorer-tree-wiring" class="json-tree" style="display:none"></div></div></body></html>',
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
    const leaves = {};
    for (let i = 0; i < 10; i++) leaves['amp_' + i] = 0.1 * i;
    if (q === 3) leaves.zeta_only = 7;
    DATA.qubits['q' + q] = leaves;
}
const d = window.document;
const c = d.getElementById('explorer-tree-state');
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1, crud: true });
const rendered = () => c.querySelectorAll('.tree-node').length;
const highlighted = () => c.querySelectorAll('.tree-highlight').length;
const visibleNodes = () => Array.prototype.filter.call(c.querySelectorAll('.tree-node'), (n) => !n.classList.contains('tree-search-hidden')).length;
// the ? (key-help) rows belong to LIVE state trees (crud renders) only
ok(c.querySelectorAll('.tree-help').length > 0, 'a crud (live state) tree carries the ? rows');
const ro = d.createElement('div'); ro.id = 'ro-tree'; ro.className = 'json-tree'; d.body.appendChild(ro);
window.renderJsonTree('ro-tree', { a: { b: 1 } }, { defaultDepth: 2 });
ok(ro.querySelectorAll('.tree-help').length === 0, 'a non-crud tree (compare view, inspector subtree) carries none');
const before = rendered();

// 1. below the cap: classic highlight
window.jsonTreeSearch('explorer-tree-state', 'zeta');
setTimeout(function () {
    ok(!d.querySelector('.tree-search-results'), 'a narrow query renders no notice');
    ok(highlighted() === 1, 'and highlights its one match in the tree');
    // 2. above the cap: the tree stays a tree, capped
    window.jsonTreeSearch('explorer-tree-state', 'amp');
    setTimeout(function () {
        const notice = c.querySelector(':scope > .tree-search-results');
        ok(!!notice, 'a broad query renders a notice INSIDE the tree');
        ok(/60 matches/.test(notice.textContent) && /first 20/.test(notice.textContent), 'naming the true count and the shown count (' + notice.textContent.trim().slice(0, 60) + ')');
        ok(highlighted() === 20, 'exactly the first CAP matches are highlighted (' + highlighted() + ')');
        ok(visibleNodes() > 0 && !!c.querySelector('.tree-node[data-path="qubits.q1.amp_0"]'), 'the tree is still on screen (q1 branch materialised)');
        const q6 = c.querySelector('.tree-node[data-path="qubits.q6.amp_0"]');
        ok(!q6 || q6.classList.contains('tree-search-hidden'), 'matches past the cap are not built / not shown');
        // "show all" builds the rest, on the press
        notice.querySelector('.tsr-all').click();
        setTimeout(function () {
            ok(highlighted() === 60, 'show all: every match highlighted (' + highlighted() + ')');
            ok(!c.querySelector('.tree-search-results'), 'and the notice is gone');
            // 3. clearing
            window.jsonTreeSearch('explorer-tree-state', 'amp_');   // a new broad query is capped again
            setTimeout(function () {
                ok(!!c.querySelector(':scope > .tree-search-results') && highlighted() === 20, 'the next broad query is capped again');
                window.jsonTreeSearch('explorer-tree-state', '');
                setTimeout(function () {
                    ok(!d.querySelector('.tree-search-results'), 'clearing the query removes the notice');
                    ok(rendered() >= before && highlighted() === 0, 'the tree is intact');
                    // 4. two trees under one parent: the notice survives a tab round trip
                    const w = d.getElementById('explorer-tree-wiring');
                    window.renderJsonTree('explorer-tree-wiring', { ports: { amp_a: 1, amp_b: 2 } }, { defaultDepth: 2 });
                    window.jsonTreeSearch('explorer-tree-state', 'amp');
                    setTimeout(function () {
                        const stateNotice = c.querySelector(':scope > .tree-search-results');
                        ok(!!stateNotice && stateNotice.getAttribute('data-for') === 'explorer-tree-state', 'the state notice lives INSIDE the state tree');
                        c.style.display = 'none'; w.style.display = '';
                        window.jsonTreeSearch('explorer-tree-wiring', 'amp');
                        setTimeout(function () {
                            ok(c.contains(stateNotice), 'the wiring search did not remove the state notice');
                            ok(!w.querySelector('.tree-search-results') && w.querySelectorAll('.tree-highlight').length === 2, 'wiring (2 matches) shows the classic highlight, no notice');
                            w.style.display = 'none'; c.style.display = '';
                            window.jsonTreeSearch('explorer-tree-state', 'amp');
                            setTimeout(function () {
                                ok(c.querySelector(':scope > .tree-search-results') === stateNotice && highlighted() === 20, 'back on state: the capped tree is as it was');
                                process.exit(fails ? 1 : 0);
                            }, 300);
                        }, 300);
                    }, 300);
                }, 300);
            }, 300);
        }, 300);
    }, 300);
}, 300);
