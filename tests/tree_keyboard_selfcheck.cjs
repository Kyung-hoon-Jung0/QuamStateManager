/* jsdom selfcheck for QA JT-20 -- the Json tree works from the keyboard.
 *
 * The QA pass: Tab from the tree search walked the toolbar and the chips and
 * then only the ✎ JSON pencils; the ▶ toggles and every scalar value were
 * click-only spans, so opening a node, editing a value and F1 on a row all
 * needed a mouse. Driven against the REAL shipped app.js + manual.js:
 *
 *   1. one Tab stop per tree: the container is tabbable, rows are not (they
 *      are focusable), and arriving by keyboard lands on a row
 *   2. ArrowUp/Down walk the VISIBLE rows -- collapsed children and
 *      search-hidden rows are skipped; Right opens (materialising a lazy
 *      node) then steps in; Left closes then steps out; Home/End
 *   3. Enter on a value does the value's own click (the inline editor); a
 *      read-only copy tree has no keyboard layer at all (review: a dataset
 *      page renders one small tree per qubit per key -- not Tab stops)
 *   4. the editor hands focus back to its row on Escape and on Enter, and
 *      Enter inside the editor commits exactly once
 *   5. F1 on a focused row opens the manual on that row's path
 *   6. the keys the tree handles do not ALSO reach a page-level handler, and
 *      Shift+Tab out of the tree is not a trap
 *
 * Run: node tests/tree_keyboard_selfcheck.cjs   (driven by tests/test_tree_keyboard.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<input id="search" type="text">' +
    '<div id="tree-edit"></div><div id="tree-copy"></div>' +
    '<button id="after">after</button></body></html>', {
    url: 'http://localhost/explorer', pretendToBeVisual: true,
});
const { window } = dom;
// The standing harness rule: bridge every global the shipped code reads bare.
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
function bridge(obj, name, value) {
    try { obj[name] = value; } catch (e) { /* getter-only */ }
    if (obj[name] !== value) Object.defineProperty(obj, name, { value: value, configurable: true, writable: true });
}
bridge(global, 'navigator', window.navigator);
bridge(global, 'location', window.location);
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[global, window].forEach((o) => { bridge(o, 'localStorage', STORE); bridge(o, 'sessionStorage', STORE); });
const posts = [];
function fetchStub(url, opts) {
    url = String(url);
    if (url.indexOf('/field/edit') === 0) posts.push({ url: url, body: opts && opts.body });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(
        url.indexOf('/field/edit') === 0 ? { ok: true } : {}) });
}
global.fetch = fetchStub;
bridge(window, 'fetch', fetchStub);
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

for (const f of ['app.js', 'manual.js']) {
    try { window.eval(fs.readFileSync(path.join(STATIC, f), 'utf8')); } catch (e) {
        console.error('FAIL: ' + f + ' did not evaluate under jsdom: ' + e.message);
        process.exit(1);
    }
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

// the manual is observed, not opened: its F1 handler calls window.openConfigManual
const manualOpens = [];
window.openConfigManual = function (o) { manualOpens.push(o && o.path); };
const copies = [];
window.copyWithFeedback = function (raw) { copies.push(raw); };

// a page-level arrow/Enter handler, like the dataset table's (app.js #table-pane)
let pageKeys = 0;
doc.addEventListener('keydown', function (e) {
    if (['ArrowDown', 'ArrowUp', 'Enter', 'ArrowLeft', 'ArrowRight'].indexOf(e.key) >= 0) pageKeys++;
});

function key(k, opts) {
    const t = doc.activeElement || doc.body;
    const ev = new window.KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, opts || {}));
    t.dispatchEvent(ev);
    return ev;
}
const pathOf = (el) => {
    const n = el && el.closest ? el.closest('.tree-node') : null;
    return n ? n.getAttribute('data-path') : null;
};
const focused = () => pathOf(doc.activeElement);
const onRow = () => !!(doc.activeElement && doc.activeElement.classList.contains('tree-row'));

const DATA = {
    qubits: {
        q1: { f_01: 4.3e9, xy: { amplitude: 0.1 } },
        q2: { f_01: 4.6e9 },
    },
    flag: true,
};

