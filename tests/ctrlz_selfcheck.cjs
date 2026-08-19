/* jsdom selfcheck for the Ctrl+Z undo client wiring in web/static/app.js:
 *  1. Ctrl/⌘+Z (outside a text field, tray present) → POST /undo into #pending-tray
 *  2. Guarded: typing inside an <input>/<textarea> does NOT hijack Ctrl+Z
 *  3. Guarded: no #pending-tray → no request
 *  4. cellsReverted → reverts the matching inspector cell, asks the grids to
 *     repaint the named paths, and rebuilds the grid ONLY for what a repaint
 *     cannot express (docs/122 item 3: the reflexive full /bulk re-GET cost
 *     2,418 ms per press on the real 20-qubit chip against 55 ms for the undo)
 *  5. docs/122 item 3: a burst of presses is QUEUED, never dropped — htmx used
 *     to discard six of ten silently
 *
 * Run: node tests/ctrlz_selfcheck.cjs   (driven by tests/test_ctrlz_client.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
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
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => new Promise(() => {});   // never resolves — fine for wiring tests
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

// Recording htmx stub (app.js only needs .ajax/.trigger/.process here).
const calls = [];
window.htmx = {
    ajax: function (method, url, opts) { calls.push({ method, url, opts }); return Promise.resolve(); },
    trigger: function () {},
    process: function () {},
};
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);   // app.js is head-loaded: must evaluate with no <body> deps
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function pressCtrlZ(target) {
    const ev = new window.KeyboardEvent('keydown',
        { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
    (target || window.document).dispatchEvent(ev);
    return ev;
}

/* docs/122 item 3 — the server tier is a QUEUE now, so a press that arrives
   while a request is in flight is HELD, not dropped. Its release rides the
   request promise, i.e. a microtask; in a browser there is always one between
   two key events, and in this file there is not unless we make one. `settle`
   is that boundary. Everything below therefore runs inside an async main —
   the alternative (a stub that resolves synchronously) would pin a completion
   path the real htmx does not have. */
const settle = () => new Promise((r) => setTimeout(r, 0));
// The grid rebuild is DEBOUNCED (a burst of presses must cost one rebuild,
// not N), so its absence and its arrival are both real-time facts.
const settleDebounce = () => new Promise((r) => setTimeout(r, 1200));

