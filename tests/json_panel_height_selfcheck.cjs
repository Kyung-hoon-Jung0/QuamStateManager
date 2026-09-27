/* smallui re-verify P3 (2026-09-27) -- the #json-panel height restore in a
 * private window.
 *
 * app.js remembers the drag-resized JSON panel height in localStorage
 * (`quam_json_panel_h`) and re-applies it on DOMContentLoaded and after every
 * htmx swap. A private window throws on storage ACCESS; without the try/catch
 * around the read, every swap on every page with a panel would raise inside
 * the listener. This drives the REAL app.js under jsdom:
 *
 *   1. a stored height is applied at load and again to a swapped-in panel
 *      (so the no-error checks below cannot pass vacuously -- the listeners
 *      demonstrably run);
 *   2. a private window (localStorage getter throws) raises NOTHING from
 *      either listener, and the panel keeps its CSS height.
 *
 * Mutation: remove the try/catch in _applyPersistedHeight -> check 2 goes red
 * (jsdom reports the uncaught listener error on the virtual console).
 *
 * Run: node tests/json_panel_height_selfcheck.cjs  (driven by tests/test_json_panel_height.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
// JSON_PANEL_APP_JS: an alternate app.js (the mutation sweep runs a copy with
// the try/catch removed and expects this file to go red)
const APP_JS = fs.readFileSync(process.env.JSON_PANEL_APP_JS
  || path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

ok(/quam_json_panel_h/.test(APP_JS), 'setup: app.js carries the json-panel height key');

/* storage: undefined = key absent; a string = the stored height; 'throw' = private window */
function world(storage) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', function (e) { errors.push(String(e && (e.detail || e.message || e))); });
  const html = '<!doctype html><html><head></head><body class="app-shell">'
    + '<div id="main"><div id="json-panel" class="json-panel" style="height: 240px">'
    + '<div class="json-panel-resizer"></div><pre>{}</pre></div></div></body></html>';
  const dom = new JSDOM(html, { url: 'http://localhost/qubits', runScripts: 'outside-only',
                               pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  if (storage === 'throw') {
    Object.defineProperty(w, 'localStorage', { get: function () { throw new Error('SecurityError'); } });
  } else if (storage !== undefined) {
    w.localStorage.setItem('quam_json_panel_h', storage);
  }
  w.addEventListener('error', function (e) { errors.push('window.onerror: ' + (e && e.message)); });
  w.htmx = { ajax: function () { return w.Promise.resolve(); }, process: function () {}, on: function () {},
             config: {}, find: function () { return null; } };
  w.Element.prototype.scrollIntoView = function () {};
  w.fetch = function () { return new w.Promise(function () {}); };
  try { new w.Function(APP_JS).call(w); } catch (e) { console.error('app.js threw: ' + e.stack); fails++; }
  const d = w.document;
  return {
    w: w, d: d, errors: errors,
    panel: () => d.getElementById('json-panel'),
    load: () => d.dispatchEvent(new w.Event('DOMContentLoaded', { bubbles: true })),
    swapIn: () => {
      // an htmx swap that brings a fresh panel copy (every page renders its own)
      const host = d.createElement('div');
      host.innerHTML = '<div id="json-panel-2" class="json-panel" style="height: 240px"><pre>{}</pre></div>';
      d.getElementById('main').appendChild(host);
      const ev = new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: host } });
      host.dispatchEvent(ev);
      return d.getElementById('json-panel-2');
    },
  };
}

// 1. a remembered size is applied at load and to a swapped-in panel
{
  const T = world('333');
  T.load();
  ok(T.panel().style.height === '333px', 'stored 333 -> the panel is 333px after DOMContentLoaded (' + T.panel().style.height + ')');
  const p2 = T.swapIn();
  ok(p2.style.height === '333px', 'stored 333 -> a swapped-in panel is 333px too (' + p2.style.height + ')');
  ok(T.errors.length === 0, 'no listener error with a working storage (' + T.errors.join(' | ') + ')');
}
// 2. an absent key leaves the CSS height alone
{
  const T = world(undefined);
  T.load();
  ok(T.panel().style.height === '240px', 'no key -> the panel keeps its CSS height (' + T.panel().style.height + ')');
  ok(T.errors.length === 0, 'no listener error with an absent key');
}
// 3. a private window: storage access throws; neither listener raises
{
  const T = world('throw');
  T.load();
  const p2 = T.swapIn();
  ok(T.errors.length === 0,
     'private window: DOMContentLoaded + htmx:afterSwap raise nothing (' + T.errors.join(' | ') + ')');
  ok(T.panel().style.height === '240px' && p2.style.height === '240px',
     'private window: both panels keep their CSS height (' + T.panel().style.height + ', ' + p2.style.height + ')');
}

console.log(fails ? ('FAILED ' + fails) : ('json_panel_height_selfcheck: all ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
