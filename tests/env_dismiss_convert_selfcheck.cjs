/* jsdom selfcheck, QA diagnostics-r2-17 + F-O, against the REAL app.js:
 *
 *  1. "Don't show this again" in the env-schema overlay (envSchemaDismiss)
 *     re-announces diagnostics-changed on a 2xx -- the types card listens for
 *     it and re-renders saying the set is hidden. It used to POST and close
 *     with nothing on screen changing until F5.
 *  2. A refused dismissal (non-2xx) does NOT announce a change and says so
 *     with a toast; the overlay still closes either way.
 *  3. A FAILED type-fix apply restores the Convert button's markup, not its
 *     flattened text: #tfx-count survives, so ticking a row afterwards still
 *     updates the number (it used to go stale after the first failure).
 *
 * Run: node tests/env_dismiss_convert_selfcheck.cjs
 *      (driven by tests/test_env_schema_routes.py::test_env_dismiss_convert_selfcheck)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray"></div>' +
    '</body></html>',
    { url: 'http://localhost/diagnostics', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.URLSearchParams = window.URLSearchParams;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const triggers = [];
window.htmx = {
    ajax: function () { return Promise.resolve(); },
    trigger: function (el, name) { triggers.push(name); },
    process: function () {},
};
global.htmx = window.htmx;

/* The overlay the server renders for a changed env transition. */
const CHANGES_HTML =
    '<div class="tfx-card env-changes-card" data-from="fromK" data-to="toK" data-sig="SIG">' +
    '<div class="tfx-actions"><button type="button" id="dismiss"' +
    ' onclick="window.envSchemaDismiss(this)">Don’t show this again</button></div>' +
    '<div class="tfx-error" hidden></div></div>';

let dismissStatus = 200, dismissBody = null;
window.fetch = global.fetch = function (url, opts) {
    const u = String(url);
    if (u.indexOf('/env-schema/changes') === 0) {
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(CHANGES_HTML),
                                 json: () => Promise.resolve({}) });
    }
    if (u.indexOf('/env-schema/dismiss') === 0) {
        dismissBody = (opts && opts.body) || '';
        return Promise.resolve({ ok: dismissStatus < 300, status: dismissStatus,
                                 text: () => Promise.resolve(''), json: () => Promise.resolve({}) });
    }
    if (u.indexOf('/type-fix/apply') === 0) {
        return Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({
            ok: false, error: 'The plan changed underneath you.' }) });
    }
    return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(''),
                             json: () => Promise.resolve({}) });
};

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window._swapPendingTray = function () {};
const d = window.document;

function overlayOpen() {
    const o = d.querySelector('.envchg-overlay');
    return !!o && o.style.display === 'flex';
}
function toasts() { return Array.prototype.map.call(d.querySelectorAll('#status-bar .toast'), (t) => t.textContent); }

(async function () {
    await flush(50);

    /* 1. a successful dismissal refreshes the card */
    window.openEnvSchemaChanges();
    await flush(30);
    ok(overlayOpen() && !!d.getElementById('dismiss'), 'the overlay opened with the dismiss button');
    triggers.length = 0;
    window.envSchemaDismiss(d.getElementById('dismiss'));
    await flush(30);
    ok(/from_key=fromK/.test(dismissBody) && /sig=SIG/.test(dismissBody),
       'the dismissal carries the transition keys + signature (' + dismissBody + ')');
    ok(!overlayOpen(), 'the overlay closes');
    ok(triggers.indexOf('diagnostics-changed') !== -1,
       'a 2xx dismissal re-announces diagnostics-changed (the card re-renders)');
    ok(toasts().length === 0, 'no failure toast on success');

    /* 2. a refused dismissal says so and announces nothing */
    dismissStatus = 400;
    window.openEnvSchemaChanges();
    await flush(30);
    triggers.length = 0;
    window.envSchemaDismiss(d.getElementById('dismiss'));
    await flush(30);
    ok(!overlayOpen(), 'the overlay closes on a refusal too');
    ok(triggers.indexOf('diagnostics-changed') === -1, 'a refused dismissal announces no change');
    ok(toasts().some((t) => /Could not hide this set/.test(t)), 'a refused dismissal says so');

    /* 3. a failed apply keeps the live count span */
    const card = d.createElement('div');
    card.className = 'tfx-card';
    card.setAttribute('data-sig', 'P');
    card.innerHTML =
        '<input type="checkbox" class="tfx-pick" checked data-path="qubits.q1.T1">' +
        '<input type="checkbox" class="tfx-pick" checked data-path="qubits.q1.f_01">' +
        '<button id="tfx-apply"><span class="tfx-apply-label">Convert ' +
        '<span id="tfx-count">2</span> field(s)</span></button>' +
        '<div class="tfx-error" hidden></div>';
    d.getElementById('inspector-pane').appendChild(card);
    window.typeFixApply(d.getElementById('tfx-apply'));
    await flush(30);
    ok(!card.querySelector('.tfx-error').hidden, 'the apply failed visibly (the rig reached the error path)');
    ok(!!d.getElementById('tfx-count'), 'the count span survives a failed apply');
    card.querySelector('.tfx-pick').checked = false;
    window.typeFixCount();
    const label = d.getElementById('tfx-apply').textContent.replace(/\s+/g, ' ').trim();
    ok(label === 'Convert 1 field(s)', 'unticking a row after the failure updates the count (' + label + ')');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('ALL OK');
    process.exit(0);
})();
