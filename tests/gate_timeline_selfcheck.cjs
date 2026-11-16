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
const host2 = doc.createElement("div"); doc.body.appendChild(host2);
const lastRoot = () => [...doc.querySelectorAll("[data-gate-timeline]")].pop();
const facts = (fig, key) => fig.layout.annotations.find((a) => a.meta && a.meta.role === 'lane-facts' && a.meta.lane === key);
const segText = (fig, key) => fig.layout.annotations.filter((a) => a.meta && a.meta.role === 'segment-text' && a.meta.lane === key);

// The retired tick clause must not return (even behind an always-true branch).
const fitRule = src.slice(src.indexOf('function startsAtFits('), src.indexOf('function onRelayout('));
ok(!/tickvals|ticktext/.test(fitRule) && /Tick labels live in the left gutter/.test(fitRule),
   'tick exclusion is removed with its gutter reason beside the shared fit rule');
ok((src.match(/startsAtFits\(/g) || []).length === 3,
   'one starts-at helper is called by first render and relayout');

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
ok(require('crypto').createHash('sha256').update(JSON.stringify({ data: fig.data, layout: fig.layout })).digest('hex') ===
   'c57fdd52080a3da5f4beb390ab7dd52310c77ab7715b9fc38a9a7f72268a91d8',
   'without steps the complete plotted data and layout remain byte identical');

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
ok(t3.text === '<b>coupler_q1_q2</b>' && t3.font.size === 12 && f3.text === 'con1 / 1 / 13<br>limit -0.25 to 0.25 V<br>(port range less the 0.05 V offset)' && f3.font.size === 11,
   'title lines: label 12 px; port, limit and the offset it already has taken off, at 11 px');
ok(/range not known/.test(facts(fig, 'q2.z').text) && !/offset/.test(facts(fig, 'q1.z').text), 'unknown range said; offset only when not 0');
{   const gutter = fig.layout.margin.l - 52;
    const longest = '(port range less the 0.05 V offset)';
    ok(gutter >= longest.length * 5 && gutter <= longest.length * 7, 'gutter is as wide as the longest title line needs, no wider');
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
{   const all = h.valuesAt(250, { all: true }).find((r) => r.key === 'q3.z');
    ok(all.operation === 'park' && all.v === null && all.missing === 'not received', 'truncated: past the received samples v is null, not received');
}
ok(h.valuesAt(64).length === 3 && h.valuesAt(64).every((r) => r.key !== 'q3.z') && h.valuesAt(64, { all: true }).length === 4,
   'valuesAt lists the lines drawn (as hover does); {all: true} lists every line');
const hov = role(fig, 'hover').find((t) => t.meta.lane === 'q2.z');
ok(hov && hov.text[hov.x.indexOf(40)] === 'idle' && hov.text[hov.x.indexOf(10)] === 'flux_a_long_operation_name: -0.07 V', 'hover text is the valuesAt readout');
ok([...Array(116).keys()].every((t) => hov.x.includes(t)), 'hover grid has every 1 ns from program time 0 over the drawn span');

// the time axis: from program time 0 to the last end plus a small pad, fixed (no autorange)
{
    const xr = fig.layout.xaxis.range;
    ok(xr[0] === 0 && xr[1] > 1000 && xr[1] <= 1000 * 1.04 + 2 && fig.layout.xaxis.autorange === false, 'time axis 0 to the last end plus a pad, no autorange');
    const one = GT.render(host2, { lanes: [lane('q9.z', 'gate', [seg('late', [161, 261], [0.1, 0.1])])], notes: [] });
    const f1 = last();
    ok(f1.layout.xaxis.range[0] === 0 && f1.layout.xaxis.range[1] > 261 && f1.layout.xaxis.range[1] < 280, 'a single late pulse: axis still starts at 0');
    const sa = f1.layout.annotations.filter((a) => a.meta && a.meta.role === 'starts-at');
    ok(sa.length === 1 && sa[0].text === 'starts at 161 ns' && sa[0].xref === 'x' && sa[0].x === 161 && sa[0].xanchor === 'right' && sa[0].xshift < 0 && sa[0].font.size === 11, 'late start: "starts at 161 ns" anchored just before its own bar');
    one.destroy();
    // a bar near the right edge and two lanes starting at the same time: each label ends at its own bar, in its own lane
    const two = GT.render(host2, { lanes: [lane('q8.z', 'gate', [seg('a', [900, 1000], [0.1, 0.1])]),
                                           lane('q9.z', 'gate', [seg('b', [900, 1000], [0.1, 0.1])]),
                                           lane('q7.z', 'gate', [seg('c', [700, 800], [0.1, 0.1])])], notes: [] });
    const f3 = last();
    const sa3 = f3.layout.annotations.filter((a) => a.meta && a.meta.role === 'starts-at');
    ok(sa3.length === 3 && sa3.every((a) => a.xref === 'x' && a.x === (a.meta.lane === 'q7.z' ? 700 : 900) && a.xanchor === 'right' && a.x <= f3.layout.xaxis.range[1]) &&
       new Set(sa3.map((a) => a.yref)).size === 3 && sa3.map((a) => a.meta.lane).join() === 'q8.z,q9.z,q7.z',
       'right-edge bars, same start: one label per lane, each anchored at its bar, in its own lane');
    two.destroy();
    const neg = GT.render(host2, { lanes: [lane('q9.z', 'gate', [seg('early', [-20, 30], [0.1, 0.1])])], notes: [] });
    const f2 = last();
    ok(f2.layout.xaxis.range[0] === -20, 'a negative start extends the axis to it');
    ok(!f2.layout.annotations.some((a) => a.meta && a.meta.role === 'starts-at'), 'no "starts at" text when a lane starts at or before 0');
    neg.destroy();
    // here the axis runs to 1000 ns: starts at 10, 20 and 63 ns leave too little idle room for the words
    ok(fig.layout.annotations.filter((a) => a.meta && a.meta.role === 'starts-at').length === 0,
       '"starts at" only where the idle stretch can hold the words (it would sit on the pulse otherwise)');
}

// the "starts at" fit rule, at its edge: the idle stretch must hold the words plus a 12 px margin
{
    const CHAR = 5.8, MARGIN = 12;
    const draw = (start) => {
        const tl = GT.render(host2, { lanes: [lane('q1.z', 'gate', [seg('long', [0, 2000], [0.1, 0.1])]),
                                              lane('q2.z', 'gate', [seg('late', [start, start + 100], [0.1, 0.1])])], notes: [] });
        const f = last();
        tl.destroy();
        return f;
    };
    const probe = draw(500);
    const xr = probe.layout.xaxis.range;
    const w = Math.max(200, 900 - probe.layout.margin.l - probe.layout.margin.r);
    const px = (start) => (start - xr[0]) * w / (xr[1] - xr[0]);
    const words = (start) => ('starts at ' + start + ' ns').length * CHAR;
    const shown = (start) => draw(start).layout.annotations.some((a) => a.meta && a.meta.role === 'starts-at' && a.meta.lane === 'q2.z');
    let tight = 1; while (px(tight) <= words(tight)) tight++;            // the words alone just fit
    let roomy = tight; while (px(roomy) <= words(roomy) + MARGIN) roomy++; // the words and the margin fit
    ok(tight < roomy && px(tight) <= words(tight) + MARGIN && px(roomy - 1) <= words(roomy - 1) + MARGIN,
       'fit edge: a start exists where the words fit but the margin does not');
    ok(!shown(tight) && !shown(roomy - 1), '"starts at" absent while the idle stretch is short of the words plus 12 px');
    ok(shown(roomy), '"starts at" present once the idle stretch holds the words plus 12 px');
}

// A compact spectator must leave space for its own title (ticks are in the gutter).
{
    const draw = (group, width) => {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: width });
        const hh = GT.render(host2, { lanes: [lane('late', group,
            [seg('a_very_long_pulse_operation_name', [161, 261], [0.05, 0.05])])], notes: [] }, { foldSpectators: false });
        const f = last(); hh.destroy(); return f;
    };
    const wide = draw('gate', 900);
    ok(wide.layout.annotations.some(a => a.meta && a.meta.role === 'starts-at'), 'roomy gate keeps its starts-at label');
    ok(draw('spectator', 900).layout.annotations.some(a => a.meta && a.meta.role === 'starts-at'), 'wide spectator keeps starts-at when title is horizontally clear');
    const compact = draw('spectator', 450);
    ok(!compact.layout.annotations.some(a => a.meta && a.meta.role === 'starts-at'), 'compact title clearance hides starts-at even when words fit');
    const shown = width => draw('spectator', width).layout.annotations.some(a => a.meta && a.meta.role === 'starts-at');
    let lo = 450, hi = 900;
    while (hi - lo > 1) { const mid = Math.floor((lo + hi) / 2); if (shown(mid)) hi = mid; else lo = mid; }
    ok(!shown(hi - 1) && shown(hi), 'title collision edge: one pixel below clearance hides starts-at; at clearance it returns');
    // a narrow host stacks each lane title above its lane, so the plot takes the row
    const stack = draw('gate', 280); // chart body inside a 400 px dialog
    const sTitle = stack.layout.annotations.find(a => a.meta && a.meta.role === 'lane-title');
    const sFacts = stack.layout.annotations.find(a => a.meta && a.meta.role === 'lane-facts');
    ok(stack.layout.margin.l < 80 && 280 - stack.layout.margin.l - stack.layout.margin.r >= 200, 'narrow host: the plot keeps at least 200 px');
    ok(sTitle.y === 1 && sTitle.yanchor === 'bottom' && sFacts.y === 1 && sFacts.yanchor === 'bottom' && sTitle.yshift > sFacts.yshift, 'narrow host: title and facts sit above the lane');
    ok(stack.layout.xaxis.nticks >= 2 && stack.layout.xaxis.nticks <= Math.floor(209 / 70), 'narrow host: at most one time tick per 70 px');
    ok(wide.layout.xaxis.nticks === undefined && wide.layout.annotations.find(a => a.meta && a.meta.role === 'lane-title').yanchor === 'bottom' && wide.layout.margin.l > 80, 'wide host keeps the titles beside the lanes');
    const narrow = draw('gate', 130); // a plot too short for the words even when stacked
    ok(!narrow.layout.annotations.some(a => a.meta && a.meta.role === 'starts-at'), 'actual plot width decides the starts-at fit');
    const text = segText(narrow, 'late')[0];
    ok(text.meta.name.includes('\u2026') && text.hovertext === 'a_very_long_pulse_operation_name', 'short pulse title exposes the full name on hover');
    delete host2.clientWidth;
}

