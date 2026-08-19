/* docs/124 C-1 — the Live-diff per-row ✓/✗ buttons must actually work.
 *
 * The tree renderer (one IIFE) wires the buttons to _acceptLiveValue /
 * _rejectLiveValue, which are locals of the live-diff IIFE. The bare calls
 * threw ReferenceError on EVERY click, so ✓ Accept was a silent no-op: the
 * user believed Qualibrate's value was staged, applied to live, and the value
 * they explicitly accepted was absent from what hit the hardware. Pre-existing
 * on main; zero coverage anywhere (a button.click() returning true never meant
 * the handler ran — the earlier probe was fooled by exactly that).
 *
 * Pins, at the OBSERVABLE level (the pre-fix behavior produces none of these):
 *   1. the handlers are exported (typeof function on window)
 *   2. clicking ✓ on a real rendered diff row issues the /field/edit-batch
 *      request with the row's dot_path, and the row turns pending
 *   3. clicking ✗ clears the incoming marker and updates the diff-bar count
 *
 * Run: node tests/livediff_buttons_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator',
                      { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
// Recording fetch stub shaped like the real endpoints _liveFetchJson expects:
// Response-like with ok/status/text().
const fetches = [];
let liveDiffPayload = { live_state: {}, live_wiring: {} };
function fetchStub(url, opts) {
    fetches.push({ url: String(url), opts: opts || {} });
    const body = String(url).indexOf('/state/live-diff') === 0
        ? liveDiffPayload
        : { ok: true, results: [{ ok: true }], tray_html: '' };
    return Promise.resolve({
        ok: true, status: 200,
        text: function () { return Promise.resolve(JSON.stringify(body)); },
    });
}
global.fetch = fetchStub;
Object.defineProperty(window, 'fetch',
                      { value: fetchStub, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

// This harness runs app.js through Node-realm eval, where a window PROPERTY
// is not a bare-identifier global (in a browser it is — the global object IS
// window). explorerLiveDiff's ON branch calls renderJsonTree bare across
// IIFEs, which resolves fine in production and ReferenceErrors here unless
// bridged (the CLAUDE.md bridge-every-bare-global rule; the miss is swallowed
// by the diff path's own catch into a recover toast).
global.renderJsonTree = window.renderJsonTree;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = () => new Promise((r) => setTimeout(r, 30));

(async function main() {

// ── 1. the exports exist ───────────────────────────────────────────────────
ok(typeof window._acceptLiveValue === 'function',
   'window._acceptLiveValue is exported (cross-IIFE reachable)');
ok(typeof window._rejectLiveValue === 'function',
   'window._rejectLiveValue is exported (cross-IIFE reachable)');

// ── 2. a real rendered diff row: ✓ stages through /field/edit-batch ───────
const host = window.document.createElement('div');
host.id = 'explorer-tree-state';
window.document.body.appendChild(host);
const cnt = window.document.createElement('span');
cnt.id = 'livediff-bar-count';
cnt.textContent = '2';
window.document.body.appendChild(cnt);

// Working copy vs live: two leaves differ -> two rows carry ✓/✗. Children are
// built LAZILY (production renders at defaultDepth 1 then expands along the
// diff paths), so expand by clicking the real toggles — the same machinery a
// user drives — until no collapsed node remains.
window.renderJsonTree('explorer-tree-state',
    { qubits: { q1: { f_01: 4.30e9, T1: 30e-6 } } },
    { defaultDepth: 1,
      refData: { qubits: { q1: { f_01: 4.31e9, T1: 31e-6 } } },
      valueClick: 'livediff' });
for (let pass = 0; pass < 10; pass++) {
    const collapsed = host.querySelectorAll('.tree-toggle.collapsed');
    if (!collapsed.length) break;
    collapsed.forEach((t) => t.click());
    await settle();
}

const accBtns = host.querySelectorAll('.tree-accept-btn');
const rejBtns = host.querySelectorAll('.tree-reject-btn');
ok(accBtns.length === 2, 'both differing leaves render an accept button (got ' + accBtns.length + ')');
ok(rejBtns.length === 2, 'both differing leaves render a reject button (got ' + rejBtns.length + ')');
if (!accBtns.length || !rejBtns.length) { console.error(String(fails) + ' check(s) failed'); process.exit(1); }

// Capture the row BEFORE clicking: a successful accept runs _clearIncoming,
// which removes the ✓/✗ buttons themselves — the button is detached afterwards.
const accRow = accBtns[0].closest('.tree-row');
const before = fetches.length;
accBtns[0].click();
await settle();
ok(fetches.length === before + 1,
   'clicking accept issues exactly one request (pre-fix: ReferenceError, zero requests)');
const req = fetches[fetches.length - 1] || { url: '', opts: {} };
ok(req.url.indexOf('/field/edit-batch') === 0,
   'the request is /field/edit-batch (got ' + req.url + ')');
let body = null;
try { body = JSON.parse(req.opts.body); } catch (e) {}
ok(!!(body && body.updates && body.updates.length === 1 &&
      /qubits\.q1\./.test(body.updates[0].dot_path)),
   'the body carries the clicked row\'s dot_path (' +
   (body && body.updates && body.updates[0] && body.updates[0].dot_path) + ')');
ok(accRow && accRow.className.indexOf('tree-row-pending') >= 0,
   'the accepted row turns pending after the edit lands');
ok(!accBtns[0].isConnected,
   'the accepted row\'s buttons are removed (incoming markers cleared)');

// ── 3. ✗ clears the incoming marker and speaks to the bar count ───────────
const rejRow = rejBtns[1].parentElement;
const hadIncoming = rejRow.className.indexOf('tree-row-incoming') >= 0;
ok(hadIncoming, 'precondition: the reject row is marked incoming before the click');
rejBtns[1].click();
await settle();
ok(rejRow.className.indexOf('tree-row-incoming') < 0,
   'clicking reject clears the incoming marker (pre-fix: marker stayed)');
ok(cnt.textContent !== '2',
   'the diff-bar count is updated by the reject (was "2", now "' + cnt.textContent + '")');

// ── 4. docs/124 M-4/M-5 — diff-mode truth is the DOM, both halves together ──
// The old closure flag survived pane swaps while the toggle's class did not:
// a fresh render with diff previously ON produced flag=true/DOM=inactive and
// the FIRST click ran the OFF branch — a silent dead click. And the
// zero-pairs no-op flipped only the flag, leaving a stuck-lit toggle its own
// button could never turn off.
{
    const d = window.document;
    const wireHost = d.createElement('div');
    wireHost.id = 'explorer-tree-wiring';
    d.body.appendChild(wireHost);
    const toggle = d.createElement('button');
    toggle.id = 'explorer-livediff-toggle';
    d.body.appendChild(toggle);
    const bar = d.createElement('div');
    bar.id = 'explorer-livediff-bar';
    bar.hidden = true;
    bar.innerHTML = '<span id="livediff-bar-count"></span>';
    d.body.appendChild(bar);
    window._softRefreshLiveSurface = function () {};

    // fresh render (toggle INACTIVE — what _explorer.html always ships):
    // an argless call must derive ON from the DOM and fetch the diff. With
    // the old shadow flag stuck true, this exact call ran the OFF branch and
    // fetched NOTHING — the dead first click.
    const sHost = d.getElementById('explorer-tree-state');
    sHost._treeData = { qubits: { q1: { f_01: 1 } } };
    wireHost._treeData = { a: 1 };
    liveDiffPayload = { live_state: { qubits: { q1: { f_01: 2 } } },
                        live_wiring: { a: 1 } };
    window.showToast = function () {};   // capture-free stub; jsdom has no toast UI
    const before = fetches.length;
    window.explorerLiveDiff();
    await settle();
    const diffFetches = fetches.slice(before).filter(function (f) {
        return f.url.indexOf('/state/live-diff') === 0;
    });
    ok(diffFetches.length === 1,
       'M-4: with an inactive toggle, the FIRST argless call goes ON and fetches the diff');
    ok(toggle.classList.contains('active') && !bar.hidden,
       'M-4: and the toggle + bar arm together');

    // stuck-lit + zero pairs: the ON path finding nothing must clear BOTH
    // halves — the old code cleared only the flag and the lit toggle lied.
    sHost._treeData = { qubits: { q1: { f_01: 2 } } };
    liveDiffPayload = { live_state: { qubits: { q1: { f_01: 2 } } },
                        live_wiring: { a: 1 } };
    window.explorerLiveDiff(true);
    await settle();
    ok(!toggle.classList.contains('active') && bar.hidden,
       'M-5: the zero-pairs branch clears the toggle AND the bar (no stuck-lit liar)');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})().catch(function (e) {
    console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
    process.exit(1);
});
