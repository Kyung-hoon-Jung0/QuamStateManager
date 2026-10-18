/* v1.5.1 (U P2-12): the value-history chart says its unit.
 *
 * The T1 drawer drew "20µ" on an axis with no title while the field above
 * it showed µs. The driver (tests/test_v151_p2_value_history_units.py) hands
 * this harness the REAL /field/history panel the Flask test client rendered;
 * the SHIPPED app.js's FieldHistory opens it and draws the chart.
 *
 * Run: node tests/v151_value_history_unit_selfcheck.cjs <fixture.json>
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
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const dom = new JSDOM('<!doctype html><html><body></body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.fetch = () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(FX.panel) });
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
const plots = [];
w.Plotly = { newPlot: (el, data, layout) => { plots.push({ el, data, layout }); return Promise.resolve(); },
             purge: () => {}, react: () => Promise.resolve(), relayout: () => Promise.resolve() };
w.eval(APP);

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = () => new Promise((r) => setTimeout(r, 40));

(async function () {
    const anchor = w.document.createElement('button');
    w.document.body.appendChild(anchor);
    anchor.getBoundingClientRect = () => ({ left: 10, top: 10, right: 30, bottom: 30, width: 20, height: 20 });
    w.FieldHistory.open(anchor, FX.path, null);
    await settle(); await settle();
    ok(plots.length === 1 && plots[0].el.id === 'fh-chart', 'the chart is drawn from the real panel');
    const lay = plots[0] && plots[0].layout;
    const title = lay && lay.yaxis && lay.yaxis.title && lay.yaxis.title.text;
    ok(title === FX.expect_title, 'the y axis says the unit: ' + title);
    const tr = plots[0] && plots[0].data[0];
    ok(tr && JSON.stringify(tr.y) === JSON.stringify(FX.expect_y), 'the values are in that unit: ' + JSON.stringify(tr && tr.y));
    ok(tr && / µs<extra>/.test(tr.hovertemplate), 'and the hover says it: ' + (tr && tr.hovertemplate));
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