// Both layout paths use the same fit rule; hidden candidates return when room returns.
{
    const hasStart = f => f.layout.annotations.some(a => a.meta && a.meta.role === 'starts-at');
    for (const group of ['spectator', 'gate']) {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 450 });
        const hh = GT.render(host2, { lanes: [lane('late', group,
            [seg('a_very_long_pulse_operation_name', [161, 261], [0.05, 0.05])])], notes: [] }, { foldSpectators: false });
        const f = hh.figure(), plot = last().el;
        const resize = width => {
            plot._fullLayout = { xaxis: { range: f.span.slice(), _length: width - f.layout.margin.l - f.layout.margin.r } };
            hh.relayout({ width });
        };
        ok(hasStart(f) === (group === 'gate'), group + ': initial compact width');
        resize(450);
        ok(hasStart(f) === (group === 'gate'), group + ': relayout compact title overlap respects vertical clearance');
        resize(900);
        ok(hasStart(f), group + ': resize makes room and restores the label');
        resize(450);
        ok(hasStart(f) === (group === 'gate'), group + ': narrowing hides only the compact lane label');
        plot._fullLayout = { xaxis: { range: [155, 275], _length: 250 } };
        hh.relayout({ 'xaxis.range': [155, 275] });
        ok(!hasStart(f), group + ': pan leaves too little idle space');
        resize(900);
        ok(hasStart(f), group + ': pan back restores the label');
        hh.destroy();
    }
    delete host2.clientWidth;
}

// Only the other lane's title crosses the idle label's horizontal box.
{
    const hh = GT.render(host2, { lanes: [
        lane('other', 'gate', [seg('a_very_long_pulse_operation_name', [0, 1000], [0.05, 0.05])]),
        lane('late', 'spectator', [seg('p', [400, 500], [0.05, 0.05])])
    ], notes: [] }, { foldSpectators: false });
    const f = hh.figure();
    const check = label => {
        const a = f.layout.annotations.find(a => a.meta && a.meta.role === 'starts-at' && a.meta.lane === 'late');
        ok(!!a, label + ': another lane cannot hide starts-at');
        const right = (400 - f.span[0]) * f.widthPx / (f.span[1] - f.span[0]) - 4;
        const left = right - 'starts at 400 ns'.length * 5.8;
        const overlaps = t => {
            const x = (t.x - f.span[0]) * f.widthPx / (f.span[1] - f.span[0]);
            const width = Math.max(t.meta.name.length, t.meta.shown2.length) * 5.8;
            const tl = t.xanchor === 'right' ? x - width : x;
            return right > tl && left < tl + width;
        };
        ok(segText(f, 'other').some(overlaps) && !segText(f, 'late').some(overlaps), label + ': only other title overlaps horizontally');
    };
    check('first render');
    last().el._fullLayout = { xaxis: { range: f.span.slice(), _length: f.widthPx } };
    hh.relayout({ 'xaxis.range': f.span.slice() });
    check('relayout');
    hh.destroy();
}

