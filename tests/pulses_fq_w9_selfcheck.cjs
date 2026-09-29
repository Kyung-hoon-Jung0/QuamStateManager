/* w9 final QA (Pulses) -- three user-visible defects, pinned in the REAL
 * htmx.min.js + REAL app.js (+ pulses.js where the page code lives) under
 * jsdom, with a fake XMLHttpRequest standing in for the server.
 *
 *  P2  another window's edit: the open inspector follows it. htmx.ajax with no
 *      `source` issues from document.body, whose per-element queue is `last`:
 *      the poll's second /state/tray GET (onSyncSig) REPLACED the queued
 *      /pulse/detail GET and the inspector kept the old value forever.
 *        F1  the poll's own sequence (onEditSeq, then onSyncSig) -> the
 *            inspector ends on the value a cold render shows
 *        F2  any other sourceless request in flight cannot drop it either
 *        F3  this window's own commit in flight is never aborted by it
 *            (#inspector-pane carries hx-sync="this:replace")
 *        F4  it never lands over a pulse the reader opened meanwhile
 *        F5  a window with a user in it waits, then follows once idle
 *  P3  a lab-refused Delete (hx-target #inspector-pane, HX-Retarget into
 *      #pulse-delete-result) must not purge the waveform plot: htmx 2.0.4
 *      raises beforeSwap on the ORIGINAL target
 *        T1  the refusal lands in the slot, the plot is untouched
 *        T2  a real swap of the inspector still purges it (the rule stands)
 *  P3  the section header's "preview unavailable" note goes once the class's
 *      own code has drawn the curve (H1), and stays when it could not (H2)
 *  P3  a page opened on a pulse (?pulse=) shows that pulse's row (R1 paged,
 *      R2 the virtual All view hands it to PulsesVT.reveal), once (R3)
 *
 * Run: node tests/pulses_fq_w9_selfcheck.cjs  (driven by tests/test_pulses_fq_w9.py)
 */
'use strict';
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

const P = 'qubits.q1.xy.operations.x180_DragCosine';
const Q = 'qubits.q2.xy.operations.x90';
const SLOT = 'qubit_pairs.q24-25.macros.cz_SNZ.flux_pulse_target';

function detailHtml(p, amp, extra) {
    return '<div id="pulse-detail-root" data-pulse-path="' + p + '">'
        + '<form class="inline-edit pulse-edit-form" hx-post="/pulse/edit" hx-target="#inspector-pane" hx-swap="innerHTML">'
        + '<input type="hidden" name="path" value="' + p + '">'
        + '<input type="text" name="value" class="edit-input" data-param="amplitude" data-committed="' + amp
        + '" value="' + amp + '"></form>' + (extra || '') + '</div>';
}

function world(bodyHtml, opts) {
    opts = opts || {};
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + bodyHtml + '</body></html>',
        { url: opts.url || 'http://localhost/pulses', runScripts: 'dangerously', pretendToBeVisual: true });
    const w = dom.window;
    w.Element.prototype.scrollIntoView = function () { (w.__scrolled = w.__scrolled || []).push(this); };
    w.XPathEvaluator = function () {};
    w.XPathEvaluator.prototype.createExpression = function () {
        return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
    };
    const xhrs = [];
    class FakeXHR {
        constructor() { this.readyState = 0; this.status = 0; this._h = {}; this.aborted = false; this.answered = false;
                        this.upload = { addEventListener() {} }; this.headers = {}; this.reqHeaders = {}; xhrs.push(this); }
        open(m, u) { this.method = m; this.url = u; }
        setRequestHeader(k, v) { this.reqHeaders[k] = v; }
        overrideMimeType() {}
        addEventListener(n, f) { (this._h[n] = this._h[n] || []).push(f); }
        getAllResponseHeaders() { return Object.keys(this.headers).map((k) => k + ': ' + this.headers[k]).join('\r\n'); }
        getResponseHeader(k) {
            const hit = Object.keys(this.headers).find((h) => h.toLowerCase() === String(k).toLowerCase());
            return hit ? this.headers[hit] : null;
        }
        abort() { this.aborted = true; if (this.onabort) this.onabort(); (this._h.abort || []).forEach(f => f({})); (this._h.loadend || []).forEach(f => f({})); }
        send(body) { this.body = body; this.sent = true; }
        answer(status, text, headers) {
            if (this.aborted || this.answered) return;
            this.answered = true;
            this.headers = headers || {};
            this.status = status; this.responseText = text; this.response = text;
            this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
            if (this.onload) this.onload();
            (this._h.load || []).forEach(f => f({}));
            (this._h.loadend || []).forEach(f => f({}));
        }
    }
    w.XMLHttpRequest = FakeXHR;
    w.fetch = opts.fetch || function () { return new w.Promise(function () {}); };
    w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    (opts.more || []).forEach((f) => w.eval(fs.readFileSync(path.join(STATIC, f), 'utf8')));
    const open = () => xhrs.filter((x) => x.sent && !x.answered && !x.aborted);
    return { w: w, d: w.document, xhrs: xhrs, open: open };
}

