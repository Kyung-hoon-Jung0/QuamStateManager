/* QA live-sync package (fix/qa-live-sync), the client halves, EXECUTED against
 * the real shipped JS under jsdom. Each section builds its own window and
 * evaluates the files IN that realm (runScripts 'outside-only'), so every bare
 * global the code reads is the window's own (docs/125 harness rule).
 *
 *  A. F3 -- the tray ✕ (cellDiscarded) repaints the Live-Edit grid cell and,
 *     when the server says the path's last log entry went, clears its red box.
 *     Real bulk-edit.js grid.
 *  B. liveedit-r2-08 -- a Ctrl+Z (cellsReverted) and a pull patch
 *     (LiveSurfacePatch) reach the discovered-collection grids (wiring doc):
 *     real pair-edit.js factory instance holding a pointer cell.
 *  C. liveedit-r2-06 -- the tray Apply waits for the click-away row commit its
 *     own mousedown started, then declares the tray as it is after it.
 *     Real bulk-edit.js focusout + real doStateSync.
 *  D. liveedit-r2-05 -- the one-click apply asks for the same-field collision
 *     check; a collision is asked ONCE (human) or bannered (automatic), and
 *     OK re-posts with its own token, never force=1.
 *  E. liveedit-r2-07 -- the liveConflict signal re-renders the drift banner in
 *     place (auto-apply.js), guarded by the chip token.
 *
 * Run: node tests/live_sync_selfcheck.cjs   (driven by tests/test_live_sync_client.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const SRC = {};
['app.js', 'bulk-edit.js', 'grid-virt.js', 'pair-edit.js', 'auto-apply.js'].forEach(function (f) {
    SRC[f] = fs.readFileSync(path.join(STATIC, f), 'utf8');
});

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function mkResp(payload, status) {
    return Promise.resolve({
        status: status || 200, ok: (status || 200) < 400,
        json: function () { return Promise.resolve(payload); },
        text: function () { return Promise.resolve(''); },
    });
}

/* One realm. `files` are evaluated in order, AFTER the stubs are installed. */
function world(bodyHtml, url, files, setup) {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + bodyHtml + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: url || 'http://localhost/bulk' });
    const w = dom.window;
    const S = { ajax: [], sync: [], edits: [], toasts: [], confirms: [], confirmAnswer: false,
                syncQueue: [], editHandler: null, stateChanged: 0 };
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: function (m, u, o) { S.ajax.push({ method: m, url: u, opts: o }); return Promise.resolve(); },
               trigger: function () {}, process: function () {}, on: function () {}, config: {} };
    w.fetch = function (url, opts) {
        const u = String(url);
        if (u.indexOf('/state/sync') === 0) {
            S.sync.push({ url: u, body: (opts && opts.body) || '' });
            return mkResp(S.syncQueue.length ? S.syncQueue.shift() : { status: 'ok', mode: 'apply' });
        }
        if (u.indexOf('/field/edit') === 0) {
            S.edits.push({ url: u, body: (opts && opts.body) || '' });
            if (S.editHandler) return S.editHandler(u, opts);
            return mkResp({ ok: true, results: [], tray_html: null });
        }
        return mkResp({});
    };
    w.confirm = function (m) { S.confirms.push(String(m)); return S.confirmAnswer; };
    w.showToast = function (m, l) { S.toasts.push({ m: String(m), l: l }); };
    w.LiveEditUndo = { record: function () {}, clear: function () {}, _updateTrayBtn: function () {} };
    w.__bulkSearchDebounce = 1;
    if (setup) setup(w, S);
    w.document.addEventListener('quam:state-changed', function () { S.stateChanged++; });
    files.forEach(function (f) {
        try { w.eval(SRC[f]); } catch (e) { console.error('FAIL: ' + f + ' did not evaluate: ' + e.message); fails++; }
    });
    // app.js installs its own window.showToast; record what the code says
    w.showToast = function (m, l) { S.toasts.push({ m: String(m), l: l }); };
    return { w: w, S: S };
}

