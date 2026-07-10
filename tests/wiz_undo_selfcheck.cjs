/* jsdom selfcheck for the Generate-Config wizard Ctrl+Z (generate.js _wizUndo
 * + the app.js global handler that delegates to it):
 *  1. committed field edit (focusin→type→change) is recorded
 *  2. Ctrl+Z restores the previous value + re-dispatches input/change (state resync)
 *  3. repeated Ctrl+Z walks back through the stack (LIFO)
 *  4. wizard mounted + empty stack → CONSUMED (no server POST /undo behind the user)
 *  5. wizard NOT mounted → falls through to the app-wide POST /undo
 *
 * Run: node tests/wiz_undo_selfcheck.cjs   (driven by tests/test_ctrlz_client.py).
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
const _store = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.localStorage = _store; global.sessionStorage = _store;
window.localStorage = _store; window.sessionStorage = _store;
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const ajaxCalls = [];
window.htmx = {
    ajax: function (m, u, o) { ajaxCalls.push({ m, u, o }); return Promise.resolve(); },
    trigger: function () {}, process: function () {},
};
global.htmx = window.htmx;

const staticDir = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
for (const f of ['app.js', 'generate.js']) {           // base.html load order
    try { window.eval(fs.readFileSync(path.join(staticDir, f), 'utf8')); }
    catch (e) { console.error('FAIL: ' + f + ' did not evaluate: ' + e.message); process.exit(1); }
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function pressCtrlZ() {
    window.document.dispatchEvent(new window.KeyboardEvent('keydown',
        { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
}

// ── 5. wizard NOT mounted → falls through to server /undo ───────────────────
const tray = window.document.createElement('div');
tray.id = 'pending-tray';
window.document.body.appendChild(tray);
pressCtrlZ();
ok(ajaxCalls.length === 1 && ajaxCalls[0].u === '/undo',
   'no wizard mounted → Ctrl+Z falls through to the server POST /undo');
ajaxCalls.length = 0;

// ── mount a minimal wizard with a qubit-value field ─────────────────────────
const genRoot = window.document.createElement('div');
genRoot.id = 'generate-root';
genRoot.innerHTML = '<input id="wiz-f01" aria-label="f_01" value="6.25">';
window.document.body.appendChild(genRoot);
const field = window.document.getElementById('wiz-f01');

// ── 4. wizard mounted + nothing recorded → consumed, no server undo ─────────
pressCtrlZ();
ok(ajaxCalls.length === 0,
   'wizard mounted + empty stack → CONSUMED (server /undo NOT fired behind the user)');

// ── 1-2. commit an edit, Ctrl+Z restores it ─────────────────────────────────
field.dispatchEvent(new window.Event('focusin', { bubbles: true }));   // snapshot 6.25
field.value = '6.30';
field.dispatchEvent(new window.Event('change', { bubbles: true }));    // commit
let resync = 0;
field.addEventListener('change', function () { resync++; });
pressCtrlZ();
ok(field.value === '6.25', 'Ctrl+Z restored the committed field to its previous value');
ok(resync === 1, 'undo re-dispatched change (wizard state resync listeners run)');
ok(ajaxCalls.length === 0, 'wizard undo never touches the server /undo');

// ── 3. two commits → two Ctrl+Z walk back LIFO ──────────────────────────────
field.dispatchEvent(new window.Event('focusin', { bubbles: true }));
field.value = 'A'; field.dispatchEvent(new window.Event('change', { bubbles: true }));
field.dispatchEvent(new window.Event('focusin', { bubbles: true }));
field.value = 'B'; field.dispatchEvent(new window.Event('change', { bubbles: true }));
pressCtrlZ();
ok(field.value === 'A', 'first Ctrl+Z → back to A (LIFO)');
pressCtrlZ();
ok(field.value === '6.25', 'second Ctrl+Z → back to the original');

// unchanged commit records nothing
field.dispatchEvent(new window.Event('focusin', { bubbles: true }));
field.dispatchEvent(new window.Event('change', { bubbles: true }));    // no edit
pressCtrlZ();
ok(field.value === '6.25' && ajaxCalls.length === 0,
   'an unchanged commit records no undo entry (no-op guard)');

process.exit(fails ? 1 : 0);
