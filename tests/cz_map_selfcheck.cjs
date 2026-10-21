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

// v1.5.1: copied visible paragraphs -> shipped map section, pin the footer's actual DOM.
function html(sel, rows) {
    return `<div id="czp-root" class="czp-root" data-selected="${sel || ''}">
${fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_cz_plan.html'), 'utf8').match(/<section class="czp-map-box"[\s\S]*?<\/section>/)[0]}
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

// v1.5.1: visible paragraphs -> collapsed footer, keep provenance below the loss detail.
async function footerPins(only) {
    const names = ['closed', 'words', 'counts', 'kinds', 'hidden', 'live', 'order', 'state', 'wrapping'];
    for (const name of names.filter(n => !only || n === only)) {
        const S = await page({map: FX('map_rig5q.json'), loss: FX('loss_rig5q.json'),
            rows: ['qubit_pairs.q1-2.macros.cz_SNZ', 'qubit_pairs.q3-4.macros.cz_GNZ']});
        const {d, w} = S, key = d.querySelector('#czp-map-key-box'), notes = d.querySelector('#czp-map-notes-box');
        const summary = d.querySelector('#czp-map-notes-summary');
        try {
            if (name === 'closed') check(key && notes && !key.open && !notes.open && !notes.hidden,
                'footer/closed: both disclosures start closed, populated notes are available');
            if (name === 'words') check(key && key.querySelector('summary').textContent === 'Key' &&
                summary && /^Notes \(\d+\): .*axis check/.test(summary.textContent),
                'footer/words: compact Key and Notes summaries still disclose axis checks');
            if (name === 'counts') {
                const sameChange = JSON.parse(JSON.stringify(S.root._czLoss.staleness.qubits.q5[0]));
                sameChange.run_id = 1909;
                S.root._czLoss.staleness.qubits.q4 = [sameChange];
                await w.CzMap.paint(S.root);
                check(summary && summary.textContent === 'Notes (5): 1 axis check, 4 data sources' &&
                    d.querySelector('#czp-map-notes').textContent.split('\n').length === 5 &&
                    d.querySelector('#czp-map-notes').textContent.includes('after the scans of q4 #1909, q5 #1388'),
                    'footer/counts: count rendered group lines, not affected qubits or scans');
            }
            if (name === 'kinds') {
                const loss = S.root._czLoss, map = S.root._czMap;
                loss.curves.q4.scan.identity = 'unchecked';
                delete loss.curves.q3; map.qubits.find(q => q.name === 'q3').loss_data = false;
                await w.CzMap.paint(S.root);
                const mixed = summary && summary.textContent === 'Notes (7): 1 axis check, 4 data sources, 1 chip identity check, 1 data gap';
                loss.reason = 'Scan unavailable';
                loss.staleness.mode = 'preparing';
                // Avoid scheduling retries: paint uses the answer already held by the page.
                await w.CzMap.paint(S.root);
                check(mixed && summary.textContent === 'Notes (9): 1 axis check, 1 pending check, 4 data sources, 1 chip identity check, 1 data gap, 1 other note',
                    'footer/kinds: identity, missing data, pending axis checks and other notes all count');
            }
            if (name === 'hidden') {
                S.root._czLoss = JSON.parse(JSON.stringify(NO_LOSS));
                await w.CzMap.paint(S.root);
                const empty = notes && notes.hidden && summary.textContent === 'Notes (0)' && !d.querySelector('#czp-map-notes').textContent;
                S.root._czLoss = FX('loss_rig5q.json'); await w.CzMap.paint(S.root);
                check(empty && !notes.hidden && summary.textContent.startsWith('Notes (5)'),
                    'footer/hidden: hide the whole notes disclosure only while there are no notes');
            }
            if (name === 'live') check(summary && summary.closest('summary') && summary.getAttribute('aria-live') === 'polite' &&
                summary.getAttribute('aria-atomic') === 'true' && !d.querySelector('#czp-map-notes').closest('[aria-live], [role="status"]'),
                'footer/live: announce only the compact summary, never the long notes');
            if (name === 'order') {
                const box = d.querySelector('.czp-map-box'), children = Array.from(box.children), footer = d.querySelector('.czp-map-footer');
                check(children.indexOf(S.mapEl) + 1 === children.indexOf(d.querySelector('#czp-map-detail')) &&
                    children.indexOf(d.querySelector('#czp-map-detail')) + 1 === children.indexOf(footer) &&
                    children.at(-1) === footer && footer.contains(key) && footer.contains(notes) &&
                    key.contains(d.querySelector('#czp-map-key')) && notes.contains(d.querySelector('#czp-map-notes')),
                    'footer/order: map then measured loss then the footer containing both text pins');
            }
            if (name === 'state') {
                let kept = !!(key && notes);
                if (kept) {
                    key.open = notes.open = true;
                    for (const [id, value] of [['czp-map-op', 'cz_SNZ'], ['czp-map-order', 'name'], ['czp-map-scale', '0.5']]) {
                        const before = S.renders.length, el = d.getElementById(id);
                        el.value = value; el.dispatchEvent(new w.Event('change', {bubbles: true})); await wait(40);
                        kept = kept && S.renders.length > before && key.open && notes.open;
                    }
                    for (const op of ['qubit_pairs.q1-2.macros.cz_SNZ', 'qubit_pairs.q3-4.macros.cz_GNZ']) {
                        const before = S.renders.length;
                        if (op.includes('q1-2')) S.root.dispatchEvent(new w.CustomEvent('czp:open', {detail: {op}}));
                        else d.querySelector('[data-op="' + op + '"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
                        await wait(40);
                        kept = kept && S.renders.length > before && key.open && notes.open;
                    }
                    const before = S.renders.length;
                    d.documentElement.setAttribute('data-theme', 'dark'); await wait(40);
                    kept = kept && S.renders.length > before && key.open && notes.open;
                    key.open = notes.open = false;
                    await w.CzMap.paint(S.root); kept = kept && !key.open && !notes.open;
                }
                check(kept, 'footer/state: open and closed state survive operation, columns, scale, selection and theme redraws');
            }
            if (name === 'wrapping') {
                const css = STATIC('style.css');
                check(/\.czp-map-footer\s*\{[^}]*flex-wrap:\s*wrap[^}]*justify-content:\s*flex-start[^}]*font-size:\s*0\.8rem[^}]*color:\s*var\(--pico-muted-color\)/.test(css) &&
                    /\.czp-map-key, \.czp-map-notes\s*\{[^}]*order:\s*1[^}]*flex:\s*0 0 100%[^}]*overflow-wrap:\s*anywhere/.test(css) &&
                    /\.czp-map-notes\s*\{[^}]*white-space:\s*pre-line/.test(css),
                    'footer/wrapping: muted captions share a row; bodies wrap below at section width');
            }
        } finally { S.close(); }
    }
}

(async () => {
    if (process.env.CZ_DERIVED_PIN) {
        const derived = JSON.parse(JSON.stringify(MAP));
        derived.segments[0].kind = 'lab flux model, re-anchored to f_01 [derived]';
        const P = await page({map: derived, loss: NO_LOSS, order: 'name'});
        try {
            check(segments(P.last()).find(s => s.pair === 'q10-2' && s.width > 2).dash === 'dash',
                  'a derived re-anchored value uses the estimate style');
            check(P.d.getElementById('czp-map-key').textContent.includes('derived'),
                  'the key identifies derived estimates');
        } finally { P.close(); }
        console.log(checks + ' derived assertions passed');
        return;
    }
    await footerPins(process.env.CZ_FOOTER_PIN);
    if (process.env.CZ_FOOTER_PIN) { console.log(checks + ' footer assertions passed'); return; }
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
    // v1.4.1 (A14 fix3 P2-1): a hidden 0-50 % scale -> the Loss scale select, so a column without a scan
    // and a scanned one read on one scale (the 0.5 case keeps the old numbers), and the scale is printed.
    const scaleSel1 = d.getElementById('czp-map-scale');
    for (const [order, scale] of [['name', '0.25'], ['f01', '0.5']]) {
        ord.value = order; scaleSel1.value = scale;
        await w.CzPlan.drawMap(P1.root);
        const t = P1.last().traces.find(x => x.meta === 'defect-bars'), column = order === 'name' ? 1 : 2;
        const bars = [];
        for (let k = 0; k + 1 < t.x.length; k += 3) bars.push([t.x[k], t.x[k + 1]]);
        const want = [0, null, .1, .3, .5, .8].map(p => .08 + .82 * Math.min(1, (p || 0) / +scale));
        check(bars.length === 6 && bars.every((b, i) => Math.abs((b[1] - b[0]) - want[i]) < 1e-12 &&
              Math.abs(b[0] - (column + .05)) < 1e-12) &&
              (scale !== '0.5' || want.every((v, i) => Math.abs(v - [.08, .08, .244, .572, .9, .9][i]) < 1e-12)) &&
              P1.last().layout.annotations.some(a => a.name === 'scale' && a.text === 'loss 0–' + Math.round(100 * scale) + ' %'),
              'defect width numerically follows peak loss on the Loss scale select (' + scale + '), including zero, missing and saturation after ' + order + ' ordering');
    }
    scaleSel1.value = '0.25';
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
    const ticks = P1.last().layout.xaxis.ticktext;
    check(ticks.length === 3 && ticks.every(t => !t.includes('<b>') && t.includes('&lt;b&gt;literal&lt;/b&gt;') && t.includes('&amp;')),
          'fix3 P2-7: the qubit names on the x axis are escaped like every hover (Plotly reads tick text as markup)');

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
    // The map answer of a picked operation name: the frozen default answer with that name on every
    // segment (the 5-qubit chip's default draws cz_unipolar: it has no macro named cz).
    const opMap = base => url => {
        const body = JSON.parse(JSON.stringify(base)), hit = /[?&]op=([^&]+)/.exec(url);
        if (hit) {
            const name = decodeURIComponent(hit[1]);
            body.op = name;
            body.segments.concat(body.refused || []).forEach(s => { s.operation = name; s.path = s.path.replace(/macros\.[^.]+$/, 'macros.' + name); });
        }
        return body;
    };
    // v1.4.1 (A14 fix3 P1-1): rows of the drawn default -> rows of the selected cz_SNZ; the map now follows
    // the selection (one refetch), so the rest of these pins read the map of the selected operation.
    const ops5 = Array.from(new Set(map5.segments.map(s => s.path.replace(/macros\.[^.]+$/, 'macros.cz_SNZ'))));
    const S5 = await page({map: opMap(map5), loss: loss5, sel: 'qubit_pairs.q3-4.macros.cz_SNZ', rows: ops5, width: 1100});
    const S13 = await page({map: map13, loss: loss13, width: 1100});
    const S21 = await page({map: map21, loss: loss21, width: 1000});
    const r5 = S5.last(), r13 = S13.last(), r21 = S21.last();
    // ---- fix3 P1-1: the map draws the operation the reader selected ----
    check(map5.op === null && map5.segments.every(s => s.operation === 'cz_unipolar') && S5.fetched.filter(u => u.startsWith('/cz-plan/map')).join() ===
          '/cz-plan/map,/cz-plan/map?op=cz_SNZ' && S5.d.getElementById('czp-map-op').value === 'cz_SNZ' &&
          segments(r5).every(s => s.path.endsWith('.cz_SNZ')) && named(r5, 'selected:q3-4').length === 3 &&
          !S5.d.getElementById('czp-map-key').textContent.includes('Outline:'),
          'P1-1: the selected cz_SNZ is not what the default map draws (cz_unipolar): the map switches to it with one refetch and outlines it');
    const SF = await page({map: opMap(map5), loss: loss5, sel: 'qubit_pairs.q3-4.macros.cz_SNZ', rows: ops5, width: 1100});
    const fOp = SF.d.getElementById('czp-map-op');
    fOp.value = 'cz_GNZ'; fOp.dispatchEvent(new SF.w.Event('change', {bubbles: true})); await wait(30);
    const rf = SF.last(), bf = rf.traces.find(t => t.meta === 'bars');
    check(SF.mapFetches() === 3 && fOp.value === 'cz_GNZ' && segments(rf).every(s => s.path.endsWith('.cz_GNZ')) &&
          SF.d.getElementById('czp-map-key').textContent.includes('Outline: the map shows cz_GNZ for q3-4; selected below: cz_SNZ.') &&
          bf.text[bf.customdata.findIndex(c => c[1] === 'q3-4')].includes('selected below: cz_SNZ (this map shows cz_GNZ)'),
          'P1-1: a name the reader picks on the map stays; the key and the outline\'s hover name the drawn and the selected operation');
    SF.root.querySelector('tr[data-op="qubit_pairs.q1-2.macros.cz_SNZ"]').dispatchEvent(new SF.w.MouseEvent('click', {bubbles: true})); await wait(30);
    check(SF.mapFetches() === 4 && fOp.value === 'cz_SNZ' && segments(SF.last()).every(s => s.path.endsWith('.cz_SNZ')) &&
          named(SF.last(), 'selected:q1-2').length === 3 && !SF.d.getElementById('czp-map-key').textContent.includes('Outline:'),
          'P1-1: the next selection below brings the map to its operation again');
    // a selection made while the map could not be drawn asks the next map to follow it; a name the reader
    // picks before that map lands wins over it
    SF.state.map = {ok: false, error: 'temporary failure'};
    await SF.w.CzPlan.drawMap(SF.root);
    SF.root.querySelector('tr[data-op="qubit_pairs.q2-3.macros.cz_SNZ"]').dispatchEvent(new SF.w.MouseEvent('click', {bubbles: true})); await wait(10);
    SF.state.map = opMap(map5);
    fOp.value = 'cz_GNZ'; fOp.dispatchEvent(new SF.w.Event('change', {bubbles: true})); await wait(30);
    check(fOp.value === 'cz_GNZ' && segments(SF.last()).every(s => s.path.endsWith('.cz_GNZ')) &&
          SF.fetched.filter(u => u.startsWith('/cz-plan/map')).at(-1) === '/cz-plan/map?op=cz_GNZ',
          'P1-1: a name picked on the map after a failed draw stays; the pending follow does not override it');
    SF.close();
    const SM = await page({map: opMap(map5), loss: loss5, sel: 'qubit_pairs.q3-4.macros.cz_custom', rows: ['qubit_pairs.q3-4.macros.cz_custom'], width: 1100});
    const bm = SM.last().traces.find(t => t.meta === 'bars');
    check(SM.mapFetches() === 1 && SM.d.getElementById('czp-map-op').value === '' &&
          SM.d.getElementById('czp-map-key').textContent.includes('Outline: the map shows cz_unipolar for q3-4; selected below: cz_custom.') &&
          bm.text[bm.customdata.findIndex(c => c[1] === 'q3-4')].includes('selected below: cz_custom (this map shows cz_unipolar)') &&
          segments(SM.last()).filter(s => s.pair === 'q3-4').every(s => s.text.includes('selected below: cz_custom')),
          'P1-1: a selected name the map cannot draw: no refetch; the key and the hovers of the outlined pair name both');
    const defaultKey = SM.d.getElementById('czp-map-key').textContent;
    SM.close();
    const opt0 = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_cz_plan.html'), 'utf8');
    // v1.4.1: long closed label -> compact label with the full default rule on hover.
    check(opt0.includes('<option value="">cz per pair</option>') && opt0.includes('title="cz per pair: each pair\'s cz, else its first CZ operation"') &&
          defaultKey.includes("cz per pair = each pair's cz, else its first CZ operation"),
          'P1-1: the default picker entry says what it draws when a pair has no cz');
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
    // v1.4.1 (A14 fix3): label(...).text -> (label(...) || {}).text: a label may now go to hover only, and a
    // missing one must fail this assertion, not throw
    check(one.length === 1 && (label(r5, 'q1-2') || {}).text === (one[0].frequency_hz / 1e9).toFixed(3), 'a bar carries the moving qubit\'s operating frequency (GHz, 3 decimals)');
    const both = Object.keys(map13.pairs).find(p => mover(map13, p).length === 2);
    const ctl = both && mover(map13, both).find(s => s.role === 'control');
    check(ctl && (label(r13, both) || {}).text === ctl.qubit + ' ' + (ctl.frequency_hz / 1e9).toFixed(3), 'both members move: the label names its owner (the control)');
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
    // v1.4.1 (A14 fix3 P1-3): "a short bar's label is lifted 12 px along its upward normal" -> placed where it
    // hides no other bar, node or label (the old lift put it ON the row above's bar); pinned by geometry below.
    // ---- fix3 P1-3: the drawing follows the rows; bar labels never hide another bar or a node ----
    const drawnPx = r => r.layout.yaxis2.domain[1] * (r.layout.height - r.layout.margin.t - r.layout.margin.b);
    check(near(drawnPx(r21), 220, 0.5) && near(drawnPx(r13), Math.max(150, 44 * (r13.layout.yaxis2.range[1] - r13.layout.yaxis2.range[0] - 0.2)), 0.5) &&
          near(drawnPx(r5), 150, 0.5), 'P1-3: the drawing is 44 px per row of the chip (at least 150 px): 5 rows -> 220 px, the 5-qubit chain 150 px');
    // an independent check of the drawn geometry: every bar sampled each px, every node a disc, every
    // label the box the page sizes it with, at the real figure widths of a 1280 and a 1600 px window
    function labelHits(S, r) {
        const W = S.width - 80, n = r.layout.xaxis.ticktext.length, colPx = W / n;
        const span = r.layout.yaxis2.range[1] - r.layout.yaxis2.range[0], rowPx = drawnPx(r) / span, y0 = r.layout.yaxis2.range[0];
        const P = (x, y) => [x * colPx, (y - y0) * rowPx];
        const nodes = r.traces.find(t => t.meta === 'nodes'), bars = named(r, 'bar:'), out = [];
        const labels = (r.layout.annotations || []).filter(a => String(a.name || '').startsWith('bar-label:'));
        labels.forEach(a => {
            const own = a.name.slice(10), [cx, cy] = P(a.x, a.y), el = S.d.createElement('i');
            el.innerHTML = a.text;
            const sz = S.w.CzMap.labelSize(el.textContent);
            const bx = {x0: cx + a.xshift - sz.w / 2, x1: cx + a.xshift + sz.w / 2, y0: cy + a.yshift - sz.h / 2, y1: cy + a.yshift + sz.h / 2};
            nodes.x.forEach((x, k) => {
                const [nx, ny] = P(x, nodes.y[k]);
                const d = Math.hypot(Math.max(bx.x0 - nx, 0, nx - bx.x1), Math.max(bx.y0 - ny, 0, ny - bx.y1));
                if (d < 12) out.push(own + ' on node ' + nodes.text[k]);
            });
            bars.forEach(b => {
                if (b.name.slice(4) === own) return;
                const [ax, ay] = P(b.x0, b.y0), [qx, qy] = P(b.x1, b.y1), len = Math.hypot(qx - ax, qy - ay), half = b.line.width / 2;
                for (let s = 0; s <= len; s += 1) {
                    const x = ax + (qx - ax) * s / len, y = ay + (qy - ay) * s / len;
                    if (x > bx.x0 - half && x < bx.x1 + half && y > bx.y0 - half && y < bx.y1 + half) { out.push(own + ' on bar ' + b.name.slice(4)); break; }
                }
            });
        });
        return {hits: out, labels: labels.length};
    }
    for (const [m, l, nm, nPairs] of [[map21, loss21, '21Q', 32], [map13, loss13, '13Q', 16]]) {
        // the map's measured width and top in real Chrome: 919 px at 181 (1280x800), 1237 px at 194 (1600x950)
        for (const [W, Hv, top] of [[919, 800, 181], [1237, 950, 194]]) {
            const S = await page({map: m, loss: l, width: W, innerHeight: Hv, top});
            const res = labelHits(S, S.last());
            check(res.hits.length === 0 && res.labels >= nPairs - (W > 1000 ? 1 : 2),
                  'P1-3: ' + nm + ' at a ' + (W > 1000 ? 1600 : 1280) + ' px window: ' + res.labels + ' bar labels, none on another bar or a node ' + res.hits.join(', '));
            S.close();
        }
    }
    // the row-pitch gate: below ROW_LABEL_MIN_PX a short bar's label is hover only, a long one keeps its own
    const CM = S21.w.CzMap, pt = (X, Y) => ({X, Y});
    const gate = rp => CM.placeLabels([{pair: 'short', text: '5.287', a: pt(100, 30), b: pt(130, 30 + rp)},
                                       {pair: 'long', text: '5.430', a: pt(100, 30), b: pt(330, 30)}],
                                      [], [pt(100, 30), pt(130, 30 + rp), pt(330, 30)], {rowPx: rp, x0: 0, x1: 600, y0: 0, y1: 300});
    const g30 = gate(30), g44 = gate(44);
    check(g30.short === null && g30.long && g30.long.dx === 0 && g30.long.dy === 0 && g44.short && Math.hypot(g44.short.dx, g44.short.dy) > 0,
          'P1-3: under a 36 px row pitch a short bar\'s label is hover only; above it the label sits beside its bar; a long bar keeps its label on itself');
    // the 13-qubit chip in a narrower window (an 800 px map): q7-q8 is one column long and its "q7 5.275"
    // fits beside it nowhere -> hover only
    const keyHidden = await page({map: map13, loss: loss13, width: 800, innerHeight: 800, top: 181});
    const hid = keyHidden.last(), hidBar = hid.traces.find(t => t.meta === 'bars');
    check(!label(hid, 'q7-8') && named(hid, 'bar:q7-8')[0].line.width > 2 &&
          hidBar.text[hidBar.customdata.findIndex(c => c[1] === 'q7-8')].includes('q7 5.275 GHz') &&
          keyHidden.d.getElementById('czp-map-key').textContent.includes('a bar without a label: hover it') &&
          !S21.d.getElementById('czp-map-key').textContent.includes('without a label'),
          'P1-3: a label with no free spot goes to the hover of its bar and the key says so (and only then)');
    keyHidden.close();

    // ---- fix3 P1-4: segments that coincide in one column sit side by side, every colour visible ----
    function clusters(r) {
        const H = r.layout.height - r.layout.margin.t - r.layout.margin.b, dom = r.layout.yaxis.domain, rng = r.layout.yaxis.range;
        const pxPerGHz = H * (dom[1] - dom[0]) / (rng[1] - rng[0]);
        const segs = segments(r), bad = [];
        let close = 0;
        segs.forEach((a, i) => segs.forEach((b, j) => {
            if (j <= i || Math.floor(a.x0) !== Math.floor(b.x0) || Math.abs(a.y - b.y) * pxPerGHz > 6) return;
            close++;
            if (a.x0 < b.x1 && b.x0 < a.x1) bad.push(a.pair + '/' + b.pair + ' in column ' + r.layout.xaxis.ticktext[Math.floor(a.x0)]);
        }));
        return {bad, close, pxPerGHz};
    }
    const cl21 = clusters(r21);
    check(r21.layout.yaxis.autorange === false && cl21.close >= 16 && cl21.bad.length === 0 &&
          r21.traces.findIndex(t => t.meta === 'idle') < r21.traces.findIndex(t => t.meta === 'seg'),
          'P1-4: on the 21-qubit map every pair of segments within 6 px in one column sits side by side (' + cl21.close + ' such pairs), over the idle dot ' + cl21.bad.join(', '));
    const p21 = r21.traces.find(t => t.meta === 'partners');
    const names = t => t.split(/<br>| /);
    const multi = p21.text.filter(t => names(t).length >= 3);
    // every partner label as the box it prints in (9 px font; anchored at its segment's top right or top left)
    const colPx21 = (1000 - 80) / 21, pbox = k => {
        const lines = p21.text[k].split('<br>'), h = 11 * lines.length;
        const w = Math.max(...lines.map(l => S21.w.CzMap.partnerWidth(l))), x = p21.x[k] * colPx21, y = (p21.y[k] - r21.layout.yaxis.range[0]) * cl21.pxPerGHz;
        return p21.textposition[k] === 'top left' ? {x0: x - w, x1: x, y0: y, y1: y + h} : {x0: x, x1: x + w, y0: y, y1: y + h};
    };
    const overprint = [];
    p21.x.forEach((x, k) => p21.x.forEach((x2, k2) => {
        if (k2 <= k) return;
        const a = pbox(k), b = pbox(k2);
        if (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1) overprint.push(p21.text[k] + '|' + p21.text[k2]);
    }));
    const wide = p21.text.map((t, k) => k).filter(k => {
        const w = Math.max(...p21.text[k].split('<br>').map(l => S21.w.CzMap.partnerWidth(l)));
        return names(p21.text[k]).length > 1 && w > 0.76 * colPx21 + 0.5;
    });
    check(multi.length >= 2 && overprint.length === 0 && wide.length === 0 && p21.text.some(t => t.includes('<br>')),
          'P1-4: one partner label lists every partner of a shared row ("' + multi[0] + '"), wrapped to its column; no two partner labels print over each other ' + overprint.join(', '));

    // v1.4.1: fixed 40 px cutoff -> compact names when their boxes fit the column.
    const pt5 = r5.traces.find(t => t.meta === 'partners');
    const s12 = segments(r5).find(s => s.pair === 'q1-2' && near(s.x0, 0.12));
    check(pt5 && pt5.x.some((x, k) => near(x, 0.88) && near(pt5.y[k], s12.y) && pt5.text[k] === 'q2'), 'each segment names its partner at its right end');
    S21.width = 900; await S21.w.CzMap.paint(S21.root);
    check(S21.last().traces.some(t => t.meta === 'partners' && t.text.some(s => /[A-D]\d/.test(s) && !/q[A-D]/.test(s))), 'columns under 40 px: compact partner names');
    S21.width = 1000; await S21.w.CzMap.paint(S21.root);
    check(S21.last().traces.some(t => t.meta === 'partners'), 'columns of 40 px and more: partner names');
    for (const width of [919, 1237]) {
        S21.width = width; await S21.w.CzMap.paint(S21.root);
        const r = S21.last(), p = r.traces.find(t => t.meta === 'partners');
        check(p && p.text.length >= 35, '21 columns retain partner labels at map width ' + width);
        const col = (width - 80) / 21, scale = clusters(r).pxPerGHz;
        const boxes = p.text.map((text, i) => {
            const lines = text.split('<br>'), w = Math.max(...lines.map(s => S21.w.CzMap.partnerWidth(s)));
            const x = p.x[i] * col, y = p.y[i] * scale;
            return {x0: x - (p.textposition[i] === 'top left' ? w : 0), x1: x + (p.textposition[i] === 'top left' ? 0 : w), y0: y, y1: y + lines.length * 11, w};
        });
        check(boxes.every((a, i) => a.w <= 0.76 * col && boxes.every((b, j) => j <= i || !(a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1))),
              'computed partner boxes fit without overlap at viewport ' + (width === 919 ? 1280 : 1600));
    }
    S21.width = 1000; await S21.w.CzMap.paint(S21.root);

    S21.width = 240; await S21.w.CzMap.paint(S21.root);
    check(!S21.last().traces.some(t => t.meta === 'partners'), 'a partner name wider than its column stays in hover');
    S21.width = 1000; await S21.w.CzMap.paint(S21.root);

    function rgb(c) {
        if (c.startsWith('#')) { let s = c.slice(1); if (s.length === 3) s = s.split('').map(x => x + x).join(''); return [0, 2, 4].map(i => parseInt(s.slice(i, i + 2), 16)); }
        return c.match(/[\d.]+/g).map(Number);
    }
    function ratio(c, bg) {
        const b = rgb(bg), f = rgb(c), a = f.length > 3 ? f[3] : 1;
        const lum = v => v.map(x => x / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4).reduce((s, x, i) => s + x * [.2126, .7152, .0722][i], 0);
        const l = lum(f.slice(0, 3).map((x, i) => x * a + b[i] * (1 - a))), l2 = lum(b);
        return (Math.max(l, l2) + .05) / (Math.min(l, l2) + .05);
    }
    for (const theme of ['light', 'dark']) {
        const dark = theme === 'dark', bg = dark ? '#181c25' : '#ffffff', ink = dark ? '#d0d5de' : '#373c44', muted = dark ? '#98a1b3' : '#646b79';
        S5.d.documentElement.style.cssText = '--pico-color:' + ink + ';--pico-muted-color:' + muted + ';--pico-card-background-color:' + bg;
        const n = S5.renders.length, fetches = S5.fetched.length;
        S5.d.documentElement.setAttribute('data-theme', theme); await wait(40);
        const r = S5.last();
        check(S5.renders.length > n && S5.fetched.length === fetches && r.traces.find(t => t.meta === 'nodes').marker.color === bg &&
              r.layout.annotations.filter(a => String(a.name).startsWith('bar-label:')).every(a => a.bgcolor === bg && (a.opacity || 1) === 1),
              'theme toggle redraws marks, node and label backings without requests: ' + theme);
        check(S5.w.CzMap.pairColors().slice(0, 8).every(c => ratio(c, bg) >= 4.5), 'all pair hues clear text contrast in ' + theme);
        check(['loss-line', 'loss-source'].every(meta => { const t = r.traces.find(t => t.meta === meta); return t && ratio(t.line.color, bg) >= 3 && (t.opacity || 1) === 1; }),
              'scan and reference boundaries clear mark contrast in ' + theme);
        check(r.traces.filter(t => t.meta === 'connector').every(t => (t.opacity || 1) === 1 && ratio(t.line.color, bg) >= 3), 'connector contrast in ' + theme);
        check(r.traces.find(t => t.meta === 'partners').textfont.color === ink && ratio(ink, bg) >= 4.5 &&
              r.traces.find(t => t.meta === 'loss-fill').fillcolor === 'rgba(128,128,128,0.16)', 'partner text keeps contrast over a pale scan fill in ' + theme);
        S13.d.documentElement.style.cssText = S5.d.documentElement.style.cssText;
        S13.d.documentElement.setAttribute('data-theme', theme); await wait(40);
        await S13.w.CzMap.paint(S13.root);
        const defect = S13.last().traces.find(t => t.meta === 'defect-bars');
        check((defect.opacity || 1) === 1 && ratio(defect.line.color, bg) >= 3, 'stored-only marks keep their contrast: ' + theme);
        check(r.layout.shapes.filter(s => s.type === 'rect').every(s => (s.opacity || 1) === 1 && ratio(s.fillcolor, bg) >= 3), 'safe strips keep their contrast: ' + theme);
    }
    S5.d.documentElement.style.cssText = '';
    S5.d.documentElement.removeAttribute('data-theme'); await wait(40);
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
    // v1.4.1 (A14 fix3 P2-6): "stored defects from run #1390" -> "equal run #1390's": the match is by content.
    check(n5.includes("q4: stored defects equal run #1390's (dotted), newest scan #1909.") &&
          n5.includes('q1: source of the stored defects not in the data folder.') && !n5.some(l => l.startsWith('q3:')),
          'the note names a qubit only when something is off (source run, no match)');
    // v1.4.1 (A14 fix3 P1-2): "axis of run #1388 predates 2 flux-model inputs changed (run #1897)" in the qubit's line
    // -> one merged line per change, worded by what the ledger proves (#1897 only carried the values first).
    check(n5.includes("Axis check: freq_vs_flux_01_quad_term, z.joint_offset changed after the scan of q5 #1388 (first seen in run #1897's saved state).") &&
          !n5.some(l => /\(run #1897\)|changed by run #1897/.test(l)),
          'P1-2: the ledger\'s staleness of a curve\'s axis names the run only as the first saved state that carried the change');
    const n21 = S21.d.getElementById('czp-map-notes').textContent.split('\n');
    const zl = n21.filter(l => l.startsWith('Axis check: z.joint_offset '));
    check(zl.length === 1 && zl[0] === 'Axis check: z.joint_offset changed after the scans of qA3 #653, qA1 #824, qB4 #1936, qA4 #1667, ' +
          "qA2 #1822 and #676, qB2 #1725, qA5 #1825 and #432 (first seen in run #3450's saved state)." &&
          !n21.some(l => /^q[A-D]\d: .*predate/.test(l) || /\(run #3450\)|changed by run #3450/.test(l)),
          'P1-2: nine identical axis lines (same field, same change) read as one, listing the qubits and their scans');
    const proven21 = JSON.parse(JSON.stringify(loss21));
    Object.values(proven21.staleness.qubits).forEach(ls => ls.forEach(s => { s.changed_provenance = 'run_proven'; s.changed_words = 'changed by run #3450'; }));
    const SP = await page({map: map21, loss: proven21, width: 1000});
    check(SP.d.getElementById('czp-map-notes').textContent.split('\n').some(l => l.startsWith('Axis check: z.joint_offset changed by run #3450 after the scans of qA3 #653,')),
          'P1-2: a run the ledger proves wrote the change is named as its writer');
    SP.close();
    const det = S5.d.getElementById('czp-map-detail');
    check(!det.open && S5.d.getElementById('czp-map-detail-summary').textContent.includes('5 of 5 with a scan') &&
          det.textContent.includes('newest scan run #1909') && det.textContent.includes('dotted outline run #1390'),
          'the full loss detail sits in a collapsed box under the map');
    // ---- fix3 P2-6: a content match is worded as one; a run nobody could tie to the chip says so ----
    const src5 = r5.traces.find(t => t.meta === 'loss-source');
    check(S5.d.getElementById('czp-map-key').textContent.includes('dotted = the run whose defects equal the stored list (matched by content)') &&
          src5.hovertemplate.includes('(the run whose defects equal the stored list)') && !/came from/.test(S5.d.getElementById('czp-map-key').textContent + src5.hovertemplate),
          'P2-6: the key and the outline hover say "the run whose defects equal the stored list", not where they came from');
    const unchecked5 = JSON.parse(JSON.stringify(loss5));
    unchecked5.curves.q4.scan.identity = 'unchecked';
    const SU = await page({map: map5, loss: unchecked5, width: 1100});
    const ru = SU.last(), lu = ru.traces.find(t => t.meta === 'loss-line'), q4u = ru.layout.xaxis.ticktext.indexOf('q4');
    const q4t = lu.text.filter((t, k) => t && Math.floor(lu.x[k]) === q4u), q2t = lu.text.filter((t, k) => t && t.startsWith('q2 '));
    check(q4t.length && q4t.every(t => t.includes('chip identity unchecked')) && q2t.length && !q2t.some(t => t.includes('identity')) &&
          SU.d.getElementById('czp-map-notes').textContent.split('\n').includes('Chip identity unchecked for the loss runs of q4 (no change history holds them and one side declares no chip name).') &&
          SU.d.getElementById('czp-map-detail-body').textContent.includes('chip identity unchecked') &&
          !S5.d.getElementById('czp-map-notes').textContent.includes('identity'),
          'P2-6: a run judged by neither the history nor a chip name says so in its hover, the notes and the detail (and only that run)');
    SU.close();

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
    SA.holdLoss = false;
    for (let k = 0; k < SA.w.CzMap.RETRY_N && SA.w._timers.length; k++) { SA.w._timers.shift()(); await wait(15); }
    const gaveUp = SA.d.getElementById('czp-map-notes').textContent;
    check(SA.fetched.filter(u => u.startsWith('/cz-plan/loss')).length === SA.w.CzMap.RETRY_N + 1 && SA.w._timers.length === 0 &&
          gaveUp.includes('The change history was still being prepared after 60 s; the axis check of the loss scans was not done') &&
          !gaveUp.includes('follows'), 'fix3 P2-5: when the re-asks run out the note says the check was not done, not that it follows');
    SA.close();
    [S5, S13, S21, SV].forEach(S => S.close());
    console.log(checks + ' assertions passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
