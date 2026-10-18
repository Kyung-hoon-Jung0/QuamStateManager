/* v1.5.1 (U P2-8): the plot-apply popup's rows fit the popup, and the
 * "Changed since this run by #N ..." note spans its row.
 *
 * Every row carried a horizontal scrollbar (its column floors summed past the
 * popup) and the history note sat in the path column, wrapped into a narrow
 * strip. The driver (tests/test_v151_p2_plot_apply_rows.py) hands this harness
 * a REAL full page; the SHIPPED app.js builds the popup's rows
 * (_openPlotApplyPopup) and the SHIPPED stylesheets are applied to them.
 *
 * Run: node tests/v151_plot_apply_rows_selfcheck.cjs <fixture.json>
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
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const CSS = ['pico.min.css', 'style.css'].map((f) => fs.readFileSync(path.join(STATIC, f), 'utf8'));
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

(async function () {
    const body = FX.page.slice(FX.page.indexOf('<body'), FX.page.lastIndexOf('</body>') + 7)
        .replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html data-theme="light"><head>' + CSS.map((c) => '<style>' + c + '</style>').join('')
        + '</head>' + body + '</html>', { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    w.fetch = (url) => (String(url).indexOf('/chip/active-token') === 0
        ? Promise.resolve({ ok: true, json: () => Promise.resolve(FX.active) }) : new Promise(() => {}));
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {}, on: () => {}, config: {} };
    w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.eval(APP);
    w._openPlotApplyPopup([{ dot_path: 'qubits.q1.resonator.f_01', value: 6477147005.884673 }],
                          '03_resonator_spectroscopy_single', 'q1', [], null, null, null);
    await tick();
    const row = d.querySelector('#plot-apply-popup .plot-apply-row');
    ok(!!row, 'the popup builds its row');
    const hist = row && row.querySelector('.plot-apply-row-history');
    ok(!!hist && w.getComputedStyle(hist).gridColumn.replace(/\s+/g, '') === '1/-1',
       'the "Changed since..." note spans the row: ' + (hist && w.getComputedStyle(hist).gridColumn));
    const dw = row && row.querySelector('.plot-apply-row-domainwarn');
    ok(!!dw && w.getComputedStyle(dw).gridColumn.replace(/\s+/g, '') === '1/-1', 'and so does the domain warning');
    const cols = row ? w.getComputedStyle(row).gridTemplateColumns : '';
    const floors = (cols.match(/minmax\(([\d.]+)(px|rem)/g) || []).map((m) => {
        const v = parseFloat(m.replace('minmax(', '')); return /rem/.test(m) ? v * 16 : v;
    });
    const total = floors.reduce((a, b) => a + b, 0);
    ok(total > 0 && total <= 160, 'the column floors leave the row inside the popup (' + total + ' px: ' + cols + ')');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
