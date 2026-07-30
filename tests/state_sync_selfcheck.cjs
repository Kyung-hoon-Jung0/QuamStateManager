/* jsdom selfcheck for the docs/65 state-roundtrip client wiring, running the
 * REAL shipped JS:
 *
 *  1. doStateSync needs_confirm: the server's staged-content refusal turns
 *     into ONE confirm(); decline = no re-post, accept = re-post with force=1.
 *  2. stateRestored bridge: a stage (State-History / dataset Load State /
 *     Revert-last-apply) soft-refreshes the state surface (/bulk re-GET) —
 *     and does NOT close an inspector that hosts a dataset detail.
 *  3. Plot-apply popup closes after ONE successful "Apply All" (it used to
 *     stay open showing ✓ until a second press hit the empty-pending path).
 *  4. Bulk toolbar press stamp: pointerdown on "Apply all" suppresses the
 *     focusout row-commit, so the button can't be disabled between mousedown
 *     and mouseup (the lost-click "needs two presses" mechanism).
 *
 * Run: node tests/state_sync_selfcheck.cjs  (driven by tests/test_state_roundtrip.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

/* ── app.js harness (sections 1-3) — url /bulk so the soft refresh is live ── */
const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray"></div>' +
    '<div id="plot-apply-popup" style="display:flex">' +
    '<div id="plot-apply-rows"></div>' +
    '<div id="plot-apply-context"></div><div id="plot-apply-extra" hidden></div>' +
    '<button id="plot-apply-all">Apply All</button></div>' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
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
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const ajaxCalls = [];
window.htmx = {
    ajax: function (method, url, opts) { ajaxCalls.push({ method, url, opts }); return Promise.resolve(); },
    trigger: function () {},
    process: function () {},
};
global.htmx = window.htmx;

/* URL-routed stub — app.js fires unrelated fetches at eval time (e.g.
   /diagnostics/findings.json), which must never consume a section's queue. */
function mkResp(payload) {
    return Promise.resolve({
        status: 200,
        json: function () { return Promise.resolve(payload); },
        text: function () { return Promise.resolve(''); },
    });
}
const syncCalls = [], editCalls = [];
let syncQueue = [], editQueue = [];
window.fetch = global.fetch = function (url, opts) {
    const u = String(url);
    if (u.indexOf('/state/sync') === 0) {
        syncCalls.push({ url: u, body: (opts && opts.body) || '' });
        return mkResp(syncQueue.length ? syncQueue.shift() : { status: 'ok' });
    }
    if (u.indexOf('/field/edit') === 0) {
        editCalls.push({ url: u, body: (opts && opts.body) || '' });
        return mkResp(editQueue.length ? editQueue.shift() : { ok: true, results: [] });
    }
    return mkResp({});
};

let confirmAnswer = false;
window.confirm = function () { return confirmAnswer; };

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