// The shared "starts at" fit rule, three edges the other pins cannot reach. The constants are read
// from the module so the fixtures follow the code.
{
    const konst = (name) => +new RegExp('var ' + name + ' = ([0-9.]+);').exec(src)[1];
    const CHAR = konst('CHAR_PX'), BAND = konst('BAND_PX'), TEXT = konst('TEXT_PX');
    const startsAt = (f, key) => f.layout.annotations.find((a) => a.meta && a.meta.role === 'starts-at' && a.meta.lane === key);
    const spanOf = (f) => ({ xr: f.span.slice(), w: f.widthPx });
    const pxOf = (t, xr, w) => (t - xr[0]) * w / (xr[1] - xr[0]);
    const clearOf = (a, t, fy) => (t.meta.row === 'top' ? 1 - fy : fy) * a.meta.lanePx > BAND + TEXT / 2 + 12;
    const overlapsWith = (a, t, xr, w, widthOf) => {
        const right = pxOf(a.x, xr, w) - 4, left = right - a.text.length * CHAR;
        const x = pxOf(t.x, xr, w), width = widthOf(t) * CHAR;
        const titleLeft = t.xanchor === 'right' ? x - width : x;
        return right > titleLeft && left < titleLeft + width;
    };
    const full = (t) => Math.max(t.meta.name.length, t.meta.shown2.length);
    const nameOnly = (t) => t.meta.name.length;
    const relayoutTo = (hh, f, xr, w) => {
        last().el._fullLayout = { xaxis: { range: xr.slice(), _length: w } };
        hh.relayout({ 'xaxis.range': xr.slice() });
    };
    const flagged = { flags: ['over_range', 'bad_length'], truncated: true };
    // mixed-sign data keeps 0 V at mid height: the spots are 0.35 and 0.62, the middle one is blocked
    const build = (segs) => GT.render(host2, { lanes: [lane('late', 'gate', segs, { limit_v: null })], notes: [] }, {});

    // (1) the first spot fails clearance against a title of the lane, a later spot passes
    {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 900 });
        const hh = build([seg('a', [400, 500], [-0.1, 0.1]), seg('b', [520, 540], [0.05, 0.05], flagged)]);
        const f = hh.figure();
        let a = startsAt(f, 'late');
        const { xr, w } = spanOf(f);
        const spots = a.meta.spots;
        const texts = segText(f, 'late');
        const bottom = texts.find((t) => t.meta.row === 'bottom');
        ok(spots.length === 2 && spots[0] < spots[1] && overlapsWith(a, bottom, xr, w, full) &&
           !clearOf(a, bottom, spots[0]) && clearOf(a, bottom, spots[1]) &&
           texts.filter((t) => t.meta.row === 'top').every((t) => !overlapsWith(a, t, xr, w, full)),
           'later spot: fixture has a bottom title over the label, the first spot too close, the second clear');
        ok(a && a.y === spots[1], 'later spot: first render keeps the label at the later spot');
        // room returns: the title clears the label horizontally and the first spot is used again
        relayoutTo(hh, f, xr, w * 4);
        a = startsAt(f, 'late');
        ok(a && a.y === spots[0], 'later spot: with the title clear of the label the first spot returns');
        relayoutTo(hh, f, xr, w);
        a = startsAt(f, 'late');
        ok(a && a.y === spots[1], 'later spot: relayout back to the crowded width moves it to the later spot again');
        hh.destroy();
        // counter-examples: no title in the lane keeps the first spot; another lane's title does not move it
        const solo = build([seg('a', [400, 500], [-0.1, 0.1])]);
        ok(startsAt(solo.figure(), 'late').y === startsAt(solo.figure(), 'late').meta.spots[0], 'later spot: a lane whose titles are clear keeps the first spot');
        solo.destroy();
    }

    // (2) the overlap exists only through the second title line
    {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 900 });
        const hh = build([seg('a', [400, 410], [-0.1, 0.1], flagged), seg('b', [420, 440], [0.05, 0.05], flagged)]);
        const f = hh.figure();
        const { xr, w } = spanOf(f);
        const probe = startsAt(hh.figure(), 'late');
        const texts = segText(f, 'late');
        ok(!probe, 'second line: label hidden when a title overlaps it only through its second line');
        // the same fixture drawn wide enough for no overlap shows it, so the fixture is not hiding it for another reason
        const aShown = (() => {
            const g = build([seg('a', [400, 410], [-0.1, 0.1], flagged), seg('b', [420, 440], [0.05, 0.05], flagged)]);
            const fg = g.figure();
            relayoutTo(g, fg, fg.span, fg.widthPx * 30);
            const r = startsAt(fg, 'late');
            g.destroy();
            return !!r;
        })();
        ok(aShown, 'second line: the same lane shows the label once the titles are clear of it');
        const fa = hh.figure().fixedAnnotations.find((x) => x.meta && x.meta.role === 'starts-at');
        ok(texts.length === 2 && texts.every((t) => t.meta.shown2.length > t.meta.name.length) &&
           texts.some((t) => overlapsWith(fa, t, xr, w, full)) && texts.every((t) => !overlapsWith(fa, t, xr, w, nameOnly)),
           'second line: every title name is shorter than its second line and no name alone reaches the label');
        hh.destroy();
    }

    // (3) the exact left-edge boundary: label left edge at the limit hides, one pixel further shows
    {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 900 });
        const hh = build([seg('p', [128, 228], [0.05, 0.05])]);
        const f = hh.figure();
        const words = 'starts at 128 ns'.length * CHAR;
        // plot half width p: right = p - 4, left = p - 4 - words; walk p until left is exactly 8
        let p = 12 + words;
        const step = Math.pow(2, -40);
        for (let n = 0; n < 4000 && (p - 4) - words > 8; n++) p -= step;
        for (let n = 0; n < 4000 && (p - 4) - words < 8; n++) p += step;
        ok((p - 4) - words === 8, 'left edge: a start pixel exists that puts the label edge exactly at the limit');
        const has = (w) => { relayoutTo(hh, f, [0, 256], w); return !!startsAt(f, 'late'); };
        ok(has(2 * p + 2), 'left edge: one pixel further than the limit shows the label');
        ok(!has(2 * p), 'left edge: exactly at the limit hides the label');
        ok(has(2 * p + 2), 'left edge: and it returns one pixel further');
        hh.destroy();
    }
    delete host2.clientWidth;
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
const segBtns = [...host.querySelectorAll('[aria-label="Vertical scale"] .ui-segmented-opt')];
ok(segBtns.length === 2 && segBtns[1].classList.contains('ui-segmented-on') && segBtns[1].getAttribute('aria-pressed') === 'true', 'segmented control shows common');
ok(host.querySelector('[aria-label="Vertical scale"]').getAttribute('role') !== 'group' && host.querySelector('[aria-label="Vertical scale"]').getAttribute('aria-label') === 'Vertical scale',
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
    const q1t = st.filter((a) => a.meta.lane === 'q1.z');
    ok(q1t.length === 1 && q1t[0].xanchor === 'right' && q1t[0].x === 75 && q1t[0].text.split('<br>')[1] === 'max 0.1 V, 52 ns',
       'zoomed: a label whose numbers do not fit to the right is placed left of the visible end, numbers whole');
    ok(st.every((a) => a.meta.shown2 === a.meta.line2), 'a number is never cut');
    hs[0]({ 'yaxis.range[0]': 0 });
    ok(window.__relayouts.length === before + 1, 'a y-only change does not re-fit');
    hs[0]({ width: 300 });
    ok(window.__relayouts.length === before + 2, 'a resize re-fits');
    delete g._fullLayout;
}

// identity colours
{
    const P = window.PlotTheme.palette();
    // without the kit's --ui-line-N colours: the plot palette in the order 1, 4, 6, 3, 5, 2
    const pal = [0, 3, 5, 2, 4, 1].map((k) => P[k]);
    ok(GT.laneColor(0) === P[0] && GT.laneColor(1) === P[3] && GT.laneColor(2) === P[5] && GT.laneColor(3) === P[2] && GT.laneColor(7) === P[3],
       'laneColor fallback: palette order 1, 4, 6, 3, 5, 2, modulo six');
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
    const hc = GT.render(host2, many, { foldSpectators: false, toolbarHost: hostTb });
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
    const hn = GT.render(host2, many, { toolbar: false });
    ok(!lastRoot().querySelector('[data-gate-timeline-bar]') && lastRoot().querySelector('[data-gate-timeline-plot]'), 'toolbar false: no controls');
    hn.setFold(false);
    ok(hn.laneCount().drawn === 5, 'toolbar false: the setters still work');
    hn.destroy();
    hostTb.remove();
}

