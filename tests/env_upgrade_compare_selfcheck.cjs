/* jsdom selfcheck against the REAL env-upgrade.js and the REAL htmx:
 *
 *  1. EnvUpgrade.compare replaces #env-upgrade-report with the server's card and
 *     never makes an htmx swap whose detail lacks a target -- a shared
 *     htmx:afterSwap listener that reads evt.detail.target.id must not throw.
 *  2. The new card is processed by htmx (its hx-* buttons work after the swap).
 *  3. A refused compare (400 JSON) shows the server's reason in the status line.
 *  4. Check this env disables its button and asks the card to poll.
 *
 * Run: node tests/env_upgrade_compare_selfcheck.cjs
 *      (driven by tests/test_env_upgrade_card_ux.py::test_env_upgrade_compare_selfcheck)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 20); }); }

const card = '<section id="env-upgrade-report"><p id="env-upgrade-status" role="status"></p>' +
  '<div id="env-upgrade-references"><div class="env-reference">' +
  '<input class="env-reference-filter" value=""><select class="env-reference-python"><option value="py">env</option></select>' +
  '<button id="cmp" data-key="k">Compare</button></div></div></section>';
const dom = new JSDOM('<!doctype html><html><body><div id="diag-env-card">' + card + '</div></body></html>',
  { url: 'http://localhost/diagnostics', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'env-upgrade.js'), 'utf8'));

const thrown = [];
// the same shape as base.html's shared listener before its guard
window.document.body.addEventListener('htmx:afterSwap', function (evt) {
  try { void evt.detail.target.id; } catch (e) { thrown.push(String(e)); }
});
let swaps = 0;
const realSwap = window.htmx.swap;
window.htmx.swap = function () { swaps++; return realSwap.apply(this, arguments); };
const processed = [];
const realProcess = window.htmx.process;
window.htmx.process = function (el) { processed.push(el); return realProcess.apply(this, arguments); };
const ajax = [];
window.htmx.ajax = function () { ajax.push(Array.from(arguments)); return Promise.resolve(); };

let answer = null;
window.fetch = function (url, opts) {
  return Promise.resolve(answer(url, opts));
};

(async function () {
  answer = function () {
    return { ok: true, status: 200, text: function () {
      return Promise.resolve('<section id="env-upgrade-report"><p class="fresh">All 3 waveforms are bit-identical to the reference</p>' +
        '<button hx-get="/env-upgrade/references" hx-target="#x">References</button>' +
        '<p id="env-upgrade-status"></p></section><script src="/static/env-upgrade.js"></script>'); } };
  };
  await window.EnvUpgrade.compare(window.document.getElementById('cmp'));
  await flush();
  ok(window.document.querySelector('#env-upgrade-report .fresh'), 'compare result replaced the report');
  ok(window.document.querySelectorAll('#env-upgrade-report').length === 1, 'exactly one report section');
  ok(swaps === 0, 'no htmx.swap call without a target');
  ok(thrown.length === 0, 'no listener threw: ' + thrown.join(' | '));
  ok(processed.some(function (el) { return el.id === 'env-upgrade-report'; }), 'new card handed to htmx.process');

  window.document.getElementById('env-upgrade-report').insertAdjacentHTML('beforeend',
    '<div class="env-reference"><input class="env-reference-filter" value=""><select class="env-reference-python">' +
    '<option value="py">env</option></select><button id="cmp2" data-key="k">Compare</button></div>');
  answer = function () {
    return { ok: false, status: 400, json: function () { return Promise.resolve({ ok: false, error: 'choose an environment SM discovered' }); } };
  };
  await window.EnvUpgrade.compare(window.document.getElementById('cmp2'));
  await flush();
  ok(/choose an environment SM discovered/.test(window.document.getElementById('env-upgrade-status').textContent),
     'a refused compare shows the server reason');

  window.document.getElementById('env-upgrade-report').insertAdjacentHTML('beforeend',
    '<button id="env-upgrade-check">Check this env</button>');
  answer = function () { return { ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, checking: true }); } }; };
  await window.EnvUpgrade.check();
  await flush();
  ok(window.document.getElementById('env-upgrade-check').disabled, 'Check this env disables itself');
  ok(ajax.some(function (a) { return /diagnostics\/env-card\?poll=1/.test(a[1]); }), 'card asked to poll');
  ok(/Checking/.test(window.document.getElementById('env-upgrade-status').textContent), 'status says Checking');

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('all ok');
})().catch(function (e) { console.error(e); process.exit(1); });