(async function () {
    /* ── 1. doStateSync needs_confirm ─────────────────────────────────── */
    syncQueue = [{ status: 'needs_confirm', mode: 'discard', message: 'staged content' }];
    confirmAnswer = false;
    window.doStateSync('discard');
    await flush(20);
    ok(syncCalls.length === 1, 'needs_confirm + decline: exactly one POST, no forced retry');

    syncCalls.length = 0;
    syncQueue = [
        { status: 'needs_confirm', mode: 'discard', message: 'staged content' },
        { status: 'ok', mode: 'discard', tray_html: null, replay: null },
    ];
    confirmAnswer = true;
    window.doStateSync('discard');
    await flush(40);
    ok(syncCalls.length === 2, 'needs_confirm + accept: forced re-post happens');
    ok(/force=1/.test(syncCalls[1] ? syncCalls[1].body : ''),
       'the retry carries force=1 (got: ' + (syncCalls[1] && syncCalls[1].body) + ')');

    /* ── 2. stateRestored bridge ──────────────────────────────────────── */
    ajaxCalls.length = 0;
    let inspectorClosed = 0;
    const realClose = window.closeInspector;
    window.closeInspector = function () { inspectorClosed++; };
    window.document.dispatchEvent(new window.CustomEvent('stateRestored', { bubbles: true }));
    ok(ajaxCalls.some(function (c) { return c.method === 'GET' && c.url.indexOf('/bulk') === 0; }),
       'stateRestored soft-refreshes the /bulk surface');
    ok(inspectorClosed === 1, 'stateRestored closes a non-dataset inspector');

    const ip = window.document.getElementById('inspector-pane');
    ip.innerHTML = '<div id="ds-detail-root"></div>';
    inspectorClosed = 0;
    ajaxCalls.length = 0;
    window.document.dispatchEvent(new window.CustomEvent('stateRestored', { bubbles: true }));
    ok(inspectorClosed === 0,
       'stateRestored KEEPS the inspector when it hosts a dataset detail');
    ok(ajaxCalls.length >= 1, 'the surface still refreshes in that case');
    ip.innerHTML = '';
    window.closeInspector = realClose;

    /* ── 3. plot-apply popup closes after one successful Apply All ────── */
    const rowsBox = window.document.getElementById('plot-apply-rows');
    function mkRow(dp) {
        return '<div class="plot-apply-row" data-dot-path="' + dp + '">' +
            '<input class="plot-apply-new-input" value="1.5">' +
            '<span class="plot-apply-row-action"><button class="plot-apply-row-btn">Apply</button></span>' +
            '<span class="plot-apply-row-error" hidden></span></div>';
    }
    rowsBox.innerHTML = mkRow('qubits.q1.f_01') + mkRow('qubits.q2.f_01');
    const popup = window.document.getElementById('plot-apply-popup');
    popup.style.display = 'flex';
    editCalls.length = 0;
    editQueue = [{ ok: true, results: [], tray_html: null }];
    window.applyAllPlotRows();
    await flush(20);
    ok(editCalls.length === 1, 'Apply All posts once');
    ok(popup.style.display === 'none',
       'popup CLOSES after one successful Apply All (was: needs a second press)');
    ok(window.document.getElementById('status-bar').textContent.indexOf('Applied 2') >= 0,
       'success toast reports the applied count');

    /* single-row path: the LAST applied row also closes the popup */
    rowsBox.innerHTML = mkRow('qubits.q1.f_01');
    popup.style.display = 'flex';
    editQueue = [{ ok: true, tray_html: null }];
    window.applyPlotRow(rowsBox.querySelector('.plot-apply-row'));
    await flush(20);
    ok(popup.style.display === 'none', 'last per-row apply also closes the popup');

    /* ── 4. bulk toolbar press stamp (separate jsdom, real mount) ─────── */
    const COLS = [
        { key: 'f_01', label: 'f01', section: 'Qubit', unit: 'Hz', default_on: true },
        { key: 'T1', label: 'T1', section: 'Qubit', unit: 's', default_on: true },
    ];
    function cellTd(colKey, qid, val) {
        return '<td class="bulk-td" data-col-key="' + colKey + '">' +
            '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"' +
            ' data-dot-path="qubits.' + qid + '.' + colKey + '" data-resolved="qubits.' + qid + '.' + colKey + '"></td>';
    }
    function rowHtml(qid) {
        return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>' +
            cellTd('f_01', qid, '5e9') + cellTd('T1', qid, '2e-5') +
            '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>' +
            '<span class="bulk-row-error" hidden></span></td></tr>';
    }
    const BULK_HTML = '<!doctype html><html><body><div id="bulk-panel">' +
        '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>' +
        '<button id="bulk-qubit-pill" hidden></button>' +
        '<input id="bulk-search"><span id="bulk-search-count"></span>' +
        '<button id="bulk-dyncol-hint" hidden></button>' +
        '<span id="bulk-dirty-count"></span>' +
        '<button id="bulk-apply-all" disabled>Apply all</button>' +
        '<button id="bulk-apply-sync" disabled>Apply &amp; sync</button>' +
        '<button id="bulk-reset" disabled>Reset</button>' +
        '<div class="bulk-table-wrap"><table id="bulk-table"><thead>' +
        '<tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit<span class="bulk-sort-caret"></span></th></tr>' +
        '<tr class="bulk-head-row">' +
        COLS.map(function (c) {
            return '<th class="bulk-col-head" data-col-key="' + c.key + '"><span class="bulk-col-label">' +
                c.label + '</span><span class="bulk-sort-caret"></span><span class="bulk-col-stats" data-col-stats="' + c.key + '"></span></th>';
        }).join('') + '</tr></thead><tbody>' +
        rowHtml('q1') + rowHtml('q2') +
        '</tbody></table></div></div></body></html>';

    const bdom = new JSDOM(BULK_HTML, { runScripts: 'outside-only', url: 'http://localhost/bulk' });
    const bw = bdom.window;
    const bulkFetches = [];
    bw.fetch = function (url, opts) {
        bulkFetches.push({ url: url });
        return Promise.resolve({
            status: 200,
            json: function () { return Promise.resolve({ ok: true, results: [], tray_html: null }); },
        });
    };
    bw.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));
    bw.BulkEdit.mount(COLS, { bands: {} }, [], {
        chip: 'testchip', qubits: [{ id: 'q1', grid: null }, { id: 'q2', grid: null }],
    });

    const cell = bw.document.querySelector('tr[data-qubit="q1"] td[data-col-key="f_01"] .bulk-cell');
    cell.value = '5.1e9';
    cell.dispatchEvent(new bw.Event('input', { bubbles: true }));
    const applyAllBtn = bw.document.getElementById('bulk-apply-all');
    ok(!applyAllBtn.disabled, 'a dirty cell enables Apply all');

    cell.focus();
    applyAllBtn.dispatchEvent(new bw.Event('pointerdown', { bubbles: true }));
    cell.dispatchEvent(new bw.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
    await flush(20);
    ok(bulkFetches.length === 0,
       'pointerdown-stamped Apply all press: focusout does NOT race a row commit');
    ok(!applyAllBtn.disabled, 'the button stays enabled at mouseup (click can land)');

    // control: with the stamp expired, the focusout row-commit fires as designed
    bw.BulkEdit._toolbarPressTs = Date.now() - 5000;
    cell.focus();
    cell.dispatchEvent(new bw.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
    await flush(20);
    ok(bulkFetches.length > 0, 'expired stamp: the click-away row commit still works');

    process.exit(fails ? 1 : 0);
})().catch(function (e) {
    console.error('FAIL: selfcheck crashed: ' + (e && e.stack || e));
    process.exit(1);
});
