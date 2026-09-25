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
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const ajaxCalls = [], triggerCalls = [];
window.htmx = {
    ajax: function (method, url, opts) { ajaxCalls.push({ method, url, opts }); return Promise.resolve(); },
    trigger: function (elt, name) { triggerCalls.push({ elt: elt, name: name }); },
    process: function () {},
};
global.htmx = window.htmx;

/* URL-routed stub — app.js fires unrelated fetches at eval time (e.g.
   /diagnostics/findings.json), which must never consume a section's queue. */
function mkResp(payload, status) {
    return Promise.resolve({
        status: status || 200,
        json: function () { return Promise.resolve(payload); },
        text: function () { return Promise.resolve(''); },
    });
}
const syncCalls = [], editCalls = [], preflightCalls = [], revertCalls = [];
let syncQueue = [], editQueue = [], preflightQueue = [], revertQueue = [];
window.fetch = global.fetch = function (url, opts) {
    const u = String(url);
    if (u.indexOf('/state/revert-last-apply/preflight') === 0) {
        revertCalls.push(u);
        return mkResp(revertQueue.length ? revertQueue.shift() : { ok: true });
    }
    if (u.indexOf('/state/overwrite-live/preflight') === 0) {
        preflightCalls.push(u);
        return mkResp(preflightQueue.length ? preflightQueue.shift() : { ok: true });
    }
    if (u.indexOf('/state/sync') === 0) {
        syncCalls.push({ url: u, body: (opts && opts.body) || '' });
        return mkResp(syncQueue.length ? syncQueue.shift() : { status: 'ok' });
    }
    if (u.indexOf('/field/edit') === 0) {
        editCalls.push({ url: u, body: (opts && opts.body) || '' });
        // queue entries are either a plain 200 payload, or {__status, body}
        const q = editQueue.length ? editQueue.shift() : { ok: true, results: [] };
        return (q && q.__status) ? mkResp(q.body, q.__status) : mkResp(q);
    }
    return mkResp({});
};

