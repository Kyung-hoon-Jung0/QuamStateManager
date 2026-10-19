/* R3B MINOR 7 -- under Auto-Sync the plot-apply popup reports what the live
 * write did: "staged -- Auto-Sync is writing it" while the flush is out, then
 * written, refused (with the server's reason) or not written yet (the session
 * is merging a moved chip). It used to say "applied -- written to the chip"
 * the moment the value was staged.
 *
 * The REAL app.js + auto-apply.js under jsdom. Every server answer replayed
 * here (staging trays, apply-to-live bodies, HX-Trigger, X-SM-Error-Code) is
 * a REAL answer captured by tests/test_v151_r5b_plot_outcome.py; the fake
 * htmx only replays it in htmx 2's order: HX-Trigger events, the swap,
 * htmx:afterRequest, then the request's promise.
 *
 * Run: node tests/v151_plot_outcome_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const AUTO = fs.readFileSync(path.join(STATIC, 'auto-apply.js'), 'utf8');
const tick = (ms) => new Promise(r => setTimeout(r, ms || 30));
let checks = 0;
function ok(c, msg) { checks++; if (!c) throw new Error('FAIL: ' + msg); console.log('ok - ' + msg); }
const P1 = 'qubits.q1.f_01', P2 = 'qubits.q2.f_01';

function trayOf(html, d) {
    const t = new d.defaultView.DOMParser().parseFromString(html, 'text/html').getElementById('pending-tray');
    if (!t) throw new Error('no #pending-tray in a fixture answer');
    return t.outerHTML;
}

function boot(tray) {
    const dom = new JSDOM('<body><div id="status-bar"></div><div id="topbar-tray-slot"></div>' +
        '<div id="plot-apply-popup" style="display:none"><div id="plot-apply-context"></div>' +
        '<div id="plot-apply-extra" hidden></div><div id="plot-apply-verdict" hidden></div>' +
        '<div id="plot-apply-rows"></div><button id="plot-apply-all">Apply all</button></div></body>',
        { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document, requests = [], toasts = [], staged = [];
    d.getElementById('topbar-tray-slot').innerHTML = trayOf(tray, d);
    w.fetch = (url, opts) => {
        const u = String(url);
        if (u === '/chip/active-token') return Promise.resolve({ ok: true, json: async () => ({
            loaded: true, token: 'test-chip', name: 'chip', path: '/chip' }) });
        if (opts && opts.method === 'POST' && /^\/field\/edit/.test(u)) {
            // the REAL staging answer queued by the case
            const tray_html = staged.shift();
            if (!tray_html) throw new Error('a staging answer was not queued');
            return Promise.resolve({ status: 200, json: async () => ({ ok: true, tray_html }) });
        }
        if (u.startsWith('/field/peek')) return Promise.resolve({ ok: true, json: async () => ({
            ok: true, values: { [P1]: 5e9, [P2]: 5e9 }, errors: {}, resolved: {
                [P1]: { is_pointer: false, resolvable: true, candidates: [] },
                [P2]: { is_pointer: false, resolvable: true, candidates: [] } } }) });
        return new Promise(() => {});
    };
    function fireTriggers(trigger, elt) {
        if (!trigger) return;
        if (trigger.trim().charAt(0) === '{') {
            const obj = JSON.parse(trigger);
            Object.keys(obj).forEach(k => elt.dispatchEvent(new w.CustomEvent(k, { bubbles: true, detail: obj[k] })));
        } else {
            trigger.split(',').map(s => s.trim()).filter(Boolean)
                .forEach(k => elt.dispatchEvent(new w.CustomEvent(k, { bubbles: true, detail: {} })));
        }
    }
    function xhrOf(a) {
        return { status: a.status, responseText: a.body,
                 getResponseHeader: (h) => ({ 'hx-trigger': a.trigger || null,
                                              'x-sm-error-code': a.code || null })[h.toLowerCase()] || null };
    }
    w.htmx = { on() {}, process() {}, trigger() {}, ajax(method, url) {
        if (url !== '/state/apply-to-live') return Promise.resolve();
        return new Promise((resolve) => requests.push({
            answer(a) {
                const elt = d.getElementById('pending-tray');
                fireTriggers(a.trigger, elt);
                // htmx 2 swaps a 2xx; app.js lets a 409 carrying X-SM-Error-Code swap too
                if ((a.status >= 200 && a.status < 300) || (a.status === 409 && a.code)) {
                    elt.outerHTML = trayOf(a.body, d);
                }
                const host = elt.isConnected ? elt : d.body;
                host.dispatchEvent(new w.CustomEvent('htmx:afterRequest', { bubbles: true, detail: {
                    requestConfig: { path: url }, xhr: xhrOf(a), successful: a.status < 400 } }));
                resolve();
            },
            network() {
                d.body.dispatchEvent(new w.CustomEvent('htmx:afterRequest', { bubbles: true, detail: {
                    requestConfig: { path: url }, xhr: xhrOf({ status: 0, body: '' }), successful: false } }));
                resolve();
            } }));
    } };
    w.eval(APP);
    w.eval(AUTO);
    w.showToast = (msg) => toasts.push(String(msg));
    const row = (p) => d.querySelector('.plot-apply-row[data-input-path="' + p + '"]');
    const text = (r) => (r.querySelector('.plot-apply-row-action') || {}).textContent || '';
    const popupOpen = () => d.getElementById('plot-apply-popup').style.display !== 'none';
    return { dom, w, d, requests, toasts, staged, row, text, popupOpen,
             setTray: (html) => { d.getElementById('pending-tray').outerHTML = trayOf(html, d); } };
}

async function open(h) {
    await tick();
    h.w._openPlotApplyPopup([{ dot_path: P1, value: 5.1e9 }, { dot_path: P2, value: 5.2e9 }], 'scan', 'q1', [], null);
    await tick();
}

(async () => {
    let h;

    // 1. One row, written by its own flush. While the flush is out the row is
    //    staged and being written -- never "applied" / "written".
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.stage1);
    h.w.applyPlotRow(h.row(P1)); await tick();
    ok(h.requests.length === 1, 'the staged value starts one auto flush');
    const t1 = h.text(h.row(P1));
    ok(/staged/.test(t1) && /writing/.test(t1) && !/applied|written/.test(t1),
       'armed: the row says staged and being written while the flush is out: ' + t1);
    ok(h.popupOpen(), 'the popup waits for the answer');
    h.requests[0].answer(FX.flush1); await tick();
    ok(/applied/.test(h.text(h.row(P1))) && /written to the chip/.test(h.text(h.row(P1))),
       'the landed flush says written: ' + h.text(h.row(P1)));
    h.dom.window.close();

    // 2. A flush for an earlier edit is out when the row is staged; it ends
    //    before the row's edit reached the chip (its tray still holds the
    //    edit): it cannot answer for the row, the next flush does.
    h = boot(FX.tray0); await open(h);
    h.setTray(FX.stage1); await tick();
    ok(h.requests.length === 1, 'an earlier auto flush is in flight');
    h.staged.push(FX.stage2);
    h.w.applyPlotRow(h.row(P2)); await tick();
    h.requests[0].answer(FX.flush1_late); await tick();
    ok(!/applied|written/.test(h.text(h.row(P2))), 'the earlier flush does not claim the later edit: ' + h.text(h.row(P2)));
    ok(h.requests.length === 2, 'the edit still in the tray gets its own flush');
    h.requests[1].answer(FX.flush2); await tick();
    ok(/applied/.test(h.text(h.row(P2))) && /written to the chip/.test(h.text(h.row(P2))),
       'its own landed flush says written');
    h.dom.window.close();

    // 2b. ...and a clean tray whose sequence is OLDER than the row's staging
    //     answer (that write was decided before the edit) never confirms it.
    h = boot(FX.tray0); await open(h);
    h.setTray(FX.stage1); await tick();
    h.staged.push(FX.stage2);
    h.w.applyPlotRow(h.row(P2)); await tick();
    h.requests[0].answer(FX.flush1); await tick();
    ok(!/applied|written/.test(h.text(h.row(P2))),
       'an older clean tray does not confirm a newer edit: ' + h.text(h.row(P2)));
    h.dom.window.close();

    // 3. The out flush acquired the state after the row's edit: its clean tray
    //    (a sequence at least as new as the staging answer's) confirms it.
    h = boot(FX.tray0); await open(h);
    h.setTray(FX.both_stage1); await tick();
    h.staged.push(FX.both_stage2);
    h.w.applyPlotRow(h.row(P2)); await tick();
    h.requests[0].answer(FX.both_flush); await tick();
    ok(/written to the chip/.test(h.text(h.row(P2))), 'a write that carried the edit confirms it');
    ok(h.requests.length === 1, 'no second write for a value already written');
    h.dom.window.close();

    // 4. The staging answer arrives after the write that carried it: told at
    //    once while the tray on screen is still that write's receipt...
    h = boot(FX.tray0); await open(h);
    h.setTray(FX.both_stage2); await tick();
    h.requests[0].answer(FX.both_flush); await tick();
    h.w._markPlotRowApplied(h.row(P2), FX.both_stage2); await tick();
    ok(/written to the chip/.test(h.text(h.row(P2))), 'a late staging answer uses the receipt on screen');
    // ...and never once the tray on screen is another one (a new render with
    // its own sequence, e.g. a rebuilt store): no success claimed from memory.
    h.setTray(FX.tray0); await tick();
    const r4 = h.row(P1);       // held: a wrongly closed popup takes the row away
    h.w._markPlotRowApplied(r4, FX.both_stage1); await tick();
    ok(/writing/.test(h.text(r4)) && !/written to the chip/.test(h.text(r4)),
       'an old receipt is not reused once the tray on screen changed: ' + h.text(r4));
    h.dom.window.close();

    // 5. Stale, push only: the server stops the session. The row names the
    //    refusal; the popup stays open; no success toast.
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.off_stage);
    h.w.applyPlotRow(h.row(P1)); await tick();
    h.requests[0].answer(FX.off_flush); await tick();
    const t5 = h.text(h.row(P1));
    ok(/refused/.test(t5) && /live chip changed/.test(t5) && /nothing was written/.test(t5)
       && !/applied|written to/.test(t5), 'a stale refusal names the real reason: ' + t5);
    ok(h.popupOpen(), 'the refused row stays on screen');
    ok(!h.toasts.some(t => /^Applied/.test(t)), 'no success toast for a refused write');
    h.dom.window.close();

    // 6. Stale with pull armed: the session merges -- the row says not written
    //    yet and why, never "refused" and never "Auto-Sync is off".
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.merge_stage);
    h.w.applyPlotRow(h.row(P1)); await tick();
    h.requests[0].answer(FX.merge_flush); await tick();
    const t6 = h.text(h.row(P1));
    ok(/not written yet/.test(t6) && /merging/.test(t6) && !/refused|is off|written to/.test(t6),
       'a merging session says not written yet: ' + t6);
    h.dom.window.close();

    // 6b. The row was staged while an earlier flush was out, and that flush
    //     found the chip moved (merging): the conflict tray carries no armed
    //     attribute, which must not read as "Auto-Sync is off".
    h = boot(FX.tray0); await open(h);
    h.setTray(FX.merge_stage); await tick();
    ok(h.requests.length === 1, 'an earlier flush is out');
    h.staged.push(FX.merge_stage);
    h.w.applyPlotRow(h.row(P1)); await tick();
    h.requests[0].answer(FX.merge_flush); await tick();
    const t6b = h.text(h.row(P1));
    ok(/not written yet/.test(t6b) && /merging/.test(t6b) && !/is off|written to/.test(t6b),
       'a queued row under a merging conflict says merging: ' + t6b);
    h.dom.window.close();

    // 7. A save that failed (409 + X-SM-Error-Code): the server's own message.
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.fail_stage);
    h.w.applyPlotRow(h.row(P1)); await tick();
    h.requests[0].answer(FX.fail_flush); await tick();
    const t7 = h.text(h.row(P1));
    ok(/refused/.test(t7) && /Save failed/.test(t7) && /not writable/.test(t7),
       'a failed save names the server message: ' + t7);
    h.dom.window.close();

    // 8. Apply all: one flush, the popup stays until it answers, then closes
    //    with a toast that says written.
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.both_stage2);
    h.w.applyAllPlotRows(); await tick();
    ok(h.requests.length === 1, 'the batch is one auto flush');
    ok(h.popupOpen() && !h.toasts.some(t => /^Applied/.test(t)), 'no closing toast before the answer');
    h.requests[0].answer(FX.both_flush); await tick();
    ok(!h.popupOpen(), 'a confirmed batch closes the popup');
    ok(h.toasts.some(t => /^Applied 2 values/.test(t) && /written to the chip/.test(t)),
       'the closing toast confirms the write: ' + h.toasts.join(' | '));
    h.dom.window.close();

    // 9. Apply all refused: every row names it; the popup stays.
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.off_stage);
    h.w.applyAllPlotRows(); await tick();
    h.requests[0].answer(FX.off_flush); await tick();
    ok([P1, P2].every(p => /refused/.test(h.text(h.row(p)))), 'every row of a refused batch says refused');
    ok(h.popupOpen(), 'the popup stays with the refusal');
    h.dom.window.close();

    // 10. A lost answer (network error) never claims success.
    h = boot(FX.tray0); await open(h);
    h.staged.push(FX.stage1);
    h.w.applyPlotRow(h.row(P1)); await tick();
    h.requests[0].network(); await tick();
    const t10 = h.text(h.row(P1));
    ok(/could not be confirmed/.test(t10) && !/refused|written to/.test(t10),
       'a lost answer is unconfirmed, neither written nor refused: ' + t10);
    h.dom.window.close();

    // 11. Another write holds the shared latch; the session is turned off by
    //     the server before this value's flush starts: the row says so.
    h = boot(FX.tray0); await open(h);
    h.w._applyInFlight = true;
    h.staged.push(FX.stage1);
    h.w.applyPlotRow(h.row(P1)); await tick();
    ok(h.requests.length === 0, 'the busy latch holds the flush');
    h.d.dispatchEvent(new h.w.CustomEvent('autoApplyDisarm', { detail: { reason: 'conflict' } }));
    await tick();
    const t11 = h.text(h.row(P1));
    ok(/Auto-Sync turned off/.test(t11) && /conflict/.test(t11) && /not written/.test(t11)
       && !/refused|written to/.test(t11),
       'a disarm names why the value stayed staged: ' + t11);
    h.w._applyInFlight = false;
    h.dom.window.close();

    console.log('DONE (' + checks + ' assertions)');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
