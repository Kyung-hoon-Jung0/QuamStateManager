/* Pulses final QA (2026-09-28) -- three UI pins in the REAL htmx.min.js +
 * REAL app.js under jsdom, with a fake XMLHttpRequest standing in for the
 * server.
 *
 *  P2  the env strip's after-probe poll must never abort an in-flight
 *      POST /api/pulse/create. Both live inside #inspector-pane
 *      (hx-sync="this:replace"); the strip carries its own hx-sync (read
 *      from the REAL template), so its ticks synchronise against the strip.
 *      Mutation: drop the strip's hx-sync -> the create XHR is aborted -> red.
 *  P3b an undo repaints an inspector input from the RAW value, never the
 *      grids' comma-grouped display string ("-363,323.0").
 *  P3c an undo that restores a DELETED pulse while the inspector says
 *      "Deleted <name>" re-opens the pulse (GET /pulse/detail).
 *
 * Run: node tests/pulses_final_qa_selfcheck.cjs  (driven by tests/test_pulses_final_qa.py)
 */
'use strict';
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const TPL = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_pulse_env_strip.html'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

// the strip's own sync attribute, as the REAL template renders it
const stripTag = (TPL.match(/<div id="pulse-env-strip"[^>]*>/) || [''])[0];
const stripSync = (stripTag.match(/hx-sync="([^"]*)"/) || [, ''])[1];
ok(stripTag.length > 0, 'setup: the strip tag was found in _pulse_env_strip.html');
const syncAttr = stripSync ? ' hx-sync="' + stripSync + '"' : '';

