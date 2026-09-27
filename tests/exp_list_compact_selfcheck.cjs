/* Queue #8 (2026-09-26) -- the Datasets sidebar run list defaults to COMPACT.
 *
 * The user and a second customer both asked for it. localStorage
 * `quam_exp_list_compact` absent must now mean compact; only an explicit '0'
 * (the user pressed "Full names") brings the wrapped rows back, and the
 * toggle keeps working both ways.
 *
 * The REAL app.js under jsdom, on a page built from base.html's REAL <body>
 * tag and REAL density buttons (read from the template, so a template that
 * stops rendering the default class goes red here too -- the no-flash half of
 * the contract).
 *
 * Run: node tests/exp_list_compact_selfcheck.cjs  (driven by tests/test_exp_list_compact.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const APP_JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const BASE = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

const bodyTag = (BASE.match(/<body\b[^>]*>/) || [''])[0].replace(/\{\{[^}]*\}\}/g, '');
// queue #8 follow-up: the inline script that sits FIRST in the body and drops the
// class for a user who chose Full names, before anything paints (reverse flash)
const INLINE = ((BASE.split(/<body\b[^>]*>/)[1] || '').match(/^\s*(?:\{#[\s\S]*?#\}\s*)?<script>([\s\S]*?)<\/script>/) || [])[1] || '';
ok(INLINE && /quam_exp_list_compact/.test(INLINE), 'setup: base.html body starts with the inline compact-class read');
const btn = (id) => (BASE.match(new RegExp('<button[^>]*id="' + id + '"[^>]*>')) || [''])[0];
ok(bodyTag && btn('exp-density-full') && btn('exp-density-compact'),
   'setup: base.html body tag + both density buttons found');
// final QA fix 2: the same inline script brings aria-pressed along the moment
// the parser inserts the two buttons (a MutationObserver: its callback is a
// microtask, so no frame paints them first)
ok(/MutationObserver/.test(INLINE) && /exp-density-full/.test(INLINE),
   'setup: the inline body script also watches for the density buttons');
// ...and the on-state rules in style.css: the pressed LOOK must come from the
// body class, which is right before the first paint, never from the buttons'
// static aria-pressed (compact=true), which app.js fixes only at DCL.
const CSS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
const DRULES = [];
CSS.replace(/\/\*[\s\S]*?\*\//g, '').replace(/([^{}]+)\{([^{}]*)\}/g, function (_, sel, body) {
  if (/density/.test(sel)) DRULES.push({ sel: sel.trim(), body: body });
  return '';
});
const ON_RE = /(^|[;\s])color:\s*var\(--pico-primary\)/;
ok(DRULES.some(function (r) { return ON_RE.test(r.body) && !/:hover/.test(r.sel); }),
   'setup: style.css carries a density on-state rule (' + DRULES.length + ' density rules)');
function looksPressed(doc, id) {
  const el = doc.getElementById(id);
  return DRULES.some(function (r) {
    if (!ON_RE.test(r.body)) return false;
    return r.sel.split(',').some(function (s) {
      s = s.trim();
      if (/:hover|:focus|:active/.test(s)) return false;    // no pointer at first paint
      try { return el.matches(s); } catch (e) { return false; }
    });
  });
}

/* storage: undefined = key absent; 'throw' = a private window that throws on access */
function world(storage, opts) {
  // the body starts EMPTY: its inline script runs before the parser reaches the
  // sidebar, and the toolbar is inserted after it, as the parser inserts it
  const html = '<!doctype html><html><head></head>' + bodyTag + '</body></html>';
  const TOOLBAR = '<div class="sidebar-tree-toolbar">' + btn('exp-density-full') + '</button>'
    + btn('exp-density-compact') + '</button></div><div id="sidebar-tree"></div>';
  const dom = new JSDOM(html, { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  if (storage === 'throw') {
    Object.defineProperty(w, 'localStorage', { get: function () { throw new Error('SecurityError'); } });
  } else if (storage !== undefined) {
    w.localStorage.setItem('quam_exp_list_compact', storage);
  }
  w.htmx = { ajax: function () { return w.Promise.resolve(); }, process: function () {}, on: function () {},
             config: {}, find: function () { return null; } };
  w.Element.prototype.scrollIntoView = function () {};
  w.fetch = function () { return new w.Promise(function () {}); };
  // the body's inline script runs first, exactly as the browser runs it (a
  // throw here is a throw on every page load for a private window)
  try { new w.Function(INLINE).call(w); } catch (e) { console.error('inline body script threw: ' + e.stack); fails++; }
  const d0 = w.document;
  d0.body.insertAdjacentHTML('beforeend', TOOLBAR);   // the parser reaches the sidebar
  const firstPaint = {
    w: w,
    compact: () => d0.body.classList.contains('exp-list-compact'),
    pressed: () => d0.getElementById('exp-density-full').getAttribute('aria-pressed') + '/'
                 + d0.getElementById('exp-density-compact').getAttribute('aria-pressed'),
    looks: () => (looksPressed(d0, 'exp-density-full') ? 'full' : '') + (looksPressed(d0, 'exp-density-compact') ? 'compact' : ''),
  };
  // buttonsOnly: read synchronously -- the buttons just parsed, not even a
  // microtask has run; inlineOnly: a microtask later, still before DCL/app.js
  if (opts && (opts.buttonsOnly || opts.inlineOnly)) return firstPaint;
  try { new w.Function(APP_JS).call(w); } catch (e) { console.error('app.js threw: ' + e.stack); fails++; }
  const d = w.document;
  return {
    w: w,
    compact: () => d.body.classList.contains('exp-list-compact'),
    pressed: () => d.getElementById('exp-density-full').getAttribute('aria-pressed') + '/'
                 + d.getElementById('exp-density-compact').getAttribute('aria-pressed'),
    stored: () => { try { return w.localStorage.getItem('quam_exp_list_compact'); } catch (e) { return 'n/a'; } },
  };
}

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));
(async function main() {
// 1. first visit: key absent -> compact, compact button pressed, key STILL absent
{
  const T = world(undefined);
  ok(T.compact(), 'no flash: the body is ALREADY compact before any script restore runs (base.html renders the class)');
  await tick();
  ok(T.compact() && T.pressed() === 'false/true',
     'absent key -> compact, and the Compact button is the pressed one (' + T.compact() + ', ' + T.pressed() + ')');
  ok(T.stored() === null, 'restoring never writes the key -- absent stays absent (' + T.stored() + ')');
  // 2. the toggle still works both ways and persists
  T.w.setExpListCompact(false);
  ok(!T.compact() && T.pressed() === 'true/false' && T.stored() === '0',
     'Full names takes it off and stores 0 (' + T.pressed() + ', ' + T.stored() + ')');
  T.w.setExpListCompact(true);
  ok(T.compact() && T.pressed() === 'false/true' && T.stored() === '1',
     'Compact puts it back and stores 1 (' + T.pressed() + ', ' + T.stored() + ')');
}
// 2b. the reverse flash: a stored '0' is off BEFORE app.js runs (the inline
//     body script alone), an absent key and a private window keep the default
{
  ok(!world('0', { inlineOnly: true }).compact(), "stored '0' -> the inline body script already dropped the class (no reverse flash)");
  ok(world(undefined, { inlineOnly: true }).compact(), 'absent key -> the inline body script leaves compact on');
  ok(world('1', { inlineOnly: true }).compact(), "stored '1' -> the inline body script leaves compact on");
  ok(world('throw', { inlineOnly: true }).compact(), 'private window -> the inline body script leaves compact on and does not throw');
}
// 3. an explicit '0' (the user chose full names earlier) is honoured on load
{
  const T = world('0');
  await tick();
  ok(!T.compact() && T.pressed() === 'true/false', "stored '0' -> full names on load (" + T.pressed() + ')');
}
// 4. an explicit '1' stays compact
{
  const T = world('1');
  await tick();
  ok(T.compact() && T.pressed() === 'false/true', "stored '1' -> compact (" + T.pressed() + ')');
}
// 5. junk and a private window both fall to the default: compact
{
  const J = world('yes');
  await tick();
  ok(J.compact(), 'a junk value is not an explicit full-names choice -> compact');
  const P = world('throw');
  await tick();
  ok(P.compact() && P.pressed() === 'false/true', 'a private window (storage throws) -> compact, no crash');
}

// 6. final QA fix 2: the toggle's PRESSED LOOK at the first paint. Rows were
//    right from the first paint already (2b); the buttons shipped a static
//    compact-pressed state that only app.js's DOMContentLoaded apply fixed --
//    real Chrome painted the wrong button in 3 of 6 Full-names reloads.
for (const [st, want] of [['0', 'full'], [undefined, 'compact'], ['1', 'compact'], ['throw', 'compact']]) {
  const lbl = st === undefined ? 'absent key' : (st === 'throw' ? 'private window' : "stored '" + st + "'");
  const B = world(st, { buttonsOnly: true });
  ok(B.looks() === want, lbl + ': the buttons look ' + want + '-pressed the moment they are parsed, before anything else runs (' + B.looks() + ')');
  const I = world(st, { inlineOnly: true });
  await Promise.resolve();                     // the observer's microtask -- no task, no frame in between
  const aria = want === 'full' ? 'true/false' : 'false/true';
  ok(I.pressed() === aria && I.looks() === want,
     lbl + ': aria-pressed follows in the same turn, before any frame and before DOMContentLoaded (' + I.pressed() + ', ' + I.looks() + ')');
}
{
  const T = world('0');
  await tick();
  const d = T.w.document;
  const looks = () => (looksPressed(d, 'exp-density-full') ? 'full' : '') + (looksPressed(d, 'exp-density-compact') ? 'compact' : '');
  ok(looks() === 'full', "after app.js, stored '0' still looks full (" + looks() + ')');
  T.w.setExpListCompact(true);
  ok(looks() === 'compact' && T.pressed() === 'false/true', 'pressing Compact moves the look and the aria together (' + looks() + ')');
  T.w.setExpListCompact(false);
  ok(looks() === 'full' && T.pressed() === 'true/false', 'pressing Full names moves them back (' + looks() + ')');
}

console.log(fails ? ('FAILED ' + fails) : ('exp_list_compact_selfcheck: all ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
