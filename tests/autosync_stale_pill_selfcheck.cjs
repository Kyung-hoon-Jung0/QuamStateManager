/* SB-08: a tray answer rendered BEFORE the latest Auto-Sync change landed must
 * not paint the old session back. Under jsdom with the REAL auto-apply.js and
 * synthetic htmx lifecycle events (the guard reads only their detail).
 *
 * Run: node tests/autosync_stale_pill_selfcheck.cjs  (driven by test_final_batch_v130.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'auto-apply.js');
const OFF = '<div id="pending-tray" data-change-count="0"><span class="auto-sync-wrap">'
  + '<button class="auto-apply-pill auto-apply-off"></button></span></div>';
const ON_TXT = '<div id="pending-tray" data-auto-apply="1"><span class="auto-sync-wrap">'
  + '<button type="button" class="auto-apply-pill auto-apply-on"></button></span></div>';

const dom = new JSDOM('<!doctype html><html><body>' + OFF + '</body></html>',
  { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
const calls = [];
w.htmx = { ajax: function (m, u) { calls.push(m + ' ' + u); return Promise.resolve(); } };
w.eval(fs.readFileSync(SRC, 'utf8'));
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('  ok  ' + m); else { fails++; console.error('FAIL  ' + m); } }
function fire(type, detail) {
  w.document.body.dispatchEvent(new w.CustomEvent(type, { bubbles: true, detail: detail }));
  return detail;
}
const tray = () => w.document.getElementById('pending-tray');

// a tray GET issued while Auto-Sync was still on ...
const stale = { xhr: { responseText: ON_TXT } };
fire('htmx:beforeRequest', stale);
// ... then "Turn off" is pressed, lands, and its OFF tray is on screen
const off = { xhr: {}, successful: true, pathInfo: { requestPath: '/auto-apply/disarm' } };
fire('htmx:beforeRequest', off);
fire('htmx:afterRequest', off);
// the stale answer arrives last
const sw = fire('htmx:beforeSwap', { xhr: stale.xhr, target: tray(), serverResponse: ON_TXT, shouldSwap: true });
ok(sw.shouldSwap === false, 'a tray answer issued before Turn off does not paint the pill ON again');
ok(calls.indexOf('GET /state/tray') >= 0, 'the tray is re-read instead (server truth)');

// a NEW request (after the session change) swaps normally, whatever it says
const fresh = { xhr: { responseText: ON_TXT } };
fire('htmx:beforeRequest', fresh);
const sw2 = fire('htmx:beforeSwap', { xhr: fresh.xhr, target: tray(), serverResponse: ON_TXT, shouldSwap: true });
ok(sw2.shouldSwap === true, 'an answer issued after the change swaps as usual');

// a stale answer that agrees with the screen is left alone (no extra read)
calls.length = 0;
const agree = { xhr: { responseText: OFF } };
w.document.body.dispatchEvent(new w.CustomEvent('htmx:beforeRequest', { bubbles: true, detail: agree }));
agree.xhr._smSeq = 1;   // older than the disarm
const sw3 = fire('htmx:beforeSwap', { xhr: agree.xhr, target: tray(), serverResponse: OFF, shouldSwap: true });
ok(sw3.shouldSwap === true && calls.length === 0, 'an older answer that agrees with the pill is untouched');

// SB-07: an auto flush while the sync panel is open re-reads the panel once it landed
(async function () {
  let refreshed = 0;
  w.SyncPanel = { isOpen: function () { return true; }, refresh: function () { refreshed++; } };
  calls.length = 0;
  tray().outerHTML = '<div id="pending-tray" data-auto-apply="1" data-change-count="1" data-working-dirty="0"></div>';
  await new Promise(function (r) { setTimeout(r, 50); });
  ok(calls.indexOf('POST /state/apply-to-live') >= 0, 'precondition: the armed session flushed the staged inverse');
  ok(refreshed === 1, 'the open sync panel is re-read after the flush (got ' + refreshed + ')');
  if (fails) { console.error(fails + ' of ' + n + ' checks failed'); process.exit(1); }
  console.log('all checks passed (' + n + ')');
})();