// overlay view: every line on one time axis
{
    const sq = (op, s, e, v) => seg(op, [s, e], [v, v]);
    const L = (key, segs, extra) => lane(key, 'gate', segs, extra);
    const OV = { lanes: [L('coupler_q1_q2', [sq('cp', 0, 100, 0.1)], { color: 3 }), L('q1.z', [sq('fx', 161, 261, 0.01)], { color: 0 }),
                         lane('s1.z', 'spectator', [sq('sp', 161, 201, -0.02)]), lane('s2.z', 'spectator', [sq('sp', 161, 205, 0.03)])],
                 notes: ['n'] };
    const ho = GT.render(host2, OV, { view: 'overlay' });
    let f = last();
    const r = lastRoot();
    ok(xAxes(f.layout).length === 1 && yAxes(f.layout).length === 1 && f.data.every((t) => t.xaxis === 'x' && t.yaxis === 'y'), 'overlay: one time axis and one V axis');
    const segO = role(f, 'segment'), fillO = role(f, 'fill');
    ok(segO.find((t) => t.meta.lane === 'q1.z').line.color === GT.laneColor(0) && segO.find((t) => t.meta.lane === 'coupler_q1_q2').line.color === GT.laneColor(3) &&
       fillO.length === 2 && fillO.every((t) => t.fill === 'tozeroy'), 'overlay: each gate line in its identity colour, filled');
    ok(JSON.stringify(segO.find((t) => t.meta.lane === 'q1.z').x) === '[161,261]' && JSON.stringify(segO.find((t) => t.meta.lane === 'q1.z').y) === '[0.01,0.01]', 'overlay: real samples in Real V');
    ok(f.layout.xaxis.range[0] === 0 && f.layout.xaxis.autorange === false, 'overlay: time axis from 0');
    ok(JSON.stringify(ho.timing()) === JSON.stringify([{ a: 'coupler_q1_q2', b: 'q1.z', kind: 'gap', ns: 61, from_ns: 100, to_ns: 161, intervals: [[100, 161]] }]), 'timing: gap 61 ns from the data');
    const br = f.layout.annotations.filter((a) => a.meta && a.meta.role === 'bracket-label');
    ok(br.length === 1 && /gap 61 ns/.test(br[0].text) && br[0].font.color !== f.layout.annotations.find((a) => a.meta && a.meta.role === 'guide-label').font.color,
       'gap bracket labelled, in the warn colour');
    const guides = f.layout.shapes.filter((s) => s.name === 'guide').map((s) => s.x0);
    ok(JSON.stringify(guides) === '[0,100,161,261]', 'guides at every gate edge');
    const gtxt = f.layout.annotations.filter((a) => a.meta && a.meta.role === 'guide-label').map((a) => a.text);
    ok(['0', '100', '161', '261'].every((t) => gtxt.includes(t)), 'guide times written at the top');
    ok(ho.laneCount().drawn === 2 && role(f, 'segment').length === 2, 'spectators folded: not drawn');
    // legend: the colour key in the chart (not interactive), the controls in the DOM
    ok(!f.layout.annotations.some((a) => a.meta && a.meta.role === 'legend-chip'), 'one legend only: no chip row inside the chart');
    const leg = r.querySelector('[data-gate-timeline-legend]');
    const btns = [...leg.querySelectorAll('button[data-legend]')];
    ok(btns.length === 3 && btns.every((b) => b.type === 'button' && b.getAttribute('aria-pressed') === 'true') && btns[0].childNodes[1].textContent === 'coupler_q1_q2' &&
       btns[2].hidden, 'legend: a kit button per line labelled with the lane label, pressed = shown (Space and Enter are native)');
    ok(btns[0].querySelector('.ui-line-dot.ui-line-4') && btns[1].querySelector('.ui-line-dot.ui-line-1') && !btns[2].querySelector('.ui-line-dot'),
       'each line button carries its colour dot (ui-line-N of its colour index)');
    const ltext = [...leg.querySelectorAll('.ui-note')].map((n) => n.textContent);
    ok(ltext.includes('level 0.01 V, 100 ns, starts 161 ns') && ltext.includes('level 0.1 V, 100 ns, starts 0 ns'), 'legend text: level, length, start');
    btns[1].focus();
    btns[1].click();
    f = last();
    ok(doc.activeElement === btns[1] && btns[1].getAttribute('aria-pressed') === 'false', 'toggling keeps the focus on the button');
    ok(ho.timing().length === 0 && f.layout.shapes.filter((s) => s.name === 'guide').map((s) => s.x0).join() === '0,100' &&
       f.layout.annotations.filter((a) => a.meta && a.meta.role === 'bracket-label').length === 0 && role(f, 'segment').every((t) => t.meta.lane !== 'q1.z'),
       'a hidden line leaves the plot, its guides and its bracket');
    ok(btns[1].querySelector('.ui-note').textContent === 'hidden', 'the button of a hidden line says "hidden"');
    ok(btns[1].classList.contains('ui-legend-btn--off') && btns[1].querySelector('.ui-line-dot').classList.contains('ui-line-dot--off') &&
       !btns[0].classList.contains('ui-legend-btn--off') && !btns[0].querySelector('.ui-line-dot').classList.contains('ui-line-dot--off'),
       'the button of a hidden line and its dot look off; a shown one does not');
    ok(!ho.valuesAt(200).some((v) => v.key === 'q1.z') && ho.valuesAt(200, { all: true }).some((v) => v.key === 'q1.z'), 'valuesAt leaves out a hidden line');
    ho.setLineHidden('q1.z', false);
    // Same height: each line scaled to its own largest value; no volt numbers
    ho.setHeight('same');
    f = last();
    const mx = (key) => Math.max(...role(f, 'segment').find((t) => t.meta.lane === key).y.map(Math.abs));
    ok(mx('q1.z') === 1 && mx('coupler_q1_q2') === 1, 'Same height: 0.01 V and 0.1 V lines equally tall');
    ok(f.layout.yaxis.showticklabels === false && /normalised/.test(f.layout.yaxis.title.text), 'Same height: the V axis says normalised, no volts');
    ok([...r.querySelectorAll('[data-gate-timeline-legend] .ui-note')].some((a) => a.textContent.startsWith('level 0.01 V')), 'Same height: real level still in the legend');
    ok(role(f, 'hover').find((t) => t.meta.lane === 'q1.z').text[role(f, 'hover').find((t) => t.meta.lane === 'q1.z').x.indexOf(200)] === 'fx: 0.01 V', 'hover reads real volts');
    ok(f.layout.shapes.every((s) => s.name !== 'limit'), 'Same height: no limit lines');
    ho.setHeight('real');
    ok(last().layout.yaxis.ticksuffix === ' V' && last().layout.yaxis.showticklabels === true, 'Real V: the overlay ticks carry " V"');
    // toolbar: view first; each view shows its own scale control
    const bar = r.querySelector('[data-gate-timeline-bar]');
    ok(bar.firstElementChild.textContent === 'View' && [...bar.querySelectorAll('[aria-label="View"] .ui-segmented-opt')].map((b) => b.textContent).join() === 'Lanes,Overlay',
       'View control first: Lanes | Overlay');
    ok(bar.querySelector('[aria-label="Vertical scale"]').hidden && !bar.querySelector('[aria-label="Line height"]').hidden &&
       [...bar.querySelectorAll('[aria-label="Line height"] .ui-segmented-opt')].map((b) => b.textContent).join() === 'Real V,Same height', 'overlay: Height control replaces V scale');
    // spectators shown: thin quiet lines, no fill, one chip
    ho.setFold(false);
    f = last();
    const sp = role(f, 'segment').filter((t) => t.meta.lane.startsWith('s'));
    ok(sp.length === 2 && sp.every((t) => t.line.width === 1 && t.line.color === sp[0].line.color) && !role(f, 'fill').some((t) => t.meta.lane.startsWith('s')),
       'spectators: thin quiet lines without fill');
    ok([...r.querySelectorAll('[data-gate-timeline-legend] button[data-legend]')].filter((b) => !b.hidden).map((b) => b.childNodes[b.childNodes.length - 2].textContent).join() === 'coupler_q1_q2,q1.z,2 spectators',
       'one legend button for the spectator group');
    ok(f.layout.shapes.filter((s) => s.name === 'guide').length === 4, 'spectator edges get no guides');
    // back to lanes, unchanged
    bar.querySelector('[data-view="lanes"]').click();
    ok(yAxes(last().layout).length === 4 && !bar.querySelector('[aria-label="Vertical scale"]').hidden &&
       bar.querySelector('[aria-label="Line height"]').hidden && bar.querySelector('[data-height-label]').hidden, 'Lanes view comes back with its own scale control only');
    ho.destroy();

    // overlap, meet, three lines and more than three
    const tm = (lanes) => { const h2 = GT.render(host2, { lanes, notes: [] }, { view: 'overlay' }); const t = h2.timing(); const fig2 = last(); const note = !!lastRoot().querySelector('[data-overlay-note]'); h2.destroy(); return { t, fig2, note }; };
    let x = tm([L('a', [sq('p', 0, 100, 0.1)]), L('b', [sq('p', 40, 140, 0.1)])]);
    ok(JSON.stringify(x.t) === JSON.stringify([{ a: 'a', b: 'b', kind: 'overlap', ns: 60, from_ns: 40, to_ns: 100, intervals: [[40, 100]] }]), 'timing: overlap 60 ns');
    ok(x.fig2.layout.annotations.some((a) => a.meta && a.meta.role === 'bracket-label' && /overlap 60 ns/.test(a.text)), 'overlap bracket labelled');
    x = tm([L('a', [sq('p', 0, 100, 0.1)]), L('b', [sq('p', 100, 140, 0.1)])]);
    ok(x.t.length === 1 && x.t[0].kind === 'meet' && x.t[0].ns === 0 && x.t[0].from_ns === 100, 'timing: touching edges meet, neither gap nor overlap');
    x = tm([L('a', [sq('p', 0, 10, 0.1)]), L('b', [sq('p', 20, 30, 0.1)]), L('c', [sq('p', 25, 50, 0.1)])]);
    ok(x.t.length === 3 && x.t.map((p) => p.a + p.b + p.kind + p.ns).join() === 'bcoverlap5,abgap10,acgap15' && !x.note, 'three lines: every pair, overlaps first');
    // a line is the union of its segments: its own idle time is not playing
    x = tm([L('a', [sq('p', 0, 10, 0.1), sq('p', 30, 40, 0.1)]), L('b', [sq('p', 15, 20, 0.1)])]);
    ok(JSON.stringify(x.t) === JSON.stringify([{ a: 'a', b: 'b', kind: 'gap', ns: 5, from_ns: 10, to_ns: 15, intervals: [[10, 15]] }]),
       'union: a line inside the idle time of the other is a gap (5 ns, 10 to 15), not an overlap');
    x = tm([L('a', [sq('p', 0, 10, 0.1), sq('p', 30, 40, 0.1)]), L('b', [sq('p', 5, 35, 0.1)])]);
    ok(x.t.length === 1 && x.t[0].kind === 'overlap' && x.t[0].ns === 10 && JSON.stringify(x.t[0].intervals) === '[[5,10],[30,35]]' &&
       x.fig2.layout.shapes.filter((s2) => s2.name === 'bracket').length === 2, 'union: overlap 10 ns in two brackets');
    ok(x.fig2.layout.annotations.some((a) => a.meta && a.meta.role === 'bracket-label' && /\(10 ns in all\)/.test(a.text)), 'a split overlap states its total');
    // every pair with four lines; a gap bracket only for true silence
    x = tm([L('a', [sq('p', 0, 100, 0.1)]), L('b', [sq('p', 1, 2, 0.1)]), L('c', [sq('p', 3, 4, 0.1)]), L('d', [sq('p', 200, 210, 0.1)])]);
    ok(x.t.length === 6 && x.t.map((p) => p.a + p.b + p.kind).join() === 'aboverlap,acoverlap,bcgap,bdgap,cdgap,adgap' && !x.note,
       'four lines: all six pairs, overlaps first, then gaps in time order, no neighbours-only note');
    const gaps = x.fig2.layout.shapes.filter((s2) => s2.name === 'bracket' && s2.meta.kind === 'gap');
    ok(gaps.length === 1 && gaps[0].meta.from === 100 && gaps[0].meta.to === 200, 'only the gap of true silence (100 to 200) gets a bracket');
    // tolerance
    x = tm([L('a', [sq('p', 0, 40.000000001, 0.1)]), L('b', [sq('p', 40, 80, 0.1)])]);
    ok(x.t.length === 1 && x.t[0].kind === 'meet', 'times within 1e-6 ns are equal: meet, not a tiny overlap');
    // close edges: labels stagger, then share one label
    x = tm([L('a', [sq('p', 0, 100, 0.1)]), L('b', [sq('p', 101, 102, 0.1)]), L('c', [sq('p', 103, 300, 0.1)])]);
    const gl = x.fig2.layout.annotations.filter((a) => a.meta && a.meta.role === 'guide-label');
    const shown = gl.flatMap((a) => a.meta.times);
    ok([0, 100, 101, 102, 103, 300].every((t) => shown.includes(t)), 'every edge time is written');
    ok(gl.some((a) => a.meta.row === 1) && gl.some((a) => / \/ /.test(a.text)), 'close edges: a second row, then a shared label');
}

