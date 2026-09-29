/* docs/226 -- the htmx side of the URL prefix, under the REAL bundled htmx 2.0.4.
 *
 * sm-root.js makes htmx requests prefix-correct with an htmx EXTENSION, not a
 * listener: htmx's triggerEvent runs dispatchEvent, then the kebab-case
 * dispatch, and only THEN every extension's onEvent -- so the extension runs
 * after every DOM listener, whenever and wherever it was registered. These
 * pins drive real requests (a stand-in XMLHttpRequest records what htmx
 * OPENS) in jsdom with the shipped sm-root.js + htmx.min.js loaded as parse-
 * time scripts, as base.html loads them:
 *
 *  C1  an element-level configRequest listener that REPLACES detail.path with
 *      an un-prefixed '/diff?base=1', then a document-level one that edits
 *      base= -> the request goes out as '/sm/diff?base=7', prefixed exactly
 *      once; a listener added after the page loaded (a lazy bundle) that
 *      replaces the path is still prefixed
 *  C2  htmx.ajax('GET', '/qubits') opens '/sm/qubits'; an already-prefixed
 *      path is not prefixed again
 *  C3  hx-get="/sm/qubits" hx-push-url="true" (a template attribute, rooted
 *      by the server) pushes '/sm/qubits' -- no double; an un-prefixed
 *      hx-get (JS-built markup) is requested AND pushed under /sm
 *  C4  HX-Location: {"path": "/diff", "target": ...} and the plain-string
 *      form -> the follow-up request and the pushed URL are under /sm
 *  C5  the two shipped path gates fire under a prefixed path: bulk-edit.js's
 *      /bulk rewrite (dynhide=/vw=) and diff-panes.js's /diff baseline
 *      rewrite, driven through the shipped files on real requests
 *  O*  order independence: htmx loaded BEFORE sm-root.js, and htmx loaded
 *      after the page finished loading, both prefix the first request
 *  Z*  root (data-root=""): the same requests go out un-prefixed, even with
 *      hx-ext="sm-root" on the body -- nothing is defined at root
 *
 * Run: node tests/prefix_hooks_selfcheck.cjs   (driven by tests/test_js_root_lint.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const HTMX = read('htmx.min.js'), SMROOT = read('sm-root.js'), BULK = read('bulk-edit.js'), DIFF = read('diff-panes.js');
const ORIGIN = 'http://127.0.0.1:5050';

let fails = 0, passes = 0;
function ok(c, m) { if (c) { passes++; console.log('ok - ' + m); } else { fails++; console.error('FAIL: ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

// htmx scans for hx-on attributes through `new XPathEvaluator`; jsdom's XPath
// cannot run the compiled expression. No fixture uses hx-on, so an empty
// iterator is faithful (the settle_config selfcheck's accommodation).
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

const BODY =
  '<div id="t"></div>' +
  '<button id="b-replace" hx-get="/sm/diff?base=0" hx-target="#t">replace</button>' +
  '<button id="b-late" hx-get="/sm/qubits" hx-target="#t">late</button>' +
  '<button id="b-push" hx-get="/sm/qubits" hx-push-url="true" hx-target="#t">push</button>' +
  '<button id="b-bare" hx-get="/pairs" hx-push-url="true" hx-target="#t">bare</button>' +
  '<button id="b-loc" hx-get="/sm/go-json" hx-target="#t">loc json</button>' +
  '<button id="b-loc2" hx-get="/sm/go-str" hx-target="#t">loc str</button>' +
  '<div id="diff-root" data-base="3"><button id="b-diff" hx-get="/sm/diff?a=x&amp;base=0" hx-target="#t">tab</button>' +
  '<input class="dp-search" value="needle"></div>' +
  '<button id="b-bulk" hx-get="/sm/bulk?page=1" hx-target="#t">bulk</button>' +
  '<button id="b-bulk-av" hx-get="/sm/bulk/all-values" hx-target="#t">all values</button>';

/* A stand-in XHR: records what htmx opens, answers on the next task. */
function fakeXHR(w, log, respond) {
  function X() { this._h = {}; this._rh = {}; this.readyState = 0; this.status = 0; this.upload = { addEventListener: function () {} }; }
  X.prototype.open = function (m, u) { this.method = m; this.url = u; this.readyState = 1; };
  X.prototype.setRequestHeader = function (k, v) { this._h[k] = v; };
  X.prototype.overrideMimeType = function () {};
  X.prototype.addEventListener = function () {};
  X.prototype.abort = function () {};
  X.prototype.getAllResponseHeaders = function () {
    const rh = this._rh; return Object.keys(rh).map((k) => k + ': ' + rh[k]).join('\r\n');
  };
  X.prototype.getResponseHeader = function (k) {
    k = k.toLowerCase();
    for (const n of Object.keys(this._rh)) if (n.toLowerCase() === k) return this._rh[n];
    return null;
  };
  X.prototype.send = function (body) {
    const self = this;
    log.push({ method: self.method, url: self.url, body: body });
    const r = respond(self.url) || {};
    w.setTimeout(function () {
      self.status = r.status || 200; self.readyState = 4;
      self.response = self.responseText = r.body != null ? r.body : '<p>ok</p>';
      self._rh = r.headers || {};
      self.responseURL = new w.URL(self.url, w.location.href).href;
      if (self.onload) self.onload();
    }, 0);
  };
  return X;
}

