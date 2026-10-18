/* v1.5.1 SA-F2: a failed live write stays on screen and swaps its tray.
 *
 *  - showToast(msg, level, -1) is sticky: no fade timer, an x to close it;
 *  - a 409 carrying X-SM-Error-Code into #pending-tray is swapped (its tray
 *    may carry the read-only pill) and is not an htmx error, and an in-flight
 *    Keep mine learns that nothing was written;
 *  - the same 409 into a target that cannot show it becomes a sticky toast
 *    and the tray re-reads;
 *  - doStateSync's {status: error, error_code} swaps the returned tray and
 *    shows a sticky toast; a plain error keeps the timed toast.
 *
 * Run: node tests/v151_live_write_failure_selfcheck.cjs
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
    '<div id="topbar-tray-slot"><div id="pending-tray" data-change-count="1"></div></div>' +
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
const ajaxCalls = [];
window.htmx = { ajax: (m, u, o) => { ajaxCalls.push([m, u]); return Promise.resolve(); },
                trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
let syncAnswer = { status: 'ok' };
window.fetch = global.fetch = () => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(syncAnswer), text: () => Promise.resolve('') });

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
const bar = () => window.document.getElementById('status-bar');
function xhr(status, code, text) {
    return { status: status, responseText: text || '',
             getResponseHeader: (h) => (h === 'X-SM-Error-Code' ? code : null) };
}

(async function main() {
    await tick(30);
    // 1. sticky toast
    const realSet = window.setTimeout;
    const timers = [];
    window.setTimeout = function (f, ms) { timers.push(ms); return realSet(f, ms); };
    const t = window.showToast('Apply to live failed: the live folder refused the write', 'error', -1);
    window.setTimeout = realSet;
    ok(t && t.classList.contains('toast-sticky'), 'a sticky toast is marked sticky');
    ok(timers.length === 0, 'no fade timer for a sticky toast (got ' + JSON.stringify(timers) + ')');
    const x = t && t.querySelector('.toast-x');
    ok(!!x, 'a sticky toast has its x');
    x.click();
    ok(!bar().contains(t), 'the x closes it');

    // 2. beforeSwap into the tray
    window._keepMineInFlight = {};
    const tray = window.document.getElementById('pending-tray');
    const det = { xhr: xhr(409, 'live_write_failed'), target: tray, shouldSwap: false, isError: true,
                  requestConfig: { path: '/state/apply-to-live?force=1' } };
    tray.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: det }));
    ok(det.shouldSwap === true && det.isError === false, 'a named failure into the tray is swapped, not an error');
    ok(window._keepMineInFlight._writeFailed === 'live_write_failed', 'Keep mine learns nothing was written');
    window._keepMineInFlight = null;
    const det2 = { xhr: xhr(409, null), target: tray, shouldSwap: false, isError: true,
                   requestConfig: { path: '/state/apply-to-live' } };
    tray.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: det2 }));
    ok(det2.shouldSwap === false, 'a 409 without the code keeps htmx default handling');

    // 3. responseError elsewhere -> sticky toast + tray re-read
    const pane = window.document.getElementById('table-pane');
    ajaxCalls.length = 0;
    pane.dispatchEvent(new window.CustomEvent('htmx:responseError', { bubbles: true, detail: {
        target: pane, xhr: xhr(409, 'live_write_failed',
            '<div class="toast toast-error"><p>Restore to live failed: denied</p></div>') } }));
    const last = [...bar().querySelectorAll('.toast')].pop();
    ok(last && /Restore to live failed/.test(last.textContent) && last.classList.contains('toast-sticky'),
       'a named failure the target cannot show is a sticky toast');
    ok(ajaxCalls.some((c) => c[0] === 'GET' && c[1] === '/state/tray'), 'and the tray re-reads');

    // 4. doStateSync with error_code
    syncAnswer = { status: 'error', error_code: 'live_write_failed',
                   message: 'Apply to live failed: the live folder refused the write',
                   tray_html: '<div id="pending-tray" data-change-count="1"><span class="tray-ro-lock">ro</span></div>' };
    window.doStateSync('apply');
    await tick(40);
    ok(!!window.document.querySelector('#pending-tray .tray-ro-lock'), 'the returned tray (read-only pill) is swapped in');
    const sticky = [...bar().querySelectorAll('.toast-sticky')].pop();
    ok(sticky && /Sync failed: Apply to live failed/.test(sticky.textContent), 'the sync failure stays on screen');
    window._applyInFlight = false;

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_live_write_failure_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
