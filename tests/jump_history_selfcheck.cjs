/* QA F-C -- a jump to the Json Tree is a NAVIGATION, so it gets a history entry.
 *
 * Diagnostics' "Go to field", the Types card / type-alarm "Show in Json Tree
 * View", TypeAlert.manual ("I'll fix them myself") and every other caller of
 * `_navigateToExplorerPath` swapped /explorer into #table-pane with a
 * source-less htmx.ajax. htmx 2 has no `pushUrl` ajax option and decides the
 * push from the SOURCE element's hx-push-url, so nothing was pushed: the URL
 * stayed /diagnostics, the sidebar kept Diagnostics lit, Back skipped over the
 * Diagnostics page and F5 on the tree showed Diagnostics again.
 *
 * The fix sources the request from the sidebar's own Json Tree View link
 * (hx-push-url="true") -- the same entry + sidebar sync a click on it gives.
 *
 * Pins, against the real shipped app.js:
 *   H1  from /diagnostics the /explorer request is sourced from that link
 *   H2  ...whose hx-push-url is "true" (the thing htmx reads to push)
 *   H3  from /explorer itself there is NO source (no same-URL entry)
 *   H4  with no sidebar link on the page it degrades to the old call, no throw
 *
 * Run: node tests/jump_history_selfcheck.cjs   (needs jsdom)
 */
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
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const HTML = '<!doctype html><html><body>'
  + '<aside class="sidebar"><nav class="sidebar-nav"><ul>'
  + '<li><a id="nav-diag" href="/diagnostics" hx-get="/diagnostics" hx-target="#table-pane" hx-push-url="true" class="active">Diagnostics</a></li>'
  + '<li><a id="nav-explorer" href="/explorer" hx-get="/explorer" hx-target="#table-pane" hx-sync="#table-pane:replace" hx-push-url="true">Json Tree View</a></li>'
  + '</ul></nav></aside>'
  + '<main><div id="table-pane"></div></main><div id="status-bar"></div></body></html>';

const dom = new JSDOM(HTML, { url: 'http://localhost/diagnostics', pretendToBeVisual: true });
const { window } = dom;
const d = window.document;
global.window = window; global.document = d; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.location = window.location;
global.history = window.history;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
// /chip/active-token says a chip is loaded, so the helper goes straight to the
// /explorer request (the path every QA entry point took).
global.fetch = function (url) {
  if (String(url).indexOf('/chip/active-token') === 0) {
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ loaded: true }); } });
  }
  return new Promise(function () {});
};
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const calls = [];
window.htmx = {
  ajax: function (verb, url, opts) { calls.push({ verb: verb, url: url, opts: opts || {} }); return Promise.resolve(); },
  trigger: function () {}, process: function () {},
};
global.htmx = window.htmx;
window.Element.prototype.scrollIntoView = function () {};

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
// the toast is not what this file is about (the tree never renders here)
window._showPlotClickToast = function () {};

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function explorerCalls() { return calls.filter(function (c) { return String(c.url).indexOf('/explorer') === 0; }); }

async function main() {
  ok(typeof window._navigateToExplorerPath === 'function', 'preflight: the helper is exported');

  // H1/H2 -- from /diagnostics
  calls.length = 0;
  window._navigateToExplorerPath('qubits.q2.resonator.f_01');
  await sleep(50);
  let ex = explorerCalls();
  ok(ex.length === 1, 'H1 one /explorer request (got ' + ex.length + ')');
  const link = d.getElementById('nav-explorer');
  const src = ex[0] && ex[0].opts.source;
  const srcEl = typeof src === 'string' ? d.querySelector(src) : src;
  ok(srcEl === link, 'H1 the request is sourced from the sidebar Json Tree View link'
     + ' (got ' + (srcEl ? (srcEl.id || srcEl.tagName) : String(src)) + ')');
  ok(srcEl && srcEl.getAttribute('hx-push-url') === 'true',
     'H2 the source carries hx-push-url="true", so htmx pushes /explorer');
  ok(ex[0] && ex[0].opts.target === '#table-pane' && ex[0].opts.swap === 'innerHTML',
     'H2 target and swap are unchanged');

  // H3 -- already on /explorer (the tree's own warning-mark click)
  window.history.replaceState({}, '', '/explorer');
  calls.length = 0;
  window._navigateToExplorerPath('qubits.q2.resonator.f_01');
  await sleep(50);
  ex = explorerCalls();
  ok(ex.length === 1 && !ex[0].opts.source,
     'H3 on /explorer the request has no source (no same-URL history entry)');

  // H4 -- no sidebar link on the page
  window.history.replaceState({}, '', '/diagnostics');
  link.parentNode.removeChild(link);
  calls.length = 0;
  let threw = null;
  try { window._navigateToExplorerPath('qubits.q2.resonator.f_01'); } catch (e) { threw = e; }
  await sleep(50);
  ex = explorerCalls();
  ok(!threw && ex.length === 1 && !ex[0].opts.source,
     'H4 without the sidebar link it degrades to the source-less call');

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'jump_history_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
}

main().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
