/* docs/124 M-18 — the value-history popover's mini trend must render on its
 * PRIMARY surfaces.
 *
 * Plotly is lazy-loaded, and the popover's home surfaces (qubit/pair
 * inspectors, the bulk grids) mount no other chart — so window.Plotly is
 * absent there on any fresh page load, and renderChart's `!window.Plotly`
 * bail made the documented mini-trend (docs/20) dead on arrival: panel opens,
 * table renders, mount + data in the DOM, no chart, no error. It only ever
 * appeared if the user had visited a plotting surface earlier in the same tab
 * session. The fix routes through window.requirePlotly() and re-renders when
 * the library lands.
 *
 * Run: node tests/fh_chart_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator',
                      { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
// The popover HTML the server would return: mount + a 3-point series.
const PANEL_HTML =
    '<div class="fh-body">' +
    '<div id="fh-chart"></div>' +
    '<script type="application/json" id="fh-chart-data">' +
    JSON.stringify([
        { t: '2026-08-01 10:00', v: 1.0, trigger: 'save' },
        { t: '2026-08-02 10:00', v: 2.0, trigger: 'experiment' },
        { t: '2026-08-03 10:00', v: 1.5, trigger: 'save' },
    ]) +
    '</scr' + 'ipt></div>';
function fetchStub() {
    return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(PANEL_HTML) });
}
global.fetch = fetchStub;
Object.defineProperty(window, 'fetch',
                      { value: fetchStub, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
// renderChart reads theme colors via bare getComputedStyle — bridge it or the
// call is a ReferenceError swallowed by FieldHistory's fetch .catch (the
// CLAUDE.md harness rule: bridge every global the code under test reads bare).
global.getComputedStyle = window.getComputedStyle.bind(window);
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = () => new Promise((r) => setTimeout(r, 30));

(async function main() {

// The primary-surface condition under test: NO Plotly loaded yet. This
// harness runs app.js through Node-realm eval, so bare `Plotly` resolves via
// Node's `global` — install/clear it on BOTH objects (the CLAUDE.md harness
// rule again; window-only installation left renderChart's bare read undefined
// and the miss was swallowed by FieldHistory's fetch .catch).
delete window.Plotly;
delete global.Plotly;
const newPlots = [];
let requireCalls = 0;
// Production contract: renderChart asks window.requirePlotly for the library.
// The stub installs a recording fake and resolves — like the real loader.
window.requirePlotly = function () {
    requireCalls++;
    const fake = {
        // real Plotly adds the class at render — the purge pin relies on the
        // mount being discoverable the way a real chart is
        newPlot: function (el) {
            newPlots.push(el);
            if (el && el.classList) el.classList.add('js-plotly-plot');
            return Promise.resolve();
        },
        react: function () { return Promise.resolve(); },
        purge: function () {},
        relayout: function () { return Promise.resolve(); },
    };
    window.Plotly = fake;
    global.Plotly = fake;
    return Promise.resolve();
};

const anchor = window.document.createElement('button');
window.document.body.appendChild(anchor);
anchor.getBoundingClientRect = () => ({ left: 10, top: 10, right: 30, bottom: 30, width: 20, height: 20 });

window.FieldHistory.open(anchor, 'qubits.q1.f_01', null);
await settle();
await settle();

ok(requireCalls >= 1,
   'with no Plotly loaded, opening the popover asks requirePlotly (pre-fix: silent bail, chart never appears)');
ok(newPlots.length === 1,
   'the mini trend renders once the library lands (newPlot calls: ' + newPlots.length + ')');
ok(newPlots.length === 1 && newPlots[0] && newPlots[0].id === 'fh-chart',
   'and it renders into #fh-chart');

// Control: with Plotly already present, no loader round-trip is added.
requireCalls = 0; newPlots.length = 0;
window.FieldHistory.open(anchor, 'qubits.q1.T1', null);
await settle();
ok(requireCalls === 0, 'with Plotly already loaded, requirePlotly is not asked again');
ok(newPlots.length === 1, 'and the chart still renders (' + newPlots.length + ')');

// A further open PURGES the previous chart before innerHTML destroys it
// (docs/125 round 2 — a responsive:true chart's window handler kept the
// detached subtree alive, one leak per open). The fake's newPlot marks the
// mount as a graph div so PlotHost.purgeWithin can find it structurally.
const purges = [];
window.Plotly.purge = function (el) { purges.push(el); };
const firstChart = newPlots[0];
firstChart._fullLayout = { width: 400 };
firstChart.data = [{}];
window.FieldHistory.open(anchor, 'qubits.q1.f_01', null);
await settle();
ok(purges.indexOf(firstChart) >= 0,
   'reopening the popover purges the previous chart before replacing it (' +
   purges.length + ' purged)');

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})().catch(function (e) {
    console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
    process.exit(1);
});
