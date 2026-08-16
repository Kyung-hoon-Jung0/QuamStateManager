// docs/120 item 3 — the search-syntax help panel, driving the REAL app.js
// under jsdom.
//
// The customer report: "whenever a user types something in the search box the
// syntax always shows up, so the user has to scroll down to see the folder
// list". Two independent defects compounded, in two parallel implementations
// (the Datasets page's id-based handler and the generic class/data-attribute
// one the sidebar filter uses):
//
//   1. the panel opened ITSELF on the first focus of the input per browser
//      session (a sessionStorage flag), so it appeared the instant a user
//      began typing;
//   2. the ? button was OPEN-ONLY -- clicking it again did nothing, so the
//      same control a user opened the panel with could not put it away.
//
// It hurts far more in the sidebar because that copy of the panel is
// `position: static` (a narrow scrolling sidebar would clip an absolute
// popover), so it renders INLINE and pushes the experiment tree down.
//
// What must hold now:
//   - focusing or typing in either search box opens NOTHING
//   - the ? toggles: closed -> open -> closed
//   - the x still closes
//   - the Datasets dead-click guard still closes on table engagement
//   - clicking an example still pastes into its own input and fires `input`
//
// Run: node tests/search_help_selfcheck.cjs   (needs jsdom)
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

// Mirrors both real shells: the Datasets page (_datasets.html, id-based) and
// the sidebar filter (base.html, class + data-search-help based).
const DOM = `
<div class="table-filter ds-search-wrap">
  <input type="search" id="dataset-search">
  <button type="button" id="ds-search-help-toggle" class="ds-search-help-btn">?</button>
</div>
<div id="ds-search-help" class="ds-search-help-panel" hidden>
  <button type="button" id="ds-search-help-close" class="ds-search-help-close">&times;</button>
  <button class="ds-help-example" data-example="name:iq_blob">name:iq_blob</button>
</div>
<div id="datasets-scroll"><div id="a-run-row">run</div></div>

<div class="sidebar-filter">
  <div class="ds-search-wrap">
    <textarea id="sidebar-filter-input" class="search-help-input"
              data-search-help="sidebar-search-help"></textarea>
    <button type="button" class="ds-search-help-btn search-help-toggle"
            data-search-help="sidebar-search-help">?</button>
  </div>
  <div id="sidebar-search-help" class="ds-search-help-panel" hidden>
    <button type="button" class="ds-search-help-close search-help-close"
            data-search-help="sidebar-search-help">&times;</button>
    <button class="ds-help-example search-help-example"
            data-search-help-input="sidebar-filter-input"
            data-example="rabi | ramsey">rabi | ramsey</button>
  </div>
  <div id="sidebar-tree">the folder list</div>
</div>`;

const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
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

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const doc = window.document;
const dsInput = doc.getElementById('dataset-search');
const dsPanel = doc.getElementById('ds-search-help');
const dsToggle = doc.getElementById('ds-search-help-toggle');
const dsClose = doc.getElementById('ds-search-help-close');
const sbInput = doc.getElementById('sidebar-filter-input');
const sbPanel = doc.getElementById('sidebar-search-help');
const sbToggle = doc.querySelector('.search-help-toggle');
const sbClose = doc.querySelector('.search-help-close');

function click(el) { el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); }
function focus(el) { el.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true })); }
function type(el, v) {
  el.value = v;
  el.dispatchEvent(new window.Event('input', { bubbles: true }));
}

/* ── A. nothing opens itself ─────────────────────────────────────────── */
ok(dsPanel.hidden === true, 'A1: datasets panel starts closed');
ok(sbPanel.hidden === true, 'A1: sidebar panel starts closed');

focus(dsInput);
ok(dsPanel.hidden === true, 'A2: focusing the datasets search does NOT open the panel');
focus(sbInput);
ok(sbPanel.hidden === true, 'A2: focusing the sidebar filter does NOT open the panel');

// The actual report: typing must never bury the tree.
type(dsInput, 'r');
type(dsInput, 'ra');
ok(dsPanel.hidden === true, 'A3: typing in the datasets search opens nothing');
type(sbInput, 'r');
type(sbInput, 'ra');
ok(sbPanel.hidden === true, 'A3: typing in the sidebar filter opens nothing (the tree stays put)');

// Refocusing after a session of use is still silent (the old flag would have
// allowed exactly one open; there must now be zero).
focus(dsInput); focus(dsInput);
ok(dsPanel.hidden === true, 'A4: repeat focus never opens the datasets panel');
focus(sbInput); focus(sbInput);
ok(sbPanel.hidden === true, 'A4: repeat focus never opens the sidebar panel');

/* ── B. the ? is a real toggle ───────────────────────────────────────── */
click(dsToggle);
ok(dsPanel.hidden === false, 'B1: ? opens the datasets panel');
click(dsToggle);
ok(dsPanel.hidden === true, 'B2: ? again CLOSES it (the reported bug)');
click(dsToggle);
ok(dsPanel.hidden === false, 'B3: ? opens it again — a real toggle, not one-shot');

click(sbToggle);
ok(sbPanel.hidden === false, 'B1: ? opens the sidebar panel');
click(sbToggle);
ok(sbPanel.hidden === true, 'B2: ? again CLOSES the sidebar panel');
click(sbToggle);
ok(sbPanel.hidden === false, 'B3: sidebar ? toggles back open');

/* ── C. the x still closes (both) ────────────────────────────────────── */
click(dsClose);
ok(dsPanel.hidden === true, 'C1: x closes the datasets panel');
click(sbClose);
ok(sbPanel.hidden === true, 'C1: x closes the sidebar panel');

/* ── D. the Datasets dead-click guard survives ───────────────────────── */
// The datasets panel floats over the run list (z-index 30), so engaging the
// table must dismiss it. The sidebar panel is inline and has no such guard.
click(dsToggle);
ok(dsPanel.hidden === false, 'D0: datasets panel open again');
click(doc.getElementById('a-run-row'));
ok(dsPanel.hidden === true, 'D1: clicking a run row still dismisses the datasets panel');

/* ── E. click-to-paste still works ───────────────────────────────────── */
let dsFired = 0;
dsInput.addEventListener('input', () => { dsFired++; });
click(doc.querySelector('#ds-search-help .ds-help-example'));
ok(dsInput.value === 'name:iq_blob', 'E1: datasets example pastes into #dataset-search');
ok(dsFired > 0, 'E1: pasting fires an input event so the filter re-runs');

let sbFired = 0;
sbInput.addEventListener('input', () => { sbFired++; });
click(doc.querySelector('#sidebar-search-help .search-help-example'));
ok(sbInput.value === 'rabi | ramsey', 'E2: sidebar example pastes into its own input');
ok(sbFired > 0, 'E2: pasting fires an input event');

/* ── F. the two panels are independent ───────────────────────────────── */
dsPanel.hidden = true; sbPanel.hidden = true;
click(dsToggle);
ok(dsPanel.hidden === false && sbPanel.hidden === true,
  'F1: opening the datasets panel leaves the sidebar panel closed');
click(sbToggle);
ok(sbPanel.hidden === false && dsPanel.hidden === false,
  'F2: the sidebar toggle does not touch the datasets panel');

process.exit(fails ? 1 : 0);
