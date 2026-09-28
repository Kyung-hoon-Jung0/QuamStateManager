/* w8 pulsehint -- the create form's "Don't see your pulse class?" line, in the
 * REAL htmx.min.js + REAL app.js + REAL pulses.js under jsdom, against the
 * HTML the real routes render (the fixture JSON is written by
 * tests/test_pulse_class_find.py::test_pulses_classfind_client_fragcheck).
 *
 * Named *_fragcheck, not *_selfcheck: `npm run selfcheck` runs every
 * *_selfcheck.cjs with no arguments, and this one needs the renders its
 * pytest driver writes.
 *
 *  C1  the full form swapped in by htmx keeps the line right under the class
 *      list (a nested hx-swap-oob would have pulled it out of the form);
 *  C2  its link opens the env strip's module <details>, scrolls it into view
 *      and puts the caret in the module input -- no navigation;
 *  C3  a strip-only response (poll / Add module) swaps the strip AND the line
 *      (out-of-band): a module that failed to import reads "✗ <module>:
 *      <error>" under the class list, and the link then opens the NEW strip;
 *  C4  a module name typed in the strip is not a form edit (the probe it
 *      starts must rebuild the class list, not offer "clears this form");
 *  C5  with no env the line points at the env picker (an htmx link) and has
 *      nothing to open -- and that link, the one htmx element inside the
 *      create form, inherits none of the form's request attributes (it lit
 *      "Creating..." and logged a disabled-elt selector error on every press);
 *      once the picker landed, the stale form is closed.
 *
 * Run: node tests/pulses_classfind_fragcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
if (!process.argv[2]) {
  console.error('FAIL  usage: node <this> <fixture.json>  (written by tests/test_pulse_class_find.py)');
  process.exit(1);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function world() {
  const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<div id="pending-tray"></div><div id="table-pane"></div>'
    + '<div id="inspector-pane" hx-sync="this:replace"></div></body></html>',
    { url: 'http://localhost/pulses', runScripts: 'dangerously', pretendToBeVisual: true });
  const w = dom.window;
  const scrolled = [];
  const consoleErrors = [];
  w.console.error = function () { consoleErrors.push(Array.prototype.join.call(arguments, ' ')); };
  w.Element.prototype.scrollIntoView = function () { scrolled.push(this); };
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
    abort() { this.aborted = true; (this._h.abort || []).forEach(f => f({})); (this._h.loadend || []).forEach(f => f({})); }
    send(body) { this.body = body; this.sent = true; }
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
  w.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));
  return { w, d: w.document, xhrs, scrolled, consoleErrors };
}

async function load(T, url, html, target, swap) {
  T.w.htmx.ajax('GET', url, { target: target, swap: swap || 'innerHTML' });
  await tick(10);
  const x = T.xhrs.filter(r => r.url && r.url.indexOf(url) === 0 && !r.answered).pop();
  if (!x) return false;
  x.answered = true;
  x.answer(200, html);
  await tick(60);
  return true;
}

function clickLink(T, a) {
  const ev = new T.w.MouseEvent('click', { bubbles: true, cancelable: true, view: T.w });
  a.dispatchEvent(ev);
  return ev;
}

const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

(async function main() {
  // ── env ok: the form, then the link ────────────────────────────────────
  const T = world();
  const { w, d } = T;
  ok(!!(w.htmx && w.htmx.ajax && w.PulsesPage), 'setup: REAL htmx, app.js and pulses.js loaded');
  ok(await load(T, '/pulse/new', FX.form_ok, '#inspector-pane'), 'setup: the create form was requested and answered');
  let find = d.getElementById('pulse-create-classfind');
  const typeSel = d.getElementById('pulse-create-type');
  ok(!!find && !!typeSel, 'C1: the form has the class list and the line');
  ok(find && find.previousElementSibling === typeSel,
     'C1: the line sits RIGHT under the class list (after: ' + (find && find.previousElementSibling && find.previousElementSibling.id) + ')');
  ok(find && !!find.closest('form.pulse-create-form'), 'C1: the line is inside the form (not pulled out by a nested OOB)');
  ok(d.querySelectorAll('#pulse-create-classfind').length === 1, 'C1: exactly one line');
  ok(find && find.getAttribute('data-classfind-state') === 'ok', 'C1: state ok');
  ok(/Don.t see your pulse class\? Name the module it lives in \(e\.g\. my_lab\.cz_pulses\)/.test(txt(find)),
     'C1: says "Don\'t see your pulse class? Name the module it lives in (e.g. my_lab.cz_pulses)" -> ' + txt(find));

  let det = d.querySelector('#pulse-env-strip details.pulse-env-modules');
  ok(!!det && !det.open, 'C2: the strip\'s module box starts folded');
  T.scrolled.length = 0;
  const hashBefore = w.location.href;
  let ev = clickLink(T, find.querySelector('[data-pulse-module-open]'));
  await tick(20);
  const inp = det && det.querySelector('input[name="module"]');
  ok(det && det.open === true, 'C2: the click opened the module box');
  ok(inp && d.activeElement === inp, 'C2: the caret is in the module input (active: ' + (d.activeElement && (d.activeElement.name || d.activeElement.tagName)) + ')');
  ok(T.scrolled.indexOf(det) !== -1, 'C2: the box was scrolled into view');
  ok(ev.defaultPrevented && w.location.href === hashBefore, 'C2: no navigation (the href is only the no-JS fallback)');

  // ── C4: a module name typed in the strip is not a form edit ────────────
  const P = w.PulsesPage;
  const formInput = d.getElementById('pulse-create-name');
  ok(P._createEditMarksDirty({ isTrusted: true, target: inp }) === false,
     'C4: typing in the strip\'s module input does not mark the form touched');
  ok(P._createEditMarksDirty({ isTrusted: true, target: formInput }) === true,
     'C4: typing in the form still does');
  ok(P._createEditMarksDirty({ isTrusted: false, target: formInput }) === false,
     'C4: a script-fired input still does not');

  // ── C3: a strip-only response swaps the line out-of-band ───────────────
  det.open = false;
  inp.blur();
  const oldStrip = d.getElementById('pulse-env-strip');
  ok(await load(T, '/pulse/new/env-strip', FX.strip_failed, '#pulse-env-strip', 'outerHTML'),
     'setup: a strip-only response was requested and answered');
  const newStrip = d.getElementById('pulse-env-strip');
  ok(newStrip && newStrip !== oldStrip, 'C3: the strip was replaced');
  find = d.getElementById('pulse-create-classfind');
  ok(d.querySelectorAll('#pulse-create-classfind').length === 1, 'C3: still exactly one line');
  ok(find && find.previousElementSibling === d.getElementById('pulse-create-type'),
     'C3: the swapped line is still right under the class list');
  ok(find && find.getAttribute('data-classfind-state') === 'failed', 'C3: state failed');
  ok(/✗ my_lab\.nope: ModuleNotFoundError: No module named 'my_lab'/.test(txt(find)),
     'C3: "✗ <module>: <error>" under the class list -> ' + txt(find));
  det = newStrip.querySelector('details.pulse-env-modules');
  det.open = false;
  clickLink(T, find.querySelector('[data-pulse-module-open]'));
  await tick(20);
  ok(det.open === true && d.activeElement === det.querySelector('input[name="module"]'),
     'C2/C3: after the swap the link opens + focuses the NEW strip\'s box');

  // ── C5: no env ─────────────────────────────────────────────────────────
  ok(await load(T, '/pulse/new', FX.form_noenv, '#inspector-pane'), 'setup: the no-env form was answered');
  find = d.getElementById('pulse-create-classfind');
  ok(find && find.getAttribute('data-classfind-state') === 'noenv', 'C5: state noenv');
  ok(/Don.t see your pulse class\? Pick a Python environment first/.test(txt(find)),
     'C5: says "Pick a Python environment first" -> ' + txt(find));
  ok(!find.querySelector('[data-pulse-module-open]') && P.openModuleForm() === false,
     'C5: nothing to open without an env');
  const envA = find.querySelector('a[href="/generate"]');
  ok(!!envA, 'C5: the env picker link is there');
  const nBefore = T.xhrs.length;
  const errBefore = T.consoleErrors.length;
  clickLink(T, envA);
  await tick(20);
  const gen = T.xhrs.slice(nBefore).find(x => x.method === 'GET' && /^\/generate/.test(x.url));
  ok(!!gen, 'C5: the link opens the env picker through htmx (' + T.xhrs.slice(nBefore).map(x => x.method + ' ' + x.url).join(', ') + ')');
  const busy = d.getElementById('pulse-create-busy');
  ok(!!busy && !busy.classList.contains('htmx-request'),
     'C5: the press does not light the form\'s "Creating..." indicator');
  const leaked = T.consoleErrors.slice(errBefore).filter(m => /hx-disabled-elt|returned no matches/.test(m));
  ok(leaked.length === 0, 'C5: no inherited disabled-elt selector error (' + leaked.join(' | ') + ')');
  if (gen) { gen.answered = true; gen.answer(200, '<div id="gen-env-list">envs</div>'); await tick(60); }
  ok(!!d.querySelector('#table-pane #gen-env-list'), 'C5: the picker lands in the main pane');
  ok(!d.getElementById('pulse-create-root') && d.getElementById('inspector-pane').innerHTML === '',
     'C5: once it landed, the stale create form is closed (the picker gets the whole page)');

  // ── C6: the probe lands while the form is TOUCHED ──────────────────────
  // (a qubit picked, a name typed): the class list grows in place, nothing
  // typed is lost, no "clears this form" offer; only when the SELECTED class's
  // own spec moved is the rebuild still offered.
  {
    const T6 = world();
    const d6 = T6.d, w6 = T6.w, P6 = w6.PulsesPage;
    ok(await load(T6, '/pulse/new', FX.form_ok, '#inspector-pane'), 'C6 setup: the form was answered');
    const root6 = d6.getElementById('pulse-create-root');
    const sel6 = d6.getElementById('pulse-create-type');
    ok(!!sel6 && !sel6.querySelector('option[value="LabWigglePulse"]'), 'C6 setup: the new class is not offered yet');
    const name6 = d6.getElementById('pulse-create-name');
    name6.value = 'kept_name';
    const before = sel6.value;
    root6._dirty = true;                               // what a real keystroke sets
    let swaps6 = 0;
    const realSwap = w6.htmx.swap;
    w6.htmx.swap = function () { swaps6++; return realSwap.apply(this, arguments); };
    const answer = (html) => function () {
      return w6.Promise.resolve({ ok: true, text: function () { return w6.Promise.resolve(html); } });
    };
    w6.fetch = answer(FX.form_more);
    P6.reloadCreateForm();                             // what the after-probe strip calls
    await tick(60);
    ok(d6.getElementById('pulse-create-type') === sel6 && !!sel6.querySelector('option[value="LabWigglePulse"]'),
       'C6: the new class joined the SAME list (no rebuild)');
    ok(d6.getElementById('pulse-create-name') === name6 && name6.value === 'kept_name', 'C6: the typed name is untouched');
    ok(sel6.value === before, 'C6: the selected class is kept (' + sel6.value + ')');
    ok(swaps6 === 0, 'C6: nothing was swapped');
    ok(!d6.querySelector('#pulse-env-strip .pulse-env-refresh'), 'C6: no "refresh (clears this form)" offer');
    sel6.value = 'LabWigglePulse';
    P6.createTypeChanged(sel6);
    ok(d6.getElementById('pulse-create-qclass').value === 'otherlab.custom.pulses.LabWigglePulse',
       'C6: picking the new class fills its class path from the merged catalog');
    // the selected class's own spec moves in the next render -> the rebuild is offered
    const moved = FX.form_more.replace(
      /(<script id="pulse-catalog-data" type="application\/json">)([\s\S]*?)(<\/script>)/,
      function (m, a, b, c) {
        const cat = JSON.parse(b);
        cat.LabWigglePulse.params = cat.LabWigglePulse.params.concat([{ name: 'skew', label: 'Skew', kind: 'float', default: 0, unit: '', synth: true, required: false }]);
        return a + JSON.stringify(cat) + c;
      });
    ok(moved !== FX.form_more, 'C6 setup: a render whose selected class grew a field');
    root6._dirty = true;
    w6.fetch = answer(moved);
    P6.reloadCreateForm();
    await tick(60);
    ok(!!d6.querySelector('#pulse-env-strip .pulse-env-refresh') && swaps6 === 0 && name6.value === 'kept_name',
       'C6: the selected class changed its own fields -> the rebuild is OFFERED, never done behind the user');
  }

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('ALL OK pulses_classfind_fragcheck');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
