/* Sidebar compare selfcheck: selection, undo, and decisions D-1 through D-6.
 * Run with NODE_PATH set to the jsdom installation:
 * node tests/compare_float_selfcheck.cjs
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
// The compare-bar rules live in ui-kit.css as --ui-* tokens. Resolve each
// --ui-* reference against the first :root token block so every pin below
// still asserts the same literal value the rule computes to.
function resolveUiTokens(text) {
  const kit = fs.readFileSync(path.join(STATIC, 'ui-kit.css'), 'utf8');
  const rootStart = kit.indexOf(':root {');
  const root = kit.slice(rootStart, kit.indexOf('}', rootStart));
  const tokens = {};
  root.replace(/(--ui-[\w-]+)\s*:\s*([^;]+);/g, (m, k, v) => { if (!(k in tokens)) tokens[k] = v.trim(); return m; });
  for (let i = 0; i < 20; i++) {
    const next = text.replace(/var\((--ui-[\w-]+)\)/g, (m, k) => (k in tokens ? tokens[k] : m));
    if (next === text) break; text = next;
  }
  return text;
}
function cfBlock(text) {
  const start = text.indexOf('.compare-float-anchor {');
  return text.slice(start, text.indexOf('.port-test', start));
}
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
const DOM = '<div id="status-bar"></div><div class="app-layout"><div id="sidebar">' +
  '<form id="compare-form"><button type="submit" class="btn-compare">Compare Selected</button>' +
  '<button type="submit" class="btn-trend">Trend Tracker</button><button type="button" id="compare-clear" hidden>Clear</button>' +
  '<span class="compare-hint" id="compare-hint">Tick runs…</span>' +
  '<span class="compare-offview" id="compare-offview" hidden></span></form>' +
  '<div class="sidebar-workspace">' + FLOAT + '<div id="sidebar-tree">' + treeHtml(8) + '</div></div></div></div>';

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
function click(win, el, shift) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true, shiftKey: !!shift })); }
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
    const cssAll = ['style.css', 'ui-kit.css'].map((f) => fs.readFileSync(path.join(STATIC, f), 'utf8')).join(String.fromCharCode(10));
    const rules = cfBlock(resolveUiTokens(cssAll));
    const style = win.document.createElement('style'); style.textContent = rules; win.document.head.appendChild(style);
    ok($(win, 'cf-compare').classList.contains('cf-compare--off'),
      'Compare carries off styling at one tick');
    const cmpSt = win.getComputedStyle($(win, 'cf-compare'));
    const op = parseFloat(cmpSt.opacity);
    ok(op > 0.4 && op < 0.5, 'Compare at one tick is ~45% opacity (' + op + ')');
    ok(cmpSt.cursor === 'not-allowed', 'Compare at one tick shows not-allowed cursor');
    const fw = cmpSt.fontWeight === 'normal' ? 400 : Number(cmpSt.fontWeight);
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
  ok($(win, 'cf-pop').hidden === false, 'the selected list starts expanded');
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
// D-1: only an explicit Trend action arms one automatic chart request.
{
  const { win } = makeWorld(); await settle();
  click(win, boxes(win)[0]); click(win, boxes(win)[1]);
  const pane = win.document.createElement('div'); pane.id = 'table-pane'; win.document.body.appendChild(pane);
  let charts = 0;
  function picker() {
    pane.innerHTML = '<form id="trend-form" hx-post="/trend/chart"><button type="submit">Show Chart</button></form>';
    pane.querySelector('button').addEventListener('click', e => { e.preventDefault(); charts++; });
  }
  function swap(type, rp, okk) { pane.dispatchEvent(new win.CustomEvent(type, { bubbles: true, detail: { pathInfo: { requestPath: rp || '/trend' }, successful: okk !== false } })); }
  win.document.querySelector('.btn-trend').addEventListener('click', e => e.preventDefault());
  picker(); swap('htmx:afterSwap'); swap('htmx:afterSettle'); await settle(); ok(charts === 0, 'D-1 unrelated picker stays idle');
  click(win, $(win, 'cf-trend')); picker(); swap('htmx:afterSwap'); await settle();
  ok(charts === 0, 'D-1 not pressed at afterSwap (htmx has not processed the form yet: a press would be a native submit)');
  swap('htmx:afterSettle'); await settle();
  ok(charts === 1, 'D-1 floating Trend auto-submits exactly once');
  picker(); swap('htmx:afterSettle'); await settle(); ok(charts === 1, 'D-1 flag clears after use');
  click(win, win.document.querySelector('.btn-trend')); picker(); swap('htmx:afterSettle'); await settle();
  ok(charts === 2, 'D-1 form Trend also arms afterSettle');
  click(win, $(win, 'cf-trend')); picker(); swap('htmx:afterSettle', '/other'); await settle();
  ok(charts === 2, 'D-1 a picker from another request does not consume the Trend press');
  swap('htmx:afterRequest', '/trend', false); picker(); swap('htmx:afterSettle'); await settle();
  ok(charts === 2, 'D-1 a failed /trend disarms the automatic chart'); win.close();
}
const css = resolveUiTokens(['style.css', 'ui-kit.css'].map((f) => fs.readFileSync(path.join(STATIC, f), 'utf8')).join(String.fromCharCode(10)));
const block = cfBlock(css);
function rule(selector) {
  const start = ('\n' + block).indexOf('\n' + selector + ' {');
  return block.slice(start, block.indexOf('}', start) + 1);
}
// D-2: default expanded list takes a full row inside the wrapping bar.
{
  const { win } = makeWorld(); await settle(); click(win, boxes(win)[0]);
  ok(!$(win, 'cf-pop').hidden && $(win, 'cf-pop').parentNode === $(win, 'compare-float'), 'D-2 list defaults to expanded inside bar');
  ok(/flex-wrap: wrap/.test(rule('.compare-float')) && /flex-basis: 100%/.test(rule('.cf-pop')) && /position: static/.test(rule('.cf-pop')), 'D-2 list occupies an in-flow full row'); win.close();
}
// D-3: only the disclosure changes the persisted choice, with normal keyboard flow.
{
  const { win } = makeWorld(); await settle(); click(win, boxes(win)[0]);
  const pop = $(win, 'cf-pop'), list = $(win, 'cf-list');
  const group = win.document.createElement('details'); $(win, 'sidebar-tree').appendChild(group);
  function layoutEvents() {
    $(win, 'sidebar').dispatchEvent(new win.Event('scroll'));
    group.dispatchEvent(new win.Event('toggle'));
    win.dispatchEvent(new win.Event('resize')); win._resizeCallbacks.forEach(cb => cb());
    click(win, win.document.body);
  }
  layoutEvents(); ok(!pop.hidden, 'D-3 layout and outside clicks keep expanded list');
  const key = new win.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  pop.querySelector('button').dispatchEvent(key);
  pop.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  ok(!key.defaultPrevented && !pop.hidden, 'D-3 in-flow list has normal Tab and Escape behavior');
  click(win, list); layoutEvents(); click(win, boxes(win)[1]);
  $(win, 'sidebar-tree').dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  await settle(); ok(pop.hidden, 'D-3 collapsed choice survives layout and rerender');
  click(win, list); click(win, boxes(win)[2]); layoutEvents();
  ok(!pop.hidden, 'D-3 expanded choice survives rerender'); win.close();
}
// D-4: sidebar containment, published height, and scroll compensation in both directions.
{
  const { win } = makeWorld(); await settle();
  const bar = $(win, 'compare-float'), anchor = bar.parentNode, sidebar = $(win, 'sidebar'), tick = boxes(win)[0];
  let height = 40;
  anchor.getBoundingClientRect = () => ({ height: bar.hidden ? 0 : height });
  sidebar.getBoundingClientRect = () => ({ top: 0 }); sidebar.scrollTop = 100;
  tick.getBoundingClientRect = () => ({ top: 100 + (bar.hidden ? 0 : listed(win).length * 20) });
  click(win, tick); ok(sidebar.scrollTop === 120, 'D-4 first growth compensates tick');
  click(win, boxes(win)[1]); ok(sidebar.scrollTop === 140, 'D-4 subsequent growth compensates tick');
  click(win, boxes(win)[1]); ok(sidebar.scrollTop === 120, 'D-4 shrink compensates tick');
  ok(anchor.parentNode.matches('.sidebar-workspace') && anchor.nextElementSibling.id === 'sidebar-tree' && sidebar.contains(anchor) && sidebar.style.getPropertyValue('--cf-h') === '40px', 'D-4 anchor stays in sidebar and publishes rendered height');
  height = 70; win._resizeCallbacks.forEach(cb => cb());
  ok(sidebar.style.getPropertyValue('--cf-h') === '70px', 'D-4 resize republishes height');
  click(win, tick); ok(sidebar.style.getPropertyValue('--cf-h') === '0px' && sidebar.scrollTop === 100, 'D-4 hidden bar publishes zero and compensates final shrink');
  ok(/position: sticky/.test(rule('.compare-float-anchor')) && /top: calc\(-1 \* var\(--sidebar-pad-v\)\)/.test(rule('.compare-float-anchor')) && /z-index: 5;/.test(rule('.compare-float-anchor')) && /background: var\(--pico-background-color\)/.test(rule('.compare-float-anchor')), 'D-4 opaque sticky anchor stays below main stacking layers');
  ok(/\.tree-dir\.tree-leafdir > summary \{\s*position: sticky;\s*top: calc\(var\(--cf-h, 0px\) - var\(--sidebar-pad-v\)\)/.test(css), 'D-4 date header follows bar height');
  win.close();
}
// D-5: compact rows retain ellipsis and put remove immediately after the name.
{
  const { win } = makeWorld(); await settle();
  boxes(win)[0].value = '/w/long_run/quam_state'; click(win, boxes(win)[0]);
  $(win, 'sidebar-tree').innerHTML = treeHtml(8, [1, 2]); win.syncCompareCount();
  const name = win.document.querySelector('.cf-name');
  ok(name.nextElementSibling.matches('.cf-x') && /long_run/.test(name.textContent), 'D-5 remove follows run name even off-view');
  ok(/flex: 0 1 auto/.test(rule('.cf-name')) && /min-width: 0/.test(rule('.cf-name')) && /text-overflow: ellipsis/.test(rule('.cf-name')), 'D-5 names shrink and ellipsize without stretching');
  ok(['.compare-float-anchor', '.compare-float', '.cf-pop'].every(s => /width: fit-content/.test(rule(s)) && /max-width: 100%/.test(rule(s))), 'D-5 bar and list fit content within sidebar'); win.close();
}
// D-6: accessible button with a visible, token-sized state arrow.
{
  const { win } = makeWorld(); await settle(); click(win, boxes(win)[0]);
  const list = $(win, 'cf-list');
  ok(list.tagName === 'BUTTON' && list.type === 'button' && list.getAttribute('aria-controls') === 'cf-pop' && list.getAttribute('aria-expanded') === 'true' && list.querySelector('.cf-arrow').textContent === '\u25be', 'D-6 expanded toggle button and arrow');
  click(win, list.querySelector('.cf-arrow'));
  ok(list.getAttribute('aria-expanded') === 'false' && list.querySelector('.cf-arrow').textContent === '\u25b8', 'D-6 clicking arrow collapses and updates glyph');
  ok(/font-size: calc\(var\(--tree-entry-label-font\) \* 1\.3\)/.test(rule('.cf-arrow')), 'D-6 arrow uses run-row font token'); win.close();
}
// D-7: removing a run by keyboard leaves focus on the next row, else the previous, else the toggle.
{
  const { win } = makeWorld(); await settle();
  click(win, boxes(win)[1]); click(win, boxes(win)[2]); click(win, boxes(win)[3]);
  const order = listed(win); const mid = order[1], next = order[2];
  const xOf = p => win.document.querySelector('#cf-items .cf-item[data-path="' + p + '"] .cf-x');
  xOf(mid).focus(); click(win, xOf(mid));
  const ae = () => win.document.activeElement;
  ok(listed(win).indexOf(mid) < 0 && ae() && ae().closest('.cf-item') && ae().closest('.cf-item').getAttribute('data-path') === next, 'D-7 removing the middle focuses the next row');
  const last = listed(win)[listed(win).length - 1], prev = listed(win)[0];
  click(win, xOf(last));
  ok(ae() && ae().closest('.cf-item') && ae().closest('.cf-item').getAttribute('data-path') === prev, 'D-7 removing the last focuses the previous row');
  click(win, xOf(prev));
  ok(ae() && ae() !== win.document.body && ae().matches('input[type=checkbox][value="' + prev + '"]'), 'D-7 removing the only row focuses that run tick, never body');
  win.close();
}
// D-7b: the only selected run is off-view (filtered away): focus lands on the sidebar, not body.
{
  const { win } = makeWorld(); await settle();
  click(win, boxes(win)[7]);
  win.document.getElementById('sidebar-tree').innerHTML = treeHtml(8, [0, 1]);
  win.document.getElementById('sidebar-tree').dispatchEvent(new win.CustomEvent('htmx:afterSwap', { bubbles: true }));
  await settle();
  click(win, win.document.querySelector('#cf-items .cf-x'));
  ok(win.document.activeElement && win.document.activeElement.id === 'sidebar', 'D-7b removing an off-view only row focuses the sidebar, never body');
  win.close();
}
// D-8: the bar height is published in the same task as the rows change (no RO frame).
{
  const { win } = makeWorld(); await settle();
  const bar = $(win, 'compare-float'), anchor = bar.parentNode, sidebar = $(win, 'sidebar');
  anchor.getBoundingClientRect = () => ({ height: bar.hidden ? 0 : 20 + win.document.querySelectorAll('#cf-items .cf-item').length * 10 });
  click(win, boxes(win)[0]); click(win, boxes(win)[1]);
  ok(sidebar.style.getPropertyValue('--cf-h') === '40px', 'D-8 height after rows rebuild is published synchronously');
  win.close();
}
if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
process.exit(0);
})();