(async function main() {

// ── 3. no tray → no request ──────────────────────────────────────────────────
pressCtrlZ();
ok(calls.length === 0, 'Ctrl+Z without #pending-tray issues no request');

// ── 1. tray present, focus outside a field → POST /undo ─────────────────────
const tray = window.document.createElement('div');
tray.id = 'pending-tray';
window.document.body.appendChild(tray);
pressCtrlZ();
ok(calls.length === 1, 'Ctrl+Z issues exactly one request');
ok(calls[0] && calls[0].method === 'POST' && calls[0].url === '/undo',
   'Ctrl+Z posts /undo (got ' + JSON.stringify(calls[0]) + ')');
// docs/124 M-11: the SOURCE must be the queue's own element — sourcing from
// the tray put /undo in the same htmx sync lane as the ⚡ apply and the
// auto-apply flush, whose in-flight request replaced-and-dropped the press.
// (This pin used to assert source === '#pending-tray', i.e. the bug's own
// vector — the docs/123 §8 class, met again.)
ok(calls[0] && calls[0].opts && calls[0].opts.source
   && calls[0].opts.source.id === 'undo-sync-src'
   && calls[0].opts.target === '#pending-tray',
   'undo request rides the queue\'s own sync lane, targets the tray');
await settle();

// ── 1b. docs/122 item 3: a BURST is queued, not dropped ────────────────────
// The bug: measured on the real 20-qubit chip, ten presses reached the server
// tier ten times and produced four requests — htmx discards a second request
// from the same source while one is in flight, silently. Every press must now
// become exactly one request, in order.
{
    const n = calls.length;
    for (let i = 0; i < 10; i++) pressCtrlZ();
    ok(calls.length === n + 1, 'a burst issues one request immediately, holds the rest');
    for (let i = 0; i < 12; i++) await settle();
    const issued = calls.slice(n).filter((c) => c.url === '/undo');
    ok(issued.length === 10, 'all ten presses reach /undo (got ' + issued.length + ')');
    ok(window.UndoQueue.depth() === 0 && !window.UndoQueue.busy(),
       'the queue drains empty');
}

// ── 1c. the bound is a held key, not a rate limit ──────────────────────────
// Past the cap we refuse the press rather than discarding one somewhere in the
// middle, which is what made the original failure invisible.
{
    let accepted = 0;
    for (let i = 0; i < 40; i++) if (window.UndoQueue.push('/undo')) accepted++;
    ok(accepted < 40 && accepted >= 20,
       'the queue is bounded and says so (accepted ' + accepted + ' of 40)');
    for (let i = 0; i < 45; i++) await settle();
}

// ── 1d. docs/124 M-11: the queue has its own sync lane and waits out applies ─
// htmx 2.0.4's per-element sync (strategy "last") lives on the SOURCE element,
// and both the grid ⚡ apply and the armed auto-apply flush issue from
// "#pending-tray" — a /undo sourced there during an apply window was
// replaced-and-dropped (executed on the real chip: 3 presses inside an apply
// window → 0 POST /undo, no toast). The queue now issues from its own
// body-level element, and an in-flight apply HOLDS the press — never a race,
// never a drop.
{
    const n = calls.length;
    pressCtrlZ();
    await settle();
    const c = calls[calls.length - 1];
    ok(calls.length === n + 1 && c.url === '/undo', 'baseline press issues');
    const srcEl = c.opts && c.opts.source;
    ok(!!srcEl && srcEl.id === 'undo-sync-src' && srcEl.isConnected,
       'M-11: the request is sourced from the queue\'s own element, never the tray');
    window._applyInFlight = true;
    const n2 = calls.length;
    pressCtrlZ();
    await settle();
    ok(calls.length === n2, 'M-11: no request while an apply is in flight');
    ok(window.UndoQueue.depth() === 1, 'M-11: the press is HELD, not dropped');
    window._applyInFlight = false;
    await new Promise((r) => setTimeout(r, 350));   // past the 120ms hold retry
    ok(calls.length === n2 + 1 && calls[calls.length - 1].url === '/undo',
       'M-11: the held press issues once the apply settles');
    ok(window.UndoQueue.depth() === 0 && !window.UndoQueue.busy(),
       'M-11: and the queue drains');
}

// ── 2. focus inside an input → native undo untouched ───────────────────────
const inp = window.document.createElement('input');
window.document.body.appendChild(inp);
inp.focus();
const before = calls.length;
const ev = new window.KeyboardEvent('keydown',
    { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
inp.dispatchEvent(ev);
ok(calls.length === before, 'Ctrl+Z inside an <input> does NOT hijack (native text undo)');
ok(!ev.defaultPrevented, 'default not prevented inside an <input>');
inp.blur();

// ── 4. cellsReverted → cell revert + quam:state-changed ─────────────────────
// Build a minimal inspector cell (hidden dot_path + value input, modified marks).
const form = window.document.createElement('form');
form.innerHTML = '<input type="hidden" name="dot_path" value="qubits.qA1.f_01">' +
                 '<input name="value" class="edit-input edit-input-modified" value="6.30e9" title="x">';
const td = window.document.createElement('td');
td.className = 'cell-modified';
td.appendChild(form);
window.document.body.appendChild(td);

let stateChanged = 0;
window.document.addEventListener('quam:state-changed', function () { stateChanged++; });

const ENTRY = { dot_path: 'qubits.qA1.f_01', old_value_str: '6.25e9', created: false };
function revert(entries) {
    window.document.dispatchEvent(new window.CustomEvent('cellsReverted', {
        detail: { message: 'Undone', entries: entries },
    }));
}
revert([ENTRY]);

const valInput = form.querySelector('input[name="value"]');
ok(valInput.value === '6.25e9', 'cellsReverted restores the inspector cell value');
ok(!valInput.classList.contains('edit-input-modified'), 'modified marker cleared');
ok(!td.classList.contains('cell-modified'), 'td modified marker cleared');

/* docs/122 item 3 — the full-grid re-GET is now the FALLBACK, not the reflex.
   It cost 2,418 ms per press on the real 20-qubit chip while the undo itself
   cost 55 ms, and the response already names every path it reverted. These pin
   the decision, which lives in app.js: repaint by path, and rebuild only for
   what a repaint provably cannot express. */
ok(stateChanged === 0, 'no grid on screen ⇒ an undo triggers no grid rebuild at all');

// Stub the grid API the way the real grids answer: `covered` lists the paths
// this surface actually repainted.
const grid = window.document.createElement('table');
grid.id = 'bulk-table';
window.document.body.appendChild(grid);
let repainted = [];
window.BulkEdit = {
    revertPaths: function (entries) {
        repainted = entries.map((e) => e.dot_path);
        return { patched: entries.length, missing: 0, covered: repainted };
    },
};

stateChanged = 0; repainted = [];
revert([ENTRY]);
ok(repainted.length === 1 && repainted[0] === 'qubits.qA1.f_01',
   'the grid is asked to repaint the reverted path');
await settleDebounce();
ok(stateChanged === 0, 'a covered value revert costs NO /bulk rebuild');

// created/deleted is a STRUCTURAL change: a restored subtree can add columns and
// an undone creation turns a cell back into "not set". A value repaint cannot
// express either, so the rebuild must still happen.
stateChanged = 0;
revert([{ dot_path: 'qubits.qA1.new_leaf', old_value_str: '', created: true }]);
await settleDebounce();
ok(stateChanged === 1, 'an undone CREATE still rebuilds the grid');

stateChanged = 0;
revert([{ dot_path: 'qubits.qA1.gone', old_value_str: '1', deleted: true }]);
await settleDebounce();
ok(stateChanged === 1, 'an undone DELETE still rebuilds the grid');

// A path no surface could reach: we cannot claim the grid is current for it.
window.BulkEdit.revertPaths = function () {
    return { patched: 0, missing: 1, covered: [] };
};
stateChanged = 0;
revert([ENTRY]);
await settleDebounce();
ok(stateChanged === 1, 'an UNCOVERED path falls back to the rebuild');

// A burst of covered reverts must not queue N rebuilds behind it.
window.BulkEdit.revertPaths = function (entries) {
    return { patched: entries.length, missing: 0, covered: entries.map((e) => e.dot_path) };
};
stateChanged = 0;
for (let i = 0; i < 6; i++) revert([{ dot_path: 'x.y', old_value_str: String(i), deleted: true }]);
await settleDebounce();
ok(stateChanged === 1, 'six structural reverts debounce to ONE rebuild, not six');

delete window.BulkEdit;
grid.remove();
stateChanged = 0;

// ── 5. docs/107: Ctrl+Shift+Z → POST /redo (same guards) ────────────────────
function pressShiftZ(target) {
    const ev = new window.KeyboardEvent('keydown',
        { key: 'Z', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true });
    (target || window.document).dispatchEvent(ev);
    return ev;
}
await settle();
let n0 = calls.length;
pressShiftZ();
ok(calls.length === n0 + 1, 'Ctrl+Shift+Z issues exactly one request');
ok(calls[n0] && calls[n0].method === 'POST' && calls[n0].url === '/redo',
   'Ctrl+Shift+Z posts /redo (got ' + JSON.stringify(calls[n0]) + ')');
ok(calls[n0] && calls[n0].opts && calls[n0].opts.source
   && calls[n0].opts.source.id === 'undo-sync-src'
   && calls[n0].opts.target === '#pending-tray',
   'redo request rides the queue\'s own sync lane, targets the tray');

// guard: inside an <input> the browser keeps native redo
await settle();
inp.focus();
n0 = calls.length;
const rev = pressShiftZ(inp);
ok(calls.length === n0, 'Ctrl+Shift+Z inside an <input> does NOT hijack');
ok(!rev.defaultPrevented, 'default not prevented inside an <input> (redo)');
inp.blur();

// guard: _wizUndo exists on EVERY page (generate.js is head-loaded) — only a
// MOUNTED wizard may swallow redo (the bug the first real-browser pass caught:
// a bare existence check ate Ctrl+Shift+Z app-wide).
window._wizUndo = { tryUndo: () => false, mounted: () => false };
await settle();
n0 = calls.length;
pressShiftZ();
ok(calls.length === n0 + 1 && calls[n0].url === '/redo',
   'wizard NOT mounted: Ctrl+Shift+Z still reaches /redo');
window._wizUndo.mounted = () => true;
n0 = calls.length;
const wev = pressShiftZ();
ok(calls.length === n0, 'wizard mounted: Ctrl+Shift+Z issues no /redo');
ok(wev.defaultPrevented, 'wizard mounted: the press is swallowed');
delete window._wizUndo;

// ── 6. docs/107: LiveEditUndo redo stack (tryUndo → tryRedo round trip) ─────
const cell = window.document.createElement('input');
cell.className = 'bulk-cell';
cell.setAttribute('data-dot-path', 'qubits.qA1.f_01');
cell.value = '7';
window.document.body.appendChild(cell);
window.LiveEditUndo.record('test fill', [{ dp: 'qubits.qA1.f_01', prev: '5', next: '7' }]);
ok(window.LiveEditUndo.tryUndo() === true, 'LiveEditUndo.tryUndo restores the cell');
ok(cell.value === '5', 'undo put prev back');
ok(window.LiveEditUndo.tryRedo() === true, 'tryRedo re-applies the action');
ok(cell.value === '7', 'redo put next back');
ok(window.LiveEditUndo.tryUndo() === true, 'the redone action is undoable again');
ok(cell.value === '5', 'second undo works after redo');
// a NEW action forks history — redo dies
window.LiveEditUndo.record('fork', [{ dp: 'qubits.qA1.f_01', prev: '5', next: '9' }]);
ok(window.LiveEditUndo.tryRedo() === false, 'a new record() clears the redo stack');
// a moved cell is never clobbered by redo
window.LiveEditUndo.tryUndo();                 // pops 'fork' (cell shows 5)
cell.value = '42';                             // user typed since
ok(window.LiveEditUndo.tryRedo() === false, 'redo skips a cell that moved since');
ok(cell.value === '42', 'moved cell untouched');
// clear() kills both stacks
window.LiveEditUndo.record('x', [{ dp: 'qubits.qA1.f_01', prev: '42', next: '43' }]);
window.LiveEditUndo.tryUndo();
window.LiveEditUndo.clear();
ok(window.LiveEditUndo.tryRedo() === false, 'clear() empties the redo stack too');

process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
