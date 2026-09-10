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
{
  // A caller that never opened the popup: no row was ever displayed, so there
  // is no `shown` and the raw computed value is what goes out — byte-identical
  // to before this dialog existed.
  const bare = mkPlan();
  const bareUps = window._fspCompUpdates(bare);
  ok(bareUps[0].value === String(bare.amps[0].new)
     && bareUps[1].value === String(bare.amps[1].new),
     'B0: a plan the popup never rendered serialises from a.new, unchanged');
}
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

/* ── K. Ctrl+Z in an amplitude field is NATIVE text undo ─────────────── */
// Review finding: the app-wide Ctrl+Z handler skips its "leave ordinary inputs
// alone" bail-out inside `.ch-overlay`, a carve-out written for the Column
// History panel (which has no text fields). This popup reuses that shell, so
// making the amplitudes editable would have routed a typo-undo into
// LiveEditUndo -- restoring a grid cell hidden behind the modal -- or into
// POST /undo, discarding a staged change group. The carve-out now keys on the
// Column History panel's own class instead.
plan = mkPlan();
openPopup(plan);
let undoTiers = 0;
const realTryUndo = window.LiveEditUndo.tryUndo;
window.LiveEditUndo.tryUndo = function () { undoTiers++; return false; };
let ajaxCalls = 0;
window.htmx.ajax = function () { ajaxCalls++; return Promise.resolve(); };
inputs()[0].focus();
const ev = new window.KeyboardEvent('keydown',
  { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
doc.dispatchEvent(ev);
ok(undoTiers === 0, 'K1: Ctrl+Z in an amplitude field does NOT reach LiveEditUndo');
ok(ajaxCalls === 0, 'K2: and never POSTs /undo behind the modal');
ok(ev.defaultPrevented === false, 'K3: the browser keeps its native text undo');
window.LiveEditUndo.tryUndo = realTryUndo;


/* ── L. a value that does NOT fit six significant figures ─────────────
 *
 * Customer, 2026-09-10, con1/3/1, FSP 5 -> 0 dBm: the dialog opened with
 * "Edited amplitudes no longer satisfy P = FSP + 20*log10|amp|" already
 * showing, over a table nobody had touched. Their question -- "이미
 * calculation이 잘 된거 아닌가?" -- was exactly right.
 *
 * The compensated cell is seeded with a SIX-significant-figure rendering
 * (deliberately: 0.15848931924611134 overflowed the field), and the
 * override test compared that rendering against the exact number. Every
 * row whose product does not terminate therefore read as a hand-edit the
 * instant the dialog opened -- and `_fspCompUpdates` then wrote the
 * ROUNDING instead of the amplitude SM computed, which is the one thing
 * this dialog exists to get right.
 *
 * Sections A-K all use 0.4 / 0.2, which ARE their own 6-figure rendering,
 * so none of them could see it. */
const F5 = Math.pow(10, 5 / 20);                 // the customer's factor
function mkRealPlan() {
  return {
    port: 'con1/3/1', fsp_old: 5, fsp_new: 0, factor: F5,
    clip_count: 0, skipped: [],
    amps: [
      { path: 'qubits.q1.resonator.operations.readout.amplitude',
        old: 0.0692, new: 0.0692 * F5, channel: 'q1.resonator',
        op: 'readout', clips: false },
      { path: 'qubits.q2.resonator.operations.readout.amplitude',
        old: 0.0675, new: 0.0675 * F5, channel: 'q2.resonator',
        op: 'readout', clips: false },
    ],
  };
}
plan = mkRealPlan();
const EXACT0 = plan.amps[0].new, EXACT1 = plan.amps[1].new;
seen = openPopup(plan);
ok(String(EXACT0).length > 8 && inputs()[0].value.length <= 8,
  'L0: the fixture really is a value the box has to round (' + EXACT0
  + ' -> ' + inputs()[0].value + ')');
ok(editNote().style.display === 'none',
  'L1: nothing typed, nothing accused — the note stays hidden');
ok(Array.from(card().querySelectorAll('.fsp-amp-reset'))
     .every(b => b.style.visibility === 'hidden'),
  'L2: and no row arms its undo arrow');
ok(!/edited/i.test(compBtn().title || ''),
  'L3: the apply button does not claim edits either');
ups = window._fspCompUpdates(plan);
ok(ups[0].value === inputs()[0].value && ups[1].value === inputs()[1].value,
  'L4: an untouched row writes the number the dialog SHOWED it ('
  + ups[0].value + '), not a seventeen-digit product it never displayed');
ok(ups[0].value !== String(EXACT0),
  'L4b: ...and those two really are different strings (' + String(EXACT0) + ')');

/* The Δ column has to describe the value that will actually be WRITTEN. The
 * box shows a rounding; `_rowValue` hands the exact number to the Δ paint and
 * to the clip check for exactly that reason, so a row reading "+0.053857"
 * while 0.05385693517469345 goes to disk would be a second, quieter version of
 * the same lie the note was telling. (Sections A-K cannot see this either:
 * their 0.4 IS its own rendering.) */
const dcell = card().querySelectorAll('.fsp-delta')[0];
const dExact = window.ValueDelta.compute(plan.amps[0].old, EXACT0).text;
const dShown = window.ValueDelta.compute(plan.amps[0].old, Number(inputs()[0].value)).text;
ok(dExact !== dShown,
  'L7: the two candidate deltas really are different strings ('
  + dExact + ' vs ' + dShown + ')');
ok(dcell.textContent.indexOf(dShown) === 0,
  'L8: Δ describes the amplitude the dialog shows AND writes: ' + dcell.textContent);
ok(dcell.textContent.length < dExact.length + 10,
  'L9: ...which is also why the column fits — the exact product is '
  + dExact.length + ' characters and pushed ↺ off the card');

/* Retyping what is already in the box is not an override either. */
type(inputs()[0], inputs()[0].value);
ok(editNote().style.display === 'none', 'L5: retyping the seed is not an edit');
ok(window._fspCompUpdates(plan)[0].value === inputs()[0].value,
  'L6: ...and still writes what is in the box');

/* ── M. what an override says, once there IS one ──────────────────────
 * The old sentence restated the identity, which is what the customer read
 * as "but the arithmetic is fine". Say the consequence, in dB. */
type(inputs()[0], '0.2');
ok(editNote().style.display !== 'none', 'M1: a real override does say so');
let note = editNote().textContent;
ok(note.indexOf('q1.resonator · readout') >= 0,
  'M2: a single override names the pulse: ' + note);
ok(/\+4\.2\d dB louder/.test(note),
  'M3: ...and what it costs, in dB (0.2 over ' + EXACT0.toFixed(6) + '): ' + note);
ok(note.indexOf('log10') < 0,
  'M4: the identity is no longer the explanation');
ok(/keeps the power identical/.test(note),
  'M5: and ↺ is described by what it restores');

type(inputs()[0], '0.05');
ok(/−\d\.\d\d dB quieter/.test(editNote().textContent),
  'M6: a smaller amplitude reads as quieter: ' + editNote().textContent);

type(inputs()[1], '0.05');
note = editNote().textContent;
ok(/2 pulses/.test(note) && /dB to /.test(note),
  'M7: two overrides give the range, not one number: ' + note);

type(inputs()[0], '-0.2'); type(inputs()[1], inputs()[1].value === '' ? '0' : '0');
note = editNote().textContent;
ok(/inverts the pulse/.test(note), 'M8: a sign flip is called out: ' + note);
ok(/silent \(amplitude 0\)/.test(note), 'M9: a zeroed row is called out too: ' + note);

/* ── N. the stored amplitude stays reachable ──────────────────────────
 * `amplitude now` renders rounded so the Δ column fits on the card; the
 * exact stored float must still be one hover away. */
plan = mkRealPlan();
openPopup(plan);
const oldCell = card().querySelector('.fsp-old');
ok(oldCell && oldCell.textContent === '0.0692',
  'N1: the stored amplitude is shown');
const longPlan = mkRealPlan();
longPlan.amps[0].old = 0.011220184543019634;
openPopup(longPlan);
const longCell = card().querySelector('.fsp-old');
ok(longCell.textContent.length <= 9,
  'N2: a 20-digit stored float is rendered short: ' + longCell.textContent);
ok(longCell.title === '0.011220184543019634',
  'N3: ...with the exact value on hover: ' + longCell.title);

process.exit(fails ? 1 : 0);
