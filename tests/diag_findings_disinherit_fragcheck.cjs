/* QA diagnostics-r2-01 -- a link inside the self-refreshing #diag-findings
 * slot must navigate like a sidebar link, not inherit the slot's own
 * hx-select/hx-swap.
 *
 * #diag-findings refreshes itself with hx-select="#diag-findings"
 * hx-swap="outerHTML" (docs/141 4f). Both are INHERITED htmx attributes, so
 * the "Config Viewer" / "Regenerate in the Config Viewer" links inside it
 * (hx-get /config, hx-target #table-pane) selected #diag-findings out of the
 * /config response -- nothing -- and outerHTML-swapped #table-pane itself
 * away. The pane was gone, the page blank, and every later sidebar click
 * raised htmx:targetError until Back or F5.
 *
 * This drives the REAL bundled htmx against a real local HTTP server and the
 * REAL server-rendered /diagnostics fragment (written by the pytest driver,
 * argv[2]). A CONTROL run strips the slot's hx-disinherit and must reproduce
 * the loss, so the fixed-side assertions cannot pass vacuously.
 *
 * Named *_fragcheck, not *_selfcheck: `npm run selfcheck` runs every
 * *_selfcheck.cjs with no arguments, and this one needs the fragment its
 * driver (tests/test_diagnostics_refresh.py) writes.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) {
  console.log('SKIP: jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const HTMX_SRC = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'htmx.min.js'), 'utf8');
const BASE = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const FRAG_PATH = process.argv[2];
if (!FRAG_PATH) { console.error('FAIL  usage: node <this> <diagnostics-fragment.html>'); process.exit(1); }
const FRAG = fs.readFileSync(FRAG_PATH, 'utf8');

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}

// Same accommodation as settle_config_selfcheck.cjs: jsdom's XPath cannot run
// htmx's hx-on: scan; nothing here uses hx-on.
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';
const META = (BASE.match(/<meta\s+name="htmx-config"[^>]*>/) || [''])[0];

const server = http.createServer(function (req, res) {
  const u = req.url.split('?')[0];
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  if (u === '/config') res.end('<h2 id="cfg-view">Config Viewer</h2>');
  else if (u === '/bulk') res.end('<div id="bulk-ok">Live State Edit</div>');
  else { res.statusCode = 404; res.end('nope'); }
});

function until(w, pred, ms) {
  return new Promise(function (resolve) {
    const t0 = Date.now();
    (function poll() {
      let v = false;
      try { v = pred(); } catch (e) { v = false; }
      if (v || Date.now() - t0 > ms) resolve(!!v);
      else w.setTimeout(poll, 15);
    })();
  });
}

async function run(port, frag) {
  const html = '<!doctype html><html><head>' +
    '<script>' + XPATH_SHIM + '</scr' + 'ipt>' + META +
    '<script>' + HTMX_SRC + '</scr' + 'ipt>' +
    '</head><body><nav class="sidebar-nav">' +
    '<a id="side-bulk" href="/bulk" hx-get="/bulk" hx-target="#table-pane" hx-push-url="true">Live State Edit</a>' +
    '</nav><main id="main"><div id="table-pane">' + frag + '</div></main></body></html>';
  const vc = new VirtualConsole();   // the fragment's inline handlers name app.js globals
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:' + port + '/diagnostics',
                                virtualConsole: vc });
  const w = dom.window;
  await new Promise(function (resolve) {
    if (w.document.readyState !== 'loading') resolve();
    else w.document.addEventListener('DOMContentLoaded', function () { w.setTimeout(resolve, 0); });
  });
  const targetErrors = [];
  w.document.body.addEventListener('htmx:targetError', function () { targetErrors.push(1); });
  const link = w.document.querySelector('#diag-findings a[href="/config"]');
  const out = { link: !!link, targetErrors: targetErrors };
  if (!link) return out;
  link.click();
  await until(w, function () {
    return w.document.getElementById('cfg-view') || !w.document.getElementById('table-pane'); }, 3000);
  await new Promise(function (r) { w.setTimeout(r, 80); });   // let settle finish
  const pane = w.document.getElementById('table-pane');
  out.paneAfterLink = !!pane;
  out.cfgInPane = !!(pane && pane.querySelector('#cfg-view'));
  out.url = w.location.pathname;
  w.document.getElementById('side-bulk').click();
  await until(w, function () { return w.document.getElementById('bulk-ok') || targetErrors.length; }, 3000);
  out.bulkShown = !!w.document.getElementById('bulk-ok');
  w.close();
  return out;
}

server.listen(0, '127.0.0.1', async function () {
  const port = server.address().port;
  try {
    check('P0 the fragment carries the slot and a Config Viewer link inside it',
          /id="diag-findings"/.test(FRAG) && /hx-get="\/config"/.test(FRAG));
    // CONTROL: the slot without its hx-disinherit reproduces the reported loss.
    const stripped = FRAG.replace(/\s+hx-disinherit="[^"]*"/, '');
    check('P1 control preflight: stripping hx-disinherit changed the fragment', stripped !== FRAG);
    const c = await run(port, stripped);
    check('C1 CONTROL: without hx-disinherit the link deletes #table-pane (the bug is real)',
          c.link && c.paneAfterLink === false, JSON.stringify(c));
    check('C2 CONTROL: ...and the next sidebar click raises htmx:targetError',
          c.targetErrors.length > 0 && !c.bulkShown, JSON.stringify(c));
    // FIXED: the shipped fragment.
    const f = await run(port, FRAG);
    check('F1 the Config Viewer link keeps #table-pane', f.link && f.paneAfterLink === true, JSON.stringify(f));
    check('F2 ...and shows the /config response inside it', f.cfgInPane === true, JSON.stringify(f));
    check('F3 ...and pushes /config like the sidebar link', f.url === '/config', JSON.stringify(f));
    check('F4 the sidebar still navigates afterwards (no htmx:targetError)',
          f.bulkShown === true && f.targetErrors.length === 0, JSON.stringify(f));
  } catch (e) {
    failures++; console.error('FAIL  selfcheck threw: ' + (e && e.stack || e));
  }
  // exitCode, not process.exit(): exiting while libuv is still closing the
  // server's sockets trips a Windows async.c assertion (exit 0xC0000409).
  if (failures) { console.error(failures + ' check(s) failed'); process.exitCode = 1; }
  else { console.log('all checks passed'); process.exitCode = 0; }
  if (server.closeAllConnections) server.closeAllConnections();
  server.close();
});
