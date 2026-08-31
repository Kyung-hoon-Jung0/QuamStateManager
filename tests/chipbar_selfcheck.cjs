// docs/120 item 4 — the Live-Edit quick-filter chip bar, driving the REAL
// bulk-edit.js under jsdom.
//
// Customer, on the daily loop: "users go to the search box and TYPE x180, amp,
// ro, power... it's very repetitive and eats time. Put the main parameters
// there as clickable patches, multi-select of course."
//
// The design decision that makes this cheap and honest: chips are a VIEW OF
// THE QUERY STRING, not a second filter. Toggling one rewrites #bulk-search and
// lets the existing search do the work — so one chip filters both grids (they
// share that input), typing a chip's word by hand lights the chip, and there is
// no parallel state that can disagree with what the box says.
//
// The AND/OR toggle is the user's: leftmost, prominent, default AND, and on
// zero matches the popup offers OR as a one-click switch so accepting costs
// the same as finding the toggle.
//
// Run: node tests/chipbar_selfcheck.cjs   (needs jsdom)
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

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// Minimal grid mirroring _bulkedit.html: the chip bar, the shared search box,
// and a table whose columns carry the label/key/section the haystack reads.
//
// Two details are load-bearing, and getting either wrong makes every
// column-filter assertion below pass for the wrong reason:
//   - `default_on` on each column. The Properties layer hides any column
//     without it, so the whole grid renders bulk-col-hidden.
//   - `class="bulk-col-head"` on each <th>. applySearch toggles
//     `th.bulk-col-head, td[data-col-key]`, so a header lacking it is never
//     hidden no matter what the search decides.
const COLS = [
  { key: 'f_01', label: 'Qubit f01', section: 'Frequencies', default_on: true },
  { key: 'readout_amplitude', label: 'RO amp', section: 'Readout', default_on: true },
  { key: 'x180_amplitude', label: 'x180 amp', section: 'XY Drive', default_on: true },
  { key: 'z_joint_offset', label: 'Flux offset', section: 'Flux', default_on: true },
];
// `x180` is an OPERATION chip (kind "op"), which the server now leads the row
// with -- it was the term the customer named first and the one the row could
// not offer. It carries an extra class; everything below must treat it as an
// ordinary chip, which is exactly what that extra class could have broken.
const CHIPS = [['x180', 'op'], ['freq', 'kw'], ['readout', 'kw'],
               ['amp', 'kw'], ['flux', 'kw']];

