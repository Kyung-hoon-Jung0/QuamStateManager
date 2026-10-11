/* SB-07: the applied log's x inside the sync panel. The revert targets
 * #pending-tray, so the panel row (outside it) kept offering its x. After a
 * successful revert the row says "reverted" at once and the panel is re-read.
 * Real app.js under jsdom; the htmx:afterRequest event is synthetic.
 *
 * Run: node tests/applied_log_revert_selfcheck.cjs  (driven by test_final_batch_v130.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const ROW = '<div class="applied-log-row" data-unit-id="u1"><div class="applied-log-vals"></div>'
  + '<button type="button" class="outline btn-sm btn-al-revert" hx-post="/auto-apply/revert" '
  + 'data-after-request="appliedLogReverted">x</button></div>';
const dom = new JSDOM('<!doctype html><html><body><div id="table-pane"></div><div id="inspector-pane"></div>'
  + '<div id="status-bar"></div><div id="pending-tray" data-change-count="0"></div>'
  + '<div id="state-review-host"><div class="sync-panel">' + ROW + ROW.replace('u1', 'u2') + '</div></div>'
  + '</body></html>', { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.CSS = window.CSS; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
try { Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true }); } catch (e) { /* node >= 21 */ }
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: function () { return Promise.resolve(); }, process: function () {}, trigger: function () {} };
window.fetch = function () { return Promise.resolve({ ok: true, text: () => Promise.resolve(''), json: () => Promise.resolve({}) }); };
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('ok - ' + m); else { fails++; console.error('FAIL: ' + m); } }
let refreshed = 0;
window.SyncPanel = { refresh: function () { refreshed++; return Promise.resolve(); } };
const btn = (id) => window.document.querySelector('.applied-log-row[data-unit-id="' + id + '"] .btn-al-revert');

// a refused revert (409) changes nothing
let b2 = btn('u2');
b2.dispatchEvent(new window.CustomEvent('htmx:afterRequest', { bubbles: true, detail: { successful: false } }));
ok(btn('u2') && refreshed === 0, 'a refused revert keeps the x and does not re-read the panel');

(async function () {
// a landed revert while the armed Auto-Sync flush is still writing it
window._applyInFlight = true;
const b1 = btn('u1');
b1.dispatchEvent(new window.CustomEvent('htmx:afterRequest', { bubbles: true, detail: { successful: true } }));
const row = window.document.querySelector('.applied-log-row[data-unit-id="u1"]');
ok(!btn('u1'), 'the reverted row no longer offers its x');
ok(row.classList.contains('al-reverted') && /reverted/.test(row.textContent), 'the row says reverted');
await new Promise(function (r) { setTimeout(r, 250); });
ok(refreshed === 0, 'the panel waits while the flush is writing (it would show the revert as unapplied)');
window._applyInFlight = false;
await new Promise(function (r) { setTimeout(r, 250); });
ok(refreshed === 1, 'the sync panel is re-read from the server once the write landed');

if (fails) { console.error(fails + ' of ' + n + ' checks failed'); process.exit(1); }
console.log('all checks passed (' + n + ')');
process.exit(0);
})();
