/* QA F17 -- /datasets on the 1366x768 laptop showed no run on its first
 * screen: the top bar wrapped to three ~78 px rows (Pico nav spacing, plus a
 * Settings/Calculator fallback pair that showed ALWAYS), and the Experiments
 * band opened with every chip row. Executes the SHIPPED code, sliced from
 * app.js, under jsdom:
 *   1. toggleSidebar mirrors the collapsed state on <html>
 *      (html.sidebar-is-collapsed), the class the fallback's CSS reads -- the
 *      bar is not inside .app-layout, so .app-layout's class cannot reach it.
 *   2. the Experiments band's apply(): with NO stored choice it starts folded
 *      on a short window (< 900 px) and open on a tall one; a stored choice
 *      wins either way; the toggle's aria-expanded follows.
 * Run: node tests/topbar_compact_selfcheck.cjs (driven by
 * tests/test_topbar_compact.py). Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

function slice(startMarker, endMarker) {
  const i = APP.indexOf(startMarker);
  if (i < 0) throw new Error('not found in app.js: ' + startMarker);
  const j = APP.indexOf(endMarker, i);
  if (j < 0) throw new Error('end not found after: ' + startMarker);
  return APP.slice(i, j + endMarker.length);
}
const TOGGLE_SIDEBAR = slice('window.toggleSidebar = function() {', '\n};');
// toggleExpFilterCollapsed + the apply() IIFE right after it
const EXP_BAND = slice('window.toggleExpFilterCollapsed = function() {', '\n})();');

function world(h, stored) {
  const dom = new JSDOM('<!doctype html><html><body>' +
    '<div class="shell-head"><header class="topbar"><nav><ul><li class="topbar-tools-fallback"></li></ul></nav></header></div>' +
    '<div class="app-layout"><button id="exp-filter-toggle" aria-expanded="true"></button></div>' +
    '</body></html>', { url: 'http://localhost/datasets', runScripts: 'outside-only' });
  const w = dom.window;
  Object.defineProperty(w, 'innerHeight', { value: h, configurable: true });
  if (stored != null) w.localStorage.setItem('quam_exp_filter_collapsed', stored);
  return w;
}

// 1. the sidebar toggle mirrors its state on <html>
{
  const w = world(768);
  w.eval(TOGGLE_SIDEBAR);
  const html = w.document.documentElement;
  const layout = w.document.querySelector('.app-layout');
  w.toggleSidebar();
  ok(layout.classList.contains('sidebar-collapsed') && html.classList.contains('sidebar-is-collapsed'),
     'toggleSidebar: collapsing marks <html> too (' + html.className + ')');
  w.toggleSidebar();
  ok(!layout.classList.contains('sidebar-collapsed') && !html.classList.contains('sidebar-is-collapsed'),
     'toggleSidebar: expanding clears it (' + html.className + ')');
}

// 2. the Experiments band's default (apply() runs at DOMContentLoaded, or at
//    once when the document is already parsed -- either way, read after both)
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
async function band(h, stored) {
  const w = world(h, stored);
  w.eval(EXP_BAND);
  await tick();
  return { folded: w.document.body.classList.contains('exp-filter-collapsed'),
           aria: w.document.getElementById('exp-filter-toggle').getAttribute('aria-expanded') };
}
(async () => {
let b = await band(768, null);
ok(b.folded && b.aria === 'false', '1366x768, no stored choice: the band starts folded (' + JSON.stringify(b) + ')');
b = await band(1000, null);
ok(!b.folded && b.aria === 'true', 'a tall window, no stored choice: the band starts open, as before (' + JSON.stringify(b) + ')');
b = await band(768, '0');
ok(!b.folded && b.aria === 'true', 'a stored "open" wins on a short window (' + JSON.stringify(b) + ')');
b = await band(1000, '1');
ok(b.folded && b.aria === 'false', 'a stored "folded" wins on a tall window (' + JSON.stringify(b) + ')');

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
