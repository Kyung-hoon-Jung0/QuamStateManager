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
    return Promise.resolve(el);
  };
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

const shaped = seg('cz_x_pulse_q1_q2', [63, 64, 65, 66, 115], [0.0, 0.05, 0.1, 0.05, 0.0]);
const DATA = {
    lanes: [
        lane('q1.z', 'gate', [shaped]),
        lane('q2.z', 'gate', [seg('flux_a', [10, 30], [-0.2, -0.2]), seg('flux_b', [50, 60], [0.1, 0.1])], { limit_v: null }),
        lane('coupler_q1_q2', 'gate', [seg('bip', [20, 21, 22, 40], [0.3, -0.3, 0.3, 0.0],
                                           { flags: ['over_range', 'bad_length'] })],
             { limit_v: [-0.25, 0.25], offset_v: 0.05, sim: { t_ns: [20, 30, 40], v: [0.29, -0.1, 0.01] } }),
        lane('q3.z', 'spectator', [seg('park', [0, 100, 200], [0.02, 0.02, 0.02], { truncated: true, end_ns: 300 })]),
    ],
    notes: ['Times are program time, before filters.', 'MW timing against z lines is not computed.'],
    cannot_draw: null,
};

const h = GT.render(host, DATA, {});
let fig = last();

// one row per drawn lane, one x axis
let ya = yAxes(fig.layout);
ok(ya.length === 3 && h.laneCount().drawn === 3, 'folded: one y axis per drawn lane (3)');
ok(xAxes(fig.layout).length === 1 && fig.data.every((t) => t.xaxis === 'x'), 'one shared x axis for every trace');
ok(ya.every((k, i) => i === 0 || fig.layout[k].domain[1] < fig.layout[ya[i - 1]].domain[0]), 'lane rows stacked without overlap');
ok(fig.layout.hovermode === 'x unified', 'hover is one unified readout');
ok(JSON.stringify(h.laneCount()) === JSON.stringify({ gate: 3, spectator: 1, drawn: 3 }), 'laneCount folded');
ok(fig.layout.yaxis.title.text.startsWith('q1.z') && fig.layout.yaxis3.title.text.startsWith('coupler_q1_q2'), 'gate lanes on top in order');

// samples value by value, gaps not joined
const segs = role(fig, 'segment');
const want = DATA.lanes.slice(0, 3).flatMap((l, i) => l.segments.map((s) => ({ s, ax: axRef(i) })));
ok(segs.length === want.length && want.every((w, k) =>
    segs[k].yaxis === w.ax && JSON.stringify(segs[k].x) === JSON.stringify(w.s.t_ns) &&
    JSON.stringify(segs[k].y) === JSON.stringify(w.s.v)), 'trace points equal the input t_ns / v');
ok(segs.filter((t) => t.meta.lane === 'q2.z').length === 2, 'two segments with idle between are two traces (gap not joined)');
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
ok(hov && hov.text[hov.x.indexOf(40)] === 'idle' && hov.text[hov.x.indexOf(10)] === 'flux_a: -0.2 V', 'hover text is the valuesAt readout');

// scales
const r1 = fig.layout.yaxis.range, r2 = fig.layout.yaxis2.range;
ok(JSON.stringify(r1) !== JSON.stringify(r2), 'per lane: each lane has its own range');
ok(r2[0] < -0.2 && r2[1] > 0.1 && r2[0] <= 0 && r2[1] >= 0, 'per lane: an all-negative lane shows its data and 0 V');
ok(r1[0] <= -0.5 && r1[1] >= 0.5, 'per lane: the range covers known limits');
h.setScale('common');
fig = last();
ya = yAxes(fig.layout);
ok(ya.every((k) => JSON.stringify(fig.layout[k].range) === JSON.stringify(fig.layout.yaxis.range)), 'common: all lanes share one range');
ok(fig.layout.yaxis.range[0] <= -0.5 && fig.layout.yaxis.range[1] >= 0.5, 'common: range covers every lane');
const segBtns = [...host.querySelectorAll('.ui-segmented .ui-segmented-opt')];
ok(segBtns.length === 2 && segBtns[1].classList.contains('ui-segmented-on') && segBtns[1].getAttribute('aria-pressed') === 'true', 'segmented control shows common');
segBtns[0].click();
fig = last();
ok(JSON.stringify(fig.layout.yaxis.range) !== JSON.stringify(fig.layout.yaxis2.range), 'clicking Per lane goes back to own ranges');

// zero and limit lines
const shapesOf = (ax, name) => fig.layout.shapes.filter((s) => s.yref === ax && s.name === name);
ok(['y', 'y2', 'y3'].every((ax) => shapesOf(ax, 'zero').length === 1), 'every lane has its 0 V line');
ok(shapesOf('y', 'limit').length === 2 && shapesOf('y2', 'limit').length === 0 && shapesOf('y3', 'limit').length === 2,
   'limit lines only where limit_v is known');
ok(/range not known/.test(fig.layout.yaxis2.title.text) && !/range not known/.test(fig.layout.yaxis.title.text), 'unknown range said in the label');
ok(/offset 0\.05 V/.test(fig.layout.yaxis3.title.text) && !/offset/.test(fig.layout.yaxis.title.text), 'offset shown only when not 0');