// state tones and kit line colours (the kit tokens set here as test values)
{
    const st = doc.createElement('style');
    st.textContent = ':root { --ui-line-1: rgb(1,1,1); --ui-line-2: rgb(2,2,2); --ui-line-3: rgb(3,3,3); ' +
        '--ui-danger-text: rgb(200,0,0); --ui-danger-bg: rgb(250,220,220); --ui-warn-text: rgb(150,100,0); --ui-warn-bg: rgb(250,240,200); }';
    doc.head.appendChild(st);
    const nameOf = (D, key) => (D.lanes.find((l) => l.key === key) || {}).label || key;
    ok(GT.laneColor(0) === 'rgb(1,1,1)' && GT.laneColor(2) === 'rgb(3,3,3)' && GT.laneColor(3) === window.PlotTheme.palette()[2] && GT.laneColor(6) === 'rgb(1,1,1)', 'identity colours come from the kit line colours, slot by slot, six slots');
    const sq = (op, s, e, v, extra) => seg(op, [s, e], [v, v], extra);
    const D = { lanes: [
        lane('coupler_q1_q2', 'gate', [sq('big', 0, 50, 0.62, { flags: ['over_range'] })], { color: 0 }),
        lane('q1.z', 'gate', [sq('odd', 60, 90, 0.1, { flags: ['something_new'] })], { color: 1 }),
        lane('q2.z', 'gate', [Object.assign(sq('long', 100, 300, 0.1), { t_ns: [100, 150], v: [0.1, 0.1], truncated: true })], { color: 2 })],
        notes: [] };
    const hs = GT.render(host2, D, {});
    let f = last();
    const segOf = (fig, key) => role(fig, 'segment').find((t) => t.meta.lane === key);
    const fillOf = (fig, key) => role(fig, 'fill').find((t) => t.meta.lane === key);
    ok(segOf(f, 'coupler_q1_q2').line.color === 'rgb(200,0,0)' && fillOf(f, 'coupler_q1_q2').fillcolor === 'rgb(250,220,220)',
       'a refusing flag draws in the danger tone (line and fill)');
    ok(segText(f, 'coupler_q1_q2')[0].font.color === 'rgb(200,0,0)', 'its label in the danger tone');
    ok(title(f, 'coupler_q1_q2').font.color === 'rgb(1,1,1)', 'the lane chip keeps its identity colour');
    ok(segOf(f, 'q1.z').line.color === 'rgb(150,100,0)' && fillOf(f, 'q1.z').fillcolor === 'rgb(250,240,200)', 'an unknown flag only informs: warn tone');
    ok(segOf(f, 'q2.z').line.color === 'rgb(150,100,0)', 'a truncated segment informs: warn tone');
    const ll = f.layout.annotations.filter((a) => a.meta && a.meta.role === 'limit-label');
    ok(ll.length === 1 && ll[0].text === 'coupler_q1_q2 limit 0.5 V' && ll[0].font.color === 'rgb(200,0,0)' && ll[0].x === 1 && ll[0].xanchor === 'right',
       'lanes: an exceeded limit is labelled at its right end in the danger tone');
    hs.setView('overlay');
    f = last();
    const lim = f.layout.shapes.filter((s) => s.name === 'limit');
    ok(lim.length === 1 && lim[0].y0 === 0.5 && lim[0].line.color === 'rgb(200,0,0)' && lim[0].meta.lane === 'coupler_q1_q2',
       'overlay: a limit only for the line that exceeds it, in the danger tone');
    ok(f.layout.annotations.some((a) => a.meta && a.meta.role === 'limit-label' && a.text === 'coupler_q1_q2 limit 0.5 V'), 'overlay: the limit is labelled');
    hs.setHeight('same');
    ok(!last().layout.shapes.some((x2) => x2.name === 'limit'), 'Same height: no limit line even for an exceeded limit');
    hs.setHeight('real');
    f = last();
    // the list: every pair, in timing() order; two drawn brackets are labelled in the chart
    ok(hs.timing().length === 3 && f.layout.shapes.filter((x2) => x2.name === 'bracket').length === 2 &&
       f.layout.annotations.filter((a) => a.meta && a.meta.role === 'bracket-label').length === 2, 'two drawn brackets (the third gap is not silent) carry their text');
    const rows = [...lastRoot().querySelectorAll('[data-gate-timeline-pairs] [data-pair]')];
    ok(rows.length === 3 && rows.map((r) => r.textContent).join('|') === hs.timing().map((p) =>
        nameOf(D, p.a) + 'to' + nameOf(D, p.b) + (p.kind === 'gap' ? 'gap ' + p.ns + ' ns' : p.kind === 'overlap' ? 'overlap ' + p.ns + ' ns' : 'meet') + p.from_ns + ' to ' + p.to_ns + ' ns').join('|'),
       'the pair list is timing(), one row per pair');
    ok(rows[0].querySelector('.ui-badge--warn') && rows.every((r) => r.getAttribute('tabindex') === '0'), 'gap rows in the warn tone; rows focusable');
    rows[0].dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    f = last();
    const br = f.layout.shapes.filter((x2) => x2.name === 'bracket');
    const p0 = hs.timing()[0];
    ok(br.filter((x2) => x2.meta.lit).length === 1 && br.find((x2) => x2.meta.lit).meta.index === 0 &&
       f.layout.shapes.filter((x2) => x2.name === 'guide' && x2.meta.lit).map((x2) => x2.x0).sort((a, b) => a - b).join() === [p0.from_ns, p0.to_ns].join(),
       'hovering a row highlights exactly its bracket and its two guides');
    rows[0].dispatchEvent(new window.MouseEvent('mouseout', { bubbles: true }));
    ok(!last().layout.shapes.some((x2) => x2.meta && x2.meta.lit), 'leaving the row clears the highlight');
    hs.setView('lanes');
    ok(lastRoot().querySelectorAll('[data-gate-timeline-pairs] [data-pair]').length === 3, 'the Lanes view shows the same list');
    const aria = lastRoot().querySelector('[data-gate-timeline-plot]');
    ok(aria.getAttribute('role') === 'img' && /coupler_q1_q2 plays 0 to 50 ns at 0\.62 V/.test(aria.getAttribute('aria-label')) &&
       /gap 10 ns between coupler_q1_q2 and q1\.z/.test(aria.getAttribute('aria-label')), 'the plot has a text alternative with the lines and the timing');
    hs.destroy();
    st.remove();
}

