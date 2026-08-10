/* docs/113 (#13) — keyboard polish in the REAL app.js under jsdom:
 * '/' focuses the page's primary search, Ctrl+Enter presses an ARMED
 * Apply-all (disabled = nothing), '?' toggles the cheat sheet (Esc closes),
 * and none of it fires while typing in a field.
 *
 * Run: node tests/kb_polish_selfcheck.cjs  (driven by tests/test_kb_polish.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/bulk', pretendToBeVisual: true,
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

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function key(k, opts) {
    const ev = new window.KeyboardEvent('keydown', Object.assign(
        { key: k, bubbles: true, cancelable: true }, opts || {}));
    (opts && opts.target ? opts.target : window.document).dispatchEvent(ev);
    return ev;
}

const doc = window.document;
doc.body.innerHTML =
    '<input type="search" id="global-search">' +
    '<div id="table-pane">' +
    '  <input type="search" id="bulk-search">' +
    '  <button id="bulk-apply-all" disabled>Apply all</button>' +
    '  <input type="text" class="bulk-cell" id="cell1">' +
    '</div>';

// '/' focuses the pane's search
key('/');
ok(doc.activeElement === doc.getElementById('bulk-search'),
   "'/' focuses the page's primary search");

// '/' while typing does nothing (the box keeps the character semantics)
const cell = doc.getElementById('cell1');
cell.focus();
const ev2 = key('/', { target: cell });
ok(!ev2.defaultPrevented, "'/' inside a field is never hijacked");
cell.blur();

// Ctrl+Enter: disabled button → nothing; armed → clicked
let applies = 0;
const btn = doc.getElementById('bulk-apply-all');
btn.addEventListener('click', () => applies++);
key('Enter', { ctrlKey: true });
ok(applies === 0, 'Ctrl+Enter on a DISABLED Apply-all does nothing');
btn.disabled = false;
key('Enter', { ctrlKey: true });
ok(applies === 1, 'Ctrl+Enter presses an armed Apply-all');
cell.focus();
key('Enter', { ctrlKey: true, target: cell });
ok(applies === 2, 'Ctrl+Enter works FROM a grid cell (that is where the user is)');
cell.blur();

// '?' opens the cheat sheet; Esc closes it
key('?');
ok(!!doc.getElementById('kb-cheatsheet'), "'?' opens the cheat sheet");
ok(doc.getElementById('kb-cheatsheet').textContent.indexOf('Ctrl+Shift+Z') >= 0,
   'the sheet documents undo/redo');
key('Escape');
ok(!doc.getElementById('kb-cheatsheet'), 'Esc closes the cheat sheet');
key('?'); key('?');
ok(!doc.getElementById('kb-cheatsheet'), "'?' toggles (second press closes)");

process.exit(fails ? 1 : 0);