let confirmAnswer = false, lastConfirm = '';
window.confirm = function (msg) { lastConfirm = String(msg == null ? '' : msg); return confirmAnswer; };

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

    /* ── 1a. QA correctness-r2-03: Take live asks about ANOTHER window's
       edits it would destroy. The server's refusal carries discard:true; the
       confirm must ask the discard question (not "Apply everything"), a
       decline names that nothing was discarded, and OK re-posts with
       ack_unseen=1 -- carrying a force=1 already given, so the docs/65
       question is never asked twice. */
    syncCalls.length = 0; lastConfirm = '';
    syncQueue = [{ status: 'unseen_changes', discard: true, have: 1, seen: 0,
                   paths: ['qubits.q1.T2ramsey'],
                   message: 'Taking the live chip now would also discard 1 unapplied edit' }];
    confirmAnswer = false;
    const _toasts1a = [];
    const _st1a = window.showToast;
    window.showToast = function (m) { _toasts1a.push(String(m)); };
    window.doStateSync('discard');
    await flush(20);
    ok(/qubits\.q1\.T2ramsey/.test(lastConfirm) && /discard them too/i.test(lastConfirm)
       && !/Apply everything/.test(lastConfirm),
       'Take live: the confirm names the other window\'s edit and asks the DISCARD question (got: ' + lastConfirm + ')');
    ok(syncCalls.length === 1, 'Take live + decline: no re-post');
    ok(_toasts1a.some(function (t) { return /Nothing was discarded/.test(t); }),
       'Take live + decline: the toast says nothing was discarded (got: ' + _toasts1a.join(' | ') + ')');
    syncCalls.length = 0;
    syncQueue = [{ status: 'unseen_changes', discard: true, have: 1, seen: 0,
                   paths: ['qubits.q1.T2ramsey'], message: 'm' },
                 { status: 'ok', mode: 'discard', tray_html: null, replay: null }];
    confirmAnswer = true;
    window.doStateSync('discard', true);
    await flush(40);
    ok(syncCalls.length === 2 && /ack_unseen=1/.test(syncCalls[1].body)
       && /force=1/.test(syncCalls[1].body),
       'Take live + OK: re-post acknowledges AND keeps the force already given (got: '
       + (syncCalls[1] && syncCalls[1].body) + ')');
    window.showToast = _st1a;
    syncCalls.length = 0;

    /* ── 1b. "Keep mine — overwrite live" (docs/86) ────────────────────
       The third choice. It must be ONE confirm that actually names what it
       destroys, and it must force — an unforced push would land on the
       staleness conflict screen and ask a second time. */
    ajaxCalls.length = 0; preflightCalls.length = 0; lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: 7, unsaved: 0, reversible: true,
                        run_active: false, run_label: null }];
    confirmAnswer = false;
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(preflightCalls.length === 1, 'overwrite: preflights once before asking');
    ok(/7 values/.test(lastConfirm),
       'the confirm NAMES how many live values disappear (got: ' + lastConfirm + ')');
    ok(/Revert last apply/.test(lastConfirm),
       'the confirm says the push is reversible');
    ok(ajaxCalls.length === 0, 'declining posts nothing');

    ajaxCalls.length = 0; lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: 2, unsaved: 3, reversible: true,
                        run_active: true, run_label: 'window on port 5051' }];
    confirmAnswer = true;
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/run is in progress/.test(lastConfirm) && /5051/.test(lastConfirm),
       'a live run is named in the confirm, not hidden (got: ' + lastConfirm + ')');
    ok(/3 unsaved edits/.test(lastConfirm), 'unsaved edits are declared as riding along');
    const push = ajaxCalls.filter(function (c) {
        return c.method === 'POST' && c.url.indexOf('/state/apply-to-live') === 0; })[0];
    ok(!!push, 'accepting pushes the working state to live');
    ok(push && /force=1/.test(push.url),
       'the push FORCES — one confirm, not two (got: ' + (push && push.url) + ')');
    ok(push && push.opts && push.opts.target === '#pending-tray',
       'the response swaps the tray, which is where Revert last apply lives');

    /* QA correctness-r2-09: the push is held to the live content the confirm
       counted -- the preflight's hash rides the forced POST -- and a server
       refusal (keepMineReask) re-runs the preflight + confirm with the new count. */
    ajaxCalls.length = 0; preflightCalls.length = 0; lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: 1, unsaved: 0, reversible: true,
                        live_hash: 'abc123', run_active: false }];
    confirmAnswer = true;
    window.overwriteLiveWithWorking();
    await flush(30);
    const push2 = ajaxCalls.filter(function (c) {
        return c.method === 'POST' && c.url.indexOf('/state/apply-to-live') === 0; })[0];
    ok(push2 && /force=1/.test(push2.url) && /expect_live_hash=abc123/.test(push2.url),
       'the forced push carries the hash the confirm counted from (got: ' + (push2 && push2.url) + ')');
    ajaxCalls.length = 0;
    preflightQueue = [{ ok: true, live_changes: null, unsaved: 0, reversible: false,
                        live_read: 'unreadable', live_hash: null, run_active: false }];
    window.overwriteLiveWithWorking();
    await flush(30);
    const push3 = ajaxCalls.filter(function (c) {
        return c.method === 'POST' && c.url.indexOf('/state/apply-to-live') === 0; })[0];
    ok(push3 && !/expect_live_hash/.test(push3.url),
       'no hash (unreadable live) -> the plain forced push (got: ' + (push3 && push3.url) + ')');
    preflightCalls.length = 0; lastConfirm = ''; confirmAnswer = false;
    preflightQueue = [{ ok: true, live_changes: 3, unsaved: 0, reversible: true,
                        live_hash: 'def456', run_active: false }];
    document.dispatchEvent(new window.CustomEvent('keepMineReask', { bubbles: true }));
    await flush(120);
    ok(preflightCalls.length === 1 && /3 values/.test(lastConfirm),
       'keepMineReask asks again with the NEW count (got: ' + preflightCalls.length + ' / ' + lastConfirm + ')');

    /* jsontree-r2-30: ONE unsaved edit "is" saved — the verb follows the
       count the noun already followed ("Your 1 unsaved edit are saved"). */
    ajaxCalls.length = 0; lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: 1, unsaved: 1, reversible: true,
                        run_active: false, run_label: null }];
    confirmAnswer = false;
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/Your 1 unsaved edit is saved/.test(lastConfirm) && !/ edit are /.test(lastConfirm),
       'one unsaved edit reads "is saved", never "edit are" (got: ' + lastConfirm + ')');

    /* a refusal (archive / no chip) never opens a confirm */
    ajaxCalls.length = 0; lastConfirm = '';
    preflightQueue = [{ ok: false, message: 'read-only archive' }];
    confirmAnswer = true;
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(lastConfirm === '', 'a refused preflight never asks');
    ok(ajaxCalls.length === 0, 'and never pushes');

    /* an unreadable live folder still lets the user decide, honestly */
    lastConfirm = ''; confirmAnswer = false;
    preflightQueue = [{ ok: true, live_changes: null, unsaved: 0, reversible: true,
                        run_active: false }];
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/could not be read/.test(lastConfirm),
       'an unknown live count is stated, not faked (got: ' + lastConfirm + ')');

    /* QA correctness-r2-01: the server now says it CANNOT snapshot an unreadable
       pair (reversible:false) -- the confirm must stop promising the backup
       and say the push refuses instead of writing blind. */
    lastConfirm = ''; confirmAnswer = false;
    preflightQueue = [{ ok: true, live_changes: null, unsaved: 0, reversible: false,
                        live_read: 'unreadable', run_active: false }];
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/could not be read/.test(lastConfirm) && !/snapshotted first/.test(lastConfirm)
       && /refuses the overwrite/.test(lastConfirm),
       'an unreadable live never promises a snapshot; it says the push refuses (got: ' + lastConfirm + ')');
    lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: null, unsaved: 0, reversible: false,
                        live_read: 'missing', run_active: false }];
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/no state files/.test(lastConfirm) && !/snapshotted first/.test(lastConfirm),
       'a missing live folder says nothing is replaced (got: ' + lastConfirm + ')');

    /* QA diagnostics-r2-04: crash-class values the push carries are named in
       the SAME confirm (one clause -- never a second dialog, never a block) */
    lastConfirm = ''; confirmAnswer = false;
    preflightQueue = [{ ok: true, live_changes: 1, unsaved: 1, reversible: true,
                        run_active: false, crash_values: { count: 1,
                        sentence: '1 value on the live chip would crash a node run: q1 readout.' } }];
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(/would crash a node run: q1 readout/.test(lastConfirm),
       'Keep mine: the confirm names the crash-class values (got: ' + lastConfirm + ')');
    lastConfirm = '';
    preflightQueue = [{ ok: true, live_changes: 1, unsaved: 0, reversible: true,
                        run_active: false, crash_values: null }];
    window.overwriteLiveWithWorking();
    await flush(30);
    ok(lastConfirm && !/crash/.test(lastConfirm), 'a clean chip adds no clause');

    /* ...and the ⚡ pull-and-apply result line names them too */
    const _toasts = [], _realToast = window.showToast;
    window.showToast = function (m, l) { _toasts.push([String(m), l]); };
    syncCalls.length = 0;
    syncQueue = [{ status: 'ok', mode: 'apply', tray_html: null,
                   replay: { applied: 1, failed: [] },
                   crash_values: { count: 1, sentence: '1 value on the live chip would crash a node run: q1 readout.' } }];
    window.doStateSync('apply');
    await flush(40);
    const _last = _toasts[_toasts.length - 1] || ['', ''];
    ok(/applied them to the live chip/.test(_last[0]) && /would crash a node run/.test(_last[0])
       && _last[1] === 'warning',
       'pull-and-apply names the crash-class values, as a warning (got: ' + JSON.stringify(_last) + ')');
    /* sync-ux 2026-09-25 (default 2 of the user's decisions): success toasts
       go -- the status control says it for 4 s. Re-scoped from "keeps its
       green line" (a success TOAST): a clean apply now adds no toast and
       flashes the control instead; the crash-class case above stays a toast. */
    const _flashes = [], _realFlash = window.SyncControl && window.SyncControl.flash;
    if (window.SyncControl) window.SyncControl.flash = function (t) { _flashes.push(String(t)); };
    const _nToasts = _toasts.length;
    syncQueue = [{ status: 'ok', mode: 'apply', tray_html: null, replay: { applied: 1, failed: [] } }];
    window.doStateSync('apply');
    await flush(40);
    ok(_toasts.length === _nToasts && _flashes.length === 1 && /Written to live · 1 edit/.test(_flashes[0]),
       'a clean pull-and-apply says it on the control, not in a toast (got: ' + JSON.stringify(_flashes) + ' / '
       + JSON.stringify(_toasts.slice(_nToasts)) + ')');
    if (window.SyncControl) window.SyncControl.flash = _realFlash;
    window.showToast = _realToast;

    /* QA correctness-r2-08: a pull that caught the live pair mid-save is
       answered with its OWN token -- OK re-posts ack_torn=1 (never force=1),
       Cancel pulls nothing and says so. */
    syncCalls.length = 0; lastConfirm = '';
    syncQueue = [{ status: 'torn_live', mode: 'discard', count: 1,
                   message: 'The live chip looks mid-save: wiring.json still points X at Y.' },
                 { status: 'ok', mode: 'discard', tray_html: null, replay: null }];
    confirmAnswer = true;
    window.doStateSync('discard');
    await flush(40);
    ok(/mid-save/.test(lastConfirm) && /take it as it is/.test(lastConfirm),
       'torn_live asks, naming the mid-save (got: ' + lastConfirm + ')');
    ok(syncCalls.length === 2 && /ack_torn=1/.test(syncCalls[1].body) && !/force=1/.test(syncCalls[1].body),
       'OK re-posts with ack_torn=1 and not force (got: ' + (syncCalls[1] && syncCalls[1].body) + ')');
    syncCalls.length = 0;
    syncQueue = [{ status: 'torn_live', mode: 'discard', message: 'mid-save' }];
    confirmAnswer = false;
    window.doStateSync('discard');
    await flush(40);
    ok(syncCalls.length === 1, 'Cancel pulls nothing (one POST, no retry)');

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

    /* QA correctness-r2-06: the armed "Revert this session" asks through the
       preflight, NAMES the outside values it also rolls back, says the push
       writes at once, and fires the button's hx-post only on OK. */
    const _btn = document.createElement('button');
    triggerCalls.length = 0; revertCalls.length = 0; lastConfirm = '';
    revertQueue = [{ ok: true, push_armed: true, mine_n: 2, outside_n: 1,
                     outside: [{ path: 'qubits.q1.T2ramsey', now: '7.3779e-05', back_to: '1.817e-05' }] }];
    confirmAnswer = false;
    window.revertSessionConfirm(_btn);
    await flush(30);
    ok(revertCalls.length === 1, 'revert: preflights once before asking');
    ok(/qubits\.q1\.T2ramsey: 7\.3779e-05 → 1\.817e-05/.test(lastConfirm) && /ALSO 1 value/.test(lastConfirm),
       'the confirm names the outside value it rolls back (got: ' + lastConfirm + ')');
    ok(/ARMED/.test(lastConfirm) && /immediately/.test(lastConfirm) && !/review it first/i.test(lastConfirm),
       'it says the push writes at once, never "you review it first"');
    ok(triggerCalls.length === 0, 'Cancel fires nothing');
    revertQueue = [{ ok: true, push_armed: true, mine_n: 1, outside_n: 0, outside: [] }];
    confirmAnswer = true;
    window.revertSessionConfirm(_btn);
    await flush(30);
    ok(triggerCalls.length === 1 && triggerCalls[0].elt === _btn && triggerCalls[0].name === 'revertconfirmed',
       'OK fires the button\'s own request (revertconfirmed)');

    /* ── 2b. LiveEditUndo boundary discipline (audit-r10) ─────────────── */
    {
        const td = window.document.createElement('td');
        td.innerHTML = '<input class="bulk-cell" data-dot-path="qubits.qX.f_01"'
            + ' data-orig="1.0" value="1.0">';
        window.document.body.appendChild(td);
        const cell = td.querySelector('.bulk-cell');
        // staged entry (data-orig advanced to next by the commit) is dropped —
        // the server tier owns that undo now
        window.LiveEditUndo.record('fill', [{ dp: 'qubits.qX.f_01', prev: '1.0', next: '2.0' }]);
        cell.value = '2.0';
        cell.setAttribute('data-orig', '2.0');
        ok(window.LiveEditUndo.tryUndo() === false,
           'staged LiveEditUndo entry is dropped (falls through to the server tier)');
        ok(cell.value === '2.0', 'a staged value is never half-reverted from memory');
        // un-staged entry still restores
        cell.setAttribute('data-orig', '1.0');
        window.LiveEditUndo.record('fill2', [{ dp: 'qubits.qX.f_01', prev: '1.0', next: '2.0' }]);
        ok(window.LiveEditUndo.tryUndo() === true, 'un-staged entry restores');
        ok(cell.value === '1.0', 'restored to the recorded prev');
        // stateRestored is a hard boundary — the stack clears
        window.LiveEditUndo.record('fill3', [{ dp: 'qubits.qX.f_01', prev: '1.0', next: '3.0' }]);
        window.document.dispatchEvent(new window.CustomEvent('stateRestored', { bubbles: true }));
        ok(window.LiveEditUndo.tryUndo() === false,
           'stateRestored clears the in-memory undo stack');
        // Ctrl+Z mid-typing in a DIRTY cell restores the committed value and
        // never deletes a staged group behind the user's back
        cell.value = '9.9';
        cell.focus();
        ajaxCalls.length = 0;
        cell.dispatchEvent(new window.KeyboardEvent('keydown',
            { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        ok(cell.value === '1.0', 'Ctrl+Z in a dirty cell restores data-orig');
        ok(!ajaxCalls.some(function (c2) { return c2.url === '/undo'; }),
           'keystroke-level undo never posts the server /undo');
        td.remove();
    }

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

    /* ── 3b. plot-apply popup routes the r12 FSP 409 (docs/36 amendment) ── */
    const FSP_DP = 'ports.mw_outputs.con1.3.4.full_scale_power_dbm';
    const fspPlan = {
        port: 'con1/3/4', fsp_old: 12, fsp_new: -11,
        factor: Math.pow(10, 23 / 20), clip_count: 0, skipped: [],
        amps: [{ path: 'qubits.q1.xy.operations.x180.amplitude',
                 old: 0.2, new: 0.1, channel: 'q1.xy', op: 'x180', clips: false }],
    };
    const realFsp = window._openFspPopup;
    let fspOpens = 0;

    // comp on Apply All: resend = original rows + amp rows + fsp_ack=comp
    window._openFspPopup = function (plan, resend) { fspOpens++; resend('comp', plan); };
    rowsBox.innerHTML = mkRow(FSP_DP) + mkRow('qubits.q1.f_01');
    popup.style.display = 'flex';
    editCalls.length = 0;
    editQueue = [
        { __status: 409, body: { ok: false, fsp_compensation: fspPlan, fsp_dot_path: FSP_DP,
                                 error: 'confirm the amplitude compensation first' } },
        { ok: true, results: [], tray_html: null },
    ];
    window.applyAllPlotRows();
    await flush(30);
    ok(fspOpens === 1, 'FSP 409 on Apply All opens the compensation popup');
    ok(editCalls.length === 2, 'comp: exactly one resend');
    const resent = JSON.parse(editCalls[1] ? editCalls[1].body : '{}');
    ok(resent.fsp_ack === 'comp', 'resend carries fsp_ack=comp');
    ok(resent.updates && resent.updates.length === 3 &&
       resent.updates.some(function (u2) {
           return u2.dot_path === fspPlan.amps[0].path && u2.value === '0.1';
       }),
       'resend = 2 original rows + the compensated amp from the plan');
    ok(popup.style.display === 'none', 'comp success applies the rows and closes the popup');

    // cancel: nothing resent, rows pending, NO error text, button re-enabled
    window._openFspPopup = function (plan, resend) { resend('cancel', plan); };
    rowsBox.innerHTML = mkRow(FSP_DP);
    popup.style.display = 'flex';
    editCalls.length = 0;
    editQueue = [{ __status: 409, body: { ok: false, fsp_compensation: fspPlan,
                                          error: 'confirm first' } }];
    window.applyAllPlotRows();
    await flush(30);
    ok(editCalls.length === 1, 'cancel: no resend — nothing committed');
    const cRow = rowsBox.querySelector('.plot-apply-row');
    ok(!cRow.classList.contains('plot-apply-applied'), 'cancel leaves the row unapplied');
    const cErr = cRow.querySelector('.plot-apply-row-error');
    ok(cErr.hidden && cErr.textContent === '',
       'cancel shows NO error text (user choice, not a failure)');
    ok(!window.document.getElementById('plot-apply-all').disabled,
       'cancel re-enables Apply All');
    ok(popup.style.display === 'flex', 'cancel keeps the popup open');

    // per-row comp: transport switches to /field/edit-batch, one batch
    window._openFspPopup = function (plan, resend) { resend('comp', plan); };
    editCalls.length = 0;
    editQueue = [
        { __status: 409, body: { ok: false, fsp_compensation: fspPlan } },
        { ok: true, results: [], tray_html: null },
    ];
    const soloRow = rowsBox.querySelector('.plot-apply-row');
    window.applyPlotRow(soloRow);
    await flush(30);
    ok(editCalls.length === 2 &&
       editCalls[1].url.indexOf('/field/edit-batch') === 0,
       'per-row comp switches to /field/edit-batch');
    const b2 = JSON.parse(editCalls[1] ? editCalls[1].body : '{}');
    ok(b2.fsp_ack === 'comp' && b2.updates && b2.updates.length === 2,
       'per-row comp batch = row + amp with fsp_ack=comp');
    ok(soloRow.classList.contains('plot-apply-applied'),
       'row is marked applied from the batch-shaped response');
    window._openFspPopup = realFsp;

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
