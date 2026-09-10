/* How every SM chart axis writes its numbers, driven against the REAL files.
 *
 * Customer, 2026-09-10: a Trends fidelity axis read "996m" for 0.996.
 * The first cure set tickformat '~s'; a review round then measured what that
 * costs -- `~s` caps a label at 6 significant digits, so a zoomed axis draws
 * three ticks reading the SAME string. The root was never tickformat: Plotly's
 * DEFAULT exponentformat is 'B', which is what writes 4.9e9 as "4.9B".
 *
 * Every expectation below was READ OFF a real chart in headless Chrome on a
 * real 5-qubit chip, at full range and after a tight zoom. They are not
 * predictions:
 *
 *     setting              fidelity     f_01       zoomed ticks
 *     ~s                   991m    X    4.8G  ok   3 -> 1 distinct  X
 *     '' + SI              0.991   ok   4.8G  ok   3 -> 3 distinct  ok
 *
 * The rule lives in ONE place and both chart surfaces ASK it -- and this file
 * pins that they APPLY what they are given, because a review round found the
 * previous version only grepped for the call.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let checks = 0, fails = 0;
function ok(cond, msg) {
    checks++;
    if (!cond) { fails++; console.log('FAIL: ' + msg); }
}
const done = [];

/* ---------------------------------------------------------------- the rule */
(function () {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
                          { runScripts: 'outside-only', url: 'http://localhost/' });
    const win = dom.window;
    win.eval(read('plot-theme.js'));

    const F = win.PlotTheme && win.PlotTheme.axisNumberFormat;
    ok(typeof F === 'function', '1a PlotTheme exports axisNumberFormat');
    if (typeof F !== 'function') return;

    const f = F();
    ok(f.tickformat === '',
       '1b tickformat is empty -- a bare ratio gets no prefix (0.991, not 991m)');
    ok(f.exponentformat === 'SI',
       '1c exponentformat is SI -- 4.9e9 reads 4.9G, never Plotly default 4.9B');
    ok(F() !== F(), '1d a fresh object each call (a caller may not mutate the rule)');
    ok(Object.keys(f).sort().join(',') === 'exponentformat,tickformat',
       '1e it says exactly those two things');
})();

/* ------------------------------------------------- the Trends charts APPLY it */
done.push(new Promise(function (resolve) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body><div class="topo-trends-grid">'
        + '<div class="topo-trend-box" data-trend-metric="m" data-trend-kind="qubit">'
        + '<div class="topo-trend-title">m</div><div class="topo-trend-chart"></div>'
        + '</div></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const win = dom.window;
    win.eval(read('plot-theme.js'));
    win.Plotly = {
        newPlot: function (el, data, layout) {
            el.data = data; el.layout = layout;
            el.on = function () {}; el.removeAllListeners = function () {};
            return win.Promise.resolve(el);
        },
        react: function (el, d, l) { return win.Plotly.newPlot(el, d, l); },
        relayout: function () {}, purge: function () {},
    };
    win._plotlyRender = function (host, traces, layout, cfg) {
        return win.Plotly.newPlot(host, traces, layout, cfg);
    };
    win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };
    win.eval(read('chip-status.js'));

    const host = win.document.querySelector('.topo-trend-chart');
    const pts = (vals) => vals.map((v, i) => ['2026090' + (i + 1) + '_010000_1', v]);
    const render = (vals) => win.ChipTrends.render([{
        metric: 'm', kind: 'qubit',
        series: [{ entity: 'q1', points: pts(vals) }],
    }]);

    setTimeout(function () {
        render([0.991, 0.996]);
        setTimeout(function () {
            const y = (host.layout || {}).yaxis || {};
            ok(y.tickformat === '', '2a a Trends axis applies the empty tickformat');
            ok(y.exponentformat === 'SI', '2b ...and the SI exponentformat');

            // The same settings regardless of magnitude: the gate is gone, and
            // a per-magnitude branch would show up here as a difference.
            render([4.75e9, 5.25e9]);
            setTimeout(function () {
                const y2 = (host.layout || {}).yaxis || {};
                ok(y2.tickformat === '' && y2.exponentformat === 'SI',
                   '2c a GHz axis gets the SAME settings (no magnitude branch)');
                resolve();
            }, 30);
        }, 30);
    }, 0);
}));