/* A real qubit grid: rows q1..q3, T1 + an alias column (x180 amp). */
const BULK_COLS = [
    { key: 'T1', label: 'T1', section: 'Qubit', unit: 's', default_on: true },
    { key: 'x180_amp', label: 'x180 amp', section: 'XY', unit: '', default_on: true },
];
function bulkRow(qid, t1) {
    return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>'
        + '<td class="bulk-td" data-col-key="T1"><input type="text" class="bulk-cell" value="' + t1
        + '" data-orig="' + t1 + '" data-dot-path="qubits.' + qid + '.T1" data-resolved="qubits.' + qid + '.T1"></td>'
        + '<td class="bulk-td" data-col-key="x180_amp"><input type="text" class="bulk-cell" value="0.3" data-orig="0.3"'
        + ' data-dot-path="qubits.' + qid + '.xy.operations.x180.amplitude"'
        + ' data-resolved="qubits.' + qid + '.xy.operations.x180_DragCosine.amplitude"></td>'
        + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>'
        + '<span class="bulk-row-error" hidden></span></td></tr>';
}
function bulkHtml(trayAttrs) {
    return '<div id="live-diverged-slot"></div>'
        + '<div id="pending-tray" ' + (trayAttrs || 'data-change-count="1" data-change-sig="S1"') + '>'
        + '<button type="button" class="btn-apply-live">Apply to live now</button></div>'
        + '<div id="table-pane"><div id="bulk-panel">'
        + '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>'
        + '<button id="bulk-qubit-pill" hidden></button>'
        + '<input id="bulk-search"><span id="bulk-search-count"></span>'
        + '<button id="bulk-dyncol-hint" hidden></button><span id="bulk-dirty-count"></span>'
        + '<button id="bulk-apply-all" disabled>Apply all</button>'
        + '<button id="bulk-apply-sync" disabled>Apply &amp; sync</button>'
        + '<button id="bulk-reset" disabled>Reset</button>'
        + '<div class="bulk-table-wrap"><table id="bulk-table" class="bulk-table"><thead>'
        + '<tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit<span class="bulk-sort-caret"></span></th></tr>'
        + '<tr class="bulk-head-row">'
        + BULK_COLS.map(function (c) {
            return '<th class="bulk-col-head" data-col-key="' + c.key + '"><span class="bulk-col-label">' + c.label
                + '</span><span class="bulk-sort-caret"></span><span class="bulk-col-stats" data-col-stats="' + c.key + '"></span></th>';
        }).join('') + '</tr></thead><tbody>'
        + bulkRow('q1', '1.3e-05') + bulkRow('q2', '1.2e-05') + bulkRow('q3', '1.1e-05')
        + '</tbody></table></div></div></div>';
}
function mountBulk(w) {
    w.BulkEdit.mount(BULK_COLS, { bands: {} }, [], {
        chip: 'testchip', qubits: [{ id: 'q1', grid: null }, { id: 'q2', grid: null }, { id: 'q3', grid: null }],
    });
}
function cellOf(w, qid, col) {
    return w.document.querySelector('tr[data-qubit="' + qid + '"] td[data-col-key="' + col + '"] .bulk-cell');
}
function markModified(c, shown, baseline) {
    c.value = shown; c.setAttribute('data-orig', shown);
    c.setAttribute('data-baseline', baseline);
    c.classList.add('bulk-cell-modified');
}
function discard(w, detail) {
    w.document.dispatchEvent(new w.CustomEvent('cellDiscarded', { detail: detail }));
}

