/* jsdom selfcheck for the bulk-grid value-history 🕘 (#fh-cellbtn) docking
 * (r10): the shared button must live INSIDE the focused cell's <td> —
 * absolute in the td, so it moves with the cell through resize/scroll/scale
 * (the old body-mounted position:fixed float pinned stale viewport coords and
 * drifted outside the box). Pins:
 *   1. focusin on an editable cell → button is a CHILD of that cell's td,
 *      visible, and the input gets .fh-docked (text padding)
 *   2. moving focus cell-to-cell re-docks the button and cleans the previous
 *      input's .fh-docked
 *   3. focusing outside the grid parks the button back on <body>, hidden,
 *      class removed (search/sort surface stays clean)
 *   4. readonly cells never dock
 *   5. CSS source pins: td anchoring + enlarged icons
 *
 * Run: node tests/cellbtn_selfcheck.cjs   (driven by tests/test_tab_focus.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<table><tbody><tr data-qubit="q1">' +
    '<td class="bulk-td" data-col-key="f_01" id="td1">' +
    '<input class="bulk-cell" id="c1" data-dot-path="qubits.q1.f_01"></td>' +
    '<td class="bulk-td" data-col-key="T1" id="td2">' +
    '<input class="bulk-cell" id="c2" data-dot-path="qubits.q1.T1"></td>' +
    '<td class="bulk-td" data-col-key="ro" id="td3">' +
    '<input class="bulk-cell bulk-cell-ro" id="c3" readonly></td>' +
    '</tr></tbody></table>' +
    '<input id="outside">' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
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

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
const c1 = d.getElementById('c1'), c2 = d.getElementById('c2'),
      c3 = d.getElementById('c3');

// 1. dock into the focused cell's td
c1.focus();
let btn = d.getElementById('fh-cellbtn');
ok(!!btn, 'the shared 🕘 button exists after first focus');
ok(btn && btn.parentElement === d.getElementById('td1'),
   'button docks INSIDE the focused cell\'s td');
ok(btn && btn.style.display === 'block', 'button visible while docked');
ok(c1.classList.contains('fh-docked'), 'focused input gets .fh-docked padding');

// 2. cell-to-cell re-dock cleans the previous input
c2.focus();
ok(btn.parentElement === d.getElementById('td2'), 'button re-docks to the new cell\'s td');
ok(!c1.classList.contains('fh-docked'), 'previous input\'s .fh-docked is cleaned');
ok(c2.classList.contains('fh-docked'), 'new input padded');

// 3. leaving the grid parks the button on <body>, hidden
d.getElementById('outside').focus();
ok(btn.style.display === 'none', 'button hidden when focus leaves the grid');
ok(btn.parentElement === d.body, 'button parked on <body> (td stays byte-clean)');
ok(!c2.classList.contains('fh-docked'), 'padding class removed on undock');

// 4. readonly cells never dock
c3.focus();
ok(btn.style.display === 'none' && btn.parentElement === d.body,
   'readonly cell does not dock the button');

// 5. r11: TEXT-anchored positioning — left is JS-managed and follows the value
c1.focus();
btn = d.getElementById('fh-cellbtn');
ok(/^\d+(\.\d+)?px$/.test(btn.style.left), 'style.left is set on dock (' + btn.style.left + ')');
c1.value = '5,123,456,789.123456';
c1.dispatchEvent(new window.Event('input', { bubbles: true }));
ok(/^\d+(\.\d+)?px$/.test(btn.style.left), 'reposition on typing keeps a valid left');
d.getElementById('outside').focus();
ok(btn.style.left === '', 'hide clears the managed left');

// the measurer itself grows with text length (canvas or fallback path)
const mw = window.FieldHistory && window.FieldHistory._cellTextWidth;
ok(typeof mw === 'function', 'measure seam exported');
c1.value = 'abc';
const w3 = mw(c1);
c1.value = 'abcdefghijkl';
const w12 = mw(c1);
ok(w12 > w3 && w3 > 0, 'text width grows with length (' + w3.toFixed(1) + ' -> ' + w12.toFixed(1) + ')');

// 6. CSS pins — text-anchor contract + enlarged icons
const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
ok((css.match(/\.bulk-td \{ position: relative; \}/g) || []).length === 1,
   'td containing-block rule declared exactly once');
const cellBtnRule = (css.split('#fh-cellbtn {')[1] || '').split('}')[0];
ok(cellBtnRule.indexOf('position: absolute') >= 0, '#fh-cellbtn is absolute (not fixed)');
ok(cellBtnRule.indexOf('z-index: 5') >= 0,
   'icon paints ABOVE the focused input (z4) — the invisibility root cause');
ok(cellBtnRule.indexOf('right:') === -1,
   'no td-edge right anchor — left is JS-managed at the value tail');
ok(cellBtnRule.indexOf('width: 20px') >= 0, 'cell clock enlarged to 20px');
ok(css.indexOf('.bulk-cell.fh-docked { padding-right: 1.5rem; }') >= 0,
   'docked input padding rule present (clamped case only)');
const inspRule = css.split('.field-hist-btn {')[1] || '';
ok(inspRule.indexOf('font-size: .9rem') >= 0, 'inspector clock enlarged');

process.exit(fails ? 1 : 0);
