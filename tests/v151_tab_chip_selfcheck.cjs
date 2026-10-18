/* v1.5.1 SB-F2/F5 + SB-F4: what a page's write requests declare.
 *
 * F2/F5: every non-GET request -- fetch and htmx -- carries the page's chip
 * folder key (X-SM-Chip-Key, from window.__chipKey), so the server can refuse
 * a press from a tab that shows another chip than the one it has open.
 *
 * F4: a refusal of the grids' "Apply to live now" naming an edit its confirm
 * never showed asks; OK re-posts with ack_unseen, Cancel writes nothing. WHAT
 * the push declares (seen_paths, read from the real tray markup) is pinned in
 * tests/test_v151_grid_push_real_tray.py (R2 M1) -- this harness renders no
 * tray rows of its own.
 *
 * Run: node tests/v151_tab_chip_selfcheck.cjs (driven by tests/test_v151_sb_tab_chip.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

/* Only the attributes the real _pending_tray.html renders. The pending ROWS
 * are never invented here: what the grid push reads from the real markup is
 * pinned by tests/test_v151_grid_push_real_tray.py (R2 M1). */
function tray(attrs) {
    return '<div id="pending-tray" ' + attrs + '></div>';
}

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' +
    '<div id="topbar-tray-slot">' + tray('data-change-count="0" data-working-dirty="1"') + '</div>' +
    '<div id="table-pane"></div></body></html>', { url: 'http://localhost/bulk', pretendToBeVisual: true });
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
// the browser has these; bridge Node's into the window realm
window.Headers = global.Headers;
window.Request = global.Request;

const ajaxCalls = [];
window.htmx = {
    ajax: function (verb, url, opts) { ajaxCalls.push({ verb: verb, url: url, opts: opts || {} }); return Promise.resolve(); },
    trigger: () => {}, process: () => {},
};
global.htmx = window.htmx;
const fetchCalls = [];
let fetchAnswer = { status: 'ok' };
window.fetch = global.fetch = function (input, init) {
    fetchCalls.push({ input: input, init: init || {} });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(fetchAnswer),
                             text: () => Promise.resolve('') });
};

window.__chipKey = 'chip_x-1a2b3c4d';
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
window.eval(fs.readFileSync(path.join(STATIC, 'tab-chip.js'), 'utf8'));
// in a browser a bare fetch IS window.fetch; bridge the wrapped one
global.fetch = window.fetch;

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
function hdr(call) {
    const h = call.init.headers;
    if (!h) return null;
    return typeof h.get === 'function' ? h.get('X-SM-Chip-Key') : h['X-SM-Chip-Key'];
}

