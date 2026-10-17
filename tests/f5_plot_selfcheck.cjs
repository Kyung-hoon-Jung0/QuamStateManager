'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web');
const template = fs.readFileSync(path.join(root, 'templates', '_map_tools.html'), 'utf8');
const dom = new JSDOM('<html data-theme="dark"><body>' + template +
    '<div id="run"><div id="tile"><div class="ds-interactive-fig-title"></div><div id="plot"></div></div></div></body></html>',
    {runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true});
const w = dom.window;
Object.assign(w, {console, AbortController, structuredClone});
const fg = '#e6e9ef';
w.document.documentElement.style.setProperty('--plot-axis-text', fg);
w.document.documentElement.style.setProperty('--plot-hover-bg', '#222222');
w.eval(fs.readFileSync(path.join(root, 'static', 'plot-theme.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(root, 'static', 'map-tools.js'), 'utf8'));
const app = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
w.eval(app.slice(app.indexOf('function _fetchInteractiveFig('), app.indexOf('function _fetchAndRenderPlot(')));
const map = {type: 'heatmap', x: [0, 1], y: [0, 1], z: [[null, 1], [2, 0]]};
const note = {x: 0, y: 0, showarrow: false, font: {color: '#000000'}};
let checks = 0;
function equal(actual, expected, label) { assert.deepStrictEqual(actual, expected, label); checks++; }
function color(n, data) { return w.PlotTheme.houseLayout({annotations: [n]}, data).annotations[0].font.color; }
for (const theme of ['dark', 'light', 'dark']) {
    w.document.documentElement.setAttribute('data-theme', theme);
    const want = theme === 'dark' ? fg : '#000000';
    equal(color(note, [map]), want, 'missing cell follows theme');
    equal(color({...note, x: 1}, [map]), '#000000', 'finite cell keeps source paint');
    for (const visible of [false, 'legendonly']) equal(color({...note, x: 1}, [{...map, visible}]), want, 'hidden cells are transparent');
    for (const bg of ['transparent', '#fff0', '#ffffff00', 'rgba(255,255,255,0)', 'rgba(255, 255, 255, 0%)',
        'rgb(255 255 255 / 0)', 'hsla(0, 0%, 100%, 0)', 'hsl(0 0% 100% / 0%)']) {
        equal(color({...note, bgcolor: bg}), want, 'transparent annotation box ' + bg);
    }
    equal(color({...note, bgcolor: 'rgba(255,255,255,0.5)'}), '#000000', 'painted box keeps source paint');
}
equal(color({...note, x: 1, y: 1}, [{...map, x: [2, 1, -1], y: [2, 1, -1]}]), fg, 'descending edges select null cell');
const backing = {name: 'legend-backing', fillcolor: '#ffffff', line: {width: 1}};
equal(w.PlotTheme.houseLayout({shapes: [backing]}).shapes[0].fillcolor, '#222222', 'whole legend backing follows theme');
const source = [map, {type: 'scatter', mode: 'markers', x: [.5], y: [.5], marker: {size: 10, color: '#000000'}}];
const tile = w.document.getElementById('tile'), plot = w.document.getElementById('plot');
plot.data = structuredClone(source); plot.layout = {}; plot._fullLayout = {};
let draws = [];
w.Plotly = {react: (el, data, layout) => {draws.push(structuredClone(data)); el.data = data; el.layout = layout; return Promise.resolve();},
    purge: () => {}, relayout: () => Promise.resolve(), restyle: () => Promise.resolve()};
function chips(clickable) {
    w.MapTools.mount(tile, plot, {data: source, layout: {}, clickable}, {uid: 'run:1', fig: 'map'});
    return Array.from(tile.querySelectorAll('.map-tools-chip'), c => c.textContent);
}
for (const payload of [{n_verified: 0, targets: []}, {targets: [{evidence_tier: 'applied'}]},
    {n_verified: 0, targets: [{evidence_tier: 'applied'}]},
    {n_verified: 1, targets: []}, {n_verified: 2, targets: [{evidence_tier: 'applied'}]},
    {n_verified: 1, evidence_tier: 'proposed', verified_against: 'patches', targets: [{evidence_tier: 'applied'}]},
    {n_verified: 1, verified_against: 'patches', targets: [{evidence_tier: 'proposed'}]},
    {n_verified: 1, verified_against: 'unknown', targets: [{evidence_tier: 'applied'}]},
    {n_verified: 1, targets: [{}]}]) {
    equal(chips(payload), ['not verified on this run'], 'unproved or inconsistent verification fails closed');
}
// more distinct target paths than verified writes: which one was verified is unknown
equal(chips({n_verified: 1, targets: [{path: 'a', evidence_tier: 'applied'}, {path: 'b', evidence_tier: 'applied'}]}),
    ['not verified on this run'], 'more target paths than verified writes fails closed');
// the real producers: a learned contract counts distinct paths (one target per panel), a recipe counts targets
equal(chips({n_verified: 1, verified_against: 'patches', evidence_tier: 'applied',
    targets: [{path: 'a', evidence_tier: 'applied'}, {path: 'a', evidence_tier: 'applied'}]}),
    ['verified on this run'], 'a learned contract shown in two panels stays verified');
equal(chips({n_verified: 2, verified_against: 'proposals',
    targets: [{path: 'a', evidence_tier: 'proposed'}, {path: 'a', evidence_tier: 'proposed'}]}),
    ['proposed, not applied'], 'a recipe with two targets on one path stays proposed');
const analysis = {request_hash: 'h', classification: {type: 'ridge', models: ['ridge'], feature_axis: 'x'},
    caps_applied: {cap_x: 2, cap_y: 2, sigma_x: 0, sigma_y: 0, default_x: 1, default_y: 1},
    smoothed: {x: [0, 1], y: [0, 1], z: [[9, 9], [9, 9]]}, contours: [], xref: 'x', yref: 'y',
    compare: {verdict: 'n/a'}, fit: {verdict_line: ''}, provenance_text: '', updates: []};
w.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({results: [analysis]})});
function pause() { return new Promise(resolve => setTimeout(resolve, 50)); }
(async () => {
    chips(null);
    plot.data[1].marker = {size: 5, color: '#000000'};
    w.document.documentElement.setAttribute('data-theme', 'light');
    tile.querySelector('.map-tools-button').click();
    await pause();
    const select = tile.querySelector('[data-map="view"]');
    function view(value) { select.value = value; select.dispatchEvent(new w.Event('input', {bubbles: true})); }
    view('raw'); await pause();
    assert(draws.length); checks++;
    equal(draws.at(-1)[1].marker, {size: 5, color: '#000000'}, 'redraw keeps resize and light theme');
    equal(draws.at(-1)[0].z, map.z, 'raw data never read from smoothed live plot');
    plot.data[1].marker = {size: 7, color: fg};
    plot.data[0].z = [[9, 9], [9, 9]];
    w.document.documentElement.setAttribute('data-theme', 'dark');
    view('raw'); await pause();
    equal(draws.at(-1)[1].marker, {size: 7, color: fg}, 'redraw keeps later dark theme');
    equal(draws.at(-1)[0].z, map.z, 'second raw redraw keeps original gaps');
    const d = {source_size_inches: [16, 12], source_aspect: .75, layout: {
        xaxis201: {anchor: 'y', side: 'top', tickfont: {size: 10}, title: {text: 'Amplitude', font: {size: 14}}},
        yaxis: {domain: [.1, .91]}, title: {text: 'Heading', y: .98, yanchor: 'top', font: {size: 12}},
        annotations: [{name: 'axes-caption:y', xref: 'paper', yref: 'paper',
            y: .94, text: 'Caption', font: {size: 10}, yshift: 0}]}};
    for (const width of [1237, 917]) {
        const fit = w._replotLayoutAt(d, width), axis = fit.xaxis201, caption = fit.annotations[0];
        const axisHeight = axis.tickfont.size * 1.3 + 8 + 5 + axis.title.font.size * 1.3 + 3;
        assert((caption.y - .91) * fit.height + caption.yshift >= axisHeight - 1e-6, 'caption clears fixed top-axis standoff'); checks++;
        const captionTop = caption.y * fit.height + caption.yshift + caption.font.size * 1.3;
        assert(fit.title.y * fit.height - fit.title.font.size * 1.3 >= captionTop + 3 - 1e-6,
            'heading clears the lifted caption'); checks++;
        // a live width change moves the heading where a fresh open puts it
        equal(w._replotSizeUpdate(fit)['title.y'], fit.title.y, 'resize relayout carries the lifted heading');
    }
    const framePlot = w.document.createElement('div'); w.document.body.appendChild(framePlot);
    framePlot._fullLayout = {_size: {w: 1000, h: 500, l: 0, t: 0}};
    framePlot.layout = {shapes: [{name: 'legend-backing', legendgroup: 'legend,legend2', x0: 0, x1: 0, y0: 0, y1: 0}]};
    framePlot.getBoundingClientRect = () => ({left: 0, top: 0});
    framePlot.querySelector = name => ({getBoundingClientRect: () => name === 'g.legend' ?
        {left: 100, right: 300, top: 100, bottom: 120} : {left: 100, right: 350, top: 120, bottom: 140}});
    let frameUpdate = null;
    w.Plotly.relayout = (el, updates) => {frameUpdate = updates; return Promise.resolve();};
    await w._fitLegendBackings(framePlot);
    equal(frameUpdate && frameUpdate['shapes[0].x1'], .353, 'one frame covers the widest actual row');
    equal(frameUpdate && frameUpdate['shapes[0].y0'], .714, 'one frame covers minimum-height rows');
    plot.classList.add('js-plotly-plot');
    plot._houseSourceLayout = {annotations: [{...note, x: 1}]};
    plot._houseSourceData = [map]; plot.data = [{...map, visible: 'legendonly'}];
    let themeUpdates = {};
    w.Plotly.relayout = (el, updates) => {if (el === plot) Object.assign(themeUpdates, updates); return Promise.resolve();};
    w.PlotTheme.retheme();
    equal(themeUpdates['annotations[0].font.color'], fg, 'theme toggle sees the live hidden map');
    w.document.documentElement.setAttribute('data-theme', 'light'); w.PlotTheme.retheme();
    equal(themeUpdates['annotations[0].font.color'], '#000000', 'hidden map text restores source paint in light');
    w.document.documentElement.setAttribute('data-theme', 'dark');
    plot.data = [{...map, z: [[null, null], [null, null]]}]; w.PlotTheme.retheme();
    equal(themeUpdates['annotations[0].font.color'], fg, 'theme toggle sees a live map gap');
    plot.data = [map]; w.PlotTheme.retheme();
    equal(themeUpdates['annotations[0].font.color'], '#000000', 'painted cell restores source paint on the next toggle');
    // Fetch two tiles with the same refusal, then one with another reason.
    tile._mapTools.destroy();
    w.MapTools = null;
    w._plotlyRender = (el, data, layout) => Promise.resolve();
    w._pruneInteractiveTiles = () => {};
    const container = w.document.getElementById('run'); container._gen = 1;
    const head = w.document.createElement('div'); head.className = 'ds-replot-head';
    const list = w.document.createElement('div'); list.className = 'ds-interactive-list';
    container.prepend(head); container.appendChild(list);
    // three figures: a refusal, one that adds a clause and repeats it, then the first again
    const reasons = ['a.x: no proof', 'b.y: ambiguous; a.x: no proof', 'a.x: no proof'];
    let call = 0;
    w.fetch = () => { const reason = reasons[call++]; return Promise.resolve({json: () => Promise.resolve({data: [], layout: {},
        display_only_reason: reason, notices: [reason, 'Artist omitted']})}); };
    for (let i = 0; i < 3; i++) {
        const div = w.document.createElement('div'); div.dataset.endpoint = 'replot/plot'; list.appendChild(div);
        w._fetchInteractiveFig(div, 'run:1', 'f' + i, 1, container); await pause();
    }
    equal(container.querySelectorAll('.ds-replot-run-note').length, 1, 'refusal appears once per run');
    equal(container.querySelector('.ds-replot-run-note').textContent, 'a.x: no proof; b.y: ambiguous',
        'each refusal clause is said once, however many figures give it');
    equal(container.querySelector('.ds-replot-run-note').nextElementSibling === list && container.firstElementChild === head,
        true, 'the run note sits under the run header, above the figures');
    equal(Array.from(container.querySelectorAll('.ds-replot-note')).filter(n => n.textContent === 'Artist omitted').length, 3,
        'per-figure unsupported content still has its own notice');
    console.log('f5_plot_selfcheck: ' + checks + ' assertions passed');
    w.close();
})().catch(error => {console.error(error); w.close(); process.exitCode = 1;});
