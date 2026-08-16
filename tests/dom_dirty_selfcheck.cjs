// docs/120 item 8 / docs/122 — the ONE predicate that stands between an armed
// auto-pull and a column the user typed but has not applied yet.
//
// The policy table's third row ("pull on, replace OFF, local edits present")
// says: do NOT pull, raise the banner, let the user choose. The server cannot
// see those edits — a fill-down or a pasted column lives only in the DOM until
// Apply — so the CLIENT has to report them, and `/auto-sync/pull` is only safe
// because it does.
//
// It did not. The first cut looked for `.bulk-cell.bulk-dirty`; the class the
// grid actually sets is `dirty` (`_markCellDirty`, in bulk-edit.js AND
// pair-edit.js), and `bulk-dirty` exists only as the id of the COUNTER span,
// `#bulk-dirty-count`. The fallback called `BulkEdit.hasUnsaved`, which does
// not exist anywhere in the repo. So `dom_dirty` was NEVER sent, and a typed
// column was destroyed with no prompt and no snapshot.
//
// Nothing failed, because the pytest pin posts `dom_dirty=1` by hand — it
// proves the SERVER honours the flag, never that the client raises it. This
// file closes that gap from both ends:
//   A. drive the real bulk-edit.js and assert the marker it sets
//   B. assert app.js looks for exactly that marker
//
// Run: node tests/dom_dirty_selfcheck.cjs   (needs jsdom)
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
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const COLS = [
  { key: 'f_01', label: 'Qubit f01', section: 'Frequencies', default_on: true },
  { key: 'x180_amplitude', label: 'x180 amp', section: 'XY Drive', default_on: true },
];
const DOM = `
<div class="bulk-panel" id="bulk-panel">
  <input type="search" id="bulk-search">
  <span class="bulk-search-count" id="bulk-search-count"></span>
  <span id="bulk-dirty-count"></span>
  <table class="bulk-table" id="bulk-table">
    <thead><tr class="bulk-header-row">
      <th class="bulk-corner"></th>
      ${COLS.map(c => `<th class="bulk-col-head" data-col-key="${c.key}">${c.label}</th>`).join('')}
    </tr></thead>
    <tbody>
      <tr data-qubit="q1">
        <td class="bulk-rowhead">q1</td>
        ${COLS.map(c => `<td data-col-key="${c.key}"><input class="bulk-cell" data-dot-path="qubits.q1.${c.key}" data-orig="1" value="1"></td>`).join('')}
      </tr>
    </tbody>
  </table>
</div>`;

const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.Event = window.Event;
global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.fetch = global.fetch = () => Promise.resolve({
  status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve(''),
});
window.htmx = global.htmx = { ajax() { return Promise.resolve(); }, trigger() {}, process() {} };

window.eval(read('search-query.js'));
global.SearchQuery = window.SearchQuery;
window.eval(read('bulk-edit.js'));

const doc = window.document;
window.BulkEdit.mount(COLS);

const cell = doc.querySelector('.bulk-cell[data-dot-path="qubits.q1.x180_amplitude"]');

/* ── A. the marker the grid REALLY sets ──────────────────────────────── */
ok(!!cell, 'A0: the grid mounted with an editable cell');
ok(doc.querySelectorAll('.bulk-cell.dirty').length === 0,
  'A1: nothing is dirty before typing');

cell.value = '0.42';
cell.dispatchEvent(new window.Event('input', { bubbles: true }));

ok(cell.classList.contains('dirty'),
  'A2: typing into a cell marks it `dirty` — the real class, from _markCellDirty');
ok(doc.querySelectorAll('.bulk-cell.dirty').length === 1,
  'A3: and it is findable from the document, which is how the poller must see it');

/* The exact predicate app.js runs. This is the assertion whose absence let a
   typed column be destroyed: the class list was right, the query was not. */
ok(!!doc.querySelector('.bulk-cell.dirty'),
  'A4: `.bulk-cell.dirty` matches — the selector auto-sync reports dirt with');
ok(!doc.querySelector('.bulk-cell.bulk-dirty'),
  'A5: `.bulk-cell.bulk-dirty` matches NOTHING — the selector that shipped');
ok(!doc.querySelector('.bulk-cell[data-dirty="1"]'),
  'A6: nor does data-dirty="1" (that marker belongs to the wizard, generate.js)');
ok(typeof window.BulkEdit.hasUnsaved !== 'function',
  'A7: BulkEdit.hasUnsaved does not exist — the fallback was a no-op too');

/* Committing must clear it, or an armed session could never pull again. */
cell.value = '1';
cell.dispatchEvent(new window.Event('input', { bubbles: true }));
ok(!doc.querySelector('.bulk-cell.dirty'),
  'A8: restoring the original value clears the marker (a pull is allowed again)');

/* ── B. app.js asks for exactly that ─────────────────────────────────── */
{
  const src = read('app.js');
  const i = src.indexOf('/auto-sync/pull');
  ok(i !== -1, 'B0: the auto-pull call site is in app.js');
  const block = src.slice(Math.max(0, i - 1400), i + 200);
  ok(/querySelector\(\s*'\.bulk-cell\.dirty'\s*\)/.test(block),
    'B1: it looks for `.bulk-cell.dirty`');
  ok(!/bulk-cell\.bulk-dirty/.test(block),
    'B2: and no longer for the counter-id class that can never match');
  // Strip comment lines first: the comment above the fix names the dead API on
  // purpose, so the next reader knows why it is not there.
  const code = block.split(/\r?\n/).filter(function (l) {
    return !/^\s*(\/\/|\*|\/\*)/.test(l);
  }).join(' ');
  ok(!/hasUnsaved/.test(code),
    'B3: the non-existent hasUnsaved fallback is gone, not left as dead armor');
  ok(/dom_dirty=1/.test(block),
    'B4: a dirty grid still turns into ?dom_dirty=1 on the pull');
}

process.exit(fails ? 1 : 0);
