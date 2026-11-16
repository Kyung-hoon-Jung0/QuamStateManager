/* jsdom selfcheck under the REAL bundled htmx: the CZ gate status line of an open pulse detail
 * follows an Apply.
 *   A  the clean Apply's signal (pulses-changed on body) reads the line once; the answer says
 *      "on the chip"; the pulse fields are the same nodes (only the line is redrawn)
 *   L  liveDriftChanged alone (a push of saved values) reads the line too; events fired together
 *      read it once
 *   O  the line's own press answer (its pulses-changed from inside the line) reads nothing
 *   H  a press of the line out: a signal reads nothing until the press ends, then once, and the
 *      press answer standing in the line is carried into the new line
 *   F  a delete offer open in the line: a signal reads nothing; Cancel closes it, then one read
 *   G  a read answered 404 (the gate is gone) leaves the line as drawn
 * Run: node tests/gate_line_refresh_selfcheck.cjs <fixture.json>
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

// the mock server, installed in the page's realm: every request (xhr or fetch) waits until the
// test answers it
const MOCK = `(function () {
  window.__reqs = []; window.__fetches = [];
  function Target() { this._l = {}; }
  Target.prototype.addEventListener = function (t, f) { (this._l[t] = this._l[t] || []).push(f); };
  Target.prototype._fire = function (t) { (this._l[t] || []).forEach(function (f) { f({type: t}); }); };
  function X() { Target.call(this); this.upload = new Target(); this._h = {}; this.status = 0; this.readyState = 0; }
  X.prototype = Object.create(Target.prototype);
  X.prototype.open = function (m, u) { this.method = m; this.url = u; };
  X.prototype.overrideMimeType = function () {};
  X.prototype.setRequestHeader = function () {};
  X.prototype.send = function (b) { this.body = b; window.__reqs.push(this); };
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
  window.fetch = function (url) {
    return new Promise(function (resolve) {
      window.__fetches.push({ url: String(url), respond: function (body, status) {
        resolve({ status: status, ok: status >= 200 && status < 300,
                  text: function () { return Promise.resolve(body); } });
      } });
    });
  };
})();`;

// the headers htmx acts on; HX-Trigger kept only where a check asks for it
function hx(headers, keepTrigger) {
  const out = {};
  for (const k of Object.keys(headers || {})) {
    if (/^HX-(Retarget|Reswap)$/i.test(k) || (keepTrigger && /^HX-Trigger$/i.test(k))) out[k] = headers[k];
  }
  return out;
}

const GNAME = FIX.gate;
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
// let every queued task run, the line's 30 ms gathering of one Apply's events included
async function drain() {
  for (let i = 0; i < 20; i++) await new Promise((r) => w.setTimeout(r, 0));
  await new Promise((r) => w.setTimeout(r, 60));
  for (let i = 0; i < 20; i++) await new Promise((r) => w.setTimeout(r, 0));
}

const STATUS = /^\/pulse\/cz-gate\/status\?/;
const reads = () => w.eval('__fetches').filter((f) => STATUS.test(f.url));
const open = () => reads().filter((f) => !f.done);
async function answerRead(body, status, what) {
  await until(() => open().length > 0, what);
  const f = open()[0];
  f.done = true;
  f.respond(body, status);
  await drain();
}
async function answerXhr(match, a, keepTrigger, what) {
  let req = null;
  await until(() => { req = w.eval('__reqs').find((r) => match(r.url) && !r.__done); return !!req; }, what);
  req.__done = true;
  req.respond(a.body, hx(a.headers, keepTrigger), a.status || 200);
  await drain();
}
const pane = () => w.document.getElementById('inspector-pane');
const line = () => pane().querySelector('#pulse-detail-root .gate-status[data-gate="' + GNAME + '"]');
const pills = () => (line().querySelector('.gate-status-items') || { textContent: '' }).textContent;
const lineText = () => line().textContent.replace(/\s+/g, ' ');
const fields = () => Array.prototype.slice.call(pane().querySelectorAll('#pulse-detail-root input'));
const body = (ev) => w.document.body.dispatchEvent(new w.CustomEvent(ev, { bubbles: true }));

(async function main() {
  ok(!!line(), 'the pulse detail of the new gate shows its status line');
  ok(lineText().includes('only in the working copy'), 'the line says "only in the working copy"');
  const inputs = fields();
  ok(inputs.length > 0, 'the detail has pulse fields (' + inputs.length + ')');

  // A: the clean Apply's signal
  body('pulses-changed');
  await drain();
  ok(reads().length === 1 && reads()[0].url.includes('gate=' + GNAME),
     'A: the Apply signal reads the line once');
  await answerRead(FIX.after.body, 200, 'the line after the Apply');
  ok(lineText().includes('on the chip') && !lineText().includes('only in the working copy'),
     'A: the line says "on the chip"');
  const now = fields();
  ok(now.length === inputs.length && now.every((x, i) => x === inputs[i]),
     'A: the pulse fields are the same nodes (only the line is redrawn)');
  ok(!!line().querySelector('.gate-status-delete') && !!line().querySelector('.gate-status-activate'),
     'A: the new line has its buttons');

  // L: a push of saved values fires liveDriftChanged alone; events fired together read once
  body('liveDriftChanged');
  await drain();
  ok(reads().length === 2, 'L: liveDriftChanged alone reads the line');
  await answerRead(FIX.after.body, 200, 'the line again');
  body('liveDriftChanged');
  body('stateHistoryChanged');
  body('pulses-changed');
  w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { entries: [] } }));
  await drain();
  ok(reads().length === 3, 'L: the events of one moment read the line once');
  await answerRead(FIX.before.body, 200, 'the line once more');
  ok(lineText().includes('only in the working copy'), 'L: the line shows what the server answered');

  // O: the line's own press answer
  line().querySelector('.gate-status-items').dispatchEvent(new w.CustomEvent('pulses-changed', { bubbles: true }));
  await drain();
  ok(reads().length === 3, 'O: a signal from inside the line (its own answer) reads nothing');

  // H: a press of the line out
  line().querySelector('.gate-status-activate').click();
  await until(() => w.eval('__reqs').some((r) => /make-active/.test(r.url) && !r.__done), 'the press');
  body('pulses-changed');
  await drain();
  ok(reads().length === 3, 'H: a signal while the press is out reads nothing');
  const noted = FIX.before.body.replace('<div class="gate-status-slot"',
    '<div data-press-answer><p class="press-note">the press answer</p></div><div class="gate-status-slot"');
  ok(noted !== FIX.before.body, 'H: the press answer fixture carries a note');
  await answerXhr((u) => /make-active/.test(u),
                  { body: noted, headers: { 'HX-Trigger': 'pulses-changed' } }, true, 'the press answer');
  ok(!!line().querySelector('.press-note'), 'H: the press answer is painted in the line');
  ok(reads().length === 4, 'H: once the press ends the owed read runs once (its own trigger is not counted)');
  await answerRead(FIX.after.body, 200, 'the owed read');
  ok(lineText().includes('on the chip'), 'H: the owed read shows "on the chip"');
  ok(!!line().querySelector('.press-note'), 'H: the press answer is carried into the new line');

  // F: a delete offer open
  line().querySelector('.gate-status-delete').click();
  await answerXhr((u) => /delete-offer/.test(u), FIX.offer, false, 'the delete offer');
  ok(!!line().querySelector('.gate-delete-offer'), 'F: the delete offer is open in the line');
  body('liveDriftChanged');
  await drain();
  ok(reads().length === 4, 'F: a signal while the offer is open reads nothing');
  line().querySelector('.gate-delete-cancel').click();
  await answerXhr((u) => /offer-close/.test(u), { body: '', headers: {} }, false, 'the offer close');
  ok(!line().querySelector('.gate-delete-offer'), 'F: Cancel closed the offer');
  ok(reads().length === 5, 'F: the held read runs once the offer closes');
  await answerRead(FIX.after.body, 200, 'the read after the offer');
  ok(!line().querySelector('.press-note'), 'F: a read no press owed shows the server answer alone');

  // G: the gate is gone
  const drawn = line();
  body('pulses-changed');
  await drain();
  await answerRead('<div>gone</div>', 404, 'a 404 read');
  ok(line() === drawn, 'G: a 404 read leaves the line as drawn');

  console.log('ALL OK (' + n + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('FAIL - ' + (e && e.stack || e)); process.exit(1); });
