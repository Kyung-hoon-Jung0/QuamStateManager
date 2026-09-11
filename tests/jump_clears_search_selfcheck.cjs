/* docs/180 — a jump must actually SHOW the field. Customer report, on-site:
 *
 *   "json tree view에서 검색어로 search하면서 보다가, diagnostic에서 issue
 *    때문에 go to field 하면, 여전히 검색어 그대로 mode여서 go to field로
 *    보여야할것이 안보인다."
 *
 * The tree's search survives navigation — PaneState parks the pane (docs/110)
 * and `_explorer.html` re-applies the box on a tab switch. Right for "go back
 * to what I was doing", wrong for "take me to THIS field": Go to field landed
 * on a tree still filtered by the user's query, so the row it had just promised
 * to show was not on screen, and nothing said why.
 *
 * Pins, against the real shipped app.js:
 *   J1  a target the query HIDES: the filter is cleared and the row is shown
 *   J2  …and the page says so, naming the query it dropped
 *   J3  a target the query already MATCHES: the filter is kept (clearing it
 *       would throw away the user's own context for nothing)
 *   J4  no query at all: nothing is cleared, nothing is announced
 *   J5  the box is driven the way a person drives it, so the chip bar cannot be
 *       left claiming a filter that is no longer applied
 *
 * Run: node tests/jump_clears_search_selfcheck.cjs   (needs jsdom)
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
  + '<div class="explorer-pane">'
  + '<input type="text" id="explorer-search" oninput="explorerSearch(this.value)">'
  + '<div id="explorer-chipbar"></div>'
  + '<div id="explorer-tree-state" class="json-tree"></div>'
  + '<div id="explorer-tree-wiring" class="json-tree" style="display:none"></div>'
  + '</div><div id="status-bar"></div></body></html>';

const dom = new JSDOM(HTML, { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
const d = window.document;
global.window = window; global.document = d; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
// Node 24 makes `global.navigator` a getter-only property; the code under
// test reads it off `window`, which is what matters.
global.location = window.location;
// jsdom's own localStorage is real and getter-only on the window; the code
// under test only reads/writes it, so use it rather than replacing it.
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
// jsdom has no layout, so neither of these exists there. The code under test
// calls both on the way to showing a row; without them the jump throws before
// it ever reaches the decision this file is about.
window.Element.prototype.scrollIntoView = function () {};
// `_activeTreeId` is defined by _explorer.html, not by app.js — the two trees
// share one search box and the page decides which is showing.
window._activeTreeId = function () {
  var el = d.getElementById('explorer-tree-state');
  return (el && el.style.display !== 'none') ? 'explorer-tree-state'
                                             : 'explorer-tree-wiring';
};

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

// What the page says, captured rather than rendered — the harness is about the
// decision, not the toast's markup. AFTER the eval: app.js defines its own
// window.showToast, and a spy installed first is simply overwritten (which is
// how this assertion first read `undefined`).
const said = [];
window.showToast = function (msg) { said.push(String(msg)); };

// A chip whose "amplitude" leaves are what the user was searching, plus the
// unrelated leaf Diagnostics is about to send them to.
const DATA = { qubits: {} };
for (let q = 1; q <= 4; q++) {
  DATA.qubits['q' + q] = {
    xy: { amplitude: 0.1 * q, detuning: 1e6 * q },
    T1: 2e-5 * q
  };
}
const TARGET = 'qubits.q3.T1';          // no "amplitude" anywhere in this path
const MATCHING = 'qubits.q3.xy.amplitude';

const c = d.getElementById('explorer-tree-state');
const box = d.getElementById('explorer-search');

function render() {
  window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1, crud: true });
}
// jsdom does not COMPILE inline handler attributes (no runScripts), so the
// page's own `oninput="explorerSearch(this.value)"` would never fire here. Wire
// it as a real listener — that is exactly what a browser does with the
// attribute, and it is what makes the fix's own dispatch meaningful below.
box.addEventListener('input', function () { window.explorerSearch(box.value); });

function type(q) {
  box.value = q;
  box.dispatchEvent(new window.Event('input', { bubbles: true }));
}
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function shown(p) { return window._treePathVisible('explorer-tree-state', p); }
function node(p) { return c.querySelector('.tree-node[data-path="' + p + '"]'); }

async function main() {
  // ── J1/J2: the query hides the target ────────────────────────────────────
  render();
  type('amplitude');
  await sleep(300);
  ok(!shown(TARGET),
    'fixture: with "amplitude" in the box, the target really is filtered away');
  ok(shown(MATCHING), 'fixture: a matching leaf really is on screen');

  said.length = 0;
  window._jumpToTreePath('explorer-tree-state', TARGET);
  await sleep(400);

  ok(box.value === '', 'J1: the filter that was in the way is cleared');
  ok(shown(TARGET), 'J1: and the field the jump promised is on screen');
  const t = node(TARGET);
  ok(t && t.classList.contains('tree-highlight'),
    'J1: …highlighted, which is what "go to field" means');
  ok(said.length === 1 && /amplitude/.test(said[0]) && /cleared/i.test(said[0]),
    'J2: the page says the search was dropped, and which one (' + said[0] + ')');

  // ── J3: the query already shows the target ───────────────────────────────
  render();
  type('amplitude');
  await sleep(300);
  said.length = 0;
  window._jumpToTreePath('explorer-tree-state', MATCHING);
  await sleep(400);
  ok(box.value === 'amplitude',
    'J3: a target the query already matches keeps the user’s filter');
  ok(said.length === 0, 'J3: …and says nothing, because nothing was taken away');
  ok(shown(MATCHING), 'J3: the field is on screen either way');

  // ── J4: no query ─────────────────────────────────────────────────────────
  render();
  type('');
  await sleep(300);
  said.length = 0;
  window._jumpToTreePath('explorer-tree-state', TARGET);
  await sleep(400);
  ok(box.value === '' && said.length === 0,
    'J4: with no search there is nothing to clear and nothing to announce');
  ok(shown(TARGET), 'J4: the jump still works');

  // ── J5: the box is driven, not assigned ──────────────────────────────────
  // The chip bar repaints from the box's own `input` event. Setting `.value`
  // silently would leave a chip lit for a filter that is no longer applied.
  render();
  let inputs = 0;
  box.addEventListener('input', function () { inputs++; });
  type('amplitude');
  await sleep(300);
  inputs = 0;
  window._jumpToTreePath('explorer-tree-state', TARGET);
  await sleep(400);
  ok(inputs >= 1,
    'J5: clearing fires the box’s own input event, so the chip bar follows');

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'jump_clears_search_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
}

main().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
