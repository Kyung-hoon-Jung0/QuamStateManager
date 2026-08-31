/* docs/146b -- the red modified boxes follow the TRAY, not the render.
 * Customer: edited two values, synced, "No differences" -- and the red
 * outlines stayed (the patch-first rounds removed the full re-render that
 * used to clear them). Pins, against the real app.js:
 *  1. a tray swap still showing pending changes KEEPS the markers
 *  2. a tray swap showing zero pending changes clears them everywhere
 *     (both grids' red boxes incl. baseline/Δ-line, tree pending tints,
 *     all-values dirty rows) -- while typed-uncommitted `dirty` survives
 *  3. a conflict tray (no count attribute) never clears
 *  4. the JS _swapPendingTray path and the OOB swap path both clear
 * Run: node tests/pending_markers_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body>'
    + '<div id="pending-tray" data-change-count="2"></div>'
    + '<table><tr><td class="bulk-td">'
    + '<input class="bulk-cell bulk-cell-modified" data-dot-path="qubits.q1.T1" data-baseline="1e-05" value="2e-05">'
    + '<span class="bulk-ba-old">1e-05</span></td>'
    + '<td class="bulk-td"><input class="bulk-cell bulk-cell-modified dirty" data-dot-path="qubits.q1.T2" value="3e-05"></td>'
    + '<td class="bulk-td"><input class="bulk-cell dirty" data-dot-path="qubits.q1.f_01" value="5e9"></td></tr></table>'
    + '<div class="tree-node"><div class="tree-row tree-row-pending"></div></div>'
    + '<div class="av-row av-row-dirty"><input class="av-input"></div>'
    + '</body></html>', { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.eval("fetch = window.fetch = function(){ return new Promise(function(){}); };");
global.fetch = window.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
function nRed() { return d.querySelectorAll('.bulk-cell-modified').length; }
function tray() { return d.getElementById('pending-tray'); }
function fireSwap(target) {
    d.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { detail: { target: target } }));
}

// 1. a tray still holding pending changes keeps the markers
fireSwap(tray());
ok(nRed() === 2 && d.querySelectorAll('.tree-row-pending').length === 1,
   'a tray with pending changes keeps every marker');

// 2. the tray goes clean -> markers cleared, dirty survives
tray().setAttribute('data-change-count', '0');
fireSwap(tray());
ok(nRed() === 0, 'a CLEAN tray clears the red boxes (both cells)');
ok(d.querySelectorAll('.tree-row-pending').length === 0
   && d.querySelectorAll('.av-row-dirty').length === 0,
   'tree pending tints + all-values dirty rows clear too');
ok(d.querySelectorAll('input.dirty').length === 2,
   'typed-but-uncommitted `dirty` cells are NOT touched');
const cell1 = d.querySelector('input[data-dot-path="qubits.q1.T1"]');
ok(!cell1.hasAttribute('data-baseline'), 'the before/after baseline is retired');
ok(d.querySelector('.bulk-ba-old').textContent === '', 'the Δ old-value line empties');

// 3. a conflict tray (no count attr) never clears
cell1.classList.add('bulk-cell-modified');
tray().removeAttribute('data-change-count');
fireSwap(tray());
ok(nRed() === 1, 'a conflict tray (no count) keeps the markers');

// 4a. the JS _swapPendingTray path clears as well (called IN-REALM -- a
// top-level function declaration is a window prop in browsers, but the
// jsdom Node realm cannot see it: the docs/125 bridging rule again)
window.__trayHtml = '<div id="pending-tray" data-change-count="0"></div>';
window.eval('_swapPendingTray(window.__trayHtml)');
ok(nRed() === 0, '_swapPendingTray with a clean tray clears the markers');

// 4b. the OOB swap path (stage / apply-to-chip responses)
cell1.classList.add('bulk-cell-modified');
d.dispatchEvent(new window.CustomEvent('htmx:oobAfterSwap', { detail: { target: tray() } }));
ok(nRed() === 0, 'an OOB tray swap clears them too');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (9 assertions)');
process.exit(fails ? 1 : 0);
