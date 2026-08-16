/* jsdom selfcheck for r16 5/5-1 (docs/48 amendment), real app.js:
 *
 *  T1. _fetchInteractiveFig themes the server layout via PlotTheme.houseLayout
 *      (dark-mode axis text was unreadable: the raw layout carried no colors
 *      so Plotly's light template won). Captured through a Plotly stub.
 *  T2. _pruneInteractiveTiles is FROZEN in the post-tray-swap settle window —
 *      an apply's layout shift can no longer purge + re-render every figure.
 *  T3. _swapPendingTray arms the freeze stamp.
 *
 * Run: node tests/interactive_theme_selfcheck.cjs (driven by tests/test_interactive_theme.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const dom = new JSDOM(
    '<!doctype html><html data-theme="dark"><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="pending-tray"></div>' +
    '<div id="ds-interactive-container"><div id="tile1" data-fig="f1"></div></div>' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
global.getComputedStyle = window.getComputedStyle.bind(window);

const plots = [];
window.Plotly = {
    newPlot: function (el, data, layout, config) {
        plots.push({ el, layout, config });
        el.classList && el.classList.add('js-plotly-plot');
        return Promise.resolve();
    },
    purge: function () {}, relayout: function () {}, react: function () {},
};
global.Plotly = window.Plotly;

window.fetch = global.fetch = function (url) {
    const u = String(url);
    if (u.indexOf('/interactive/plot') !== -1) {
        return Promise.resolve({ status: 200, json: () => Promise.resolve({
            data: [{ x: [1, 2], y: [3, 4], type: 'scatter' }],
            layout: { title: { text: 'demo' } },
        }) });
    }
    return Promise.resolve({ status: 200,
        json: () => Promise.resolve({}), text: () => Promise.resolve('') });
};
window.confirm = function () { return true; };

// theme tokens the houseLayout reads
window.document.documentElement.style.setProperty('--plot-axis-text', '#e6e9ef');
window.document.documentElement.style.setProperty('--plot-grid', 'rgba(128,128,128,0.3)');

window.eval(fs.readFileSync(path.join(STATIC, 'plot-theme.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

(async function () {
    /* T1 — the theming composition + its wiring.
       (jsdom can't drive _plotlyRender's async loader chain reliably, so the
       merge semantics are pinned directly and the WIRING is pinned at the
       source level: both fetchers route their layout through houseLayout.) */
    const L = window.PlotTheme.houseLayout({ title: { text: 'demo' },
                                             margin: { l: 99 } });
    ok(L.paper_bgcolor === 'rgba(0,0,0,0)', 'T1: house transparent paper bg');
    ok(!!(L.font && L.font.color), 'T1: house axis text color set');
    ok(!!(L.xaxis && L.xaxis.gridcolor), 'T1: house grid color set');
    ok(L.title && L.title.text === 'demo', 'T1: server layout fields preserved');
    ok(L.margin && L.margin.l === 99, 'T1: server overrides beat house defaults');
    const appSrc = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
    const fig = appSrc.slice(appSrc.indexOf('function _fetchInteractiveFig'),
                             appSrc.indexOf('function _fetchAndRenderPlot'));
    ok(fig.indexOf('PlotTheme.houseLayout') !== -1,
       'T1: _fetchInteractiveFig routes its layout through houseLayout');
    const h5 = appSrc.slice(appSrc.indexOf('function _fetchAndRenderPlot'));
    ok(h5.slice(0, 2000).indexOf('PlotTheme.houseLayout') !== -1,
       'T1: _fetchAndRenderPlot routes its layout through houseLayout');

    /* T2 — prune freeze */
    const mk = () => {
        const d = window.document.createElement('div');
        d.setAttribute('data-rendered', '1');
        d._isVisible = false;
        window.document.body.appendChild(d);
        return d;
    };
    const c2 = { _rendered: [] };
    for (let i = 0; i < 9; i++) c2._rendered.push(mk());
    window._interactiveFreezeUntil = Date.now() + 5000;
    window._pruneInteractiveTiles(c2);
    ok(c2._rendered.length === 9, 'T2: freeze window suppresses the purge');
    window._interactiveFreezeUntil = 0;
    window._pruneInteractiveTiles(c2);
    ok(c2._rendered.length <= 6, 'T2: normal purge resumes after the window');

    /* T3 — tray swap arms the freeze */
    window._interactiveFreezeUntil = 0;
    window.eval("_swapPendingTray('<div id=\\'pending-tray\\'></div>')");
    ok(typeof window._interactiveFreezeUntil === 'number'
       && window._interactiveFreezeUntil > Date.now(),
       'T3: _swapPendingTray arms the freeze stamp');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('interactive_theme_selfcheck: all checks passed');
    process.exit(0);
})().catch(function (e) { console.error('UNCAUGHT:', e && e.stack || e); process.exit(1); });
