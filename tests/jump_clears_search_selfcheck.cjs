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
 *   J6  (QA r2-03) a jump from the WIRING tab with both trees filtered shows
 *       the state tab, clears the state tree's filter, and the toast is true
 *   J7  (QA r2-03) a tree left filtered under a box cleared on the other tab
 *       is re-synced when shown -- never a blank tree under an empty box
 *       (the real switchExplorerTab, extracted from _explorer.html)
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
  + '<div id="explorer-tabs"><span class="tree-file-tab active">state.json</span><span class="tree-file-tab">wiring.json</span></div>'
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

  // ── J4b: a hidden ANCESTOR hides its child ───────────────────────────────
  // Constructed directly rather than through a query: today's jsonTreeSearch
  // marks the non-matching node itself, so the ancestor walk is a guard whose
  // state the search does not currently produce — and a guard nothing exercises
  // is a guard nobody knows is broken. The helper's contract is what is pinned:
  // a row under a hidden branch is not on screen, whatever marked the branch.
  render();
  type('');
  await sleep(300);
  window._jumpToTreePath('explorer-tree-state', TARGET);   // materialise it
  await sleep(400);
  ok(shown(TARGET), 'J4b: the target is visible to begin with');
  const anc = node('qubits.q3');
  ok(!!anc, 'J4b: its ancestor is in the DOM');
  anc.classList.add('tree-search-hidden');
  ok(!shown(TARGET),
    'J4b: a row under a hidden branch is not on screen, even unmarked itself');
  anc.classList.remove('tree-search-hidden');

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

  // ── J6/J7 (QA r2-03): the wiring tab ─────────────────────────────────────
  // The page's own tab switcher and active-tree probe, taken from the template
  // text (never a hand copy: the pin must test what ships). They call
  // jsonTreeSearch / _activeTreeId bare, so bridge them.
  const TPL = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', '_explorer.html'), 'utf8');
  function extract(sig) {
    const i = TPL.indexOf(sig);
    if (i < 0) throw new Error('template lost: ' + sig);
    let depth = 0, j = TPL.indexOf('{', i);
    for (; j < TPL.length; j++) {
      if (TPL[j] === '{') depth++;
      else if (TPL[j] === '}' && --depth === 0) break;
    }
    return TPL.slice(i, j + 1) + ';';
  }
  window.eval(extract('window._activeTreeId = function'));
  window.eval(extract('window.switchExplorerTab = function'));
  global._activeTreeId = window._activeTreeId;
  global.jsonTreeSearch = window.jsonTreeSearch;
  const w = d.getElementById('explorer-tree-wiring');
  const WDATA = { network: { host: '10.0.0.1', cluster_name: 'c1' }, ports: { p1: { offset: 0.1 } } };
  function hiddenIn(el) { return el.querySelectorAll('.tree-search-hidden').length; }

  // J6: type on state, switch to wiring (both trees filtered), jump to a state path
  window.switchExplorerTab('state');
  render();
  window.renderJsonTree('explorer-tree-wiring', WDATA, { defaultDepth: 1, crud: true });
  type('host');
  await sleep(300);
  window.switchExplorerTab('wiring');
  await sleep(300);
  ok(c.style.display === 'none' && hiddenIn(c) > 0 && hiddenIn(w) > 0,
    'J6 fixture: wiring shown, both trees filtered by "host"');
  said.length = 0;
  const realFetch = global.fetch;
  global.fetch = window.fetch = () => Promise.resolve({ json: () => Promise.resolve({ loaded: true }) });
  window._navigateToExplorerPath(TARGET);
  await sleep(700);
  global.fetch = window.fetch = realFetch;
  ok(c.style.display !== 'none' && w.style.display === 'none',
    'J6: a jump to a state path shows the state tab');
  ok(box.value === '' && shown(TARGET) && hiddenIn(c) === 0,
    'J6: the state tree is unfiltered and the target is on screen (hidden rows=' + hiddenIn(c) + ')');
  const t6 = node(TARGET);
  ok(t6 && t6.classList.contains('tree-highlight') && !t6.classList.contains('tree-search-hidden'),
    'J6: the target is highlighted AND visible');
  ok(said.length === 1 && /host/.test(said[0]), 'J6: the toast names the dropped query (' + said.join(' | ') + ')');

  // J7a: the wiring tree the jump left filtered is re-synced when shown
  window.switchExplorerTab('wiring');
  await sleep(300);
  ok(hiddenIn(w) === 0, 'J7: a tree left filtered under an empty box is un-filtered when shown (' + hiddenIn(w) + ' hidden)');

  // J7b: the Diagnostics-free gesture: filter state, switch, clear, switch back
  window.switchExplorerTab('state');
  render();
  type('amplitude');
  await sleep(300);
  ok(hiddenIn(c) > 0, 'J7b fixture: the state tree is filtered');
  window.switchExplorerTab('wiring');
  await sleep(300);
  type('');
  await sleep(300);
  window.switchExplorerTab('state');
  await sleep(300);
  ok(hiddenIn(c) === 0, 'J7b: switching back after clearing the box on the other tab shows the state tree (' + hiddenIn(c) + ' hidden)');

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'jump_clears_search_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
}

main().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