const TRAY0 = '<div id="pending-tray" data-edit-seq="E0" data-sync-sig="S0"></div>';
const TRAY1 = '<div id="pending-tray" data-edit-seq="E1" data-sync-sig="S1"></div>';
function answerAll(T, detailAmp, rounds) {
    // the server: every request sent so far is answered, then whatever htmx
    // issues from its queues next -- until nothing is left in flight
    for (let i = 0; i < (rounds || 8); i++) {
        const o = T.open();
        if (!o.length) return;
        o.forEach((x) => {
            if (x.url.indexOf('/state/tray') === 0) x.answer(200, TRAY1);
            else if (x.url.indexOf('/pulse/detail') === 0) {
                const p = decodeURIComponent(x.url.split('path=')[1].split('&')[0]);
                x.answer(200, detailHtml(p, detailAmp));
            } else x.answer(200, '');
        });
    }
}
const shown = (T) => { const i = T.d.querySelector('#inspector-pane input[data-param]'); return i ? i.value : null; };
const shownPath = (T) => { const r = T.d.querySelector('#pulse-detail-root'); return r ? r.getAttribute('data-pulse-path') : null; };
const FOREIGN = { edit_seq: 'E1', sync: { sig: 'S1', stale: {} } };
function poll(T) {
    // the drift poll's own sequence (app.js poll(): onEditSeq, then onSyncSig)
    const tray = T.d.getElementById('pending-tray');
    const a = T.w._onDriftEditSeq(FOREIGN, tray);
    const b = T.w._onDriftSyncSig(FOREIGN, tray);
    return [a, b];
}
function passive(T) {
    T.w._editSeqSeen = 'E0';
    T.w.__lastUserAct = 0;
    if (T.d.activeElement && T.d.activeElement.blur) T.d.activeElement.blur();
}

