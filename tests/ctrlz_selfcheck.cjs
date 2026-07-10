/* jsdom selfcheck for the Ctrl+Z undo client wiring in web/static/app.js:
 *  1. Ctrl/⌘+Z (outside a text field, tray present) → POST /undo into #pending-tray
 *  2. Guarded: typing inside an <input>/<textarea> does NOT hijack Ctrl+Z
 *  3. Guarded: no #pending-tray → no request
 *  4. cellsReverted → reverts the matching inspector cell + dispatches
 *     quam:state-changed (the Live-State-Edit grids re-pull on it)
 *
 * Run: node tests/ctrlz_selfcheck.cjs   (driven by tests/test_ctrlz_client.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
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
global.fetch = () => new Promise(() => {});   // never resolves — fine for wiring tests
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

// Recording htmx stub (app.js only needs .ajax/.trigger/.process here).
const calls = [];
window.htmx = {
    ajax: function (method, url, opts) { calls.push({ method, url, opts }); return Promise.resolve(); },
    trigger: function () {},
    process: function () {},
};
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);   // app.js is head-loaded: must evaluate with no <body> deps
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function pressCtrlZ(target) {
    const ev = new window.KeyboardEvent('keydown',
        { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
    (target || window.document).dispatchEvent(ev);
    return ev;
}

// ── 3. no tray → no request ──────────────────────────────────────────────────
pressCtrlZ();
ok(calls.length === 0, 'Ctrl+Z without #pending-tray issues no request');

// ── 1. tray present, focus outside a field → POST /undo ─────────────────────
const tray = window.document.createElement('div');
tray.id = 'pending-tray';
window.document.body.appendChild(tray);
pressCtrlZ();
ok(calls.length === 1, 'Ctrl+Z issues exactly one request');
ok(calls[0] && calls[0].method === 'POST' && calls[0].url === '/undo',
   'Ctrl+Z posts /undo (got ' + JSON.stringify(calls[0]) + ')');
ok(calls[0] && calls[0].opts && calls[0].opts.source === '#pending-tray'
   && calls[0].opts.target === '#pending-tray',
   'undo request is source+target #pending-tray (hx-sync scoped, no body-queue wedge)');

// ── 2. focus inside an input → native undo untouched ───────────────────────
const inp = window.document.createElement('input');
window.document.body.appendChild(inp);
inp.focus();
const before = calls.length;
const ev = new window.KeyboardEvent('keydown',
    { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
inp.dispatchEvent(ev);
ok(calls.length === before, 'Ctrl+Z inside an <input> does NOT hijack (native text undo)');
ok(!ev.defaultPrevented, 'default not prevented inside an <input>');
inp.blur();

// ── 4. cellsReverted → cell revert + quam:state-changed ─────────────────────
// Build a minimal inspector cell (hidden dot_path + value input, modified marks).
const form = window.document.createElement('form');
form.innerHTML = '<input type="hidden" name="dot_path" value="qubits.qA1.f_01">' +
                 '<input name="value" class="edit-input edit-input-modified" value="6.30e9" title="x">';
const td = window.document.createElement('td');
td.className = 'cell-modified';
td.appendChild(form);
window.document.body.appendChild(td);

let stateChanged = 0;
window.document.addEventListener('quam:state-changed', function () { stateChanged++; });

window.document.dispatchEvent(new window.CustomEvent('cellsReverted', {
    detail: { message: 'Undone: qubits.qA1.f_01 → 6.25e9',
              entries: [{ dot_path: 'qubits.qA1.f_01', old_value_str: '6.25e9', created: false }] },
}));

const valInput = form.querySelector('input[name="value"]');
ok(valInput.value === '6.25e9', 'cellsReverted restores the inspector cell value');
ok(!valInput.classList.contains('edit-input-modified'), 'modified marker cleared');
ok(!td.classList.contains('cell-modified'), 'td modified marker cleared');
ok(stateChanged === 1, 'cellsReverted dispatches quam:state-changed (grids re-pull)');

process.exit(fails ? 1 : 0);
