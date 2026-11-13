/* gl-2o5 -- the Gaussian CZ form's answer panel, in the REAL htmx.min.js +
 * REAL app.js under jsdom, against the HTML the real routes render (written
 * by tests/test_gaussian_cz.py::TestSharedMovingQubit::
 * test_the_panel_renders_the_refusal).
 *
 * Named *_fragcheck, not *_selfcheck: it needs the renders its pytest driver
 * writes.
 *
 *  P1  a 200 renders the success line in #gcz-result;
 *  P2  a new request clears that line while it is in flight;
 *  P3  a 409 (Replace offer) renders in the panel instead of being dropped,
 *      with no generic error toast;
 *  P4  pressing Replace sends overwrite=1 once; the next submit does not.
 *
 * Run: node tests/gcz_panel_fragcheck.cjs <form.html> <ok.html> <refusal.html>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
if (process.argv.length < 5) {
  console.error('FAIL  usage: node <this> <form.html> <ok.html> <refusal.html>');
  process.exit(1);
}
const FORM = fs.readFileSync(process.argv[2], 'utf8');
const OK = fs.readFileSync(process.argv[3], 'utf8');
const REFUSAL = fs.readFileSync(process.argv[4], 'utf8');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function world() {
  const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<div id="pending-tray"></div><div id="status-bar"></div><div id="table-pane"></div>'
    + '<div id="inspector-pane">' + FORM + '</div></body></html>',
    { url: 'http://localhost/pulses', runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  w.XPathEvaluator = function () {};
  w.XPathEvaluator.prototype.createExpression = function () {
    return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
  };
  const xhrs = [];
  class FakeXHR {
    constructor() { this.readyState = 0; this.status = 0; this._h = {}; this.upload = { addEventListener() {} }; xhrs.push(this); }
    open(m, u) { this.method = m; this.url = u; }
    setRequestHeader() {}
    overrideMimeType() {}
    addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
    getAllResponseHeaders() { return ''; }
    getResponseHeader() { return null; }
    abort() {}
    send(body) { this.body = body == null ? '' : String(body); }
    answer(status, text) {
      this.status = status; this.responseText = text; this.response = text;
      this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
      if (this.onload) this.onload();
      (this._h.load || []).forEach(f => f({}));
      (this._h.loadend || []).forEach(f => f({}));
    }
  }
  w.XMLHttpRequest = FakeXHR;
  w.fetch = function () { return new w.Promise(function () {}); };
  const toasts = [];
  w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  w.showToast = function (m, lvl) { toasts.push([m, lvl]); };
  w.htmx.process(w.document.body);
  return { w, d: w.document, xhrs, toasts };
}

function posts(T) { return T.xhrs.filter(x => x.method === 'POST' && /gaussian-cz$/.test(x.url || '')); }
const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

(async function main() {
  const T = world();
  const { w, d } = T;
  const form = d.getElementById('gcz-form');
  const slot = d.getElementById('gcz-result');
  ok(!!(w.htmx && form && slot), 'setup: REAL htmx + app.js, the form and its panel');

  w.htmx.trigger(form, 'submit');
  await tick(20);
  let x = posts(T).pop();
  ok(!!x, 'setup: the form posted');
  x.answer(200, OK);
  await tick(40);
  ok(/Created cz_gaussian_unipolar/.test(txt(slot)), 'P1: the success line renders -> ' + txt(slot).slice(0, 80));

  w.htmx.trigger(form, 'submit');
  await tick(20);
  x = posts(T).pop();
  ok(posts(T).length === 2, 'setup: the second submit posted');
  ok(txt(slot) === '', 'P2: the previous success line is cleared at request start -> ' + txt(slot).slice(0, 80));
  const toastsBefore = T.toasts.length;
  x.answer(409, REFUSAL);
  await tick(40);
  ok(/Already present on pair/.test(txt(slot)), 'P3: the 409 renders in the panel -> ' + txt(slot).slice(0, 80));
  ok(!/Created cz_gaussian_unipolar/.test(txt(slot)), 'P3: no stale success line beside it');
  ok(T.toasts.length === toastsBefore, 'P3: no generic error toast (' + JSON.stringify(T.toasts.slice(toastsBefore)) + ')');

  const btn = Array.prototype.find.call(slot.querySelectorAll('button'), b => /Replace existing/.test(b.textContent));
  ok(!!btn, 'P4: the panel offers Replace existing');
  if (btn) {
    // jsdom does not run this inline handler from swapped-in markup; the
    // browser does (the CSP allows inline handlers), so run its own text.
    new w.Function(btn.getAttribute('onclick') || '').call(btn);
    await tick(20);
    x = posts(T).pop();
    ok(posts(T).length === 3 && /(^|&)overwrite=1(&|$)/.test(x.body), 'P4: Replace sends overwrite=1 -> ' + (x && x.body));
    x.answer(200, OK);
    await tick(40);
    w.htmx.trigger(form, 'submit');
    await tick(20);
    x = posts(T).pop();
    ok(posts(T).length === 4 && !/overwrite=/.test(x.body), 'P4: the next submit carries no overwrite -> ' + (x && x.body));
    x.answer(200, OK);
    await tick(40);
  }

  // process.exit: app.js leaves timers running in the jsdom realm.
  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch((e) => { console.error('FAIL  threw: ' + (e && e.stack || e)); process.exit(1); });
