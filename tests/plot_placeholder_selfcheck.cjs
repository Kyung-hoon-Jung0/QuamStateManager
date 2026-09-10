/* A placeholder is not part of the plot.
 *
 * Customer, 2026-09-10, on the dataset Raw Data tab: "loading contrast…" sat
 * under the finished chart, on every figure. Plotly.newPlot does NOT clear a
 * foreign child -- it appends its own .plot-container beside whatever is
 * already in the div -- so every caller that writes a placeholder first ends
 * up showing it forever:
 *
 *   ndview.js:436   'loading <var>…'      (the reported one)
 *   app.js          'No numeric values.'  (the drawer's empty state)
 *   app.js          'Plot library failed to load.'
 *
 * window._plotlyRender is the one door all of them go through, so that is
 * where the clearing belongs -- and only on the newPlot branch, which is by
 * definition the branch where the div holds no plot yet.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const APP = fs.readFileSync(
    path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let checks = 0, fails = 0;
function ok(cond, msg) {
    checks++;
    if (!cond) { fails++; console.log('FAIL: ' + msg); }
}

const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="host"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;

/* A Plotly stand-in that behaves the way the real one does about children:
   newPlot APPENDS its container and never removes what it finds. */
win.Plotly = {
    newPlot: function (el, data, layout) {
        const c = win.document.createElement('div');
        c.className = 'plot-container plotly';
        el.appendChild(c);              // append, exactly like the real one
        el.data = data; el.layout = layout;
        return win.Promise.resolve(el);
    },
    react: function (el, data, layout) {
        el.data = data; el.layout = layout;
        el.__reacted = (el.__reacted || 0) + 1;
        return win.Promise.resolve(el);
    },
};
win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };
win.eval(APP);

const host = win.document.getElementById('host');
const TR = [{ x: [1, 2], y: [3, 4] }];

host.innerHTML = '<div class="ndv-loading muted">loading contrast…</div>';
ok(host.querySelectorAll('.ndv-loading').length === 1, '0a the placeholder is there to begin with');

win._plotlyRender('host', TR, {}, {}).then(function () {
    ok(host.querySelectorAll('.ndv-loading').length === 0,
       '1a the placeholder is gone once the plot is drawn');
    ok(host.querySelectorAll('.plot-container').length === 1,
       '1b ...and exactly one plot container is present');
    ok(host.children.length === 1,
       '1c the div holds the plot and nothing else');

    /* The clearing must NOT reach a live plot: a re-render takes the react
       branch, which keeps the existing container and its event handlers.
       Wiping it there would throw away every bound handler -- the docs/125
       "frozen plot" class of defect. */
    return win._plotlyRender('host', [{ x: [5], y: [6] }], {}, {});
}).then(function () {
    ok(host.__reacted === 1, '2a a second render goes through react, not newPlot');
    ok(host.querySelectorAll('.plot-container').length === 1,
       '2b the live plot container survives the re-render');

    /* An empty div is the ordinary case and must not be disturbed. */
    const h2 = win.document.createElement('div');
    h2.id = 'host2';
    win.document.body.appendChild(h2);
    return win._plotlyRender('host2', TR, {}, {}).then(function () {
        ok(h2.querySelectorAll('.plot-container').length === 1,
           '3a a div that was empty still gets its plot');
        if (fails === 0) console.log('all checks passed (' + checks + ' assertions)');
        process.exit(fails ? 1 : 0);
    });
}).catch(function (e) {
    console.error('harness error: ' + (e && e.stack || e));
    process.exit(1);
});
