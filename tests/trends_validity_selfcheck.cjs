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

// ---- recheck round (N1, P3, F13) ----
// N1: an excluded point ends the held step AT its own time; the gap covers
// the excluded interval only. q1 holds 95.00 % from 07:19; the next runs
// (11:55, 11:58) are excluded; the next valid value is at 12:04.
const K = (h, m) => '20261113_' + h + m + '00';
const n1 = [{metric: 'm', kind: 'qubit', label: 'GE', unit: '', scale: 1, series: [
    {entity: 'q1', points: [[K('07', '19'), .95], [K('11', '55'), null], [K('11', '58'), null], [K('12', '04'), .9601]],
     excluded: [[K('11', '55'), 2.0, 'invalid'], [K('11', '58'), 2.0, 'invalid']]},
    {entity: 'q2', points: [[K('07', '19'), .88], [K('12', '04'), .89]]},
]}];
w.localStorage.setItem('quam_trends_show_excluded', '0');
w.ChipTrends.render(JSON.parse(JSON.stringify(n1)));
d = s.draws.pop();
let t0 = d.data[0];
const at1155 = t0.x.map((x, i) => [x, t0.y[i]]).filter(p => String(p[0]).indexOf('11:55') >= 0);
ok(at1155.length === 2 && at1155[0][1] === .95 && at1155[1][1] === null,
   'N1: the held 95.00 % runs up to the excluded run, then the gap: ' + JSON.stringify(at1155));
ok(t0.marker && t0.marker.size[t0.x.indexOf(at1155[0][0])] === 0, 'N1: the bridge point carries no marker');
ok(/held until/.test(t0.customdata[1][1]), 'N1: its hover says it is the held value: ' + t0.customdata[1][1]);
ok(t0.y.filter(v => v === null).length === 2, 'N1: the excluded interval itself stays a gap');
// a line with no excluded point is drawn exactly as before
ok(JSON.stringify(d.data[1].y) === JSON.stringify([.88, .89]) && d.data[1].marker.size === 5,
   'N1: an all-valid line is unchanged');

// P3: a line whose every value is excluded leaves the legend; the key names it
const p3 = [{metric: 'm', kind: 'qubit', label: 'F', unit: '', scale: 1, series: [
    {entity: 'q5', points: [[T(0), null], [T(1), null]], excluded: [[T(0), 0, 'unphysical'], [T(1), 0, 'unphysical']]},
    {entity: 'q6', points: [[T(0), .99], [T(1), .98]]},
]}];
w.ChipTrends.render(JSON.parse(JSON.stringify(p3)));
d = s.draws.pop();
ok(d.data[0].showlegend === false && d.data[1].showlegend !== false, 'P3: the all-excluded line is not in the legend');
ok(/no line \(every value excluded\): q5/.test((d.layout.annotations || []).map(a => a.text).join(' ')),
   'P3: the chart key names it');

// F13: at a narrow width a long legend grows the figure, a wide one does not
const RW = w.ChipTrends._legendRows;
const names15 = Array.from({length: 15}, (_, i) => 'q' + String.fromCharCode(65 + (i % 4)) + (i + 1));
ok(RW(names15, 360) > 2 && RW(names15, 1600) === 1 && RW(names15, 0) === 1,
   'F13: legend rows by width: ' + [RW(names15, 360), RW(names15, 1600)]);
Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', {configurable: true,
    get() { return this.classList && this.classList.contains('topo-trend-chart') ? (w.__cw || 0) : 0; }});
const many15 = [{metric: 'm', kind: 'qubit', label: 'T1', unit: '', scale: 1, series:
    names15.map((nm, i) => ({entity: nm, points: [[T(0), 1 + i], [T(2), 2 + i]]}))}];
w.__cw = 360; w.ChipTrends.render(JSON.parse(JSON.stringify(many15)));
const hNarrow = s.draws.pop().layout.height;
w.__cw = 1600; w.ChipTrends.render(JSON.parse(JSON.stringify(many15)));
const hWide = s.draws.pop().layout.height;
ok(hWide === 300 && hNarrow > 300, 'F13: the narrow figure grows by its legend rows: ' + hNarrow + ' vs ' + hWide);

// F13: a page opened on ?view= holds its jump past the 8 s window until the
// reader moves (or the 60 s cap); a click jump keeps the 8 s window
const J = w.ChipStatus.jumpGuard;
const realNow = w.Date.now;
let clock = 1e12; w.Date.now = () => clock;
J.note('trends', null); J.deepen(); clock += 20000;
ok(J.current() === 'trends', 'F13: a deep-link jump is still live 20 s on');
clock += 45000;
ok(J.current() === null, 'F13: ...never past the 60 s cap');
J.note('trends', null); clock += 20000;
ok(J.current() === null, 'F13: a plain jump keeps its 8 s window');
J.note('trends', null); J.deepen(); J.cancel();
ok(J.current() === null, 'F13: the reader moving ends a deep-link jump');
w.Date.now = realNow;
const src = fs.readFileSync(path.join(STATIC, 'chip-status.js'), 'utf8');
ok(/setChipStatusView\(_deepView, null, true\);\s*window\.ChipStatus\.jumpGuard\.deepen\(\);/.test(src),
   'F13: the mount deepens the ?view= jump');

process.exit(fails ? 1 : 0);
