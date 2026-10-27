/* v1.5.5 bug 6 -- a click on an Interactive figure shows the clicked point's
 * values to copy: x and y on a 1-D plot, x, y and z on a 2-D map, at full
 * precision with the axis unit, one Copy per value and a Copy-all. Nothing is
 * written. Esc or an outside click closes it. A figure with a click contract
 * keeps its apply popup, with the same row inside it. EXECUTED against the real
 * shipped app.js; the apply popup's markup comes from a REAL rendered page.
 * Run: node tests/v155_point_copy_selfcheck.cjs <fixture.json> (driven by tests/test_v155_bugs.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
function block(page, id) {
    const i = page.indexOf('<div id="' + id + '"');
    if (i < 0) return '';
    let depth = 0, j = i;
    const re = /<\/?div\b/g; re.lastIndex = i;
    let m;
    while ((m = re.exec(page))) {
        depth += m[0] === '<div' ? 1 : -1;
        if (depth === 0) { j = page.indexOf('>', m.index) + 1; break; }
    }
    return page.slice(i, j);
}
const POPUP = block(FX.page, 'plot-apply-popup');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function () {
    ok(POPUP.includes('plot-apply-point'), 'the real apply popup carries the point slot');
    const dom = new JSDOM('<!doctype html><html><body><main id="table-pane"><div id="ds-detail-root" data-uid="u1" data-experiment="e">'
        + '</div></main><div id="status-bar"></div>' + POPUP + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/datasets' });
    const w = dom.window, d = w.document;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger() {}, process() {}, on() {}, config: {} };
    const posts = [];
    w.fetch = (url, opt) => {
        if (opt && opt.method && opt.method !== 'GET') posts.push(url);
        const body = String(url).indexOf('/chip/active-token') === 0 ? { loaded: true, token: 't', name: 'c' } : {};
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve('') });
    };
    w.LiveEditUndo = { record() {}, clear() {}, _updateTrayBtn() {} };
    let copied = [];
    Object.defineProperty(w.navigator, 'clipboard', { value: { writeText: (t) => { copied.push(t); return Promise.resolve(); } }, configurable: true });
    w.eval(APP);
    ok(!!w.PointCopy, 'PointCopy ships');
    if (!w.PointCopy) { console.log(fails + ' FAILED'); process.exit(1); }

    function plot() {
        const el = d.createElement('div');
        const hs = [];
        el.on = (n, f) => { if (n === 'plotly_click') hs.push(f); };
        el.fire = (ev) => hs.forEach((f) => f(ev));
        el.count = () => hs.length;
        d.getElementById('ds-detail-root').appendChild(el);
        return el;
    }
    const X = 6123456789.0123457, Y = 0.000123456789012345;
    const ev1 = { event: { clientX: 200, clientY: 150 }, points: [{ x: X, y: Y, data: { type: 'scatter' }, fullData: { type: 'scatter' },
        xaxis: { title: { text: 'Drive frequency [Hz]' } }, yaxis: { title: { text: 'Amplitude (V)' } } }] };
    const rows = () => Array.from(d.querySelectorAll('#pt-copy-pop .pt-copy-row')).map((r) => r.getAttribute('data-axis') + '='
        + r.querySelector('.pt-copy-v').textContent + '|' + ((r.querySelector('.pt-copy-u') || {}).textContent || ''));

    // 1-D, no contract
    const p1 = plot();
    w.PointCopy.attach(p1); w.PointCopy.attach(p1);
    ok(p1.count() === 1, 'binding twice into the same node binds once');
    p1.fire(ev1);
    ok(!!d.getElementById('pt-copy-pop'), 'a click on a 1-D figure opens the point popup');
    ok(JSON.stringify(rows()) === JSON.stringify(['x=' + String(X) + '|Hz', 'y=' + String(Y) + '|V']),
       'x and y at full precision with their units: ' + JSON.stringify(rows()));
    d.querySelector('#pt-copy-pop .pt-copy-row[data-axis="x"] .pt-copy-btn').click();
    const both = d.querySelector('#pt-copy-pop .pt-copy-all');
    ok(both && both.textContent === 'Copy both', 'a 1-D point offers Copy both');
    both.click();
    await sleep(5);
    ok(copied[0] === String(X), 'Copy x copies the full-precision value: ' + copied[0]);
    ok(copied[1] === 'x = ' + String(X) + ' Hz\ny = ' + String(Y) + ' V', 'Copy both copies both lines: ' + JSON.stringify(copied[1]));
    d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(!d.getElementById('pt-copy-pop'), 'Esc closes it');

    // 2-D map
    const ev2 = { event: { clientX: 50, clientY: 60 }, points: [{ x: 0.25, y: 5.1e9, z: -0.0031415926535, data: { type: 'heatmap' },
        fullData: { type: 'heatmap', colorbar: { title: { text: 'Signal [mV]' } } },
        xaxis: { title: { text: 'Flux bias (V)' } }, yaxis: { title: { text: 'Frequency [Hz]' } } }] };
    const p2 = plot(); w.PointCopy.attach(p2); p2.fire(ev2);
    ok(JSON.stringify(rows()) === JSON.stringify(['x=0.25|V', 'y=5100000000|Hz', 'z=-0.0031415926535|mV']),
       'a 2-D map shows x, y and the cell value z with units: ' + JSON.stringify(rows()));
    const all = d.querySelector('#pt-copy-pop .pt-copy-all');
    ok(all && all.textContent === 'Copy all', 'a 2-D point offers Copy all');
    copied = []; all.click(); await sleep(5);
    ok(copied[0] === 'x = 0.25 V\ny = 5100000000 Hz\nz = -0.0031415926535 mV', 'Copy all copies all three: ' + JSON.stringify(copied[0]));
    d.querySelector('#pt-copy-pop .pt-copy-row[data-axis="z"] .pt-copy-btn').click(); await sleep(5);
    ok(copied[1] === '-0.0031415926535', 'Copy z copies the cell value');
    await sleep(5);
    d.getElementById('table-pane').dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true }));
    ok(!d.getElementById('pt-copy-pop'), 'an outside click closes it');
    ok(posts.length === 0, 'nothing was written');

    // click contract: the apply popup carries the row
    for (const [name, ev, n] of [['1-D', ev1, 2], ['2-D', ev2, 3]]) {
        const p3 = plot();
        w._attachInteractivePlotClickHandler(p3, { axis: 'x', qubit: 'q1', targets: [{ path: 'qubits.q1.f_01', axis: 'x' }] }, 'u1');
        p3.fire(ev);
        await sleep(60);
        const slot = d.getElementById('plot-apply-point');
        const got = slot ? slot.querySelectorAll('.pt-copy-row').length : 0;
        ok(slot && !slot.hidden && got === n, 'a contract figure (' + name + ') keeps its apply popup with the x/y(/z) row inside: ' + got);
        ok(!d.getElementById('pt-copy-pop'), 'and opens no second popup (' + name + ')');
    }
    console.log(fails ? fails + ' FAILED' : 'all checks passed (' + asserts + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
