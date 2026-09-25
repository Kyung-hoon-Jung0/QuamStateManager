/* QA datasets-r2-21 -- the Alt+click compare basket, in the REAL app.js under
 * jsdom: a 9th pick is refused OUT LOUD (a toast, like the sidebar diff's cap)
 * instead of silently, and pressing "Compare N" takes the fixed bar off screen
 * (it sat over the compare view's figures and outlived its x) while keeping
 * the picks for the next Alt+click.
 *
 * Run: node tests/ds_basket_selfcheck.cjs  (driven by tests/test_ds_basket.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/datasets', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;                 // app.js reads bare `CSS` (docs/113 harness rule)
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => Promise.resolve({ json: () => Promise.resolve({}) });
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const loads = [];
window.htmx = {
    ajax: (m, url) => { loads.push(url); return Promise.resolve(); },
    trigger: () => {}, process: () => {},
};
global.htmx = window.htmx;

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

// record toasts (app.js defines window.showToast; the handler reads it at click time)
const toasts = [];
window.showToast = (msg, level) => { toasts.push({ msg: String(msg), level: level }); };

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const doc = window.document;
let html = '<ul>';
for (let i = 1; i <= 10; i++) {
    html += '<li class="tree-entry"><div class="tree-entry-label">'
        + '<span class="tree-entry-click" data-folder-path="/d" data-run-id="' + i
        + '" data-uid="f1:' + i + '" tabindex="0" role="button">#' + i + '</span></div></li>';
}
doc.body.innerHTML = html + '</ul><div id="inspector-pane"></div>';
function altClick(uid) {
    const el = doc.querySelector('.tree-entry-click[data-uid="' + uid + '"]');
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true, altKey: true }));
}
function click(el) {
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

for (let i = 1; i <= 8; i++) altClick('f1:' + i);
ok(window._dsBasket.length === 8, '8 Alt+clicks collect 8 runs');
ok(toasts.length === 0, 'no toast below the cap');
altClick('f1:9');
ok(window._dsBasket.length === 8 && window._dsBasket.indexOf('f1:9') === -1,
   'a 9th pick is not added (the route caps at 8)');
ok(toasts.length === 1 && /at most 8/.test(toasts[0].msg) && toasts[0].level === 'warning',
   'r2-21: ... and says so (a warning toast naming the cap), got '
   + JSON.stringify(toasts));

let bar = doc.getElementById('ds-basket-bar');
ok(!!bar, 'the floating basket bar shows the collection');
const go = bar && bar.querySelector('.ds-basket-go');
ok(!!go && /Compare 8/.test(go.textContent), 'the bar offers "Compare 8"');
click(go);
ok(loads.length === 1 && loads[0] === '/datasets/compare?ids='
   + ['f1:1', 'f1:2', 'f1:3', 'f1:4', 'f1:5', 'f1:6', 'f1:7', 'f1:8'].join(','),
   'Compare opens /datasets/compare with every picked uid: ' + loads[0]);
ok(!doc.getElementById('ds-basket-bar'),
   'r2-21: once the compare opens, the fixed bar no longer sits over it');
ok(window._dsBasket.length === 8, 'r2-21: ... and the picks are kept');
// the compare's x (closeInspector) must not bring it back
doc.body.dispatchEvent(new window.Event('inspector-closed'));
ok(!doc.getElementById('ds-basket-bar'), 'closing the compare view does not re-show the bar');
// the next Alt+click brings the collection back, editable
altClick('f1:8');
bar = doc.getElementById('ds-basket-bar');
ok(!!bar && bar.querySelectorAll('.ds-basket-chip').length === 7,
   'the next Alt+click brings the bar back with the kept picks (8 -> 7 after the toggle)');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ds_basket_selfcheck: all ok');
process.exit(0);            // app.js arms intervals that would keep node alive
