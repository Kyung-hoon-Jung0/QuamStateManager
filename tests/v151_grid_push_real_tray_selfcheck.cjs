/* v1.5.1 R2 M1: the grids' "Apply to live now" declares the rows its confirm
 * showed -- read from the REAL tray markup.
 *
 * The first fix read the tray's rows from `#tray-drawer .tray-change-item`,
 * which no template renders (the rows live in the sync panel); its pin
 * invented that element. On the real page the capture was empty, the push
 * declared `seen_paths=[]`, and a single-window user's own edits were refused
 * as "staged in another State Manager window?".
 *
 * This harness never builds a tray: tests/test_v151_grid_push_real_tray.py
 * renders /bulk (the full page) and /state/tray (the fragment) with the Flask
 * test client and hands their HTML over in a fixture file. The #pending-tray
 * element is taken from that markup as rendered; the REAL tab-chip.js, app.js
 * and bulk-edit.js run on it under jsdom. Every write request the page makes
 * is printed (REQ ...) so the driver can replay it against the same server.
 *
 * Run: node tests/v151_grid_push_real_tray_selfcheck.cjs <fixture.json>
 *      (driven by tests/test_v151_grid_push_real_tray.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const fixturePath = process.argv[2];
if (!fixturePath) {
    console.error('usage: node v151_grid_push_real_tray_selfcheck.cjs <fixture.json> '
                  + '(the driver renders the real markup)');
    process.exit(1);
}
const FX = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

/* #pending-tray exactly as the server rendered it, from a page or a fragment */
function realTray(html) {
    const d = new JSDOM('<!doctype html><html><body>' + html + '</body></html>').window.document;
    const t = d.getElementById('pending-tray');
    if (!t) throw new Error('the rendered markup has no #pending-tray');
    return t.outerHTML;
}
function pageGlobal(html, name) {
    const m = new RegExp('window\\.' + name + ' = ("[^"]*");').exec(html);
    return m ? JSON.parse(m[1]) : '';
}

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<ul><li id="topbar-tray-slot">' + realTray(FX.tray_html) + '</li></ul>'
    + '<div id="table-pane"></div></body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
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
window.Headers = global.Headers;
window.Request = global.Request;

function out(tag, obj) { console.log(tag + ' ' + JSON.stringify(obj)); }

window.htmx = {
    ajax: function (verb, url, opts) {
        opts = opts || {};
        const det = { verb: String(verb).toLowerCase(), headers: {} };
        document.dispatchEvent(new window.CustomEvent('htmx:configRequest', { detail: det }));
        if (det.verb !== 'get') out('REQ', { via: 'htmx', url: url, values: opts.values || {}, headers: det.headers });
        return Promise.resolve();
    },
    trigger: () => {}, process: () => {},
};
global.htmx = window.htmx;
window.fetch = global.fetch = function (input, init) {
    init = init || {};
    const method = String(init.method || 'GET').toUpperCase();
    if (method !== 'GET') {
        const h = {};
        if (init.headers && typeof init.headers.forEach === 'function') {
            init.headers.forEach(function (v, k) { h[k] = v; });
        } else Object.assign(h, init.headers || {});
        out('REQ', { via: 'fetch', url: String(input), body: String(init.body || ''), headers: h });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'ok' }),
                             text: () => Promise.resolve('') });
};
window.__chipKey = pageGlobal(FX.page_html || '', '__chipKey');
window.__chipToken = pageGlobal(FX.page_html || '', '__chipToken');

window.eval(fs.readFileSync(path.join(STATIC, 'tab-chip.js'), 'utf8'));
global.fetch = window.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));
window.confirm = function (m) { out('CONFIRM', String(m)); return true; };
window.showToast = function (m, level) { out('TOAST', { msg: String(m), level: level }); };

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
function swapTray(html) {
    document.getElementById('topbar-tray-slot').innerHTML = realTray(html);
}

(async function main() {
    await tick(30);
    window._applyInFlight = false;
    const sc = FX.scenario;
    if (sc === 'type_then_click') {
        // the click-away committed the typed cell; the push button then runs
        window.BulkEdit._applyCommittedToLive();
        await tick(60);
    } else if (sc === 'stale_confirm') {
        // the confirm is built on this tab's tray; the grid's commit then
        // swaps in the server's tray, which holds another window's edit
        out('EXTRAS', window.livePushExtrasLine(FX.typed || []));
        swapTray(FX.tray_after_html);
        window.applyEditsToLive();
        await tick(60);
    } else {
        console.error('unknown scenario ' + sc);
        process.exit(1);
    }
    console.log('DONE');
    process.exit(0);
})();
