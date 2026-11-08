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
const DOM = '<div id="status-bar"></div><div class="app-layout"><div id="sidebar"><div id="sidebar-tree">' + treeHtml(8) + '</div>' +
  '<form id="compare-form"><button type="submit" class="btn-compare">Compare Selected</button>' +
  '<button type="submit" class="btn-trend">Trend Tracker</button><button type="button" id="compare-clear" hidden>Clear</button>' +
  '<span class="compare-hint" id="compare-hint">Tick runs…</span>' +
  '<span class="compare-offview" id="compare-offview" hidden></span></form>' +
  FLOAT + '</div></div>';

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
  window._resizeCallbacks = [];
  global.ResizeObserver = class { constructor(cb) { window._resizeCallbacks.push(cb); } observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
  window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
  window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  return { win: window, store: store };
}
function toastNodes(win) { return Array.from(win.document.querySelectorAll('#status-bar > .toast')); }
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
  ok($(win, 'cf-compare').title === 'Tick at least 2 runs', 'Compare title names the two-run floor at one tick');
  {
    const cssAll = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
    const rules = cssAll.slice(cssAll.indexOf('/* FB-13:'), cssAll.indexOf('.cf-pop {'));
    const style = win.document.createElement('style'); style.textContent = rules; win.document.head.appendChild(style);
    ok($(win, 'cf-compare').classList.contains('cf-compare--off'),
      'Compare carries off styling at one tick');
    const cmpSt = win.getComputedStyle($(win, 'cf-compare'));
    const op = parseFloat(cmpSt.opacity);
    ok(op > 0.4 && op < 0.5, 'Compare at one tick is ~45% opacity (' + op + ')');
    ok(cmpSt.cursor === 'not-allowed', 'Compare at one tick shows not-allowed cursor');
    const fw = Number(cmpSt.fontWeight);
    ok(fw < 600, 'Compare at one tick is not bold (' + cmpSt.fontWeight + ')');
  }
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
  const { win, store } = makeWorld();
  await settle();
  click(win, boxes(win)[0]); click(win, boxes(win)[2]); click(win, boxes(win)[4]);
  click(win, $(win, 'cf-clear'));
  ok(model(store).length === 0 && $(win, 'compare-float').hidden, 'Clear empties model and hides the bar');
  const nodes = toastNodes(win);
  const t = nodes[nodes.length - 1];
  const u = t && t.querySelector('.toast-undo');
  ok(win.document.activeElement === u, 'Clear focuses Undo');
  ok(u && /Undo clear/.test(u.textContent), 'Clear leaves an "Undo clear" toast');
  u.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(model(store).length === 3 && checked(win) === 3 && listed(win).length === 3, 'Undo clear restores all three');
}
// repeated Clear keeps one toast, replaced in place
{
  const { win } = makeWorld();
  await settle();
  const clearTwice = (idxs) => {
    idxs.forEach((i) => click(win, boxes(win)[i]));
    click(win, $(win, 'cf-clear'));
  };
  clearTwice([0, 2, 4]);
  ok(toastNodes(win).length === 1, 'first Clear leaves one toast');
  clearTwice([1, 3]);
  const nodes = toastNodes(win);
  ok(nodes.length === 1, 'second Clear replaces the toast instead of stacking');
  ok(/Cleared 2/.test(nodes[0].textContent), 'the toast names the latest clear count');
  clearTwice([5]);
  ok(toastNodes(win).length === 1 && /Cleared 1/.test(toastNodes(win)[0].textContent),
    'third Clear still leaves exactly one toast');
}
// First selection does not move the tick when the top form wraps.
{
  const { win } = makeWorld();
  await settle();
  const bar = $(win, 'compare-float'), sidebar = $(win, 'sidebar'), tick = boxes(win)[0];
  sidebar.scrollTop = 100;
  sidebar.getBoundingClientRect = () => ({ top: 0 });
  Object.defineProperty(bar, 'offsetHeight', { value: 20 });
  tick.getBoundingClientRect = () => ({ top: bar.hidden ? 100 : 145 });
  click(win, tick);
  ok(sidebar.scrollTop === 145, 'first tick compensates all form growth');
  const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
  const rules = css.slice(css.indexOf('/* FB-13:'), css.indexOf('.compare-float .cf-btn {'));
  const style = win.document.createElement('style'); style.textContent = rules; win.document.head.appendChild(style);
  ok(win.getComputedStyle(bar.parentNode).position === 'fixed', 'toolbar is fixed outside run flow');
  ok(bar.parentNode.parentNode === win.document.body, 'toolbar escapes sidebar clipping and tree swaps');
  ok(win.getComputedStyle(bar).flexWrap === 'nowrap', 'toolbar stays on one line');
  ok(Number(win.getComputedStyle(bar.parentNode).zIndex) > 6, 'toolbar is above the main pane and resizer');
  win.close();
}
// 4. Real keyboard events and viewport geometry, independent of jsdom layout.
{
  const { win } = makeWorld();
  await settle();
  click(win, boxes(win)[0]); click(win, boxes(win)[1]);
  const pop = $(win, 'cf-pop'), bar = $(win, 'compare-float'), list = $(win, 'cf-list');
  let rect = { left: 120, bottom: 200 };
  bar.getBoundingClientRect = () => rect;
  Object.defineProperty(win.document.documentElement, 'clientWidth', { value: 800 });
  Object.defineProperty(win.document.documentElement, 'clientHeight', { value: 600 });
  Object.defineProperty(pop, 'offsetHeight', { value: 150 });
  click(win, list);
  ok(win.getComputedStyle(pop).left === '120px' && win.getComputedStyle(pop).top === '204px', 'popover follows the stubbed bar rect');
  ok(pop.contains(win.document.activeElement), 'dialog receives focus on open');
  const controls = pop.querySelectorAll('button');
  controls[controls.length - 1].focus();
  const tab = new win.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  win.document.activeElement.dispatchEvent(tab);
  ok(tab.defaultPrevented && win.document.activeElement === controls[0], 'Tab wraps inside the dialog');
  controls[0].dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
  ok(win.document.activeElement === controls[controls.length - 1], 'Shift Tab wraps inside the dialog');
  let escaped = 0;
  win.document.body.addEventListener('keydown', e => { if (e.key === 'Escape') escaped++; });
  const escape = () => win.document.activeElement.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  escape();
  ok(pop.hidden && list.getAttribute('aria-expanded') === 'false' && win.document.activeElement === list, 'Escape closes the dialog and restores trigger focus');
  ok(escaped === 0, 'open dialog consumes Escape');
  escape();
  ok(escaped === 1, 'closed dialog leaves Escape alone');
  rect = { left: 780, bottom: 590 };
  click(win, list);
  ok(win.getComputedStyle(pop).left === '376px' && win.getComputedStyle(pop).top === '442px', 'popover clamps right and bottom to the viewport');
  escape();
  rect = { left: -50, bottom: -20 };
  click(win, list);
  ok(win.getComputedStyle(pop).left === '8px' && win.getComputedStyle(pop).top === '8px', 'popover clamps left and top to the viewport');
  win.dispatchEvent(new win.Event('resize'));
  ok(pop.hidden, 'window resize closes an anchored dialog');
  click(win, list);
  $(win, 'sidebar').dispatchEvent(new win.Event('scroll'));
  ok(pop.hidden, 'sidebar scroll closes an anchored dialog');
  click(win, list);
  win._resizeCallbacks.forEach(cb => cb());
  ok(pop.hidden, 'sidebar resize closes an anchored dialog');
  const group = win.document.createElement('details');
  group.className = 'tree-dir tree-leafdir'; group.open = true;
  group.innerHTML = '<summary>2026-01-01 (8)</summary>';
  $(win, 'sidebar-tree').appendChild(group);
  const header = group.querySelector('summary'), anchor = bar.parentNode;
  header.getClientRects = () => [{}];
  let sr = { top: 80, right: 260, bottom: 600 };
  let hr = { top: 80, right: 245, height: 24 };
  $(win, 'sidebar').getBoundingClientRect = () => sr;
  header.getBoundingClientRect = () => hr;
  group.getBoundingClientRect = () => ({ bottom: 900 });
  Object.defineProperty(header, 'offsetHeight', { value: 24 });
  const positioned = (left, top, height, why) => {
    ok(anchor.style.left === left + 'px' && anchor.style.top === top + 'px'
      && anchor.style.getPropertyValue('--cf-height') === height + 'px', why);
  };
  win.syncCompareCount();
  positioned(266, 80, 24, 'bar is right of the date on the same line');
  hr = { top: 110, right: 330, height: 22 };
  $(win, 'sidebar').dispatchEvent(new win.Event('scroll'));
  positioned(336, 110, 22, 'scroll recomputes header coordinates and row height');
  sr = { top: 80, right: 420, bottom: 600 };
  win._resizeCallbacks.forEach(cb => cb());
  positioned(426, 110, 22, 'sidebar resize or width drag recomputes coordinates');
  hr = { top: 130, right: 410, height: 28 };
  win.dispatchEvent(new win.Event('resize'));
  positioned(426, 130, 28, 'window resize recomputes header coordinates');
  const layout = $(win, 'sidebar').closest('.app-layout');
  layout.classList.add('sidebar-collapsed');
  await settle();
  ok(anchor.hidden, 'collapsed sidebar hides the overflowing toolbar');
  layout.classList.remove('sidebar-collapsed');
  await settle();
  ok(!anchor.hidden, 'expanded sidebar restores the toolbar');
  group.open = false;
  group.dispatchEvent(new win.Event('toggle'));
  positioned(426, 80, 26, 'collapsing the last group resets to sidebar coordinates');
  group.open = true; win.syncCompareCount();
  group.remove();
  $(win, 'sidebar-tree').dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  positioned(426, 80, 26, 'filtering all date groups resets to sidebar coordinates');
  ok(win.document.contains(bar), 'tree replacement preserves the body toolbar');
  win.close();
}
if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
process.exit(0);
})();
