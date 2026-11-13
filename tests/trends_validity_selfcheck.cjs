/* Chip Status Trends, the client half of F1/F2 (excluded points), F4 (a
 * provenance line every point shares is said once), F5 (the combined tooltip
 * is never squeezed at the window edge), F6 (one pair's prefix said once) and
 * F7 (many unchanged lines start hidden). Drives the shipped chip-status.js
 * under jsdom. Run: node tests/trends_validity_selfcheck.cjs
 * (driven by tests/test_trends_validity_js.py) */
const {JSDOM} = require('jsdom');
const fs = require('fs');
const path = require('path');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else console.log('ok - ' + m); }
const HTML = '<div id="topo-trends"><input type="checkbox" id="topo-trends-show-excluded">'
    + '<div class="topo-trends-grid"><div class="topo-trend-box" data-trend-metric="m" data-trend-kind="pair">'
    + '<div class="topo-trend-chart" id="topo-trend-0"></div></div></div>'
    + '<script type="application/json" id="topo-trends-data"></script></div>';
function boot() {
    const dom = new JSDOM(HTML, {url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window;
    const s = {w, draws: []};
    w.htmx = {trigger() {}, ajax() { return Promise.resolve(); }, on() {}, process() {}};
    w.fetch = () => new Promise(() => {});
    w.eval(fs.readFileSync(path.join(STATIC, 'chip-status.js'), 'utf8'));
    w._plotlyRender = (host, data, layout) => { s.draws.push({data, layout}); return Promise.resolve(); };
    return s;
}
const T = (i) => '2026010' + (i + 1) + '_000000';
const charts = [{metric: 'm', kind: 'pair', label: 'IRB', unit: '', scale: 1, series: [
    {entity: 'q1-2 \u00b7 cz_a', points: [[T(0), .95], [T(1), null], [T(2), .96]],
     excluded: [[T(1), 2.167, 'unphysical']],
     attr: {[T(0)]: {label: 'saved in #1', sub: 'writer not proven'},
            [T(2)]: {label: 'saved in #3', sub: 'writer not proven'}}},
    {entity: 'q1-2 \u00b7 cz_b', points: [[T(0), .94], [T(2), .97]]},
]}];

// toggle off (the default): no excluded mark, the axis is the valid points'
let s = boot(), w = s.w;
w.localStorage.clear();
w.ChipTrends.render(JSON.parse(JSON.stringify(charts)));
let d = s.draws.pop();
ok(!d.data.some(t => t.meta === 'excluded'), 'off: no excluded mark is drawn');
const ys = [].concat(...d.data.map(t => t.y || []));
ok(!ys.includes(2.167), 'off: the excluded value is in no trace');
ok(d.layout.yaxis.autorange === true, 'off: Plotly autoranges over the valid points only');

// F4: the shared sub-line leaves the hovers and is said once
const cd = d.data[0].customdata;
ok(!/writer not proven/.test(cd[0][1]) && !/writer not proven/.test(cd[2][1]), 'F4: no point hover repeats "writer not proven"');
const key = (d.layout.annotations || []).map(a => a.text || '').join('\n');
ok(/every point: writer not proven/.test(key), 'F4: the chart key says it once: ' + key);

// F6: the shared pair prefix is the legend title, the lines are the macros
ok(d.data[0].name === 'cz_a' && d.data[1].name === 'cz_b', 'F6: legend names without the repeated pair');
ok(d.layout.legend.title && /q1-2/.test(d.layout.legend.title.text), 'F6: the pair is the legend title');

// toggle on: a hollow mark for each excluded value, outside the axis range
w.document.getElementById('topo-trends-data').textContent = JSON.stringify(charts);
w.ChipTrends.setShowExcluded(true);
d = s.draws.pop();
const ex = d.data.find(t => t.meta === 'excluded');
ok(ex && ex.x.length === 1, 'on: one excluded mark');
ok(ex && /open/.test(ex.marker.symbol), 'on: the mark is hollow');
ok(ex && /outside the physical range/.test(ex.customdata[0][1]), 'on: its hover says why');
ok(ex && /2\.167/.test(ex.text[0]), 'on: its hover keeps the real value: ' + (ex && ex.text[0]));
ok(d.layout.yaxis.autorange === false && d.layout.yaxis.range[1] < 1.1,
   'on: the axis is still the valid points\' range: ' + JSON.stringify(d.layout.yaxis.range));
ok(w.localStorage.getItem('quam_trends_show_excluded') === '1', 'on: remembered in this browser');
ok(w.document.getElementById('topo-trends-show-excluded').checked, 'on: the box shows it');

// an all-valid chart draws the same with the toggle on or off
const valid = [{metric: 'm', kind: 'pair', label: 'IRB', unit: '', scale: 1, series: [
    {entity: 'q1-2', points: [[T(0), .95], [T(2), .96]]}]}];
w.ChipTrends.render(JSON.parse(JSON.stringify(valid)));
const on = JSON.stringify(s.draws.pop());
w.localStorage.setItem('quam_trends_show_excluded', '0');
w.ChipTrends.render(JSON.parse(JSON.stringify(valid)));
ok(JSON.stringify(s.draws.pop()) === on, 'an all-valid chart is unchanged by the toggle');

// F7: past eight lines, the unchanged ones start hidden; one moving line stays
const many = [{metric: 'm', kind: 'pair', label: 'T', unit: '', scale: 1, series:
    Array.from({length: 10}, (_, i) => ({entity: 'q' + i,
        points: [[T(0), 1 + i], [T(2), i === 0 ? 5 : 1 + i]]}))}];
w.ChipTrends.render(many);
d = s.draws.pop();
const hidden = d.data.filter(t => t.visible === 'legendonly').length;
ok(hidden === 9 && d.data[0].visible !== 'legendonly', 'F7: 9 unchanged lines hidden, the moving one shown');
ok(/9 unchanged lines hidden/.test((d.layout.annotations || []).map(a => a.text).join(' ')), 'F7: the key says how many');
const few = [{metric: 'm', kind: 'pair', label: 'T', unit: '', scale: 1, series:
    Array.from({length: 4}, (_, i) => ({entity: 'q' + i, points: [[T(0), 1 + i], [T(2), 1 + i]]}))}];
w.ChipTrends.render(few);
ok(!s.draws.pop().data.some(t => t.visible === 'legendonly'), 'F7: a chart of few lines hides nothing');

// F5: a combined tooltip near the right edge flips to the pointer's left
Object.defineProperty(w.HTMLElement.prototype, 'offsetWidth', {configurable: true, get() { return this.id === 'trends-cotip' ? 300 : 0; }});
Object.defineProperty(w.HTMLElement.prototype, 'offsetHeight', {configurable: true, get() { return this.id === 'trends-cotip' ? 60 : 0; }});
const axis = {d2c: (v) => v, l2p: (v) => v, _offset: 0};
const host = w.document.getElementById('topo-trend-0');
host.data = [{name: 'a', x: [w.innerWidth - 20], y: [100], text: ['1 %']},
             {name: 'b', x: [w.innerWidth - 20], y: [100], text: ['2 %']}];
w._trendsCoTip(host, {points: [{x: w.innerWidth - 20, y: 100, xaxis: axis, yaxis: axis}]});
const tip = w.document.getElementById('trends-cotip');
const left = parseInt(tip.style.left, 10);
ok(left + 300 <= w.innerWidth - 8, 'F5: the tooltip fits left of the pointer: left=' + left + ' vw=' + w.innerWidth);
const css = fs.readFileSync(path.join(STATIC, 'ui-kit.css'), 'utf8');
ok(/\.ui-trends-cotip\s*\{[^}]*width:\s*max-content/.test(css), 'F5: the tooltip takes its content width, never a squeezed column');

process.exit(fails ? 1 : 0);
