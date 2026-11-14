/* jsdom selfcheck: the lane timeline component (gate-timeline.js).
 *
 * Real: gate-timeline.js and plot-theme.js (house layout), run in the jsdom realm.
 * Stubbed: window._plotlyRender (records the figure handed to Plotly instead of
 * drawing; Plotly itself is not loaded) and window.PlotHost (records observe /
 * unobserve / purgeWithin calls).
 *
 * `npm run selfcheck` runs it; exit 2 = jsdom not installed. */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('skip: jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const src = fs.readFileSync(path.join(STATIC, 'gate-timeline.js'), 'utf8');
const themeSrc = fs.readFileSync(path.join(STATIC, 'plot-theme.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>',
    { url: 'http://localhost/', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;
const doc = window.document;
window.eval(themeSrc);
window.eval(`
  window.__renders = []; window.__host = { observe: 0, unobserve: 0, purge: 0 };
  window._plotlyRender = function (el, data, layout, config) {
    window.__renders.push({ el: el, data: data, layout: layout });
    // the graph div's event API as Plotly gives it (on / removeListener)
    el.__handlers = el.__handlers || {};
    el.on = function (ev, fn) { (el.__handlers[ev] = el.__handlers[ev] || []).push(fn); };
    el.removeListener = function (ev, fn) { el.__handlers[ev] = (el.__handlers[ev] || []).filter(function (f) { return f !== fn; }); };
    return Promise.resolve(el);
  };
  window.__relayouts = [];
  window.Plotly = { relayout: function (el, upd) { window.__relayouts.push(upd); return Promise.resolve(el); } };
  window.PlotHost = {
    observe: function () { window.__host.observe++; },
    unobserve: function () { window.__host.unobserve++; },
    purgeWithin: function () { window.__host.purge++; }
  };
  (function () {
    var add = EventTarget.prototype.addEventListener, rm = EventTarget.prototype.removeEventListener;
    window.__live = 0;
    EventTarget.prototype.addEventListener = function () { window.__live++; return add.apply(this, arguments); };
    EventTarget.prototype.removeEventListener = function () { window.__live--; return rm.apply(this, arguments); };
  })();
`);
window.eval(src);

let failed = 0, passed = 0;
function ok(cond, label) {
    if (cond) { passed++; console.log('ok - ' + label); } else { failed++; console.log('FAIL: ' + label); }
}
const host = doc.getElementById('host');
const GT = window.GateTimeline;
const last = () => window.__renders[window.__renders.length - 1];
const yAxes = (L) => Object.keys(L).filter((k) => /^yaxis\d*$/.test(k)).sort((a, b) => (+a.slice(5) || 1) - (+b.slice(5) || 1));
const xAxes = (L) => Object.keys(L).filter((k) => /^xaxis\d*$/.test(k));
const role = (fig, r) => fig.data.filter((t) => t.meta && t.meta.role === r);
const axRef = (i) => (i === 0 ? 'y' : 'y' + (i + 1));

function lane(key, group, segs, extra) {
    return Object.assign({ key, group, label: key, port: 'con1 / 1 / ' + key.length, limit_v: [-0.5, 0.5], offset_v: 0,
                           segments: segs, sim: null }, extra || {});
}
function seg(op, t, v, extra) {
    return Object.assign({ operation: op, start_ns: t[0], end_ns: t[t.length - 1], t_ns: t, v,
                           peak_v: Math.max(...v), min_v: Math.min(...v), length_ns: t[t.length - 1] - t[0],
                           truncated: false, flags: [] }, extra || {});
}

const title = (fig, key) => fig.layout.annotations.find((a) => a.meta && a.meta.role === 'lane-title' && a.meta.lane === key);
const lastRoot = () => [...host.querySelectorAll('[data-gate-timeline]')].pop();
const facts = (fig, key) => fig.layout.annotations.find((a) => a.meta && a.meta.role === 'lane-facts' && a.meta.lane === key);
const segText = (fig, key) => fig.layout.annotations.filter((a) => a.meta && a.meta.role === 'segment-text' && a.meta.lane === key);

const shaped = seg('cz_x_pulse_q1_q2', [63, 64, 65, 66, 115], [0.0, 0.05, 0.1, 0.05, 0.0]);
const DATA = {
    lanes: [
        lane('q1.z', 'gate', [shaped], { limit_v: [-0.5, 0.08] }),
        lane('q2.z', 'gate', [seg('flux_a_long_operation_name', [10, 30], [-0.07, -0.07]), seg('flux_b', [50, 60], [0.1, 0.1]),
                              seg('flux_c', [62, 70], [0.05, 0.05])], { limit_v: null }),
        lane('coupler_q1_q2', 'gate', [seg('bip', [20, 21, 22, 40], [0.3, -0.3, 0.3, 0.0],
                                           { flags: ['over_range', 'bad_length'] }), seg('late', [900, 1000], [0.1, 0.1])],
             { limit_v: [-0.25, 0.25], offset_v: 0.05, sim: { t_ns: [20, 30, 40], v: [0.29, -0.1, 0.01] } }),
        lane('q3.z', 'spectator', [seg('park', [0, 100, 200], [0.02, 0.02, 0.02], { truncated: true, end_ns: 300 })]),
    ],
    notes: ['Times are program time, before filters.', 'MW timing against z lines is not computed.'],
    cannot_draw: null,
};

(async () => {
const h = GT.render(host, DATA, {});
let fig = last();

// one row per drawn lane, one x axis
let ya = yAxes(fig.layout);
ok(ya.length === 3 && h.laneCount().drawn === 3, 'folded: one y axis per drawn lane (3)');
ok(xAxes(fig.layout).length === 1 && fig.data.every((t) => t.xaxis === 'x'), 'one shared x axis for every trace');
ok(ya.every((k, i) => i === 0 || fig.layout[k].domain[1] < fig.layout[ya[i - 1]].domain[0]), 'lane rows stacked without overlap');
ok(fig.layout.hovermode === 'x unified' && fig.layout.hoverdistance === -1, 'hover is one unified readout at any distance');
ok(JSON.stringify(h.laneCount()) === JSON.stringify({ gate: 3, spectator: 1, drawn: 3 }), 'laneCount folded');
ok(title(fig, 'q1.z').yref === 'y domain' && title(fig, 'coupler_q1_q2').yref === 'y3 domain', 'gate lanes on top in order');

// lane titles: horizontal, left aligned at the chart's left edge, one fact per line
const t3 = title(fig, 'coupler_q1_q2'), f3 = facts(fig, 'coupler_q1_q2');
ok([t3, f3].every((a) => a.xref === 'paper' && a.x === 0 && a.xanchor === 'left' && a.align === 'left' &&
   a.xshift === -fig.layout.margin.l + 9 && !a.textangle) && ya.every((k) => !fig.layout[k].title || !fig.layout[k].title.text),
   'lane titles left aligned at the chart edge, horizontal, no axis title');
ok(t3.text === '<b>coupler_q1_q2</b>' && t3.font.size === 12 && f3.text === 'con1 / 1 / 13<br>limit -0.25 to 0.25 V<br>offset 0.05 V' && f3.font.size === 11,
   'title lines: label 12 px; port, limit, offset each on its own line at 11 px');
ok(/range not known/.test(facts(fig, 'q2.z').text) && !/offset/.test(facts(fig, 'q1.z').text), 'unknown range said; offset only when not 0');
{   const gutter = fig.layout.margin.l - 52;
    ok(gutter >= 'limit -0.25 to 0.25 V'.length * 5 && gutter <= 'limit -0.25 to 0.25 V'.length * 7, 'gutter is as wide as the longest title line needs, no wider');
}
ok(fig.layout.font.size === 11 && fig.layout.font.family && fig.layout.annotations.every((a) => a.font && a.font.size >= 11) &&
   ['xaxis', ...ya].every((k) => fig.layout[k].tickfont && fig.layout[k].tickfont.size === 11), 'UI font family; nothing below 11 px')

// samples value by value, gaps not joined
const segs = role(fig, 'segment');
const want = DATA.lanes.slice(0, 3).flatMap((l, i) => l.segments.map((s) => ({ s, ax: axRef(i) })));
ok(segs.length === want.length && want.every((w, k) =>
    segs[k].yaxis === w.ax && JSON.stringify(segs[k].x) === JSON.stringify(w.s.t_ns) &&
    JSON.stringify(segs[k].y) === JSON.stringify(w.s.v)), 'trace points equal the input t_ns / v');
ok(segs.filter((t) => t.meta.lane === 'q2.z').length === 3, 'segments with idle between are separate traces (gap not joined)');
ok(segs[1].x[0] === 10 && segs[0].x[0] === 63, 'each lane starts where its start_ns says');

// valuesAt
const at = (t, key) => h.valuesAt(t).find((r) => r.key === key);
ok(at(64, 'q1.z').v === 0.05 && at(64, 'q1.z').operation === 'cz_x_pulse_q1_q2', 'valuesAt inside a segment');
ok(at(65.5, 'q1.z').v === 0.1, 'valuesAt between samples holds the last sample');
ok(at(63, 'q1.z').v === 0.0 && at(115, 'q1.z').operation === 'cz_x_pulse_q1_q2', 'valuesAt at both segment edges');
ok(at(40, 'q2.z').operation === null && at(40, 'q2.z').v === null, 'valuesAt in the idle gap is idle');
ok(at(500, 'q1.z').operation === null && at(0, 'q1.z').v === null, 'valuesAt outside every segment is idle');
ok(at(250, 'q3.z').operation === 'park' && at(250, 'q3.z').v === null, 'truncated: past the received samples v is null');
ok(h.valuesAt(64).length === 4, 'valuesAt lists every lane, folded spectators too');
const hov = role(fig, 'hover').find((t) => t.meta.lane === 'q2.z');
ok(hov && hov.text[hov.x.indexOf(40)] === 'idle' && hov.text[hov.x.indexOf(10)] === 'flux_a_long_operation_name: -0.07 V', 'hover text is the valuesAt readout');
ok([...Array(116).keys()].every((t) => hov.x.includes(t)), 'hover grid has every 1 ns from program time 0 over the drawn span');

// the time axis: from program time 0 to the last end plus a small pad, fixed (no autorange)
{
    const xr = fig.layout.xaxis.range;
    ok(xr[0] === 0 && xr[1] > 1000 && xr[1] <= 1000 * 1.04 + 2 && fig.layout.xaxis.autorange === false, 'time axis 0 to the last end plus a pad, no autorange');
    const one = GT.render(host, { lanes: [lane('q9.z', 'gate', [seg('late', [161, 261], [0.1, 0.1])])], notes: [] });
    const f1 = last();
    ok(f1.layout.xaxis.range[0] === 0 && f1.layout.xaxis.range[1] > 261 && f1.layout.xaxis.range[1] < 280, 'a single late pulse: axis still starts at 0');
    const sa = f1.layout.annotations.filter((a) => a.meta && a.meta.role === 'starts-at');
    ok(sa.length === 1 && sa[0].text === 'starts at 161 ns' && sa[0].x === 0 && sa[0].xanchor === 'left' && sa[0].font.size === 11, 'late start: "starts at 161 ns" at the lane left');
    one.destroy();
    const neg = GT.render(host, { lanes: [lane('q9.z', 'gate', [seg('early', [-20, 30], [0.1, 0.1])])], notes: [] });
    const f2 = last();
    ok(f2.layout.xaxis.range[0] === -20, 'a negative start extends the axis to it');
    ok(!f2.layout.annotations.some((a) => a.meta && a.meta.role === 'starts-at'), 'no "starts at" text when a lane starts at or before 0');
    neg.destroy();
    // here the axis runs to 1000 ns: starts at 10, 20 and 63 ns leave too little idle room for the words
    ok(fig.layout.annotations.filter((a) => a.meta && a.meta.role === 'starts-at').length === 0,
       '"starts at" only where the idle stretch can hold the words (it would sit on the pulse otherwise)');
}

// scales fit the data, not the limits
const r1 = fig.layout.yaxis.range, r2 = fig.layout.yaxis2.range;
ok(JSON.stringify(r1) !== JSON.stringify(r2), 'per lane: each lane has its own range');
ok(r1[0] <= 0 && r1[1] >= 0.1 && r1[1] < 0.3 && r1[0] > -0.1, 'per lane: range fits the data (0..0.1 V), not the 0.5 V limit');
ok(r2[0] < -0.07 && r2[1] > 0.1, 'per lane: an all-negative segment and 0 V stay visible');
h.setScale('common');
fig = last();
ya = yAxes(fig.layout);
ok(JSON.stringify(fig.layout.yaxis.tickvals) === JSON.stringify(fig.layout.yaxis3.tickvals) && fig.layout.yaxis.tickvals[0] === -0.3 && fig.layout.yaxis.tickvals.slice(-1)[0] === 0.3,
   'common: all lanes share one data range (ticks)');
ok(fig.layout.yaxis.range[1] - 0.3 < fig.layout.yaxis2.range[1] - 0.3 || fig.layout.yaxis.range[0] !== fig.layout.yaxis2.range[0],
   'common: head room per lane (a lane with two label rows differs)');
ok(fig.layout.yaxis.range[0] <= -0.3 && fig.layout.yaxis.range[1] >= 0.3 && fig.layout.yaxis.range[1] < 1.5, 'common: range covers the data of every lane');
const segBtns = [...host.querySelectorAll('.ui-segmented .ui-segmented-opt')];
ok(segBtns.length === 2 && segBtns[1].classList.contains('ui-segmented-on') && segBtns[1].getAttribute('aria-pressed') === 'true', 'segmented control shows common');
ok(host.querySelector('.ui-segmented').getAttribute('role') !== 'group' && host.querySelector('.ui-segmented').getAttribute('aria-label') === 'Vertical scale',
   'scale switch is not a stretched [role=group] and keeps its label');
segBtns[0].click();
fig = last();
ok(JSON.stringify(fig.layout.yaxis.range) !== JSON.stringify(fig.layout.yaxis2.range), 'clicking Per lane goes back to own ranges');

// zero and limit lines
const shapesOf = (ax, name) => fig.layout.shapes.filter((s) => s.yref === ax && s.name === name);
ok(['y', 'y2', 'y3'].every((ax) => shapesOf(ax, 'zero').length === 1), 'every lane has its 0 V line');
ok(shapesOf('y', 'limit').length === 1 && shapesOf('y', 'limit')[0].y0 === 0.08, 'only the limit inside the shown range is drawn');
ok(shapesOf('y2', 'limit').length === 0 && shapesOf('y3', 'limit').length === 2, 'no limit line when unknown; an over-range lane shows the limits it crosses');

// segment text
const tq1 = segText(fig, 'q1.z')[0];
ok(tq1.text === 'cz_x_pulse_q1_q2<br>max 0.1 V, 52 ns' && tq1.x === 63 && tq1.xanchor === 'left', 'shaped text: name, max, length at the segment start');
ok(!/\\u00b7|00b7|·/.test(tq1.text), 'no middle-dot separator');
const tq2 = segText(fig, 'q2.z');
ok(tq2[0].meta.line2 === 'level -0.07 V, 20 ns', 'negative constant shows its negative level');
ok(segText(fig, 'coupler_q1_q2')[0].meta.line2.startsWith('max 0.3 V, min -0.3 V, 20 ns'), 'max and min when below 0');
ok(tq2.map((a) => a.meta.row).join() === 'top,bottom,top' && tq2[1].yanchor === 'bottom', 'several segments alternate label rows');
ok(tq2[0].meta.name.includes('…') && tq2[0].meta.name.length < 'flux_a_long_operation_name'.length && tq2[1].meta.name === 'flux_b',
   'a name that would reach the next label in its row is shortened in the middle');
ok(tq1.y === 1 && tq1.yanchor === 'top', 'segment text sits in the top band');
{   // the data stays out of the text bands: max data below the top band, min above the bottom band
    const lanePx = 110, band = 36, per = (r) => (r[1] - r[0]) / lanePx;
    ok(r1[1] - 0.1 >= band * per(r1) - 1e-9, 'top text band is free of data');
    ok(-0.07 - r2[0] >= band * per(r2) - 1e-9, 'bottom text band is free of data');
}

// flags: the segment itself and its text in the flag colour, no marker
const coupler = segs.find((t) => t.meta.lane === 'coupler_q1_q2');
const tc = segText(fig, 'coupler_q1_q2')[0];
ok(/over the port range/.test(tc.text) && /length is not a multiple of 4 ns/.test(tc.text), 'flag texts');
ok(coupler.line.color === tc.font.color && coupler.line.color !== segs[0].line.color, 'flagged segment and its text in the flag colour');
ok(role(fig, 'flag').length === 0 && !fig.data.some((t) => t.mode === 'markers' && t.marker && t.marker.symbol), 'no marker on the trace');
ok(tq1.font.color === fig.layout.font.color || tq1.font.color !== tc.font.color, 'clean segment text not in the flag colour');

// pulse body: filled to 0 V, see-through, flag colour when flagged; the simulation has no fill
{
    const fills = role(fig, 'fill');
    const alpha = (c) => window.PlotTheme.withAlpha(c, 0.18);
    ok(fills.length === segs.length && fills.every((f) => f.fill === 'tozeroy'), 'every segment has a body filled to 0 V');
    ok(fills.every((f) => { const line = segs.find((s) => s.meta.lane === f.meta.lane && s.meta.operation === f.meta.operation);
        return f.fillcolor === alpha(line.line.color) && f.fillcolor !== line.line.color; }), 'fill is the line colour, see-through');
    const cf = fills.find((f) => f.meta.lane === 'coupler_q1_q2' && f.meta.operation === 'bip');
    ok(cf.fillcolor === alpha(window.PlotTheme.lossColor()), 'flagged fill in the flag colour');
    ok(role(fig, 'sim').every((t) => !t.fill), 'simulation line has no fill');
}

// zoom, pan, resize: the texts are re-fitted to the real width and the visible range
{
    const g = host.querySelector('[data-gate-timeline-plot]');
    await new Promise((r) => setTimeout(r, 10));
    const hs = (g.__handlers || {}).plotly_relayout || [];
    ok(hs.length === 1, 'listens to the chart relayout event once');
    g._fullLayout = { xaxis: { range: [49, 75], _length: 120 } };
    const before = window.__relayouts.length;
    hs[0]({ 'xaxis.range[0]': 49, 'xaxis.range[1]': 75 });
    const upd = window.__relayouts[before];
    const st = (upd ? upd.annotations : []).filter((a) => a.meta && a.meta.role === 'segment-text');
    const q2 = st.filter((a) => a.meta.lane === 'q2.z');
    ok(upd && q2.length === 2 && q2[0].meta.operation === 'flux_b' && q2[0].x === 50, 'after a zoom only the visible segments are labelled');
    const qc = st.find((a) => a.meta.lane === 'coupler_q1_q2');
    ok(qc === undefined || qc.x >= 49, 'a segment started before the range is labelled at the left edge');
    ok(st.every((a) => { const [n1, n2] = a.text.split('<br>'); return true; }) &&
       st.filter((a) => a.meta.lane === 'q1.z').every((a) => a.meta.name.length < 'cz_x_pulse_q1_q2'.length && a.x === 63),
       'names re-fitted to the zoomed width');
    hs[0]({ 'yaxis.range[0]': 0 });
    ok(window.__relayouts.length === before + 1, 'a y-only change does not re-fit');
    hs[0]({ width: 300 });
    ok(window.__relayouts.length === before + 2, 'a resize re-fits');
    delete g._fullLayout;
}

// identity colours
{
    const pal = window.PlotTheme.palette();
    ok(GT.laneColor(0) === pal[0] && GT.laneColor(3) === pal[3] && GT.laneColor(pal.length + 1) === pal[1], 'laneColor: palette by index, modulo its length');
    const lineOf = (key, op) => segs.find((s) => s.meta.lane === key && s.meta.operation === op).line.color;
    ok(lineOf('q1.z', 'cz_x_pulse_q1_q2') === pal[0] && lineOf('q2.z', 'flux_b') === pal[1] && lineOf('coupler_q1_q2', 'late') === pal[2],
       'gate lanes without a colour take 0, 1, 2 in order');
    ok(lineOf('coupler_q1_q2', 'bip') === window.PlotTheme.lossColor(), 'a flagged segment keeps the flag colour');
    const chip = title(fig, 'coupler_q1_q2');
    ok(chip.font.color === pal[2] && chip.bgcolor === window.PlotTheme.withAlpha(pal[2], 0.16) && chip.borderpad >= 2 && chip.font.size >= 12 &&
       /^<b>.*<\/b>$/.test(chip.text), 'lane name is a chip in the identity colour (text and tint), also on a flagged lane');
    ok(facts(fig, 'coupler_q1_q2').font.color !== pal[2] && facts(fig, 'coupler_q1_q2').font.size === 11, 'port and limit lines stay quiet');
    const bars = fig.layout.shapes.filter((s) => s.name === 'lane-bar');
    ok(bars.length === 3 && bars.every((b, i) => b.yref === axRef(i) + ' domain' && b.y0 === 0 && b.y1 === 1 && b.xsizemode === 'pixel' &&
       b.x0 === -fig.layout.margin.l && b.x1 - b.x0 <= 4) && bars[1].fillcolor === pal[1], 'a thin bar in the lane colour along each lane at the gutter edge');
    ok(role(fig, 'hover').find((t) => t.meta.lane === 'q2.z').marker.color === pal[1], 'hover row in the lane colour');
    ok(role(fig, 'fill').find((t) => t.meta.lane === 'q1.z').fillcolor === window.PlotTheme.withAlpha(pal[0], 0.18), 'fill in the lane colour');
    // explicit indices, and spectators in one quiet colour
    const hostTb = doc.createElement('div'); doc.body.appendChild(hostTb);
    const many = { lanes: [lane('a.z', 'gate', [seg('p', [0, 10], [0.1, 0.1])], { color: 0 }), lane('b.z', 'gate', [seg('p', [0, 10], [0.1, 0.1])], { color: 3 })]
        .concat([1, 2, 3].map((i) => lane('s' + i + '.z', 'spectator', [seg('p', [0, 10], [0.01, 0.01])]))), notes: [] };
    const hc = GT.render(host, many, { foldSpectators: false, toolbarHost: hostTb });
    const fc = last();
    const col = (key) => role(fc, 'segment').find((t) => t.meta.lane === key).line.color;
    ok(col('a.z') === GT.laneColor(0) && col('b.z') === GT.laneColor(3) && col('a.z') !== col('b.z'), 'lane.color picks the palette index');
    ok(col('s1.z') === col('s2.z') && col('s2.z') === col('s3.z') && ![col('a.z'), col('b.z')].includes(col('s1.z')), 'spectators share one quiet colour');
    ok(title(fc, 's1.z').font.color === col('s1.z') && title(fc, 's1.z').bgcolor === window.PlotTheme.withAlpha(col('s1.z'), 0.1) &&
       title(fc, 'a.z').bgcolor === window.PlotTheme.withAlpha(col('a.z'), 0.16), 'spectator chips: same shape, quiet colour, fainter tint');
    // controls outside the component
    ok(hostTb.querySelector('[data-gate-timeline-bar] .ui-segmented') && !lastRoot().querySelector('[data-gate-timeline-bar]'), 'toolbarHost: the controls render there, nothing above the chart');
    hostTb.querySelector('[data-scale="common"]').click();
    ok(JSON.stringify(last().layout.yaxis.tickvals) !== JSON.stringify(fc.layout.yaxis.tickvals) || last().layout.yaxis.tickvals.length === 2, 'controls in the toolbar host work');
    hc.destroy();
    ok(hostTb.childNodes.length === 0, 'destroy removes the controls from the toolbar host');
    const hn = GT.render(host, many, { toolbar: false });
    ok(!lastRoot().querySelector('[data-gate-timeline-bar]') && lastRoot().querySelector('[data-gate-timeline-plot]'), 'toolbar false: no controls');
    hn.setFold(false);
    ok(hn.laneCount().drawn === 5, 'toolbar false: the setters still work');
    hn.destroy();
    hostTb.remove();
}

// sim
ok(role(fig, 'sim').length === 1 && role(fig, 'sim')[0].yaxis === 'y3' && role(fig, 'sim')[0].line.dash === 'dot', 'sim drawn on its lane, visibly different');
h.setShowSim(false);
fig = last();
ok(role(fig, 'sim').length === 0, 'sim switched off');
h.setShowSim(true);

// fold: the summary sits in the toolbar
const bar = host.querySelector('[data-gate-timeline-bar]');
const plotEl = host.querySelector('[data-gate-timeline-plot]');
ok(bar.querySelector('label.ui-check[data-spectator-summary]').textContent === 'Show 1 spectator line' &&
   bar.querySelector('input[type=checkbox][data-fold]') && !bar.querySelector('[data-fold]').checked && !host.querySelector('button.btn-sm'),
   'spectator switch is the kit checkbox in the toolbar, unchecked when folded');
ok([...bar.children].map((n) => n.matches('[data-scale-label]') ? 'label' : n.matches('.ui-segmented') ? 'scale' : n.matches('[data-spectator-summary]') ? 'spect' : n.querySelector('[data-show-sim]') ? 'sim' : '?').join() === 'label,scale,spect,sim',
   'toolbar order: scale label, scale, spectators, simulation');
ok(bar.querySelector('[data-scale-label]').textContent === 'V scale' && bar.querySelector('[data-scale-label]').classList.contains('ui-note') &&
   [...bar.querySelectorAll('.ui-segmented-opt')].map((b) => b.textContent).join('|') === 'Per lane|Shared',
   'scale control: quiet "V scale" label, options Per lane and Shared');
ok(bar.compareDocumentPosition(plotEl) & 4 && [...plotEl.parentNode.children].slice([...plotEl.parentNode.children].indexOf(plotEl) + 1)
   .every((n) => !n.querySelector('button')), 'below the chart only the notes');
{ const b = host.querySelector('[data-fold]'); b.checked = true; b.dispatchEvent(new window.Event('change')); }
fig = last();
ok(h.laneCount().drawn === 4 && yAxes(fig.layout).length === 4, 'unfold: spectator lane drawn');
const tq3 = segText(fig, 'q3.z')[0];
ok(tq3 && /only part of this waveform was received/.test(tq3.text) && tq3.x === 0, 'truncated text at the segment start');
const nr = fig.layout.shapes.filter((s) => s.name === 'not-received');
ok(nr.length === 1 && nr[0].x0 === 200 && nr[0].x1 === 300 && nr[0].yref === 'y4 domain', 'not-received band from the last sample to end_ns');
ok(fig.layout.annotations.some((a) => a.meta && a.meta.role === 'not-received' && a.text === 'not received'), 'not-received words');
ok(segs.length && role(fig, 'segment').find((t) => t.meta.lane === 'q3.z').x.slice(-1)[0] === 200, 'no line invented past the received samples');
const d1 = fig.layout.yaxis.domain, d4 = fig.layout.yaxis4.domain;
ok((d4[1] - d4[0]) < (d1[1] - d1[0]) * 0.7, 'spectator lane is compact');
ok(JSON.stringify(fig.layout.yaxis4.tickvals) === '[0,0.02]', 'spectator lane ticks: 0 V and its level');
ok(['yaxis', 'yaxis2', 'yaxis3'].every((k) => fig.layout[k].tickvals.length <= 3) && JSON.stringify(fig.layout.yaxis2.tickvals) === '[-0.07,0,0.1]',
   'gate lane: at most 3 ticks (min below 0, 0 V, max)');
ok(yAxes(fig.layout).every((k) => fig.layout[k].showgrid === false) && fig.layout.xaxis.showgrid === true, 'no horizontal grid; time grid kept');
ok(title(fig, 'q3.z').yref === 'y4 domain', 'spectator below the gate lanes');
h.setFold(true);
ok(h.laneCount().drawn === 3, 'fold again');

// notes
const notes = [...host.querySelectorAll('p.ui-note')].map((p) => p.textContent);
ok(DATA.notes.every((n) => notes.includes(n)), 'notes shown verbatim with the kit note part');

// colours come from the theme, never literals in the component
ok(!/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/.test(src), 'no colour literal in gate-timeline.js');
ok(!/\.style\.(?:color|background\w*|fontSize)\s*=/.test(src) && !/style=/.test(src), 'no inline styles');

// destroy
const liveBefore = window.__live;
const purgeBefore = window.__host.purge;
h.destroy();
ok(!host.querySelector('[data-gate-timeline]') && host.childNodes.length === 0, 'destroy removes its nodes');
ok(window.__live <= liveBefore - 3, 'destroy removes its listeners');
ok(window.__host.purge === purgeBefore + 1 && window.__host.unobserve >= 1, 'destroy purges and unobserves');

// no spectators -> no summary
const h0 = GT.render(host, { lanes: DATA.lanes.slice(0, 1), notes: [] });
ok(!host.querySelector('[data-spectator-summary]') && h0.laneCount().drawn === 1, 'no spectator lane: no summary');
ok(!last().layout.xaxis2, 'few lanes: no top time axis');
h0.destroy();

// 16 spectators
const many = { lanes: [DATA.lanes[0]].concat(Array.from({ length: 16 }, (_, i) =>
    lane('q' + (i + 4) + '.z', 'spectator', [seg('park', [0, 50], [0.01 * i, 0.01 * i])]))), notes: [] };
const h16 = GT.render(host, many, {});
fig = last();
ok(yAxes(fig.layout).length === 1 && /16 spectator lines/.test(host.textContent), '16 spectators folded: one summary');
h16.setFold(false);
fig = last();
ya = yAxes(fig.layout);
ok(ya.length === 17, '16 spectators unfolded: 17 lanes');
const plotH = fig.layout.height - fig.layout.margin.t - fig.layout.margin.b;
const lanePx = ya.map((k) => (fig.layout[k].domain[1] - fig.layout[k].domain[0]) * plotH);
ok(lanePx.slice(1).every((p) => Math.abs(p - lanePx[1]) < 0.5 && p >= 50) && lanePx[0] > lanePx[1] &&
   ya.every((k, i) => i === 0 || fig.layout[k].domain[1] < fig.layout[ya[i - 1]].domain[0]),
   '17 lanes keep their fixed heights and do not overlap');
ok(many.lanes.every((l, i) => title(fig, l.key) && title(fig, l.key).yref === axRef(i) + ' domain'), 'every unfolded lane is labelled');
ok(fig.layout.xaxis2 && fig.layout.xaxis2.side === 'top' && fig.layout.xaxis2.matches === 'x' &&
   role(fig, 'top-axis').length === 1 && fig.data.every((t) => t.xaxis === 'x' || t.meta.role === 'top-axis'),
   'many lanes: time ticks at the top too, matched to the one time axis');
h16.destroy();

// cannot_draw and empty
const hc = GT.render(host, { lanes: DATA.lanes, notes: ['note a'], cannot_draw: 'cannot draw: for loop over n' });
const nRenders = window.__renders.length;
const st = host.querySelector('.ui-state--empty');
ok(st && st.textContent === 'cannot draw: for loop over n' && /note a/.test(host.textContent), 'cannot_draw: statement and notes shown');
ok(hc.laneCount().drawn === 0 && !host.querySelector('[data-gate-timeline-plot]'), 'cannot_draw: no lanes drawn');
hc.destroy();
const he = GT.render(host, { lanes: [], notes: [] });
ok(host.querySelector('.ui-state--empty') && window.__renders.length === nRenders, 'empty lanes: empty state, no chart');
he.destroy();
ok(host.childNodes.length === 0, 'host clean at the end');

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL: crash ' + e.stack); process.exit(1); });
