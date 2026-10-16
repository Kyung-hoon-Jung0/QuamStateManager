'use strict';
// Chip Status > CZ gate plan > Chip frequency map: the shipped cz-map.js (with topo-graph.js and
// cz-plan.js, the page's bundle order) under jsdom. Part 1 pins the columns on a synthetic map
// (segment styles, defects, safe strips, refusals, picker, orders, click, hover grammar, the
// render lifecycle). Part 2 drives the frozen route answers (tests/fixtures/cz_plan_contract):
// the chip drawing joined to the columns (shear order, leaders, logical / none modes), the
// proper edge colouring shared by segments and bars, bar and partner labels, the persistent
// selection and the hover / table-row highlight, the trace bound, the measured loss (pieces
// never joined, clip ticks, scale select, the stored defects' source run, the notes), and the
// drawing inside the viewport at 1280x800.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const probe = require('./cz_plotly_probe.cjs');
const root = path.join(__dirname, '..');
const STATIC = f => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', f), 'utf8');
const FX = f => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/cz_plan_contract', f), 'utf8'));
const BUNDLE = ['topo-graph.js', 'cz-map.js', 'cz-plan.js'];
const wait = ms => new Promise(r => setTimeout(r, ms));
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }

function html(sel, rows) {
    return `<div id="czp-root" class="czp-root" data-selected="${sel || ''}">
<section class="czp-map-box"><select id="czp-map-op"><option value="">default</option></select>
<select id="czp-map-order"><option value="chip" selected>chip</option><option value="name">name</option><option value="f01">f01</option></select>
<select id="czp-map-scale"><option value="0.25" selected>25</option><option value="0.5">50</option><option value="1">100</option></select>
<div id="czp-map"></div><p id="czp-map-key"></p><p id="czp-map-notes"></p>
<details id="czp-map-detail"><summary id="czp-map-detail-summary"></summary><div id="czp-map-detail-body"></div></details></section>
<table id="czp-table"><tbody>${(rows || []).map(op => `<tr class="czp-row" data-op="${op}"><td class="czp-last" data-last="${op}">x</td></tr>`).join('')}</tbody></table>
<p id="czp-history-note"></p>
<section><h3 id="czp-title"></h3><button id="czp-stale-chip" hidden></button><select id="czp-op"></select><p id="czp-status" hidden></p>
<div class="czp-plots"><p id="czp-flux-caveat" hidden></p><div id="czp-flux"></div><p id="czp-time-caveat" hidden></p><div id="czp-time"></div></div>
<div id="czp-card"></div><details id="czp-notes-box"><summary id="czp-notes-summary"></summary><ul id="czp-notes"></ul></details></section></div>`;
}

/* One page: the bundle evaluated, fetch answered from `state`, Plotly's restyle/relayout recorded. */
async function page(opts) {
    const dom = new JSDOM('<!doctype html><body>' + html(opts.sel, opts.rows) + '</body>',
                          {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window, d = w.document;
    Object.assign(w, {console});
    const env = {dom, w, d, renders: [], fetched: [], restyles: [], relayouts: [], width: opts.width || 1200, top: opts.top || 0,
                 state: {map: opts.map, loss: opts.loss}, held: null, holdLoss: !!opts.holdLoss};
    Object.defineProperty(w, 'innerHeight', {value: opts.innerHeight || 950, configurable: true, writable: true});
    w.requirePlotly = () => Promise.resolve();
    w._plotlyRender = (el, traces, layout) => {
        env.renders.push({id: el.id, traces, layout});
        probe.emitter(el);
        el.data = traces;
        return Promise.resolve();
    };
    w.Plotly = {restyle: (el, upd, idx) => { env.restyles.push({upd, idx}); return Promise.resolve(); },
                relayout: (el, upd) => { env.relayouts.push(upd); return Promise.resolve(); }};
    w.PlotTheme = {houseLayout: l => l, houseConfig: c => c, palette: () => ['#111', '#222', '#333', '#444', '#555', '#666']};
    w.fetch = url => {
        env.fetched.push(url);
        if (url.startsWith('/cz-plan/map')) {
            const body = typeof env.state.map === 'function' ? env.state.map(url) : JSON.parse(JSON.stringify(env.state.map));
            return Promise.resolve({json: () => Promise.resolve(body)});
        }
        if (url.startsWith('/cz-plan/loss')) {
            if (env.holdLoss) return new Promise(resolve => { env.held = () => resolve({json: () => Promise.resolve(env.state.loss)}); });
            return Promise.resolve({json: () => Promise.resolve(JSON.parse(JSON.stringify(env.state.loss)))});
        }
        if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve({ok: true, mode: 'ledger', ops: {}})});
        return new Promise(() => {});      // /cz-plan/data: only that it was asked
    };
    const mapEl = d.getElementById('czp-map');
    Object.defineProperty(mapEl, 'clientWidth', {get: () => env.width, configurable: true});
    mapEl.getBoundingClientRect = () => ({top: env.top, bottom: env.top, left: 0, right: env.width, width: env.width, height: 0});
    if (opts.order) d.getElementById('czp-map-order').value = opts.order;
    if (opts.beforeScripts) opts.beforeScripts(w);
    (opts.scripts || BUNDLE).forEach(f => w.eval(STATIC(f)));
    env.root = d.getElementById('czp-root');
    env.mapEl = mapEl;
    env.last = () => env.renders.filter(x => x.id === 'czp-map').at(-1);
    env.mapFetches = () => env.fetched.filter(u => u.startsWith('/cz-plan/map')).length;
    env.close = () => dom.window.close();
    w.CzPlan.mount();
    await wait(30);
    return env;
}

