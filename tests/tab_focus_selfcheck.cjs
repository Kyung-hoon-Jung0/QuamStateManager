/* jsdom selfcheck for the "global Tab is dead" fix + the Tab-navigation
 * feedback batch (r8). Four sections, each running the REAL shipped JS:
 *
 *  A. trapFocus (app.js) leak class:
 *     - an active trap still traps; release() frees it
 *     - re-trapping the same container releases the previous trap (the
 *       double-open overwrite used to orphan a CAPTURE handler forever)
 *     - self-heal: a trap whose container went hidden (own display:none,
 *       hidden attr, ancestor display:none) or was detached WITHOUT release
 *       detaches itself on the next keydown and swallows nothing
 *  B. the exact user-visible kill sequence, end-to-end: Ctrl+K, Ctrl+K,
 *     Escape, Tab → Tab must stay alive (Ctrl+K is a toggle now)
 *  C. Live State Edit grid Tab hop (bulk-edit.js): next/prev edit cell,
 *     hidden-column skip, row-edge wrap, hidden-row skip, grid-edge exit
 *  D. calculator Tab hop (calc.js): visible inputs only, closed <details>
 *     skipped, wrap-around, Shift+Tab reverse
 *
 * Run: node tests/tab_focus_selfcheck.cjs   (driven by tests/test_tab_focus.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

/* ────────────────────────────────────────────────────────────────────────────
 * Sections A + B: app.js under the ctrlz_selfcheck harness
 * ──────────────────────────────────────────────────────────────────────────── */
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
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
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

