/* jsdom selfcheck for the r16 6 apply-UX hardening (docs/65 amendment),
 * running the REAL app.js:
 *
 *  A1. doStateSync needs_confirm + DECLINE -> an explicit "Cancelled" toast
 *      (used to end silently — the click looked accepted).
 *  A2. applyEditsToLive with stale zero tray attrs -> re-checks the SERVER
 *      (GET /state/tray); when the fresh tray shows pending changes the
 *      routing re-runs and actually applies.
 *  A3. When the fresh tray is genuinely empty -> exactly one recheck + the
 *      honest "Nothing to apply" toast (no loop).
 *
 * Run: node tests/apply_ux_selfcheck.cjs  (driven by tests/test_apply_ux.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div>' +
    '<div id="pending-tray" data-change-count="0" data-working-dirty="0"></div>' +
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
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const ajaxCalls = [];
let trayOnRefetch = null;    // attrs the /state/tray "server" applies
window.htmx = {
    ajax: function (method, url, opts) {
        ajaxCalls.push({ method, url, opts });
        if (String(url) === '/state/tray' && trayOnRefetch) {
            const t = window.document.getElementById('pending-tray');
            t.setAttribute('data-change-count', trayOnRefetch.cc);
            t.setAttribute('data-working-dirty', trayOnRefetch.dirty);
        }
        return Promise.resolve();
    },
    trigger: function () {},
    process: function () {},
};
global.htmx = window.htmx;

const toasts = [];
window.showToast = function (msg) { toasts.push(String(msg)); };

const syncBodies = [];
let syncPayload = { status: 'ok', mode: 'apply' };
window.fetch = global.fetch = function (url, opts) {
    const u = String(url);
    if (u.indexOf('/state/sync') === 0) {
        syncBodies.push((opts && opts.body) || '');
        return Promise.resolve({ status: 200,
            json: () => Promise.resolve(syncPayload), text: () => Promise.resolve('') });
    }
    return Promise.resolve({ status: 200,
        json: () => Promise.resolve({}), text: () => Promise.resolve('') });
};

let confirmAnswer = false;
window.confirm = function () { return confirmAnswer; };

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
// app.js installs its own showToast at eval — re-stub AFTER so we capture.
window.showToast = function (msg) { toasts.push(String(msg)); };

(async function () {
    /* A1 — needs_confirm decline toasts */
    syncPayload = { status: 'needs_confirm', mode: 'discard', message: 'staged content' };
    confirmAnswer = false;
    toasts.length = 0;
    window.doStateSync('discard');
    await flush(25);
    ok(toasts.some(function (t) { return t.indexOf('Cancelled') === 0; }),
       'A1: declined confirm shows an explicit Cancelled toast');

    /* A2 — stale zero tray re-checks the server, then really applies */
    syncPayload = { status: 'ok', mode: 'apply' };
    trayOnRefetch = { cc: '2', dirty: '0' };     // server truth: 2 pending
    ajaxCalls.length = 0;
    syncBodies.length = 0;
    toasts.length = 0;
    window.applyEditsToLive();
    await flush(30);
    ok(ajaxCalls.some(function (c) { return c.url === '/state/tray'; }),
       'A2: zero attrs -> server recheck via GET /state/tray');
    ok(syncBodies.some(function (b) { return b.indexOf('mode=apply') === 0; }),
       'A2: fresh tray shows pending -> the apply actually fires');
    ok(!toasts.some(function (t) { return t.indexOf('Nothing to apply') === 0; }),
       'A2: no false "Nothing to apply" when the server had changes');

    /* A3 — genuinely empty: one recheck + honest toast, no loop */
    const tray = window.document.getElementById('pending-tray');
    tray.setAttribute('data-change-count', '0');
    tray.setAttribute('data-working-dirty', '0');
    trayOnRefetch = { cc: '0', dirty: '0' };
    ajaxCalls.length = 0;
    toasts.length = 0;
    window.applyEditsToLive();
    await flush(30);
    const rechecks = ajaxCalls.filter(function (c) { return c.url === '/state/tray'; });
    ok(rechecks.length === 1, 'A3: exactly one server recheck (no loop)');
    ok(toasts.some(function (t) { return t.indexOf('Nothing to apply') === 0; }),
       'A3: honest no-op toast after the server confirmed empty');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('apply_ux_selfcheck: all checks passed');
    process.exit(0);
})().catch(function (e) { console.error('UNCAUGHT:', e && e.stack || e); process.exit(1); });
