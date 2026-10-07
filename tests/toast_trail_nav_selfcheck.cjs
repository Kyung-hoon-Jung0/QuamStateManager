/* docs/301 F7: a success toast leaves on its own, and an undo takes it back;
 * the Undo / Redo panel does not follow the user onto the next page.
 *
 * The REAL htmx.min.js + REAL app.js + REAL undo-trail.js under jsdom, with a
 * fake XMLHttpRequest standing in for the server, so the out-of-band toast,
 * the HX-Trigger (cellsReverted), the pushed address and the history restore
 * are htmx's own.
 *
 *  A. "Applied to the live chip." arrives OUT OF BAND (the apply answer swaps
 *     the tray and rides the toast beside it): it fades after a few seconds.
 *     A warning toast keeps its x and stays (control).
 *  B. Ctrl+Z that lands takes the apply's success toast away at once; a
 *     refused step (level warning, nothing reverted) leaves it.
 *  C. The panel hides when the page changes, stays for a re-render of the
 *     same page, keeps its steps, and the next step shows it again.
 *  D. "go to field" that opens another page keeps the panel (the user went
 *     there from it); the next ordinary page change hides it.
 *  E. Back restores htmx's body snapshot, which held a COPY of the visible
 *     panel (no listeners, a dead x): no such copy is left on screen.
 *
 * Run: node tests/toast_trail_nav_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const HTMX = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const TRAIL = fs.readFileSync(path.join(STATIC, 'undo-trail.js'), 'utf8');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(r => setTimeout(r, ms || 40)); }

const TOAST = (level, msg) => '<div class="toast toast-' + level + '" style="transition: opacity 0.5s;">'
  + '<p>' + msg + '</p><button type="button" class="toast-x" aria-label="Dismiss" '
  + 'onclick="this.closest(\'.toast\').remove()">&times;</button></div>';
const TRAY = '<div id="pending-tray" data-change-count="0">tray</div>';
const UNDONE = (dp) => JSON.stringify({ cellsReverted: {
  message: 'Undone -> live: ' + dp + ' -> 0.00001', live: true,
  entries: [{ dot_path: dp, old_value_str: '1e-05', old_value_disp: '0.00001', old_kind: 'num' }] } });
const REFUSED = JSON.stringify({ cellsReverted: {
  message: 'Not undone - the live chip moved', level: 'warning', live: false, entries: [] } });

function route(method, url) {
  const p = String(url).split('?')[0];
  const q = String(url).split('?')[1] || '';
  if (p === '/state/apply-to-live') {
    const warn = /level=warning/.test(q);
    return { status: 200, body: TRAY + '\n<div id="status-bar" hx-swap-oob="innerHTML">'
      + (warn ? TOAST('warning', 'Applied to the live chip - a crash value')
              : TOAST('success', 'Applied to the live chip.')) + '</div>' };
  }
  if (p === '/undo') {
    const dp = /dp=([^&]+)/.exec(q);
    return { status: 200, body: TRAY,
             headers: { 'HX-Trigger': /refused/.test(q) ? REFUSED : UNDONE(dp ? decodeURIComponent(dp[1]) : 'qubits.q1.T1') } };
  }
  if (p === '/trends') return { status: 200, body: '<div id="trends-page">charts</div>' };
  if (p === '/bulk') return { status: 200, body: '<div id="bulk-page">grid</div>' };
  if (p === '/explorer') return { status: 200, body: '<div id="explorer-tree-state"><div class="tree-node" data-path="ports">tree</div></div>' };
  return { status: 404, body: '' };
}

function world(url) {
  const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<nav class="sidebar-nav"><ul>'
    + '<li><a id="nav-trends" href="/trends" hx-get="/trends" hx-target="#table-pane" hx-push-url="true">Trends</a></li>'
    + '</ul></nav>' + TRAY
    + '<div id="table-pane"><div id="bulk-page">grid</div></div>'
    + '<div id="inspector-pane"></div><div id="status-bar"></div></body></html>',
    { url: 'http://localhost' + (url || '/bulk'), runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {};
  w.scrollTo = function () {};
  w.XPathEvaluator = function () {};
  w.XPathEvaluator.prototype.createExpression = function () {
    return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
  };
  class FakeXHR {
    constructor() { this.readyState = 0; this.status = 0; this._h = {}; this._rh = {}; this.upload = { addEventListener() {} }; }
    open(m, u) { this.method = m; this.url = u; }
    setRequestHeader() {}
    overrideMimeType() {}
    addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
    getAllResponseHeaders() {
      return Object.keys(this._rh).map(k => k.toLowerCase() + ': ' + this._rh[k]).join('\r\n');
    }
    getResponseHeader(n) {
      const k = Object.keys(this._rh).find(x => x.toLowerCase() === String(n).toLowerCase());
      return k ? this._rh[k] : null;
    }
    abort() {}
    send() {
      setTimeout(() => {
        const r = route(this.method, this.url);
        this.status = r.status; this.responseText = r.body; this.response = r.body;
        this._rh = r.headers || {};
        this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
        if (this.onload) this.onload();
        (this._h.loadend || []).forEach(f => f({}));
      }, 0);
    }
  }
  w.XMLHttpRequest = FakeXHR;
  w.fetch = function (url) {
    // a chip is open: the Explorer jump asks before it navigates
    const body = String(url).indexOf('/chip/active-token') === 0
      ? { loaded: true, token: 't', name: 'chipX', path: '/c' } : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body),
                             text: () => Promise.resolve('') });
  };
  w.eval(HTMX);
  w.eval(APP);
  w.eval(TRAIL);
  return w;
}
async function ready(w) {
  if (w.document.readyState === 'loading') {
    await new Promise(r => w.document.addEventListener('DOMContentLoaded', r));
  }
  await tick(20);
  w.htmx.process(w.document.body);
}
const bar = (w) => w.document.getElementById('status-bar');
const has = (w, sel, text) => Array.prototype.some.call(bar(w).querySelectorAll(sel),
  t => t.textContent.indexOf(text) >= 0);
const panel = (w) => w.document.getElementById('undo-trail');
const shownPanels = (w) => Array.prototype.filter.call(w.document.querySelectorAll('.undo-trail'), p => !p.hidden);

(async () => {
  // -- A + B: the success toast fades, and an undo takes it back ----------
  const wa = world(), ww = world(), wu = world(), wr = world();
  await Promise.all([wa, ww, wu, wr].map(ready));
  wa.htmx.ajax('POST', '/state/apply-to-live', { target: '#pending-tray', swap: 'outerHTML' });
  ww.htmx.ajax('POST', '/state/apply-to-live?level=warning', { target: '#pending-tray', swap: 'outerHTML' });
  wu.htmx.ajax('POST', '/state/apply-to-live', { target: '#pending-tray', swap: 'outerHTML' });
  wr.htmx.ajax('POST', '/state/apply-to-live', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  ok(has(wa, '.toast-success', 'Applied to the live chip.'), '(fixture) the apply answer lands the success toast out of band');
  ok(has(ww, '.toast-warning', 'Applied to the live chip'), '(fixture) ...and the warning variant');
  wu.htmx.ajax('POST', '/undo', { target: '#pending-tray', swap: 'outerHTML' });
  wr.htmx.ajax('POST', '/undo?refused=1', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  ok(!has(wu, '.toast-success', 'Applied to the live chip.'),
     'B1 Ctrl+Z that reverted the apply takes its success toast away at once');
  ok(has(wu, '.toast', 'Undone -> live'), 'B2 ...and its own toast says what holds now');
  ok(has(wr, '.toast-success', 'Applied to the live chip.'),
     'B3 a refused step (nothing reverted) leaves the apply toast');
  await tick(4300);
  ok(!has(wa, '.toast', 'Applied to the live chip.'),
     'A1 the out-of-band success toast fades on its own (' + bar(wa).innerHTML.length + ' chars left in the bar)');
  ok(has(ww, '.toast-warning', 'Applied to the live chip'), 'A2 a warning toast stays until its x is pressed');

  // -- C: the panel leaves with the page ----------------------------------
  const wc = world();
  await ready(wc);
  wc.htmx.ajax('POST', '/undo', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  ok(panel(wc) && !panel(wc).hidden, '(fixture) a server undo opens the panel on /bulk');
  wc.htmx.ajax('GET', '/bulk', { target: '#table-pane', swap: 'innerHTML' });
  await tick(120);
  ok(!panel(wc).hidden, 'C1 a re-render of the same page keeps the panel');
  wc.document.getElementById('nav-trends').click();
  await tick(150);
  ok(wc.location.pathname === '/trends', '(fixture) the sidebar link navigated: ' + wc.location.pathname);
  ok(panel(wc).hidden === true, 'C2 a page change hides the panel (it sat over the next page\'s charts)');
  ok(wc.UndoTrail.steps().length === 1, 'C3 its steps are kept');
  wc.htmx.ajax('POST', '/undo', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  ok(!panel(wc).hidden && wc.UndoTrail.steps().length === 2, 'C4 the next step shows it again');

  // -- D: "go to field" keeps the panel on the page it opened -------------
  const wd = world();
  await ready(wd);
  wd.htmx.ajax('POST', '/undo?dp=ports.con1.1.offset', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  const go = panel(wd) && panel(wd).querySelector('.undo-trail-goto');
  ok(!!go, '(fixture) the step carries a go-to-field button');
  go.click();
  await tick(250);
  ok(wd.location.pathname === '/explorer', '(fixture) go to field opened the field\'s page: ' + wd.location.pathname);
  ok(!panel(wd).hidden, 'D1 the panel stays on the page the user opened from it');
  wd.document.getElementById('nav-trends').click();
  await tick(150);
  ok(wd.location.pathname === '/trends' && panel(wd).hidden === true,
     'D2 the next ordinary page change hides it');

  // -- E: Back leaves no dead copy of the panel ---------------------------
  const we = world();
  await ready(we);
  we.htmx.ajax('POST', '/undo', { target: '#pending-tray', swap: 'outerHTML' });
  await tick(120);
  we.document.getElementById('nav-trends').click();
  await tick(150);
  let restored = false;
  we.document.addEventListener('htmx:historyRestore', function () { restored = true; });
  we.history.back();
  await tick(200);
  ok(restored && we.location.pathname === '/bulk', '(fixture) Back restored /bulk from htmx\'s snapshot');
  ok(shownPanels(we).length === 0,
     'E1 no copy of the panel is left on screen after Back (' + shownPanels(we).length + ' shown)');

  console.log(fails ? fails + ' FAILED' : 'all toast / trail navigation checks passed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: harness threw', e && e.stack || e); process.exit(1); });
