/* docs/301 F17: what hx-history="false" does under the BUNDLED htmx 2.0.4.
 *
 * Leaving the Calibration log or the Datasets list logged
 * htmx:historyCacheError x4: htmx snapshots the page it leaves (the whole
 * body) into localStorage, and those pages are larger than the quota. They now
 * carry hx-history="false". This check pins the mechanism the fix relies on,
 * with the REAL htmx.min.js + REAL app.js (PaneState) under jsdom and a fake
 * server:
 *
 *  M1 leaving a page that carries the marker stores NO snapshot of it (and
 *     raises no historyCacheError);
 *  M2 Back onto it asks the SERVER (an HX-History-Restore-Request), and the
 *     page it answers is what the user sees -- the pane holds the list again;
 *  C1 CONTROL: the same page without the marker IS snapshotted, so M1 is not
 *     vacuous (an htmx that ignored the attribute would fail M1, not pass it).
 *
 * Run: node tests/history_marker_selfcheck.cjs  (driven by tests/test_history_cache_marker.py)
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
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(r => setTimeout(r, ms || 40)); }

const NAV = '<nav class="sidebar-nav"><ul>'
  + '<li><a id="nav-trends" href="/trends" hx-get="/trends" hx-target="#table-pane" hx-push-url="true">Trends</a></li>'
  + '</ul></nav>';
const LIST = (marked) => '<div class="datasets-page"' + (marked ? ' hx-history="false"' : '') + '>'
  + '<table><tbody id="datasets-tbody"><tr><td>run 1</td></tr></tbody></table></div>';
const SHELL = (pane) => NAV + '<div id="pending-tray" data-seq="1">tray</div>'
  + '<div id="table-pane">' + pane + '</div><div id="inspector-pane"></div><div id="status-bar"></div>';

function world(marked) {
  const requests = [];
  const dom = new JSDOM('<!doctype html><html><head></head><body>' + SHELL(LIST(marked)) + '</body></html>',
    { url: 'http://localhost/datasets', runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {};
  w.scrollTo = function () {};
  w.XPathEvaluator = function () {};
  w.XPathEvaluator.prototype.createExpression = function () {
    return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
  };
  class FakeXHR {
    constructor() { this.readyState = 0; this.status = 0; this._h = {}; this._req = {}; this.upload = { addEventListener() {} }; }
    open(m, u) { this.method = m; this.url = u; }
    setRequestHeader(k, v) { this._req[k] = v; }
    overrideMimeType() {}
    addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
    getAllResponseHeaders() { return ''; }
    getResponseHeader() { return null; }
    abort() {}
    send() {
      requests.push({ url: this.url, restore: this._req['HX-History-Restore-Request'] === 'true' });
      setTimeout(() => {
        const p = String(this.url).split('?')[0];
        let body = '';
        if (p === '/trends') body = '<div id="trends-page">charts</div>';
        // a history restore gets the FULL page (routes._is_htmx), body and all
        if (p === '/datasets') body = '<!doctype html><html><head><title>Datasets</title></head><body>'
          + SHELL(LIST(marked)) + '</body></html>';
        this.status = body ? 200 : 404; this.responseText = body; this.response = body;
        this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
        if (this.onload) this.onload();
        (this._h.loadend || []).forEach(f => f({}));
      }, 0);
    }
  }
  w.XMLHttpRequest = FakeXHR;
  w.fetch = function () {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
  };
  w.eval(HTMX);
  w.eval(APP);
  const errors = [];
  w.document.addEventListener('htmx:historyCacheError', () => errors.push(1));
  return { w, requests, errors };
}
async function ready(w) {
  if (w.document.readyState === 'loading') {
    await new Promise(r => w.document.addEventListener('DOMContentLoaded', r));
  }
  await tick(20);
  w.htmx.process(w.document.body);
}
function cached(w, url) {
  let c = [];
  try { c = JSON.parse(w.localStorage.getItem('htmx-history-cache') || '[]') || []; } catch (e) { c = []; }
  return c.some(it => it && it.url === url);
}

(async () => {
  // C1 control first: without the marker the page IS snapshotted
  const ctl = world(false);
  await ready(ctl.w);
  ctl.w.document.getElementById('nav-trends').click();
  await tick(150);
  ok(ctl.w.location.pathname === '/trends', '(fixture) the sidebar link navigated');
  ok(cached(ctl.w, '/datasets'), 'C1 control: an unmarked page is snapshotted on the way out');

  const m = world(true);
  await ready(m.w);
  m.w.document.getElementById('nav-trends').click();
  await tick(150);
  ok(m.w.location.pathname === '/trends', '(fixture) the sidebar link navigated');
  ok(!cached(m.w, '/datasets') && m.errors.length === 0,
     'M1 a page carrying hx-history="false" is not snapshotted (no cache write, no historyCacheError)');
  m.w.history.back();
  await tick(250);
  const restore = m.requests.filter(r => r.restore);
  ok(m.w.location.pathname === '/datasets' && restore.length === 1 && restore[0].url.indexOf('/datasets') === 0,
     'M2 Back asks the server for the page (' + JSON.stringify(m.requests) + ')');
  ok(!!m.w.document.querySelector('#table-pane #datasets-tbody') && !m.w.document.getElementById('trends-page'),
     'M2 ...and the restored page is the list again');

  console.log(fails ? fails + ' FAILED' : 'all history-marker checks passed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: harness threw', e && e.stack || e); process.exit(1); });
