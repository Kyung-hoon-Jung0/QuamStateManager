/* v1.3.0 B1 P2: a tray notice that is information is read as a toast.
 *
 * After SM is killed during an Apply whose edits are on disk, the tray carries
 * one notice saying where they are (pending_tray._where_rows_are). Inline in
 * the one-row top bar it squeezed every label ("QSM", "Re", "Sear"); it is
 * now rendered hidden with data-toast and app.js shows it as a toast long
 * enough to read, once. An error notice (rows really gone) stays inline.
 *
 * Run: node tests/v130_tray_notice_toast_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const TEXT = 'SM stopped while applying 1 edit (qubits.q1.chi). They had already reached the live chip, so nothing needed restoring.';
const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' +
    '<div id="pending-tray"><div role="status" class="tray-recovery-notice tray-recovery-info" data-toast="info" hidden>' +
    TEXT + '</div><div role="alert" class="status-error tray-recovery-notice tray-recovery-error">lost rows</div></div>' +
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
window.fetch = global.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') });

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

(async function main() {
    await new Promise((r) => setTimeout(r, 30));
    const bar = window.document.getElementById('status-bar');
    const toasts = [...bar.querySelectorAll('.toast')].map((t) => [t.className, t.textContent]);
    ok(toasts.length === 1 && toasts[0][1] === TEXT && /toast-info/.test(toasts[0][0]),
       'the information notice became one info toast (got ' + JSON.stringify(toasts) + ')');
    ok(!window.document.querySelector('#pending-tray .tray-recovery-info'),
       'the hidden notice left the top bar once shown');
    ok(!!window.document.querySelector('#pending-tray .tray-recovery-error'),
       'an error notice (rows really gone) stays where it was');
    // a later tray swap does not show it again
    window.document.dispatchEvent(new window.Event('htmx:afterSwap'));
    ok(bar.querySelectorAll('.toast').length === 1, 'shown once, not again on the next swap');
    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v130_tray_notice_toast_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
