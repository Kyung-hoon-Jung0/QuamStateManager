/* v1.5.1 (r6 P2-9): header Search results open at the top of the pane.
 *
 * The results swap into #inspector-pane, the same scroller the previous
 * inspector used, and kept its scroll offset: they opened 661 px down, the
 * first results and the "Pages and actions" line out of view. The driver
 * (tests/test_v151_r6_search_scroll.py) renders a REAL qubit inspector and
 * REAL search results; the SHIPPED app.js handles the swap here. jsdom has no
 * layout, so the pane's scroll offset is the one geometry fact given: an
 * own, writable scrollTop, set to the re-sweep's 661 px.
 *
 * Run: node tests/v151_r6_search_scroll_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div><div id="table-pane"></div>'
    + '<div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/qubits', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.fetch = () => new Promise(() => {});
w.htmx = { ajax: () => Promise.resolve(), process() {}, trigger() {}, on() {}, config: {} };
w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.eval(APP);

const pane = d.getElementById('inspector-pane');
let top = 0;
Object.defineProperty(pane, 'scrollTop', { configurable: true, get: () => top, set: (v) => { top = +v; } });
function swap(html, reqPath) {
    // htmx's order for a swap into the pane: beforeSwap, the content, afterSwap
    const detail = { target: pane, elt: pane, xhr: { status: 200, responseText: html },
                     serverResponse: html, shouldSwap: true,
                     requestConfig: { verb: 'get', path: reqPath }, pathInfo: { requestPath: reqPath } };
    pane.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', { bubbles: true, detail }));
    pane.innerHTML = html;
    pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail }));
}

(async function () {
    swap(FX.qubit, '/qubit/q1');
    await sleep(30);
    top = 661;                                        // the reader scrolled the qubit inspector down
    swap(FX.search, '/search?q=T1');
    await sleep(30);
    ok(!!pane.querySelector('.search-panel'), 'the real search results are in the pane');
    ok(top === 0, 'the results open at the top of the pane (scrollTop ' + top + ')');

    top = 420;                                        // scrolled within the results, then a category tab
    swap(FX.search_qubit, '/search?q=T1&category=qubit');
    await sleep(30);
    ok(top === 0, 'a category tab opens its results at the top too (scrollTop ' + top + ')');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