(async function main() {
    // ---- F2/F5: the header ------------------------------------------------
    fetchCalls.length = 0;
    window.fetch('/state/sync', { method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' }, body: 'mode=apply' });
    let c = fetchCalls[0];
    ok(c && hdr(c) === 'chip_x-1a2b3c4d', 'a fetch POST names the page chip -- ' + (c && hdr(c)));
    ok(c && c.init.headers.get('Content-Type') === 'application/x-www-form-urlencoded'
       && c.init.headers.get('HX-Request') === 'true', 'and keeps the headers it was given');
    window.fetch('/state/tray');
    ok(!hdr(fetchCalls[1]), 'a GET carries no chip key');
    window.fetch('http://elsewhere.example/x', { method: 'POST' });
    ok(!hdr(fetchCalls[2]), 'a request to another origin carries no chip key');

    let det = { verb: 'post', headers: {} };
    document.dispatchEvent(new window.CustomEvent('htmx:configRequest', { detail: det }));
    ok(det.headers['X-SM-Chip-Key'] === 'chip_x-1a2b3c4d', 'an htmx POST names the page chip');
    det = { verb: 'get', headers: {} };
    document.dispatchEvent(new window.CustomEvent('htmx:configRequest', { detail: det }));
    ok(!det.headers['X-SM-Chip-Key'], 'an htmx GET does not');
    window.__chipKey = '';
    det = { verb: 'post', headers: {} };
    document.dispatchEvent(new window.CustomEvent('htmx:configRequest', { detail: det }));
    ok(!det.headers['X-SM-Chip-Key'], 'a page with no chip sends no key (no gate, as before)');
    window.__chipKey = 'chip_x-1a2b3c4d';

    // ---- the page's own code ----------------------------------------------
    window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    await tick(30);
    const confirms = [];
    let confirmAnswer = true;
    window.confirm = function (m) { confirms.push(String(m)); return confirmAnswer; };
    const toasts = [];
    window.showToast = function (msg, level, ms) { toasts.push({ msg: String(msg), level: level, ms: ms }); };
    function setTray(attrs) {
        document.getElementById('topbar-tray-slot').innerHTML = tray(attrs);
    }

    // ---- F4: the refused push asks (what it declares is pinned on the real
    // markup by tests/test_v151_grid_push_real_tray.py) ---------------------
    setTray('data-change-count="2" data-change-sig="abc" data-working-dirty="1"');
    let a, vals;
    // the server refuses (q4 unseen): the user is asked, and OK writes them too
    window._gridPushInFlight = { seen: ['qubits.q1.T1'] };
    const evt = new window.CustomEvent('htmx:beforeSwap', { detail: {
        xhr: { status: 409, responseText: JSON.stringify({ status: 'unseen_changes',
               paths: ['qubits.q4.T1'], message: '1 pending edit not shown in this confirm' }) },
        target: document.getElementById('pending-tray'),
        requestConfig: { path: '/state/apply-to-live' }, shouldSwap: true, isError: true } });
    ajaxCalls.length = 0;
    confirmAnswer = true;
    document.dispatchEvent(evt);
    await tick(20);
    ok(evt.detail.shouldSwap === false && evt.detail.isError === false, 'the refusal is not swapped as an error');
    ok(confirms.length && /qubits\.q4\.T1/.test(confirms[confirms.length - 1]),
       'the confirm names the other window\'s edit -- ' + confirms[confirms.length - 1]);
    a = ajaxCalls[0];
    vals = (a && a.opts.values) || {};
    ok(a && vals.ack_unseen === '1' && vals.seen_paths === JSON.stringify(['qubits.q1.T1']),
       'OK re-posts with ack_unseen -- ' + JSON.stringify(vals));
    window._gridPushInFlight = { seen: ['qubits.q1.T1'] };
    ajaxCalls.length = 0;
    confirmAnswer = false;
    document.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { detail: {
        xhr: { status: 409, responseText: JSON.stringify({ status: 'unseen_changes', paths: ['qubits.q4.T1'] }) },
        target: document.getElementById('pending-tray'),
        requestConfig: { path: '/state/apply-to-live' }, shouldSwap: true, isError: true } }));
    await tick(20);
    ok(!ajaxCalls.some(function (x) { return x.verb === 'POST'; }), 'Cancel writes nothing');
    window._gridPushInFlight = null;

    // a push with no confirm record declares the tray it finds (as before)
    window._livePushSeen = null;
    setTray('data-change-count="1" data-change-sig="def" data-working-dirty="1"');
    ajaxCalls.length = 0;
    window._applyInFlight = false;
    window.applyEditsToLive();
    vals = (ajaxCalls[0] && ajaxCalls[0].opts.values) || {};
    ok(vals.seen_changes === '1' && vals.seen_sig === 'def' && !vals.seen_paths,
       'without a confirm the push declares the tray -- ' + JSON.stringify(vals));

    // ---- F5: a wrong-chip answer to a sync press stays on screen -----------
    toasts.length = 0;
    fetchAnswer = { ok: false, status: 'chip_mismatch', chip_mismatch: true,
                    message: "Not done: this tab shows chip 'chip_x'; the server has chip 'chip_y' open now." };
    window._applyInFlight = false;
    window.doStateSync('discard');
    await tick(40);
    const t = toasts.find(function (x) { return /chip_x/.test(x.msg); });
    ok(t && t.level === 'error' && t.ms === -1, 'a refused Take live says which chips, until closed -- '
       + JSON.stringify(toasts));
    ok(!toasts.some(function (x) { return /Took live|Written/.test(x.msg); }), 'and never claims it pulled');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_tab_chip_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
