// docs/89 — Settings + Calculator as sidebar tools, driving the REAL app.js
// and calc.js under jsdom.
//
// What must hold:
//  - ONE popover in the document per tool (the topbar pair is a fallback
//    trigger, not a second copy)
//  - opening anchors the popover with position:fixed from the trigger's rect —
//    it cannot stay `absolute` inside the scrolling sidebar, which clips it
//  - while the sidebar is collapsed the TOPBAR trigger is used, because the
//    sidebar collapses to width 0 and its row is gone
//  - the two tools are a singleton: opening one closes the other
//  - Alt+C toggles the calculator, but never while typing in a field
//  - a dragged (floating) popover keeps its place instead of snapping back
//
// Run: node tests/sidebar_tools_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// Minimal shell mirroring base.html's structure after docs/89: sidebar row +
// topbar fallback + BODY-LEVEL popovers.
const DOM = `
<div class="app-layout" id="app-layout">
  <header class="topbar"><nav><ul>
    <li class="topbar-tools-fallback">
      <button class="settings-btn topbar-tool" onclick="toggleSettings(this)"></button>
      <button class="calc-btn topbar-tool" onclick="toggleCalc(this)" aria-expanded="false"></button>
    </li>
  </ul></nav></header>
  <aside id="sidebar">
    <div class="sidebar-tools">
      <button class="sidebar-tool settings-btn" onclick="toggleSettings(this)"></button>
      <button class="sidebar-tool calc-btn" id="calc-btn" onclick="toggleCalc(this)" aria-expanded="false"></button>
    </div>
  </aside>
  <div id="settings-dropdown" class="settings-dropdown settings-hidden">
    <div class="settings-header" id="settings-header"><b>Settings</b><span class="settings-header-tools"><button id="settings-x" onclick="toggleSettings()">×</button></span></div>
    <div class="settings-group"><button id="settings-inner">S</button></div>
  </div>
  <div id="calc-popover" class="calc-popover calc-hidden">
    <div class="calc-header" id="calc-header"></div>
    <input id="calc-s1-dp"><input id="calc-s1-amp"><input id="calc-s1-from"><input id="calc-s1-to">
    <input id="calc-s2-fsp"><input id="calc-s2-amp"><input id="calc-s2-target">
    <input id="calc-s3-dbm"><input id="calc-s3-r"><input id="calc-s3-vrms">
    <input id="calc-s3-vpk"><input id="calc-s3-vpp"><input id="calc-expr">
    <span id="calc-s1-k"></span><span id="calc-s1-anew"></span><span id="calc-s2-dbm"></span>
    <span id="calc-s2-anew"></span><span id="calc-s3-pmw"></span><span id="calc-expr-res"></span>
  </div>
  <input id="outside-field">
</div>`;

const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.location = window.location;
// jsdom already provides localStorage/sessionStorage on the window; only the
// bare globals the scripts touch need bridging.
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.fetch = global.fetch = () => Promise.resolve({
  status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve(''),
});
window.htmx = global.htmx = { ajax() { return Promise.resolve(); }, trigger() {}, process() {} };

// jsdom does no layout: give the triggers real rects so the anchoring maths
// has something to work from.
function rect(el, r) {
  el.getBoundingClientRect = () => Object.assign(
    { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }, r);
}

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'float-panel.js'), 'utf8'));   // docs/141 4u: the drag core
window.eval(fs.readFileSync(path.join(STATIC, 'calc.js'), 'utf8'));

const doc = window.document;
const layout = doc.getElementById('app-layout');
const sideCalc = doc.querySelector('.sidebar-tool.calc-btn');
const topCalc = doc.querySelector('.topbar-tool.calc-btn');
const sideSet = doc.querySelector('.sidebar-tool.settings-btn');
const pop = doc.getElementById('calc-popover');
const dd = doc.getElementById('settings-dropdown');

rect(sideCalc, { left: 12, top: 60, bottom: 84, width: 150, height: 24 });
rect(topCalc, { left: 900, top: 4, bottom: 28, width: 28, height: 24 });
rect(sideSet, { left: 12, top: 32, bottom: 56, width: 150, height: 24 });

/* ── 1. exactly one popover per tool ─────────────────────────────────── */
ok(doc.querySelectorAll('#calc-popover').length === 1, 'one calculator popover in the document');
ok(doc.querySelectorAll('#settings-dropdown').length === 1, 'one settings dropdown in the document');
ok(doc.querySelectorAll('.calc-btn').length === 2, 'two calculator TRIGGERS (sidebar + fallback)');

/* ── 2. the popover is anchored, not left absolute inside the sidebar ── */
window.toggleCalc(sideCalc);
ok(!pop.classList.contains('calc-hidden'), 'sidebar trigger opens the calculator');
ok(pop.classList.contains('pop-anchored'),
   'popover is anchored (position:fixed) — an absolute child of the scrolling sidebar would be clipped');
ok(pop.style.left === '12px', 'anchored to the trigger x (got ' + pop.style.left + ')');
ok(pop.style.top === '88px', 'anchored just under the trigger (got ' + pop.style.top + ')');
ok(!pop.parentElement.closest('#sidebar'), 'popover lives OUTSIDE #sidebar');

/* aria tracks on BOTH triggers, so the fallback is never left stale */
ok(sideCalc.getAttribute('aria-expanded') === 'true'
   && topCalc.getAttribute('aria-expanded') === 'true', 'aria-expanded set on both triggers');
window.toggleCalc(sideCalc);
ok(pop.classList.contains('calc-hidden'), 'toggles closed');