try {
    window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

function press(key, opts) {
    const ev = new window.KeyboardEvent('keydown',
        Object.assign({ key: key, bubbles: true, cancelable: true }, opts || {}));
    window.document.dispatchEvent(ev);
    return ev;
}
function tabAlive(msg) { ok(!press('Tab').defaultPrevented, msg); }
function tabTrapped(msg) { ok(press('Tab').defaultPrevented, msg); }
function makeBox() {
    const d = window.document.createElement('div');
    d.innerHTML = '<button>x</button>';
    window.document.body.appendChild(d);
    return d;
}

// ── A0: baseline — no trap, Tab passes ──────────────────────────────────────
tabAlive('baseline: Tab not swallowed with no trap');

// ── A1: an active trap traps; release() frees it; Escape → onEscape ────────
const a1 = makeBox();
let escaped = 0;
const rel1 = window.trapFocus(a1, function () { escaped++; });
tabTrapped('active trap intercepts Tab');
press('Escape');
ok(escaped === 1, 'Escape inside an active trap calls onEscape');
rel1();
tabAlive('release() frees Tab');
press('Escape');
ok(escaped === 1, 'released trap no longer sees Escape');

// ── A2: re-trapping the same container releases the previous trap ──────────
const a2 = makeBox();
window.trapFocus(a2);                    // "leaked" first trap (release discarded)
const rel2 = window.trapFocus(a2);       // double-open overwrite
rel2();
tabAlive('double-trap then single release: no orphan handler survives');

// ── A3: self-heal — own display:none without release ────────────────────────
const a3 = makeBox();
window.trapFocus(a3);
a3.style.display = 'none';               // hidden without any release() call
tabAlive('self-heal: hidden (display:none) container stops swallowing Tab');
tabAlive('self-heal detached the handler (second Tab also clean)');

// ── A4: self-heal — container removed from the DOM ─────────────────────────
const a4 = makeBox();
window.trapFocus(a4);
a4.remove();
tabAlive('self-heal: detached container stops swallowing Tab');

// ── A5: self-heal — hidden attribute (the palette hide mechanism) ──────────
const a5 = makeBox();
window.trapFocus(a5);
a5.hidden = true;
tabAlive('self-heal: [hidden] container stops swallowing Tab');

// ── A6: self-heal — ANCESTOR display:none (Column-History card case) ───────
const wrap6 = window.document.createElement('div');
window.document.body.appendChild(wrap6);
const a6 = window.document.createElement('div');
a6.innerHTML = '<button>x</button>';
wrap6.appendChild(a6);
window.trapFocus(a6);                    // trap on the CHILD card
wrap6.style.display = 'none';            // overlay hidden, card untouched
tabAlive('self-heal: ancestor-hidden container stops swallowing Tab');

// ── B: the user-visible kill sequence — Ctrl+K, Ctrl+K, Escape, Tab ────────
const pal = window.document.createElement('div');
pal.id = 'cmd-palette';
pal.hidden = true;
pal.innerHTML = '<div class="cmd-palette-backdrop"></div>' +
    '<div class="cmd-palette-box"><input id="cmd-palette-input" type="text">' +
    '<ul id="cmd-palette-results"></ul></div>';
window.document.body.appendChild(pal);

press('k', { ctrlKey: true });
ok(!pal.hidden, 'Ctrl+K opens the palette');
press('k', { ctrlKey: true });
ok(pal.hidden, 'Ctrl+K again CLOSES it (toggle, not a second stacked open)');
press('Escape');
tabAlive('Ctrl+K, Ctrl+K, Escape leaves Tab ALIVE (the reported global kill)');
press('k', { ctrlKey: true });
ok(!pal.hidden, 'palette still opens after the toggle round-trip');
window.closeCmdPalette();
tabAlive('Tab alive after closeCmdPalette');

/* ────────────────────────────────────────────────────────────────────────────
 * Section C: Live State Edit grid Tab hop (bulk-edit.js, real mount)
 * ──────────────────────────────────────────────────────────────────────────── */
const COLS = [
    { key: 'f_01', label: 'f01', section: 'Qubit', unit: 'Hz', default_on: true },
    { key: 'T1', label: 'T1', section: 'Qubit', unit: 's', default_on: true },
    { key: 'T2', label: 'T2', section: 'Qubit', unit: 's', default_on: true },
];
function cellTd(colKey, qid, val) {
    return '<td class="bulk-td" data-col-key="' + colKey + '">' +
        '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"' +
        ' data-dot-path="qubits.' + qid + '.' + colKey + '" data-resolved="qubits.' + qid + '.' + colKey + '"></td>';
}
function rowHtml(qid) {
    return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>' +
        cellTd('f_01', qid, '5e9') + cellTd('T1', qid, '2e-5') + cellTd('T2', qid, '3e-5') +
        '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>' +
        '<span class="bulk-row-error" hidden></span></td></tr>';
}
const BULK_HTML = '<!doctype html><html><body><div id="bulk-panel">' +
    '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>' +
    '<button id="bulk-qubit-pill" hidden></button>' +
    '<input id="bulk-search"><span id="bulk-search-count"></span>' +
    '<button id="bulk-dyncol-hint" hidden></button>' +
    '<span id="bulk-dirty-count"></span>' +
    '<button id="bulk-apply-all"></button><button id="bulk-reset"></button>' +
    '<div class="bulk-table-wrap"><table id="bulk-table"><thead>' +
    '<tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit<span class="bulk-sort-caret"></span></th></tr>' +
    '<tr class="bulk-head-row">' +
    COLS.map(function (c) {
        return '<th class="bulk-col-head" data-col-key="' + c.key + '"><span class="bulk-col-label">' +
            c.label + '</span><span class="bulk-sort-caret"></span><span class="bulk-col-stats" data-col-stats="' + c.key + '"></span></th>';
    }).join('') + '</tr></thead><tbody>' +
    rowHtml('q1') + rowHtml('q2') + rowHtml('q3') +
    '</tbody></table></div></div></body></html>';

const bdom = new JSDOM(BULK_HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
const bw = bdom.window;
bw.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));
bw.BulkEdit.mount(COLS, { bands: {} }, [], {
    chip: 'testchip',
    qubits: [{ id: 'q1', grid: null }, { id: 'q2', grid: null }, { id: 'q3', grid: null }],
});

