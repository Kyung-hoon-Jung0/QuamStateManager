/* jsdom selfcheck for FB-13 (the floating compare bar), on the docs/141 4y harness.
 * Original header:: the sidebar's compare ticks.
 *  - at most FIVE runs tick (the sixth is refused with a toast; a shift
 *    range is clamped from its far end)
 *  - the ticks survive a tree re-render (htmx swap of #sidebar-tree) and a
 *    reload (sessionStorage mirror)
 *  - Compare Selected is disabled below two ticks
 *  - an HX-Trigger {"sm:toast": …} reaches showToast
 * Run: node tests/sidebar_compare_selfcheck.cjs   (driven by tests/test_sidebar_compare.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function treeHtml(n, keep) {
  let h = '<ul id="grp">';
  for (let i = 0; i < n; i++) {
    if (keep && keep.indexOf(i) < 0) continue;        // a FILTERED tree: the row is gone, not hidden
    h += '<li class="tree-entry"><div class="tree-entry-label"><input type="checkbox" name="paths" value="/w/run' + i + '" form="compare-form"><span class="entry-name">run' + i + '</span></div></li>';
  }
  return h + '</ul>';
}
const FLOAT = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8')
  .match(/<div class="compare-float-anchor">[\s\S]*?<\/div><\/div>/)[0];
const DOM = '<div id="sidebar-tree">' + treeHtml(8) + '</div>' +
  '<form id="compare-form"><button type="submit" class="btn-compare">Compare Selected</button>' +
  '<button type="submit" class="btn-trend">Trend Tracker</button><button type="button" id="compare-clear" hidden>Clear</button>' +
  '<span class="compare-hint" id="compare-hint">Tick runs…</span>' +
  '<span class="compare-offview" id="compare-offview" hidden></span></form>' +
  FLOAT;

function makeWorld(seed) {
  const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>', { url: 'http://localhost/datasets', pretendToBeVisual: true });
  const { window } = dom;
  global.window = window; global.document = window.document; global.CSS = window.CSS;
  global.getComputedStyle = window.getComputedStyle.bind(window);
  global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
  global.navigator = window.navigator; global.location = window.location;
  const store = {}; if (seed) store.quam_sidebar_compare_sel = JSON.stringify(seed);
  const ss = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
  global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage;
  global.sessionStorage = ss; window.sessionStorage = ss;
  global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
  global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
  global.MutationObserver = window.MutationObserver;
  global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
  global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
  window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
  window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  const toasts = [];
  window.showToast = function (m, l) { const d = window.document.createElement('div'); window.document.body.appendChild(d); toasts.push({ m: m, l: l, el: d }); return d; };
  return { win: window, store: store, toasts: toasts };
}
function boxes(win) { return Array.from(win.document.querySelectorAll('#sidebar-tree input[name="paths"]')); }
function checked(win) { return boxes(win).filter((b) => b.checked).length; }
function click(win, el, shift) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, shiftKey: !!shift })); }
function label(win) { return win.document.querySelector('.btn-compare').textContent; }
function cmpDisabled(win) { return win.document.querySelector('.btn-compare').disabled; }
function settle() { return new Promise((r) => setTimeout(r, 20)); }

(async function main() {
const $ = (win, id) => win.document.getElementById(id);
const listed = (win) => Array.from(win.document.querySelectorAll('#cf-items .cf-item')).map((li) => li.getAttribute('data-path'));
const model = (store) => JSON.parse(store.quam_sidebar_compare_sel || '[]');
// 1. hidden at 0, visible at >=1
{
  const { win, store } = makeWorld();
  await settle();
  ok($(win, 'compare-float').hidden === true, 'the bar is hidden with nothing ticked');
  click(win, boxes(win)[3]);
  ok($(win, 'compare-float').hidden === false, 'one tick shows the bar');
  ok($(win, 'cf-compare').disabled === true, 'Compare on the bar is disabled at one tick');
  click(win, boxes(win)[6]);
  ok($(win, 'cf-compare').textContent === 'Compare 2' && !$(win, 'cf-compare').disabled, 'two ticks: "Compare 2", enabled');
  ok(JSON.stringify(listed(win)) === JSON.stringify(model(store)), 'listed = selection model: ' + listed(win));
  // the bar's Compare presses the real form button (hx-post + hidden inputs)
  let pressed = 0;
  win.document.querySelector('#compare-form .btn-compare').addEventListener('click', (e) => { pressed++; e.preventDefault(); });
  click(win, $(win, 'cf-compare'));
  ok(pressed === 1, 'the bar Compare presses the form Compare');
  // x removes one
  click(win, $(win, 'cf-list'));
  ok($(win, 'cf-pop').hidden === false, 'the list opens as a popover');
  click(win, win.document.querySelector('#cf-items .cf-x'));
  ok(model(store).length === 1 && checked(win) === 1 && listed(win).length === 1, 'x removes one from model, box and list');
  click(win, boxes(win)[3]);
  ok(listed(win).length === 2, 'ticking updates the list');
  click(win, boxes(win)[3]);
  ok(listed(win).length === 1, 'unticking updates the list');
  click(win, boxes(win)[6]);
  ok($(win, 'compare-float').hidden === true && listed(win).length === 0, 'back to zero hides the bar');
}
// 2. off-view ticks are listed and marked
{
  const { win, store } = makeWorld();
  await settle();
  click(win, boxes(win)[1]); click(win, boxes(win)[7]);
  win.document.getElementById('sidebar-tree').innerHTML = treeHtml(8, [0, 1, 2]);
  win.document.getElementById('sidebar-tree').dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  await settle();
  ok(listed(win).length === 2 && listed(win).indexOf('/w/run7') >= 0, 'a filtered-out tick stays listed');
  ok(/not in view/.test(win.document.querySelector('#cf-items .cf-item[data-path="/w/run7"]').textContent), 'and says it is not in view');
  ok(JSON.stringify(listed(win).slice().sort()) === JSON.stringify(model(store).slice().sort()), 'listed = model after a filter');
  win.document.querySelector('#cf-items .cf-item[data-path="/w/run7"] .cf-x').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(model(store).indexOf('/w/run7') < 0, 'x removes an off-view entry from the model');
}
// 3. clear + undo
{
  const { win, store, toasts } = makeWorld();
  await settle();
  click(win, boxes(win)[0]); click(win, boxes(win)[2]); click(win, boxes(win)[4]);
  click(win, $(win, 'cf-clear'));
  ok(model(store).length === 0 && $(win, 'compare-float').hidden, 'Clear empties model and hides the bar');
  const t = toasts[toasts.length - 1];
  const u = t && t.el.querySelector('.toast-undo');
  ok(u && /Undo clear/.test(u.textContent), 'Clear leaves an "Undo clear" toast');
  u.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(model(store).length === 3 && checked(win) === 3 && listed(win).length === 3, 'Undo clear restores all three');
}
if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
process.exit(0);
})();
