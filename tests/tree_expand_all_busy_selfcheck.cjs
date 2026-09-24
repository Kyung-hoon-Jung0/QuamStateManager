/* jsdom selfcheck for QA JT-16 -- Depth "All" says so before the page pauses.
 *
 * Measured on the real 5-qubit chip (31,227 nodes): pressing All froze the
 * page ~5 s with no word -- ~1.3 s of JS, then a ~5 s style/layout frame that
 * no JS chunking can split. The Explorer's All button now goes through
 * jsonTreeExpandAllUi: above the row budget it paints a note, disables the
 * button and marks the tree busy BEFORE the expand, and clears all three
 * after it. At or below the budget it is the old synchronous press.
 * window.jsonTreeExpandAll itself stays synchronous (its other callers read
 * the expansion right after it).
 *
 * Run: node tests/tree_expand_all_busy_selfcheck.cjs   (driven by tests/test_tree_expand_all_busy.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div class="tree-toolbar"><div class="tree-depth-group">' +
    '<button id="all-big">All</button><button id="all-small">All</button></div></div>' +
    '<div id="big"></div><div id="small"></div></body></html>', {
    url: 'http://localhost/explorer', pretendToBeVisual: true,
});
const { window } = dom;
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
global.fetch = () => new Promise(() => {});
bridge(window, 'fetch', global.fetch);
// frames are counted, so "the note painted before the expand" is observable
let frames = 0;
global.requestAnimationFrame = (f) => setTimeout(() => { frames++; f(); }, 5);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

const budget = Number((src.match(/_EXPAND_ALL_NOTE_AT\s*=\s*(\d+)/) || [])[1]);
ok(budget > 0, 'the row budget is declared (' + budget + ')');

// just over the budget: q0..qN, each {a:{v:..}, b:..} = 4 rows per qubit
const BIG = { qubits: {} };
for (let i = 0; i * 4 + 1 <= budget + 8; i++) BIG.qubits['q' + i] = { a: { v: i }, b: i };
const SMALL = { qubits: { q1: { a: { v: 1 }, b: 1 } } };
const collapsed = (id) => doc.getElementById(id).querySelectorAll('.tree-toggle.collapsed').length;

(async function main() {
    // ── the markup: the Explorer's All goes through the announcing wrapper ─
    const tpl = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_explorer.html'), 'utf8');
    ok(/onclick="jsonTreeExpandAllUi\(_activeTreeId\(\), this\)"/.test(tpl),
       "the Explorer's All button uses jsonTreeExpandAllUi (passing itself)");

    // ── small tree: exactly the old synchronous press ─────────────────────
    window.renderJsonTree('small', SMALL, { defaultDepth: 1 });
    ok(collapsed('small') > 0, 'setup: the small tree starts partly collapsed');
    window.jsonTreeExpandAllUi('small', doc.getElementById('all-small'));
    ok(collapsed('small') === 0, 'a tree under the budget expands synchronously, as before');
    ok(!doc.querySelector('.tree-busy-note') && !doc.getElementById('all-small').disabled,
       'and shows no note, disables nothing');

    // ── big tree: say so, THEN expand, then clear ────────────────────────
    window.renderJsonTree('big', BIG, { defaultDepth: 1 });
    const big = doc.getElementById('big');
    const btn = doc.getElementById('all-big');
    const before = collapsed('big');
    ok(before > 0, 'setup: the big tree starts collapsed (' + before + ' closed nodes)');
    const f0 = frames;
    window.jsonTreeExpandAllUi('big', btn);
    const note = doc.querySelector('.tree-busy-note');
    ok(!!note && /Expanding all [\d,]+ rows/.test(note.textContent),
       'over the budget the press puts up a note naming the row count (' + (note && note.textContent) + ')');
    ok(note && note.previousElementSibling === doc.querySelector('.tree-depth-group'),
       'the note sits next to the depth buttons, outside the tree');
    ok(btn.disabled === true, 'the All button is disabled while it works');
    ok(big.getAttribute('aria-busy') === 'true' && big.classList.contains('tree-busy'),
       'the tree is marked busy (aria-busy + progress cursor class)');
    ok(collapsed('big') === before, 'and NOTHING is expanded yet -- the note gets a frame to paint first');
    window.jsonTreeExpandAllUi('big', btn);          // an impatient second press
    ok(doc.querySelectorAll('.tree-busy-note').length === 1,
       'a second press while the first is pending does not stack a second note');
    await tick(5);
    ok(frames > f0 && collapsed('big') === before, 'still unexpanded after the first frame (the deferral is real)');
    // jsdom builds ~5k rows slowly: wait on the state, bounded, never on a guess
    for (let i = 0; i < 500 && collapsed('big') > 0; i++) await tick(20);
    ok(collapsed('big') === 0, 'then every node is expanded');
    for (let i = 0; i < 100 && doc.querySelector('.tree-busy-note'); i++) await tick(20);
    ok(!doc.querySelector('.tree-busy-note'), 'the note is cleared afterwards');
    ok(btn.disabled === false && !big.hasAttribute('aria-busy') && !big.classList.contains('tree-busy'),
       'the button is re-enabled and the busy marks are gone');

    // ── the shared synchronous API is unchanged ───────────────────────────
    window.renderJsonTree('big', BIG, { defaultDepth: 1 });
    window.jsonTreeExpandAll('big');
    ok(collapsed('big') === 0, 'window.jsonTreeExpandAll stays synchronous for its other callers');

    console.log(fails ? ('FAILED: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
