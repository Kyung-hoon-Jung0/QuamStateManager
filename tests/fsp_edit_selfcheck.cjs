// docs/120 item 7 — the FSP compensation popup's amplitudes are EDITABLE.
//
// Customer: "when you change FSP, the option to change the related amps comes
// up too -- that itself is really good. The problem is the user can't edit the
// amps AT ALL: it's accept, or discard and update FSP only. Users want to be
// able to adjust the amps a little and then update."
//
// The popup used to render the compensated value as plain text, so the only
// choices were `comp` (take SM's numbers exactly) or `solo` (change FSP and
// leave every amplitude alone). Now each row is an input.
//
// The invariants that matter:
//   - the seeded value is the COMPUTED one, and `a.new` keeps it forever so
//     the per-row reset has something to return to
//   - editing recomputes the Δ, the per-row clip mark AND the header clip
//     warning, because that warning is a claim about what will be WRITTEN, not
//     about the proposal that arrived with the 409
//   - a cell that isn't a number BLOCKS the apply rather than silently falling
//     back to the computed value
//   - blank means "use the computed value" -- clearing a field is not a request
//     to write nothing
//   - departing from P = FSP + 20*log10|amp| is said out loud
//   - a plan nobody edited serialises EXACTLY as before, so the five existing
//     call sites and their pins are untouched
//
// Run: node tests/fsp_edit_selfcheck.cjs   (needs jsdom)
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

const dom = new JSDOM('<!doctype html><html><body></body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// See docs/120: `CSS` must be bridged or `(window.CSS && CSS.escape)` throws.
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

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const doc = window.document;

function mkPlan() {
  return {
    port: 'con1/3/4', fsp_old: 12, fsp_new: 6,
    factor: Math.pow(10, 6 / 20), clip_count: 0, skipped: [],
    amps: [
      { path: 'qubits.q1.xy.operations.x180.amplitude',
        old: 0.2, new: 0.4, channel: 'q1.xy', op: 'x180', clips: false },
      { path: 'qubits.q2.xy.operations.x180.amplitude',
        old: 0.1, new: 0.2, channel: 'q2.xy', op: 'x180', clips: false },
    ],
  };
}
function openPopup(plan) {
  const seen = [];
  window._openFspPopup(plan, (mode, p) => seen.push({ mode, plan: p }));
  return seen;
}
const card = () => doc.querySelector('.fsp-card');
const inputs = () => Array.from(doc.querySelectorAll('.fsp-amp-input'));
const compBtn = () => Array.from(card().querySelectorAll('button'))
  .find(b => /compensate/i.test(b.textContent));
const warn = () => card().querySelector('.fsp-warn');
const editNote = () => card().querySelector('.fsp-edited-note');
function type(el, v) {
  el.value = v;
  el.dispatchEvent(new window.Event('input', { bubbles: true }));
}

/* ── A. the rows are editable, seeded with the computed value ────────── */
let plan = mkPlan();
let seen = openPopup(plan);
ok(inputs().length === 2, 'A1: one input per compensated amplitude');
ok(inputs()[0].value === '0.4' && inputs()[1].value === '0.2',
  'A2: seeded with the COMPUTED value, raw (no thousands separators)');
ok(compBtn() && !compBtn().disabled, 'A3: apply is enabled with valid values');
ok(editNote().style.display === 'none', 'A4: no "edited" note before any edit');
ok(warn().style.display === 'none', 'A5: no clip warning when nothing clips');

/* ── B. an untouched plan serialises exactly as before ───────────────── */
let ups = window._fspCompUpdates(plan);
ok(ups.length === 2 && ups[0].value === '0.4' && ups[1].value === '0.2',
  'B1: un-edited plan yields the computed values (byte-identical legacy)');

/* ── C. editing a row is what actually gets written ──────────────────── */
type(inputs()[0], '0.37');
ok(editNote().style.display !== 'none', 'C1: an override says so, out loud');
ups = window._fspCompUpdates(plan);
ok(ups[0].value === '0.37', 'C2: the EDITED value is what the resend carries');
ok(ups[1].value === '0.2', 'C3: untouched rows keep the computed value');
ok(plan.amps[0].new === 0.4,
  'C4: a.new still holds the computed value (reset has a target)');

/* ── D. the reset returns the row to the computed value ──────────────── */
const resetBtn = card().querySelectorAll('.fsp-amp-reset')[0];
ok(resetBtn.style.visibility === 'visible', 'D1: the reset shows on an edited row');
resetBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(inputs()[0].value === '0.4', 'D2: reset restores the computed value');
ok(window._fspCompUpdates(plan)[0].value === '0.4', 'D3: and the resend follows');
ok(editNote().style.display === 'none', 'D4: the note goes away with the override');

/* ── E. the clip warning tracks what will be WRITTEN ─────────────────── */
type(inputs()[0], '1.4');
ok(warn().style.display !== 'none', 'E1: an edit above 1.0 raises the DAC clip warning');
ok(/1 amplitude above 1\.0/.test(warn().textContent),
  'E2: the warning counts the EDITED rows, not the 409 payload');
ok(card().querySelectorAll('.fsp-clipmark')[0].textContent.indexOf('>1.0') >= 0,
  'E3: the row is marked too');
type(inputs()[1], '1.1');
ok(/2 amplitudes above 1\.0/.test(warn().textContent), 'E4: count follows a second row');
type(inputs()[0], '0.4'); type(inputs()[1], '0.2');
ok(warn().style.display === 'none', 'E5: warning clears when the values come back down');

/* ── F. a typo can never be written ──────────────────────────────────── */
type(inputs()[0], 'abc');
ok(compBtn().disabled === true, 'F1: a non-numeric amplitude DISABLES the apply');
ok(inputs()[0].classList.contains('fsp-amp-bad'), 'F2: and the cell is marked');
type(inputs()[0], '0.4');
ok(compBtn().disabled === false, 'F3: fixing it re-enables the apply');

/* ── G. blank means "use the computed value" ─────────────────────────── */
type(inputs()[0], '');
ok(compBtn().disabled === false, 'G1: an empty cell is not an error');
ok(window._fspCompUpdates(plan)[0].value === '0.4',
  'G2: blank falls back to the computed value, never writes nothing');

/* ── H. the choice still reaches the caller once ─────────────────────── */
type(inputs()[0], '0.33');
compBtn().dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(seen.length === 1 && seen[0].mode === 'comp', 'H1: comp is reported exactly once');
ok(window._fspCompUpdates(seen[0].plan)[0].value === '0.33',
  'H2: the plan handed back carries the edit');
ok(doc.querySelector('.ch-overlay').style.display === 'none', 'H3: the popup closes');

/* ── I. "Apply FSP only" is unaffected by edits ──────────────────────── */
plan = mkPlan();
seen = openPopup(plan);
type(inputs()[0], '0.99');
const solo = Array.from(card().querySelectorAll('button'))
  .find(b => /Apply FSP only/i.test(b.textContent));
solo.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(seen.length === 1 && seen[0].mode === 'solo', 'I1: solo still reports solo');

/* ── J. a plan with no amps still opens and refuses comp ─────────────── */
const empty = mkPlan(); empty.amps = [];
openPopup(empty);
ok(inputs().length === 0, 'J1: no rows, no inputs');
ok(compBtn().disabled === true, 'J2: nothing to compensate — apply stays disabled');

process.exit(fails ? 1 : 0);
