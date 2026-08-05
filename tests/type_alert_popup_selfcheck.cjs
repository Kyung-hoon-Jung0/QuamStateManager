/* jsdom selfcheck for the self-raising type-anomaly alert (docs/78), running
 * the REAL shipped app.js:
 *
 *  1. It never interrupts: no request while the user is typing, dragging,
 *     looking at another modal, or in a background tab — and it retries when
 *     the moment is calm (the server flag is only consumed by a 200, so
 *     deferring is free).
 *  2. One dialog per content-entry event: two check() calls with one 200
 *     produce one overlay and one request (in-flight guard); a 204 produces
 *     nothing.
 *  3. Closing is not dismissing: Esc / backdrop / Cancel never POST the
 *     dismissal memo — only the explicit "Don't show this again" does, and it
 *     carries BOTH class signatures plus the chip token.
 *  4. Auto-correct is the docs/77 path: the dialog's primary button POSTs
 *     /type-fix/apply with the checked paths + the plan signature, then swaps
 *     the tray and re-announces diagnostics-changed.
 *  5. The manual entry point still works with no argument (/type-fix/plan).
 *
 * Run: node tests/type_alert_popup_selfcheck.cjs  (driven by tests/test_gen_ux_selfchecks.py).
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try {
    ({ JSDOM } = require('jsdom'));
} catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray"></div>' +
    '<input id="a-field" type="text">' +
    '<div id="plot-apply-popup" style="display:none"><div id="plot-apply-rows"></div>' +
    '<button id="plot-apply-all"></button></div>' +
    '</body></html>',
    { url: 'http://localhost/qubits', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.URLSearchParams = window.URLSearchParams;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const htmxTriggers = [];
window.htmx = {
    ajax: function () { return Promise.resolve(); },
    trigger: function (el, name) { htmxTriggers.push(name); },
    process: function () {},
};
global.htmx = window.htmx;

/* The alert card the server would render, with both signatures on it. */
const ALERT_HTML =
    '<div class="tfx-card" data-sig="planSIG" data-alert-sig="SIG1"' +
    ' data-alert-env-sig="ESIG" data-alert-token="fp:abc" data-alert-first="qubits.q1.f_01">' +
    '<p class="tfx-alert-head">2 values have a type problem</p>' +
    '<input type="checkbox" class="tfx-pick" checked data-path="qubits.q1.f_01">' +
    '<input type="checkbox" class="tfx-pick" checked data-path="qubits.q1.T1">' +
    '<button id="tfx-apply">Convert <span id="tfx-count">2</span> field(s)</button>' +
    '<div class="tfx-error" hidden></div></div>';

let alertStatus = 200;              // what /type-alert answers
const calls = [];                   // every fetch, in order
let applyBody = null, dismissBody = null;
window.fetch = global.fetch = function (url, opts) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/type-alert') === 0) {
        return Promise.resolve({
            status: alertStatus,
            text: function () { return Promise.resolve(alertStatus === 200 ? ALERT_HTML : ''); },
            json: function () { return Promise.resolve({}); },
        });
    }
    if (u.indexOf('/type-fix/apply') === 0) {
        applyBody = JSON.parse((opts && opts.body) || '{}');
        return Promise.resolve({
            status: 200,
            json: function () {
                return Promise.resolve({ ok: true, count: 2, tray_html: '<div id="t"></div>' });
            },
            text: function () { return Promise.resolve(''); },
        });
    }
    if (u.indexOf('/type-alarm/dismiss') === 0) {
        dismissBody = (opts && opts.body) || '';
        return Promise.resolve({ status: 200, text: () => Promise.resolve(''),
                                 json: () => Promise.resolve({}) });
    }
    if (u.indexOf('/type-fix/plan') === 0) {
        return Promise.resolve({ status: 200, text: () => Promise.resolve(ALERT_HTML),
                                 json: () => Promise.resolve({}) });
    }
    return Promise.resolve({ status: 200, text: () => Promise.resolve(''),
                             json: () => Promise.resolve({}) });
};

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

/* app.js declares _swapPendingTray as a top-level function: a real browser
   exposes that on window, jsdom's window.eval does not. Stub it so the apply
   path runs to completion here AND we can see it was called. */
const traySwaps = [];
window._swapPendingTray = function (html) { traySwaps.push(html); };

function alertCalls() { return calls.filter(function (u) { return u.indexOf('/type-alert') === 0; }); }
function overlay() { return document.querySelector('.tfx-overlay'); }
function overlayOpen() { const o = overlay(); return !!o && o.style.display === 'flex'; }

