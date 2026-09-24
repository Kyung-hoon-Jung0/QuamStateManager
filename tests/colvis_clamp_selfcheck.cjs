/* QA liveedit-r2-30 -- a `.bulk-colvis` dropdown stays inside its clip.
 *
 * The Qubits / Pairs pickers hang left-aligned under their summary; when the
 * wrapping toolbar puts the button near the right edge (800-911 px windows)
 * the 240 px menu ran past #table-pane's clip (right edge 807 vs 785 at
 * 800 px), which can never be scrolled into view. app.js now nudges an opened
 * menu left by exactly its overflow and drops the nudge on close.
 *
 * jsdom has no layout, so the rects are stubbed with the numbers measured in
 * real Chrome at 800x900 (menu 567..807, pane clip right 785).
 *
 * Run: node tests/colvis_clamp_selfcheck.cjs  (driven by test_colvis_clamp.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('SKIP: jsdom not installed'); process.exit(2); }

const dom = new JSDOM(
  '<!doctype html><html><head><style>'
  + '.bulk-colvis{position:relative}.bulk-colvis-menu{position:absolute;top:100%;left:0}'
  + '</style></head><body>'
  + '<div id="table-pane" style="overflow-y:auto"><div class="bulk-toolbar">'
  + '<details class="bulk-colvis bulk-qubitvis" id="qv"><summary>Qubits</summary>'
  + '<div class="bulk-colvis-menu bulk-qubitvis-menu" id="qm">'
  + '<details class="bulk-colvis-dyn" id="inner"><summary>more</summary></details></div></details>'
  + '</div></div></body></html>',
  { url: 'http://localhost/bulk', pretendToBeVisual: true });
const window = dom.window;
global.window = window;
global.document = window.document;
global.CSS = window.CSS;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
Object.defineProperty(global, 'navigator',
  { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const MEM = {};
const STORE = {
  getItem: (k) => (k in MEM ? MEM[k] : null),
  setItem: (k, v) => { MEM[k] = String(v); },
  removeItem: (k) => { delete MEM[k]; },
};
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
  Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
global.fetch = () => new Promise(() => {});
Object.defineProperty(window, 'fetch', { value: global.fetch, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); }
catch (e) { console.error('FAIL: app.js did not evaluate: ' + e.message); process.exit(1); }

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = (ms) => new Promise((r) => setTimeout(r, ms == null ? 20 : ms));

const doc = window.document;
const pane = doc.getElementById('table-pane');
const det = doc.getElementById('qv');
const menu = doc.getElementById('qm');
function rect(l, t, w, h) {
  return { left: l, top: t, width: w, height: h, right: l + w, bottom: t + h, x: l, y: t };
}
Object.defineProperty(doc.documentElement, 'clientWidth', { value: 800, configurable: true });
Object.defineProperty(pane, 'clientWidth', { value: 485, configurable: true });
Object.defineProperty(pane, 'clientLeft', { value: 0, configurable: true });
pane.getBoundingClientRect = () => rect(300, 305, 500, 595);
let menuLeft = 567, menuWidth = 240;
// the menu's rect follows its inline left, as layout would
menu.getBoundingClientRect = function () {
  const shift = parseFloat(menu.style.left || '0') || 0;
  return rect(menuLeft + shift, 470, menuWidth, 260);
};
async function openDet(open) {
  det.open = open;
  // jsdom may or may not fire `toggle` itself; the handler is idempotent
  det.dispatchEvent(new window.Event('toggle'));
  await settle();
}

(async function main() {
  await openDet(true);
  const r = menu.getBoundingClientRect();
  ok(menu.style.left === '-28px', 'an overflowing picker is nudged left by its overflow (left=' + menu.style.left + ')');
  ok(r.right <= 785 - 6 + 0.5 && r.left >= 300,
     'and now ends inside the pane clip (menu ' + r.left + '..' + r.right + ', clip right 785)');

  await openDet(false);
  ok(menu.style.left === '', 'closing drops the nudge');

  // a picker that already fits is left exactly where the stylesheet put it
  menuLeft = 329;
  await openDet(true);
  ok(menu.style.left === '', 'a picker that fits is untouched (left=' + JSON.stringify(menu.style.left) + ')');
  await openDet(false);

  // a menu wider than the clip: pinned to the clip's left edge, never past it
  menuLeft = 400; menuWidth = 520;
  await openDet(true);
  const r2 = menu.getBoundingClientRect();
  ok(Math.abs(r2.left - 306) < 0.5, 'a menu wider than the clip stops at its left edge (left ' + r2.left + ')');
  await openDet(false);

  // a nested <details> inside a menu is not a picker
  menuLeft = 567; menuWidth = 240;
  const inner = doc.getElementById('inner');
  inner.open = true; inner.dispatchEvent(new window.Event('toggle'));
  await settle();
  ok(menu.style.left === '', 'a nested details toggling inside the menu moves nothing');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch(function (e) {
  console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
  process.exit(1);
});
