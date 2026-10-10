/* v1.3.0 stability sweep -- closing the inspector must not leave htmx's
 * trigger listeners behind.
 *
 * window.closeInspector blanks #inspector-pane with innerHTML, which bypasses
 * htmx's own cleanup. A "... from:body" trigger inside the pane (the qubit
 * inspector's Generated Config section) kept its listener on <body>, and the
 * listener's closure kept the whole closed pane alive: ~15,000 DOM nodes and
 * ~1,250 listeners per close, measured in Chrome over CDP.
 *
 * Pinned under the REAL bundled htmx:
 *   P  preflight: htmx registers the from:body listener (else this is vacuous)
 *   C  control: a bare innerHTML blank removes nothing from <body>
 *   R  closeInspector (shipped app.js source) removes it before blanking
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
  console.log('SKIP: jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const HTMX_SRC = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'htmx.min.js'), 'utf8');
const APP = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}

// the shipped function expressions, cut out of app.js by brace matching
function fnSource(marker) {
  const at = APP.indexOf(marker);
  if (at < 0) return null;
  const open = APP.indexOf('{', at);
  let depth = 0;
  for (let i = open; i < APP.length; i++) {
    const c = APP[i];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return APP.slice(at, i + 1) + ';'; }
  }
  return null;
}
const RELEASE = fnSource('window.releaseHtmxWithin = function');
const CLOSE = fnSource('window.closeInspector = function');
check('S1 app.js defines window.releaseHtmxWithin', !!RELEASE);
check('S2 app.js defines window.closeInspector', !!CLOSE);

// jsdom cannot run htmx's XPath hx-on scan; the fixture has no hx-on (see
// settle_config_selfcheck.cjs for the same accommodation)
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

function boot() {
  const html = '<!doctype html><html><head>' +
    '<script>' + XPATH_SHIM + '</scr' + 'ipt>' +
    '<script>' + HTMX_SRC + '</scr' + 'ipt>' +
    '</head><body><div id="inspector-pane"><article>' +
    '<details class="detail-section"><summary>Generated Config</summary>' +
    '<div id="cfg" hx-get="/qubit/q1/config" ' +
    'hx-trigger="toggle once from:closest details, configRegenerated from:body" ' +
    'hx-swap="innerHTML"><p>loading</p></div></details>' +
    '<button class="inspector-close">x</button>' +
    '</article></div></body></html>';
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/' });
  const w = dom.window;
  const ready = new Promise((resolve) => {
    if (w.document.readyState !== 'loading') w.setTimeout(resolve, 0);
    else w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(resolve, 0));
  });
  return { w, ready };
}

function spyBody(w) {
  const removed = [];
  const body = w.document.body;
  const orig = body.removeEventListener.bind(body);
  body.removeEventListener = function (type, fn, opts) { removed.push(type); return orig(type, fn, opts); };
  return removed;
}

(async function main() {
  // control: the old blank leaves the body listener in place
  {
    const { w, ready } = boot();
    await ready;
    const cfg = w.document.getElementById('cfg');
    const infos = (cfg['htmx-internal-data'] || {}).listenerInfos || [];
    const onBody = infos.filter((li) => li.on === w.document.body && li.trigger === 'configRegenerated');
    check('P1 bundled htmx registers the from:body trigger listener on <body>', onBody.length === 1,
          JSON.stringify(infos.map((li) => li.trigger)));
    const removed = spyBody(w);
    w.document.getElementById('inspector-pane').innerHTML = '';
    check('C1 CONTROL: a bare innerHTML blank removes nothing from <body>', removed.length === 0,
          JSON.stringify(removed));
  }
  // the shipped closeInspector
  {
    const { w, ready } = boot();
    await ready;
    w.eval(RELEASE);
    w.eval(CLOSE);
    const cfg = w.document.getElementById('cfg');
    const removed = spyBody(w);
    w.closeInspector();
    check('R1 closeInspector removes the from:body listener before blanking',
          removed.indexOf('configRegenerated') >= 0, JSON.stringify(removed));
    check('R2 the closed element keeps no htmx listener records',
          !((cfg['htmx-internal-data'] || {}).listenerInfos), '');
    check('R3 the pane is blank', w.document.getElementById('inspector-pane').innerHTML === '');
  }
  if (failures) { console.error(failures + ' failure(s)'); process.exit(1); }
  console.log('htmx_release_selfcheck: all ok');
})();
