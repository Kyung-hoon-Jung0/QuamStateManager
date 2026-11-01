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
    const roleItems = legend ? legend.querySelectorAll('.iw-legend-item:not([class*="iw-key-"])').length : 0;
    ok(roleItems === roles.size, 'one entry per role drawn, no more (' + roleItems + ' vs ' + roles.size + ')');

    // uv-2: the uncoloured marks and the DIG column have a key too
    ok(items.indexOf('Free port') >= 0 && items.indexOf('No port on this row') >= 0 &&
       items.some((t) => /^DIG = digital outputs/.test(t)), 'uv-2: legend keys free / no-port / DIG');

    // uv-2: structural labels survive Fit -- drawn >= 9 px instead of hidden
    const femLbls = Array.from(svg.querySelectorAll('.iw-fem-label'));
    ok(femLbls.length >= 1 && femLbls.every((t) => t.getAttribute('data-iw-keep') === '1'), 'uv-2: FEM labels are structural');
    const firstPortY = Math.min(...Array.from(svg.querySelectorAll('.iw-port circle')).map((c) => +c.getAttribute('cy') - +c.getAttribute('r')));
    ok(femLbls.every((t) => +t.getAttribute('y') < firstPortY), 'uv-2: each FEM label heads its column (above port 1)');
    const heads = Array.from(svg.querySelectorAll('text')).filter((t) => /^(OUT|IN|DIG)$/.test(t.textContent));
    ok(heads.length >= 2 && heads.every((t) => t.getAttribute('data-iw-keep') === '1'), 'uv-2: OUT/IN/DIG headings are structural');
    const natW = parseFloat(svg.dataset.natW);
    svg.getBoundingClientRect = () => ({ width: natW * 0.5, height: 100, left: 0, top: 0, right: natW * 0.5, bottom: 100 });
    w.eval('_instrHideTinyLabels')(svg, natW);
    ok(femLbls.every((t) => parseFloat(t.getAttribute('font-size')) * 0.5 >= 8.99 && t.style.display !== 'none'),
       'uv-2: at scale 0.5 a FEM label is drawn >= 9 px, not hidden');
    const kept = Array.from(svg.querySelectorAll('text')).filter((t) => !t.getAttribute('data-iw-keep'));
    ok(kept.filter((t) => parseFloat(t.getAttribute('font-size')) * 0.5 < 9).every((t) => t.style.display === 'none'),
       'uv-2: a non-structural tiny label is still hidden');
    svg.getBoundingClientRect = () => ({ width: natW, height: 100, left: 0, top: 0, right: natW, bottom: 100 });
    w.eval('_instrHideTinyLabels')(svg, natW);
    ok(femLbls.every((t) => parseFloat(t.getAttribute('font-size')) === 10), 'uv-2: back at scale 1 the label returns to its own 10 px');

    // uv-2: hover numbers keep their significant figures
    ok(w.eval('_fmtNum')(4.4588e-4, 4) === '0.00044588', 'uv-2: threshold 4.4588e-4 is not printed as 0.0004');

    // uv-2: a numerically-keyed controller (the wizard's) is named con<N>
    const host = d.createElement('div'); host.id = 'uv2host'; d.body.appendChild(host);
    w.renderInstrumentWiring('uv2host', { controllers: { '1': { fems: { '1': { type: 'mw-fem', output_ports: {}, input_ports: {} } }, max_output_port: 8 } } }, {}, {});
    const h4 = host.querySelector('.iw-chassis-head');
    ok(!!h4 && /^Chassis 1 · con1/.test(h4.textContent), 'uv-2: wizard rack header reads con1 (' + (h4 && h4.textContent) + ')');

    // uv-2: an input feedline of 4+ qubits keeps 3-char names at >= 9 px
    const host2 = d.createElement('div'); host2.id = 'uv2host2'; d.body.appendChild(host2);
    const ro = ['q1', 'q2', 'q3', 'q4', 'q5'].map((q) => ({ element: q, role: 'rr_in', label: 'qA' + q.slice(1) + '.rr' }));
    w.renderInstrumentWiring('uv2host2', { controllers: { con1: { fems: { '1': { type: 'mw-fem', output_ports: {}, input_ports: { '1': ro } } }, max_output_port: 8 } } }, {}, {});
    const inTexts = Array.from(host2.querySelectorAll('.iw-port[data-io="input"] .iw-port-circle text'));
    ok(inTexts.length === 5 && inTexts.every((t) => parseFloat(t.getAttribute('font-size')) >= 9),
       'uv-2: every readout-IN dot carries a >= 9 px name (' + inTexts.map((t) => t.getAttribute('font-size')) + ')');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
