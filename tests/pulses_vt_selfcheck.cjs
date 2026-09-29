/* jsdom selfcheck: the virtual per-page "All" view of the Pulses table
 * (w9/pulsesall, quam_state_manager/web/static/pulses-vt.js + its app.js
 * hand-overs). jsdom has no layout, so a small geometry shim stands in for
 * one: #table-pane scrolls, every row is 30 px (an alias row 22 px, a
 * spacer its style height) and a row's box is the sum of the rows above it.
 *
 * What it pins:
 *   A  init renders a WINDOW of the model (not the 1,500 rows), in model order;
 *   B  a scroll anywhere leaves no blank band in the viewport;
 *   C  the first visible row stays put while estimated heights are replaced by
 *      measured ones (scrolling up from a jump into the middle);
 *   D  a header click sorts the MODEL exactly as the rendered table's own sort
 *      orders the same rows (string natural order + numeric, both directions);
 *   E  arrow keys walk the MODEL past the rendered window; Enter opens the row;
 *   F  a checked compare box survives its row leaving and re-entering the DOM;
 *   G  a value change patches a named row even when it is OFF screen (it is a
 *      row of the table, not a missing one) and re-lists only for a row the
 *      view does not hold;
 *   H  `pulses-changed` becomes the in-place refresh: htmx's request is
 *      cancelled, only the rows whose digest moved are fetched, the count
 *      follows, the rows on screen keep their place;
 *   I  a swap asks for digests only (vids=1) once the model holds the text --
 *      and only for the All view;
 *   J  the thumbnails of the rendered rows are asked for, filled in, and asked
 *      again when the chip moves (the stamp).
 *
 * Run: node tests/pulses_vt_selfcheck.cjs (driven by tests/test_pulses_virtual.py)
 */
'use strict';
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/pulses?per_page=0', pretendToBeVisual: true,
});
const { window } = dom;
for (const k of ['document', 'CustomEvent', 'Event', 'KeyboardEvent', 'MouseEvent', 'navigator',
                 'location', 'history', 'HTMLElement', 'Node', 'MutationObserver']) {
    try { global[k] = window[k]; } catch (e) {
        Object.defineProperty(global, k, { value: window[k], configurable: true, writable: true });
    }
}
global.window = window;
global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
const mem = {};
global.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
Object.defineProperty(window, 'localStorage', { value: global.localStorage, configurable: true });
global.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(() => f(Date.now()), 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const processed = [];
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: (el) => { processed.push(el); } };
global.htmx = window.htmx;
window.showToast = () => {};

// ---- the server, stubbed ----------------------------------------------------
const calls = [];
const server = { rows: null, stamp: 's1', rowHook: null };
function resp(status, body, headers) {
    const h = headers || {};
    return Promise.resolve({
        ok: status >= 200 && status < 300, status,
        headers: { get: (k) => (k in h ? h[k] : null) },
        json: () => Promise.resolve(typeof body === 'string' ? JSON.parse(body) : body),
        text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
    });
}
global.fetch = window.fetch = function (url, opts) {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url: String(url), body });
    const u = new URL(url, 'http://localhost');
    if (u.pathname === '/pulses/vids') {
        return resp(200, { v: 1, stamp: server.stamp, n: server.rows.length,
                           rows: server.rows.map((r) => [r.p, r.v]), empty: '<tr><td colspan="9">No pulses found.</td></tr>' });
    }
    if (u.pathname === '/pulses/vrows') {
        const by = new Map(server.rows.map((r) => [r.p, r]));
        return resp(200, { ok: true, stamp: server.stamp,
                           rows: body.paths.filter((p) => by.has(p)).map((p) => [p, by.get(p).v, by.get(p).h]),
                           gone: body.paths.filter((p) => !by.has(p)) });
    }
    if (u.pathname === '/pulses/sparks') {
        const by = new Map(server.rows.map((r) => [r.p, r]));
        const out = {};
        body.paths.forEach((p) => { const r = by.get(p); if (r) out[p] = [r.v, full(r)]; });
        const sg = server.sparkGone || new Set();
        sg.forEach((p) => { delete out[p]; });
        return resp(200, { ok: true, stamp: server.stamp, rows: out, warming: [],
                           gone: body.paths.filter((p) => !by.has(p) || sg.has(p)) });
    }
    if (u.pathname === '/pulse/row') {
        const p = u.searchParams.get('path');
        const r = server.rows.find((x) => x.p === p);
        if (!r) return resp(404, '');
        if (server.rowHook) { const h = server.rowHook(r); if (h) return h; }
        return resp(200, full(r), { 'X-Pulse-Ver': r.v, 'X-Pulse-Stamp': server.stamp });
    }
    return resp(404, '');
};