/* A render's segments, one entry per drawn member (the colour classes are null-separated). */
function segments(r) {
    const out = [];
    r.traces.filter(t => t.meta === 'seg').forEach(t => {
        for (let k = 0; k + 1 < t.x.length; k += 3) {
            out.push({x0: t.x[k], x1: t.x[k + 1], y: t.y[k], color: t.line.color, width: t.line.width, dash: t.line.dash,
                      path: t.customdata[k][0], pair: t.customdata[k][1], text: t.text[k]});
        }
    });
    return out;
}
const named = (r, prefix) => (r.layout.shapes || []).filter(s => String(s.name || '').startsWith(prefix));
const label = (r, pair) => (r.layout.annotations || []).find(a => a.name === 'bar-label:' + pair);
function polygons(t) {
    const out = [];
    let cur = [];
    t.x.forEach((x, k) => {
        if (x === null) { if (cur.length) out.push(cur); cur = []; } else cur.push([x, t.y[k]]);
    });
    if (cur.length) out.push(cur);
    return out;
}
const near = (a, b, tol) => Math.abs(a - b) <= (tol || 1e-9);

const MAP = {ok: true, unavailable: [], op: null, missing: [], operations: ['cz', 'cz_b'],
    qubits: [{name: 'q2', path: 'qubits.q2', f01_hz: 4.0e9, defects: [], safe: [], loss_data: false, tier: 'quad-term estimate', direction: 'lower sweet spot'},
             {name: 'q10', path: 'qubits.q10', f01_hz: 5.0e9, defects: [{lo_hz: 4.618e9, hi_hz: 4.622e9, peak_loss: .3}],
              safe: [{lo_hz: 4.7e9, hi_hz: 4.95e9}], loss_data: true, tier: 'lab flux model', direction: 'upper sweet spot'},
             {name: 'q3', path: 'qubits.q3', f01_hz: 4.5e9, defects: [], safe: [], loss_data: false, tier: 'quad-term estimate', direction: 'unclear'}],
    segments: [{pair: 'q10-2', operation: 'cz', path: 'qubit_pairs.q10-2.macros.cz', role: 'control', qubit: 'q10', qubit_path: 'qubits.q10',
                moves: true, frequency_hz: 4.98e9, kind: 'lab flux model', validity: 'within the quadratic range', detuning_hz: 980e6, nearest: null},
               {pair: 'q10-2', operation: 'cz', path: 'qubit_pairs.q10-2.macros.cz', role: 'target', qubit: 'q2', qubit_path: 'qubits.q2',
                moves: false, frequency_hz: 4.0e9, kind: 'idle f_01', validity: 'idle', detuning_hz: 980e6, nearest: null},
               {pair: 'q3-2', operation: 'cz', path: 'qubit_pairs.q3-2.macros.cz', role: 'target', qubit: 'q2', qubit_path: 'qubits.q2',
                moves: true, frequency_hz: 4.2e9, kind: 'quad-term estimate', validity: 'within the quadratic range', detuning_hz: null, nearest: null}],
    refused: [{pair: 'q3-2', operation: 'cz', qubit: 'q3', reason: 'sign convention unclear'}],
    planned: [{pair: 'q10-2', qubit: 'q10', qubit_path: 'qubits.q10', frequency_hz: 4.6e9, what: 'planned interaction frequency'}]};
const NO_LOSS = {ok: true, reason: null, roots: [], runs_listed: 0, runs_read: 0, cap: 60, skipped: [], curves: {}, staleness: {mode: 'none', note: null, qubits: {}}};
const ORDER_13 = 'q7 q8 q3 q1 q9 q4 q2 q17 q15 q10 q5 q11 q6';
const ORDER_21 = 'qC5 qD1 qD2 qC2 qC4 qD3 qD4 qD5 qC1 qC3 qA6 qA3 qA1 qB5 qB4 qB3 qA4 qA2 qB2 qB1 qA5';

