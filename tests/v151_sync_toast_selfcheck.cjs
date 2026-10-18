/* v1.5.1 SA-F9 (f): a pull's result toast never says "re-applied 0 edits,
 * and applied them".
 *
 * After an Auto-Sync merge whose edits were already saved, the replay count
 * is 0 and the write carried crash-class values: the toast read "Pulled the
 * live state, re-applied 0 edits, and applied them to the live chip." The
 * count is spoken only when there is one; the failed-replay toasts follow
 * the same rule.
 *
 * Run: node tests/v151_sync_toast_selfcheck.cjs (driven by tests/test_v151_messages.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' +
    '<div id="topbar-tray-slot"><div id="pending-tray" data-change-count="0"></div></div>' +
    '<div id="table-pane"></div>' +
    '</body></html>', { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator', { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'], [window, 'localStorage'], [window, 'sessionStorage']]
    .forEach(function (t) { Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true }); });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
let syncAnswer = { status: 'ok' };
window.fetch = global.fetch = () => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(syncAnswer), text: () => Promise.resolve('') });

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
const toasts = [];
const realToast = window.showToast;
window.showToast = function (msg, level, ms) { toasts.push(String(msg)); return realToast && realToast(msg, level, ms); };

async function press(mode, answer) {
    syncAnswer = answer;
    toasts.length = 0;
    window._applyInFlight = false;
    window.doStateSync(mode);
    await tick(60);
    window._applyInFlight = false;
    return toasts.join(' | ');
}
const ZERO = /\b0 edits?\b|re-applied 0|applied 0/;

(async function main() {
    await tick(30);
    const crash = { sentence: 'qA1 amp is above 1' };
    let t = await press('apply', { status: 'ok', mode: 'apply', replay: { applied: 0, failed: [] },
                                   crash_values: crash });
    ok(t && !ZERO.test(t) && !/applied them/.test(t),
       'a merge that replayed nothing never counts 0 edits -- "' + t + '"');
    ok(/wrote to the live chip/.test(t) && /amp is above 1/.test(t),
       'it still says it wrote, with the advisory -- "' + t + '"');

    t = await press('apply', { status: 'ok', mode: 'apply', replay: { applied: 2, failed: [] },
                               crash_values: crash });
    ok(/re-applied 2 edits, and applied them/.test(t), 'a real count is still spoken -- "' + t + '"');

    t = await press('apply', { status: 'ok', mode: 'apply',
                               replay: { applied: 0, failed: [{ path: 'qubits.qA1.T1' }] } });
    ok(t && !ZERO.test(t) && /1 could not be re-applied/.test(t),
       'a failed replay with nothing applied names only the failure -- "' + t + '"');

    t = await press('reapply', { status: 'ok', mode: 'reapply',
                                 replay: { applied: 0, failed: [{ path: 'qubits.qA1.T1' }] } });
    ok(t && !ZERO.test(t) && /1 could not be re-applied/.test(t),
       'Take live + re-apply says the same -- "' + t + '"');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_sync_toast_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