const DOM = `
<div class="bulk-panel" id="bulk-panel">
  <input type="search" id="bulk-search">
  <span class="bulk-search-count" id="bulk-search-count"></span>
  <button id="bulk-dyncol-hint" hidden></button>
  <div class="bulk-chipbar" id="bulk-chipbar">
    <button class="bulk-chip-mode" id="bulk-chip-mode" aria-pressed="false" data-mode="and">AND</button>
    <span class="bulk-chip-scroll">
      ${CHIPS.map(([t, k]) => `<button class="bulk-chip bulk-chip-${k}" data-chip-term="${t}" aria-pressed="false">${t}</button>`).join('')}
    </span>
    <span class="bulk-chip-offer" id="bulk-chip-offer" hidden>
      <button id="bulk-chip-offer-yes">Yes</button>
      <button id="bulk-chip-offer-no">No</button>
    </span>
  </div>
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
global.CSS = window.CSS;          // see docs/120: bare CSS must be bridged
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

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
// Same trap as `CSS` (docs/120): bulk-edit.js guards on `window.SearchQuery`
// but CALLS bare `SearchQuery`, so without this bridge the ReferenceError would
// surface instead of either branch being taken. Bridging it is also what makes
// this selfcheck exercise the REAL docs/96 AND/OR grammar rather than the
// no-grammar fallback — which matters here, because the mode toggle emits
// exactly the pipe syntax that grammar parses.
global.SearchQuery = window.SearchQuery;
window.eval(fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));

const doc = window.document;
// mount(columns, bandMeta, dynModel, qubitMeta) — POSITIONAL. Handing it an
// object leaves COLS empty and every column-filter assertion below passes
// vacuously, which is what this comment is here to stop happening again.
window.BulkEdit.mount(COLS);

const box = doc.getElementById('bulk-search');
const chip = (t) => doc.querySelector(`.bulk-chip[data-chip-term="${t}"]`);
const modeBtn = () => doc.getElementById('bulk-chip-mode');
const offer = () => doc.getElementById('bulk-chip-offer');
function click(el) { el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); }
function typeBox(v) {
  box.value = v;
  box.dispatchEvent(new window.Event('input', { bubbles: true }));
}
const q = () => box.value.trim();
const pressed = (t) => chip(t).getAttribute('aria-pressed') === 'true';

/* ── A. a chip writes the query ──────────────────────────────────────── */
ok(q() === '', 'A1: the box starts empty');
click(chip('freq'));
ok(q() === 'freq', 'A2: clicking a chip puts its term in the shared search box');
ok(pressed('freq'), 'A3: and the chip reads as pressed');

/* ── B. multi-select, joined by the mode ─────────────────────────────── */
click(chip('amp'));
ok(q() === 'freq amp', 'B1: a second chip ANDs by default (space = AND)');
ok(pressed('freq') && pressed('amp'), 'B2: both chips stay lit — multi-select');
click(chip('freq'));
ok(q() === 'amp', 'B3: clicking again removes just that term');
ok(!pressed('freq') && pressed('amp'), 'B4: and only that chip goes dark');

/* ── C. the AND/OR toggle rewrites the join ──────────────────────────── */
click(chip('freq'));
ok(q() === 'amp freq', 'C0: two chips again');
click(modeBtn());
ok(modeBtn().textContent === 'OR', 'C1: the toggle flips to OR');
ok(q() === 'amp | freq', 'C2: OR re-joins with the docs/96 pipe grammar');
click(modeBtn());
ok(q() === 'amp freq', 'C3: back to AND');

/* ── D. free text the user typed survives every chip press ───────────── */
typeBox('amp freq q1');
click(chip('flux'));
ok(/q1/.test(q()), 'D1: hand-typed text is preserved when a chip is pressed');
ok(/flux/.test(q()), 'D2: and the new chip term is there too');
click(chip('flux'));
ok(/q1/.test(q()) && !/flux/.test(q()), 'D3: and preserved when one is released');

/* ── E. the box is the truth: typing lights the chip ─────────────────── */
typeBox('readout');
ok(pressed('readout'), 'E1: typing a chip word by hand lights that chip');
typeBox('');
ok(!pressed('readout'), 'E2: clearing the box un-lights it');
ok(!pressed('amp') && !pressed('freq'), 'E3: no chip survives an emptied box');

/* ── F. chips actually filter (they run the real search) ─────────────── */
typeBox('');
click(chip('readout'));
const shown = () => Array.from(doc.querySelectorAll('#bulk-table thead th[data-col-key]'))
  .filter(th => !th.classList.contains('bulk-search-hidden')).length;
ok(shown() === 1, 'F1: one chip filters the columns down to its section');
click(chip('readout'));
ok(shown() === 4, 'F2: releasing it restores every column');

/* ── G. the zero-match OR offer ──────────────────────────────────────── */
click(chip('freq')); click(chip('readout'));
ok(q() === 'freq readout', 'G0: two chips that share no column, in AND');
ok(shown() === 0, 'G1: AND of two disjoint bands matches nothing');
ok(offer().hidden === false, 'G2: that is when OR is offered');
click(doc.getElementById('bulk-chip-offer-yes'));
ok(modeBtn().textContent === 'OR', 'G3: Yes switches the mode in ONE click');
ok(q() === 'freq | readout', 'G4: and re-joins the query');
ok(shown() === 2, 'G5: the union now matches both bands');
ok(offer().hidden === true, 'G6: the offer withdraws once it is moot');

/* ── H. the offer never appears where it could not help ──────────────── */
typeBox('');
click(chip('freq'));
ok(offer().hidden === true, 'H1: never offered for a single chip');
typeBox('zzzznothing');
ok(offer().hidden === true, 'H2: nor for free text that matches nothing');

/* ── I. an operation chip is an ordinary chip ────────────────────────
   docs/120 review finding 1. `x180` is what the customer types most and the
   row could not offer it, because terms came from curated words and SECTION
   names while x180 is an operation. The server harvests it now and marks it
   `bulk-chip-op` so it reads as a name rather than a property word — a purely
   visual distinction that must not reach the behaviour. */
typeBox('');
// Block G left the toggle on OR; this block is about AND.
if (modeBtn().textContent === 'OR') click(modeBtn());
click(chip('freq'));   // clear the H-block selection
click(chip('freq'));
ok(q() === '' && modeBtn().textContent === 'AND', 'I0: starting clean, in AND');
const x180 = chip('x180');
ok(!!x180 && x180.className.indexOf('bulk-chip-op') !== -1,
   'I1: the operation chip carries its own class');
click(x180);
ok(q() === 'x180', 'I2: ...and writes the plain term, with no class artefact');
ok(x180.getAttribute('aria-pressed') === 'true', 'I3: it toggles like any chip');
ok(shown() === 1, 'I4: and filters — x180 amp is the one column it names');
click(chip('amp'));
ok(q() === 'x180 amp', 'I5: the customer’s own sentence, as two clicks');
ok(shown() === 1, 'I6: AND of an operation and a property still matches it');
click(x180); click(chip('amp'));
ok(q() === '', 'I7: releasing both empties the box');

process.exit(fails ? 1 : 0);
