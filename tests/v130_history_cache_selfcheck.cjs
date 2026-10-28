/* v1.3.0 A5: no htmx:historyCacheError -- htmx keeps no localStorage page snapshots.
 *
 * htmx 2.0.4 snapshots the whole <body> into localStorage before every
 * pushed navigation. Live State Edit's body is ~5.6 M characters on a
 * 30-qubit chip: a same-page navigation (the sidebar's own entry, a refresh
 * through hx-push-url) tried to store it, failed the quota, and retried while
 * dropping every other cached page -- 85-138 htmx:historyCacheError per session,
 * a multi-MB JSON.stringify per try, and an emptied cache for everything else.
 * base.html's htmx-config sets historyCacheSize 0: htmx stores nothing, and
 * Back/Forward ask the server (HX-History-Restore-Request), which every route
 * already answers with the full page (routes._is_htmx). That also means Back
 * never shows a stale snapshot of a value that changed since.
 *
 * Pinned here under the REAL bundled htmx with a 6 M character body:
 *   CONTROL (default config): the push writes the snapshot and fails with
 *     htmx:historyCacheError -- the mechanism is real in this htmx;
 *   SHIPPED (base.html's meta): no error, no cache key, and Back restores the
 *     page from the server with the history-restore header.
 *
 * Run: node tests/v130_history_cache_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
  console.log('SKIP: jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const BASE = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const HTMX_SRC = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'htmx.min.js'), 'utf8');
const META = (BASE.match(/<meta\s+name="htmx-config"[^>]*>/) || [''])[0];
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

let failures = 0, n = 0;
function check(name, cond, detail) {
  n++;
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}

const BIG = 'x'.repeat(6000000);
const restores = [];
const server = http.createServer(function (req, res) {
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  if (req.headers['hx-history-restore-request'] === 'true') {
    restores.push(req.url);
    res.end('<body><div id="pane"><p id="restored">page A, from the server</p></div></body>');
    return;
  }
  res.end('<p id="page-b">page B</p>');
});

function boot(port, meta) {
  const html = '<!doctype html><html><head><script>' + XPATH_SHIM + '</scr' + 'ipt>' + meta +
    '<script>' + HTMX_SRC + '</scr' + 'ipt></head><body>' +
    '<div id="pane"><div id="big">' + BIG + '</div>' +
    '<a id="go" hx-get="/b" hx-target="#pane" hx-push-url="true">B</a></div></body></html>';
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:' + port + '/a',
                                pretendToBeVisual: true });
  const w = dom.window;
  const errors = [];
  w.document.addEventListener('htmx:historyCacheError', function () { errors.push(1); });
  w.console.error = function () {};          // htmx logs each error; counted above
  const ready = new Promise((resolve) => {
    if (w.document.readyState !== 'loading') resolve();
    else w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(resolve, 0));
  });
  return { w, errors, ready };
}

function until(w, fn, ms) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    (function poll() {
      if (fn() || Date.now() - t0 > ms) return resolve(fn());
      w.setTimeout(poll, 20);
    })();
  });
}

server.listen(0, '127.0.0.1', async function () {
  const port = server.address().port;
  try {
    check('P0 base.html carries the htmx-config meta', !!META, META);
    let cfg = null;
    try { cfg = JSON.parse((META.match(/content='([^']+)'/) || [])[1]); } catch (e) { /* P1 fails */ }
    check('P1 the meta sets historyCacheSize 0', !!cfg && cfg.historyCacheSize === 0, JSON.stringify(cfg));

    // CONTROL: the default config stores snapshots, and a 6 M body fails the quota
    {
      const { w, errors, ready } = boot(port, '');
      await ready;
      check('C0 control preflight: default historyCacheSize > 0', w.htmx.config.historyCacheSize > 0,
            String(w.htmx.config.historyCacheSize));
      w.document.getElementById('go').click();
      await until(w, () => w.document.getElementById('page-b'), 5000);
      check('C1 CONTROL: the push tried to store the big page and failed (historyCacheError)',
            errors.length > 0, 'errors=' + errors.length);
      w.close();
    }
    // SHIPPED: no snapshot, no error; Back asks the server
    {
      const { w, errors, ready } = boot(port, META);
      await ready;
      check('S0 the shipped meta reaches htmx.config', w.htmx.config.historyCacheSize === 0,
            String(w.htmx.config.historyCacheSize));
      w.document.getElementById('go').click();
      const b = await until(w, () => w.document.getElementById('page-b'), 5000);
      check('S1 the navigation itself works', !!b && w.location.pathname === '/b', w.location.pathname);
      check('S2 no htmx:historyCacheError', errors.length === 0, 'errors=' + errors.length);
      check('S3 nothing is written to localStorage',
            w.localStorage.getItem('htmx-history-cache') === null);
      restores.length = 0;
      w.history.back();
      const back = await until(w, () => w.document.getElementById('restored'), 5000);
      check('S4 Back restores the page from the server (history-restore request)',
            !!back && restores.length === 1 && restores[0] === '/a',
            'restores=' + JSON.stringify(restores) + ' path=' + w.location.pathname);
      w.close();
    }
  } catch (e) {
    failures++;
    console.error('FAIL  selfcheck threw: ' + (e && e.stack || e));
  }
  // Let the event loop drain instead of process.exit(): exiting while a handle is
  // still closing trips a libuv assertion on Windows.
  const code = failures ? 1 : 0;
  if (failures) console.error(failures + ' check(s) failed');
  else console.log('v130_history_cache_selfcheck: all checks passed (' + n + ' checks)');
  process.exitCode = code;
  server.close();
  if (server.closeAllConnections) server.closeAllConnections();
});
