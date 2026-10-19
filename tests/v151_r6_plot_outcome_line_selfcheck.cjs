/* v1.5.1 (r6 P2-1): a plot-apply row's outcome gets its own line.
 *
 * Once a row showed its outcome ("staged -- the live write was refused:
 * Apply failed: ..."), the text sat in the row's auto-width button column,
 * took the width, and squeezed the path and PREVIOUS columns to one character
 * per line. The driver (tests/test_v151_r6_plot_outcome_line.py) hands this
 * harness a REAL full page; the SHIPPED app.js builds the popup's rows and
 * marks their outcome, and the SHIPPED stylesheets are applied to them.
 * jsdom has no layout: the pins read the computed declarations that decide it.
 *
 * Run: node tests/v151_r6_plot_outcome_line_selfcheck.cjs <fixture.json>
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
const flat = (s) => String(s || '').replace(/\s+/g, '');

function boot() {
    const body = FX.page.slice(FX.page.indexOf('<body'), FX.page.lastIndexOf('</body>') + 7)
        .replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html data-theme="light"><head>' + CSS.map((c) => '<style>' + c + '</style>').join('')
        + '</head>' + body + '</html>', { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.fetch = (url) => (String(url).indexOf('/chip/active-token') === 0
        ? Promise.resolve({ ok: true, json: () => Promise.resolve(FX.active) }) : new Promise(() => {}));
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {}, on: () => {}, config: {} };
    w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.eval(APP);
    return w;
}
async function openRow(w) {
    w._openPlotApplyPopup([{ dot_path: 'qubits.q1.resonator.f_01', value: 6477147005.884673 }],
                          '03_resonator_spectroscopy_single', 'q1', [], null, null, null);
    await tick();
    return w.document.querySelector('#plot-apply-popup .plot-apply-row');
}
function lineChecks(w, row, label) {
    const slot = row && row.querySelector('.plot-apply-row-action');
    ok(!!slot, label + ': the row has its outcome slot');
    if (!slot) return;
    const st = w.getComputedStyle(slot);
    ok(flat(st.gridColumn) === '1/-1',
       label + ': the outcome is its own line across the row (grid-column ' + JSON.stringify(st.gridColumn) + ')');
    ok(st.overflowWrap === 'anywhere', label + ': a long reason wraps inside its line (' + st.overflowWrap + ')');
    const span = slot.querySelector('.plot-apply-row-check');
    ok(!!span && w.getComputedStyle(span).whiteSpace === 'normal',
       label + ': the outcome text may wrap (' + (span && w.getComputedStyle(span).whiteSpace) + ')');
    const path = row.querySelector('.plot-apply-row-path'), old = row.querySelector('.plot-apply-row-old');
    ok(flat(w.getComputedStyle(path).gridColumn) !== '1/-1' && flat(w.getComputedStyle(old).gridColumn) !== '1/-1',
       label + ': field and PREVIOUS keep their own columns');
}

(async function () {
    // Before any outcome: the Apply button sits in its column, beside the values.
    let w = boot();
    let row = await openRow(w);
    ok(!!row, 'the popup builds its row');
    const slot0 = row.querySelector('.plot-apply-row-action');
    ok(flat(w.getComputedStyle(slot0).gridColumn) !== '1/-1', 'the Apply button stays beside the values');

    // Auto-Sync off: "staged -- press Apply" is the outcome.
    w._markPlotRowApplied(row, '');
    ok(/staged/.test(row.querySelector('.plot-apply-row-action').textContent), 'the row says it is staged');
    lineChecks(w, row, 'staged');

    // Auto-Sync on, the live write refused with a long reason (a .tmp path in it).
    w = boot();
    row = await openRow(w);
    const reason = 'Apply failed: the live folder refused the write (read-only, or no write permission). '
        + 'Your edits are kept. D:\\lab\\chips\\a_very_long_chip_folder_name\\quam_state\\state.json.40412.878c.129.tmp';
    w.AutoApply = { armed: () => true, whenFlushed: () => Promise.resolve({ ok: false, refused: true, reason }) };
    w._markPlotRowApplied(row, '<div id="pending-tray"></div>');
    await tick();
    const text = row.querySelector('.plot-apply-row-action').textContent;
    ok(text.indexOf('the live write was refused') >= 0 && text.indexOf('.tmp') > 0, 'the row shows the whole refusal reason');
    lineChecks(w, row, 'refused');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