(async function main() {

/* ── A. F3: the tray ✕ repaints the grid ─────────────────────────────── */
{
    const { w, S } = world(bulkHtml(), 'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
    mountBulk(w);
    const q2 = cellOf(w, 'q2', 'T1');
    markModified(q2, '1.2e-05', '1.1627458545397817e-05');
    discard(w, { dot_path: 'qubits.q2.T1', old_value_str: '1.162746e-05',
                 old_value_disp: '1.1627458545397817e-05', old_kind: 'num',
                 created: false, deleted: false, source_file: 'state', still_pending: false });
    ok(q2.value === '1.1627458545397817e-05',
       'A1 the ✕ puts the discarded cell back to the lossless old value (got ' + q2.value + ')');
    ok(q2.getAttribute('data-orig') === '1.1627458545397817e-05' && !q2.classList.contains('dirty'),
       'A2 ...as the clean baseline, not an edit');
    ok(!q2.classList.contains('bulk-cell-modified') && !q2.hasAttribute('data-baseline'),
       'A3 the path had no other log entry: its red box goes with it');

    // A second entry for the same path remains: the box is still honest.
    const q3 = cellOf(w, 'q3', 'T1');
    markModified(q3, '1.1e-05', '1.0e-05');
    discard(w, { dot_path: 'qubits.q3.T1', old_value_disp: '1.05e-05', old_value_str: '1.05e-05',
                 old_kind: 'num', created: false, deleted: false, still_pending: true });
    ok(q3.value === '1.05e-05' && q3.classList.contains('bulk-cell-modified'),
       'A4 still_pending: the value repaints, the pending box stays');

    // The ALIAS cell (x180 amp): the server names the RESOLVED path.
    const a1 = cellOf(w, 'q1', 'x180_amp');
    markModified(a1, '0.31', '0.3046');
    discard(w, { dot_path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude',
                 old_value_disp: '0.3046', old_value_str: '0.3046', old_kind: 'num',
                 created: false, deleted: false, still_pending: false });
    ok(a1.value === '0.3046' && !a1.classList.contains('bulk-cell-modified'),
       'A5 an alias cell is found by data-resolved: repainted and un-boxed');

    // A structural discard (an undone create) still gets the honest rebuild.
    S.stateChanged = 0;
    discard(w, { dot_path: 'qubits.q1.new_leaf', old_value_str: '', old_kind: 'null',
                 created: true, deleted: false, still_pending: false });
    await sleep(1100);
    ok(S.stateChanged === 1, 'A6 a discarded CREATE schedules the one debounced grid rebuild');
}

/* ── B. r2-08: the wiring grid is repainted too ──────────────────────── */
{
    const P = 'bulk-w_qubits', DP = 'wiring.qubits.q1.z.opx_output';
    const html = '<div id="table-pane"><div class="bulk-panel"><input type="search" id="bulk-search">'
        + '<div class="bulk-pair-divider" id="' + P + '-divider"><div class="bulk-colvis-menu" id="' + P + '-colvis-menu"></div>'
        + '<span id="' + P + '-search-count"></span><span id="' + P + '-dirty-count"></span>'
        + '<button id="' + P + '-apply-all" disabled></button><button id="' + P + '-apply-sync" disabled></button>'
        + '<button id="' + P + '-reset" disabled></button></div>'
        + '<div class="bulk-table-wrap"><table class="bulk-table bulk-pair-table" id="' + P + '-table"><thead><tr class="bulk-head-row">'
        + '<th class="bulk-corner" data-col-key="__id__"></th><th class="bulk-col-head ck-0" data-col-key="z__opx_output" data-section="z" data-maxlen="30">'
        + '<span class="bulk-col-label">opx_output</span></th><th class="bulk-apply-col"></th></tr></thead><tbody>'
        + '<tr data-qubit="q1" data-entity="q1"><th class="bulk-rowhead" data-col-key="__id__">q1</th>'
        + '<td class="bulk-td ck-0" data-col-key="z__opx_output"><input type="text" class="bulk-cell"'
        + ' value="#/ports/analog_outputs/con1/5/9" data-orig="#/ports/analog_outputs/con1/5/9" data-is-pointer="1"'
        + ' data-dot-path="' + DP + '" data-resolved="ports.analog_outputs.con1.5.9" size="30"></td>'
        + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button><span class="bulk-row-error" hidden></span></td></tr>'
        + '</tbody></table></div></div></div>';
    let staleCalls = 0, releaseB = null;
    const trayB = '<div id="pending-tray" data-change-count="1" data-change-sig="S1">'
        + '<button type="button" class="btn-apply-live">Apply to live now</button></div>';
    const { w, S } = world(trayB + html, 'http://localhost/bulk?doc=wiring', ['app.js', 'grid-virt.js', 'pair-edit.js'], function (w, S) {
        // an instance whose table an htmx swap already removed (e_twpas after ?doc=wiring)
        w.EntityGrids = { e_twpas: { revertPaths: function () { staleCalls++; return { patched: 0 }; } } };
        S.editHandler = function () {
            return new Promise(function (res) {
                releaseB = function (body) { res({ status: 200, json: function () { return Promise.resolve(body); } }); };
            });
        };
    });
    const g = w.makeEntityGrid('w_qubits', 'Qubits');
    g.mount([{ key: 'z__opx_output', label: 'opx_output', section: 'z', unit: '', default_on: true,
               editable: true, kind: 'scalar', maxlen: 30 }]);
    const cell = w.document.querySelector('.bulk-cell[data-dot-path="' + DP + '"]');
    w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: {
        entries: [{ dot_path: DP, old_value_disp: '#/ports/analog_outputs/con1/5/1',
                    old_value_str: '#/ports/analog_outputs/con1/5/1', old_kind: 'pointer',
                    created: false, deleted: false, source_file: 'wiring' }],
        requested: 1, consumed: 1 } }));
    ok(cell.value === '#/ports/analog_outputs/con1/5/1',
       'B1 Ctrl+Z repaints the WIRING grid cell (got ' + cell.value + ')');
    ok(cell.getAttribute('data-orig') === '#/ports/analog_outputs/con1/5/1' && !cell.classList.contains('dirty'),
       'B2 ...as its clean baseline');
    const r = w.LiveSurfacePatch.apply([{ dot_path: DP, old_value_disp: '#/ports/analog_outputs/con1/5/2',
        old_value_str: '#/ports/analog_outputs/con1/5/2', old_kind: 'pointer', value: '#/ports/analog_outputs/con1/5/2' }]);
    ok(cell.value === '#/ports/analog_outputs/con1/5/2' && r.patched >= 1,
       'B3 a pull patch (LiveSurfacePatch) reaches it too (patched ' + r.patched + ')');
    ok(staleCalls === 0, 'B4 an instance whose table is gone is never asked to repaint');
    ok(typeof w._liveEditGrids === 'function' && w._liveEditGrids().indexOf(g) >= 0,
       'B5 the on-screen list names the mounted entity grid');

    // r2-06, the factory grids (pair / entity / wiring): same click-away wait
    cell.focus();
    cell.value = '#/ports/analog_outputs/con1/5/3';
    cell.dispatchEvent(new w.Event('input', { bubbles: true }));
    cell.dispatchEvent(new w.FocusEvent('focusout', { bubbles: true,
        relatedTarget: w.document.querySelector('#pending-tray .btn-apply-live') }));
    ok(S.edits.length === 1, 'B6 a factory-grid blur starts its row commit');
    w.doStateSync('apply');
    await sleep(30);
    ok(S.sync.length === 0, 'B7 the apply waits for the factory-grid commit too');
    releaseB({ ok: true, results: [{ dot_path: DP, applied: true, display: '#/ports/analog_outputs/con1/5/3' }],
               tray_html: '<div id="pending-tray" data-change-count="2" data-change-sig="S2"></div>' });
    await sleep(60);
    ok(S.sync.length === 1 && /seen_sig=S2(&|$)/.test(S.sync[0].body),
       'B8 ...then declares the tray after it: ' + (S.sync[0] && S.sync[0].body));
}

