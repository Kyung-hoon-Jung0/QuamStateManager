/* QA F16 — a jump into the Explorer is a NAVIGATION.
 *
 * A run's figure "Edit qN" button (and every other _navigateToExplorerPath
 * caller: Go to state, plot clicks, Diagnostics, value history, UndoNav) loaded
 * /explorer into #table-pane with no history entry: the URL and the sidebar
 * still said /datasets and Back left the app. And the q button did not
 * collapse the run below first (Go to state does), so the Explorer arrived as
 * a ~113 px sliver over a run opened at the expanded preset.
 *
 * The REAL htmx.min.js + REAL app.js under jsdom, with a fake XMLHttpRequest
 * standing in for the server, so htmx performs the real request, the real swap
 * and fires the real afterSwap / afterRequest (with its real pathInfo).
 *
 * Run: node tests/explorer_nav_history_selfcheck.cjs  (driven by tests/test_explorer_nav_history.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

const dom = new JSDOM('<!doctype html><html><head></head><body>'
  + '<nav class="sidebar-nav"><ul><li><a href="/datasets" id="nav-ds">Datasets</a></li>'
  + '<li><a href="/explorer" id="nav-ex">Json Tree View</a></li></ul></nav>'
  + '<div id="table-pane"><div id="datasets-page">list</div></div>'
  + '<div id="inspector-pane"><div id="ds-detail-root" data-uid="k:9" data-experiment="03_resonator_spectroscopy_single">'
  + '<button id="q3" onclick="event.stopPropagation(); goToQubitState(\'03_resonator_spectroscopy_single\', \'q3\')">q3</button>'
  + '</div></div></body></html>',
  { url: 'http://localhost/datasets', runScripts: 'dangerously', pretendToBeVisual: true });
const w = dom.window;
const doc = w.document;
w.Element.prototype.scrollIntoView = function () {};
w.XPathEvaluator = function () {};
w.XPathEvaluator.prototype.createExpression = function () {
  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
};

// the "server": what each path answers
let explorerStatus = 200;
const served = [];
function route(url) {
  const p = String(url).split('?')[0];
  served.push(p);
  if (p === '/explorer') return explorerStatus === 200
    ? { status: 200, body: '<div id="explorer-tree-state"><div class="tree-node">tree</div></div>' }
    : { status: explorerStatus, body: 'nope' };
  if (p === '/bulk') return { status: 200, body: '<div id="bulk-page">grid</div>' };
  return { status: 404, body: '' };
}
class FakeXHR {
  constructor() { this.readyState = 0; this.status = 0; this._h = {}; this.upload = { addEventListener() {} }; }
  open(m, u) { this.method = m; this.url = u; }
  setRequestHeader() {}
  overrideMimeType() {}
  addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
  getAllResponseHeaders() { return ''; }
  getResponseHeader() { return null; }
  abort() {}
  send() {
    setTimeout(() => {
      const r = route(this.url);
      this.status = r.status; this.responseText = r.body; this.response = r.body;
      this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
      if (this.onload) this.onload();
      (this._h.loadend || []).forEach(f => f({}));
    }, 0);
  }
}
w.XMLHttpRequest = FakeXHR;
w.fetch = function (url) {
  url = String(url);
  const body = url.indexOf('/chip/active-token') === 0 ? { loaded: true, token: 't', name: 'LabA', path: '/c' } : {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
const order = [];
w._applySplitPreset = function (which) { order.push('preset:' + which); };
w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const htmx = w.htmx;
ok(!!(htmx && htmx.ajax), '(fixture) the REAL htmx loaded');
let sidebarSyncs = 0;
const realSync = w.syncSidebarNavActive;
w.syncSidebarNavActive = function () { sidebarSyncs++; if (realSync) try { realSync(); } catch (e) {} };
doc.addEventListener('htmx:beforeRequest', function (e) {
  if (e.detail && e.detail.target && e.detail.target.id === 'table-pane') order.push('request:' + e.detail.pathInfo.requestPath);
});

(async () => {
  // ── the figure's q3 button: collapse, then a real navigation ───────────
  const h0 = w.history.length;
  doc.getElementById('q3').click();
  await tick(150);
  ok(!!doc.querySelector('#table-pane #explorer-tree-state'), 'the Explorer is in #table-pane');
  ok(w.location.pathname === '/explorer', 'the address bar names the Explorer: ' + w.location.pathname);
  ok(w.history.length === h0 + 1, 'exactly one history entry for the jump (Back undoes it)');
  ok(w.history.state && w.history.state.htmx === true, 'an htmx-shaped entry (Back runs htmx\'s own restore)');
  ok(sidebarSyncs >= 1, 'the sidebar highlight is re-synced to the new address');
  ok(order[0] === 'preset:collapsed' && order.indexOf('request:/explorer') > 0,
     'the run below is collapsed BEFORE the Explorer loads: ' + JSON.stringify(order));

  // ── already on /explorer: a second jump adds no duplicate entry ────────
  const h1 = w.history.length;
  w._navigateToExplorerPath('qubits.q3.f_01');
  await tick(150);
  ok(w.history.length === h1, 'a jump while already on /explorer pushes nothing');

  // ── a failed /explorer load moves no address, and leaves no listener ───
  w.history.pushState({ htmx: true }, '', '/datasets');
  doc.getElementById('table-pane').innerHTML = '<div id="datasets-page">list</div>';
  explorerStatus = 500;
  const h2 = w.history.length;
  w._navigateTablePane('/explorer');
  await tick(80);
  ok(w.location.pathname === '/datasets' && w.history.length === h2, 'a failed load leaves the address alone');
  explorerStatus = 200;
  htmx.ajax('GET', '/explorer', { source: '#table-pane', target: '#table-pane', swap: 'innerHTML' });
  await tick(80);
  ok(w.location.pathname === '/datasets',
     'the failed jump\'s listener is gone (a later foreign /explorer swap is not renamed)');

  // ── PaneState's skip path pushes its own entry and fires paneRestored ──
  w.history.pushState({ htmx: true }, '', '/datasets');
  const cancelSkip = function (e) {
    if (e.detail && e.detail.pathInfo && e.detail.pathInfo.requestPath === '/explorer') {
      e.preventDefault(); e.stopImmediatePropagation();
      w.history.pushState({ htmx: true }, '', '/explorer');
      doc.dispatchEvent(new w.CustomEvent('paneRestored', { detail: { route: '/explorer' } }));
    }
  };
  doc.addEventListener('htmx:beforeRequest', cancelSkip, true);
  const h3 = w.history.length;
  w._navigateTablePane('/explorer');
  await tick(80);
  doc.removeEventListener('htmx:beforeRequest', cancelSkip, true);
  ok(w.history.length === h3 + 1 && w.location.pathname === '/explorer', 'the skip path leaves exactly one /explorer entry');
  w.history.pushState({ htmx: true }, '', '/datasets');
  htmx.ajax('GET', '/explorer', { source: '#table-pane', target: '#table-pane', swap: 'innerHTML' });
  await tick(80);
  ok(w.location.pathname === '/datasets', 'the skip path released the listeners (paneRestored)');

  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