(async function () {
    ok(typeof window.TypeAlert === 'object' && typeof window.TypeAlert.check === 'function',
       'window.TypeAlert.check exists');

    /* ── 1. never interrupt ─────────────────────────────────────────────── */
    const field = document.getElementById('a-field');
    field.focus();
    window.TypeAlert.check();
    await flush(20);
    ok(alertCalls().length === 0, 'no request while an input has focus');

    field.blur();
    document.body.classList.add('dragging');
    window.TypeAlert.check();
    await flush(20);
    ok(alertCalls().length === 0, 'no request mid-drag');
    document.body.classList.remove('dragging');

    document.getElementById('plot-apply-popup').style.display = 'flex';
    window.TypeAlert.check();
    await flush(20);
    ok(alertCalls().length === 0, 'no request while another modal is open');
    document.getElementById('plot-apply-popup').style.display = 'none';

    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    window.TypeAlert.check();
    await flush(20);
    ok(alertCalls().length === 0, 'no request in a background tab (the one-shot survives)');
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });

    /* ── 2. one dialog per event ────────────────────────────────────────── */
    window.TypeAlert.check();
    window.TypeAlert.check();               // second press while in flight
    await flush(30);
    ok(alertCalls().length === 1, 'two checks in flight produce ONE request (got '
       + alertCalls().length + ')');
    ok(document.querySelectorAll('.tfx-card').length === 1, 'exactly one dialog is mounted');
    ok(overlayOpen(), 'the overlay is open');
    ok(/type problem/.test(overlay().textContent), 'the alert header is shown');
    ok(overlay().querySelectorAll('.tfx-pick').length === 2,
       'the per-field proposal is IN the popup (auto-correct is never blind)');
    ok(typeof overlay()._releaseTrap === 'function', 'focus is trapped while open');

    /* ── 3. closing is not dismissing ───────────────────────────────────── */
    window.closeTypeFixPlan();
    ok(!overlayOpen(), 'closeTypeFixPlan hides the overlay');
    ok(overlay()._releaseTrap === null, 'the focus trap is released on close');
    await flush(10);
    ok(dismissBody === null, 'Cancel / Esc / backdrop never memo a dismissal');

    /* a 204 must not open anything */
    calls.length = 0;
    alertStatus = 204;
    window.TypeAlert.check();
    await flush(20);
    ok(alertCalls().length === 1 && !overlayOpen(), '204 opens no dialog');
    alertStatus = 200;

    /* ── 4. auto-correct = the docs/77 apply path ───────────────────────── */
    calls.length = 0;
    window.TypeAlert.check();
    await flush(30);
    ok(overlayOpen(), 're-armed alert opens again');
    ok(window.typeFixCount() === 2, 'the count is scoped to the open dialog');
    window.typeFixApply(document.getElementById('tfx-apply'));
    await flush(30);
    ok(applyBody && applyBody.paths && applyBody.paths.length === 2,
       'apply POSTs the checked paths');
    ok(applyBody && applyBody.sig === 'planSIG',
       'apply carries the plan signature (server re-validates)');
    ok(!overlayOpen(), 'the dialog closes after a successful repair');
    ok(traySwaps.length === 1, 'the repair swaps the Review tray (one group, one undo)');
    ok(htmxTriggers.indexOf('diagnostics-changed') !== -1,
       'the repair re-announces diagnostics-changed (banner + card refresh)');

    /* ── 5. explicit dismissal carries both signatures ──────────────────── */
    calls.length = 0;
    window.TypeAlert.check();
    await flush(30);
    const card = document.querySelector('.tfx-card');
    window.TypeAlert.dismiss(card.querySelector('#tfx-apply'));
    await flush(20);
    ok(typeof dismissBody === 'string' && dismissBody.indexOf('sig=SIG1') !== -1,
       'dismiss sends the stored-as-text signature');
    ok(dismissBody.indexOf('env_sig=ESIG') !== -1,
       'dismiss sends the env-mismatch signature too (classes are independent)');
    ok(dismissBody.indexOf('token=fp') !== -1, 'dismiss is keyed to the chip token');
    ok(!overlayOpen(), 'dismiss closes the dialog');

    /* ── 6. the manual entry point is unchanged ─────────────────────────── */
    calls.length = 0;
    window.openTypeFixPlan();
    await flush(20);
    ok(calls.some(function (u) { return u.indexOf('/type-fix/plan') === 0; }),
       'openTypeFixPlan() with no argument still fetches /type-fix/plan');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('ALL OK');
    process.exit(0);
})();