(async function main() {
    // ── 1. one Tab stop per tree ──────────────────────────────────────────
    window.renderJsonTree('tree-edit', DATA, { defaultDepth: 1 });
    const tree = doc.getElementById('tree-edit');
    ok(tree.tabIndex === 0, 'the tree container is in the Tab order');
    const rows = tree.querySelectorAll('.tree-row');
    ok(rows.length > 0 && Array.prototype.every.call(rows, (r) => r.getAttribute('tabindex') === '-1'),
       'every row is focusable but NOT a Tab stop (' + rows.length + ' rows)');
    doc.getElementById('search').focus();
    tree.focus();                                    // what a Tab from the search box does
    ok(onRow() && focused() === 'qubits', 'arriving by keyboard lands on the first row (' + focused() + ')');

    // ── 2. walking ─────────────────────────────────────────────────────────
    pageKeys = 0;
    let ev = key('ArrowDown');
    ok(focused() === 'qubits.q1', 'ArrowDown moves to the next visible row (' + focused() + ')');
    ok(ev.defaultPrevented, 'a handled key is consumed (no page scroll)');
    ok(pageKeys === 0, 'and does not ALSO reach a page-level arrow handler');
    const q1 = tree.querySelector('.tree-node[data-path="qubits.q1"]');
    ok(q1._lazyData && !q1.querySelector('.tree-node[data-path="qubits.q1.f_01"]'),
       'setup: q1 is collapsed and not yet materialised');
    key('ArrowRight');
    ok(focused() === 'qubits.q1' && !!q1.querySelector('.tree-node[data-path="qubits.q1.f_01"]')
       && q1.querySelector(':scope > .tree-children').style.display !== 'none'
       && q1.querySelector(':scope > .tree-row > .tree-toggle').classList.contains('expanded'),
       'ArrowRight on a closed node opens it (lazy children built, arrow flipped), focus stays');
    key('ArrowRight');
    ok(focused() === 'qubits.q1.f_01', 'ArrowRight on an open node steps into its first child (' + focused() + ')');
    key('ArrowDown');
    ok(focused() === 'qubits.q1.xy', 'ArrowDown -> the next sibling (' + focused() + ')');
    key('ArrowDown');
    ok(focused() === 'qubits.q2', 'ArrowDown skips a collapsed node\'s children (' + focused() + ')');
    key('ArrowUp');
    ok(focused() === 'qubits.q1.xy', 'ArrowUp -> back (' + focused() + ')');
    key('ArrowLeft');
    ok(focused() === 'qubits.q1', 'ArrowLeft on a closed node steps out to its parent (' + focused() + ')');
    key('ArrowLeft');
    ok(focused() === 'qubits.q1' && q1.querySelector(':scope > .tree-children').style.display === 'none'
       && q1.querySelector(':scope > .tree-row > .tree-toggle').classList.contains('collapsed'),
       'ArrowLeft on an open node closes it');
    key('ArrowDown');
    ok(focused() === 'qubits.q2', 'and the walk skips what it just closed (' + focused() + ')');
    key('End');
    ok(focused() === 'flag', 'End -> the last visible row (' + focused() + ')');
    key('Home');
    ok(focused() === 'qubits', 'Home -> the first row (' + focused() + ')');
    key('Enter');
    ok(tree.querySelector('.tree-node[data-path="qubits"] > .tree-children').style.display === 'none',
       'Enter on a node toggles it (closed)');
    key('Enter');
    ok(tree.querySelector('.tree-node[data-path="qubits"] > .tree-children').style.display !== 'none',
       'Enter again re-opens it');

    // ── 3 + 4. Enter on a value edits; the editor hands focus back ────────
    tree.querySelector('.tree-node[data-path="qubits.q2"] > .tree-row').focus();
    key('ArrowRight'); key('ArrowRight');
    ok(focused() === 'qubits.q2.f_01', 'setup: on a leaf value row (' + focused() + ')');
    const row = doc.activeElement;
    key('Enter');
    const input = row.querySelector('input.tree-edit-input');
    ok(!!input && doc.activeElement === input, 'Enter on an editable value opens the inline editor, focused');
    key('Escape');
    ok(!row.querySelector('input.tree-edit-input'), 'Escape cancels the editor');
    ok(doc.activeElement === row, 'and hands focus back to its ROW (was: <body>)');
    key('F2');
    const input2 = row.querySelector('input.tree-edit-input');
    ok(!!input2 && doc.activeElement === input2, 'F2 edits too');
    input2.value = '4700000000';
    pageKeys = 0;
    key('Enter');
    await tick(20);
    ok(posts.length === 1, 'Enter inside the editor commits exactly once (' + posts.length + ' post)');
    ok(!row.querySelector('input.tree-edit-input'), 'the tree does not re-open the editor on the same Enter');
    ok(doc.activeElement === row, 'a keyboard commit leaves focus on the row');
    ok(pageKeys === 1, 'keys typed INTO the editor are the editor\'s (the tree does not swallow them)');

    // ── 5. F1 on a focused row ─────────────────────────────────────────────
    manualOpens.length = 0;
    ev = key('F1');
    ok(manualOpens.length === 1 && manualOpens[0] === 'qubits.q2.f_01' && ev.defaultPrevented,
       'F1 on a focused row opens the manual on that row (' + manualOpens.join(',') + ')');

    // ── 6. search-hidden rows are skipped ──────────────────────────────────
    window.jsonTreeSearch('tree-edit', 'f_01');
    await tick(320);
    tree.querySelector('.tree-node[data-path="qubits"] > .tree-row').focus();
    const seen = [];
    for (let i = 0; i < 8; i++) { key('ArrowDown'); seen.push(focused()); }
    ok(seen.indexOf('flag') < 0 && seen.indexOf('qubits.q1.xy') < 0,
       'ArrowDown never lands on a row the search hid (' + seen.join(' > ') + ')');
    ok(seen.indexOf('qubits.q1.f_01') >= 0 && seen.indexOf('qubits.q2.f_01') >= 0,
       'and reaches every match');
    window.jsonTreeSearch('tree-edit', '');
    await tick(320);

    // ── 7. a read-only copy tree is NOT a Tab stop (review) ───────────────
    // Keyboard navigation is for the trees a keyboard user acts on (edit /
    // accept-reject). A dataset page renders one small parameter/result tree
    // per qubit per key, and its Tab order used to grow by that number.
    window.renderJsonTree('tree-copy', DATA, { defaultDepth: 3, valueClick: 'copy' });
    const ct = doc.getElementById('tree-copy');
    ok(!ct.hasAttribute('tabindex') && !ct._treeKeys,
       'a read-only copy tree is not a Tab stop and has no key handling');
    ct.querySelector('.tree-node[data-path="flag"] > .tree-row').focus();
    copies.length = 0;
    const evc = key('Enter');
    ok(copies.length === 0 && !evc.defaultPrevented && !ct.querySelector('input'),
       'Enter on a read-only copy tree is left to the page (no copy, no editor)');
    // ...and the JSON pencils inside a keyboard tree are reached from their row, not by Tab
    window.jsonTreeSetExpanded('tree-edit', ['qubits', 'qubits.q1']);
    const pencils = tree.querySelectorAll('.tree-json-edit-btn');
    ok(pencils.length > 0 && Array.prototype.every.call(pencils, (b) => b.tabIndex === -1),
       'the JSON pencils are not Tab stops inside a keyboard tree (' + pencils.length + ' pencils): one Tab stop per tree, literally');
    tree.querySelector('.tree-node[data-path="qubits.q1.xy"] > .tree-row').focus();
    key('F2');
    ok(!!tree.querySelector('.tree-node[data-path="qubits.q1.xy"] .tree-json-textarea'),
       'F2 on a container row opens its JSON editor: the way in');
    const jta = tree.querySelector('.tree-node[data-path="qubits.q1.xy"] .tree-json-textarea');
    jta.focus();
    jta.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    ok(!tree.querySelector('.tree-node[data-path="qubits.q1.xy"] .tree-json-editor') && focused() === 'qubits.q1.xy' && onRow(),
       'Escape closes the JSON editor and hands focus back to its ROW, not <body> (' + focused() + ')');

    // ── 8. leaving is not a trap; a mouse click does not jump ─────────────
    const r1 = tree.querySelector('.tree-node[data-path="qubits"] > .tree-row');
    r1.focus();
    ok(tree.tabIndex === -1, 'while a row holds focus the container leaves the Tab order (Shift+Tab goes past the tree)');
    doc.getElementById('after').focus();
    ok(tree.tabIndex === 0, 'focus leaving the tree puts the container back in the Tab order');
    r1.focus();
    tree.focus();                                    // reached backwards from inside
    ok(doc.activeElement === tree, 'focus arriving on the container FROM a row is not handed straight back (no loop)');
    doc.getElementById('after').focus();
    const q2row = tree.querySelector('.tree-node[data-path="qubits.q2"] > .tree-row');
    q2row.focus();
    doc.getElementById('after').focus();
    tree.focus();
    ok(focused() === 'qubits.q2', 'Tab back into the tree returns to the row last used (' + focused() + ')');
    doc.getElementById('after').focus();
    tree.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    tree.focus();
    ok(doc.activeElement === tree, 'a click on the tree\'s blank area does not jump focus to a row');

    console.log(fails ? ('FAILED: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
