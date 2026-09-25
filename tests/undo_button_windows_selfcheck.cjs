// QA F1 (windows): the tray ↶ names what the next press does, across windows.
//
// Two SM windows share one change log and one undo journal. Window B's ↶
// read "Undo typed edit (anharmonicity)" -- a committed, applied entry of its
// own that tryUndo would silently drop -- while the press walked the journal
// and rewrote window A's applied q1.chi on the LIVE chip. A's ↶ was hidden
// ("Nothing to undo") though its Ctrl+Z would write live too, and A was never
// told when B's press changed its applied value back.
//
// Pinned against the real LiveEditUndo in app.js:
//   U1-U3 a committed (clean) entry is pruned before the button/tooltip read it;
//         a dirty one is still named; a cell off screen keeps its entry
//   U4-U6 an empty log with a journal step shows the button and names the
//         step, saying LIVE when the tray says the walk writes live
//   U7-U9 another window's live undo toasts once; this window's own does not;
//         the first tray a window sees only records
//
// Run: node tests/undo_button_windows_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function tray(attrs, btnAttrs) {
    return '<div id="pending-tray" ' + (attrs || 'data-change-count="0"') + '>'
        + '<button type="button" id="tray-undo-btn" style="display:none" ' + (btnAttrs || '') + '>&#8630;</button></div>';
}
function world(trayHtml) {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + trayHtml
        + '<input class="bulk-cell" data-dot-path="qubits.q2.anharmonicity" value="216,000,000" data-orig="216,000,000">'
        + '</body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {}, process: function () {}, on: function () {}, config: {} };
    w.fetch = function () { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({}); }, text: function () { return Promise.resolve(''); } }); };
    w.eval(APP);
    const toasts = [];
    w.showToast = function (m, l) { toasts.push({ m: String(m), l: l }); };
    return { w: w, toasts: toasts };
}
function swapTray(w, html) {
    const old = w.document.getElementById('pending-tray');
    const tmp = w.document.createElement('div');
    tmp.innerHTML = html;
    const neu = tmp.firstChild;
    old.parentNode.replaceChild(neu, old);
    w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: neu } }));
    neu.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: neu } }));
}

(async function () {
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
// ── U1-U3 the stale entry ────────────────────────────────────────────────
{
    const { w } = world(tray());
    const U = w.LiveEditUndo;
    const btn = () => w.document.getElementById('tray-undo-btn');
    const cell = w.document.querySelector('.bulk-cell');
    // B typed 216000000 + Enter: recorded, then COMMITTED (data-orig == value)
    U.record('typed edit (anharmonicity)', [{ dp: 'qubits.q2.anharmonicity', prev: '215,000,000', next: '216000000' }]);
    U._updateTrayBtn();
    ok(btn().style.display === 'none',
       'U1 a committed entry (and nothing pending, no journal step) leaves the button hidden (got "' + btn().style.display + '")');
    U.refreshTip(btn());
    ok(btn().title === 'Nothing to undo' && !/typed edit/.test(btn().title),
       'U2 ...and the tooltip never names it (got "' + btn().title + '")');
    // a DIRTY entry (typed, not committed) is still named
    cell.value = '217000000';
    U.record('typed edit (anharmonicity)', [{ dp: 'qubits.q2.anharmonicity', prev: '216,000,000', next: '217000000' }]);
    U.refreshTip(btn());
    ok(/Undo typed edit \(anharmonicity\)/.test(btn().title), 'U3 a dirty entry is still what the press undoes (got "' + btn().title + '")');
    // ...then committed (Enter) and applied from the other window -- no tray
    // swap in THIS window yet; the hover must not name it
    cell.setAttribute('data-orig', '217,000,000'); cell.value = '217,000,000';
    U.refreshTip(btn());
    ok(!/typed edit/.test(btn().title), 'U3b a hover after the commit re-judges the stack (got "' + btn().title + '")');
}

// ── U4-U6 the journal step ──────────────────────────────────────────────
{
    const live = 'data-jrn-what="qubits.q1.chi -351,000 → -350,000" data-jrn-n="1" data-jrn-live="1"';
    const { w } = world(tray('data-change-count="0"', live));
    const U = w.LiveEditUndo;
    const btn = () => w.document.getElementById('tray-undo-btn');
    U._updateTrayBtn();
    ok(btn().style.display === '', 'U4 with nothing pending, a journal step shows the ↶ (A\'s was hidden)');
    U.refreshTip(btn());
    ok(/LIVE chip/.test(btn().title) && /qubits\.q1\.chi -351,000 → -350,000/.test(btn().title)
       && /another|whichever window/.test(btn().title),
       'U5 ...and names the live step, whoever applied it (got "' + btn().title + '")');
    swapTray(w, tray('data-change-count="0"', 'data-jrn-what="qubits.q1.chi -351,000 → -350,000" data-jrn-n="3" data-jrn-live="0"'));
    U.refreshTip(btn());
    ok(/^Stage the undo of qubits\.q1\.chi/.test(btn().title) && /\+2 more/.test(btn().title) && !/LIVE/.test(btn().title),
       'U6 the walk OFF names a staged step (got "' + btn().title + '")');
}

// ── U7-U9 the other window hears ────────────────────────────────────────
{
    const { w, toasts } = world(tray('data-change-count="0" data-live-undo-seq="4" data-live-undo-msg="old"'));
    await tick();   // DOMContentLoaded: the page's first tray is read there
    ok(w._liveUndoSeen === '4' && toasts.length === 0, 'U7 the first tray a window sees only records where it is');
    swapTray(w, tray('data-change-count="0" data-live-undo-seq="5" data-live-undo-msg="Undone → live: qubits.q1.chi → -350,000.0"'));
    ok(toasts.length === 1 && /Another State Manager window undid/.test(toasts[0].m) && /qubits\.q1\.chi/.test(toasts[0].m),
       'U8 another window\'s live undo is told, naming the value (got ' + JSON.stringify(toasts) + ')');
    swapTray(w, tray('data-change-count="0" data-live-undo-seq="5" data-live-undo-msg="x"'));
    ok(toasts.length === 1, 'U8b ...once');
    // this window's own press: its cellsReverted carries the seq first
    w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { live: true, live_undo_seq: 6, entries: [], message: 'm' } }));
    const before = toasts.filter(function (t) { return /Another State Manager window/.test(t.m); }).length;
    swapTray(w, tray('data-change-count="0" data-live-undo-seq="6" data-live-undo-msg="mine"'));
    const after = toasts.filter(function (t) { return /Another State Manager window/.test(t.m); }).length;
    ok(after === before, 'U9 this window\'s own live undo is not announced as another window\'s');
}

console.log(fails ? (fails + ' failed') : ('all checks passed (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