function bcell(qid, col) {
    return bw.document.querySelector(
        'tr[data-qubit="' + qid + '"] td[data-col-key="' + col + '"] .bulk-cell');
}
function pressTabOn(el, shift) {
    const ev = new bw.KeyboardEvent('keydown',
        { key: 'Tab', shiftKey: !!shift, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

let c = bcell('q1', 'f_01');
c.focus();
let ev = pressTabOn(c);
ok(ev.defaultPrevented && bw.document.activeElement === bcell('q1', 'T1'),
   'grid: Tab moves to the next edit cell in the row');
ev = pressTabOn(bw.document.activeElement);
ok(bw.document.activeElement === bcell('q1', 'T2'), 'grid: Tab again reaches the row\'s last cell');
ev = pressTabOn(bw.document.activeElement);
ok(ev.defaultPrevented && bw.document.activeElement === bcell('q2', 'f_01'),
   'grid: Tab at the row edge wraps to the NEXT row\'s first cell');
ev = pressTabOn(bw.document.activeElement, true);
ok(ev.defaultPrevented && bw.document.activeElement === bcell('q1', 'T2'),
   'grid: Shift+Tab wraps back to the previous row\'s last cell');

// hidden COLUMN is skipped
bw.document.querySelectorAll('td[data-col-key="T1"]').forEach(function (td) {
    td.classList.add('bulk-col-hidden');
});
c = bcell('q1', 'f_01');
c.focus();
pressTabOn(c);
ok(bw.document.activeElement === bcell('q1', 'T2'),
   'grid: Tab skips a hidden column\'s cell');
bw.document.querySelectorAll('td[data-col-key="T1"]').forEach(function (td) {
    td.classList.remove('bulk-col-hidden');
});

// hidden ROW is skipped on the wrap
bw.document.querySelector('tr[data-qubit="q2"]').classList.add('bulk-row-hidden');
c = bcell('q1', 'T2');
c.focus();
pressTabOn(c);
ok(bw.document.activeElement === bcell('q3', 'f_01'),
   'grid: row-edge wrap skips a hidden row');
bw.document.querySelector('tr[data-qubit="q2"]').classList.remove('bulk-row-hidden');

// READ-ONLY cells are skipped. They already declare themselves out of the tab
// order with tabindex="-1", but the handler preventDefault()s and focuses by
// hand, which overrode that. Harmless while a couple of cells per row were
// read-only; unusable once a per-neighbour column renders a blank for every
// qubit not in that pair (a real chip measured 48 consecutive dead stops).
bw.document.querySelectorAll('td[data-col-key="T1"] .bulk-cell').forEach(function (el) {
    el.classList.add('bulk-cell-ro');
    el.setAttribute('readonly', 'readonly');
    el.setAttribute('tabindex', '-1');
});
c = bcell('q1', 'f_01');
c.focus();
pressTabOn(c);
ok(bw.document.activeElement === bcell('q1', 'T2'),
   'grid: Tab skips a read-only cell instead of parking in it');
pressTabOn(bw.document.activeElement, true);
ok(bw.document.activeElement === bcell('q1', 'f_01'),
   'grid: Shift+Tab skips it in the other direction too');
bw.document.querySelectorAll('td[data-col-key="T1"] .bulk-cell').forEach(function (el) {
    el.classList.remove('bulk-cell-ro');
    el.removeAttribute('readonly');
    el.removeAttribute('tabindex');
});

// ── arrow navigation ─────────────────────────────────────────────────────────
// The grid hides rows TWO independent ways and both are display:none: the
// search box (.bulk-row-hidden) and the qubit picker (.bulk-qubit-off). The
// movement helpers only ever filtered the first, so once a subset of qubits
// was picked, every up/down press aimed at a row still in the DOM but not on
// the screen and .focus() became a silent no-op. There was no arrow coverage
// at all before this, which is why it went unnoticed.
function pressKeyOn(el, key) {
    const ev = new bw.KeyboardEvent('keydown', { key: key, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

c = bcell('q1', 'f_01');
c.focus();
pressKeyOn(c, 'ArrowDown');
ok(bw.document.activeElement === bcell('q2', 'f_01'),
   'grid: ArrowDown moves to the next row');

bw.document.querySelector('tr[data-qubit="q2"]').classList.add('bulk-qubit-off');
c = bcell('q1', 'f_01');
c.focus();
pressKeyOn(c, 'ArrowDown');
ok(bw.document.activeElement === bcell('q3', 'f_01'),
   'grid: ArrowDown skips a row the QUBIT PICKER hid (not just the search)');
c = bcell('q3', 'f_01');
c.focus();
pressKeyOn(c, 'ArrowUp');
ok(bw.document.activeElement === bcell('q1', 'f_01'),
   'grid: ArrowUp skips it in the other direction too');
bw.document.querySelector('tr[data-qubit="q2"]').classList.remove('bulk-qubit-off');

// a read-only cell mid-column must not swallow the vertical move either
bcell('q2', 'T1').classList.add('bulk-cell-ro');
bcell('q2', 'T1').setAttribute('readonly', 'readonly');
c = bcell('q1', 'T1');
c.focus();
pressKeyOn(c, 'ArrowDown');
ok(bw.document.activeElement === bcell('q3', 'T1'),
   'grid: ArrowDown walks past a read-only cell instead of stranding the caret');
bcell('q2', 'T1').classList.remove('bulk-cell-ro');
bcell('q2', 'T1').removeAttribute('readonly');
bw.document.querySelector('tr[data-qubit="q2"]').classList.remove('bulk-row-hidden');

// grid edge: native Tab may leave (not prevented)
c = bcell('q3', 'T2');
c.focus();
ev = pressTabOn(c);
ok(!ev.defaultPrevented, 'grid: Tab at the very last cell is NOT hijacked (native exit)');

/* ────────────────────────────────────────────────────────────────────────────
 * Section D: calculator Tab hop (calc.js)
 * ──────────────────────────────────────────────────────────────────────────── */
const CALC_HTML = '<!doctype html><html><body>' +
    '<div id="calc-popover">' +
    '<details class="calc-sec" open><summary class="calc-sec-label">s1</summary>' +
    '<input type="text" id="calc-s1-dp" class="calc-in">' +
    '<input type="text" id="calc-s1-amp" class="calc-in">' +
    '</details>' +
    '<details class="calc-sec"><summary class="calc-sec-label">s2</summary>' +
    '<input type="text" id="calc-s2-fsp" class="calc-in">' +
    '</details>' +
    '<div class="calc-foot"><input type="text" id="calc-expr" class="calc-expr"></div>' +
    '</div></body></html>';
const cdom = new JSDOM(CALC_HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
const cw = cdom.window;
cw.eval(fs.readFileSync(path.join(STATIC, 'calc.js'), 'utf8'));
// jsdom may still be readyState 'loading' right after construction — fire the
// wiring event calc.js waits for (double-fire is guarded by _calcWired).
cw.document.dispatchEvent(new cw.Event('DOMContentLoaded', { bubbles: true }));

function cin(id) { return cw.document.getElementById(id); }
function calcTab(el, shift) {
    const ev = new cw.KeyboardEvent('keydown',
        { key: 'Tab', shiftKey: !!shift, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

cin('calc-s1-dp').focus();
ev = calcTab(cin('calc-s1-dp'));
ok(ev.defaultPrevented && cw.document.activeElement === cin('calc-s1-amp'),
   'calc: Tab hops to the next input');
ev = calcTab(cin('calc-s1-amp'));
ok(cw.document.activeElement === cin('calc-expr'),
   'calc: Tab skips inputs inside a CLOSED section (s2) → expression box');
ev = calcTab(cin('calc-expr'));
ok(cw.document.activeElement === cin('calc-s1-dp'), 'calc: Tab wraps around to the first input');
ev = calcTab(cin('calc-s1-dp'), true);
ok(cw.document.activeElement === cin('calc-expr'), 'calc: Shift+Tab wraps backwards');

// opening the section makes its input part of the loop
cw.document.querySelectorAll('details')[1].open = true;
cin('calc-s1-amp').focus();
calcTab(cin('calc-s1-amp'));
ok(cw.document.activeElement === cin('calc-s2-fsp'),
   'calc: an opened section\'s input joins the Tab order');

process.exit(fails ? 1 : 0);
