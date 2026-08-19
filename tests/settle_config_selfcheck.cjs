/* docs/124 §1.1 — the settle fix.
 *
 * htmx 2.0.4's attribute settle restores an id-matched element's class/style
 * to the incoming MARKUP values ~20ms after a swap, wiping anything a script
 * set during the swap window. That one mechanism stripped js-plotly-plot off
 * live Trends charts (the docs/123 §4.3 mystery), stripped .json-tree so the
 * Explorer scroll restore clamped to 0, and re-hid both Explorer trees on the
 * wiring tab (red-team findings M-7/M-13/M-15, docs/124).
 *
 * The fix is config-level: base.html's htmx-config meta drops class and style
 * from attributesToSettle. This file pins BOTH halves:
 *   - the config: the meta exists, parses, excludes class+style, and precedes
 *     the htmx <script> tag (htmx reads the meta once, at load — a meta after
 *     the script is silently ignored);
 *   - the behavior: under the REAL bundled htmx, a class added and a style
 *     shown during the swap window SURVIVE settle with our config, and the
 *     CONTROL (default config) still strips them — proving the bundled htmx
 *     still carries the mechanism and this pin is testing something real. If
 *     an htmx upgrade changes settle semantics, the control tells us which
 *     way.
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
const BASE = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const HTMX_SRC = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'htmx.min.js'), 'utf8');

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}

// ── 1. the config, as shipped in base.html ────────────────────────────────
const metaMatch = BASE.match(/<meta\s+name="htmx-config"\s+content='([^']+)'\s*>/);
check('C1 base.html carries an htmx-config meta', !!metaMatch);
let cfg = null;
try { cfg = metaMatch && JSON.parse(metaMatch[1]); } catch (e) { /* C2 fails */ }
check('C2 the meta content is valid JSON with attributesToSettle',
      !!(cfg && Array.isArray(cfg.attributesToSettle)));
if (cfg && Array.isArray(cfg.attributesToSettle)) {
  check('C3 class is NOT settled', cfg.attributesToSettle.indexOf('class') < 0,
        JSON.stringify(cfg.attributesToSettle));
  check('C4 style is NOT settled', cfg.attributesToSettle.indexOf('style') < 0,
        JSON.stringify(cfg.attributesToSettle));
}
check('C5 the meta precedes the htmx script tag (htmx reads it at load)',
      !!metaMatch && BASE.indexOf(metaMatch[0]) < BASE.indexOf('htmx.min.js'));

// ── 2. the behavior, under the REAL bundled htmx ─────────────────────────
// Markup mirrors the two shipped victims: a chart holder whose markup carries
// only its house class (Plotly adds js-plotly-plot during the swap-window
// render — _topo_trends.html), and a tree div whose markup hides it while a
// swap-window script shows it (_explorer.html wiring tab).
// htmx must load exactly as in production — as a parse-time script AFTER the
// meta, so its ready() path runs getMetaConfig and merges it (an outside-only
// eval after parse skips that path: DOMContentLoaded has already fired and the
// config merge with it). One jsdom accommodation, installed BEFORE htmx runs:
// htmx scans for hx-on: attributes through `new XPathEvaluator` (NOT
// document.createExpression), and jsdom's XPath shim cannot execute the
// compiled expression; the fixture uses no hx-on attributes, so an
// empty-iterator stand-in is faithful — and attribute settle, the thing under
// test, never touches XPath.
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

function boot(withMeta) {
  const html = '<!doctype html><html><head>' +
    '<script>' + XPATH_SHIM + '</scr' + 'ipt>' +
    (withMeta
      ? '<meta name="htmx-config" content=\'' + JSON.stringify({ attributesToSettle: ['width', 'height'] }) + '\'>'
      : '') +
    '<script>' + HTMX_SRC + '</scr' + 'ipt>' +
    '</head><body><div id="pane">' +
    '<div id="chart" class="topo-trend-chart"></div>' +
    '<div id="wtree" style="display:none"></div>' +
    '</div></body></html>';
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/' });
  const w = dom.window;
  // jsdom fires DOMContentLoaded on a task AFTER the constructor returns, and
  // htmx merges the meta config in its DOMContentLoaded callback — reading
  // htmx.config (or swapping) before that measures the pre-merge state. The
  // first version of this file did exactly that and its config assertion
  // "failed" against a fix that was working — the docs/123 §2 probe-lies class.
  const ready = new Promise((resolve) => {
    if (w.document.readyState !== 'loading') resolve();
    else w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(resolve, 0));
  });
  return { w, ready };
}

function driveSwap(w) {
  // Same-id incoming fragment → htmx's handleAttributes snapshots the markup
  // attrs and queues the settle task; then the "inline script during the swap"
  // adds the class / shows the tree, exactly like Plotly's render and
  // switchExplorerTab do in production.
  w.htmx.swap('#pane',
    '<div id="chart" class="topo-trend-chart"></div>' +
    '<div id="wtree" style="display:none"></div>',
    { swapStyle: 'innerHTML', settleDelay: 20 });
  const chart = w.document.getElementById('chart');
  const wtree = w.document.getElementById('wtree');
  chart.classList.add('js-plotly-plot');
  wtree.style.display = '';
  return { chart, wtree };
}

function settled(w) {
  return new Promise((resolve) => { w.setTimeout(resolve, 60); });
}

(async function main() {
  // Control first: default config must still strip — otherwise the bundled
  // htmx changed and the fixed-side assertions would be vacuous.
  {
    const { w, ready } = boot(false);
    await ready;
    check('B0 control preflight: default config settles class',
          w.htmx.config.attributesToSettle.indexOf('class') >= 0,
          JSON.stringify(w.htmx.config.attributesToSettle));
    const { chart, wtree } = driveSwap(w);
    check('B1 control preflight: the class IS present during the settle window',
          chart.classList.contains('js-plotly-plot'));
    await settled(w);
    check('B2 CONTROL: default config strips the swap-window class at settle',
          !chart.classList.contains('js-plotly-plot'),
          'class=' + chart.getAttribute('class'));
    check('B3 CONTROL: default config re-hides the swap-window-shown tree',
          w.getComputedStyle(wtree).display === 'none',
          'style=' + wtree.getAttribute('style'));
  }
  // Fixed side: the shipped meta config disarms both.
  {
    const { w, ready } = boot(true);
    await ready;
    check('B4 the meta config reaches htmx.config',
          w.htmx.config.attributesToSettle.indexOf('class') < 0 &&
          w.htmx.config.attributesToSettle.indexOf('style') < 0,
          JSON.stringify(w.htmx.config.attributesToSettle));
    const { chart, wtree } = driveSwap(w);
    await settled(w);
    check('B5 FIXED: a class added during the swap window SURVIVES settle',
          chart.classList.contains('js-plotly-plot'),
          'class=' + chart.getAttribute('class'));
    check('B6 FIXED: the markup class still applies (nothing over-removed)',
          chart.classList.contains('topo-trend-chart'));
    check('B7 FIXED: a tree shown during the swap window STAYS visible',
          w.getComputedStyle(wtree).display !== 'none',
          'style=' + wtree.getAttribute('style'));
  }

  if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch((e) => {
  console.error('FAIL  selfcheck threw: ' + (e && e.stack || e));
  process.exit(1);
});