// robustness: empty handles, limits, duplicate keys, lengths, one sample, missing values, names
{
    const sq = (op, s, e, v, extra) => seg(op, [s, e], [v, v], extra);
    const he = GT.render(host2, { lanes: [], notes: [], cannot_draw: 'cannot draw: x' });
    let threw = false;
    try { he.setView('overlay'); he.setHeight('same'); he.setLineHidden('a', true); he.relayout({}); he.readout(1); he.figure(); } catch (e) { threw = true; }
    ok(!threw && JSON.stringify(he.timing()) === '[]' && he.valuesAt(1).length === 0, 'an empty picture has every handle method; timing() is []');
    he.destroy();
    const h1 = GT.render(host2, { lanes: [lane('q1.z', 'gate', [sq('p', 0, 10, 0.1)], { limit_v: 0.5 }), lane('q2.z', 'gate', [sq('p', 0, 10, 0.1)], { limit_v: [0.4, -0.3] }),
                                          lane('c.z', 'gate', [sq('p', 0, 10, 0.1)], { limit_v: 'wide' })], notes: [] });
    let f = last();
    ok(/limit -0\.5 to 0\.5 V/.test(facts(f, 'q1.z').text) && /limit -0\.3 to 0\.4 V/.test(facts(f, 'q2.z').text) && /range not known/.test(facts(f, 'c.z').text),
       'limit_v: one number r is [-r, r]; a reversed pair is ordered; anything else is unknown');
    h1.destroy();
    const before = host2.childNodes.length;
    const hb = GT.render(host2, { lanes: [{ key: 'bad', group: 'gate', segments: [{ operation: 'p', start_ns: 0, end_ns: 10, t_ns: 5, v: 'x' }] }], notes: ['n'] });
    ok(host2.querySelectorAll('[data-gate-timeline]').length === host2.childNodes.length && host2.childNodes.length === before + 1 &&
       /^cannot draw: /.test(lastRoot().querySelector('.ui-state--empty').textContent) && typeof hb.setView === 'function',
       'an exception while drawing leaves no half-built root, only the cannot-draw line with the reason');
    hb.destroy();
    const hd = GT.render(host2, { lanes: [lane('q1.z', 'gate', [sq('p', 0, 10, 0.1)]), lane('q1.z', 'gate', [sq('p', 20, 30, 0.1)])], notes: [] });
    ok(lastRoot().querySelector('.ui-state--empty').textContent === 'cannot draw: two lines share the key q1.z' && hd.laneCount().drawn === 0, 'duplicate keys are refused with the reason');
    hd.destroy();
    // lengths, one sample, short v, null inside, back-to-back edge, escaping
    const odd = 'q1 <b>&</b> %{x}';
    const L2 = { lanes: [
        lane('a', 'gate', [sq('p', 0, 100, 0.1, { length_ns: 96 }), sq('q', 100, 140, 0.2)], { label: odd }),
        lane('b', 'gate', [seg('one', [50], [0.1]), seg('short', [60, 61, 62, 70], [0.1, 0.2]), seg('hole', [80, 81, 90], [0.1, null, 0.1])])], notes: [] };
    const h2 = GT.render(host2, L2, {});
    f = last();
    const ta = segText(f, 'a');
    ok(ta[0].meta.line2 === 'level 0.1 V, config length 96 ns, drawn 100 ns' && ta[1].meta.line2 === 'level 0.2 V, 40 ns',
       'the label length is the drawn width; a different configured length is said');
    const tb = segText(f, 'b');
    ok(tb[0].meta.line2 === '1 sample at 50 ns, 0.1 V' && role(f, 'segment').find((t) => t.meta.operation === 'one').mode === 'markers',
       'a one-sample segment is a marker labelled "1 sample at N ns"');
    ok(/samples end at 61 ns/.test(tb[1].meta.line2), 'v shorter than t_ns is said in the label');
    ok(h2.readout(65).find((r) => r.key === 'b').text === 'short: no value' && h2.readout(81).find((r) => r.key === 'b').text === 'hole: no value',
       'a missing value inside a received segment reads "no value"');
    ok(h2.valuesAt(100).find((r) => r.key === 'a').operation === 'q', 'the shared edge of two back-to-back segments belongs to the later one');
    ok(title(f, 'a').text === '<b>q1 &lt;b&gt;&amp;&lt;/b&gt; %{x}</b>' && !/q1 <b>/.test(JSON.stringify(f.layout.annotations.map((a) => a.text))),
       'names are escaped in Plotly text');
    const hv = role(f, 'hover').find((t) => t.meta.lane === 'a');
    ok(hv.hovertemplate === '%{text}<extra>%{customdata}</extra>' && hv.customdata[0] === 'q1 &lt;b&gt;&amp;&lt;/b&gt; %{x}', 'the hover template never contains a name');
    h2.destroy();
    // more than six pairs: six rows and a disclosure with the rest
    const many = { lanes: [0, 1, 2, 3, 4].map((i) => lane('l' + i, 'gate', [sq('p', i * 20, i * 20 + 10, 0.1)])), notes: [] };
    const h3 = GT.render(host2, many, {});
    const pl = lastRoot().querySelector('[data-gate-timeline-pairs]');
    ok(h3.timing().length === 10 && pl.querySelectorAll(':scope > [data-pair]').length === 6 &&
       pl.querySelector('details.ui-disclosure > summary.ui-disclosure-summary').textContent === 'all 10 pairs' &&
       pl.querySelectorAll('details [data-pair]').length === 4, 'more than six pairs: six rows and "all N pairs"');
    h3.destroy();
}