/* ------------------------------------------- the Param History drawer too */
done.push(new Promise(function (resolve) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body><div id="param-history-drawer">'
        + '<div id="phd-chart"></div></div><div id="table-pane"></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const win = dom.window;
    win.eval(read('plot-theme.js'));
    win._htmxCalls = [];
    win.htmx = { ajax: function () { win._htmxCalls.push([].slice.call(arguments)); } };
    win.fetch = function () { return new win.Promise(function () {}); };
    win.Plotly = {
        newPlot: function (id, data, layout) {
            const el = win.document.getElementById(id);
            el.data = data; el.layout = layout;
            el.on = function () {};
            return win.Promise.resolve(el);
        },
    };
    win.eval(read('app.js'));
    win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };

    const chart = win.document.getElementById('phd-chart');
    const at = (ts, v) => ({ timestamp: ts, value: v, trigger: 'manual',
                             run_id: null, experiment: null, uid: null });

    win.paramHistoryRenderDrawerChart(
        { property: 'f_01', values: [at('20260901_010000', 6.0e9), at('20260901_010100', 6.1e9)] });
    setTimeout(function () {
        const y = (chart.layout || {}).yaxis || {};
        ok(y.tickformat === '', '3a the drawer applies the empty tickformat');
        ok(y.exponentformat === 'SI', '3b ...and the SI exponentformat');

        /* A dead-flat history gets a range around its own value, or Plotly
           auto-ranges across floating-point noise and prints the whole double
           (measured: two ticks reading 0.9942053178797723 and ...722). But the
           dotted current-value line is a SHAPE, and a FIXED range is not
           expanded to fit one -- so the guard has to cover it, or the drawer
           hides the very number it was opened to compare against. */
        const flat = 0.9942053178797723;
        win.paramHistoryRenderDrawerChart(
            { property: 'gate_fidelity_avg',
              values: [at('20260901_010000', flat), at('20260901_010100', flat)] },
            0.80);
        setTimeout(function () {
            const y2 = (chart.layout || {}).yaxis || {};
            ok(Array.isArray(y2.range) && y2.autorange === false,
               '3c a flat history gets a fixed range, not float noise');
            ok(y2.range[0] <= 0.80 && y2.range[1] >= flat,
               '3d ...and that range still contains the current-value line');

            // ...while a current value inside the pad changes nothing.
            win.paramHistoryRenderDrawerChart(
                { property: 'gate_fidelity_avg',
                  values: [at('20260901_010000', flat), at('20260901_010100', flat)] },
                flat);
            setTimeout(function () {
                const y3 = (chart.layout || {}).yaxis || {};
                ok(y3.range[0] < flat && y3.range[1] > flat,
                   '3e a current value equal to the history still leaves a band');
                resolve();
            }, 30);
        }, 30);
    }, 30);
}));

/* --------------------------------------- one rule, asked for, never copied */
(function () {
    const chipStatus = read('chip-status.js');
    const app = read('app.js');
    ok(/PlotTheme\.axisNumberFormat\(/.test(chipStatus),
       '4a the Trends charts ask the shared rule');
    ok(/PlotTheme\.axisNumberFormat\(/.test(app),
       '4b the Param History drawer asks it too');
    // No literal format may be spelled out at either mount -- the previous
    // version of this pin only forbade one exact spelling of one of them.
    [['chip-status.js', chipStatus], ['app.js', app]].forEach(function (pair) {
        const bad = (pair[1].match(/(tickformat|exponentformat)\s*:\s*['"][^'"]*['"]/g) || [])
            .filter(function (m) { return !/:\s*['"]\s*['"]/.test(m); });
        ok(bad.length === 0,
           '4c ' + pair[0] + ' hard-codes no axis number format (' + bad.join(' ') + ')');
    });
    ok(!/axisTickFormat/.test(chipStatus + app + read('plot-theme.js')),
       '4d the retired magnitude gate is gone everywhere, name included');
})();

Promise.all(done).then(function () {
    if (fails === 0) console.log('all checks passed (' + checks + ' assertions)');
    process.exit(fails ? 1 : 0);
}, function (e) {
    console.error('harness error: ' + (e && e.stack || e));
    process.exit(1);
});
