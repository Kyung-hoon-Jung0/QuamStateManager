const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');
const root = process.argv[2] || path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, 'quam_state_manager/web', name), 'utf8');
const boot = read('templates/_theme_boot.html').match(/<script>([\s\S]*?)<\/script>/)[1];
const app = read('static/app.js');
const block = app.slice(app.indexOf('function _applyThemeToPlotly(theme)'), app.indexOf('/* Sidebar toggle'));
const dom = new JSDOM('<html><body><button data-theme-choice="light"></button><button data-theme-choice="dark"></button><button data-theme-choice="qm"></button></body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only' });
const w = dom.window;
const html = w.document.documentElement;
w.localStorage.setItem('quam_theme', 'qm');
w.eval(boot);
assert(w.SM_THEMES && w.SM_THEMES.includes('qm'), 'QM is registered');
assert.equal(html.dataset.theme, 'qm', 'saved QM boots before paint');
w.localStorage.setItem('quam_theme', 'invalid');
w.eval(boot);
assert.equal(html.dataset.theme, 'qm', 'invalid saved theme falls back to the QM default');
w.history.replaceState({}, '', '?theme=qm');
w.eval(boot);
assert.equal(html.dataset.theme, 'qm', 'QM URL override');
const values = { '--pico-color': '#e5e5e5', '--plot-axis-text': '#e5e5e5', '--pico-background-color': '#0e0e0e',
    '--pico-card-background-color': '#1d1d1d', '--pico-muted-color': '#999999', '--plot-grid': '#ffffff1a' };
w.getComputedStyle = () => ({ getPropertyValue: k => values[k] || '' });
w.UI_CONFIG = { plotly: { topology: { subLabelFont: {}, edgeLabelFont: {}, dashboard: {} } }, instrumentWiring: {} };
w._lightDefaults = {};
w.eval(read('static/plot-theme.js'));
w.eval(block);
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
assert.equal(typeof w.setTheme, 'function', 'explicit three-way theme selector');
for (const theme of ['light', 'dark', 'qm']) {
    w.setTheme(theme);
    assert.equal(html.dataset.theme, theme);
    assert.equal(w.localStorage.getItem('quam_theme'), theme);
    assert.equal(w.document.querySelector('[data-theme-choice="' + theme + '"]').getAttribute('aria-pressed'), 'true');
}
assert.equal(w.UI_CONFIG.instrumentWiring.gridBg, '#0e0e0e', 'QM wiring uses black surface');
assert.equal(w.UI_CONFIG.plotly.topology.hoverBg, '#1d1d1d', 'QM topology uses palette');
assert.equal(w.PlotTheme.houseData([{type:'scatter', line:{color:'#000000'}}])[0].line.color, '#e5e5e5', 'QM adapts black plot artists');
w.setTheme('light');
assert.equal(w.PlotTheme.houseData([{type:'scatter', line:{color:'#000000'}}])[0].line.color, '#000000', 'Light artist remains unchanged');
for (const file of ['cz-map.js', 'cz-plan.js']) {
    const source = read('static/' + file);
    const expr = source.match(/(?:return |var dark = )(\(?document\.documentElement\.getAttribute\('data-theme'\)[^;]+);/);
    assert(expr, file + ' dark predicate');
    html.dataset.theme = 'qm';
    assert(w.eval(expr[1]), file + ' treats QM as dark');
    html.dataset.theme = 'light';
    assert(!w.eval(expr[1]), file + ' keeps Light light');
}
const base = read('templates/base.html');
for (const theme of ['light', 'dark', 'qm']) assert(base.includes('data-theme-choice="' + theme + '"'), 'Settings choice ' + theme);
const calc = read('static/calc.js').match(/window\.addEventListener\('storage', function \(ev\) \{([\s\S]*?)\n        \}\);/)[0];
w.eval(calc);
w.dispatchEvent(new w.StorageEvent('storage', {key:'quam_theme', newValue:'qm'}));
assert.equal(html.dataset.theme, 'qm', 'standalone calculator follows QM');
console.log('QM controls, boot, artists, maps and calculator assertions passed');
w.close();