// numbers, the reference comparison, view-scoped hiding, bounds, colours
{
    const sq = (op, s, e, v, extra) => seg(op, [s, e], [v, v], extra);
    const L = (key, segs, extra) => lane(key, 'gate', segs, extra);
    // times above 1000 and 10000 ns print exactly
    let hx = GT.render(host2, { lanes: [L('A', [sq('p', 1024, 1030, 0.1)]), L('B', [sq('q', 1026, 1040, 0.1)]),
                                          L('C', [sq('r', 10000, 11234.5, 0.1, { length_ns: 2048 })])], notes: [] }, { view: 'overlay' });
    const rowsX = [...lastRoot().querySelectorAll('[data-pair]')].map((r) => r.textContent);
    ok(rowsX.some((t) => /overlap 4 ns1026 to 1030 ns$/.test(t)), 'times above 1000 ns print exactly (overlap 4 ns, 1026 to 1030 ns)');
    ok(/A plays 1024 to 1030 ns/.test(lastRoot().querySelector('[data-gate-timeline-plot]').getAttribute('aria-label')), 'the text alternative prints exact times');
    hx.setView('lanes');
    ok(segText(last(), 'C')[0].meta.line2 === 'level 0.1 V, config length 2048 ns, drawn 1234.5 ns', 'lengths above 1000 ns print exactly');
    const gl = last().layout.annotations.filter((a) => a.meta && a.meta.role === 'segment-text').map((a) => a.text).join();
    ok(!/e\+|e-/.test(gl), 'never scientific notation');
    hx.destroy();
    // volts: 4 significant digits; two values that differ never print the same
    hx = GT.render(host2, { lanes: [L('A', [sq('p', 0, 10, 0.1)]), L('B', [sq('q', 0, 10, 0.1001)]), L('C', [sq('r', 0, 10, 0.30000000000000004)])], notes: [] });
    ok(segText(last(), 'A')[0].meta.line2.startsWith('level 0.1 V') && segText(last(), 'B')[0].meta.line2.startsWith('level 0.1001 V') &&
       segText(last(), 'C')[0].meta.line2.startsWith('level 0.3 V'), 'volts: 0.1 and 0.1001 differ, 0.30000000000000004 reads 0.3');
    hx.destroy();
    hx = GT.render(host2, { lanes: [L('A', [sq('p', 0, 10, 0.12345)]), L('B', [sq('q', 0, 10, 0.12346)])], notes: [] });
    ok(segText(last(), 'A')[0].meta.line2 !== segText(last(), 'B')[0].meta.line2, 'volts: digits grow until different values print differently');
    hx.destroy();

    // timing() against an independent reference: quarter-ns cells and points
    let seed = 12345;
    const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
    function reference(A, B) {
        const cells = (segs) => { const c = new Set(); segs.forEach(([s, e]) => { for (let k = s; k < e; k++) c.add(k); }); return c; };
        const points = (segs) => { const p = new Set(); segs.forEach(([s, e]) => { for (let k = s; k <= e; k++) p.add(k); }); return p; };
        const ca = cells(A), cb = cells(B);
        const both = [...ca].filter((k) => cb.has(k)).sort((x, y) => x - y);
        if (both.length) {
            const runs = [];
            both.forEach((k) => { const r = runs[runs.length - 1]; if (r && r[1] === k) r[1] = k + 1; else runs.push([k, k + 1]); });
            return { kind: 'overlap', ns: both.length / 4, from_ns: runs[0][0] / 4, to_ns: runs[runs.length - 1][1] / 4, intervals: runs.map((r) => [r[0] / 4, r[1] / 4]) };
        }
        const pa = [...points(A)], pb = [...points(B)];
        let best = null;
        pa.forEach((a) => pb.forEach((b) => {
            const d = Math.abs(a - b), from = Math.min(a, b);
            if (!best || d < best.d || (d === best.d && from < best.from)) best = { d, from, to: Math.max(a, b) };
        }));
        if (best.d === 0) return { kind: 'meet', ns: 0, from_ns: best.from / 4, to_ns: best.from / 4, intervals: [] };
        return { kind: 'gap', ns: best.d / 4, from_ns: best.from / 4, to_ns: best.to / 4, intervals: [[best.from / 4, best.to / 4]] };
    }
    const randSegs = () => {
        const out = [];
        const n = 1 + rnd(3);
        for (let i = 0; i < n; i++) {
            const s = rnd(120), len = rnd(5) === 0 ? 0 : rnd(40);
            out.push([s, s + len]);
        }
        if (rnd(6) === 0) out.push(out[0].slice());                     // identical
        if (rnd(6) === 0) out.push([out[0][0] + 1, out[0][0] + 1]);     // nested point
        return out.reverse();                                           // unsorted
    };
    // a small fixed set by default (seconds); GATE_TIMELINE_FULL=1 runs the full set
    const N_REF = process.env.GATE_TIMELINE_FULL ? 1800 : 60;
    let mismatches = 0, cases = 0, firstBad = '';
    for (let c = 0; c < N_REF; c++) {
        const A = randSegs(), B = randSegs();
        const toLane = (key, ss) => L(key, ss.map(([s, e], k) => seg('s' + k, s === e ? [s / 4] : [s / 4, e / 4], s === e ? [0.1] : [0.1, 0.1],
                                                                    { start_ns: s / 4, end_ns: e / 4 })));
        const hr = GT.render(host2, { lanes: [toLane('a', A), toLane('b', B)], notes: [] });
        const got = hr.timing()[0];
        hr.destroy();
        const want = reference(A, B);
        cases++;
        const same = got && got.kind === want.kind && got.ns === want.ns && got.from_ns === want.from_ns && got.to_ns === want.to_ns &&
            JSON.stringify(got.intervals) === JSON.stringify(want.intervals);
        if (!same) { mismatches++; if (!firstBad) firstBad = JSON.stringify({ A, B, got, want }); }
    }
    ok(cases === N_REF && mismatches === 0, 'timing() equals an independent quarter-ns reference on ' + N_REF + ' seeded cases' + (firstBad ? ' ' + firstBad : ''));
    // order independence for a point inside the other line
    const pA = L('a', [sq('p', 34.5, 53.5, 0.1)]), pB = L('b', [seg('pt', [44.75], [0.1], { start_ns: 44.75, end_ns: 44.75 })]);
    const t1 = GT.render(host2, { lanes: [pA, pB], notes: [] }).timing()[0];
    const t2 = GT.render(host2, { lanes: [pB, pA], notes: [] }).timing()[0];
    ok(t1.kind === 'meet' && t1.from_ns === 44.75 && t2.from_ns === 44.75, 'a point inside the other line meets at the point, whatever the pair order');
    // nested segments of one line form one union
    const hn = GT.render(host2, { lanes: [L('a', [sq('p', 0, 100, 0.1), sq('q', 10, 20, 0.1)]), L('b', [sq('r', 50, 150, 0.1)])], notes: [] });
    ok(hn.timing()[0].kind === 'overlap' && hn.timing()[0].ns === 50, 'a nested segment does not shorten the union');
    hn.destroy();

    // hidden is Overlay-only; picture, list, timing() and text alternative agree
    const three = { lanes: [L('q1.z', [sq('p', 0, 10, 0.1)]), L('q2.z', [sq('p', 20, 30, 0.1)]), L('c', [sq('p', 40, 50, 0.1)])], notes: [] };
    const hv = GT.render(host2, three, { view: 'overlay' });
    const nRows = () => lastRoot().querySelectorAll('[data-pair]').length;
    ok(nRows() === 3, 'overlay: three pairs listed');
    hv.setLineHidden('q1.z', true);
    ok(nRows() === 1 && hv.timing().length === 1, 'the list refreshes after setLineHidden');
    ok(!/q1\.z/.test(lastRoot().querySelector('[data-gate-timeline-plot]').getAttribute('aria-label')), 'the text alternative follows hidden lines');
    hv.setView('lanes');
    ok(yAxes(last().layout).length === 3 && nRows() === 3 && hv.timing().length === 3 &&
       /q1\.z plays/.test(lastRoot().querySelector('[data-gate-timeline-plot]').getAttribute('aria-label')), 'Lanes: every lane drawn and counted');
    hv.setView('overlay');
    ok(nRows() === 1, 'back in Overlay the line is still hidden');
    hv.setLineHidden('q1.z', false);
    // the row highlight is keyed by the pair and cleared on a rebuild
    const rows3 = [...lastRoot().querySelectorAll('[data-pair]')];
    rows3[2].dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    ok(last().layout.shapes.some((x2) => x2.meta && x2.meta.lit), 'hovering the third row lights its bracket');
    hv.setLineHidden('q2.z', true);
    ok(!last().layout.shapes.some((x2) => x2.meta && x2.meta.lit), 'after a line is hidden no bracket stays lit');
    // a pair that survives the rebuild is not left lit either (the pointer is no longer on a row)
    hv.setLineHidden('q2.z', false);
    const rowQ1C = [...lastRoot().querySelectorAll('[data-pair-key]')].find((r) => r.getAttribute('data-pair-key') === 'q1.zc');
    rowQ1C.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
    hv.setLineHidden('q2.z', true);
    ok(last().layout.shapes.some((x2) => x2.name === 'bracket') && !last().layout.shapes.some((x2) => x2.meta && x2.meta.lit),
       'a rebuilt list clears the highlight even when the lit pair stays listed');
    // listeners do not pile up when the list is rebuilt
    const live0 = window.__live;
    for (let k = 0; k < 10; k++) { hv.setLineHidden('q2.z', k % 2 === 0); }
    ok(window.__live === live0, 'rebuilding the list adds no listeners');
    hv.destroy();

    // bounds: 40 overlapping gate lines
    const forty = { lanes: Array.from({ length: 40 }, (_, i) => L('l' + i, [sq('p', i, 200 + i, 0.1)])), notes: [] };
    const hf = GT.render(host2, forty, { view: 'overlay' });
    const ff = last();
    const brs = ff.layout.shapes.filter((x2) => x2.name === 'bracket');
    const ys = brs.map((b) => +b.path.split(' ')[2]);
    ok(brs.length === 12 && ys.every((y) => y > 0 && y <= 1), 'at most 12 brackets, all inside the plot');
    ok(ff.layout.annotations.some((a) => a.meta && a.meta.role === 'brackets-more' && a.text === (hf.timing().length - 12) + ' more in the list'), 'the rest are said to be in the list');
    const al = lastRoot().querySelector('[data-gate-timeline-plot]').getAttribute('aria-label');
    ok(al.length < 1500 && /and \d+ more, see the list$/.test(al), 'the text alternative is bounded');
    hf.destroy();

    // kit colours: an undefined --ui-line-1 falls back to the palette, never to black
    const st2 = doc.createElement('style');
    st2.textContent = ':root { --ui-line-2: rgb(2,2,2); --ui-line-3: color-mix(in srgb, red 50%, blue); }';
    doc.head.appendChild(st2);
    ok(GT.laneColor(0) === window.PlotTheme.palette()[0], 'an undefined kit line colour falls back to the palette');
    st2.remove();

    // "no value in part", one spectator, the reserved key, one picture per host
    const hn2 = GT.render(host2, { lanes: [L('a', [seg('h', [0, 5, 10], [0.1, null, 0.1])])], notes: [] });
    ok(/no value in part/.test(segText(last(), 'a')[0].meta.line2), 'a null inside a received segment is said in the label');
    hn2.destroy();
    const h1s = GT.render(host2, { lanes: [L('a', [sq('p', 0, 10, 0.1)]), lane('s', 'spectator', [sq('p', 0, 10, 0.01)])], notes: [] }, { view: 'overlay', foldSpectators: false });
    ok([...lastRoot().querySelectorAll('button[data-legend]')].some((b) => /^1 spectator$/.test(b.childNodes[0].textContent)), '"1 spectator", not "1 spectators"');
    h1s.destroy();
    GT.render(host2, { lanes: [L('spectators', [sq('p', 0, 10, 0.1)])], notes: [] });
    ok(lastRoot().querySelector('[data-gate-timeline-plot]'), 'a lane keyed "spectators" is an ordinary lane');
    const h4 = GT.render(host2, { lanes: [L('x', [sq('p', 0, 10, 0.1)])], notes: [] });
    ok(host2.querySelectorAll('[data-gate-timeline]').length === 1, 'a second render on the same host replaces the first');
    h4.destroy();
    // "starts at" never sits on a line
    const hs2 = GT.render(host2, { lanes: [L('a', [sq('p', 600, 700, 0.62)], { limit_v: [-0.5, 0.5] })], notes: [] });
    const sa = last().layout.annotations.find((a) => a.meta && a.meta.role === 'starts-at');
    const yr = last().layout.yaxis.range;
    ok(!sa || [0, 0.5].every((ly) => Math.abs(yr[0] + sa.y * (yr[1] - yr[0]) - ly) * 110 / (yr[1] - yr[0]) > 10), '"starts at" clear of the 0 V and limit lines');
    hs2.destroy();
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
   bar.querySelector('input[type=checkbox][data-fold]') && !bar.querySelector('[data-fold]').checked && !bar.querySelector('button.btn-sm'),
   'spectator switch is the kit checkbox in the toolbar, unchecked when folded');