/* order: 'root-first' (base.html), 'htmx-first', 'htmx-late' */
async function world(opts) {
  const root = opts.root;
  const ext = opts.ext != null ? opts.ext : !!root;
  const S = (src) => '<script>' + src + '</scr' + 'ipt>';
  let head = S(XPATH_SHIM);
  if (opts.order === 'htmx-first') head += S(HTMX) + S(SMROOT);
  else if (opts.order === 'htmx-late') head += S(SMROOT);
  else head += S(SMROOT) + S(HTMX);
  const html = '<!doctype html><html data-root="' + root + '"><head>' + head + '</head><body'
    + (ext ? ' hx-ext="sm-root"' : '') + '>' + BODY + '</body></html>';
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: ORIGIN + (opts.at || root + '/start') });
  const w = dom.window;
  const T = { w: w, xhr: [], pushed: [] };
  const answers = opts.answers || {};
  w.XMLHttpRequest = fakeXHR(w, T.xhr, (u) => answers[u]);
  w.console.error = function () {};                      // htmx logs a swap target it cannot find; not under test
  await new Promise((resolve) => {
    if (w.document.readyState === 'complete') resolve();
    else w.addEventListener('load', () => w.setTimeout(resolve, 0));
  });
  if (opts.order === 'htmx-late') w.eval(HTMX);
  T.last = () => T.xhr[T.xhr.length - 1];
  T.urls = () => T.xhr.map((x) => x.url);
  T.loc = () => w.location.pathname + w.location.search;
  T.click = async (id) => { w.document.getElementById(id).click(); await tick(30); };
  return T;
}