function world(bodyHtml, url) {
  const dom = new JSDOM('<!doctype html><html><head></head><body>' + bodyHtml + '</body></html>',
    { url: url, runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {};
  w.XPathEvaluator = function () {};
  w.XPathEvaluator.prototype.createExpression = function () {
    return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
  };
  const xhrs = [];
  class FakeXHR {
    constructor() { this.readyState = 0; this.status = 0; this._h = {}; this.aborted = false; this.upload = { addEventListener() {} }; xhrs.push(this); }
    open(m, u) { this.method = m; this.url = u; }
    setRequestHeader() {}
    overrideMimeType() {}
    addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
    getAllResponseHeaders() { return ''; }
    getResponseHeader() { return null; }
    abort() { this.aborted = true; if (this.onabort) this.onabort(); (this._h.abort || []).forEach(f => f({})); (this._h.loadend || []).forEach(f => f({})); }
    send(body) { this.body = body; this.sent = true; /* never answers unless told to */ }
    answer(status, text) {
      if (this.aborted) return;
      this.status = status; this.responseText = text; this.response = text;
      this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
      if (this.onload) this.onload();
      (this._h.load || []).forEach(f => f({}));
      (this._h.loadend || []).forEach(f => f({}));
    }
  }
  w.XMLHttpRequest = FakeXHR;
  w.fetch = function () { return new w.Promise(function () {}); };
  w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
  return { w: w, d: w.document, xhrs: xhrs };
}

(async function main() {
  // ── P2: a strip poll tick during a pending create POST ─────────────────
  {
    const T = world(
      '<div id="pending-tray"></div>'
      + '<div id="inspector-pane" hx-sync="this:replace">'
      + '  <div id="pulse-create-root">'
      + '    <div id="pulse-env-strip" class="pulse-env-strip"' + syncAttr
      + '         hx-get="/pulse/new/env-strip?after_probe=1" hx-trigger="every 80ms" hx-swap="outerHTML">probing</div>'
      + '    <form id="cf" hx-post="/api/pulse/create" hx-target="#inspector-pane" hx-swap="innerHTML">'
      + '      <input type="text" name="name" value="fq_race1"><button type="submit">Create</button></form>'
      + '  </div></div>', 'http://localhost/pulses');
    const { w, d, xhrs } = T;
    ok(!!(w.htmx && w.htmx.ajax), 'setup: the REAL htmx loaded');
    w.htmx.process(d.body);
    await tick(20);
    // press Create: the POST leaves and stays in the air (the server is slow)
    d.getElementById('cf').requestSubmit ? d.getElementById('cf').requestSubmit()
                                         : w.htmx.trigger(d.getElementById('cf'), 'submit');
    await tick(20);
    const create = xhrs.find(x => x.method === 'POST' && /\/api\/pulse\/create/.test(x.url));
    ok(!!create && create.sent, 'the create POST left (' + xhrs.map(x => x.method + ' ' + x.url).join(', ') + ')');
    // let two poll ticks fire while the POST is pending
    await tick(220);
    const polls = xhrs.filter(x => x.method === 'GET' && /env-strip\?after_probe=1/.test(x.url));
    ok(polls.length >= 1, 'the strip polled while the POST was pending (' + polls.length + ' tick(s))');
    ok(create && !create.aborted, 'P2: the strip\'s poll ticks did NOT abort the in-flight create POST (strip hx-sync=' + JSON.stringify(stripSync) + ')');
    // the answer still lands: the detail replaces the pane
    create.answer(200, '<div id="pulse-detail-root" data-pulse-path="qubits.q2.xy.operations.fq_race1">created</div>');
    await tick(30);
    ok(!!d.querySelector('#inspector-pane #pulse-detail-root'), 'P2: the create response was swapped into the pane after the ticks');
  }

  // ── P3b: the undo repaint writes the RAW value ─────────────────────────
  {
    const P = 'qubits.q1.xy.operations.x180_DragCosine';
    const T = world(
      '<div id="pending-tray"></div>'
      + '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="' + P + '">'
      + '<form class="inline-edit pulse-edit-form"><input type="hidden" name="dot_path" value="' + P + '.detuning">'
      + '<input type="text" name="value" data-param="detuning" data-kind="float" data-synth="1" data-committed="-1.0" value="-1.0"></form>'
      + '</div></div>', 'http://localhost/pulses');
    const { w, d } = T;
    d.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
      { dot_path: P + '.detuning', old_value_str: '-363323.0', old_value_disp: '-363,323.0', old_kind: 'float' }] } }));
    await tick(20);
    const inp = d.querySelector('input[name="value"][data-param="detuning"]');
    ok(inp.value === '-363323.0', 'P3b: the input repaints the RAW value (' + inp.value + ')');
    ok(inp.getAttribute('data-committed') === '-363323.0', 'P3b: data-committed is the RAW value (' + inp.getAttribute('data-committed') + ')');
    // a genuine string with a comma is left alone
    d.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
      { dot_path: P + '.detuning', old_value_str: 'MW,FEM', old_value_disp: 'MW,FEM', old_kind: 'str' }] } }));
    await tick(20);
    ok(inp.value === 'MW,FEM', 'P3b: a non-numeric string with a comma is not unwrapped (' + inp.value + ')');
  }

  // ── P3c: an undo of a delete re-opens the pulse over the 'Deleted' toast ─
  {
    const P = 'qubits.q1.xy.operations.saturation';
    const T = world(
      '<div id="pending-tray"></div>'
      + '<div id="inspector-pane"><div class="toast toast-success"><p>Deleted saturation</p></div></div>',
      'http://localhost/pulses?per_page=50');
    const { w, d, xhrs } = T;
    d.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
      { dot_path: P, old_value_str: '', old_value_disp: '', deleted: true, created: false }] } }));
    await tick(40);
    const get = xhrs.find(x => x.method === 'GET' && /\/pulse\/detail\?path=/.test(x.url));
    ok(!!get && decodeURIComponent(get.url).indexOf(P) >= 0, 'P3c: the restored pulse is re-opened (GET /pulse/detail for it): ' + (get && get.url));
    if (get) { get.answer(200, '<div id="pulse-detail-root" data-pulse-path="' + P + '">back</div>'); await tick(30); }
    ok(!!d.querySelector('#inspector-pane #pulse-detail-root') && !d.querySelector('#inspector-pane .toast'),
       'P3c: the detail replaced the stale "Deleted" toast');
    // a plain value undo over the toast opens nothing
    const before = xhrs.length;
    d.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
      { dot_path: P + '.amplitude', old_value_str: '0.1', old_value_disp: '0.1' }] } }));
    await tick(30);
    ok(xhrs.length === before, 'P3c: a value undo (no deleted entry) issues no re-open');
  }

  console.log(fails ? ('FAILED ' + fails) : ('pulses_final_qa_selfcheck: all ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