// ---- rows shaped like _pulse_row.html ---------------------------------------
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
function rowHtml(r, sparkInner) {
    const alias = r.alias;
    const spark = alias
        ? '<td class="pulse-spark-cell"><span class="muted pulse-alias-target">&rarr; ' + esc(r.op.replace(/^x/, 'x')) + '_t</span></td>'
        : (sparkInner === undefined
            ? '<td class="pulse-spark-cell" data-spark-lazy="1"><span class="pulse-spark-pending" aria-hidden="true"></span></td>'
            : '<td class="pulse-spark-cell">' + sparkInner + '</td>');
    return '<tr class="clickable-row" data-pulse-path="' + esc(r.p) + '" hx-get="/pulse/detail?path=' + encodeURIComponent(r.p) + '" title="' + esc(r.summary) + '">'
        + '<td class="pulse-sel-col" onclick="event.stopPropagation()">'
        + (alias ? '' : '<input type="checkbox" class="pulse-sel-chk" data-path="' + esc(r.p) + '">') + '</td>'
        + '<td><strong>' + esc(r.owner) + '</strong></td>'
        + '<td><span class="pulse-chan-chip">' + esc(r.ch) + '</span></td>'
        + '<td>' + esc(r.op) + (alias ? ' <span class="pointer-badge">alias</span>' : '')
        + (r.found ? '<br><span class="pulse-found-at muted">found at <code>' + esc(r.found) + '</code></span>' : '') + '</td>'
        + '<td><span class="pulse-class-chip">' + (alias ? '→' : esc(r.cls)) + '</span></td>'
        + spark
        + '<td>' + (r.len === null ? '-' : r.len) + '</td>'
        + '<td>' + (r.amp === null ? '-' : r.amp) + '</td>'
        + '<td>' + (r.used ? '<span class="pulse-usedby">' + r.used + '</span>' : '') + '</td></tr>';
}
function full(r) { return rowHtml(r, r.alias ? undefined : '<svg class="pulse-spark" data-amp="' + r.amp + '"><polyline points="0,0 1,' + r.amp + '"/></svg>'); }
function mkRow(i, over) {
    const q = 'q' + (1 + (i % 37));
    const op = ['x180_DragCosine', 'x90', 'readout', 'flux_step_v' + (i % 12), 'cz_' + i, 'EF_x180'][i % 6] + '_' + i;
    const r = Object.assign({
        p: 'qubits.' + q + '.xy.operations.' + op, owner: q, ch: ['xy', 'z', 'resonator'][i % 3], op,
        cls: ['DragCosinePulse', 'SquarePulse', 'FlatTopGaussianPulse', 'ErfSquarePulse'][i % 4],
        len: (i % 9 === 0) ? null : 16 + (i * 7) % 200, amp: (i % 11 === 0) ? null : +(((i * 37) % 1000) / 1000 - 0.3).toFixed(4),
        used: i % 5, alias: i % 13 === 4, found: i % 97 === 3 ? 'couplers.c' + i + '.operations' : '', summary: 'A=' + i,
    }, over || {});
    r.h = rowHtml(r); r.v = 'v' + i + '_' + (r.amp);
    return r;
}
server.rows = [];
for (let i = 0; i < 1500; i++) server.rows.push(mkRow(i));

// ---- the page -----------------------------------------------------------------
const doc = window.document;
function tableHtml(id, virtual, rowsHtml) {
    const th = (c, t, ty) => '<th class="sortable" data-col="' + c + '" data-type="' + ty + '">' + t + '</th>';
    return '<table id="' + id + '" class="data-table"><thead><tr><th class="pulse-sel-col"></th>'
        + th(1, 'Owner', 'str') + th(2, 'Channel', 'str') + th(3, 'Operation', 'str') + th(4, 'Class', 'str')
        + '<th>Waveform</th>' + th(6, 'Length', 'num') + th(7, 'Amp', 'num') + th(8, 'Used by', 'num')
        + '</tr></thead><tbody' + (virtual ? ' data-pulses-virtual="1"' : '') + '>' + (rowsHtml || '') + '</tbody></table>';
}
const payload = { v: 1, stamp: server.stamp, n: server.rows.length,
                  rows: server.rows.map((r) => [r.p, r.v, r.h]), empty: '<tr><td colspan="9">No pulses found.</td></tr>',
                  wide: [server.rows[1400].p, server.rows[777].p, server.rows[5].p] };
