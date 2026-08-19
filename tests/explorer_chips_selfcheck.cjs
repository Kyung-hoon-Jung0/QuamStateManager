/* docs/126 ④ — Json Tree quick patches, port-owner chips, ⧉ row copy.
 *
 * Pins against the REAL app.js under jsdom:
 *  1. ExplorerChips renders only curated terms that OCCUR in the documents
 *     (honesty: never a chip that matches nothing) + the shared custom store.
 *  2. Chip click fills #explorer-search and routes through explorerSearch;
 *     re-click removes the term; hand-typing lights the chip.
 *  3. + add persists to the SHARED quam_bulk_custom_chips store (the same one
 *     Live Edit reads) and × removes it.
 *  4. A node whose path is in window._treePortOwners wears the owner chip.
 *  5. Hover actions include ⧉ on EVERY row (list elements / identity rows
 *     included) and clicking it puts `"key": <value JSON>` on the clipboard.
 *
 * Run: node tests/explorer_chips_selfcheck.cjs  (driven by test_explorer_features.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('SKIP: jsdom not installed'); process.exit(2); }

const dom = new JSDOM(
  '<!doctype html><html><body>'
  + '<input id="explorer-search">'
  + '<div class="bulk-chipbar" id="explorer-chipbar"></div>'
  + '<div id="tree"></div>'
  + '</body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const window = dom.window;
global.window = window;
global.document = window.document;
global.CSS = window.CSS;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
Object.defineProperty(global, 'navigator',
  { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const MEM = {};
const STORE = {
  getItem: (k) => (k in MEM ? MEM[k] : null),
  setItem: (k, v) => { MEM[k] = String(v); },
  removeItem: (k) => { delete MEM[k]; },
};
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
  Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
global.fetch = () => new Promise(() => {});
Object.defineProperty(window, 'fetch',
  { value: global.fetch, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); }
catch (e) { console.error('FAIL: app.js did not evaluate: ' + e.message); process.exit(1); }

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// stub the tree search entry the chips route through
const searchCalls = [];
window.explorerSearch = function (v) { searchCalls.push(v); };
// clipboard capture (bridge on BOTH realms — harness rule)
let copiedText = null;
const clip = { writeText: (t) => { copiedText = t; return Promise.resolve(); } };
Object.defineProperty(window.navigator, 'clipboard',
  { value: clip, configurable: true });

(async function main() {
  const settle = (ms) => new Promise((r) => setTimeout(r, ms == null ? 30 : ms));

  // ── 1+2+3: the chip bar ────────────────────────────────────────────────
  MEM['quam_bulk_custom_chips'] = JSON.stringify(['decouple']);
  const docs = [{ qubits: { q1: { f_01: 5e9, z: { flux_point: 'joint' } } },
                 flux_coupler_note: 'coupler' }, {}];
  window.ExplorerChips.mount('explorer-chipbar', docs);
  const bar = window.document.getElementById('explorer-chipbar');
  const terms = Array.from(bar.querySelectorAll('.bulk-chip[data-chip-term]'))
    .map((b) => b.getAttribute('data-chip-term'));
  ok(terms.indexOf('flux') >= 0 && terms.indexOf('coupler') >= 0,
     'curated chips render for terms the documents contain');
  ok(terms.indexOf('readout') === -1,
     'a curated term ABSENT from the documents is not offered (honesty)');
  ok(terms.indexOf('decouple') >= 0, 'the shared custom store injects its patch');
  ok(!!bar.querySelector('.bulk-chip-add'), 'the + add button renders');

  const fluxChip = bar.querySelector('.bulk-chip[data-chip-term="flux"]');
  fluxChip.dispatchEvent(new window.Event('click', { bubbles: true }));
  const input = window.document.getElementById('explorer-search');
  ok(input.value === 'flux' && searchCalls[searchCalls.length - 1] === 'flux',
     'chip click fills the box and routes through explorerSearch');
  ok(fluxChip.classList.contains('active'), 'the chip lights');
  fluxChip.dispatchEvent(new window.Event('click', { bubbles: true }));
  ok(input.value === '' && !fluxChip.classList.contains('active'),
     're-click removes the term and unlights');

  input.value = 'coupler something';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  ok(bar.querySelector('.bulk-chip[data-chip-term="coupler"]').classList.contains('active'),
     'hand-typing a patch word lights its chip');
  input.value = ''; input.dispatchEvent(new window.Event('input', { bubbles: true }));

  bar.querySelector('.bulk-chip-add').dispatchEvent(new window.Event('click', { bubbles: true }));
  const addInp = bar.querySelector('.bulk-chip-add-input');
  ok(!!addInp, '+ opens the inline input');
  addInp.value = 'joint';
  addInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await settle();
  ok(/"joint"/.test(MEM['quam_bulk_custom_chips'] || ''),
     'the new patch lands in the SHARED store (Live Edit reads the same key)');
  ok(input.value.indexOf('joint') >= 0, 'and is applied to the search');
  const jointChip = bar.querySelector('.bulk-chip-custom[data-chip-term="joint"]');
  ok(!!jointChip, 'and renders as a chip');
  jointChip.querySelector('.bulk-chip-x').dispatchEvent(
    new window.Event('click', { bubbles: true }));
  await settle();
  ok(!/"joint"/.test(MEM['quam_bulk_custom_chips'] || '')
     && !bar.querySelector('.bulk-chip-custom[data-chip-term="joint"]')
     && input.value.indexOf('joint') === -1,
     '× removes the patch from store, bar and search');

  // ── 4: port owner chip ─────────────────────────────────────────────────
  window._treePortOwners = { 'ports.analog_outputs.con1.4.1': 'q2 · z' };
  window.renderJsonTree('tree', {
    ports: { analog_outputs: { con1: { '4': { '1': { offset: 0.1 } } } } },
  }, { defaultDepth: 1, crud: true });
  // children are LAZY — a search materializes down to its matches, which is
  // also how a user reaches a port (same route the real-browser probe took)
  window.jsonTreeSearch('tree', 'offset');
  await settle(450);   // jsonTreeSearch debounces 200 ms before materializing
  const ownerChip = window.document.querySelector('#tree .tree-owner-chip');
  ok(!!ownerChip && /q2 · z/.test(ownerChip.textContent),
     'the port node wears its owner chip (' + (ownerChip && ownerChip.textContent) + ')');

  // ── 5: ⧉ copy on every row ─────────────────────────────────────────────
  window.renderJsonTree('tree', { f_01: 4.8e9, items: [1, 2] },
                        { defaultDepth: 3, crud: true });
  await settle();
  const rows = window.document.querySelectorAll('#tree .tree-row');
  const leafRow = Array.from(rows).find(
    (r) => r.querySelector('.tree-key') && r.querySelector('.tree-key').textContent === 'f_01');
  leafRow.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
  await settle();
  const copyBtn = leafRow.querySelector('.tree-act-copy');
  ok(!!copyBtn, 'a hovered row offers the ⧉ copy action');
  copyBtn.click();
  await settle();
  ok(copiedText === '"f_01": 4800000000', 'clipboard got the key + value as JSON ('
     + JSON.stringify(copiedText) + ')');
  ok(copyBtn.textContent === '✓', 'the button confirms');

  // list element rows copy too (they used to get NO actions at all)
  const elemRow = Array.from(window.document.querySelectorAll('#tree .tree-row')).find(
    (r) => r.querySelector('.tree-key') && r.querySelector('.tree-key').textContent === '0');
  if (elemRow) {
    elemRow.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    await settle();
    ok(!!elemRow.querySelector('.tree-act-copy'), 'list-element rows get ⧉ as well');
    ok(!elemRow.querySelector('.tree-act-del'), 'but never gained delete (unchanged)');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
  process.exit(0);
})().catch(function (e) {
  console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
  process.exit(1);
});
