/* jsdom selfcheck under the REAL bundled htmx: a CZ gate deleted from the status line of its
 * own pulse detail (the Edit values view) leaves an honest detail.
 *   D  the delete answer replaces the pulse detail: it says the gate was deleted, and no field,
 *      no Edit values / Make active / Delete of the gone gate is left on screen
 *   U  an undo that does not bring the gate back draws nothing; the undo of the delete (its real
 *      cellsReverted detail) asks for the gate's pulse detail once and the detail is back
 * Run: node tests/gate_single_delete_selfcheck.cjs <fixture.json>
 *      (driven by tests/test_gate_status_routes.py, which renders the detail and the answers)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const HTMX = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
const GATE = fs.readFileSync(path.join(STATIC, 'gate-status.js'), 'utf8');
const FIX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

let n = 0;
function ok(cond, msg) {
  if (!cond) { console.error('FAIL - ' + msg); process.exit(1); }
  n++; console.log('ok - ' + msg);
}

const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

// the mock server, installed in the page's realm: every request waits until the test answers it
const MOCK = `(function () {
  window.__reqs = [];
  function Target() { this._l = {}; }
  Target.prototype.addEventListener = function (t, f) { (this._l[t] = this._l[t] || []).push(f); };
  Target.prototype._fire = function (t) { (this._l[t] || []).forEach(function (f) { f({type: t}); }); };
  function X() { Target.call(this); this.upload = new Target(); this._h = {}; this.status = 0; this.readyState = 0; }
  X.prototype = Object.create(Target.prototype);
  X.prototype.open = function (m, u) { this.method = m; this.url = u; };
  X.prototype.overrideMimeType = function () {};
  X.prototype.setRequestHeader = function () {};
  X.prototype.send = function (b) { this.body = b; window.__reqs.push(this); };
  // as in a browser, abort() on an answered request does nothing
  X.prototype.abort = function () {
    if (this.readyState === 4) return; if (this.onabort) this.onabort(); this._fire('loadend'); };
  X.prototype.getAllResponseHeaders = function () {
    var h = this._h; return Object.keys(h).map(function (k) { return k + ': ' + h[k]; }).join('\\r\\n'); };
  X.prototype.getResponseHeader = function (k) {
    for (var key in this._h) if (key.toLowerCase() === k.toLowerCase()) return this._h[key]; return null; };
  X.prototype.respond = function (body, headers, status) {
    this._h = headers || {}; this.status = status || 200; this.readyState = 4;
    this.response = this.responseText = body; this.responseURL = 'http://localhost' + this.url;
    this.onload(); this._fire('load'); this._fire('loadend');
  };
  window.XMLHttpRequest = X;
})();`;

// the headers htmx acts on, without the answer's HX-Trigger (its refreshes are not part of this check)
function hx(headers) {
  const out = {};
  for (const k of Object.keys(headers || {})) {
    if (/^HX-(Retarget|Reswap|Trigger-After-Swap)$/i.test(k)) out[k] = headers[k];
  }
  return out;
}

const PAIR = FIX.pair, GNAME = FIX.gate;
const w = (function () {
  const html = '<!doctype html><html><head></head><body>'
    + '<div id="inspector-pane" hx-sync="this:replace">' + FIX.detail.body + '</div></body></html>';
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
  const win = dom.window;
  win.eval(XPATH_SHIM);
  win.eval(MOCK);
  win.eval(HTMX);
  win.eval(GATE);
  win.eval('htmx.config.defaultSettleDelay = 0; htmx.process(document.body);');
  return win;
})();

async function until(cond, what) {
  for (let i = 0; i < 2000; i++) {
    if (cond()) return;
    await new Promise((r) => w.setTimeout(r, 1));
  }
  ok(false, 'timed out waiting for ' + what);
}
async function drain() { for (let i = 0; i < 20; i++) await new Promise((r) => w.setTimeout(r, 0)); }

const reqs = () => w.eval('__reqs');
async function answerNext(match, a, what) {
  let req = null;
  await until(() => { req = reqs().find((r) => match(r.url) && !r.__done); return !!req; }, what);
  req.__done = true;
  req.respond(a.body, hx(a.headers), 200);
  await drain();
}
const pane = () => w.document.getElementById('inspector-pane');
const row = () => pane().querySelector('.gate-status[data-gate="' + GNAME + '"]');
const fields = () => pane().querySelectorAll('input, select, textarea').length;
const detailGets = () => reqs().filter((r) => r.url === FIX.url).length;

(async function main() {
  ok(!!pane().querySelector('#pulse-detail-root'), 'the pulse detail of the gate is open');
  ok(!!row() && !!row().querySelector('.gate-status-delete'), 'its status line offers Delete');
  ok(fields() > 0, 'the detail has fields to edit (' + fields() + ')');

  // D: the delete from the row
  row().querySelector('.gate-status-delete').click();
  await answerNext((u) => /delete-offer/.test(u), FIX.offer, 'the delete offer');
  await until(() => !!w.document.querySelector('.gate-delete-confirm'), 'the confirm button');
  w.document.querySelector('.gate-delete-confirm').click();
  await answerNext((u) => /delete-together/.test(u), FIX.deleted, 'the delete press');
  const text = pane().textContent;
  ok(text.includes(GNAME + ' deleted') && text.includes('Ctrl+Z brings them back'),
     'D: the detail says the gate was deleted and how to bring it back');
  ok(!pane().querySelector('#pulse-detail-root'), 'D: the pulse detail of the gone gate is not shown');
  ok(!row(), 'D: no status line of the gone gate is left');
  ok(!pane().querySelector('.gate-status-edit, .gate-status-activate, .gate-status-delete'),
     'D: nothing offers Edit values, Make active or Delete of the gone gate');
  ok(fields() === 0, 'D: no field of the gone gate is left to edit');

  // U: an undo that does not bring the gate back draws nothing; the undo of the delete does
  const fire = (detail) => w.document.dispatchEvent(
    new w.CustomEvent('cellsReverted', { detail: detail }));
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.' + GNAME + '.phase_shift_control',
                     created: true, deleted: false }] });
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.' + GNAME, created: false, deleted: false }] });
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.other_gate', created: false, deleted: true }] });
  await drain();
  ok(detailGets() === 0, 'U: an undo that does not bring the gate back draws nothing');
  fire(FIX.undo);
  await answerNext((u) => u === FIX.url, FIX.again, 'the pulse detail again');
  ok(detailGets() === 1, 'U: the undo of the delete asks for the pulse detail once');
  ok(!!pane().querySelector('#pulse-detail-root') && !!row(), 'U: the pulse detail and its status line are back');
  ok(fields() > 0, 'U: its fields are back');

  console.log('ALL OK (' + n + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('FAIL - ' + (e && e.stack || e)); process.exit(1); });
