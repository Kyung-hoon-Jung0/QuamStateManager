/* jsdom selfcheck for docs/141 4y: the sidebar's compare ticks.
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

function treeHtml(n) {
  let h = '<ul>';
  for (let i = 0; i < n; i++) {
    h += '<li class="tree-entry"><div class="tree-entry-label"><input type="checkbox" name="paths" value="/w/run' + i + '" form="compare-form"><span class="entry-name">run' + i + '</span></div></li>';
  }
  return h + '</ul>';
}
const DOM = '<div id="sidebar-tree">' + treeHtml(8) + '</div>' +
  '<form id="compare-form"><button type="submit" class="btn-compare">Compare Selected</button>' +
  '<button type="submit" class="btn-trend">Trend Tracker</button><button type="button" id="compare-clear" hidden>Clear</button></form>';

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
  window.showToast = function (m, l) { toasts.push({ m: m, l: l }); };
  return { win: window, store: store, toasts: toasts };
}
function boxes(win) { return Array.from(win.document.querySelectorAll('#sidebar-tree input[name="paths"]')); }
function checked(win) { return boxes(win).filter((b) => b.checked).length; }
function click(win, el, shift) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, shiftKey: !!shift })); }
function label(win) { return win.document.querySelector('.btn-compare').textContent; }
function cmpDisabled(win) { return win.document.querySelector('.btn-compare').disabled; }
function settle() { return new Promise((r) => setTimeout(r, 20)); }

(async function main() {

// 1. the cap
{
  const { win, store, toasts } = makeWorld();
  await settle();
  ok(cmpDisabled(win) === true, 'Compare Selected is disabled with nothing ticked');
  const all = boxes(win);
  click(win, all[0]);
  ok(cmpDisabled(win) === true, 'still disabled with ONE tick');
  for (let i = 1; i < 5; i++) click(win, all[i]);
  ok(checked(win) === 5 && label(win) === 'Compare Selected (5)' && !cmpDisabled(win), 'five ticks: count 5, button enabled');
  click(win, all[5]);
  ok(all[5].checked === false && checked(win) === 5, 'the SIXTH tick is refused');
  ok(toasts.length === 1 && /Up to 5 runs/.test(toasts[0].m), 'and says why (toast): ' + (toasts[0] && toasts[0].m));
  ok(label(win) === 'Compare Selected (5)', 'the count stays 5');
  ok(JSON.parse(store.quam_sidebar_compare_sel).length === 5 && JSON.parse(store.quam_sidebar_compare_sel)[4] === '/w/run4', 'sessionStorage mirrors the five');
  // unticking one frees a slot
  click(win, all[2]);
  click(win, all[6]);
  ok(all[2].checked === false && all[6].checked === true && checked(win) === 5, 'untick one, tick another: five again');
}

// 2. a shift range beyond the cap is clamped from its far end
{
  const { win, toasts } = makeWorld();
  await settle();
  const all = boxes(win);
  click(win, all[0]);
  click(win, all[7], true);       // range 0..7 = 8 ticks -> clamp to 5
  ok(checked(win) === 5, 'a shift range of 8 is clamped to 5');
  ok(all[0].checked && all[4].checked && !all[5].checked && !all[7].checked, 'the first five of the range stay, the far end is unticked');
  ok(toasts.length === 1, 'one toast for the clamp');
}

// 3. the ticks survive a tree re-render (htmx swap) and Clear empties the mirror
{
  const { win, store } = makeWorld();
  await settle();
  const all = boxes(win);
  click(win, all[1]); click(win, all[3]); click(win, all[6]);
  const tree = win.document.getElementById('sidebar-tree');
  tree.innerHTML = treeHtml(8);       // fresh, unticked boxes
  ok(checked(win) === 0, '(the fresh tree has no ticks)');
  tree.dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  const now = boxes(win);
  ok(checked(win) === 3 && now[1].checked && now[3].checked && now[6].checked, 'after the swap the same three are ticked again');
  ok(label(win) === 'Compare Selected (3)', 'and the count is right');
  win.compareClearSelection();
  ok(checked(win) === 0 && JSON.parse(store.quam_sidebar_compare_sel).length === 0, 'Clear empties the ticks AND the mirror');
  tree.innerHTML = treeHtml(8);
  tree.dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  ok(checked(win) === 0, 'a cleared mirror restores nothing');
}

// 4. a reload restores from the mirror at script eval time
{
  const { win } = makeWorld(['/w/run2', '/w/run5', '/w/nonexistent']);
  await settle();
  const all = boxes(win);
  ok(all[2].checked && all[5].checked && checked(win) === 2, 'a reload re-ticks the remembered runs that still exist');
  ok(label(win) === 'Compare Selected (2)' && !cmpDisabled(win), 'count and button follow');
}

// 5. the HX-Trigger toast bridge
{
  const { win, toasts } = makeWorld();
  await settle();
  win.document.body.dispatchEvent(new win.CustomEvent('sm:toast', { bubbles: true, detail: { message: 'Tick at least two runs to diff.', level: 'warning' } }));
  ok(toasts.length === 1 && toasts[0].m === 'Tick at least two runs to diff.' && toasts[0].l === 'warning', 'sm:toast reaches showToast with its level');
  win.document.body.dispatchEvent(new win.CustomEvent('sm:toast', { bubbles: true, detail: {} }));
  ok(toasts.length === 1, 'an empty detail shows nothing');
}

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (21 assertions)');
process.exit(fails ? 1 : 0);
})();
