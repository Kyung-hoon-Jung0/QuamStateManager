/* jsdom selfcheck for QA JT-15 -- a long single-line value ends in an
 * ellipsis instead of pushing the row's actions off screen.
 *
 * The QA pass: qubits > q2 > extras > population_loss_safe_intervals_json is a
 * ~5.5k-character JSON-in-a-string. `.tree-row` is flex + nowrap and
 * `.tree-val` had no min-width / overflow, so the value kept its full
 * min-content width and the hover actions (copy / type / delete / ?) sat
 * ~47,000 px to the right. The fix is a stylesheet rule; jsdom has no layout,
 * but it DOES cascade the real style.css into getComputedStyle, so the rule
 * is pinned against the real rendered row (selector semantics included):
 *   1. a rendered value is min-width 0 + overflow hidden + text-overflow ellipsis
 *   2. the diff view's right-hand value too
 *   3. the inline EDITOR is not clipped (it hosts the input + the type chip)
 *   4. the text itself is untouched: the full value is still what edit/copy read
 * The real-layout check (actions inside the tree's right edge) is the CDP probe.
 *
 * Run: node tests/tree_long_value_selfcheck.cjs   (driven by tests/test_tree_long_value.py)
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
const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');

const dom = new JSDOM('<!doctype html><html><head><style>' + css + '</style></head><body>' +
    '<div id="tree"></div></body></html>', { url: 'http://localhost/explorer', pretendToBeVisual: true });
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
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
try { window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8')); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;

const LONG = JSON.stringify(Array.from({ length: 400 }, (_, i) => [i * 0.001, i * 0.001 + 0.0005]));
const DATA = { qubits: { q2: { extras: { population_loss_safe_intervals_json: LONG, short: 'ok' } } } };
window.renderJsonTree('tree', DATA, { defaultDepth: 99 });
const leaf = doc.querySelector('.tree-node[data-path="qubits.q2.extras.population_loss_safe_intervals_json"]');
const val = leaf && leaf.querySelector(':scope > .tree-row > .tree-val');
ok(!!val && LONG.length > 5000, 'setup: the long leaf rendered (' + LONG.length + ' chars)');

const cs = window.getComputedStyle(val);
ok(cs.overflow === 'hidden' || cs.overflowX === 'hidden', 'the value clips its overflow (' + cs.overflow + ')');
ok(cs.textOverflow === 'ellipsis', 'and ends in an ellipsis (' + cs.textOverflow + ')');
ok(cs.minWidth === '0px' || cs.minWidth === '0', 'with min-width 0, so the flex row can shrink it (' + cs.minWidth + ')');
const row = val.parentElement;
ok(window.getComputedStyle(row).display === 'flex', 'setup: the row is still the flex row the rule relies on');
ok(val.textContent === JSON.stringify(LONG) && val.dataset.editVal === LONG,
   'the text is untouched -- the full value is still what edit and copy read');
// review: the clipping applies to every tree, so a hover shows what the ellipsis hides
ok(val.title.indexOf('Click to edit') === 0 && val.title.indexOf(LONG) > 0,
   'a clipped long string carries its full text in the hover, after the action hint');
const shortVal = doc.querySelector('.tree-node[data-path="qubits.q2.extras.short"] > .tree-row > .tree-val');
ok(!!shortVal && shortVal.title === 'Click to edit', 'a short value keeps the plain hint (' + (shortVal && shortVal.title) + ')');

// the diff view's right-hand value is a sibling span, not a .tree-val
const inc = doc.createElement('span');
inc.className = 'tree-incoming-val tree-val-string';
inc.textContent = JSON.stringify(LONG);
row.appendChild(inc);
ok(window.getComputedStyle(inc).textOverflow === 'ellipsis',
   'the live-diff incoming value is clipped the same way');

// the inline editor must not be clipped: it hosts the input and the async chip
val.click();
ok(val.classList.contains('tree-val-editing') && !!val.querySelector('input'),
   'setup: a click opens the inline editor');
const ecs = window.getComputedStyle(val);
ok(ecs.overflow !== 'hidden' && ecs.textOverflow !== 'ellipsis',
   'the EDITING value is not clipped (overflow=' + ecs.overflow + ', text-overflow=' + ecs.textOverflow + ')');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK');
process.exit(fails ? 1 : 0);
