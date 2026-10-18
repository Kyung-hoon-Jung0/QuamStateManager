/* v1.5.1 (T1-F7): a Datasets > Trends y axis says its unit.
 *
 * t1 is stored in ns and the axis read "15k / 10k" with the title "t1": a
 * reader could not tell 15k of what. The driver (tests/test_v151_p2_trends_units.py)
 * renders the REAL /trends/data shell, the REAL /trends/series answer and the
 * REAL /trends/units answer (read from the runs' own ds_fit files) through the
 * Flask test client; this harness runs the SHELL's inline script and the
 * SHIPPED app.js (window.DatasetTrends) on them.
 *
 * Pinned: the axis title is "t1 (ns)" -- on a chart drawn before the units
 * answered (relayout) and on one drawn after (its layout) -- the chart label
 * says it too, the numbers follow PlotTheme.axisNumberFormat (SI, never "B"),
 * and a metric no fit file declares keeps its bare name.
 *
 * Run: node tests/v151_trends_units_selfcheck.cjs <fixture.json>
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
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const THEME = fs.readFileSync(path.join(STATIC, 'plot-theme.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body><div id="table-pane"><div id="trends-content"></div></div>'
    + '<div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/trends', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const asked = [];
let releaseUnits = null;
w.fetch = function (url) {
    asked.push(String(url));
    if (/^\/trends\/series\?/.test(url)) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(FX.series) });
    }
    if (/^\/trends\/units\?/.test(url)) {
        return new Promise(function (resolve) {
            releaseUnits = function () { resolve({ ok: true, status: 200, json: () => Promise.resolve(FX.units) }); };
        });
    }
    return new Promise(function () {});
};
w.eval(THEME);
w.eval(APP);
const draws = [], relayouts = [];
w._plotlyRender = function (el, data, layout) {
    el.data = data; el.layout = layout; el.on = function () {};
    draws.push({ el: el, layout: layout });
    return Promise.resolve(el);
};
w.Plotly = { relayout: function (el, upd) { relayouts.push({ el: el, upd: upd }); return Promise.resolve(); } };
w.htmx = { ajax: function () { return Promise.resolve(); } };
w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
w.PlotHost = { observe: function () {} };

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(fn, ms) { const t = Date.now(); while (!fn() && Date.now() - t < (ms || 3000)) await tick(5); }

(async function () {
    // the shell, as htmx swaps it in: markup, then its own inline script
    const i = FX.shell.lastIndexOf('<script>');
    d.getElementById('trends-content').innerHTML = FX.shell.slice(0, i);
    w.eval(FX.shell.slice(i + '<script>'.length, FX.shell.indexOf('</script>', i)));
    await until(() => draws.length >= 1 && releaseUnits);
    const ix = FX.series.series.findIndex((s) => s.m === 't1');
    const bare = FX.series.series.findIndex((s) => s.m === FX.bare_metric);
    ok(ix >= 0 && bare >= 0, 'the real series carry t1 and ' + FX.bare_metric);
    ok(asked.some((u) => /^\/trends\/units\?experiment=/.test(u)), 'the units are asked for, from the series URL');
    const first = draws[0];
    ok(first.layout.yaxis.exponentformat === 'SI' && first.layout.yaxis.tickformat === '',
       'the numbers follow PlotTheme.axisNumberFormat: ' + JSON.stringify(first.layout.yaxis));
    releaseUnits();
    await until(() => draws.length >= FX.series.series.length && relayouts.length >= 1);
    await tick(30);
    const box = d.getElementById('trend-chart-' + ix);
    const drawn = draws.find((x) => x.el === box);
    const relaid = relayouts.filter((x) => x.el === box).pop();
    const title = (relaid && relaid.upd['yaxis.title.text']) || (drawn && (drawn.layout.yaxis.title.text || drawn.layout.yaxis.title));
    ok(title === 't1 (ns)', 'the t1 axis title carries its unit: ' + title);
    const label = box.parentNode.querySelector('.trend-chart-label').textContent.replace(/\s+/g, ' ').trim();
    ok(/ — t1 \(ns\)$/.test(label), 'and so does its label: ' + label);
    const late = draws.filter((x) => x.el !== draws[0].el && x.el.id !== 'trend-chart-' + bare);
    ok(late.some((x) => / \(ns\)$/.test(String(x.layout.yaxis.title))),
       'a chart drawn after the answer has the unit in its own layout');
    const bareBox = d.getElementById('trend-chart-' + bare);
    const bareLabel = bareBox.parentNode.querySelector('.trend-chart-label').textContent;
    ok(!/\(/.test(bareLabel) && !relayouts.some((x) => x.el === bareBox),
       'a metric no fit file declares keeps its bare name: ' + bareLabel.trim());
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