(async function main() {
    // ── F1: the poll's own sequence ─────────────────────────────────────────
    {
        const T = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        passive(T);
        const [a, b] = poll(T);
        ok(a === true && b === true, 'F1 setup: the poll takes the move as foreign and the tray signature as stale');
        const detailGets = () => T.xhrs.filter((x) => x.url.indexOf('/pulse/detail') === 0);
        ok(detailGets().length === 1 && detailGets()[0].sent,
           'F1 the inspector re-fetch is SENT while the tray GETs are out (got ' + detailGets().length + ')');
        answerAll(T, '0.249458');
        await tick(60);
        ok(shown(T) === '0.249458',
           'F1 the open inspector ends on the value a cold render shows (got ' + shown(T) + ')');
        ok(T.d.getElementById('pending-tray').getAttribute('data-edit-seq') === 'E1', 'F1 the tray followed too');
        const lanes = T.d.querySelectorAll('#tray-refresh-src, #insp-follow-src');
        ok(lanes.length === 2 && Array.from(lanes).every((l) => l.parentNode === T.d.body && l.hidden),
           'F1 each background refresh has its own hidden body-level lane');
    }

    // ── F2: any other sourceless (body-lane) request cannot drop it ─────────
    {
        const T = world(TRAY0 + '<div id="sink-a"></div><div id="sink-b"></div>'
            + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        passive(T);
        T.w.htmx.ajax('GET', '/some/body-lane', { target: '#sink-a', swap: 'innerHTML' }).catch(() => {});
        T.w._refreshAfterForeignEdit(FOREIGN);
        T.w.htmx.ajax('GET', '/other/body-lane', { target: '#sink-b', swap: 'innerHTML' }).catch(() => {});
        answerAll(T, '0.249458');
        await tick(60);
        ok(shown(T) === '0.249458',
           'F2 a sourceless request in flight and a later one never drop the inspector follow (got ' + shown(T) + ')');
    }

    // ── F6: ...nor the poll's own tray refresh ──────────────────────────────
    {
        const T = world(TRAY0 + '<div id="sink-a"></div><div id="sink-b"></div>'
            + '<div id="inspector-pane" hx-sync="this:replace"></div>');
        await tick();
        passive(T);
        T.w.htmx.ajax('GET', '/some/body-lane', { target: '#sink-a', swap: 'innerHTML' }).catch(() => {});
        T.w._refreshAfterForeignEdit(FOREIGN);
        T.w.htmx.ajax('GET', '/other/body-lane', { target: '#sink-b', swap: 'innerHTML' }).catch(() => {});
        answerAll(T, '0');
        await tick(60);
        ok(T.d.getElementById('pending-tray').getAttribute('data-edit-seq') === 'E1',
           'F6 sourceless requests around it never drop the poll\'s tray refresh');
    }

    // ── F3: this window's own commit in flight is never aborted ─────────────
    {
        const T = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        T.w.htmx.trigger(T.d.querySelector('form.pulse-edit-form'), 'submit');
        await tick();
        const commit = T.xhrs.find((x) => x.url.indexOf('/pulse/edit') === 0);
        ok(commit && commit.sent, 'F3 setup: the commit POST is in flight');
        passive(T);          // its lab check is slow: the reader has been idle for seconds
        T.w._refreshAfterForeignEdit(FOREIGN);
        ok(T.xhrs.some((x) => x.url.indexOf('/pulse/detail') === 0 && x.sent), 'F3 the follow is sent');
        ok(commit && !commit.aborted, 'F3 ...and the commit in flight is NOT aborted by it');
    }

    // ── F4: never over a pulse opened meanwhile ─────────────────────────────
    {
        const T = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        passive(T);
        T.w._refreshAfterForeignEdit(FOREIGN);
        const f = T.xhrs.find((x) => x.url.indexOf('/pulse/detail') === 0);
        T.d.getElementById('inspector-pane').innerHTML = detailHtml(Q, '0.5');   // the reader's row click landed
        f.answer(200, detailHtml(P, '0.249458'));
        await tick(60);
        ok(shownPath(T) === Q && shown(T) === '0.5',
           'F4 the follow never lands over the pulse the reader opened meanwhile (shows ' + shownPath(T) + ')');
        // ...and a follow that lands while the reader is typing in THIS pulse waits
        T.open().filter((x) => x.url.indexOf('/state/tray') === 0).forEach((x) => x.answer(200, TRAY0));
        await tick(60);                                  // the first refresh is over
        T.d.getElementById('inspector-pane').innerHTML = detailHtml(P, '0.27413');
        const n0 = T.xhrs.length;
        T.w._refreshAfterForeignEdit(FOREIGN);
        const g = T.xhrs.slice(n0).filter((x) => x.url.indexOf('/pulse/detail') === 0).pop();
        ok(!!g, 'F4 setup: a second follow is out');
        if (!g) { console.log('fatal'); process.exit(1); }
        const inp = T.d.querySelector('#inspector-pane input[data-param]');
        inp.value = '0.3';                                   // typed, not committed
        g.answer(200, detailHtml(P, '0.249458'));
        await tick(60);
        ok(shown(T) === '0.3', 'F4 a typed, uncommitted value is never swapped away (shows ' + shown(T) + ')');
    }

    // ── F5: a window with a user in it waits, then follows once idle ────────
    {
        const T = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        passive(T);
        T.w.__lastUserAct = Date.now() - 1500;               // a wheel scroll half a second before the poll
        T.w._refreshAfterForeignEdit(FOREIGN);
        ok(!T.xhrs.some((x) => x.url.indexOf('/pulse/detail') === 0), 'F5 a busy window keeps its pane at the poll');
        await tick(2300);
        ok(T.xhrs.some((x) => x.url.indexOf('/pulse/detail') === 0), 'F5 ...and follows once it has been idle');
        answerAll(T, '0.249458');
        await tick(60);
        ok(shown(T) === '0.249458', 'F5 the inspector ends on the new value (got ' + shown(T) + ')');
        // a pane that re-rendered meanwhile (a commit) is already fresh: no follow
        const U = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">' + detailHtml(P, '0.27413') + '</div>');
        await tick();
        passive(U);
        U.w.__lastUserAct = Date.now() - 1500;
        U.w._refreshAfterForeignEdit(FOREIGN);
        U.d.getElementById('inspector-pane').innerHTML = detailHtml(P, '0.249458');
        await tick(2300);
        ok(!U.xhrs.some((x) => x.url.indexOf('/pulse/detail') === 0),
           'F5 a pane re-rendered meanwhile is not fetched again');
    }

    // ── T: a lab-refused delete keeps the waveform plot ──────────────────────
    {
        const plot = '<div id="pulse-detail-plot" class="js-plotly-plot"></div>';
        const delStep = '<div class="pulse-delete-confirm">'
            + '<form id="delform" hx-post="/api/pulse/delete" hx-target="#inspector-pane" hx-swap="innerHTML" hx-sync="this:drop">'
            + '<input type="hidden" name="path" value="' + SLOT + '"><button type="submit">Delete</button></form>'
            + '<div id="pulse-delete-result" class="pulse-delete-result"></div></div>';
        const T = world(TRAY0 + '<div id="inspector-pane" hx-sync="this:replace">'
            + '<div id="pulse-detail-root" data-pulse-path="' + SLOT + '">' + delStep + plot + '</div></div>');
        await tick();
        const purged = [];
        T.w.Plotly = { purge: function (el) { purged.push(el.id); } };
        T.w.htmx.trigger(T.d.getElementById('delform'), 'submit');
        await tick();
        const x = T.xhrs.find((r) => r.url.indexOf('/api/pulse/delete') === 0);
        ok(x && x.sent, 'T setup: the delete POST is out');
        x.answer(400, '<div class="pulse-delete-refused" data-refused-path="' + SLOT + '">'
            + '<p class="pulse-delete-refused-msg">Your chip\'s own generate_config() refused this</p></div>',
            { 'HX-Retarget': '#pulse-delete-result' });
        await tick(60);
        ok(!!T.d.querySelector('#pulse-delete-result .pulse-delete-refused'),
           'T1 setup: the refusal lands in the delete step');
        ok(purged.indexOf('pulse-detail-plot') < 0 && !!T.d.getElementById('pulse-detail-plot'),
           'T1 the waveform plot is NOT purged by a refusal retargeted into the delete step (purged ' + JSON.stringify(purged) + ')');
        // T2: a swap that replaces the inspector still purges what it destroys
        T.w.htmx.ajax('GET', '/pulse/detail?path=' + encodeURIComponent(SLOT), { source: T.d.getElementById('inspector-pane'),
                                                                                target: '#inspector-pane', swap: 'innerHTML' });
        const y = T.xhrs.filter((r) => r.url.indexOf('/pulse/detail') === 0).pop();
        y.answer(200, detailHtml(SLOT, '0.1'));
        await tick(60);
        ok(purged.indexOf('pulse-detail-plot') >= 0, 'T2 a swap that replaces the inspector still purges its plot');
    }

    // ── H: the section header's note follows the lab drawing ─────────────────
    for (const labOk of [true, false]) {
        const data = { path: SLOT, actual_path: SLOT, pulses: [{ path: SLOT, actual_path: SLOT, label: 'q24-25 slot',
            role: 'pulse', color: '#1f77b4', index: 0, plot: null, needs_lab: true, lab_class: true }] };
        const NOTE = "pulse class 'SNZTwoFluxPulse' is recognized by the selected environment — preview unavailable for this class";
        const html = TRAY0 + '<div id="inspector-pane" hx-sync="this:replace"><div id="pulse-detail-root" data-pulse-path="' + SLOT + '">'
            + '<div class="pulse-plot-bar"><span class="pulse-plot-label muted">x</span>'
            + '<span class="pulse-synth-err">' + NOTE + '</span></div>'
            + '<div id="pulse-detail-plot" class="pulse-plot"></div>'
            + '<details open class="detail-section pulse-sec" data-pulse-path="' + SLOT + '" data-sec-index="0">'
            + '<summary class="section-header pulse-sec-head"><span class="pulse-sec-role">pulse</span>'
            + '<span class="pulse-synth-err">' + NOTE + '</span></summary></details>'
            + '<script type="application/json" id="pulse-detail-data">' + JSON.stringify(data) + '</script>'
            + '</div></div>';
        const labAnswer = labOk
            ? { ok: true, results: [{ path: SLOT, ok: true, plot: { ok: true, traces: [{ name: 'I', x: [0, 1, 2], y: [0, 0.2, 0] }] } }] }
            : { ok: true, results: [{ path: SLOT, ok: false, error: 'your class raised ValueError' }] };
        const fetchStub = function (url) {
            const body = String(url).indexOf('/api/pulse/lab-waveform') === 0 ? labAnswer
                : String(url).indexOf('/api/pulse/paths') === 0 ? { ok: true, paths: [SLOT] } : {};
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve('') });
        };
        const T = world(html, { fetch: fetchStub, more: ['pulses.js'] });
        T.w.Plotly = { newPlot: () => Promise.resolve(), react: () => Promise.resolve(), purge: () => {}, Plots: { resize() {} } };
        await tick();
        T.w.PulsesPage.initDetail();
        await tick(150);
        const head = T.d.querySelector('.pulse-sec-head .pulse-synth-err');
        const label = (T.d.querySelector('.pulse-plot-label') || {}).textContent || '';
        if (labOk) {
            ok(/drawn by the class's own code/.test(label), 'H1 setup: the lab drawing landed (label: ' + label.slice(0, 40) + ')');
            ok(head && head.hidden === true,
               'H1 the section header drops "preview unavailable" once the class drew the curve');
        } else {
            ok(head && head.hidden !== true && head.textContent === NOTE,
               'H2 a class that could not draw it keeps the note');
        }
    }

    // ── R: a page opened on a pulse shows its row ────────────────────────────
    {
        const rows = [];
        for (let i = 0; i < 60; i++) {
            const p = 'qubits.q30.xy.operations.op_' + i;
            rows.push('<tr class="clickable-row" data-pulse-path="' + p + '"><td>' + i + '</td></tr>');
        }
        const want = 'qubits.q30.xy.operations.op_57';
        // the link lands the app's own way: htmx swaps the page into #table-pane
        const inner = '<div id="pulse-open-loader" data-open-pulse="' + want + '" style="display:none"></div>'
            + '<div id="pulses-rows-wrap"><table id="pulses-table"><tbody>' + rows.join('') + '</tbody></table>'
            + '<nav class="pagination"><span class="page-info" data-current-page="2"></span></nav></div>';
        function land(X, html) {
            const tp = X.d.getElementById('table-pane');
            tp.innerHTML = html;
            tp.dispatchEvent(new X.w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: tp } }));
        }
        const T = world(TRAY0 + '<div id="table-pane"></div><div id="inspector-pane" hx-sync="this:replace"></div>',
            { more: ['pulses-vt.js', 'pulses.js'] });
        await tick();
        T.w.__scrolled = [];
        land(T, inner);
        const tr = T.d.querySelector('tr[data-pulse-path="' + want + '"]');
        ok(tr.classList.contains('row-selected') && T.w.__scrolled.indexOf(tr) >= 0,
           'R1 the paged table scrolls the open pulse row into view and marks it');
        ok(T.d.querySelectorAll('#pulses-rows-wrap tr.row-selected').length === 1, 'R1 ...and only that row');
        // R6: the loader's pulse lands in the inspector, which re-splits the
        // panes (base.html initSplit): the row is brought back in view once
        const inspLand = (X, p) => {
            const ip = X.d.getElementById('inspector-pane');
            ip.innerHTML = detailHtml(p, '0.1');
            ip.dispatchEvent(new X.w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: ip } }));
        };
        T.w.__scrolled = [];
        inspLand(T, want);
        ok(T.w.__scrolled.indexOf(tr) >= 0, 'R6 the row is scrolled in again once the inspector opened the pulse');
        T.w.__scrolled = [];
        inspLand(T, want);
        ok(T.w.__scrolled.length === 0, 'R6 ...once');
        land(T, inner);                                   // a second landing...
        await tick(5);
        T.w.__lastUserAct = Date.now() + 1;               // ...and the reader scrolls before the pulse arrives
        T.w.__scrolled = [];
        inspLand(T, want);
        ok(T.w.__scrolled.length === 0, 'R6 a reader who moved the page meanwhile is not scrolled back');
        T.w.__lastUserAct = 0;
        land(T, inner);
        const tr3 = T.d.querySelector('tr[data-pulse-path="' + want + '"]');
        ok(tr3.classList.contains('row-selected'), 'R3 setup: the second landing revealed its row');
        T.w.__scrolled = [];
        tr3.classList.remove('row-selected');
        T.d.getElementById('pulses-rows-wrap').dispatchEvent(new T.w.CustomEvent('htmx:afterSwap',
            { bubbles: true, detail: { target: T.d.getElementById('pulses-rows-wrap') } }));
        ok(T.w.__scrolled.length === 0 && !tr3.classList.contains('row-selected') && tr3.isConnected,
           'R3 once per landing: a later rows refresh never yanks the reader back to it');
        // R2: the virtual All view hands the row to its model
        const V = world(TRAY0 + '<div id="table-pane"></div><div id="inspector-pane"></div>',
            { more: ['pulses-vt.js', 'pulses.js'] });
        await tick();
        const revealed = [];
        V.w.PulsesVT = { active: () => true, init: () => true, reveal: (p) => { revealed.push(p); return true; } };
        V.w.__scrolled = [];
        land(V, inner.replace('<tbody>', '<tbody data-pulses-virtual="1">'));
        ok(revealed.length === 1 && revealed[0] === want && V.w.__scrolled.length === 0,
           'R2 the virtual All view hands ?pulse= to PulsesVT.reveal once its model is built');
        // R4: the page-size picker -> All keeps the pulse open in the inspector in view
        const noLoader = inner.replace(/<div id="pulse-open-loader"[^>]*><\/div>/, '')
                              .replace('<tbody>', '<tbody data-pulses-virtual="1">');
        V.d.getElementById('inspector-pane').innerHTML = detailHtml(want, '0.1');
        revealed.length = 0;
        land(V, noLoader);
        ok(revealed.length === 0, 'R5 a table swap of its own (Prev/Next, a filter) never scrolls to the open pulse');
        const sel = V.d.createElement('select');
        sel.innerHTML = '<option value="50">50</option><option value="0">All</option>';
        sel.value = '0';
        V.w.setPageSize(sel, '/pulses', '', 'quam_pulses_per_page');
        land(V, noLoader);
        ok(revealed.length === 1 && revealed[0] === want,
           'R4 switching the page size to All brings the open pulse on screen (' + JSON.stringify(revealed) + ')');
        land(V, noLoader);
        ok(revealed.length === 1, 'R4 ...once');
    }

    console.log(fails ? fails + ' FAILED' : 'all passed');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
