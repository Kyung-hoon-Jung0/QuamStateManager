/* jsdom selfcheck for window.TopbarHold (docs/203): the top bar's wrapping
 * left group may GROW when the pending tray gains its action cluster, but it
 * never shrinks back while the window keeps its width -- so an edit -> apply /
 * undo / auto-apply cycle cannot bounce the page. The hold is released (and
 * re-taken at the CURRENT height) on a width change and on a navigation.
 *
 * jsdom computes no layout, so the observer and the element's rect are
 * driven by hand; the real-Chrome measurement lives in docs/203.
 *
 * Run: node tests/topbar_hold_selfcheck.cjs   (driven by tests/test_topbar_twerk.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const START = 'window.TopbarHold = (function () {';
const i = src.indexOf(START);
if (i < 0) { console.error('FAIL: window.TopbarHold not found in app.js'); process.exit(1); }
// CRLF-tolerant: a Windows checkout (core.autocrlf) hands us \r\n
const end = /\r?\n\}\)\(\);\r?\n/.exec(src.slice(i));
if (!end) { console.error('FAIL: end of the TopbarHold module not found'); process.exit(1); }
const block = src.slice(i, i + end.index + end[0].length);

const dom = new JSDOM(
    '<!doctype html><html><body><header class="topbar"><nav><ul id="left"><li>a</li></ul>' +
    '<ul class="topbar-right"><li>b</li></ul></nav></header><div class="app-layout"></div></body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
const ul = document.getElementById('left');

let rectH = 100;
ul.getBoundingClientRect = () => ({ height: rectH, width: 800, top: 0, left: 0, right: 800, bottom: rectH });
let roCb = null;
window.ResizeObserver = function (cb) { roCb = cb; this.observe = function () {}; };
function ro(h) { rectH = h; roCb([{ borderBoxSize: [{ blockSize: h }] }]); }
function held() { return ul.style.getPropertyValue('--topbar-hold'); }
Object.defineProperty(window, 'innerWidth', { value: 1366, writable: true, configurable: true });

// jsdom is still 'loading' here; the module would defer start() to an async
// DOMContentLoaded. Report 'complete' so start() runs synchronously.
Object.defineProperty(document, 'readyState', { get: () => 'complete', configurable: true });
new Function('window', 'document', block)(window, document);
ok(typeof window.TopbarHold === 'object', 'the module loads');
ok(typeof roCb === 'function', 'it observes the left group');

ro(100);
ok(held() === '100px', 'the first observed height is held (got ' + held() + ')');
ro(160.4);
ok(held() === '160.4px', 'a GROWTH raises the hold to the EXACT height -- a rounded-up hold would itself be a resize and loop the observer (got ' + held() + ')');
ro(100);
ok(held() === '160.4px', 'a SHRINK (tray cluster gone after Apply/Undo) keeps the hold -- no bounce (got ' + held() + ')');
ok(window.TopbarHold.held() === 160.4, 'held() reports it');

window.dispatchEvent(new window.Event('resize'));
ok(held() === '160.4px', 'a resize that keeps the width keeps the hold');

window.innerWidth = 1600;
rectH = 90;
window.dispatchEvent(new window.Event('resize'));
ok(held() === '90px', 'a WIDTH change releases and re-holds the current height (got ' + held() + ')');

ro(150);
rectH = 150;
document.dispatchEvent(new window.Event('htmx:pushedIntoHistory'));
ok(held() === '150px', 'a navigation re-holds what the bar needs NOW, never 0 while the tray is still pending (got ' + held() + ')');
ro(100);
ok(held() === '150px', '...so the Apply after it still cannot shrink the bar');

rectH = 80;
window.dispatchEvent(new window.PopStateEvent('popstate'));
ok(held() === '80px', 'back/forward releases too');

document.documentElement.classList.add('topbar-hidden');
ro(300);
ok(held() === '80px', 'a hidden bar is never held taller');
rectH = 0;
window.TopbarHold.release();
ok(held() === '', 'a release under a hidden bar clears the hold entirely (got ' + JSON.stringify(held()) + ')');

ok(!/style\.minHeight/.test(block), 'the hold is a CSS variable, never an inline min-height that would beat html.topbar-hidden');

/* QA F17 (review): the sidebar-collapsed fallback tools wrap the bar a row
   taller; after the sidebar came back the hold kept that row (141 vs 99 px at
   1366 in real Chrome, until reload). A flip of html.sidebar-is-collapsed
   releases; any other class change keeps the anti-shake hold. The observer
   is a MutationObserver, so each step yields a tick. */
(async () => {
    const tick = () => new Promise((r) => setTimeout(r, 0));
    const html = document.documentElement;
    html.classList.remove('topbar-hidden');
    rectH = 99;
    window.TopbarHold.release();
    ok(held() === '99px', 'setup: the bar holds 99 (got ' + held() + ')');
    html.classList.add('sidebar-is-collapsed');
    await tick();
    ro(141);
    ok(held() === '141px', 'the fallback tools wrap the bar: the hold grows to 141 (got ' + held() + ')');
    rectH = 99;
    html.classList.add('some-other-state');
    await tick();
    ok(held() === '141px', 'an unrelated <html> class change keeps the hold (got ' + held() + ')');
    html.classList.remove('sidebar-is-collapsed');
    await tick();
    ok(held() === '99px', 'the sidebar coming back releases the fallback row: 99 again (got ' + held() + ')');
    html.classList.add('topbar-hidden');
    html.classList.add('sidebar-is-collapsed');   // cycleChrome: both legs in one batch
    await tick();
    ok(held() === '', 'collapse-all under a hidden bar clears the hold (got ' + JSON.stringify(held()) + ')');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('all ok');
})();
