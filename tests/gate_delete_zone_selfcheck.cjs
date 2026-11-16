/* jsdom selfcheck under the REAL bundled htmx: a gate deleted from the pair page's CZ gate list
 * takes its parameter zone (data-detail-zone) off the page with the same answer.
 *   R  a refused delete leaves every zone and its fold
 *   D  a delete removes that gate's zone only: the other gate's zone keeps its fold, the
 *      sections that are not gate zones stay
 *      and the zone of the same name on another pair's page stays
 *   U  the undo of the delete (its real cellsReverted detail) draws the pair page again and
 *      the zone is back; an undo of a value (a gate field, the active-gate alias), of a part
 *      inside a gate or of a part outside the macros draws nothing
 * Run: node tests/gate_delete_zone_selfcheck.cjs <fixture.json>
 *      (driven by tests/test_gate_status_routes.py, which renders the page and the answers)
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

const PAIR = FIX.pair;
const ELSEWHERE = 'q90-q91';
const w = (function () {
  const html = '<!doctype html><html><head></head><body>'
    + '<div id="inspector-pane" hx-sync="this:replace">' + FIX.page.body + '</div>'
    // the page of another pair with gates of the same names, outside the pane
    + '<div id="elsewhere">' + FIX.page.body.split(PAIR).join(ELSEWHERE) + '</div></body></html>';
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
async function answerNext(re, a, what) {
  let req = null;
  await until(() => { req = reqs().find((r) => re.test(r.url) && !r.__done); return !!req; }, what);
  req.__done = true;
  req.respond(a.body, hx(a.headers), 200);
  await drain();
}
const zones = () => Array.from(w.document.querySelectorAll('#inspector-pane details[data-detail-zone]'))
  .map((d) => d.getAttribute('data-detail-zone'));
const zone = (g) => w.document.querySelector('#inspector-pane details[data-detail-zone="' + g + '"]');
const plain = () => w.document
  .querySelectorAll('#inspector-pane details.detail-section:not([data-detail-zone])').length;
const del = (g) => w.document.querySelector('.gate-status[data-gate="' + g + '"] .gate-status-delete');

async function deletePress(gate, offer, answer) {
  del(gate).click();
  await answerNext(/delete-offer/, offer, 'the delete offer of ' + gate);
  await until(() => !!w.document.querySelector('.gate-delete-confirm'), 'the confirm button');
  w.document.querySelector('.gate-delete-confirm').click();
  await answerNext(/delete-together/, answer, 'the delete press of ' + gate);
}

(async function main() {
  const [deleted, other] = FIX.gates;
  const plainBefore = plain();
  ok(zones().includes(deleted) && zones().includes(other), 'both gate zones are on the page');
  ok(plainBefore > 0, 'the page has sections that are not gate zones (' + plainBefore + ')');
  zone(other).open = true;
  zone(deleted).open = false;

  // R: a refused delete leaves every zone
  await deletePress(other, FIX.offer_other, FIX.refused);
  ok(w.document.body.textContent.includes(FIX.refused_text), 'R: the refusal is shown');
  ok(zones().includes(deleted) && zones().includes(other), 'R: a refused delete leaves every zone');
  ok(zone(other).open && !zone(deleted).open, 'R: the folds are kept');

  // D: the delete takes its zone off the page, only its own
  await deletePress(deleted, FIX.offer, FIX.deleted);
  ok(w.document.body.textContent.includes(deleted + ' deleted'), 'D: the delete notice is shown');
  ok(!zone(deleted), 'D: the deleted gate has no zone on the page');
  ok(!!zone(other) && zone(other).open, 'D: the other gate keeps its zone and its open fold');
  ok(plain() === plainBefore, 'D: the sections that are not gate zones stay');
  ok(!!w.document.querySelector('#elsewhere details[data-detail-zone="' + deleted + '"]'),
     'D: the zone of the same name on the page of another pair stays');

  // U: an undo of a value (a gate field, the active-gate alias), of a part inside a gate or of a
  // part outside the macros draws nothing
  const fire = (detail) => w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: detail }));
  const pairGets = () => reqs().filter((r) => r.url === '/pair/' + encodeURIComponent(PAIR)).length;
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.' + other + '.phase_shift_control',
                     created: false, deleted: false }] });
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.cz', created: false, deleted: false }] });
  fire({ entries: [{ dot_path: 'qubit_pairs.' + PAIR + '.macros.' + other + '.fidelity',
                     created: false, deleted: true }] });
  fire({ entries: FIX.undo.entries.filter((e) => e.dot_path.indexOf('qubit_pairs.') !== 0) });
  await drain();
  ok(pairGets() === 0, 'U: value undos and parts inside a gate or outside the macros draw nothing');
  // ...the undo of the delete draws the pair page again and the zone is back
  fire(FIX.undo);
  await answerNext(new RegExp('^/pair/' + encodeURIComponent(PAIR) + '$'), FIX.after, 'the pair page again');
  ok(pairGets() === 1, 'U: the undo of the delete asks for the pair page once');
  ok(zones().includes(deleted) && zones().includes(other), 'U: the zone is back on screen');

  console.log('ALL OK (' + n + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('FAIL - ' + (e && e.stack || e)); process.exit(1); });
