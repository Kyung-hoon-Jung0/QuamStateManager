/* sync-ux 2026-09-25 -- the ONE status control + ONE sync panel, client halves,
 * EXECUTED against the real shipped app.js under jsdom (docs/125 harness rule:
 * every bare global the code reads is the window's own).
 *
 *  S. the drift poll re-renders the control whenever the server's verdict
 *     signature differs from the one it shows -- raised (SE-01, SE-02) or
 *     LOWERED (SE-05) -- and never for an equal signature or mid-write.
 *  M. a cell older than the live chip gets the blue rule + "live now …", and
 *     loses it when the map no longer names it (SU-03).
 *  Q. a press made while another writer holds the apply latch is QUEUED and
 *     runs when the latch frees -- never dropped without a word (SYNCEXP-04).
 *  B. the control says what is happening the moment a write starts, and
 *     comes back on failure (SU-04).
 *  A. the in-panel second press: the first press only arms (SU-06 and default
 *     6 of the decisions -- replaces confirm()); the second runs it.
 *  P. same-field collision: ⇄ Pull & apply stays disabled until every
 *     colliding field has a side picked, and its Lost line names the loss.
 *
 * Run: node tests/sync_control_selfcheck.cjs  (driven by tests/test_sync_one_control.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function resp(payload, status) {
    return Promise.resolve({ status: status || 200, ok: (status || 200) < 400,
        json: function () { return Promise.resolve(payload); },
        text: function () { return Promise.resolve(typeof payload === 'string' ? payload : ''); } });
}

function world(bodyHtml) {
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + bodyHtml + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window;
    const S = { ajax: [], sync: [], drift: null, syncQueue: [], syncHold: null, toasts: [], review: 0 };
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: function (m, u, o) { S.ajax.push({ method: m, url: u, opts: o }); return Promise.resolve(); },
               trigger: function () {}, process: function () {}, on: function () {}, config: {} };
    w.fetch = function (url, opts) {
        const u = String(url);
        if (u.indexOf('/state/drift') === 0) return resp(S.drift || { ok: true });
        if (u.indexOf('/state/review') === 0) { S.review++; return resp(S.reviewHtml || '<div class="sync-panel"></div>'); }
        if (u.indexOf('/state/sync') === 0) {
            S.sync.push({ url: u, body: (opts && opts.body) || '' });
            if (S.syncHold) return S.syncHold;
            return resp(S.syncQueue.length ? S.syncQueue.shift() : { status: 'ok', mode: 'apply' });
        }
        return resp({});
    };
    w.confirm = function () { return false; };
    w.LiveEditUndo = { record: function () {}, clear: function () {}, _updateTrayBtn: function () {} };
    w.eval(APP);
    w.showToast = function (m, l) { S.toasts.push({ m: String(m), l: l }); };
    return { w: w, S: S };
}
const TRAY = function (sig, state, text) {
    return '<div id="pending-tray" data-change-count="1" data-change-sig="C1" data-edit-seq="E1"'
        + ' data-sync-sig="' + sig + '" data-sync-state="' + state + '">'
        + '<span class="sync-control sync-st-' + state + '" data-sync-state="' + state + '">'
        + '<button class="sync-control-main state-status-badge"><span class="state-status-dot">●</span>'
        + '<span class="sync-control-text">' + text + '</span></button>'
        + '<button class="sync-control-act">↑ Apply 1</button></span></div>';
};
const GRID = '<table id="bulk-table"><tr><td class="bulk-td"><input class="bulk-cell" data-dot-path="qubits.q1.T1"'
    + ' data-resolved="qubits.q1.T1" value="1.2e-05"></td>'
    + '<td class="bulk-td"><input class="bulk-cell" data-dot-path="qubits.q2.T1" data-resolved="qubits.q2.T1" value="1.1e-05"></td></tr></table>';

(async function () {
    /* ── S. the signature rule ───────────────────────────────────────── */
    {
        const { w, S } = world(TRAY('AAA', 'synced', 'In sync') + GRID);
        const trayGets = () => S.ajax.filter((a) => a.url === '/state/tray');
        // the first poll only records the edit_seq (docs/190 F05); same sig -> nothing
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'synced', sig: 'AAA', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(trayGets().length === 0, 'S1 an equal signature re-renders nothing');
        // raised: a dirty copy whose live moved (SE-01)
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'both', sig: 'BBB', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(trayGets().length === 1 && trayGets()[0].opts.target === '#pending-tray',
           'S2 a different signature re-renders the control (raise, dirty copy)');
        // lowered: the pill says a drifted state, the server says synced (SE-05)
        S.ajax.length = 0;
        w.document.getElementById('pending-tray').setAttribute('data-sync-sig', 'BBB');
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'synced', sig: 'CCC', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(trayGets().length === 1, 'S3 ...and LOWERS it too (another window took live)');
        // mid-write: this window's own request repaints the tray
        S.ajax.length = 0;
        w.document.getElementById('pending-tray').setAttribute('data-sync-sig', 'CCC');
        w._applyInFlight = true;
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'mine', sig: 'DDD', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(trayGets().length === 0, 'S4 never while this window is mid-write');
        w._applyInFlight = false;
        // an unreadable live file is just another signature (SE-02)
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'unreadable', sig: 'EEE', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(trayGets().length === 1, 'S5 "can\'t read the live chip" reaches an open page without F5');
    }

    /* ── M. stale cells ─────────────────────────────────────────────── */
    {
        const { w, S } = world(TRAY('A', 'live', 'Live chip changed') + GRID);
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'live', sig: 'A', stale: { 'qubits.q1.T1': '5e-05' } } };
        w._pollDrift(); await sleep(30);
        const td = w.document.querySelector('input[data-dot-path="qubits.q1.T1"]').closest('td');
        const sub = td.querySelector('.cell-live-now');
        ok(td.classList.contains('cell-live-stale') && sub && /live now 5e-05/.test(sub.textContent),
           'M1 a cell older than the live chip gets the rule and "live now 5e-05"');
        const other = w.document.querySelector('input[data-dot-path="qubits.q2.T1"]').closest('td');
        ok(!other.classList.contains('cell-live-stale'), 'M2 a cell the live chip did not change is left alone');
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'synced', sig: 'A', stale: {} } };
        w._pollDrift(); await sleep(30);
        ok(!td.classList.contains('cell-live-stale') && !td.querySelector('.cell-live-now'),
           'M3 ...and loses it when the map no longer names it');
        // a pane swap re-applies the last map (the grid re-renders often)
        S.drift = { ok: true, edit_seq: 'E1', sync: { state: 'live', sig: 'A', stale: { 'qubits.q2.T1': '9e-06' } } };
        w._pollDrift(); await sleep(30);
        const tbl = w.document.getElementById('bulk-table');
        tbl.outerHTML = GRID;
        w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: {} }));
        const td2 = w.document.querySelector('input[data-dot-path="qubits.q2.T1"]').closest('td');
        ok(td2.classList.contains('cell-live-stale'), 'M4 a re-rendered grid is re-marked');
    }

    /* ── Q + B. queued presses, busy state ──────────────────────────── */
    {
        const { w, S } = world(TRAY('A', 'mine', '1 unapplied edit'));
        w._applyInFlight = true;            // e.g. an Auto-Sync pull holds the latch
        w.doStateSync('apply');
        await sleep(20);
        ok(S.sync.length === 0, 'Q1 a press while another writer holds the latch waits');
        const t = w.document.querySelector('.sync-control-text').textContent;
        ok(/Queued/.test(t), 'Q2 ...and says so on the control (got "' + t + '")');
        w._applyInFlight = false;
        await sleep(200);
        ok(S.sync.length === 1 && /mode=apply/.test(S.sync[0].body), 'Q3 ...then runs once the latch frees');
        // B: busy text during the round trip, restored after a failure
        S.sync.length = 0;
        let release;
        S.syncHold = new Promise(function (r) { release = r; });
        w.document.getElementById('pending-tray').outerHTML = TRAY('A', 'mine', '1 unapplied edit');
        w.doStateSync('apply');
        await sleep(20);
        const busy = w.document.querySelector('.sync-control-text').textContent;
        ok(/Writing 1 edit to the live chip/.test(busy), 'B1 the control says it is writing at once (got "' + busy + '")');
        ok(w.document.querySelector('.sync-control-act').disabled, 'B2 ...and its button is disabled meanwhile');
        release({ status: 200, ok: true, json: function () { return Promise.resolve({ status: 'error', message: 'x' }); } });
        await sleep(40);
        S.syncHold = null;
        ok(/1 unapplied edit/.test(w.document.querySelector('.sync-control-text').textContent)
           && !w.document.querySelector('.sync-control-act').disabled,
           'B3 a failed write gives the control its words back');
    }

    /* ── A + P. the panel's second press and the per-field picks ─────── */
    {
        const PANEL = '<div id="state-review-overlay" class="state-review-overlay sync-panel-overlay" style="display:block">'
            + '<div id="state-review-host"><div class="sync-panel" data-conflicts="1"><table>'
            + '<tr class="sp-row sp-row-collide" data-path="qubits.q1.T1"><td>p</td><td class="sp-val">7.8e-05</td>'
            + '<td class="sp-val">7.9e-05</td><td></td><td>'
            + '<input type="radio" name="p0" value="mine" data-pick-path="qubits.q1.T1">'
            + '<input type="radio" name="p0" value="live" data-pick-path="qubits.q1.T1"></td></tr></table>'
            + '<button id="sp-merge" class="sp-choice" disabled><span class="sp-choice-label">⇄ Pull &amp; apply</span>'
            + '<span class="sp-lost">Pick a side first.</span></button>'
            + '<button class="sp-choice sp-take sync-arm" data-arm-label="Press again to discard 1 edit">'
            + '<span class="sp-choice-label">↓ Take live</span></button></div></div></div>';
        const { w, S } = world(TRAY('A', 'collide', '1 field changed on both sides') + PANEL);
        let ran = 0;
        const take = w.document.querySelector('.sp-take');
        w.SyncPanel.arm(take, null, function () { ran++; });
        ok(ran === 0 && /Press again/.test(take.textContent), 'A1 the first press only arms, and says what the second does');
        w.SyncPanel.arm(take, null, function () { ran++; });
        ok(ran === 1 && !/Press again/.test(take.textContent), 'A2 the second press runs it');

        const merge = w.document.getElementById('sp-merge');
        w.SyncPanel.picked();
        ok(merge.disabled, 'P1 ⇄ Pull & apply is disabled until every colliding field has a side');
        w.document.querySelector('input[value="live"]').checked = true;
        w.SyncPanel.picked();
        ok(!merge.disabled && /your qubits\.q1\.T1 edit 7\.8e-05/.test(merge.querySelector('.sp-lost').textContent),
           'P2 ...then it enables and its Lost line names the dropped edit');
        w.SyncPanel.merge(merge);
        await sleep(10);
        ok(S.sync.length === 0, 'P3 the merge with picks needs a second press');
        w.SyncPanel.merge(merge);
        await sleep(20);
        const body = decodeURIComponent(S.sync[0] ? S.sync[0].body : '');
        ok(/"qubits\.q1\.T1":"live"/.test(body) && !/check_collisions/.test(body),
           'P4 ...and posts the picks (' + body + ')');
    }

    /* ── W. QA fix6 (reviewer P1): the panel grows to its diff table ──
       A fixed 600 px panel made the table wrap a 13-digit frequency
       mid-number at every window size. _placeSyncPanel now measures the
       table at max-content and widens the host to fit, capped at
       min(960, viewport - 16). jsdom has no layout, so the widths are
       stubbed on the elements: max-content 820, the body inset 40. */
    {
        const OVER = '<div id="state-review-overlay" style="display:none"><div id="state-review-host"></div></div>';
        const { w, S } = world(TRAY('A', 'collide', '1 field changed on both sides') + OVER);
        S.reviewHtml = '<div class="sync-panel" data-conflicts="0"><div class="sp-body"><table class="sp-diff">'
            + '<tr class="sp-row"><td>qubits.q2.xy.RF_frequency</td><td class="sp-val">3,400,810,798.2070656</td></tr>'
            + '</table></div></div>';
        const stub = function (natural, vw) {
            Object.defineProperty(w, 'innerWidth', { configurable: true, value: vw });
            const host = w.document.getElementById('state-review-host');
            Object.defineProperty(host, 'offsetWidth', { configurable: true,
                get: function () { return parseFloat(host.style.width) || 0; } });
            const origQS = function (sel) { return w.Element.prototype.querySelector.call(host, sel); };
            host.querySelector = function (sel) {
                const el = origQS(sel);
                if (el && sel === '.sp-diff' && !el.__stubbed) {
                    el.__stubbed = true;
                    Object.defineProperty(el, 'offsetWidth', { configurable: true,
                        get: function () { return el.style.width === 'max-content' ? natural
                                                  : (parseFloat(host.style.width) || 0) - 40; } });
                    Object.defineProperty(el.parentElement, 'clientWidth', { configurable: true,
                        get: function () { return (parseFloat(host.style.width) || 0) - 40; } });
                }
                return el;
            };
            return host;
        };
        let host = stub(820, 1366);
        await w.openReview({ force: true }); await sleep(30);
        const w1 = parseFloat(host.style.width);
        ok(w1 >= 820 + 40 && w1 <= 960, 'W1 the panel widens to the table\'s natural width (got ' + w1 + ')');
        const tb = host.querySelector('.sp-diff');
        ok(tb && tb.style.width !== 'max-content', 'W2 the measuring width is restored on the table');
        host = stub(2000, 1366);
        await w.openReview({ force: true }); await sleep(30);
        ok(parseFloat(host.style.width) === 960, 'W3 ...never past its cap (got ' + host.style.width + ')');
        host = stub(2000, 700);
        await w.openReview({ force: true }); await sleep(30);
        ok(parseFloat(host.style.width) === 684, 'W4 ...nor past the viewport (got ' + host.style.width + ')');
        host = stub(300, 1366);
        await w.openReview({ force: true }); await sleep(30);
        ok(parseFloat(host.style.width) === 600, 'W5 a small table keeps the 600 px panel (got ' + host.style.width + ')');
    }

    console.log(fails ? ('FAILED (' + fails + ' of ' + asserts + ')')
        : ('sync_control_selfcheck: all ' + asserts + ' assertions passed'));
    process.exit(fails ? 1 : 0);
})();
