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
 *  F. liveedit-r2-09 -- a passive window's grid follows a foreign edit (after
 *     the tray lands, only while idle, through the guarded listener, focus
 *     kept); its own edit re-GETs nothing.
 *  G. F8 -- the red "modified" box follows the server's per-path `pending`
 *     flag after Ctrl+Z / Ctrl+Shift+Z (input, alias and list cells).
 *  H. F11 -- Escape and an outside click close the Live-Edit pickers and the
 *     Auto-Sync popover (capture phase; Datasets pickers untouched).
 *  I. F14 -- the grids' ⚡ confirm names what else the push carries (the
 *     tray's pending drawer, the saved working state), in all three grids.
 *  J. liveedit-r2-27 -- a note added/deleted re-marks the grids' row heads in
 *     place from the mutation's `marks` (pair map scoped to the pair table).
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
['app.js', 'bulk-edit.js', 'grid-virt.js', 'pair-edit.js', 'auto-apply.js', 'all-values.js', 'notes.js'].forEach(function (f) {
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

/* ── C'. r2-06 review: a human-length press on the tray is never swallowed ── */
{
    let release = null;
    const TRAY_BTN = '<button type="button" class="btn-apply-live">Apply to live now</button>';
    const { w, S } = world(bulkHtml('data-change-count="1" data-change-sig="S1"'),
        'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js'], function (w, S) {
        S.editHandler = function () {
            return new Promise(function (res) {
                release = function (body) { res({ status: 200, json: function () { return Promise.resolve(body); } }); };
            });
        };
    });
    mountBulk(w);
    // the tray's onclick="doStateSync('apply')" (inline handlers do not run
    // under runScripts 'outside-only', so the harness wires the same call)
    let clicks = 0;
    w.document.addEventListener('click', function (e) {
        const b = e.target.closest && e.target.closest('#pending-tray .btn-apply-live');
        if (b) { clicks++; w.doStateSync('apply'); }
    });
    const pdown = function (el) { el.dispatchEvent(new w.MouseEvent('pointerdown', { bubbles: true, button: 0 })); };
    const pup = function (el) { el.dispatchEvent(new w.MouseEvent('pointerup', { bubbles: true, button: 0 })); };
    const typeAndPress = function (qid, v) {
        const c = cellOf(w, qid, 'T1');
        c.focus(); c.value = v;
        c.dispatchEvent(new w.Event('input', { bubbles: true }));
        const old = w.document.querySelector('#pending-tray .btn-apply-live');
        pdown(old);                                           // mousedown on the tray button...
        c.dispatchEvent(new w.FocusEvent('focusout', { bubbles: true, relatedTarget: old }));
        return old;                                           // ...blurs the cell: the row commit starts
    };

    // 1. the commit lands BETWEEN mousedown and mouseup (a 40-200 ms press)
    const old1 = typeAndPress('q2', '1.25e-05');
    ok(S.edits.length === 1, "C'1 the blur starts the row commit");
    release({ ok: true, results: [{ dot_path: 'qubits.q2.T1', applied: true, display: '1.25e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="2" data-change-sig="S2">' + TRAY_BTN + '</div>' });
    await sleep(30);
    ok(!old1.isConnected, "C'2 the commit re-rendered the tray under the pressed button");
    pup(w.document.querySelector('#pending-tray .btn-apply-live'));   // release; the browser fires NO click
    await sleep(60);
    ok(S.sync.length === 1, "C'3 the press is not swallowed: the apply is posted (got " + S.sync.length + ')');
    ok(/seen_sig=S2(&|$)/.test(S.sync[0] ? S.sync[0].body : ''),
       "C'4 ...declaring the tray after the commit: " + (S.sync[0] && S.sync[0].body));

    // 2. a native click DID reach the new button: exactly one apply
    S.sync.length = 0; clicks = 0;
    typeAndPress('q3', '1.15e-05');
    release({ ok: true, results: [{ dot_path: 'qubits.q3.T1', applied: true, display: '1.15e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="3" data-change-sig="S3">' + TRAY_BTN + '</div>' });
    await sleep(30);
    const nb = w.document.querySelector('#pending-tray .btn-apply-live');
    pup(nb); nb.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    await sleep(60);
    ok(clicks === 1 && S.sync.length === 1, "C'5 a click that arrived is never doubled (clicks " + clicks + ', posts ' + S.sync.length + ')');

    // 3. released OUTSIDE the tray: the user let go of the press
    S.sync.length = 0;
    typeAndPress('q1', '1.35e-05');
    release({ ok: true, results: [{ dot_path: 'qubits.q1.T1', applied: true, display: '1.35e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="4" data-change-sig="S4">' + TRAY_BTN + '</div>' });
    await sleep(30);
    S.toasts.length = 0;
    pup(w.document.getElementById('table-pane'));
    await sleep(60);
    ok(S.sync.length === 0 && !S.toasts.some(function (t) { return /nothing was pressed/.test(t.m); }),
       "C'6 a release outside the tray presses nothing (the user let go -- no toast)");

    // 4. the new tray holds a DIFFERENT action there: never pressed for the user
    typeAndPress('q2', '1.26e-05');
    release({ ok: true, results: [{ dot_path: 'qubits.q2.T1', applied: true, display: '1.26e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="5" data-change-sig="S5" data-working-dirty="1">'
                  + '<button type="button" class="btn-apply-live" hx-post="/state/apply-to-live">Apply to live chip</button></div>' });
    await sleep(30);
    S.toasts.length = 0;
    pup(w.document.querySelector('#pending-tray .btn-apply-live'));
    await sleep(60);
    ok(S.sync.length === 0, "C'7 a button that changed its action is not pressed on the user's behalf");
    ok(S.toasts.some(function (t) { return /nothing was pressed/.test(t.m); }),
       "C'7b ...and the swallowed press is said out loud: " + JSON.stringify(S.toasts));

    // 5. a DIFFERENT press while a commit is awaited waits its turn (it was dropped)
    S.sync.length = 0;
    const c2 = cellOf(w, 'q3', 'T1');
    c2.focus(); c2.value = '1.16e-05';
    c2.dispatchEvent(new w.Event('input', { bubbles: true }));
    c2.dispatchEvent(new w.FocusEvent('focusout', { bubbles: true,
        relatedTarget: w.document.querySelector('#pending-tray') }));
    w.doStateSync('apply');
    w.doStateSync('apply');                                        // the double press
    w.doStateSync('apply', false, false, 'CHIP-A');                // the automatic merge
    await sleep(20);
    ok(S.sync.length === 0, "C'8 nothing posts while the commit is awaited");
    release({ ok: true, results: [{ dot_path: 'qubits.q3.T1', applied: true, display: '1.16e-05' }],
              tray_html: '<div id="pending-tray" data-change-count="6" data-change-sig="S6"></div>' });
    await sleep(300);
    ok(S.sync.length === 2, "C'9 both distinct presses ran, the double press once (got " + S.sync.length + ')');
    ok(S.sync.length === 2 && !/expect_chip/.test(S.sync[0].body) && /expect_chip=CHIP-A/.test(S.sync[1].body),
       "C'10 ...in the order they were pressed");
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

    // (review) the server now returns this on the real automatic path, with
    // the conflict tray re-rendered without "Auto-Sync is resolving this"
    S.sync.length = 0; S.ajax.length = 0;
    S.syncQueue = [Object.assign({}, COLL, { tray_html:
        '<div id="pending-tray" class="pending-tray-conflict" data-change-count="0" data-edit-seq="Z1">decide</div>' })];
    w.doStateSync('apply', false, false, 'CHIP-A');
    await sleep(40);
    const t = w.document.getElementById('pending-tray');
    ok(t && t.getAttribute('data-edit-seq') === 'Z1' && bannerGets().length === 1,
       'D13 the automatic collision swaps in the tray the server rendered, and bannered');
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

/* ── F. r2-09: a passive window's grid follows a foreign edit ────────── */
{
    const { w, S } = world(bulkHtml('data-change-count="1" data-change-sig="S1" data-edit-seq="E1"'),
        'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
    mountBulk(w);
    const bulkGets = function () { return S.ajax.filter(function (a) { return a.url === '/bulk'; }).length; };
    w.__lastUserAct = 0;
    w._editSeqSeen = 'E1';

    // this window's OWN edit: its tray was swapped to the same edit_seq
    S.ajax.length = 0; S.stateChanged = 0;
    ok(w._onDriftEditSeq({ edit_seq: 'E1b' }) === true, 'F0 the poll sees a move');
    w.document.getElementById('pending-tray').setAttribute('data-edit-seq', 'E2');
    await sleep(30);
    S.ajax.length = 0; S.stateChanged = 0;
    ok(w._onDriftEditSeq({ edit_seq: 'E2' }) === true, 'F1 ...and the next one');
    await sleep(60);
    ok(!S.ajax.some(function (a) { return a.url === '/state/tray'; }),
       'F2 (review) the tray is NOT re-fetched: it is the one rendered at this edit_seq');
    ok(S.stateChanged === 0 && bulkGets() === 0,
       'F3 a move this window\'s own tray already shows re-GETs no grid (docs/203)');

    // a lab-mate's edit: the tray does NOT show it
    S.ajax.length = 0; S.stateChanged = 0;
    ok(w._onDriftEditSeq({ edit_seq: 'E3' }) === true, 'F4 a foreign move is a signal');
    await sleep(60);
    ok(S.stateChanged === 1 && bulkGets() === 1,
       'F5 an idle window re-reads its grid once the tray has landed (got ' + bulkGets() + ')');

    // a busy window: nothing now, one re-read once it has been idle for 2 s
    S.ajax.length = 0; S.stateChanged = 0;
    w.__lastUserAct = Date.now();
    w._onDriftEditSeq({ edit_seq: 'E4' });
    await sleep(60);
    ok(bulkGets() === 0, 'F6 a window with a user in it keeps its grid for now');
    w.__lastUserAct = Date.now() - 5000;
    await sleep(2100);
    ok(bulkGets() === 1, 'F7 ...and follows once idle (retry), not at the next foreign edit');

    // a typed-but-uncommitted cell is never wiped (the listener's own guard)
    S.ajax.length = 0; S.stateChanged = 0;
    const q2 = cellOf(w, 'q2', 'T1');
    q2.value = '9.9e-05';
    w._onDriftEditSeq({ edit_seq: 'E5' });
    await sleep(60);
    ok(S.stateChanged === 1 && bulkGets() === 0, 'F8 typing in a cell keeps the grid (through the guarded listener)');
    q2.value = q2.getAttribute('data-orig');

    // the focused cell comes back after the swap
    S.ajax.length = 0;
    cellOf(w, 'q3', 'T1').focus();
    w._onDriftEditSeq({ edit_seq: 'E6' });
    await sleep(60);
    ok(bulkGets() === 1, 'F9 a focused but clean cell does not freeze the grid');
    const pane = w.document.getElementById('table-pane');
    const tb = w.document.querySelector('#bulk-table tbody');
    tb.innerHTML = bulkRow('q1', '1.3e-05') + bulkRow('q2', '1.2e-05') + bulkRow('q3', '1.05e-05');
    w.document.activeElement && w.document.activeElement.blur && w.document.activeElement.blur();
    w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: pane } }));
    await sleep(120);
    ok(w.document.activeElement === cellOf(w, 'q3', 'T1'),
       'F10 the focused cell is focused again after the re-render');
}

/* ── F'. r2-09 review: this window's OWN write is never judged mid-flight ── */
{
    const conflictTray = '<div id="pending-tray" class="pending-tray pending-tray-conflict" data-change-count="0"'
        + ' data-change-sig="S0" data-edit-seq="C1"><button class="btn-sm primary">Pull &amp; apply</button></div>';
    const { w, S } = world(bulkHtml('data-change-count="1" data-change-sig="S1" data-edit-seq="E1"'),
        'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
    mountBulk(w);
    const bulkGets = function () { return S.ajax.filter(function (a) { return a.url === '/bulk'; }).length; };
    const trayGets = function () { return S.ajax.filter(function (a) { return a.url === '/state/tray'; }).length; };
    w.__lastUserAct = 0;
    w._editSeqSeen = 'E1';

    // an apply is in flight: the save already moved the working copy
    w._applyInFlight = true;
    S.ajax.length = 0; S.stateChanged = 0;
    ok(w._onDriftEditSeq({ edit_seq: 'E1-saved' }) === false,
       "F'1 a move seen while this window's own apply is in flight is not judged");
    ok(w._editSeqSeen === 'E1' && trayGets() === 0 && bulkGets() === 0,
       "F'2 ...and not consumed: no tray, no grid re-GET");
    // the apply lands: its response swapped in the conflict tray (refused push)
    w.document.getElementById('pending-tray').outerHTML = conflictTray;
    w._applyInFlight = false;
    ok(w._onDriftEditSeq({ edit_seq: 'C1' }) === true, "F'3 the next poll judges it against the landed tray");
    await sleep(60);
    ok(trayGets() === 0 && bulkGets() === 0 && !!w.document.querySelector('#pending-tray.pending-tray-conflict'),
       "F'4 the conflict tray stays up (it carries data-edit-seq) and no grid is re-read");

    // a row commit (tracked) in flight counts as this window writing
    let rel = null;
    const pc = new Promise(function (r) { rel = r; });
    w._trackGridCommit(pc);
    ok(w._onDriftEditSeq({ edit_seq: 'C2' }) === false, "F'5 a tracked row commit in flight defers it too");
    rel({ ok: true }); await sleep(10);

    // the tray was re-rendered after the poll was SENT: the poll may predate it
    const issued = w.document.getElementById('pending-tray');
    w.document.getElementById('pending-tray').outerHTML =
        '<div id="pending-tray" data-change-count="1" data-change-sig="S3" data-edit-seq="C3"></div>';
    ok(w._onDriftEditSeq({ edit_seq: 'C2b' }, issued) === false,
       "F'6 a poll sent before this window's own tray render is not judged");
    ok(w._onDriftEditSeq({ edit_seq: 'C3' }, w.document.getElementById('pending-tray')) === true,
       "F'7 ...the next one is, against the tray it rendered");
    await sleep(60);
    ok(trayGets() === 0 && bulkGets() === 0, "F'8 own apply, own tray: nothing re-GET");

    // a genuine lab-mate move still follows
    ok(w._onDriftEditSeq({ edit_seq: 'X9' }, w.document.getElementById('pending-tray')) === true, "F'9 a foreign move");
    await sleep(60);
    ok(trayGets() === 1 && bulkGets() === 1, "F'10 ...re-reads the tray and the grid (got " + trayGets() + '/' + bulkGets() + ')');
}

/* ── G. F8: the red box follows the server's per-path pending flag ──── */
{
    const { w } = world(bulkHtml('data-change-count="1" data-change-sig="S1"'),
        'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
    mountBulk(w);
    const reverted = function (entries) {
        w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: entries } }));
    };
    // a list preview span, as the qubit grid renders one (alias + resolved axes)
    const LP = 'ports.analog_outputs.con1.4.1.exponential_filter';
    const td = w.document.createElement('td'); td.className = 'bulk-td';
    td.innerHTML = '<span class="bulk-cell-list bulk-cell-modified" data-path="qubits.q1.z.opx_output.exponential_filter"'
        + ' data-resolved="' + LP + '">[[0.1,300]]</span>';
    w.document.querySelector('tr[data-qubit="q1"]').appendChild(td);
    const span = td.firstChild;

    // partial undo: the undone path's LAST entry went
    const q2 = cellOf(w, 'q2', 'T1');
    markModified(q2, '1.2e-05', '1.1627458545397817e-05');
    reverted([{ dot_path: 'qubits.q2.T1', old_value_disp: '1.1627458545397817e-05', old_value_str: '1.162746e-05',
                old_kind: 'num', created: false, deleted: false, pending: false }]);
    ok(q2.value === '1.1627458545397817e-05' && !q2.classList.contains('bulk-cell-modified')
       && !q2.hasAttribute('data-baseline'),
       'G1 pending:false -- the reverted cell looks clean although the tray still holds another edit');

    // redo: the value is pending again
    const q3 = cellOf(w, 'q3', 'T1');
    reverted([{ dot_path: 'qubits.q3.T1', old_value_disp: '1.2e-05', old_value_str: '1.2e-05', old_kind: 'num',
                created: false, deleted: false, pending: true, pending_old_disp: '1.1e-05' }]);
    ok(q3.value === '1.2e-05' && q3.classList.contains('bulk-cell-modified')
       && q3.getAttribute('data-baseline') === '1.1e-05',
       'G2 pending:true -- a re-staged value is marked, with the ORIGINAL as its baseline');
    ok(!q3.classList.contains('dirty'), 'G3 ...and it is committed, not typed (not dirty)');

    // the alias cell is reached by its resolved path; no flag = today's behaviour
    const a1 = cellOf(w, 'q1', 'x180_amp');
    markModified(a1, '0.31', '0.3046');
    reverted([{ dot_path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude', old_value_disp: '0.305',
                old_value_str: '0.305', old_kind: 'num', created: false, deleted: false }]);
    ok(a1.value === '0.305' && a1.classList.contains('bulk-cell-modified'),
       'G4 no flag (a sync/pull patch): the marker is left exactly as before');
    reverted([{ dot_path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude', old_value_disp: '0.3046',
                old_value_str: '0.3046', old_kind: 'num', created: false, deleted: false, pending: false }]);
    ok(!a1.classList.contains('bulk-cell-modified'), 'G5 the alias cell follows the flag by data-resolved');

    // the list span: both directions
    reverted([{ dot_path: LP, old_value_disp: '[[0.1,300]]', old_kind: 'list', old_value_json: '[[0.1,300]]',
                created: false, deleted: false, pending: false }]);
    ok(!span.classList.contains('bulk-cell-modified'), 'G6 a list cell loses a box the log no longer names');
    reverted([{ dot_path: LP, old_value_disp: '[[0.2,300]]', old_kind: 'list', old_value_json: '[[0.2,300]]',
                created: false, deleted: false, pending: true, pending_old_disp: '[[0.1, 300]]' }]);
    ok(span.classList.contains('bulk-cell-modified') && !span.hasAttribute('data-baseline'),
       'G7 ...and gets it back when re-staged (a list keeps its own baseline rule)');
}

/* ── H. F11: Escape / an outside click close the Live-Edit popups ────── */
{
    const html = '<div id="table-pane"><div class="bulk-panel" id="bulk-panel"><h3 id="le-title">Live State Edit</h3>'
        + '<details class="bulk-colvis" id="pk-props"><summary>Properties</summary>'
        + '<div class="bulk-colvis-menu" id="bulk-colvis-menu"><input type="checkbox" id="pk-cb">'
        + '<details class="bulk-colvis-dyn" open><summary>x180</summary></details></div></details>'
        + '<details class="bulk-colvis bulk-qubitvis" id="pk-qubits"><summary>Qubits</summary>'
        + '<div class="bulk-colvis-menu" id="bulk-qubitvis-menu"><button type="button" id="pk-all">All</button></div></details>'
        + '</div></div>'
        + '<details class="bulk-colvis" id="ds-picker" open><summary>Datasets columns</summary><div>x</div></details>';
    const { w } = world(html, 'http://localhost/bulk', ['app.js']);
    const d = w.document;
    const esc = function (target) {
        const e = new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
        (target || d.body).dispatchEvent(e);
        return e;
    };
    const click = function (el) { el.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true })); };
    const props = d.getElementById('pk-props'), qubits = d.getElementById('pk-qubits'), ds = d.getElementById('ds-picker');

    props.open = true;
    d.getElementById('pk-cb').focus();
    esc(d.getElementById('pk-cb'));
    ok(!props.open, 'H1 Escape closes an open Live-Edit picker');
    ok(d.activeElement === props.querySelector('summary'), 'H2 ...and the keyboard lands on its summary');
    ok(ds.open, 'H3 a picker outside Live Edit (Datasets) is not touched by Escape');

    // the Qubits menu rebuilds itself inside its own click handler (bulk-edit.js
    // _buildQubitMenu): the click must not be read as "outside"
    qubits.open = true;
    const menu = d.getElementById('bulk-qubitvis-menu');
    menu.addEventListener('click', function () { menu.innerHTML = '<button type="button" id="pk-all">All</button>'; });
    click(d.getElementById('pk-all'));
    ok(qubits.open, 'H4 a click inside a picker whose menu re-renders keeps it open');
    click(d.getElementById('le-title'));
    ok(!qubits.open, 'H5 a click elsewhere closes it');
    ok(ds.open, 'H6 ...and leaves the Datasets picker alone');

    props.open = true; qubits.open = false;
    click(qubits.querySelector('summary'));
    ok(!props.open, 'H7 opening another picker closes the first');

    props.open = true;
    const pe = new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    pe.preventDefault();
    d.body.dispatchEvent(pe);
    ok(props.open, 'H8 an Escape a cell already consumed (preventDefault) leaves the picker open');

    // the Auto-Sync popover, first
    let closed = 0;
    w.AutoSync = { close: function () { closed++; const p = d.getElementById('auto-sync-pop'); if (p) p.remove(); } };
    const pop = d.createElement('div'); pop.id = 'auto-sync-pop'; d.body.appendChild(pop);
    esc();
    ok(closed === 1 && props.open, 'H9 Escape closes the Auto-Sync popover first (one popup per press)');
    esc();
    ok(!props.open, 'H10 ...and the next Escape the picker');
}

/* ── I. F14: the grid ⚡ confirm names everything the push carries ───── */
{
    // The push (applyEditsToLive) sends the WHOLE tray; the confirm used to
    // count only the cells typed on this screen.
    function trayF14(paths, dirty) {
        return '<div id="pending-tray" data-change-count="' + paths.length + '" data-change-sig="S1"'
            + ' data-working-dirty="' + (dirty ? '1' : '0') + '">'
            // the applied log reuses .tray-change-path inside the same tray
            + '<div class="applied-log"><code class="tray-change-path" title="qubits.q9.T1">qubits.q9.T1</code></div>'
            + (paths.length ? '<div id="tray-drawer" class="tray-drawer"><div class="tray-change-list">'
                + paths.map(function (p) {
                    return '<div class="tray-change-item"><code class="tray-change-path" title="' + p + '">' + p + '</code></div>';
                }).join('') + '</div></div>' : '')
            + '</div>';
    }
    function setTray(w, paths, dirty) {
        w.document.getElementById('pending-tray').outerHTML = trayF14(paths, dirty);
    }
    function count(s, sub) { return s.split(sub).length - 1; }
    const { w, S } = world(bulkHtml(), 'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
    mountBulk(w);
    setTray(w, ['qubits.q1.T2ramsey', 'qubits.q2.T1'], false);
    const c = cellOf(w, 'q2', 'T1');
    c.value = '1.25e-05'; c.dispatchEvent(new w.Event('input', { bubbles: true }));
    S.confirmAnswer = false;
    w.BulkEdit.applyAll(true);
    let m = S.confirms[S.confirms.length - 1] || '';
    ok(m.indexOf('Apply 1 edit across 1 qubit and push to the live chip?') === 0,
       'I1 the typed count still leads the confirm (got ' + JSON.stringify(m) + ')');
    ok(count(m, 'qubits.q1.T2ramsey') === 1 && /also carries 1 edit already in the tray/.test(m),
       'I2 the earlier committed edit the push also carries is named, once');
    ok(m.indexOf('qubits.q2.T1') < 0, 'I3 a typed path already in the tray is not listed as an extra');
    ok(m.indexOf('qubits.q9.T1') < 0, 'I4 the applied log (already on the chip) is never listed');
    ok(S.edits.length === 0, 'I5 declining the confirm commits nothing');

    const nBefore = S.confirms.length;
    w.BulkEdit.applyAll(false);
    m = S.confirms[S.confirms.length - 1] || '';
    ok(S.confirms.length === nBefore + 1 && m.indexOf('also carries') < 0 && m.indexOf('working state?') > 0,
       'I6 the plain working-state Apply all is unchanged (no push, no extras)');

    setTray(w, [], false);
    w.BulkEdit.applyAll(true);
    m = S.confirms[S.confirms.length - 1] || '';
    ok(m === 'Apply 1 edit across 1 qubit and push to the live chip?',
       'I7 an empty tray leaves the old text byte-identical (got ' + JSON.stringify(m) + ')');

    setTray(w, [], true);
    w.BulkEdit.applyAll(true);
    m = S.confirms[S.confirms.length - 1] || '';
    ok(/saved, not-yet-applied working state/.test(m) && !/\d+ edits? already/.test(m),
       'I8 a saved-but-unapplied working state is named, with no number');

    // the alias cell: the tray keys the RESOLVED leaf
    c.value = c.getAttribute('data-orig'); c.dispatchEvent(new w.Event('input', { bubbles: true }));
    const a = cellOf(w, 'q1', 'x180_amp');
    a.value = '0.31'; a.dispatchEvent(new w.Event('input', { bubbles: true }));
    setTray(w, ['qubits.q1.xy.operations.x180_DragCosine.amplitude', 'q.a', 'q.b', 'q.c', 'q.d', 'q.e', 'q.f', 'q.g'], false);
    w.BulkEdit.applyAll(true);
    m = S.confirms[S.confirms.length - 1] || '';
    ok(m.indexOf('x180_DragCosine') < 0, 'I9 an alias cell covers its resolved leaf in the tray');
    ok(/also carries 7 edits already in the tray: q\.a, q\.b, q\.c, q\.d, q\.e, \+2 more\./.test(m),
       'I10 a long tray lists five paths and counts the rest (got ' + JSON.stringify(m) + ')');
}
{
    // the pair / entity grids (pair-edit.js factory): the same confirm
    const P = 'bulk-pair';
    const html = '<div id="pending-tray" data-change-count="1" data-working-dirty="0"><div id="tray-drawer"><div class="tray-change-item">'
        + '<code class="tray-change-path" title="qubits.q1.T2ramsey">qubits.q1.T2ramsey</code></div></div></div>'
        + '<div id="table-pane"><div class="bulk-panel"><input type="search" id="bulk-search">'
        + '<div class="bulk-pair-divider" id="' + P + '-divider"><div class="bulk-colvis-menu" id="' + P + '-colvis-menu"></div>'
        + '<span id="' + P + '-search-count"></span><span id="' + P + '-dirty-count"></span>'
        + '<button id="' + P + '-apply-all" disabled></button><button id="' + P + '-apply-sync" disabled></button>'
        + '<button id="' + P + '-reset" disabled></button></div>'
        + '<div class="bulk-table-wrap"><table class="bulk-table bulk-pair-table" id="' + P + '-table"><thead><tr class="bulk-head-row">'
        + '<th class="bulk-corner" data-col-key="__id__"></th><th class="bulk-col-head ck-0" data-col-key="general__detuning" data-section="General" data-maxlen="12">'
        + '<span class="bulk-col-label">detuning</span></th><th class="bulk-apply-col"></th></tr></thead><tbody>'
        + '<tr data-qubit="q1-2" data-pair="q1-2"><th class="bulk-rowhead" data-col-key="__id__">q1-2</th>'
        + '<td class="bulk-td ck-0" data-col-key="general__detuning"><input type="text" class="bulk-cell" value="1" data-orig="1"'
        + ' data-dot-path="qubit_pairs.q1-2.detuning" data-resolved="qubit_pairs.q1-2.detuning" size="12"></td>'
        + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button><span class="bulk-row-error" hidden></span></td></tr>'
        + '</tbody></table></div></div></div>';
    const { w, S } = world(html, 'http://localhost/bulk', ['app.js', 'grid-virt.js', 'pair-edit.js']);
    w.BulkPairEdit.mount([{ key: 'general__detuning', label: 'detuning', section: 'General', unit: '',
                            default_on: true, editable: true, kind: 'scalar', maxlen: 12 }]);
    const c = w.document.querySelector('.bulk-cell[data-dot-path="qubit_pairs.q1-2.detuning"]');
    c.value = '2'; c.dispatchEvent(new w.Event('input', { bubbles: true }));
    w.BulkPairEdit.applyAll(true);
    const m = S.confirms[S.confirms.length - 1] || '';
    ok(/push to the live chip\?/.test(m) && m.split('qubits.q1.T2ramsey').length === 2,
       'I11 the pair grid ⚡ confirm names the tray edit it also pushes (got ' + JSON.stringify(m) + ')');
}
{
    // the All values tab
    const html = '<div id="pending-tray" data-change-count="1" data-working-dirty="0"><div id="tray-drawer"><div class="tray-change-item">'
        + '<code class="tray-change-path" title="qubits.q1.T2ramsey">qubits.q1.T2ramsey</code></div></div></div>'
        + '<button type="button" class="bulk-seg" data-pane="grid">Grid</button>'
        + '<button type="button" class="bulk-seg" data-pane="allvalues">All values</button>'
        + '<div data-bulk-pane="grid"></div><div data-bulk-pane="allvalues" hidden>'
        + '<input type="search" id="av-search"><span id="av-coverage"></span><span id="av-showing"></span>'
        + '<span id="av-dirty-count"></span><button id="av-apply" disabled></button>'
        + '<button id="av-apply-sync" disabled></button><button id="av-reset" disabled></button><div id="av-chips"></div>'
        + '<div class="av-scroll" id="av-scroll"><table class="av-table-virtual" id="av-table"><tbody id="av-tbody"></tbody></table></div></div>';
    const { w, S } = world(html, 'http://localhost/bulk', ['app.js', 'all-values.js'], function (w) {
        const base = w.fetch;
        w.fetch = function (u, o) {
            if (String(u).indexOf('/bulk/all-values') === 0) {
                return Promise.resolve({ status: 200, headers: { get: function () { return null; } },
                    json: function () { return Promise.resolve({ rows: [
                        ['qubits.q2.T2ramsey', '8.7e-06', 'scalar', 0]],
                        summary: { total: 1, editable: 1, readonly: 0, by_kind: {}, arrays: 0, empties: 0 } }); } });
            }
            return base(u, o);
        };
    });
    Object.defineProperty(w.document.getElementById('av-scroll'), 'clientHeight', { value: 600 });
    w.AllValues.switchPane('allvalues');
    await sleep(20);
    const g = w.document.querySelector('#av-tbody .av-group-row');
    if (g) g.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    const inp = w.document.querySelector('#av-tbody .av-input[data-dot-path="qubits.q2.T2ramsey"]');
    ok(!!inp, 'I12 the All values row renders its input');
    if (inp) {
        inp.value = '9e-06'; inp.dispatchEvent(new w.Event('input', { bubbles: true }));
        w.document.getElementById('av-apply-sync').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
        const m = S.confirms[S.confirms.length - 1] || '';
        ok(/^Apply 1 edits and push to the live chip\?/.test(m) && m.split('qubits.q1.T2ramsey').length === 2
           && m.indexOf('qubits.q2.T2ramsey') < 0,
           'I13 the All values ⚡ confirm names the tray edit it also pushes (got ' + JSON.stringify(m) + ')');
    }
}

/* ── J. r2-27: a note mutation re-marks the grid row heads in place ──── */
{
    const html = '<details id="notes-block"><summary class="notes-summary">Notes</summary>'
        + '<div id="notes-panel" data-count="0"><form class="notes-add"><input class="notes-add-subject">'
        + '<input class="notes-add-text"><button type="button" class="notes-add-go">Add note</button></form></div></details>'
        + '<table id="bulk-table"><tbody>'
        + '<tr data-qubit="q2"><th class="bulk-rowhead" data-col-key="__id__">q2<button class="bulk-pin bulk-pin-row"></button></th></tr>'
        + '<tr data-qubit="q12"><th class="bulk-rowhead" data-col-key="__id__">q12</th></tr>'
        + '</tbody></table>'
        + '<table id="bulk-pair-table"><tbody>'
        + '<tr data-qubit="q12" data-pair="q12"><th class="bulk-rowhead" data-col-key="__id__">q12</th></tr>'
        + '</tbody></table>';
    let answer = null, repins = 0;
    const { w } = world(html, 'http://localhost/bulk', ['notes.js'], function (w) {
        w.fetch = function () {
            return Promise.resolve({ status: answer.status || 200,
                json: function () { return Promise.resolve(answer.body); } });
        };
        w.__bulkRepin = function () { repins++; };
    });
    const d = w.document;
    const head = (tid, id) => d.querySelector('#' + tid + ' tr[data-qubit="' + id + '"] > th');
    function press(body, status) {
        answer = { body: body, status: status };
        d.querySelector('.notes-add-subject').value = 'qubits.q2';
        d.querySelector('.notes-add-text').value = 'x';
        d.querySelector('.notes-add-go').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
        return sleep(20);
    }
    const panel = '<div id="notes-panel" data-count="1"><form class="notes-add"><input class="notes-add-subject">'
        + '<input class="notes-add-text"><button type="button" class="notes-add-go">Add note</button></form></div>';

    await press({ ok: true, panel: panel, marks: { qubits: { q2: 'QA note: <b>q2</b> drifts' }, pairs: {} } });
    let q2 = head('bulk-table', 'q2');
    ok(q2.classList.contains('bulk-rowhead-note') && q2.getAttribute('title') === 'QA note: <b>q2</b> drifts',
       'J1 an added note marks its row head at once (class + title)');
    const mk = q2.querySelector('.bulk-note-mark');
    ok(!!mk && mk.textContent === '📝' && mk.nextElementSibling === q2.querySelector('.bulk-pin-row'),
       'J2 ...with the mark where the server puts it (after the id, before the pin)');
    ok(!q2.querySelector('b'), 'J3 note text is never parsed as HTML');
    ok(!head('bulk-table', 'q12').classList.contains('bulk-rowhead-note'), 'J4 other rows stay unmarked');
    ok(repins === 1, 'J5 pinned columns are re-laid after the row-head width moved');

    await press({ ok: true, panel: panel, marks: { qubits: {}, pairs: { q12: 'CZ drifts' } } });
    ok(!q2.classList.contains('bulk-rowhead-note') && !q2.hasAttribute('title') && !q2.querySelector('.bulk-note-mark'),
       'J6 a deleted note unmarks its row head at once');
    ok(head('bulk-pair-table', 'q12').classList.contains('bulk-rowhead-note')
       && !head('bulk-table', 'q12').classList.contains('bulk-rowhead-note'),
       'J7 a pair mark lights the pair row only, never the same-named qubit row');

    await press({ ok: true, panel: panel });
    ok(head('bulk-pair-table', 'q12').classList.contains('bulk-rowhead-note'),
       'J8 an answer without marks changes nothing (absent never means "clear all")');

    await press({ ok: false, note_conflict: true, stored: { text: 'theirs' },
                  marks: { qubits: { q2: 'theirs' }, pairs: {} } }, 409);
    ok(head('bulk-table', 'q2').getAttribute('title') === 'theirs'
       && !head('bulk-pair-table', 'q12').classList.contains('bulk-rowhead-note'),
       'J9 a 409 conflict answer re-marks too');
}

/* ── K. F15: a text coordinate is not a number in the grids' stats ───── */
{
    // grid_location "0,0" / "1,0" / "4,0": the header read "min 0 · max 40" and
    // the extremes were coloured, because the comma was stripped as grouping.
    function statsWorld(vals) {
        const r = world(bulkHtml(), 'http://localhost/bulk', ['app.js', 'grid-virt.js', 'bulk-edit.js']);
        ['q1', 'q2', 'q3'].forEach(function (q, i) {
            const c = cellOf(r.w, q, 'T1'); c.value = vals[i]; c.setAttribute('data-orig', vals[i]);
        });
        mountBulk(r.w);
        const d = r.w.document;
        return { stat: d.querySelector('[data-col-stats="T1"]').textContent,
                 marked: ['q1', 'q2', 'q3'].filter(function (q) {
                     const c = cellOf(r.w, q, 'T1');
                     return c.classList.contains('cell-best') || c.classList.contains('cell-worst');
                 }) };
    }
    let s = statsWorld(['0,0', '1,0', '4,0']);
    ok(s.stat === '' && s.marked.length === 0,
       'K1 a coordinate column gets no min/max and no extreme colouring (got ' + JSON.stringify(s) + ')');
    s = statsWorld(['1,000', '2,500', '12,345']);
    ok(s.stat === 'min 1,000 · max 12,345' && s.marked.join() === 'q1,q3',
       'K2 well-formed thousands groups still read as numbers (got ' + JSON.stringify(s) + ')');

    // the pair / entity grids (pair-edit.js factory) share the rule
    function pairStats(vals) {
        const P = 'bulk-pair';
        const row = function (id, v) {
            return '<tr data-qubit="' + id + '" data-pair="' + id + '"><th class="bulk-rowhead" data-col-key="__id__">' + id + '</th>'
                + '<td class="bulk-td ck-0" data-col-key="general__loc"><input type="text" class="bulk-cell" value="' + v + '" data-orig="' + v + '"'
                + ' data-dot-path="qubit_pairs.' + id + '.loc" data-resolved="qubit_pairs.' + id + '.loc" size="12"></td>'
                + '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button><span class="bulk-row-error" hidden></span></td></tr>';
        };
        const html = '<div id="table-pane"><div class="bulk-panel"><input type="search" id="bulk-search">'
            + '<div class="bulk-pair-divider" id="' + P + '-divider"><div class="bulk-colvis-menu" id="' + P + '-colvis-menu"></div>'
            + '<span id="' + P + '-search-count"></span><span id="' + P + '-dirty-count"></span>'
            + '<button id="' + P + '-apply-all" disabled></button><button id="' + P + '-apply-sync" disabled></button>'
            + '<button id="' + P + '-reset" disabled></button></div>'
            + '<div class="bulk-table-wrap"><table class="bulk-table bulk-pair-table" id="' + P + '-table"><thead><tr class="bulk-head-row">'
            + '<th class="bulk-corner" data-col-key="__id__"></th><th class="bulk-col-head ck-0" data-col-key="general__loc" data-section="General" data-maxlen="12">'
            + '<span class="bulk-col-label">loc</span><span class="bulk-col-stats" data-col-stats="general__loc"></span></th><th class="bulk-apply-col"></th></tr></thead><tbody>'
            + row('q1-2', vals[0]) + row('q2-3', vals[1]) + '</tbody></table></div></div></div>';
        const { w } = world(html, 'http://localhost/bulk', ['app.js', 'grid-virt.js', 'pair-edit.js']);
        w.BulkPairEdit.mount([{ key: 'general__loc', label: 'loc', section: 'General', unit: '',
                                default_on: true, editable: true, kind: 'scalar', maxlen: 12 }]);
        return w.document.querySelector('[data-col-stats="general__loc"]').textContent;
    }
    let ps = pairStats(['0,1', '4,0']);
    ok(ps === '', 'K3 the pair grid gives a coordinate column no min/max (got ' + JSON.stringify(ps) + ')');
    ps = pairStats(['1,000', '2,000']);
    ok(ps === 'min 1,000 · max 2,000', 'K4 ...while grouped numbers keep theirs (got ' + JSON.stringify(ps) + ')');
}

/* ── QA F2 (windows): the Json Tree View follows another window's edit ──
   A foreign edit_seq move re-reads /explorer/model and patches the leaf in
   place -- the row text AND the model the search reads -- keeping the
   expansion; a changed shape takes the soft re-render; a user mid-edit or a
   live-diff overlay is left alone. */
{
    const ST = { qubits: { q3: { chi: -353000, f_01: 5.1e9 } }, ports: { a: 1 } };
    const WI = { wiring: { qubits: { q3: { xy: '#/ports/a' } } } };
    let model = null;
    const html = '<div id="pending-tray" data-change-count="0" data-change-sig="" data-edit-seq="T1"></div>'
        + '<div id="table-pane"><div id="explorer-livediff-bar" class="livediff-bar" hidden></div>'
        + '<div id="explorer-tree-state"></div><div id="explorer-tree-wiring" style="display:none"></div></div>';
    const { w, S } = world(html, 'http://localhost/explorer', ['app.js'], function (w, S) {
        const f0 = w.fetch;
        w.fetch = function (u, o) {
            if (String(u).indexOf('/explorer/model') === 0) { S.modelGets = (S.modelGets || 0) + 1; return mkResp(model); }
            return f0(u, o);
        };
    });
    w.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(ST)), { defaultDepth: 3, crud: true });
    w.renderJsonTree('explorer-tree-wiring', JSON.parse(JSON.stringify(WI)), { defaultDepth: 1, crud: true });
    w.jsonTreeSetExpanded('explorer-tree-state', ['qubits', 'qubits.q3']);
    const rowVal = function () {
        const n = w.document.querySelector('#explorer-tree-state .tree-node[data-path="qubits.q3.chi"] .tree-val');
        return n && n.textContent;
    };
    ok(/353/.test(rowVal() || ''), 'X0 the tree renders chi (got ' + rowVal() + ')');
    w.__lastUserAct = 0;
    model = { ok: true, state: { qubits: { q3: { chi: -363000, f_01: 5.1e9 } }, ports: { a: 1 } }, wiring: WI };
    let r = await w._followOnExplorer();
    ok(r === 'patched' && /363/.test(rowVal() || '') && !/353/.test(rowVal() || ''),
       'X1 another window\'s value is patched into the row in place (got ' + r + ', ' + rowVal() + ')');
    const st = w.document.getElementById('explorer-tree-state');
    ok(st._treeData.qubits.q3.chi === -363000, 'X2 ...and into the model the search reads');
    ok(!S.ajax.some(function (a) { return a.url === '/explorer'; }), 'X3 ...without re-rendering the tree');
    // the foreign-edit path reaches it
    S.modelGets = 0;
    w._editSeqSeen = 'T1';
    model.state.qubits.q3.chi = -370000;
    w._onDriftEditSeq({ edit_seq: 'T2' });
    await sleep(80);
    ok(S.modelGets === 1 && /370/.test(rowVal() || ''),
       'X4 a foreign edit_seq move follows the tree (gets=' + S.modelGets + ', ' + rowVal() + ')');
    // a changed shape: soft re-render (keeps the view), never a patch
    S.ajax.length = 0;
    model = { ok: true, state: { qubits: { q3: { chi: -370000, f_01: 5.1e9, new_key: 1 } }, ports: { a: 1 } }, wiring: WI };
    r = await w._followOnExplorer();
    ok(r === 'refreshed' && S.ajax.some(function (a) { return a.url === '/explorer'; }),
       'X5 a new key takes the soft re-render (got ' + r + ')');
    // a user in the window: deferred
    S.modelGets = 0;
    w.__lastUserAct = Date.now();
    r = w._followOnExplorer();
    ok(r === null && S.modelGets === 0, 'X6 a window with a user in it is not patched now');
    w.__lastUserAct = 0;
    // the live-diff overlay owns the tree
    w.document.getElementById('explorer-livediff-bar').hidden = false;
    r = w._followOnExplorer();
    ok(r === null && S.modelGets === 0, 'X7 a live-diff overlay is left alone');
    w.document.getElementById('explorer-livediff-bar').hidden = true;
    ok(w._treeLeafDiff({ a: [1, 2] }, { a: [1, 3] }, '', []) === true
       && w._treeLeafDiff({ a: [1, 2] }, { a: [1, 2, 3] }, '', []) === false,
       'X8 a list element is a leaf, a list length change is a shape change');
}

console.log(fails ? (fails + ' failed') : ('all checks passed (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
