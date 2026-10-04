/* docs/282 -- while the change ledger is being built, the value drawer and
 * Column History show the server's "being built" line (a [data-vh-retry]
 * element) and ask again by themselves; they stop asking when the panel is
 * closed, and a retry of an older open never overwrites a newer one.
 *
 * Run: node tests/vh_retry_selfcheck.cjs
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
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
global.URLSearchParams = window.URLSearchParams;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

// The server's answers, scripted per URL+body: a queue of HTML bodies.
const queues = {};
const calls = [];
function fetchStub(url, opts) {
    const key = url.split('?')[0] + '|' + (url.split('path=')[1] || '') ;
    calls.push({ url: url, body: opts && opts.body });
    const q = queues[key] || queues[url.split('?')[0]] || [];
    const item = q.length > 1 ? q.shift() : q[0];
    const html = (item && typeof item === 'object') ? item.html : item;
    const delay = (item && typeof item === 'object') ? item.delay : 0;
    const resp = { ok: true, status: 200, text: () => Promise.resolve(html || '') };
    return delay ? new Promise((r) => setTimeout(() => r(resp), delay)) : Promise.resolve(resp);
}
global.fetch = fetchStub;
Object.defineProperty(window, 'fetch', { value: fetchStub, configurable: true, writable: true });

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0, oks = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { oks++; console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const WAIT = '<p class="vh-wait" data-vh-retry="40" data-vh-mode="building">The change history is being built (1 of 4 runs).</p>';
const DONE = (v) => '<table class="fh-table vh-table"><tr class="vh-row"><td><code>' + v + '</code></td></tr></table>';

(async function main() {
    const anchor = window.document.createElement('button');
    window.document.body.appendChild(anchor);
    anchor.getBoundingClientRect = () => ({ left: 10, top: 10, right: 30, bottom: 30, width: 20, height: 20 });

    // 1. building -> building -> the history: two retries, then it stops asking
    queues['/field/history|qubits.q1.T1'] = [WAIT, WAIT, DONE('ready-T1')];
    window.FieldHistory.open(anchor, 'qubits.q1.T1', null);
    await wait(30);
    const panel = window.document.getElementById('field-history-panel');
    ok(panel.textContent.indexOf('being built (1 of 4 runs)') >= 0,
       'the drawer shows the building line, not a history');
    await wait(200);
    ok(panel.textContent.indexOf('ready-T1') >= 0,
       'it asked again by itself and shows the history once the ledger is ready');
    const n1 = calls.filter(c => c.url.indexOf('qubits.q1.T1') >= 0).length;
    ok(n1 === 3, 'three requests: the first and two retries (' + n1 + ')');
    await wait(150);
    ok(calls.filter(c => c.url.indexOf('qubits.q1.T1') >= 0).length === n1,
       'and none after the history arrived');

    // 2. closing the drawer stops the retries
    queues['/field/history|qubits.q1.f_01'] = [WAIT];
    window.FieldHistory.open(anchor, 'qubits.q1.f_01', null);
    await wait(20);
    window.FieldHistory.close();
    const n2 = calls.filter(c => c.url.indexOf('qubits.q1.f_01') >= 0).length;
    await wait(200);
    ok(calls.filter(c => c.url.indexOf('qubits.q1.f_01') >= 0).length === n2,
       'a closed drawer asks no more (' + n2 + ')');

    // 3. an older open's retry never overwrites a newer open's panel
    queues['/field/history|qubits.q1.x'] = [WAIT, DONE('stale-x')];
    queues['/field/history|qubits.q1.y'] = [DONE('fresh-y')];
    window.FieldHistory.open(anchor, 'qubits.q1.x', null);
    await wait(10);
    window.FieldHistory.open(anchor, 'qubits.q1.y', null);
    await wait(200);
    ok(panel.textContent.indexOf('fresh-y') >= 0 && panel.textContent.indexOf('stale-x') < 0,
       'the newer field stays on screen; the older retry is dropped');

    // 3b. ...and a slow answer for an older open never lands over a newer one
    queues['/field/history|qubits.q1.slow'] = [{ html: DONE('stale-slow'), delay: 60 }];
    queues['/field/history|qubits.q1.fast'] = [DONE('fresh-fast')];
    window.FieldHistory.open(anchor, 'qubits.q1.slow', null);
    await wait(5);
    window.FieldHistory.open(anchor, 'qubits.q1.fast', null);
    await wait(150);
    ok(panel.textContent.indexOf('fresh-fast') >= 0 && panel.textContent.indexOf('stale-slow') < 0,
       'a slow answer for the older field is dropped');

    // 4. Column History: the same rule for the column card
    const table = window.document.createElement('table');
    table.innerHTML = '<thead><tr><th data-col-key="t1"><button class="bulk-col-hist" data-grid="qubit" data-label="T1">h</button></th></tr></thead>' +
        '<tbody><tr data-qubit="q1"><td data-col-key="t1"><input class="bulk-cell" data-dot-path="qubits.q1.T1"></td></tr></tbody>';
    window.document.body.appendChild(table);
    const btn = table.querySelector('button');
    queues['/bulk/column-history'] = [WAIT, '<p class="ch-empty">col-ready</p>'];
    window.ColumnHistory.open(btn);
    await wait(25);
    const card = window.document.querySelector('.ch-card');
    ok(card && card.textContent.indexOf('being built') >= 0, 'Column History shows the building line');
    await wait(150);
    ok(card.textContent.indexOf('col-ready') >= 0, 'and asks again until the column history is ready');
    const nc = calls.filter(c => c.url.indexOf('/bulk/column-history') >= 0).length;
    ok(nc === 2 && calls.filter(c => c.url.indexOf('/bulk/column-history') >= 0)
        .every(c => String(c.body).indexOf('qubits.q1.T1') >= 0),
       'the retry re-sends the same column (' + nc + ' requests)');
    queues['/bulk/column-history'] = [WAIT];
    window.ColumnHistory.open(btn);
    await wait(20);
    window.ColumnHistory.close();
    const nc2 = calls.filter(c => c.url.indexOf('/bulk/column-history') >= 0).length;
    await wait(200);
    ok(calls.filter(c => c.url.indexOf('/bulk/column-history') >= 0).length === nc2,
       'a closed Column History asks no more');

    if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
    console.log('all ' + oks + ' checks passed');
    process.exit(0);
})().catch(function (e) {
    console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
    process.exit(1);
});