/* ── 3. collapsed sidebar → the topbar fallback is the trigger ────────── */
layout.classList.add('sidebar-collapsed');
window.toggleCalc();                       // no explicit trigger (e.g. a shortcut)
// The topbar trigger sits at x=900 in a 1024-wide viewport, so a ~280px
// popover is CLAMPED back on-screen — that clamp is the point: anchoring to a
// right-edge trigger must not push the panel out of the window.
const clamped = window.innerWidth - (pop.offsetWidth || 280) - 6;
ok(pop.style.left === clamped + 'px',
   'collapsed: anchors to the TOPBAR fallback, clamped on-screen (got '
   + pop.style.left + ', expected ' + clamped + 'px)');
ok(pop.style.left !== '12px', 'collapsed: NOT the (hidden) sidebar trigger');
ok(parseInt(pop.style.left, 10) + (pop.offsetWidth || 280) <= window.innerWidth,
   'the popover never hangs off the right edge');
window.toggleCalc();
layout.classList.remove('sidebar-collapsed');
window.toggleCalc();
ok(pop.style.left === '12px', 'expanded again: back to the sidebar trigger');
window.toggleCalc();

/* ── 4. singleton ─────────────────────────────────────────────────────── */
/* docs/141 4u (user: "a bug"): the two tools are two WINDOWS now, never a
   singleton -- opening one leaves the other where it is */
window.toggleCalc(sideCalc);
window.toggleSettings(sideSet);
ok(!pop.classList.contains('calc-hidden'), 'opening Settings leaves the Calculator open');
ok(!dd.classList.contains('settings-hidden'), 'settings dropdown open');
ok(dd.classList.contains('pop-anchored'), 'settings dropdown is anchored too');
window.toggleCalc(sideCalc);
ok(dd.classList.contains('settings-hidden') === false, 'closing the Calculator leaves Settings open');
window.toggleCalc(sideCalc);
ok(!pop.classList.contains('calc-hidden') && !dd.classList.contains('settings-hidden'), 'both open at once');
/* Settings drags by its header and, once dragged, survives an outside click */
rect(dd, { left: 12, top: 60, width: 220, height: 180, right: 232, bottom: 240 });
Object.defineProperty(dd, 'offsetWidth', { value: 220, configurable: true });
Object.defineProperty(dd, 'offsetHeight', { value: 180, configurable: true });
const sh = doc.getElementById('settings-header');
function mouse(el, type, x, y) { el.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1 })); }
mouse(sh, 'mousedown', 50, 70);
mouse(doc, 'mousemove', 150, 170);
mouse(doc, 'mouseup', 150, 170);
ok(dd.classList.contains('settings-floating') && dd.classList.contains('fp-floating') && dd.style.left === '112px' && dd.style.top === '160px',
   'dragging the Settings header floats it at the dragged position (' + dd.style.left + ',' + dd.style.top + ')');
mouse(doc.getElementById('outside-field'), 'mousedown', 500, 500);
doc.getElementById('outside-field').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
ok(!dd.classList.contains('settings-hidden'), 'a dragged Settings window survives an outside click');
window.toggleSettings();                 // what the header's × calls (inline onclick does not run under jsdom here)
ok(dd.classList.contains('settings-hidden'), 'its own close button closes it');
window.toggleSettings(sideSet);
ok(!dd.classList.contains('settings-hidden') && dd.classList.contains('settings-floating') && dd.style.left === '112px',
   'reopened where it was left (still floating)');
window.toggleSettings(sideSet);
window.toggleCalc(sideCalc);
ok(pop.classList.contains('calc-hidden') && dd.classList.contains('settings-hidden'), 'both closed again');

/* ── 5. Alt+C ─────────────────────────────────────────────────────────── */
function alt(target, key) {
  (target || doc.body).dispatchEvent(new window.KeyboardEvent('keydown',
    { key: key, altKey: true, bubbles: true, cancelable: true }));
}
alt(doc.body, 'c');
ok(!pop.classList.contains('calc-hidden'), 'Alt+C opens the calculator');
alt(doc.body, 'c');
ok(pop.classList.contains('calc-hidden'), 'Alt+C toggles it closed');
alt(doc.getElementById('outside-field'), 'c');
ok(pop.classList.contains('calc-hidden'), 'Alt+C is ignored while typing in a field');

/* ── 6. a dragged popover keeps its place ─────────────────────────────── */
pop.classList.add('calc-floating');
pop.style.left = '400px';
window.toggleCalc(sideCalc);
ok(pop.style.left === '400px', 'a floating (dragged) popover is not snapped back to the trigger');
pop.classList.remove('calc-floating');
window.toggleCalc(sideCalc);                         // closed again

/* ── 7. the second path that used to close the Calculator ──────────────
   Its outside-click closer (bound a tick after opening) saw the click on
   the Settings BUTTON as "outside" (real Chrome, 2026-08-29). Async: the
   closer is bound in a setTimeout, so the click must come a tick later. */
window.toggleCalc(sideCalc);                         // open, anchored (not dragged)
setTimeout(function () {
  sideSet.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(!pop.classList.contains('calc-hidden'), 'a click on the Settings button does not close the Calculator');
  doc.getElementById('settings-inner').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(!pop.classList.contains('calc-hidden'), 'nor does a click inside the Settings window');
  doc.getElementById('outside-field').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(pop.classList.contains('calc-hidden'), 'a click elsewhere still closes an anchored (never dragged) Calculator');
  process.exit(fails ? 1 : 0);
}, 20);
