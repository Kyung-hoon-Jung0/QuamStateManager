/* docs/183 — the injector that replaced the CSP-dead `hx-vals` attribute,
 * EXECUTED rather than grepped.
 *
 * The Python pins read app.js as text, which cannot tell you whether the
 * listener actually fires, whether it finds the form from the element htmx
 * reports, or whether it leaves every other request alone. This does.
 *
 * Pins:
 *   I1  a request from inside a marked form gains freq_sync + expect_chip
 *   I2  the element htmx reports is the INPUT, not the form — the listener has
 *       to walk up, which is the whole reason it uses closest()
 *   I3  an unmarked request is untouched (the injector is not a global rewriter)
 *   I4  the toolbar's f01↔RF toggle reaches the server: "0" means off
 *   I5  no chip token yet ⇒ an empty string, never the literal "undefined",
 *       which the server would read as a mismatched chip and 409
 *
 * Run: node tests/inspector_inject_selfcheck.cjs   (needs jsdom)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const HTML = '<!doctype html><html><body>'
  + '<form class="inline-edit fh-host" id="marked" data-inject="chip-freq">'
  + '  <input type="hidden" name="dot_path" value="qubits.q1.anharmonicity">'
  + '  <input type="text" class="edit-input" id="inp" value="1">'
  + '</form>'
  + '<form id="plain"><input type="text" id="inp2" value="2"></form>'
  + '<div id="status-bar"></div></body></html>';

const dom = new JSDOM(HTML, { url: 'http://localhost/qubit/q1', pretendToBeVisual: true });
const { window } = dom;
const d = window.document;
global.window = window; global.document = d; global.CSS = window.CSS;
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
window.openConfigManual = function () {};
window.Element.prototype.scrollIntoView = function () {};

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

// What htmx does: fire configRequest with the triggering element and a
// parameters object the listeners may add to.
function config(elt) {
  const detail = { elt: elt, parameters: {}, path: '/qubit/q1/edit', verb: 'post' };
  const ev = new window.CustomEvent('htmx:configRequest',
    { bubbles: true, cancelable: true, detail: detail });
  d.dispatchEvent(ev);
  return detail.parameters;
}

// ── I1 / I2: from the INPUT, which is what htmx reports ────────────────────
window.__chipToken = 'chip-abc';
let p = config(d.getElementById('inp'));
ok(p.expect_chip === 'chip-abc',
  'I1/I2: the chip token is injected from inside the form — ' + JSON.stringify(p));
ok(p.freq_sync === '1' || p.freq_sync === true || p.freq_sync === '1',
  'I1: and the freq-sync flag — ' + JSON.stringify(p.freq_sync));

// the form itself works too (a submit can report either)
p = config(d.getElementById('marked'));
ok(p.expect_chip === 'chip-abc', 'I2: …and from the form element itself');

// ── I3: everything else is untouched ───────────────────────────────────────
p = config(d.getElementById('inp2'));
ok(Object.keys(p).length === 0,
  'I3: an unmarked request gains nothing — ' + JSON.stringify(p));

// ── I4: the toggle actually reaches the server ─────────────────────────────
const realFlag = window.freqSyncFlag;
window.freqSyncFlag = function () { return '0'; };
p = config(d.getElementById('inp'));
ok(String(p.freq_sync) === '0',
  'I4: freq_sync=0 when the toolbar toggle is off — ' + JSON.stringify(p.freq_sync));
window.freqSyncFlag = realFlag;

// ── I5: no token yet is an EMPTY string ────────────────────────────────────
// `String(undefined)` is "undefined", which the server compares against the
// chip's real token and refuses with a 409 — a broken edit instead of an
// ungated one.
delete window.__chipToken;
p = config(d.getElementById('inp'));
ok(p.expect_chip === '',
  'I5: an absent token is "" and not the string "undefined" — '
  + JSON.stringify(p.expect_chip));

console.log(fails ? 'FAILED (' + fails + ')'
  : 'inspector_inject_selfcheck ok (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
