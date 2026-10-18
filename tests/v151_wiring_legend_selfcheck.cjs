/* v1.5.1 (U P2-20): Instrument Wiring names its input ports and its colours.
 *
 * An MW-FEM's readout input 2 is drawn where the front panel has it -- the
 * last row -- and with only the output row numbers on the left it read as
 * "IN 8"; the colours had no legend. The driver
 * (tests/test_v151_p2_wiring_legend.py) renders the REAL /instrument fragment
 * (its inline script carries the rack data); the SHIPPED app.js draws it here.
 *
 * Run: node tests/v151_wiring_legend_selfcheck.cjs <fixture.json>
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

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

(async function () {
    const scripts = [...FX.page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
    const markup = FX.page.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/instrument', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    w.fetch = () => new Promise(() => {});
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {}, on: () => {}, config: {} };
    w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.console.log = () => {};
    w.eval(APP);
    scripts.forEach((s) => { try { w.eval(s); } catch (e) { /* a page script needing more of the app */ } });
    await tick();
    const svg = d.querySelector('#instrument-diagram svg');
    ok(!!svg, 'the real page draws the rack');
    const inPorts = Array.from(d.querySelectorAll('#instrument-diagram .iw-port[data-io="input"]'));
    const labels = Array.from(d.querySelectorAll('#instrument-diagram .iw-in-port-label')).map((t) => t.textContent);
    ok(inPorts.length >= 2 && labels.length === inPorts.length, 'every physical input carries a label (' + labels + ')');
    const in2 = inPorts.find((g) => g.getAttribute('data-port') === '2');
    ok(!!in2 && labels.indexOf('in 2') >= 0, 'the readout input says "in 2", not the row it sits on');
    const legend = d.querySelector('#instrument-diagram .iw-legend');
    const items = legend ? Array.from(legend.querySelectorAll('.iw-legend-item')).map((i) => i.textContent) : [];
    ok(items.indexOf('XY drive') >= 0 && items.indexOf('Readout out') >= 0 && items.indexOf('Readout in') >= 0,
       'the legend names the colours drawn: ' + items.join(', '));
    const roles = new Set(Array.from(d.querySelectorAll('#instrument-diagram .iw-port-circle[data-role]')).map((g) => g.getAttribute('data-role')));
    ok(items.length === roles.size, 'one entry per role drawn, no more (' + items.length + ' vs ' + roles.size + ')');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