(async () => {
    // ===== Part 1: the columns on a synthetic map (columns by qubit name) =====
    const P1 = await page({map: url => {
        const body = JSON.parse(JSON.stringify(MAP));
        if (url.includes('op=cz_b')) { body.op = 'cz_b'; body.segments = body.segments.slice(0, 2); body.missing = ['q3-2']; body.refused = []; }
        return body;
    }, loss: NO_LOSS, order: 'name'});
    const {w, d} = P1;
    let r = P1.last();
    check(r, 'the map is drawn on mount');
    check(r.layout.xaxis.ticktext.join() === 'q2,q10,q3', 'one column per qubit, in the order given');
    let segs = segments(r);
    check(segs.length === 3, 'one segment per drawn pair member');
    const q10 = segs.find(s => s.x0 === 1.12), q2still = segs.find(s => s.x0 === 0.12 && s.width < 2);
    check(q10 && q10.dash === 'solid' && q10.width > 2, 'a fitted-model member is a solid thick segment');
    check(q2still && q2still.y === 4.0, 'a member that stays at idle is a thin segment at its idle frequency');
    const quad = segs.find(s => s.pair === 'q3-2');
    check(quad && quad.dash === 'dash', 'a quad-term estimate is dashed');
    check(!segs.some(s => s.x0 === 2.12), 'the refused member (q3) is not drawn');
    check(d.getElementById('czp-map-notes').textContent.includes('q3 in q3-2 cz (sign convention unclear)'), 'the refusal is listed with its reason');
    const dbars = r.traces.find(t => t.meta === 'defect-bars');
    check(dbars && dbars.y[0] === 4.62, 'a defect is a bar at its frequency');
    check(r.layout.shapes.some(s => s.type === 'rect' && s.y0 === 4.7 && s.y1 === 4.95 && s.x0 >= 1.9 && s.x1 <= 2.0),
          'a safe interval is a strip at its column edge');
    const planned = r.traces.find(t => t.meta === 'planned');
    check(planned && planned.marker.symbol === 'diamond-open' && planned.y[0] === 4.6 && planned.x[0] === 1.5, 'the planned point sits in the carrier column');
    check(r.traces.some(t => t.meta === 'connector' && t.line.dash === 'dot'), 'the pair members are joined');
    const opSel = d.getElementById('czp-map-op');
    check(Array.from(opSel.options).map(o => o.value).join() === ',cz,cz_b', 'the picker lists every operation name');
    opSel.value = 'cz_b'; opSel.dispatchEvent(new w.Event('change', {bubbles: true})); await wait(30);
    check(P1.fetched.at(-1) === '/cz-plan/map?op=cz_b' && d.getElementById('czp-map-notes').textContent.includes('No cz_b operation on q3-2'),
          'an operation name is drawn where it exists and its absence is named');
    const ord = d.getElementById('czp-map-order');
    const before = P1.mapFetches();
    ord.value = 'f01'; ord.dispatchEvent(new w.Event('change', {bubbles: true})); await wait(30);
    check(P1.last().layout.xaxis.ticktext.join() === 'q2,q3,q10', 'columns by idle frequency');
    check(P1.mapFetches() === before, 'a column order change redraws from the page and asks the server nothing');
    // v1.4.1 (A14): ">10 pairs: no connectors, the legend isolates a pair" -> no connectors;
    // the legend is off now (colour classes, not pairs) and the chip drawing names every pair.
    const many = JSON.parse(JSON.stringify(MAP));
    many.segments = [];
    for (let k = 0; k < 11; k++) many.segments.push(
        {...MAP.segments[0], pair: 'p' + k, path: 'qubit_pairs.p' + k + '.macros.cz'},
        {...MAP.segments[1], pair: 'p' + k, path: 'qubit_pairs.p' + k + '.macros.cz'});
    P1.state.map = many;
    await w.CzPlan.drawMap(P1.root);
    check(!P1.last().traces.some(t => t.meta === 'connector') && P1.last().layout.showlegend === false,
          'more than ten pairs: no connectors, and no legend');
    P1.state.map = MAP;
    P1.mapEl._ev.emit('plotly_click', {points: [{customdata: 'qubit_pairs.q10-2.macros.cz'}]}); await wait(10);
    check(P1.fetched.some(u => u === '/cz-plan/data?op=' + encodeURIComponent('qubit_pairs.q10-2.macros.cz')), 'a click opens that operation');
    const widths = JSON.parse(JSON.stringify(MAP));
    widths.qubits[1].defects = [0, null, .1, .3, .5, .8].map((loss, i) => ({
        lo_hz: (4.1 + i * .01) * 1e9, hi_hz: (4.1 + i * .01) * 1e9, ...(loss == null ? {} : {peak_loss: loss})}));
    P1.state.map = widths;
    for (const order of ['name', 'f01']) {
        ord.value = order;
        await w.CzPlan.drawMap(P1.root);
        const t = P1.last().traces.find(x => x.meta === 'defect-bars'), column = order === 'name' ? 1 : 2;
        const bars = [];
        for (let k = 0; k + 1 < t.x.length; k += 3) bars.push([t.x[k], t.x[k + 1]]);
        check(bars.length === 6 && bars.every((b, i) => Math.abs((b[1] - b[0]) - [.08, .08, .244, .572, .9, .9][i]) < 1e-12 &&
              Math.abs(b[0] - (column + .05)) < 1e-12),
              'defect width numerically follows peak loss, including zero, missing and saturation after ' + order + ' ordering');
    }
    const onlyPlan = JSON.parse(JSON.stringify(MAP));
    onlyPlan.segments = [];
    onlyPlan.refused = MAP.segments.slice(0, 2).map(s => ({...s, reason: 'conversion unavailable'}));
    P1.state.map = onlyPlan; await w.CzPlan.drawMap(P1.root);
    check((P1.last().traces.find(t => t.meta === 'planned') || {y: []}).y[0] === 4.6, 'an independently stored point draws when both computed members are refused');

    const literal = '%{y} %{x:.2f} 50% {braces} <b>literal</b> < 1 > 0 & text';
    const hostile = JSON.parse(JSON.stringify(MAP));
    hostile.qubits.forEach(q => { q.name = literal; q.direction = literal; });
    hostile.segments.forEach(s => { s.pair = literal; s.operation = literal; s.qubit = literal; s.kind = literal; s.validity = literal; });
    hostile.planned.forEach(p => { p.pair = literal; p.qubit = literal; p.what = literal; });
    hostile.refused.forEach(x => { x.pair = literal; x.operation = literal; x.qubit = literal; x.reason = literal; });
    P1.state.map = hostile; await w.CzPlan.drawMap(P1.root);
    let hovered = 0;
    for (const trace of P1.last().traces.filter(t => t.hovertemplate)) {
        (trace.text || []).forEach((t, k) => {
            if (t == null) return;
            const result = probe.hover(trace.hovertemplate, {x: trace.x[k], y: trace.y[k], text: t, hovertext: (trace.hovertext || [])[k]});
            const span = d.createElement('span'); span.innerHTML = result;
            if (!span.textContent.includes(literal) || span.querySelector('b')) throw new assert.AssertionError({message: 'hover literal broken: ' + result});
            hovered++;
        });
    }
    check(hovered >= 4, 'production hover grammar preserves percent, braces, comparisons and literal markup in every state value');
    check(d.getElementById('czp-map-notes').textContent.includes(literal) && !d.getElementById('czp-map-notes').querySelector('b'),
          'refusal identities and reasons stay literal');

    // The shipped purge deletes Plotly's event emitter, leaving arbitrary element properties.
    const mapEl = P1.mapEl;
    w.PlotHost = {purgeWithin: el => { probe.purge(el); delete el.data; }};
    P1.state.map = MAP; await w.CzPlan.drawMap(P1.root); await w.CzPlan.drawMap(P1.root);
    check(mapEl._ev.listenerCount('plotly_click') === 1 && mapEl._ev.listenerCount('plotly_hover') === 1, 'ordinary redraws bind once per emitter');
    const oldEmitter = mapEl._ev;
    P1.state.map = {ok: false, error: 'temporary failure'}; await w.CzPlan.drawMap(P1.root);
    check(!mapEl._ev && !mapEl.on, 'the actual Plotly event purge removed the emitter');
    P1.state.map = MAP; await w.CzPlan.drawMap(P1.root); await w.CzPlan.drawMap(P1.root);
    check(mapEl._ev !== oldEmitter && mapEl._ev.listenerCount('plotly_click') === 1, 'recovery binds exactly once on the new emitter');
    const beforeClick = P1.fetched.length;
    mapEl._ev.emit('plotly_click', {points: [{customdata: ['qubit_pairs.q10-2.macros.cz', 'q10-2']}]}); await wait(5);
    check(P1.fetched.slice(beforeClick).filter(u => u.startsWith('/cz-plan/data')).length === 1, 'a recovered segment click opens the operation once');

    probe.queue(w, P1.renders);
    w.Plotly.restyle = () => Promise.resolve(); w.Plotly.relayout = () => Promise.resolve();
    let release;
    mapEl.__plotlyRenderChain = new Promise(resolve => { release = resolve; });
    P1.renders.length = 0;
    P1.state.map = MAP; const oldWork = w.CzPlan.drawMap(P1.root); await wait(10);
    P1.state.map = {ok: false, error: 'new selection refused'}; await w.CzPlan.drawMap(P1.root);
    release(); await oldWork;
    check(P1.renders.length === 0 && mapEl.textContent.includes('new selection refused'), 'production queue: stale map cannot replace a newer error');
    mapEl.__plotlyRenderChain = new Promise(resolve => { release = resolve; });
    P1.state.map = MAP; const previous = w.CzPlan.drawMap(P1.root); await wait(10);
    const latest = JSON.parse(JSON.stringify(MAP)); latest.segments[0].frequency_hz = 4.7e9;
    P1.state.map = latest; const current = w.CzPlan.drawMap(P1.root); await wait(10);
    release(); await Promise.all([previous, current]);
    check(P1.renders.length === 1 && segments(P1.renders[0]).find(s => s.width > 3 && s.dash === 'solid').y === 4.7,
          'production queue: only the newest numeric map draws');
    mapEl.__plotlyRenderChain = new Promise(resolve => { release = resolve; });
    const beforePurge = P1.renders.length;
    P1.state.map = MAP; const purged = w.CzPlan.drawMap(P1.root); await wait(10);
    probe.purgeWithin(w, mapEl);
    release(); await purged;
    check(P1.renders.length === beforePurge && !mapEl.data, 'production purge invalidates queued map work without a selection change');
    P1.close();

    // ===== Part 2: the chip drawing joined to the columns, on the frozen route answers =====
    const map5 = FX('map_rig5q.json'), loss5 = FX('loss_rig5q.json'), map13 = FX('map_rig13.json'), loss13 = FX('loss_rig13.json');
    const map21 = FX('map_rig21.json'), loss21 = FX('loss_rig21.json');
    const ops5 = Array.from(new Set(map5.segments.map(s => s.path)));
    const S5 = await page({map: map5, loss: loss5, sel: 'qubit_pairs.q3-4.macros.cz_SNZ', rows: ops5, width: 1100});
    const S13 = await page({map: map13, loss: loss13, width: 1100});
    const S21 = await page({map: map21, loss: loss21, width: 1000});
    const r5 = S5.last(), r13 = S13.last(), r21 = S21.last();
    check(r5.layout.xaxis.ticktext.join(' ') === 'q1 q2 q3 q4 q5' && r13.layout.xaxis.ticktext.join(' ') === ORDER_13 &&
          r21.layout.xaxis.ticktext.join(' ') === ORDER_21, 'by chip position: the grid sheared into one column per qubit (13 and 21 qubits pinned)');
    check([S5, S13, S21].every(S => S.mapEl.getAttribute('data-layout') === 'physical'), 'a complete grid is drawn as the chip itself');
    const leaders21 = named(r21, 'leader');
    check(leaders21.length === 21 && leaders21.every(s => s.x0 === s.x1 && s.y1 > s.y0), 'in chip order every leader is vertical, from the node up to its column');
    check(leaders21.every(s => s.yref === 'paper' && near(s.y1, r21.layout.yaxis.domain[0])), 'leaders end at the columns\' bottom edge');
    const nodes21 = r21.traces.find(t => t.meta === 'nodes');
    const yOf = Object.fromEntries(nodes21.text.map((n, k) => [n, nodes21.y[k]]));
    check(yOf.qC2 === 0 && yOf.qD5 === 4 && yOf.qA1 === 4 && yOf.qC5 === 1, 'a node sits at its row rank: the chip\'s row 0 at the bottom of the drawing');
    const fetch21 = S21.mapFetches();
    S21.d.getElementById('czp-map-order').value = 'name';
    S21.d.getElementById('czp-map-order').dispatchEvent(new S21.w.Event('change', {bubbles: true})); await wait(30);
    check(named(S21.last(), 'leader').some(s => s.x0 !== s.x1) && S21.mapFetches() === fetch21,
          'by qubit name the drawing stays and the leaders slant to their columns, without a request');
    S21.d.getElementById('czp-map-order').value = 'chip';
    S21.d.getElementById('czp-map-order').dispatchEvent(new S21.w.Event('change', {bubbles: true})); await wait(30);

    // logical: one qubit without a grid position -> positions from the pairs, said on the figure
    const partial = JSON.parse(JSON.stringify(map5)); partial.qubits[4].grid_location = null;
    const SL = await page({map: partial, loss: loss5});
    const rl = SL.last(), TG = SL.w.TopoGraph;
    check(SL.mapEl.getAttribute('data-layout') === 'logical' &&
          (rl.layout.annotations || []).some(a => a.name === 'logical-note' && a.text === TG.LOGICAL_LAYOUT_NOTE),
          'a chip without complete positions is drawn logically, with the logical-layout note on the figure');
    SL.close();
    // none: no positions and no pairs -> no drawing, one line; and without the topology helper
    const bare = JSON.parse(JSON.stringify(map5));
    bare.qubits.forEach(q => { q.grid_location = null; }); bare.pairs = {}; bare.segments = []; bare.planned = [];
    const SN = await page({map: bare, loss: loss5});
    check(!SN.last().layout.yaxis2 && !SN.last().traces.some(t => t.meta === 'nodes') &&
          SN.d.getElementById('czp-map-notes').textContent.includes('No chip drawing: the chip declares no complete grid positions and no pairs'),
          'no positions and no pairs: no drawing, one honest line');
    SN.close();
    const ST = await page({map: map5, loss: loss5, scripts: ['cz-map.js', 'cz-plan.js']});
    check(!ST.last().layout.yaxis2 && ST.d.getElementById('czp-map-notes').textContent.includes('the topology helper did not load') &&
          ST.last().layout.xaxis.ticktext.join(' ') === map5.qubits.map(q => q.name).join(' '),
          'without the topology helper: no drawing, columns by name, said so');
    ST.close();

    // proper edge colouring: pairs sharing a qubit never share a colour; segment colour == bar colour
    function colouring(S, r, m) {
        const bar = Object.fromEntries(named(r, 'bar:').map(x => [x.name.slice(4), x]));
        const at = {};
        Object.entries(m.pairs).forEach(([p, v]) => [v.control_path, v.target_path].forEach(q => { (at[q] = at[q] || []).push(p); }));
        const perQubit = Object.values(at).every(ps => {
            const cs = ps.filter(p => bar[p] && bar[p].line.width > 2).map(p => bar[p].line.color);
            return cs.length && new Set(cs).size === cs.length;
        });
        const ec = S.w.CzMap.edgeColours(m.pairs);
        return {perQubit, n: new Set(Object.values(ec)).size, proper: Object.values(at).every(ps => new Set(ps.map(p => ec[p])).size === ps.length),
                same: segments(r).every(x => bar[x.pair] && x.color === bar[x.pair].line.color)};
    }
    const c5 = colouring(S5, r5, map5), c13 = colouring(S13, r13, map13), c21 = colouring(S21, r21, map21);
    check(c5.perQubit && c13.perQubit && c21.perQubit, 'around every qubit the bars carry different colours');
    check(c5.n === 2 && c13.n === 4 && c21.n === 5, 'greedy proper edge colouring: 2 / 4 / 5 colours on the 5 / 13 / 21 qubit chips');
    check(c5.same && c13.same && c21.same, 'the segments of a pair carry the colour of its bar');
    check(c5.proper && c13.proper && c21.proper, 'the colouring is proper on every pair of the chip, drawn or not');
    check(JSON.stringify(S13.w.CzMap.edgeColours(map13.pairs)) === JSON.stringify({'q1-2': 0, 'q2-4': 1, 'q3-1': 1, 'q3-4': 0, 'q4-5': 2,
          'q4-9': 3, 'q5-6': 0, 'q5-10': 1, 'q6-11': 1, 'q7-8': 0, 'q8-3': 2, 'q8-9': 1, 'q9-10': 0, 'q10-11': 2, 'q15-10': 3, 'q17-15': 0}),
          'greedy in natural pair order (q2 before q10), deterministic');

    // bar labels: the mover's operating frequency; both movers -> the control's, with its name
    const mover = (m, pair) => m.segments.filter(s => s.pair === pair && s.moves);
    const one = mover(map5, 'q1-2');
    check(one.length === 1 && label(r5, 'q1-2').text === (one[0].frequency_hz / 1e9).toFixed(3), 'a bar carries the moving qubit\'s operating frequency (GHz, 3 decimals)');
    const both = Object.keys(map13.pairs).find(p => mover(map13, p).length === 2);
    const ctl = both && mover(map13, both).find(s => s.role === 'control');
    check(ctl && label(r13, both).text === ctl.qubit + ' ' + (ctl.frequency_hz / 1e9).toFixed(3), 'both members move: the label names its owner (the control)');
    const refusedMap = JSON.parse(JSON.stringify(map5));
    const mv = refusedMap.segments.find(s => s.pair === 'q3-4' && s.moves);
    refusedMap.segments = refusedMap.segments.filter(s => s !== mv);
    refusedMap.refused = [{pair: 'q3-4', operation: mv.operation, qubit: mv.qubit, reason: 'conversion unavailable', path: mv.path}];
    const SR = await page({map: refusedMap, loss: loss5});
    check((label(SR.last(), 'q3-4') || {}).text === '?' && named(SR.last(), 'bar:q3-4')[0].line.width > 2, 'a refused mover: the bar is drawn, its label is "?"');
    SR.close();
    const gray = JSON.parse(JSON.stringify(map5));
    gray.segments = gray.segments.filter(s => s.pair !== 'q2-3'); gray.missing = ['q2-3'];
    const SG = await page({map: gray, loss: loss5});
    const gbar = named(SG.last(), 'bar:q2-3')[0], gpt = SG.last().traces.find(t => t.meta === 'bars');
    check(gbar.line.width < 2 && gbar.line.dash === 'dot' && !label(SG.last(), 'q2-3') &&
          gpt.text[gpt.customdata.findIndex(c => c[1] === 'q2-3')].includes('no cz operation'), 'a pair without the operation: a thin gray bar, the reason on hover');
    SG.close();
    // a short bar's label is lifted off it along its upward normal; a long one sits on it
    const colPx21 = (1000 - 80) / 21;
    let lifted = 0, flat = 0, wrong = 0;
    named(r21, 'bar:').forEach(s => {
        const a = label(r21, s.name.slice(4));
        if (!a) return;
        const rowPx = 150 / (r21.layout.yaxis2.range[1] - r21.layout.yaxis2.range[0]);
        const len = Math.hypot((s.x1 - s.x0) * colPx21, (s.y1 - s.y0) * rowPx);
        if (len < 60) { if (a.yshift > 0 && Math.hypot(a.xshift, a.yshift) >= 11) lifted++; else wrong++; }
        else if (a.xshift === 0 && a.yshift === 0) flat++; else wrong++;
    });
    check(wrong === 0 && lifted > 5 && flat > 5, 'a bar shorter than 60 px carries its label above it, a longer one on it');

    // partner names at a segment's right end, only when a column is at least 40 px wide
    const pt5 = r5.traces.find(t => t.meta === 'partners');
    const s12 = segments(r5).find(s => s.pair === 'q1-2' && near(s.x0, 0.12));
    check(pt5 && pt5.x.some((x, k) => near(x, 0.88) && near(pt5.y[k], s12.y) && pt5.text[k] === 'q2'), 'each segment names its partner at its right end');
    S21.width = 900; await S21.w.CzMap.paint(S21.root);
    check(!S21.last().traces.some(t => t.meta === 'partners'), 'columns under 40 px: no partner names');
    S21.width = 1000; await S21.w.CzMap.paint(S21.root);
    check(S21.last().traces.some(t => t.meta === 'partners'), 'columns of 40 px and more: partner names');
    check(r21.traces.length <= 30, 'the 21-qubit map stays within 30 traces (' + r21.traces.length + ')');

    // the selected operation stays outlined; hover (map or table row) lights a pair by restyle only
    check(named(r5, 'selected:q3-4').length === 3 && named(r5, 'selected:q3-4').some(s => s.yref === 'y2'),
          'the selected operation is outlined on the map: its segments and its bar');
    const row12 = S5.root.querySelector('tr[data-op="' + ops5.find(o => o.includes('q1-2')) + '"]');
    row12.dispatchEvent(new S5.w.MouseEvent('click', {bubbles: true})); await wait(20);
    const rel = S5.relayouts.at(-1);
    check(rel && rel.shapes.some(s => s.name === 'selected:q1-2') && !rel.shapes.some(s => s.name === 'selected:q3-4') &&
          rel.shapes.length === named(r5, '').filter(s => !String(s.name || '').startsWith('selected:')).length + 3,
          'selecting another operation moves the outline by a relayout of the shapes');
    const rendersBefore = S5.renders.length, segIdx = r5.traces.findIndex(t => t.meta === 'highlight-seg'),
        barIdx = r5.traces.findIndex(t => t.meta === 'highlight-bar');
    S5.mapEl._ev.emit('plotly_hover', {points: [{customdata: ['qubit_pairs.q4-5.macros.x', 'q4-5']}]}); await wait(10);
    let rs = S5.restyles.at(-1);
    const g45 = segments(r5).filter(s => s.pair === 'q4-5'), b45 = named(r5, 'bar:q4-5')[0];
    check(rs && rs.idx.join() === segIdx + ',' + barIdx && rs.upd.x[0].filter(v => v !== null).length === 2 * g45.length &&
          rs.upd.x[1].join() === [b45.x0, b45.x1].join() && rs.upd['line.color'][0] === b45.line.color,
          'hovering a segment or bar restyles the two overlay traces to that pair');
    S5.mapEl._ev.emit('plotly_unhover', {}); await wait(10);
    rs = S5.restyles.at(-1);
    check(rs.upd.x[0].length === 0 && rs.upd.x[1].length === 0, 'leaving clears the highlight');
    const row23 = S5.root.querySelector('tr[data-op="' + ops5.find(o => o.includes('q2-3')) + '"]');
    row23.dispatchEvent(new S5.w.MouseEvent('mouseover', {bubbles: true})); await wait(10);
    rs = S5.restyles.at(-1);
    check(rs.upd.x[1].join() === [named(r5, 'bar:q2-3')[0].x0, named(r5, 'bar:q2-3')[0].x1].join(), 'hovering a table row lights its pair on the map');
    S5.root.querySelector('#czp-table').dispatchEvent(new S5.w.MouseEvent('mouseleave', {bubbles: false})); await wait(10);
    check(S5.restyles.at(-1).upd.x[1].length === 0 && S5.renders.length === rendersBefore, 'the highlight never re-renders the figure');

    // the measured loss: one polygon per piece, anchored at the column's left edge, never joined
    const q4col = r5.layout.xaxis.ticktext.indexOf('q4'), fill = r5.traces.find(t => t.meta === 'loss-fill');
    const polys = polygons(fill || {x: [], y: []}).filter(p => p[0][0] === q4col);
    const pieces4 = loss5.curves.q4.scan.pieces;
    check(polys.length === 2 && polys.every((p, k) => p[0][0] === q4col && p.at(-1)[0] === q4col &&
          p.slice(1, -1).map(v => v[1]).join() === pieces4[k].frequency_hz.map(f => f / 1e9).join()),
          'each piece of a curve is its own polygon from the column\'s left edge (q4: two pieces, never joined)');
    const q2col = r5.layout.xaxis.ticktext.indexOf('q2'), line = r5.traces.find(t => t.meta === 'loss-line');
    const p2 = loss5.curves.q2.scan.pieces[0];
    const xs2 = line.x.filter((x, k) => x !== null && x >= q2col && x < q2col + 1 && line.text[k].startsWith('q2 '));
    const j = p2.loss.findIndex(l => l > 0.05 && l < 0.2), jneg = p2.loss.findIndex(l => l < 0), jmax = p2.loss.indexOf(Math.max(...p2.loss));
    check(near(xs2[j], q2col + 0.9 * p2.loss[j] / 0.25) && xs2[jneg] === q2col && near(xs2[jmax], q2col + 0.9),
          'loss x = column left + 0.9 x loss / scale, clipped at 0 and at the edge');
    const marks = r5.traces.find(t => t.meta === 'defects-clips');
    const tick = marks.x.findIndex((x, k) => marks.marker.symbol[k] === 'triangle-right' && near(x, q2col + 0.92) && near(marks.y[k], p2.frequency_hz[jmax] / 1e9));
    check(tick >= 0 && marks.text[tick].includes((100 * p2.loss[jmax]).toFixed(1) + ' %') && marks.text[tick].includes('past the column scale'),
          'a stretch past the scale gets an edge tick whose hover gives the true loss');
    check(line.text[line.x.findIndex(x => x !== null && x >= q2col)].includes('run #' + loss5.curves.q2.scan.run_id) &&
          line.text.some(t => t && t.includes('100 ns') && t.includes('axis model') && t.includes('detuning')),
          'a curve sample names its run, the scan duration, the axis conversion and the detuning');
    const sc = S5.d.getElementById('czp-map-scale'), mf5 = S5.mapFetches();
    sc.value = '0.5'; sc.dispatchEvent(new S5.w.Event('change', {bubbles: true})); await wait(30);
    const r5b = S5.last(), line2 = r5b.traces.find(t => t.meta === 'loss-line');
    const xs2b = line2.x.filter((x, k) => x !== null && x >= q2col && x < q2col + 1 && line2.text[k].startsWith('q2 '));
    check(near(xs2b[j], q2col + 0.9 * p2.loss[j] / 0.5) && S5.mapFetches() === mf5 &&
          !(r5b.traces.find(t => t.meta === 'defects-clips') || {x: []}).x.some(x => near(x, q2col + 0.92)) &&
          r5b.layout.annotations.some(a => a.name === 'scale' && a.text.includes('0–50 %')),
          'the loss scale select rescales the columns without a request');
    sc.value = '0.25'; sc.dispatchEvent(new S5.w.Event('change', {bubbles: true})); await wait(30);
    // the run the stored defects came from: a thin outline, its stored defects as short dashes
    const src = r5.traces.find(t => t.meta === 'loss-source');
    const cols = new Set(src ? src.x.filter(x => x !== null).map(Math.floor) : []);
    check(src && cols.size === 2 && cols.has(q4col) && cols.has(r5.layout.xaxis.ticktext.indexOf('q5')) && src.line.dash === 'dot',
          'where the stored defects came from another run, that run is a dotted outline in its column');
    const dashes = marks.x.map((x, k) => [x, k]).filter(([x, k]) => marks.marker.symbol[k] === 'line-ew' && Math.floor(x) === q4col);
    check(dashes.length === map5.qubits.find(q => q.name === 'q4').defects.length &&
          dashes.every(([, k]) => marks.text[k].includes('stored defects match run #1390')), 'with a curve, the stored defects are short dashes naming their source run');
    const qA2col = r21.layout.xaxis.ticktext.indexOf('qA2'), marks21 = r21.traces.find(t => t.meta === 'defects-clips');
    const strong21 = marks21.x.map((x, k) => k).filter(k => marks21.marker.symbol[k] === 'line-ew' && Math.floor(marks21.x[k]) === qA2col);
    check(loss21.curves.qA2.marker_curve.defects[0].strong && strong21.length === 1 && marks21.text[strong21[0]].includes(', strong') &&
          !dashes.some(([, k]) => /strong/.test(marks.text[k])), 'a stored defect carries the strong flag of its source run, and only then');
    check(!r13.traces.some(t => t.meta === 'loss-fill') && r13.traces.some(t => t.meta === 'defect-bars') &&
          !(r13.traces.find(t => t.meta === 'defects-clips') || {x: []}).x.length, 'no curve: the stored defects draw as bars, as before');
    // notes: one line per qubit, only when something is off; the rest in the collapsed detail
    const n5 = S5.d.getElementById('czp-map-notes').textContent.split('\n');
    check(n5.includes('q4: stored defects from run #1390 (dotted), newest scan #1909.') &&
          n5.includes('q1: source of the stored defects not in the data folder.') && !n5.some(l => l.startsWith('q3:')),
          'the note names a qubit only when something is off (source run, no match)');
    check(n5.some(l => l.startsWith('q5:') && l.includes('axis of run #1388 predates 2 flux-model inputs changed (run #1897)')),
          'the ledger\'s staleness of a curve\'s axis is in the qubit\'s line');
    const n21 = S21.d.getElementById('czp-map-notes').textContent.split('\n');
    check(n21.filter(l => l.startsWith('qA2:')).length === 1 &&
          n21.find(l => l.startsWith('qA2:')).includes('axis of runs #1822 and #676 predate 1 flux-model input changed (run #3450)'),
          'two curves of one qubit staled by one change read as one line');
    const det = S5.d.getElementById('czp-map-detail');
    check(!det.open && S5.d.getElementById('czp-map-detail-summary').textContent.includes('5 of 5 with a scan') &&
          det.textContent.includes('newest scan run #1909') && det.textContent.includes('dotted outline run #1390'),
          'the full loss detail sits in a collapsed box under the map');

    // the drawing is inside the viewport at 1280x800: height = viewport - figure top - 40
    const SV = await page({map: map5, loss: loss5, width: 920, innerHeight: 800, top: 260});
    const rv = SV.last(), H = rv.layout.height, plotH = H - rv.layout.margin.t - rv.layout.margin.b;
    const bottom = 260 + H - rv.layout.margin.b;
    check(H === 500 && bottom <= 800 && near(rv.layout.yaxis2.domain[1] * plotH, 150, 1e-6) && bottom - 150 > 260,
          'at 1280x800 the 150 px chip drawing lies inside the viewport (bottom ' + bottom + ')');
    const nv = SV.renders.length;
    SV.w.innerHeight = 1100; SV.w.dispatchEvent(new SV.w.Event('resize')); await wait(250);
    check(SV.last().layout.height === 640 && SV.renders.length === nv + 1 && SV.mapFetches() === 1,
          'a taller window re-renders the map at the new height (clamped to 640), without a request');

    // the loss arrives after the map: drawn from the page, the map is not asked again
    const SA = await page({map: map5, loss: Object.assign({}, loss5, {staleness: {mode: 'building', note: null, qubits: {}}}), holdLoss: true,
                           beforeScripts: win => { win._timers = []; const st = win.setTimeout.bind(win);
                               win.setTimeout = (fn, ms) => { if (ms === 3000) { win._timers.push(fn); return 0; } return st(fn, ms); }; }});
    const first = SA.renders.length === 1 && !SA.renders[0].traces.some(t => t.meta === 'loss-fill') &&
        SA.d.getElementById('czp-map-detail-body').textContent.includes('Loading');
    SA.held(); await wait(30);
    check(first && SA.renders.length === 2 && SA.renders[1].traces.some(t => t.meta === 'loss-fill') && SA.mapFetches() === 1,
          'the map draws first; the measured loss joins it without a second map request');
    check(SA.d.getElementById('czp-map-notes').textContent.includes('change history is being prepared') && SA.w._timers.length === 1,
          'while the ledger is being built the curves draw and the axis check is asked again');
    SA.close();
    [S5, S13, S21, SV].forEach(S => S.close());
    console.log(checks + ' assertions passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
