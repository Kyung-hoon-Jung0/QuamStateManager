/* v1.5.1 (U P2-4): Plotly's toolbar never sits on the data.
 *
 * The toolbar is drawn in the top-right corner of a chart, about 28 px tall.
 * With a top margin of 8 px (Chip Status > Trends) it covered the newest
 * points and caught their hover; with 30 px (the CZ plan's chip map, column
 * names drawn above the plot) it covered the last column's name. The driver
 * (tests/test_v151_p2_modebar.py) renders the REAL Trends fragment (its
 * charts JSON inline) and the REAL /cz-plan markup and /cz-plan/map +
 * /cz-plan/loss / /cz-plan/data answers through the Flask test client; the SHIPPED modules
 * draw them here and every layout's top margin is read.
 *
 * Run: node tests/v151_modebar_clear_selfcheck.cjs <fixture.json>
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
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const TOOLBAR_PX = 30;          // Plotly's modebar: 2 px offset + ~26 px of buttons
const LABEL_PX = 18;            // a 10 px tick label above the plot, with its standoff

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function world(body, url) {
    const dom = new JSDOM('<!doctype html><html><body>' + body + '</body></html>',
        { url: 'http://localhost' + url, runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.cb([{ isIntersecting: true, target: t }]); } unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    const renders = [];
    w.requirePlotly = () => Promise.resolve();
    w._plotlyRender = (el, data, layout) => {
        renders.push({ id: el.id || el.className, layout: layout });
        el.data = data; el.layout = layout; el.on = () => {}; el.removeAllListeners = () => {};
        return Promise.resolve(el);
    };
    w.Plotly = { newPlot: w._plotlyRender, react: w._plotlyRender, relayout: () => Promise.resolve(),
                 restyle: () => Promise.resolve(), purge: () => {} };
    w.eval(read('plot-theme.js'));
    return { w, renders };
}

(async function () {
    /* A. Chip Status > Trends, from its real fragment */
    {
        const i = FX.trends.lastIndexOf('<script>');
        const { w, renders } = world(FX.trends.slice(0, i), '/topology/trends');
        w.eval(read('chip-status.js'));
        for (const m of FX.trends.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
            try { w.eval(m[1]); } catch (e) { /* a page script that needs more of the app */ }
        }
        await wait(200);
        ok(renders.length >= 1, 'A: the Trends fragment draws its charts (' + renders.length + ')');
        renders.forEach((r) => ok(r.layout.margin.t >= TOOLBAR_PX,
            'A: the toolbar sits above the plot, not on the newest points (top margin ' + r.layout.margin.t + ')'));
    }
    /* B. CZ gate plan > chip map, from the real page + answers */
    {
        const { w, renders } = world('<div id="table-pane">' + FX.czplan + '</div>', '/cz-plan');
        w.fetch = (url) => {
            const body = /^\/cz-plan\/map/.test(url) ? FX.map : /^\/cz-plan\/loss/.test(url) ? FX.loss
                : /^\/cz-plan\/data/.test(url) ? FX.data : null;
            if (!body) return new Promise(() => {});
            return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(body))) });
        };
        w.eval(read('topo-graph.js'));
        w.eval(read('cz-map.js'));
        w.eval(read('cz-plan.js'));
        w.CzPlan.mount();
        await wait(200);
        const map = renders.filter((r) => r.id === 'czp-map');
        ok(map.length >= 1, 'B: the chip map draws');
        map.forEach((r) => {
            const top = r.layout.xaxis && r.layout.xaxis.side === 'top';
            const need = TOOLBAR_PX + (top ? LABEL_PX : 0);
            ok(r.layout.margin.t >= need, 'B: the toolbar clears the ' + (top ? 'column names above the map' : 'plot')
               + ' (top margin ' + r.layout.margin.t + ', needs ' + need + ')');
        });
        const plots = renders.filter((r) => r.id === 'czp-flux' || r.id === 'czp-time');
        ok(plots.some((r) => r.id === 'czp-flux') && plots.some((r) => r.id === 'czp-time'),
           'B: the two plots of the operation draw');
        plots.forEach((r) => ok(r.layout.margin.t >= TOOLBAR_PX,
            'B: the toolbar sits above ' + r.id + ' (top margin ' + r.layout.margin.t + ')'));
    }
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
