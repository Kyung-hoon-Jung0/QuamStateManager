/* v1.5.1 (U P2-2): the Datasets run table fits its pane.
 *
 * The default columns summed to 1088 px; at 1280 the pane is 959 px wide and
 * TAGS sat off-screen behind a pane scrollbar. The driver
 * (tests/test_v151_p2_ds_table_fit.py) renders the REAL /datasets fragment
 * (two data folders, so the Folder column shows); the SHIPPED
 * dataset-virtual.js builds the table here. jsdom has no layout, so the
 * pane's width is the one geometry fact given: 959 px, the measured pane.
 *
 * Run: node tests/v151_ds_table_fit_selfcheck.cjs <fixture.json>
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
    w.fetch = () => new Promise(() => {});
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {} };
    w.eval(SRC);
    w.DatasetVirtual.init();
    return w;
}
const widths = (w) => Array.from(w.document.querySelectorAll('#datasets-colgroup col'))
    .map((c) => [c.getAttribute('data-col-key'), parseFloat(c.style.width)]);

(async function () {
    const w = boot();
    await tick();
    const cols = widths(w);
    const keys = cols.map((c) => c[0]);
    ok(keys.indexOf('tags') >= 0 && keys.indexOf('folder') >= 0, 'the real table shows Folder and Tags: ' + keys.join(','));
    const sum = cols.reduce((a, c) => a + c[1], 0);
    ok(sum <= PANE, 'the columns fit the ' + PANE + ' px pane: ' + sum + ' px ' + JSON.stringify(cols));
    const get = (k) => (cols.find((c) => c[0] === k) || [])[1];
    ok(get('exp') >= 150 && get('tags') >= 96, 'Experiment and Tags keep a readable width: ' + get('exp') + ' / ' + get('tags'));
    ok(get('id') === 58 && get('status') === 104, 'the fixed columns are untouched');

    const u = boot({ quam_ds_col_widths: JSON.stringify({ exp: 300 }) });
    await tick();
    const ucols = widths(u);
    const uget = (k) => (ucols.find((c) => c[0] === k) || [])[1];
    ok(uget('exp') === 300, 'a width the user dragged is kept: ' + uget('exp'));
    ok(uget('tags') < 180, 'the others still give way: tags ' + uget('tags'));
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