ok([...bar.children].map((n) => n.matches('[data-scale-label]') ? 'label' : n.matches('[aria-label="Vertical scale"]') ? 'scale' : n.matches('[data-view-label]') ? 'vlabel' : n.matches('[aria-label="View"]') ? 'view' : n.matches('[data-height-label]') ? 'hlabel' : n.matches('[aria-label="Line height"]') ? 'height' : n.matches('[data-spectator-summary]') ? 'spect' : n.querySelector('[data-show-sim]') ? 'sim' : '?').join() === 'vlabel,view,label,scale,hlabel,height,spect,sim',
   'toolbar order: scale label, scale, spectators, simulation');
ok(bar.querySelector('[data-scale-label]').textContent === 'V scale' && bar.querySelector('[data-scale-label]').classList.contains('ui-note') &&
   [...bar.querySelectorAll('[aria-label="Vertical scale"] .ui-segmented-opt')].map((b) => b.textContent).join('|') === 'Per lane|Shared',
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
// wait and align steps: labelled marks on both views, the same marks
{
    const MK = { lanes: [lane('q1.z', 'gate', [seg('fx', [40, 140], [0.01, 0.01])])], notes: [],
                 marks: [{ kind: 'wait', label: 'wait 40 ns', start_ns: 0, end_ns: 40 },
                         { kind: 'align', label: 'align', t_ns: 140 }] };
    const pick = (f) => ({
        sh: f.layout.shapes.filter((x) => x.name === 'step-mark').map((x) => x.meta.kind + ':' + x.x0 + '-' + x.x1).join(),
        an: f.layout.annotations.filter((a) => a.meta && a.meta.role === 'step-mark').map((a) => a.text + '@' + a.x).join() });
    const h1 = GT.render(host2, MK); const a = pick(last()); h1.destroy();
    const h2 = GT.render(host2, MK, { view: 'overlay' }); const b = pick(last()); h2.destroy();
    ok(a.sh === 'wait:0-40,align:140-140' && a.an === 'wait 40 ns@20,align@140', 'lanes view: a wait span and an align line, each labelled');
    ok(JSON.stringify(a) === JSON.stringify(b), 'overlay view draws the same step marks as the lanes view');
}
// A final wait and a later align extend both axes; shapes stay on their own lanes.
{
    const MK = { lanes: [lane('q1.z', 'gate', [seg('fx', [241, 341], [0.1, 0.1])]),
                         lane('q1.xy', 'gate', [seg('x', [40, 80], [0.1, 0.1])], { label: 'q1.xy (step)', step: true }),
                         lane('q3.z', 'spectator', [seg('sp', [241, 341], [0.1, 0.1])])],
                 marks: [{ kind: 'wait', element: 'q1.z', label: 'wait 16 ns', start_ns: 341, end_ns: 357 },
                         { kind: 'wait', element: 'q1.xy', label: 'wait 16 ns', start_ns: 180, end_ns: 196 },
                         { kind: 'align', element: 'q1.z', label: 'align', t_ns: 380 }] };
    for (const view of ['lanes', 'overlay']) {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 700 });
        const h = GT.render(host2, MK, { view, foldSpectators: false });
        const f = h.figure(), shapes = f.layout.shapes.filter(s => s.name === 'step-mark');
        ok(f.layout.xaxis.range[1] >= 380 && f.span[0] === 0, view + ': all final marks fit the axis');
        ok(shapes.length === 3 && shapes[0].x0 === 341 && shapes[0].x1 === 357 && shapes[1].x0 === 180,
           view + ': different drawn clocks keep separate spans');
        ok(view === 'overlay' || (shapes[0].yref === 'y domain' && shapes[1].yref === 'y2 domain'),
           view + ': lane marks cover only their own line');
        ok(f.layout.annotations.some(a => a.meta && a.meta.role === 'step-mark' && a.text === 'wait 16 ns' && a.x === 349),
           view + ': a wait after the final pulse is labelled');
        ok(h.laneCount().spectator === 1, view + ': only the real spectator is counted');
        ok(host2.querySelectorAll('[data-gate-timeline-pairs] [data-pair]').length === 0,
           view + ': a sequence step before the gate lists no gap against the gate line');
        h.setFold(true);
        ok(h.laneCount().drawn === 2, view + ': folding the spectator keeps the xy step');
        h.destroy();
    }
    for (const width of [360, 400, 700]) {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: width - 80 });
        const h = GT.render(host2, MK, { foldSpectators: false }), f = h.figure();
        const sideWidth = width - 80 - (f.layout.margin.l + f.layout.margin.r);
        ok(sideWidth >= 200 && (width === 700 || title(f, 'q1.xy').yanchor === 'bottom'),
           width + ' px: step lane titles leave at least 200 px for the plot');
        h.destroy();
    }
    Object.defineProperty(host2, 'clientWidth', { configurable: true, value: 620 });
    const resizing = GT.render(host2, MK);
    for (const width of [280, 320, 620]) {
        Object.defineProperty(host2, 'clientWidth', { configurable: true, value: width });
        resizing.relayout({ width });
        ok(resizing.figure().hostPx === width && (width === 620 || title(resizing.figure(), 'q1.xy').yanchor === 'bottom'),
           'resizing remeasures step lane titles at ' + width + ' px');
    }
    resizing.destroy();
    const crowded = Object.assign({}, MK, { marks: Array.from({ length: 8 }, () =>
        ({ kind: 'align', element: 'q1.z', label: 'align', t_ns: 341 })).concat(
        [{ kind: 'wait', element: 'q1.z', label: 'wait 16 ns', start_ns: 341, end_ns: 357 }]) });
    for (const view of ['lanes', 'overlay']) {
        const h = GT.render(host2, crowded, { view }), f = h.figure();
        const labels = f.layout.annotations.filter(a => a.meta && a.meta.role === 'step-mark');
        ok(labels.length === 2 && new Set(labels.map(a => a.yshift)).size === 2,
           view + ': crowded marks use two rows and one label serves marks that share a place');
        ok(labels.some(a => a.text === 'wait 16 ns' && a.x === 349),
           view + ': a wait keeps its label when aligns crowd the same place');
        ok(host2.querySelectorAll('[data-step-description][title]').length === 9,
           view + ': every mark keeps its full text in a title');
        h.relayout({ 'xaxis.range': [0, 400] });
        ok(h.figure().layout.annotations.filter(a => a.meta && a.meta.role === 'step-mark').length === 2,
           view + ': relayout keeps labels fitted');
        h.destroy();
    }
}
ok(host.childNodes.length === 0, 'host clean at the end');

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
})().catch((e) => { console.log('FAIL: crash ' + e.stack); process.exit(1); });