doc.body.innerHTML =
    '<div id="table-pane" style="overflow-y:auto">'
    + ' <div class="table-header-row"><h2>Pulses <small id="pulses-total">(' + server.rows.length + ')</small></h2>'
    + '  <div class="table-filter"><input type="search" name="q" value=""></div></div>'
    + ' <input type="hidden" id="pulses-owner-pick" value="">'
    + ' <nav id="pulse-channel-tabs"><ul><li><a class="active" hx-get="/pulses?rows=1&per_page=0">All</a></li></ul></nav>'
    + ' <div id="pulses-rows-wrap" hx-get="/pulses?rows=1&channel=&per_page=0">'
    + '  <div id="pulse-compare-bar" hidden><span id="pulse-compare-count">0</span></div>'
    + tableHtml('pulses-table', true)
    + '  <script type="application/json" id="pulses-vdata">' + JSON.stringify(payload).replace(/</g, '\\u003c') + '</script>'
    + '  <nav class="pagination"><span class="page-info" data-current-page="1"> (' + server.rows.length + ' total)</span></nav>'
    + ' </div>'
    + '</div>'
    + '<div id="ref-wrap" style="display:none"></div>';

// ---- the geometry shim ----------------------------------------------------------
const pane = doc.getElementById('table-pane');
const PANE_TOP = 100, PANE_H = 600, ABOVE = 200;
let scrollTop = 0;
function rowH(tr) {
    if (tr.classList.contains('pulse-vpad')) return tr.hidden ? 0 : (parseFloat(tr.firstChild.style.height) || 0);
    return tr.querySelector('.pulse-alias-target') ? 22 : 30;
}
function tbodyOf() { return doc.querySelector('#pulses-table tbody'); }
function contentH() {
    let h = ABOVE;
    const tb = tbodyOf();
    if (tb) Array.from(tb.rows).forEach((tr) => { h += rowH(tr); });
    return h + 40;
}
Object.defineProperty(pane, 'clientHeight', { get: () => PANE_H });
Object.defineProperty(pane, 'scrollHeight', { get: () => contentH() });
Object.defineProperty(pane, 'scrollTop', {
    get: () => scrollTop,
    set: (v) => { scrollTop = Math.max(0, Math.min(v, contentH() - PANE_H)); },
});
pane.getBoundingClientRect = () => ({ top: PANE_TOP, bottom: PANE_TOP + PANE_H, left: 0, right: 1000, height: PANE_H, width: 1000 });
const HTR = window.HTMLTableRowElement.prototype;
Object.defineProperty(HTR, 'offsetHeight', { get() { return this.isConnected ? rowH(this) : 0; } });
HTR.getBoundingClientRect = function () {
    const tb = this.parentNode;
    let top = PANE_TOP + ABOVE - scrollTop;
    for (let n = tb.firstElementChild; n && n !== this; n = n.nextElementSibling) top += rowH(n);
    const h = rowH(this);
    return { top, bottom: top + h, height: h, left: 0, right: 1000, width: 1000 };
};
window.HTMLTableSectionElement.prototype.getBoundingClientRect = function () {
    const top = PANE_TOP + ABOVE - scrollTop;
    return { top, bottom: top, height: 0, left: 0, right: 1000, width: 1000 };
};

// ---- load the shipped code --------------------------------------------------------
for (const f of ['app.js', 'pulses-vt.js']) {
    const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', f), 'utf8');
    try { window.eval(src); } catch (e) { console.error('FAIL: ' + f + ' did not evaluate under jsdom: ' + e.message); process.exit(1); }
}
const VT = window.PulsesVT;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function flush(n) { for (let i = 0; i < (n || 8); i++) await tick(0); }
async function scrollTo(y) { pane.scrollTop = y; pane.dispatchEvent(new window.Event('scroll')); await flush(); }
const rendered = () => Array.from(tbodyOf().querySelectorAll('tr[data-pulse-path]')).map((t) => t.getAttribute('data-pulse-path'));
function viewportCovered() {
    // every 10 px of the viewport's list band is covered by a rendered row
    const tb = tbodyOf();
    const boxes = Array.from(tb.rows).filter((t) => !t.classList.contains('pulse-vpad')).map((t) => t.getBoundingClientRect());
    const from = Math.max(PANE_TOP, PANE_TOP + ABOVE - scrollTop), to = PANE_TOP + PANE_H;
    const last = tb.rows[tb.rows.length - 2];
    const listEnd = last ? last.getBoundingClientRect().bottom : from;
    for (let y = from + 1; y < Math.min(to, listEnd) - 1; y += 10) {
        if (!boxes.some((b) => b.top <= y && b.bottom > y)) return false;
    }
    return true;
}

