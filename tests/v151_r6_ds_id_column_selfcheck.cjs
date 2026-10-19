/* v1.5.1 (r6 P2-2): the Datasets ID column fits its run ids and the
 * wrote-state mark.
 *
 * A fixed 58 px column clipped every 4-digit id to "#175..." at 1280 and hid
 * the mark beside it; at 1600 the cell was 66 px for 73 px of content (the
 * re-sweep's measurement, real Chrome). The driver
 * (tests/test_v151_r6_ds_id_column.py) renders the REAL /datasets fragment
 * (two data folders; run #1757 wrote 4 values) and a REAL changes-since
 * answer bringing run #10001; the SHIPPED dataset-virtual.js builds the
 * table. jsdom has no layout, so the pane's width is the one geometry fact
 * given: 959 px, the measured pane at 1280.
 *
 * Run: node tests/v151_r6_ds_id_column_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'dataset-virtual.js'), 'utf8');
const PANE = 959;
const MEASURED = 73;   // "#1757" + the mark + the cell padding, measured in Chrome at 1600

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 40));

function boot(storage) {
    const markup = FX.page.replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/datasets', pretendToBeVisual: true, runScripts: 'outside-only' });
    const w = dom.window;
    Object.keys(storage || {}).forEach((k) => w.localStorage.setItem(k, storage[k]));
    const scroll = w.document.getElementById('datasets-scroll');
    Object.defineProperty(scroll, 'clientWidth', { configurable: true, get: () => PANE });
    w.fetch = (url) => (String(url).indexOf('/datasets/changes-since') === 0
        ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(FX.delta) })
        : new Promise(() => {}));
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {} };
    w.eval(SRC);
    w.DatasetVirtual.init();
    return w;
}
const widths = (w) => Array.from(w.document.querySelectorAll('#datasets-colgroup col'))
    .map((c) => [c.getAttribute('data-col-key'), parseFloat(c.style.width)]);
const get = (cols, k) => (cols.find((c) => c[0] === k) || [])[1];

(async function () {
    const w = boot();
    await tick();
    const cols = widths(w);
    const id0 = get(cols, 'id');
    ok(id0 >= MEASURED, 'the ID column holds "#1757" and its wrote mark: ' + id0 + ' px >= ' + MEASURED);
    ok(id0 <= MEASURED + 20, 'and does not take more than it needs: ' + id0 + ' px');
    const sum = cols.reduce((a, c) => a + c[1], 0);
    ok(sum <= PANE, 'the columns still fit the ' + PANE + ' px pane: ' + sum + ' px ' + JSON.stringify(cols));
    const td = (rid) => Array.from(w.document.querySelectorAll('#datasets-tbody td[data-col-key="id"], tbody td[data-col-key="id"]'))
        .find((c) => (c.textContent || '').indexOf('#' + rid) === 0);
    const t1757 = td(1757), t1754 = td(1754);
    ok(!!t1757 && !!t1757.querySelector('.ds-wrote'), 'run #1757 carries the wrote mark');
    ok(!!t1757 && t1757.getAttribute('title') === 'Run #1757 · wrote 4 values into the chip state',
       'its cell names the run and its writes on hover: ' + (t1757 && t1757.getAttribute('title')));
    ok(!!t1754 && t1754.getAttribute('title') === 'Run #1754',
       'a run that wrote nothing is named alone: ' + (t1754 && t1754.getAttribute('title')));

    // A poll brings run #10001: one more digit, the column grows with it.
    ok(w.DatasetVirtual.pollNow() === true, 'the delta poll runs');
    await tick(80);
    const r10001 = FX.delta.updated.find((r) => r.id === 10001);
    ok(!!w.DatasetVirtual.getRow((r10001.f || '') + ':10001'), 'the polled run is in the table');
    const id1 = get(widths(w), 'id');
    ok(id1 >= id0 + 7, 'the ID column grew for the 5-digit id: ' + id0 + ' -> ' + id1 + ' px');

    // A width the user dragged is kept.
    const u = boot({ quam_ds_col_widths: JSON.stringify({ id: 50 }) });
    await tick();
    ok(get(widths(u), 'id') === 50, 'a width the user dragged is kept: ' + get(widths(u), 'id'));

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
