/* v1.5.1 SB-F2/F5 + SB-F4: what a page's write requests declare.
 *
 * F2/F5: every non-GET request -- fetch and htmx -- carries the page's chip
 * folder key (X-SM-Chip-Key, from window.__chipKey), so the server can refuse
 * a press from a tab that shows another chip than the one it has open.
 *
 * F4: the grids' "Apply to live now" declares the paths its confirm showed
 * (seen_paths), taken when the confirm was built -- not the tray the grid's
 * own commit refreshed from the server, which already holds an edit another
 * window staged meanwhile. A refusal naming such an edit asks; OK re-posts
 * with ack_unseen, Cancel writes nothing.
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

function tray(attrs, rows) {
    return '<div id="pending-tray" ' + attrs + '><div id="tray-drawer">'
        + (rows || []).map(function (p) {
            return '<div class="tray-change-item"><span class="tray-change-path" title="' + p + '">' + p + '</span></div>';
        }).join('') + '</div></div>';
}

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>' +
    '<div id="topbar-tray-slot">' + tray('data-change-count="0" data-working-dirty="1"', []) + '</div>' +
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

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_tab_chip_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