/* ── C. r2-06: Apply waits for the row commit its own click started ──── */
{
    let release = null;
    const { w, S } = world(bulkHtml('data-change-count="1" data-change-sig="S1"'),
        'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js'], function (w, S) {
        S.editHandler = function () {
            return new Promise(function (res) {
                release = function (body) { res({ status: 200, json: function () { return Promise.resolve(body); } }); };
            });
        };
    });
    mountBulk(w);
    const c = cellOf(w, 'q2', 'T1');
    c.focus();
    c.value = '1.25e-05';
    c.dispatchEvent(new w.Event('input', { bubbles: true }));
    const trayBtn = w.document.querySelector('#pending-tray .btn-apply-live');
    // mousedown on the tray button: the cell blurs first -> click-away row commit
    c.dispatchEvent(new w.FocusEvent('focusout', { bubbles: true, relatedTarget: trayBtn }));
    ok(S.edits.length === 1, 'C1 the blur starts the row commit (/field/edit-batch)');
    w.doStateSync('apply');                               // ...then the click lands
    await sleep(30);
    ok(S.sync.length === 0,
       'C2 the apply does NOT post while that commit is in flight (it would declare the stale tray)');
    release({ ok: true, results: [{ dot_path: 'qubits.q2.T1', applied: true, display: '1.25e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="2" data-change-sig="S2"></div>' });
    await sleep(60);
    ok(S.sync.length === 1, 'C3 once it lands, exactly one apply is posted (got ' + S.sync.length + ')');
    const body = S.sync[0] ? S.sync[0].body : '';
    ok(/seen_changes=2(&|$)/.test(body) && /seen_sig=S2(&|$)/.test(body),
       'C4 ...declaring the tray AFTER the commit (2 edits, sig S2), so the docs/179 gate matches: ' + body);
    ok(S.confirms.length === 0, 'C5 no "another window" confirm');

    // The commit fails: nothing is applied, and the user is told why.
    S.sync.length = 0; S.toasts.length = 0;
    const c3 = cellOf(w, 'q3', 'T1');
    c3.focus(); c3.value = 'garbage';
    c3.dispatchEvent(new w.Event('input', { bubbles: true }));
    c3.dispatchEvent(new w.FocusEvent('focusout', { bubbles: true,
        relatedTarget: w.document.querySelector('#pending-tray') }));
    w.doStateSync('apply');
    w.doStateSync('apply');                               // a double press waits once
    release({ ok: false, results: [{ dot_path: 'qubits.q3.T1', applied: false, error: 'not a number' }] });
    await sleep(60);
    ok(S.sync.length === 0, 'C6 a failed commit applies NOTHING');
    ok(S.toasts.some(function (t) { return t.l === 'warning' && /not committed/.test(t.m); }),
       'C7 ...and says so');
    ok(!w._awaitingGridCommit && (w._pendingGridCommits || []).length === 0,
       'C8 the wait is released and the registry is empty');
    w.doStateSync('apply');
    await sleep(20);
    ok(S.sync.length === 1, 'C9 with nothing in flight the next press posts at once');
}

/* ── D. r2-05: the collision question ────────────────────────────────── */
{
    const { w, S } = world('<div id="live-diverged-slot"></div><div id="pending-tray" data-change-count="1" data-change-sig="S1"></div>',
        'http://localhost/bulk', ['app.js']);
    const COLL = { status: 'collision', mode: 'apply', paths: ['qubits.q1.T2ramsey'], count: 1,
                   message: 'The live chip changed 1 field you also edited.' };
    const bannerGets = function () { return S.ajax.filter(function (a) { return a.url === '/state/diverged-banner'; }); };

    w.doStateSync('apply');
    await sleep(20);
    ok(/check_collisions=1/.test(S.sync[0] ? S.sync[0].body : ''), 'D1 the one-click apply asks for the collision check');
    ok(S.confirms.length === 0, 'D2 a clean apply still asks nothing (docs/104)');

    S.sync.length = 0;
    w.doStateSync('reapply');
    await sleep(20);
    ok(!/check_collisions/.test(S.sync[0] ? S.sync[0].body : 'x'), 'D3 a pull that writes nothing live is not gated');

    S.sync.length = 0;
    w.doStateSync('apply', false, false, null, { informed: true });
    await sleep(20);
    ok(!/check_collisions/.test(S.sync[0] ? S.sync[0].body : 'x'), 'D4 the review modal (informed) is not asked again');

    // human, declines
    S.sync.length = 0; S.confirms.length = 0; S.ajax.length = 0; S.toasts.length = 0;
    S.syncQueue = [COLL]; S.confirmAnswer = false;
    w.doStateSync('apply');
    await sleep(40);
    ok(S.confirms.length === 1 && /qubits\.q1\.T2ramsey/.test(S.confirms[0]),
       'D5 a collision is asked ONCE, naming the field');
    ok(S.sync.length === 1, 'D6 Cancel: nothing is re-posted');
    ok(bannerGets().length === 1 && bannerGets()[0].opts.target === '#live-diverged-slot',
       'D7 Cancel: the banner is put up in place');
    ok(!w._applyInFlight, 'D8 the latch is released');

    // human, accepts
    S.sync.length = 0; S.confirms.length = 0; S.ajax.length = 0;
    S.syncQueue = [COLL, { status: 'ok', mode: 'apply' }]; S.confirmAnswer = true;
    w.doStateSync('apply');
    await sleep(60);
    ok(S.sync.length === 2, 'D9 OK re-posts once');
    const retry = S.sync[1] ? S.sync[1].body : '';
    ok(/ack_collision=1/.test(retry) && !/force=1/.test(retry) && !/ack_unseen=1/.test(retry),
       'D10 ...with its OWN token, never force=1 / ack_unseen=1: ' + retry);

    // the automatic merge (expectChip) never answers for the user
    S.sync.length = 0; S.confirms.length = 0; S.ajax.length = 0;
    S.syncQueue = [COLL]; S.confirmAnswer = true;
    w.doStateSync('apply', false, false, 'CHIP-A');
    await sleep(40);
    ok(S.confirms.length === 0 && S.sync.length === 1, 'D11 the automatic merge shows no dialog and does not retry');
    ok(bannerGets().length === 1, 'D12 ...it puts the naming banner up instead');
}

/* ── E. r2-07: liveConflict re-renders the banner in place ───────────── */
{
    const { w, S } = world('<div id="live-diverged-slot"></div><div id="pending-tray" data-change-count="1"></div>',
        'http://localhost/bulk', ['auto-apply.js'], function (w) {
        w.__chipToken = 'CHIP-A';
        w.doStateSync = function () {};
    });
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    S.ajax.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('liveConflict', { detail: { chip: 'CHIP-A', paths: ['qubits.q1.T2ramsey'] } }));
    const g = S.ajax.filter(function (a) { return a.url === '/state/diverged-banner'; });
    ok(g.length === 1 && g[0].method === 'GET' && g[0].opts.target === '#live-diverged-slot'
       && g[0].opts.swap === 'innerHTML',
       'E1 the declined pull puts the "choose which to keep" banner up on the OPEN page');
    S.ajax.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('liveConflict', { detail: { chip: 'CHIP-B', paths: [] } }));
    ok(S.ajax.length === 0, 'E2 a signal for another chip paints nothing');
}

console.log(fails ? (fails + ' failed') : ('all checks passed (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
