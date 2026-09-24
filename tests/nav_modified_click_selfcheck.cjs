/* QA generate-r2-22 -- a Ctrl/Cmd/Shift+click on a nav link opens a tab and
 * leaves THIS page alone.
 *
 * Every `<a href hx-get>` in SM is a real page URL, but the bundled htmx 2.0.4
 * treats a modified click on it like a plain one (it exempts only hx-boost
 * anchors, and SM uses none): preventDefault + swap. So Ctrl+click on the
 * sidebar left the Generate wizard mid-step. app.js's NavModifiedClick is a
 * document CAPTURE listener that stops a modified primary click before htmx's
 * own listener on the anchor sees it, WITHOUT preventing the default.
 *
 * Runs the REAL bundled htmx and the SHIPPED block (sliced from app.js) in one
 * jsdom realm, the order base.html loads them (htmx, then app.js):
 *   N1 plain click      -> htmx issues (htmx:confirm) and cancels the default
 *   N2 ctrl/meta/shift   -> htmx never sees it, the default is NOT prevented
 *   N3 a click on a child of the anchor counts as a click on the anchor
 *   N4 href="#"          -> no page of its own: still htmx's, even with Ctrl
 *   N5 alt / right-button -> left to htmx / untouched
 *   N6 a plain anchor with no hx-get is untouched either way
 *   N7 Cmd is the tab gesture on a Mac only: Win/Super+click on Windows or
 *      Linux opens no tab, so it stays htmx's (review of generate-r2-22)
 * Mutation: delete the listener and N2/N3 go red (the control N1 stays green).
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) {
  console.error('SKIP: jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const HTMX_SRC = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let failures = 0, asserts = 0;
function check(name, cond, detail) {
  asserts++;
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}

const START = 'window.NavModifiedClick = (function () {';
const s = APP.indexOf(START);
const e = s < 0 ? -1 : APP.indexOf('})();', s);
check('N0 the NavModifiedClick block ships in app.js', s >= 0 && e > s);
if (s < 0 || e < 0) { console.error(failures + ' check(s) failed'); process.exit(1); }
const BLOCK = APP.slice(s, e + 5);

// htmx scans hx-on attributes through `new XPathEvaluator`, which jsdom cannot
// execute; the fixture has none, so an empty iterator is faithful (the same
// accommodation settle_config_selfcheck.cjs documents).
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

const vc = new VirtualConsole();
// jsdom logs "Not implemented: navigation" when an unprevented link click
// would navigate -- that IS the browser default we want; anything else is real.
vc.on('jsdomError', (err) => {
  if (!/Not implemented: navigation/.test(String(err && err.message))) {
    failures++; console.error('FAIL  jsdom error: ' + (err && err.stack || err));
  }
});

const html = '<!doctype html><html><head>' +
  '<script>' + XPATH_SHIM + '</scr' + 'ipt>' +
  '<script>' + HTMX_SRC + '</scr' + 'ipt>' +
  '<script>' + BLOCK + '</scr' + 'ipt>' +
  '</head><body>' +
  '<nav><a id="nav" href="/instrument" hx-get="/instrument" hx-target="#table-pane" hx-push-url="true">Instrument <b id="inner">Wiring</b></a>' +
  '<a id="hash" href="#" hx-get="/dataset/x" hx-target="#table-pane">parent run</a>' +
  '<a id="plain" href="/help">Help</a></nav>' +
  '<div id="table-pane"></div></body></html>';
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/generate', virtualConsole: vc });
const w = dom.window;

let issued = [];
// htmx:confirm is the first thing htmx fires when it decides to issue; cancel
// it so no request leaves the fixture.
w.document.addEventListener('htmx:confirm', (evt) => { issued.push(evt.detail && evt.detail.path); evt.preventDefault(); });

function press(id, mods) {
  issued = [];
  const el = w.document.getElementById(id);
  const ev = new w.MouseEvent('click', Object.assign({ bubbles: true, cancelable: true, button: 0 }, mods || {}));
  const target = (mods && mods._text) ? el.firstChild : el;
  target.dispatchEvent(ev);
  return { toHtmx: issued.length > 0, prevented: ev.defaultPrevented };
}

// navigator.platform decides whether Cmd is a tab gesture; jsdom reports ''
function setPlatform(p) {
  Object.defineProperty(w.navigator, 'platform', { get: () => p, configurable: true });
}

w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(run, 0));

function run() {
  check('N0b the real htmx loaded and processed the anchors', !!w.htmx && typeof w.htmx.process === 'function');

  const plain = press('nav');
  check('N1 CONTROL: a plain click goes to htmx (the pane swaps)', plain.toHtmx, JSON.stringify(plain));
  check('N1b CONTROL: htmx cancels the plain click default', plain.prevented, JSON.stringify(plain));

  setPlatform('MacIntel');
  [['ctrlKey', 'Ctrl'], ['metaKey', 'Cmd (Mac)'], ['shiftKey', 'Shift']].forEach(([k, name]) => {
    const r = press('nav', { [k]: true });
    check('N2 ' + name + '+click never reaches htmx (this page stays)', !r.toHtmx, JSON.stringify(r));
    check('N2b ' + name + '+click keeps the browser default (a tab/window opens)', !r.prevented, JSON.stringify(r));
  });
  const both = press('nav', { ctrlKey: true, shiftKey: true });
  check('N2c Ctrl+Shift+click: tab, no swap', !both.toHtmx && !both.prevented, JSON.stringify(both));

  const child = press('inner', { ctrlKey: true });
  check('N3 Ctrl+click on a child element of the link counts as the link', !child.toHtmx && !child.prevented, JSON.stringify(child));
  const text = press('nav', { ctrlKey: true, _text: true });
  check('N3b Ctrl+click whose target is a TEXT node counts as the link', !text.toHtmx && !text.prevented, JSON.stringify(text));

  const hash = press('hash', { ctrlKey: true });
  check('N4 href="#" has no page of its own: Ctrl+click is still htmx\'s', hash.toHtmx, JSON.stringify(hash));

  const alt = press('nav', { altKey: true });
  check('N5 Alt+click is left to htmx', alt.toHtmx, JSON.stringify(alt));
  const right = press('nav', { ctrlKey: true, button: 2 });
  check('N5b a non-primary button is not claimed by the guard', right.toHtmx === press('nav', { button: 2 }).toHtmx, JSON.stringify(right));

  const help = press('plain', { ctrlKey: true });
  check('N6 a plain link (no hx-get) keeps its default', !help.toHtmx && !help.prevented, JSON.stringify(help));

  ['Win32', 'Linux x86_64'].forEach((plat) => {
    setPlatform(plat);
    const win = press('nav', { metaKey: true });
    check('N7 ' + plat + ': Win/Super+click is no tab gesture -- htmx swaps as for a plain click', win.toHtmx && win.prevented, JSON.stringify(win));
    const ctl = press('nav', { ctrlKey: true });
    check('N7b ' + plat + ': Ctrl+click still opens a tab', !ctl.toHtmx && !ctl.prevented, JSON.stringify(ctl));
  });
  setPlatform('MacIntel');
  const mac = press('nav', { metaKey: true });
  check('N7c MacIntel: Cmd+click opens a tab', !mac.toHtmx && !mac.prevented, JSON.stringify(mac));

  if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ' assertions)');
  process.exit(0);
}