(async () => {
    // ---- A: init -------------------------------------------------------------
    VT.init(doc);
    await flush();
    const st = VT._state();
    ok(VT.active() && st.view.length === 1500, 'init adopts the whole model (1500 rows)');
    const r0 = rendered();
    ok(r0.length >= 20 && r0.length <= 70, 'only a window is rendered (' + r0.length + ' rows in the DOM)');
    if (!(r0.length <= 70)) { console.log('1 FAILED (fatal: not a window -- the rest would only time out)'); process.exit(1); }
    ok(r0.join('|') === st.view.slice(0, r0.length).join('|'), 'the rendered rows are the model in order');
    ok(doc.getElementById('pulses-vdata').textContent === '', 'the payload is consumed (not kept twice)');
    ok(processed.length >= r0.length, 'every rendered row is htmx-processed (its click opens the pulse)');
    // the column sizer: the server's `wide` rows, laid out but collapsed and anonymous
    const sz = Array.from(doc.querySelectorAll('#pulses-table tbody.pulse-vsizer > tr'));
    ok(sz.length === 3 && sz[0].cells.length === 9 && sz[0].cells[3].textContent.indexOf(server.rows[1400].op) >= 0,
       'the column sizer lays out the server\'s widest rows (' + sz.length + ')');
    ok(sz.every((t) => !t.hasAttribute('data-pulse-path') && !t.hasAttribute('hx-get') && !t.querySelector('[data-path], input')),
       'sizer rows carry no path, no click, no checkbox');
    ok(doc.querySelectorAll('tr[data-pulse-path]').length === r0.length, 'and are not rows of the table');
    ok(viewportCovered(), 'A: the viewport is covered at the top');

    // ---- J: thumbnails ---------------------------------------------------------
    await tick(120); await flush();
    const sparkCalls = calls.filter((c) => c.url === '/pulses/sparks');
    const asked = [].concat.apply([], sparkCalls.map((c) => c.body.paths));
    const renderedNow = rendered();
    ok(asked.length > 0 && asked.every((p) => renderedNow.indexOf(p) >= 0), 'J: thumbnails asked for the rendered rows only (' + asked.length + ')');
    ok(asked.every((p) => !server.rows.find((r) => r.p === p).alias), 'J: never for an alias row');
    const filled = Array.from(tbodyOf().querySelectorAll('td.pulse-spark-cell svg.pulse-spark')).length;
    ok(filled === asked.length && !tbodyOf().querySelector('td.pulse-spark-cell[data-spark-lazy]'), 'J: every asked thumbnail landed in its cell (' + filled + ')');

    // ---- B: scroll anywhere, no blank band ---------------------------------------
    let bOk = true;
    for (const y of [3000, 17777, 25000, 44000, 90]) {
        await scrollTo(y);
        if (!viewportCovered()) { bOk = false; console.error('  blank band at scrollTop=' + y); }
    }
    ok(bOk, 'B: after scrolls to 3000/17777/25000/44000/90 the viewport is always covered');

    // ---- C: the anchor holds while estimates become measurements -------------------
    await scrollTo(30000);
    let cOk = true;
    for (let i = 0; i < 12; i++) {
        const firstVis = Array.from(tbodyOf().querySelectorAll('tr[data-pulse-path]')).find((t) => t.getBoundingClientRect().bottom > PANE_TOP);
        const p = firstVis.getAttribute('data-pulse-path');
        const y0 = firstVis.getBoundingClientRect().top;
        await scrollTo(scrollTop - 150);
        const again = tbodyOf().querySelector('tr[data-pulse-path="' + p + '"]');
        const y1 = again ? again.getBoundingClientRect().top : NaN;
        if (Math.abs((y1 - y0) - 150) > 1.01) { cOk = false; console.error('  anchor moved ' + (y1 - y0) + ' for a 150 px scroll'); break; }
    }
    ok(cOk, 'C: scrolling up 12 x 150 px from a jump moves the rows exactly 150 px each time');

    // ---- D: sort the model like the table sorts its rows ----------------------------------
    const ref = doc.getElementById('ref-wrap');
    async function refOrder(col, times) {
        ref.innerHTML = tableHtml('ref-table', false, server.rows.map((r) => r.h).join(''));
        const th = ref.querySelector('th[data-col="' + col + '"]');
        for (let i = 0; i < times; i++) th.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        await tick(5);
        return Array.from(ref.querySelectorAll('tbody tr')).map((t) => t.getAttribute('data-pulse-path'));
    }
    for (const [col, times] of [[7, 1], [7, 2], [3, 1], [1, 2], [6, 1], [8, 2]]) {
        // reset both to server order first
        VT._state().view.length; // (no-op)
        const want = await refOrder(col, times);
        // the virtual table: fresh model order, then the same clicks
        doc.querySelector('#pulses-table').removeAttribute('data-sorted-col');
        doc.querySelectorAll('#pulses-table th.sortable').forEach((h) => h.classList.remove('sort-asc', 'sort-desc'));
        VT._state().view = server.rows.map((r) => r.p);   // server order
        const th = doc.querySelector('#pulses-table th[data-col="' + col + '"]');
        for (let i = 0; i < times; i++) th.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        await flush();
        const got = VT._state().view;
        ok(got.join('|') === want.join('|'), 'D: col ' + col + ' x' + times + ' sorts the model as the table sorts its rows');
        ok(doc.querySelector('#pulses-table').getAttribute('data-sorted-col') === String(col), 'D: the sort is remembered on the table (col ' + col + ')');
    }
    ref.innerHTML = '';

    // ---- E: keyboard walks the model -----------------------------------------------------
    await scrollTo(0);
    const clicks = [];
    doc.addEventListener('click', (e) => { const tr = e.target.closest && e.target.closest('tr[data-pulse-path]'); if (tr) clicks.push(tr.getAttribute('data-pulse-path')); });
    for (let i = 0; i < 150; i++) doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await flush();
    const v = VT._state().view;
    const selTr = tbodyOf().querySelector('tr.row-selected');
    ok(selTr && selTr.getAttribute('data-pulse-path') === v[149], 'E: 150 x ArrowDown selects the 150th row of the model (rendered: ' + !!selTr + ')');
    ok(scrollTop > 2000, 'E: and scrolled it into view (scrollTop ' + scrollTop + ')');
    ok(tbodyOf().querySelectorAll('tr.row-selected').length === 1, 'E: one selected row');
    doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    ok(clicks[clicks.length - 1] === v[148], 'E: ArrowUp + Enter opens the 149th row');

    // ---- F: a checked box survives leaving the DOM -------------------------------------------
    await scrollTo(0);
    const target = rendered().find((p) => { const t = tbodyOf().querySelector('tr[data-pulse-path="' + p + '"] .pulse-sel-chk'); return !!t; });
    const second = rendered().filter((p) => p !== target).find((p) => tbodyOf().querySelector('tr[data-pulse-path="' + p + '"] .pulse-sel-chk'));
    for (const p of [target, second]) {
        const c = tbodyOf().querySelector('tr[data-pulse-path="' + p + '"] .pulse-sel-chk');
        c.checked = true; window.pulseSelChanged(c);
    }
    await scrollTo(40000);
    ok(!tbodyOf().querySelector('tr[data-pulse-path="' + target + '"]'), 'F: premise -- the checked row left the DOM');
    await scrollTo(0);
    const back = tbodyOf().querySelector('tr[data-pulse-path="' + target + '"] .pulse-sel-chk');
    ok(back && back.checked, 'F: re-rendered checked');
    ok(doc.getElementById('pulse-compare-count').textContent === '2' && !doc.getElementById('pulse-compare-bar').hidden, 'F: the compare bar counts 2');
    window.clearPulseSelection();
    ok(!tbodyOf().querySelector('.pulse-sel-chk:checked') && VT.checkedPaths().length === 0, 'F: Clear empties the model selection too');

    // ---- G: value change: off-screen named rows are patched, not "missing" -----------------------
    calls.length = 0;
    const onScreen = rendered()[5];
    const offScreen = VT._state().view[1200];
    ok(!tbodyOf().querySelector('tr[data-pulse-path="' + offScreen + '"]'), 'G: premise -- row 1200 is off screen');
    for (const p of [onScreen, offScreen]) {
        const r = server.rows.find((x) => x.p === p);
        r.amp = 0.777; r.h = rowHtml(r); r.v = r.v + '_edited';
    }
    doc.dispatchEvent(new window.CustomEvent('pulses-rows-changed', { detail: { paths: [onScreen, offScreen] }, bubbles: true }));
    await tick(10); await flush();
    ok(calls.filter((c) => c.url.indexOf('/pulse/row?') === 0).length === 2, 'G: both named rows re-fetched through /pulse/row');
    ok(!calls.some((c) => c.url.indexOf('/pulses/vids') === 0), 'G: no whole-table re-list for rows the view holds');
    ok(calls.every((c) => c.url.indexOf('/pulse/row?') !== 0 || /[?&]vt=1(&|$)/.test(c.url)), 'G: asked with vt=1 (the digest header)');
    const onTr = tbodyOf().querySelector('tr[data-pulse-path="' + onScreen + '"]');
    ok(onTr && onTr.cells[7].textContent === '0.777', 'G: the on-screen row repainted in place');
    ok(VT._cache.get(offScreen).v === server.rows.find((x) => x.p === offScreen).v, 'G: the off-screen row\'s model entry took the new digest');
    calls.length = 0;
    doc.dispatchEvent(new window.CustomEvent('pulses-rows-changed', { detail: { paths: ['qubits.nowhere.xy.operations.new_one'] }, bubbles: true }));
    await tick(10); await flush();
    ok(calls.some((c) => c.url.indexOf('/pulses/vids') === 0), 'G: a row the view does not hold re-lists the table');

    // ---- H: pulses-changed is the in-place refresh ----------------------------------------------
    await scrollTo(9000); await flush();
    const anchorP = Array.from(tbodyOf().querySelectorAll('tr[data-pulse-path]')).find((t) => t.getBoundingClientRect().bottom > PANE_TOP + 5);
    const anchorPath = anchorP.getAttribute('data-pulse-path');
    const anchorY = anchorP.getBoundingClientRect().top;
    // the server: one row deleted (far above), one created, one changed
    const vnow = VT._state().view;
    const delP = vnow[10];
    server.rows = server.rows.filter((r) => r.p !== delP);
    const created = mkRow(5000, { p: 'qubits.q9.xy.operations.brand_new' });
    server.rows.splice(700, 0, created);
    const chg = server.rows[900]; chg.amp = 0.111; chg.h = rowHtml(chg); chg.v = chg.v + '_c';
    server.stamp = 's2';
    calls.length = 0;
    const wrap = doc.getElementById('pulses-rows-wrap');
    const ev = new window.CustomEvent('htmx:confirm', { bubbles: true, cancelable: true,
        detail: { elt: wrap, triggeringEvent: { type: 'pulses-changed' } } });
    wrap.dispatchEvent(ev);
    ok(ev.defaultPrevented, 'H: htmx\'s whole-table request is cancelled');
    const other = new window.CustomEvent('htmx:confirm', { bubbles: true, cancelable: true,
        detail: { elt: wrap, triggeringEvent: { type: 'keyup' } } });
    wrap.dispatchEvent(other);
    ok(!other.defaultPrevented, 'H: a request that is not pulses-changed is left to htmx');
    await tick(10); await flush(12);
    const vr = calls.filter((c) => c.url === '/pulses/vrows');
    const fetched = vr.length ? vr[0].body.paths.slice().sort() : [];
    ok(fetched.join('|') === [created.p, chg.p].sort().join('|'), 'H: fetched exactly the created + the changed row (' + fetched.join(', ') + ')');
    ok(VT._state().view.length === server.rows.length && VT._state().view.indexOf(delP) < 0 && VT._state().view.indexOf(created.p) >= 0, 'H: the model follows (deleted gone, created in)');
    const sortedNow = await refOrder(8, 2);   // the table is still sorted by Used by, desc (D)
    ref.innerHTML = '';
    ok(VT._state().view.join('|') === sortedNow.join('|'), 'H: in the server\'s order, the active sort re-applied');
    ok(doc.getElementById('pulses-total').textContent === '(' + server.rows.length + ')' && /\(1500 total\)/.test(doc.querySelector('.page-info').textContent), 'H: the counts follow');
    const anchorAfter = tbodyOf().querySelector('tr[data-pulse-path="' + anchorPath + '"]');
    ok(anchorAfter && Math.abs(anchorAfter.getBoundingClientRect().top - anchorY) <= 1, 'H: the row at the top of the view kept its place');
    ok(!wrap.classList.contains('htmx-request'), 'H: the busy mark is cleared');
    // J (cont.): the chip moved (stamp s1 -> s2): the rendered thumbnails are asked again
    await tick(120); await flush();
    const again = calls.filter((c) => c.url === '/pulses/sparks');
    const reasked = [].concat.apply([], again.map((c) => c.body.paths));
    const nowRendered = rendered().filter((p) => !server.rows.find((r) => r.p === p).alias);
    ok(nowRendered.length > 10 && nowRendered.every((p) => reasked.indexOf(p) >= 0), 'J: a moved stamp re-asks every rendered thumbnail (' + reasked.length + ')');
    ok(!tbodyOf().querySelector('td.pulse-spark-cell[data-spark-lazy]'), 'J: and they land');

    // ---- I: vids=1 on swaps once the model holds the text ----------------------------------------
    const inp = doc.querySelector('.table-filter input[name="q"]');
    function cfg(pathIn) {
        const d = { elt: inp, path: pathIn, parameters: {} };
        inp.dispatchEvent(new window.CustomEvent('htmx:configRequest', { bubbles: true, detail: d }));
        return d.path;
    }
    ok(/[?&]vids=1(&|$)/.test(cfg('/pulses?rows=1&channel=&per_page=0')), 'I: the All view asks for digests only');
    ok(!/vids=/.test(cfg('/pulses?rows=1&channel=&per_page=50')), 'I: a paged view never does');

    // ---- L: a slow answer never lands over a newer one (w9 review P2) ---------------------------
    const wrapL = doc.getElementById('pulses-rows-wrap');
    const confirmRefresh = () => wrapL.dispatchEvent(new window.CustomEvent('htmx:confirm', { bubbles: true, cancelable: true,
        detail: { elt: wrapL, triggeringEvent: { type: 'pulses-changed' } } }));
    server.stamp = 'b0:1:10:0:1:0';
    confirmRefresh(); await tick(10); await flush(12);
    const tL = rendered().find((p) => !server.rows.find((r) => r.p === p).alias);
    const rL = server.rows.find((r) => r.p === tL);
    const oldHtml = full(rL), oldVer = rL.v;
    let releaseL; const gateL = new Promise((r) => { releaseL = r; });
    server.rowHook = (r) => (r.p === tL
        ? gateL.then(() => resp(200, oldHtml, { 'X-Pulse-Ver': oldVer, 'X-Pulse-Stamp': 'b0:1:10:0:1:0' }))
        : null);
    doc.dispatchEvent(new window.CustomEvent('pulses-rows-changed', { detail: { paths: [tL] }, bubbles: true }));
    await tick(5);
    // meanwhile the chip moves (seq 11) and a structural refresh lands first
    rL.amp = 0.4242; rL.h = rowHtml(rL); rL.v = rL.v + '_L'; server.stamp = 'b0:1:11:0:1:0';
    confirmRefresh(); await tick(10); await flush(12);
    ok(VT._cache.get(tL).v === rL.v, 'L: the refresh put the newer text');
    releaseL(); await tick(10); await flush(12);
    ok(VT._cache.get(tL).v === rL.v, 'L: the slower, older answer did not overwrite it');
    const trL = tbodyOf().querySelector('tr[data-pulse-path="' + tL + '"]');
    ok(trL && trL.cells[7].textContent === '0.4242', 'L: the row on screen shows the newer amplitude');
    server.rowHook = null;

    // ---- M: a thumbnail answer never brings text --------------------------------------------------
    await tick(150); await flush();
    const tM = rendered().filter((p) => !server.rows.find((r) => r.p === p).alias)[2];
    const rM = server.rows.find((r) => r.p === tM);
    rM.amp = 0.5151; rM.h = rowHtml(rM); rM.v = rM.v + '_M';       // moved on the server, model not told
    calls.length = 0;
    VT._cache.get(tM).se = -1;                                     // its thumbnail is due
    VT._render(true);
    await tick(150); await flush(12); await tick(50); await flush(12);
    const askedM = calls.some((c) => c.url === '/pulses/sparks' && c.body.paths.indexOf(tM) >= 0);
    ok(askedM, 'M: premise -- the thumbnail was asked');
    ok(calls.some((c) => c.url.indexOf('/pulse/row?path=' + encodeURIComponent(tM)) === 0),
       'M: a thumbnail drawn for other text sends the row through its own door');
    const trM = tbodyOf().querySelector('tr[data-pulse-path="' + tM + '"]');
    ok(VT._cache.get(tM).v === rM.v && trM && trM.cells[7].textContent === '0.5151',
       'M: and the row shows the new text (taken there, with its stamp)');

    // ---- N: a path the thumbnail door calls gone: re-listed, never asked in a loop ------------------
    // N1: the listing still holds it (the two doors disagree): the asks stay bounded
    const tN = rendered().filter((p) => !server.rows.find((r) => r.p === p).alias)[4];
    server.sparkGone = new Set([tN]);
    calls.length = 0;
    VT._cache.get(tN).se = -1;
    VT._render(true);
    for (let i = 0; i < 14; i++) { await tick(80); await flush(); }
    const asksN = calls.filter((c) => c.url === '/pulses/sparks' && c.body.paths.indexOf(tN) >= 0).length;
    ok(asksN >= 1 && asksN <= 2, 'N: a gone path is asked at most twice (' + asksN + ')');
    ok(calls.some((c) => c.url.indexOf('/pulses/vids') === 0), 'N: `gone` re-lists the table');
    server.sparkGone = null;
    // N2: really gone: the re-list takes the row out
    server.rows = server.rows.filter((r) => r.p !== tN);
    VT._cache.get(tN).se = -1; VT._cache.get(tN).askEp = -1;
    VT._render(true);
    for (let i = 0; i < 10; i++) { await tick(80); await flush(); }
    ok(!VT._state().pos.has(tN), 'N: and a row that is really gone leaves the view');

    // ---- O: a 204 older than the listing is ignored; a newer one removes the row -------------------
    server.stamp = 'b0:1:12:0:1:0';
    confirmRefresh(); await tick(10); await flush(12);
    const tO = rendered()[6];
    server.rowHook = (r) => (r.p === tO ? resp(204, '', { 'X-Pulse-Stamp': 'b0:1:11:0:1:0' }) : null);
    doc.dispatchEvent(new window.CustomEvent('pulses-rows-changed', { detail: { paths: [tO] }, bubbles: true }));
    await tick(10); await flush(12);
    ok(VT._state().pos.has(tO), 'O: an older 204 does not remove a row the newer listing holds');
    server.rowHook = (r) => (r.p === tO ? resp(204, '', { 'X-Pulse-Stamp': 'b0:1:13:0:1:0' }) : null);
    doc.dispatchEvent(new window.CustomEvent('pulses-rows-changed', { detail: { paths: [tO] }, bubbles: true }));
    await tick(10); await flush(12);
    ok(!VT._state().pos.has(tO), 'O: a newer 204 does');
    server.rowHook = null;

    // ---- P: the open pulse an address named is brought on screen (w9 final QA P3) -----------------
    await scrollTo(0);
    const stP = VT._state();
    const far = stP.view[stP.view.length - 3];
    ok(rendered().indexOf(far) < 0, 'P: premise -- the named row is far off screen (not rendered)');
    ok(VT.reveal(far) === true, 'P: reveal() takes a row of the model');
    await flush();
    const trP = tbodyOf().querySelector('tr[data-pulse-path="' + far.replace(/"/g, '\\"') + '"]');
    const bP = trP && trP.getBoundingClientRect();
    ok(trP && bP.top >= PANE_TOP && bP.bottom <= PANE_TOP + PANE_H,
       'P: the row is rendered INSIDE the viewport (' + (bP ? bP.top + '..' + bP.bottom : 'not rendered') + ')');
    ok(trP && trP.classList.contains('row-selected') && VT._state().sel === far,
       'P: ...marked as the keyboard\'s row');
    ok(viewportCovered(), 'P: no blank band after the jump');
    const onScreenP = rendered().find((p) => {
        const t = tbodyOf().querySelector('tr[data-pulse-path="' + p.replace(/"/g, '\\"') + '"]');
        const b = t.getBoundingClientRect();
        return b.top >= PANE_TOP + 60 && b.bottom <= PANE_TOP + PANE_H - 60;
    });
    const topBefore = pane.scrollTop;
    VT.reveal(onScreenP);
    await flush();
    ok(pane.scrollTop === topBefore, 'P: a row already on screen does not move the table');
    ok(!VT.reveal('qubits.nope.xy.operations.gone'), 'P: a path the model does not hold answers false');

    // ---- K: htmx's history restore puts back a dead snapshot: re-fetched ---------------------
    const ajaxCalls = [];
    window.htmx.ajax = (verb, url, opts) => { ajaxCalls.push([verb, url, opts && opts.source && opts.source.id]); return Promise.resolve(); };
    const w2 = doc.getElementById('pulses-rows-wrap');
    const snapshot = w2.innerHTML;
    w2.innerHTML = snapshot;           // the restored snapshot: same markup, new nodes
    ok(!VT.active(), 'K: premise -- the restored snapshot is not the live view');
    doc.body.dispatchEvent(new window.CustomEvent('htmx:historyRestore', { bubbles: true }));
    await tick(5); await flush();
    ok(ajaxCalls.some((c) => c[0] === 'GET' && /^\/pulses\?rows=1&per_page=0/.test(c[1]) && c[2] === 'pulses-rows-wrap'),
       'K: a history restore re-fetches the rows through the table\'s own request (' + JSON.stringify(ajaxCalls) + ')');

    console.log(fails ? fails + ' FAILED' : 'all ok');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
