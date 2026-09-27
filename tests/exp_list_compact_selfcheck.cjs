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
const btn = (id) => (BASE.match(new RegExp('<button[^>]*id="' + id + '"[^>]*>')) || [''])[0];
ok(bodyTag && btn('exp-density-full') && btn('exp-density-compact'),
   'setup: base.html body tag + both density buttons found');

/* storage: undefined = key absent; 'throw' = a private window that throws on access */
function world(storage) {
  const html = '<!doctype html><html><head></head>' + bodyTag
    + '<div class="sidebar-tree-toolbar">' + btn('exp-density-full') + '</button>'
    + btn('exp-density-compact') + '</button></div><div id="sidebar-tree"></div></body></html>';
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

console.log(fails ? ('FAILED ' + fails) : ('exp_list_compact_selfcheck: all ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