(async function main() {
  // ── C1-C4 under '/sm', base.html's order ────────────────────────────────
  {
    const T = await world({ root: '/sm', answers: {
      '/sm/go-json': { headers: { 'HX-Location': JSON.stringify({ path: '/diff', target: '#t' }) } },
      '/sm/go-str': { headers: { 'HX-Location': '/qubits' } },
    } });
    const w = T.w, d = w.document;
    ok(w.SM.root === '/sm' && typeof w.htmx.defineExtension === 'function', 'C0 the real htmx and sm-root.js are loaded, root "/sm"');

    const seen = [];
    d.getElementById('b-replace').addEventListener('htmx:configRequest', function (ev) {
      seen.push('element:' + ev.detail.path);
      ev.detail.path = '/diff?base=1';                     // REPLACES, un-prefixed
    });
    d.addEventListener('htmx:configRequest', function (ev) {
      if (!/^\/diff\?base=/.test(ev.detail.path)) return;
      seen.push('document:' + ev.detail.path);
      ev.detail.path = ev.detail.path.replace(/base=\d+/, 'base=7');
    });
    await T.click('b-replace');
    ok(T.last().url === '/sm/diff?base=7', 'C1 element listener replaced the path, document listener edited it: sent as ' + T.last().url);
    ok(seen.join(' ; ') === 'element:/sm/diff?base=0 ; document:/diff?base=1',
       'C1 ...both DOM listeners ran BEFORE the extension (they saw the attribute path, then the replaced one): ' + seen.join(' ; '));

    d.addEventListener('htmx:configRequest', function (ev) {      // added after load: a lazy bundle
      if (ev.detail.elt && ev.detail.elt.id === 'b-late') ev.detail.path = '/qubits?late=1';
    });
    await T.click('b-late');
    ok(T.last().url === '/sm/qubits?late=1', 'C1 a listener registered after the page loaded is still followed by the prefix: ' + T.last().url);

    await w.htmx.ajax('GET', '/qubits', { target: '#t' });
    await tick(10);
    ok(T.last().url === '/sm/qubits', 'C2 htmx.ajax("GET", "/qubits") opens ' + T.last().url);
    await w.htmx.ajax('GET', '/sm/pairs?x=1', { target: '#t' });
    await tick(10);
    ok(T.last().url === '/sm/pairs?x=1', 'C2 htmx.ajax with an already-prefixed path opens ' + T.last().url + ' (no double)');

    await T.click('b-push');
    await tick(30);
    ok(T.last().url === '/sm/qubits' && T.loc() === '/sm/qubits', 'C3 hx-get="/sm/qubits" hx-push-url: requested and pushed as /sm/qubits (location ' + T.loc() + ')');
    await T.click('b-bare');
    await tick(30);
    ok(T.last().url === '/sm/pairs' && T.loc() === '/sm/pairs', 'C3 an un-prefixed hx-get="/pairs" is requested AND pushed under /sm (location ' + T.loc() + ')');

    const n0 = T.xhr.length;
    await T.click('b-loc');
    await tick(40);
    ok(T.xhr.length === n0 + 2 && T.xhr[n0 + 1].url === '/sm/diff', 'C4 HX-Location {"path":"/diff"} -> follow-up request ' + (T.xhr[n0 + 1] && T.xhr[n0 + 1].url));
    ok(T.loc() === '/sm/diff', 'C4 ...and the URL it pushes is ' + T.loc());
    const n1 = T.xhr.length;
    await T.click('b-loc2');
    await tick(40);
    ok(T.xhr[n1 + 1] && T.xhr[n1 + 1].url === '/sm/qubits' && T.loc() === '/sm/qubits',
       'C4 HX-Location "/qubits" (plain string) -> ' + (T.xhr[n1 + 1] && T.xhr[n1 + 1].url) + ', pushed ' + T.loc());
    ok(T.urls().every((u) => u.indexOf('/sm/sm') < 0), 'C no request in this world was double-prefixed (' + T.xhr.length + ' requests)');
  }

  // ── C5: the shipped path gates, on real requests under '/sm' ────────────
  {
    const T = await world({ root: '/sm' });
    const w = T.w;
    w.localStorage.setItem('quam_bulk_dynhidden', JSON.stringify(['colA']));
    Object.defineProperty(w.screen, 'availWidth', { configurable: true, get: () => 1920 });   // jsdom reports 0
    w.eval(BULK);                                        // bundle files load after htmx, as in production
    w.eval(DIFF);
    await T.click('b-bulk');
    const u1 = T.last().url;
    ok(/^\/sm\/bulk\?/.test(u1) && /[?&]dynhide=colA(&|$)/.test(u1) && /[?&]vw=1920(&|$)/.test(u1) && /[?&]page=1(&|$)/.test(u1),
       'C5 bulk-edit.js: a prefixed hx-get="/sm/bulk?page=1" still gets dynhide= and vw=: ' + u1);
    await w.htmx.ajax('GET', '/bulk', { target: '#t' });
    await tick(10);
    const u2 = T.last().url;
    ok(/^\/sm\/bulk\?/.test(u2) && /dynhide=colA/.test(u2), 'C5 bulk-edit.js: htmx.ajax("GET", "/bulk") -> ' + u2);
    await T.click('b-bulk-av');
    ok(T.last().url === '/sm/bulk/all-values', 'C5 bulk-edit.js: "/sm/bulk/all-values" is not a /bulk grid request (untouched): ' + T.last().url);
    await T.click('b-diff');
    const u3 = T.last().url;
    ok(/^\/sm\/diff\?/.test(u3) && /[?&]base=3(&|$)/.test(u3) && !/base=0/.test(u3) && /[?&]q=needle(&|$)/.test(u3),
       'C5 diff-panes.js: a prefixed hx-get="/sm/diff?..&base=0" inside #diff-root carries the current baseline and search: ' + u3);
    ok(T.urls().every((u) => u.indexOf('/sm/sm') < 0), 'C5 no gate request was double-prefixed');
  }

  // ── O: registration-order independence ───────────────────────────────
  {
    const T = await world({ root: '/sm', order: 'htmx-first' });
    await T.w.htmx.ajax('GET', '/qubits', { target: '#t' });
    await tick(10);
    ok(T.last().url === '/sm/qubits', 'O1 htmx loaded BEFORE sm-root.js: htmx.ajax("/qubits") -> ' + T.last().url);
    T.w.document.getElementById('b-replace').addEventListener('htmx:configRequest', function (ev) { ev.detail.path = '/diff?base=1'; });
    await T.click('b-replace');
    ok(T.last().url === '/sm/diff?base=1', 'O1 ...and a replacing element listener is followed by the prefix: ' + T.last().url);
  }
  {
    const T = await world({ root: '/sm', order: 'htmx-late' });
    ok(typeof T.w.htmx === 'object' && T.w.document.readyState === 'complete', 'O2 htmx evaluated after the page finished loading');
    await T.w.htmx.ajax('GET', '/qubits', { target: '#t' });
    await tick(10);
    ok(T.last().url === '/sm/qubits', 'O2 ...its FIRST request is already prefixed: ' + T.last().url);
    await T.w.htmx.ajax('GET', '/pairs', { target: '#t' });
    await tick(10);
    ok(T.last().url === '/sm/pairs', 'O2 ...and so is the next: ' + T.last().url);
  }

  // ── Z: root is untouched, even with the attribute present ─────────────
  for (const ext of [false, true]) {
    const T = await world({ root: '', ext: ext, at: '/start' });
    await T.w.htmx.ajax('GET', '/qubits', { target: '#t' });
    await tick(10);
    const tag = ext ? 'hx-ext="sm-root" present anyway' : 'no hx-ext';
    ok(T.last().url === '/qubits', 'Z1 root (' + tag + '): htmx.ajax("/qubits") opens ' + T.last().url);
    await T.click('b-bare');
    await tick(30);
    ok(T.last().url === '/pairs' && T.loc() === '/pairs', 'Z2 root (' + tag + '): hx-get="/pairs" hx-push-url -> requested and pushed as ' + T.loc());
  }

  console.log(passes + ' passed, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