// segment text, flags, truncated
const texts = fig.layout.annotations.filter((a) => a.meta && a.meta.role === 'segment-text');
const tq1 = texts.find((a) => a.meta.lane === 'q1.z');
ok(tq1 && /cz_x_pulse_q1_q2/.test(tq1.text) && /peak 0\.1 V/.test(tq1.text) && /52 ns/.test(tq1.text), 'segment text: name, peak, length');
ok(tq1.y === 1 && tq1.yanchor === 'top' && fig.layout.yaxis.range[1] > 0.5 * 1.2, 'segment text sits in head room above the data');
const tc = texts.find((a) => a.meta.lane === 'coupler_q1_q2');
ok(/over the port range/.test(tc.text) && /length is not a multiple of 4 ns/.test(tc.text), 'flag texts');
ok(role(fig, 'flag').length === 1 && role(fig, 'flag')[0].x[0] === 30 && role(fig, 'flag')[0].yaxis === 'y3', 'flag marker on its segment');
ok(!/over the port range/.test(tq1.text), 'no flag text on a clean segment');

// sim
ok(role(fig, 'sim').length === 1 && role(fig, 'sim')[0].yaxis === 'y3' && role(fig, 'sim')[0].line.dash === 'dot', 'sim drawn on its lane, visibly different');
h.setShowSim(false);
fig = last();
ok(role(fig, 'sim').length === 0, 'sim switched off');
h.setShowSim(true);

// fold
const foldBtn = host.querySelector('[data-fold]');
ok(host.querySelector('[data-spectator-summary] .ui-note').textContent === '1 spectator line', 'summary row names the spectator count');
foldBtn.click();
fig = last();
ok(h.laneCount().drawn === 4 && yAxes(fig.layout).length === 4, 'unfold: spectator lane drawn');
const tq3 = fig.layout.annotations.find((a) => a.meta && a.meta.lane === 'q3.z');
ok(tq3 && /only part of this waveform was received/.test(tq3.text), 'truncated text');
ok(fig.layout.yaxis4.title.text.startsWith('q3.z'), 'spectator below the gate lanes');
h.setFold(true);
ok(h.laneCount().drawn === 3, 'fold again');

// notes
const notes = [...host.querySelectorAll('p.ui-note')].map((p) => p.textContent);
ok(DATA.notes.every((n) => notes.includes(n)), 'notes shown verbatim with the kit note part');

// colours come from the theme, never literals in the component
ok(!/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/.test(src), 'no colour literal in gate-timeline.js');
ok(!/\.style\./.test(src) && !/style=/.test(src), 'no inline styles');

// destroy
const liveBefore = window.__live;
const purgeBefore = window.__host.purge;
h.destroy();
ok(!host.querySelector('[data-gate-timeline]') && host.childNodes.length === 0, 'destroy removes its nodes');
ok(window.__live <= liveBefore - 3, 'destroy removes its listeners');
ok(window.__host.purge === purgeBefore + 1 && window.__host.unobserve >= 1, 'destroy purges and unobserves');

// no spectators -> no summary row
const h0 = GT.render(host, { lanes: DATA.lanes.slice(0, 1), notes: [] });
ok(!host.querySelector('[data-spectator-summary]') && h0.laneCount().drawn === 1, 'no spectator lane: no summary row');
h0.destroy();

// 16 spectators
const many = { lanes: [DATA.lanes[0]].concat(Array.from({ length: 16 }, (_, i) =>
    lane('q' + (i + 4) + '.z', 'spectator', [seg('park', [0, 50], [0.01 * i, 0.01 * i])]))), notes: [] };
const h16 = GT.render(host, many, {});
fig = last();
ok(yAxes(fig.layout).length === 1 && /16 spectator lines/.test(host.textContent), '16 spectators folded: one summary row');
h16.setFold(false);
fig = last();
ya = yAxes(fig.layout);
ok(ya.length === 17, '16 spectators unfolded: 17 lanes');
const lanePx = ya.map((k) => (fig.layout[k].domain[1] - fig.layout[k].domain[0]) * (fig.layout.height - 48));
ok(lanePx.every((p) => p >= 80) && ya.every((k, i) => i === 0 || fig.layout[k].domain[1] < fig.layout[ya[i - 1]].domain[0]),
   '17 lanes keep their fixed height and do not overlap');
ok(ya.every((k, i) => fig.layout[k].title.text.startsWith(many.lanes[i].key)), 'every unfolded lane is labelled');
h16.destroy();

// cannot_draw and empty
const hc = GT.render(host, { lanes: DATA.lanes, notes: ['note a'], cannot_draw: 'cannot draw: for loop over n' });
const nRenders = window.__renders.length;
ok(host.querySelector('.ui-state--empty').textContent === 'cannot draw: for loop over n' && /note a/.test(host.textContent),
   'cannot_draw: statement and notes shown');
ok(hc.laneCount().drawn === 0 && !host.querySelector('[data-gate-timeline-plot]'), 'cannot_draw: no lanes drawn');
hc.destroy();
const he = GT.render(host, { lanes: [], notes: [] });
ok(host.querySelector('.ui-state--empty') && window.__renders.length === nRenders, 'empty lanes: empty state, no chart');
he.destroy();
ok(host.childNodes.length === 0, 'host clean at the end');

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
