/* Chip Status > CZ gate plan > Chip frequency map (bundle 'czplan', window.CzMap).
 *
 * After arXiv:2112.03708v1 p.11, Fig. 6c: one column per qubit on a shared frequency axis, and the
 * chip drawn below it with every qubit joined to its own column by a leader line. In each
 * column: the measured population loss of the newest scan (gray, /cz-plan/loss), the
 * outline of the run the stored defects came from when that is another run, the stored
 * defects, the safe intervals, the idle frequency (dot) and each pair's operating point as a
 * short segment in the pair's colour. In the drawing every pair is a bar in that same colour,
 * labelled with the moving qubit's operating frequency.
 *
 * Colour = a proper edge colouring (greedy, natural pair order): pairs that share a qubit
 * never share a colour, so inside one column a colour names one neighbour. It is NOT the
 * paper's time step. The physics lives in core/cz_gate_plan.py and core/cz_loss.py; this
 * file only draws. Render guards: a fetch token, a paint token, the element's render epoch
 * (purge on empty), and one click/hover binding per Plotly emitter.
 */
(function () {
    'use strict';

    var fseq = 0, pseq = 0;
    var DRAW_PX = 150, GAP_PX = 26, MIN_H = 460, MAX_H = 640;
    var MARGIN = { l: 64, r: 16, t: 30, b: 6 };
    var LOSS_W = 0.9;          // a column's loss scale spans 90 % of the column
    var LABEL_GATE_PX = 40;    // partner names only when a column is at least this wide
    var SHORT_BAR_PX = 60;     // a bar shorter than this carries its label above it
    /* Pair colours: green, violet, magenta, yellow first -- the four-hue subset of the
       docs/93 categorical hues that passes the dataviz validator on ALL pairs in both themes
       (CVD dE 6.9, normal-vision dE 19.3; light surface #ffffff, dark #181c25), since any
       two colours can meet at one qubit. Blue comes last: it is the selection accent. A
       fifth colour (21-qubit grid) cannot pass all pairs with any hue; aqua keeps the best
       normal-vision distance (>= 11.9) and the partner name at each segment's end is the
       secondary encoding. Light-theme magenta/yellow sit below 3:1 on white: the bar and
       partner labels are the relief. */
    var PAIR_LIGHT = ['#008300', '#4a3aa7', '#e87ba4', '#eda100', '#1baf7a', '#e34948', '#eb6834', '#2a78d6'];
    var PAIR_DARK = ['#008300', '#9085e9', '#d55181', '#c98500', '#199e70', '#e66767', '#d95926', '#3987e5'];
    var MAP_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#17becf',
                      '#bcbd22', '#7f7f7f'];
    var KEY = 'solid fitted model, dashed quad-term estimate, thin stays at idle, diamond planned; ' +
              'colour = pair (unique around each qubit, not a time step); bar label = operating frequency (GHz)';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function cssVar(name, fallback) {
        try {
            var v = getComputedStyle(document.documentElement).getPropertyValue(name);
            return (v && v.trim()) || fallback;
        } catch (e) { return fallback; }
    }
    function isDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
    function pairColors() {
        var P = (isDark() ? PAIR_DARK : PAIR_LIGHT).slice();
        return P.concat(MAP_COLORS.filter(function (c) { return P.indexOf(c) < 0; }));
    }
    function plan() {
        var P = (window.PlotTheme && window.PlotTheme.palette()) || ['#4f9cf9', '#f97316', '#22c55e'];
        return P[2] || '#22c55e';
    }
    function layout(over) {
        var base = window.PlotTheme ? window.PlotTheme.houseLayout({}) : {};
        var L = window.PlotTheme ? window.PlotTheme.houseLayout(over) : over;
        ['xaxis', 'yaxis', 'yaxis2'].forEach(function (k) {
            if (over[k]) L[k] = Object.assign({}, base[k] || base.yaxis || {}, over[k]);
        });
        return L;
    }
    function config() {
        return window.PlotTheme ? window.PlotTheme.houseConfig({}) : { displaylogo: false, responsive: true };
    }
    function ghz(v) { return v == null ? null : v / 1e9; }
    function f3(v) { return v == null ? '?' : (v / 1e9).toFixed(3); }
    function fmtMHz(v, sign) { return v == null ? '—' : (sign && v > 0 ? '+' : '') + (v / 1e6).toFixed(1) + ' MHz'; }
    function pct(v) { return v == null ? '—' : (100 * v).toFixed(1) + ' %'; }
    function finite(v) { return typeof v === 'number' && isFinite(v); }

    /* Natural order: q2 before q10, qA2-qA1 before qA10-qA1. */
    function natCmp(a, b) {
        var ax = String(a).match(/\d+|\D+/g) || [], bx = String(b).match(/\d+|\D+/g) || [];
        for (var i = 0; i < Math.min(ax.length, bx.length); i++) {
            var x = ax[i], y = bx[i];
            if (x === y) continue;
            var nx = /^\d/.test(x), ny = /^\d/.test(y);
            if (nx && ny) return (+x) - (+y) || x.length - y.length;
            return x < y ? -1 : 1;
        }
        return ax.length - bx.length;
    }
    function pairOf(path) {
        var m = /^qubit_pairs\.(.+)\.macros\./.exec(String(path || ''));
        return m ? m[1] : null;
    }
    function members(p) {
        return [p.control_path || p.control, p.target_path || p.target].filter(Boolean);
    }

    /* Proper edge colouring: greedy over the pairs in natural order; a pair takes the
       smallest colour index no pair sharing one of its qubits holds. Every pair of the chip
       is coloured (with or without the drawn operation) so a colour follows its pair when
       the operation picker changes. */
    function edgeColours(pairs) {
        var used = {}, out = {};
        Object.keys(pairs || {}).sort(natCmp).forEach(function (id) {
            var ms = members(pairs[id] || {}), taken = {};
            ms.forEach(function (q) { (used[q] || []).forEach(function (c) { taken[c] = true; }); });
            var c = 0;
            while (taken[c]) c++;
            out[id] = c;
            ms.forEach(function (q) { (used[q] = used[q] || []).push(c); });
        });
        return out;
    }

    /* Where each qubit sits in the drawing and its column in chip order.
       physical: shear -- the grid axis with more distinct positions is primary (tie -> x);
         key = rank(primary) * n_distinct(secondary) + rank(secondary), ranks over the
         chip's own coordinates; the node sits at its column centre, its height = the
         secondary rank (the chip's row 0 at the bottom, as every SM chip map draws it), so
         the leaders are vertical. A chip longer in y is turned a quarter clockwise.
       logical: columns by x-rank of TopoGraph.layoutFor's positions, nodes at those
         positions scaled, slanted leaders, LOGICAL_LAYOUT_NOTE on the figure.
       none: no drawing. */
    function chipLayout(qubits, pairs) {
        var byName = qubits.map(function (q) { return q.path; });
        var TG = window.TopoGraph;
        if (!TG || !TG.layoutFor) return { mode: 'none', order: byName, node: {}, ymax: 0,
                                           why: 'the topology helper did not load' };
        var nodes = qubits.map(function (q) { return { id: q.path, grid_location: q.grid_location }; });
        var edges = Object.keys(pairs || {}).map(function (k) {
            return { source: pairs[k].control_path, target: pairs[k].target_path };
        }).filter(function (e) { return e.source && e.target; });
        var lay = TG.layoutFor(nodes, edges);
        var name = {};
        qubits.forEach(function (q) { name[q.path] = q.name; });
        if (lay.mode === 'physical') {
            var xs = [], us = [];
            qubits.forEach(function (q) {
                var p = lay.positions[q.path];
                if (xs.indexOf(p.col) < 0) xs.push(p.col);
                if (us.indexOf(-p.row) < 0) us.push(-p.row);     // up = -screen row
            });
            xs.sort(function (a, b) { return a - b; });
            us.sort(function (a, b) { return a - b; });
            var primaryX = xs.length >= us.length;
            var nS = primaryX ? us.length : xs.length;
            var keyed = qubits.map(function (q) {
                var p = lay.positions[q.path], rx = xs.indexOf(p.col), ru = us.indexOf(-p.row);
                var prim = primaryX ? rx : ru, sec = primaryX ? ru : xs.length - 1 - rx;
                return { path: q.path, key: prim * nS + sec, y: sec };
            });
            keyed.sort(function (a, b) { return a.key - b.key || natCmp(name[a.path], name[b.path]); });
            var node = {};
            keyed.forEach(function (k, i) { node[k.path] = { x: i + 0.5, y: k.y }; });
            return { mode: 'physical', order: keyed.map(function (k) { return k.path; }), node: node,
                     ymax: Math.max(0, nS - 1), primary: primaryX ? 'x' : 'y' };
        }
        if (lay.mode === 'logical') {
            var ids = qubits.map(function (q) { return q.path; }).filter(function (id) { return lay.positions[id]; });
            var minC = Infinity, maxC = -Infinity, maxR = -Infinity, minR = Infinity;
            ids.forEach(function (id) {
                var p = lay.positions[id];
                minC = Math.min(minC, p.col); maxC = Math.max(maxC, p.col);
                minR = Math.min(minR, p.row); maxR = Math.max(maxR, p.row);
            });
            var order = qubits.map(function (q) { return q.path; }).sort(function (a, b) {
                var pa = lay.positions[a], pb = lay.positions[b];
                if (!pa || !pb) return (!pa) - (!pb) || natCmp(name[a], name[b]);
                return pa.col - pb.col || pb.row - pa.row || natCmp(name[a], name[b]);
            });
            var n = order.length, nodeL = {};
            ids.forEach(function (id) {
                var p = lay.positions[id];
                nodeL[id] = { x: maxC > minC ? 0.5 + (p.col - minC) / (maxC - minC) * (n - 1) : n / 2,
                              y: maxR - p.row };
            });
            return { mode: 'logical', order: order, node: nodeL, ymax: Math.max(0, maxR - minR),
                     note: TG.LOGICAL_LAYOUT_NOTE };
        }
        return { mode: 'none', order: byName, node: {}, ymax: 0,
                 why: 'the chip declares no complete grid positions and no pairs' };
    }

    function columnOrder(qubits, order, lay) {
        var byPath = {};
        qubits.forEach(function (q) { byPath[q.path] = q; });
        if (order === 'f01') {
            return qubits.slice().sort(function (a, b) {
                return (a.f01_hz == null) - (b.f01_hz == null) || (a.f01_hz || 0) - (b.f01_hz || 0);
            });
        }
        if (order === 'chip' && lay.mode !== 'none') {
            return lay.order.map(function (p) { return byPath[p]; }).filter(Boolean);
        }
        return qubits.slice();
    }

    /* Figure height: the drawing must be visible without scrolling, so the figure takes
       what is left of the viewport below its own top (clamped). Top = the figure's position
       in the unscrolled page (the scrolling ancestors' offsets added back). */
    function figureTop(el) {
        var top = 0;
        try { top = el.getBoundingClientRect().top; } catch (e) { return 0; }
        for (var a = el.parentElement; a; a = a.parentElement) top += a.scrollTop || 0;
        return top + (window.pageYOffset || 0);
    }
    function mapHeight(el) {
        var vh = window.innerHeight || document.documentElement.clientHeight || 800;
        return Math.round(Math.max(MIN_H, Math.min(MAX_H, vh - figureTop(el) - 40)));
    }

    function mapEmpty(el, text) {
        el.__plotlyRenderEpoch = (el.__plotlyRenderEpoch || 0) + 1;
        delete el._czpMapClick;
        delete el._czpOverlay;
        if (window.PlotHost) window.PlotHost.purgeWithin(el);
        el.innerHTML = '<p class="muted zline-empty">' + esc(text) + '</p>';
    }

    function scaleOf(root) {
        var s = root && root.querySelector('#czp-map-scale'), v = s ? parseFloat(s.value) : NaN;
        return v > 0 ? v : 0.25;
    }

    /* The defects of the run the stored list came from (they carry strong / edge_truncated;
       the stored summary does not). */
    function sourceDefects(entry) {
        var mk = entry && entry.marker;
        if (!mk || (mk.status !== 'match' && mk.status !== 'ambiguous')) return [];
        var c = mk.same_as_scan ? entry.scan : entry.marker_curve;
        return (c && c.defects) || [];
    }

    /* One curve's pieces in column i as fill polygons (anchored at the column's left edge,
       one polygon per run of finite samples -- two pieces are never joined), the hover line,
       and the clip ticks where the loss runs past the scale. */
    function curveGeometry(c, i, scale, q, out) {
        var left = i;
        function xOf(l) { return left + LOSS_W * Math.min(1, Math.max(0, l / scale)); }
        (c.pieces || []).forEach(function (pc) {
            var run = [];
            function flush() {
                if (run.length) {
                    out.fillX.push(left);
                    out.fillY.push(run[0].f);
                    run.forEach(function (s) { out.fillX.push(xOf(s.l)); out.fillY.push(s.f); });
                    out.fillX.push(left);
                    out.fillY.push(run[run.length - 1].f);
                    out.fillX.push(null); out.fillY.push(null);
                    run.forEach(function (s) {
                        out.lineX.push(xOf(s.l)); out.lineY.push(s.f); out.lineT.push(s.t);
                    });
                    out.lineX.push(null); out.lineY.push(null); out.lineT.push(null);
                    // one tick per stretch past the scale, at its peak
                    var k = 0;
                    while (k < run.length) {
                        if (run[k].l <= scale) { k++; continue; }
                        var top = k;
                        while (k < run.length && run[k].l > scale) { if (run[k].l > run[top].l) top = k; k++; }
                        out.clipX.push(left + LOSS_W + 0.02); out.clipY.push(run[top].f);
                        out.clipT.push(esc(q.name) + ' loss ' + pct(run[top].l) + ' at ' + run[top].f.toFixed(4) +
                                       ' GHz<br>past the column scale (0–' + Math.round(100 * scale) + ' %): clipped');
                    }
                }
                run = [];
            }
            var F = pc.frequency_hz || [], Ls = pc.loss || [], D = pc.detuning_hz || [], S = pc.stderr || [];
            for (var j = 0; j < F.length; j++) {
                if (!finite(F[j]) || !finite(Ls[j])) { flush(); continue; }
                run.push({ f: F[j] / 1e9, l: Ls[j], t: esc(q.name) + ' · run #' + esc(c.run_id) +
                    ' (' + (c.interaction_time_ns != null ? esc(c.interaction_time_ns) + ' ns' : 'scan duration unknown') +
                    ', axis ' + esc(c.flux_conversion || '?') + ')<br>' + (F[j] / 1e9).toFixed(4) + ' GHz' +
                    (finite(D[j]) ? ' · detuning ' + fmtMHz(D[j], true) : '') +
                    '<br>loss ' + pct(Ls[j]) + (finite(S[j]) ? ' ± ' + pct(S[j]) : '') });
            }
            flush();
        });
    }

    function render(root, m) {
        var el = root.querySelector('#czp-map');
        if (!el || !m) return Promise.resolve();
        var my = ++pseq;
        var loss = root._czLoss || null, curves = (loss && loss.curves) || {};
        var ordSel = root.querySelector('#czp-map-order');
        var order = ordSel ? ordSel.value : 'chip';
        var scale = scaleOf(root);
        var pairs = m.pairs || {};
        // every pair a segment names, even one the inventory lacks
        m.segments.concat(m.refused || [], m.planned || []).forEach(function (s) {
            if (!pairs[s.pair]) {
                var ss = m.segments.filter(function (t) { return t.pair === s.pair; });
                var c = ss.find(function (t) { return t.role === 'control'; }), t = ss.find(function (t2) { return t2.role === 'target'; });
                pairs[s.pair] = { control_path: c ? c.qubit_path : null, target_path: t ? t.qubit_path : null,
                                  control: c ? c.qubit : null, target: t ? t.qubit : null };
            }
        });
        var lay = chipLayout(m.qubits, pairs);
        var drawing = lay.mode !== 'none' && m.qubits.length > 0;
        var qs = columnOrder(m.qubits, order, lay);
        var col = {}, byPath = {};
        qs.forEach(function (q, i) { col[q.path] = i; byPath[q.path] = q; });
        var n = Math.max(1, qs.length);
        var H = mapHeight(el);
        el.style.height = H + 'px';
        var plotW = Math.max(240, (el.clientWidth || 1200) - MARGIN.l - MARGIN.r);
        var plotH = H - MARGIN.t - MARGIN.b;
        var dd = drawing ? DRAW_PX / plotH : 0, colLo = drawing ? (DRAW_PX + GAP_PX) / plotH : 0;
        var colPx = plotW / n;
        var y2r = [-0.6, lay.ymax + 0.6];
        var rowPx = DRAW_PX / (y2r[1] - y2r[0]);
        function paperY(v) { return (v - y2r[0]) / (y2r[1] - y2r[0]) * dd; }

        var P = pairColors(), colour = edgeColours(pairs);
        var muted = cssVar('--pico-muted-color', '#8a8a8a');
        var ink = cssVar('--pico-color', '#222');
        var card = cssVar('--pico-card-background-color', cssVar('--pico-background-color', '#ffffff'));
        var traces = [], shapes = [], annotations = [];

        // ---- columns: separators, safe strips, loss, defects ----
        qs.forEach(function (q, i) {
            if (i) shapes.push({ type: 'line', xref: 'x', x0: i, x1: i, yref: 'paper', y0: colLo, y1: 1,
                                 line: { color: muted, width: 0.6 }, layer: 'below' });
            (q.safe || []).forEach(function (b) {
                shapes.push({ type: 'rect', xref: 'x', x0: i + 0.93, x1: i + 0.98, yref: 'y', y0: ghz(b.lo_hz), y1: ghz(b.hi_hz),
                              fillcolor: plan(), opacity: 0.3, line: { width: 0 }, layer: 'below' });
            });
        });
        var G = { fillX: [], fillY: [], lineX: [], lineY: [], lineT: [], clipX: [], clipY: [], clipT: [] };
        var O = { fillX: [], fillY: [], lineX: [], lineY: [], lineT: [], clipX: [], clipY: [], clipT: [] };
        var thin = { x: [], y: [], t: [] }, bars = { x: [], y: [], t: [] };
        qs.forEach(function (q, i) {
            var e = curves[q.name] || null, sc = e && e.scan, oc = e && e.marker_curve;
            if (sc) curveGeometry(sc, i, scale, q, G);
            if (oc) curveGeometry(oc, i, scale, q, O);
            var src = sourceDefects(e), label = e && e.marker ? e.marker.label : '';
            (q.defects || []).forEach(function (b) {
                var f = (b.lo_hz + b.hi_hz) / 2;
                var run = src.find(function (r) { return Math.abs(r.frequency_hz - f) <= 1e3; }) || {};
                var t = esc(q.name) + ' stored defect ' + (f / 1e9).toFixed(4) + ' GHz<br>FWHM ' + ((b.hi_hz - b.lo_hz) / 1e6).toFixed(1) + ' MHz' +
                    (b.peak_loss != null ? ', peak loss ' + (100 * b.peak_loss).toFixed(0) + ' %' : '') +
                    (run.strong ? ', strong' : '') + (run.edge_truncated ? ', edge truncated' : '') +
                    (label ? '<br>' + esc(label) : '');
                if (sc || oc) {
                    thin.x.push(i + LOSS_W * Math.min(1, Math.max(0, (b.peak_loss || 0) / scale)));
                    thin.y.push(f / 1e9); thin.t.push(t);
                } else {
                    var w = 0.08 + 0.82 * Math.min(1, (b.peak_loss || 0) / 0.5);
                    bars.x.push(i + 0.05, i + 0.05 + w, null);
                    bars.y.push(f / 1e9, f / 1e9, null);
                    bars.t.push(t, t, null);
                }
            });
        });
        if (G.fillX.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: G.fillX, y: G.fillY, fill: 'toself', fillcolor: 'rgba(128,128,128,0.55)',
                          line: { width: 0, color: 'rgba(128,128,128,0)' }, hoverinfo: 'skip', showlegend: false, meta: 'loss-fill' });
            traces.push({ type: 'scatter', mode: 'lines', x: G.lineX, y: G.lineY, text: G.lineT, showlegend: false,
                          line: { color: muted, width: 0.8 }, hovertemplate: '%{text}<extra></extra>', meta: 'loss-line' });
        }
        if (O.lineX.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: O.lineX, y: O.lineY, text: O.lineT, showlegend: false,
                          line: { color: ink, width: 0.6, dash: 'dot' }, opacity: 0.4,
                          hovertemplate: '%{text}<br>(the run the stored defects came from)<extra></extra>', meta: 'loss-source' });
        }
        if (bars.x.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: bars.x, y: bars.y, text: bars.t, showlegend: false,
                          line: { color: muted, width: 3 }, opacity: 0.75, hovertemplate: '%{text}<extra></extra>', meta: 'defect-bars' });
        }

        // ---- segments: one trace per colour class and style, null-separated ----
        var segClass = {}, connectors = {}, geo = {}, partnerX = [], partnerY = [], partnerT = [];
        var listed = [];
        m.segments.concat(m.refused || [], m.planned || []).forEach(function (s) { if (listed.indexOf(s.pair) < 0) listed.push(s.pair); });
        function g(pair) {
            return geo[pair] || (geo[pair] = { segX: [], segY: [], barX: [], barY: [], color: P[colour[pair] % P.length], segs: [] });
        }
        m.segments.forEach(function (s) {
            var i = col[s.qubit_path];
            if (i == null || s.frequency_hz == null) return;
            var c = colour[s.pair] || 0, style = !s.moves ? 'idle' : (/quad/.test(s.kind || '') ? 'dash' : 'solid');
            var k = c + ':' + style, f = s.frequency_hz / 1e9;
            var t = esc(s.pair) + ' · ' + esc(s.operation) + '<br>' + esc(s.qubit) + (s.moves ? '' : ' (stays at idle)') +
                ': ' + f.toFixed(4) + ' GHz<br>' + esc(s.kind || '') + (s.validity && s.validity !== 'idle' ? ' · ' + esc(s.validity) : '') +
                '<br>Δ ' + fmtMHz(s.detuning_hz, true) + (s.nearest ? ' · ' + esc(s.nearest.name) + ' ' + fmtMHz(s.nearest.distance_hz, true) : '');
            var cl = segClass[k] || (segClass[k] = { c: c, style: style, x: [], y: [], t: [], cd: [] });
            cl.x.push(i + 0.12, i + 0.88, null); cl.y.push(f, f, null); cl.t.push(t, t, null);
            cl.cd.push([s.path, s.pair], [s.path, s.pair], null);
            var gg = g(s.pair);
            gg.segX.push(i + 0.12, i + 0.88, null); gg.segY.push(f, f, null); gg.segs.push(s);
            var pp = pairs[s.pair] || {}, partner = pp.control_path === s.qubit_path ? pp.target : pp.control;
            if (partner) { partnerX.push(i + 0.88); partnerY.push(f); partnerT.push(esc(partner)); }
        });
        Object.keys(segClass).sort().forEach(function (k) {
            var cl = segClass[k], color = P[cl.c % P.length];
            traces.push({ type: 'scatter', mode: 'lines', x: cl.x, y: cl.y, text: cl.t, customdata: cl.cd, showlegend: false,
                          line: { color: color, width: cl.style === 'idle' ? 1.6 : 3.2, dash: cl.style === 'dash' ? 'dash' : 'solid' },
                          hovertemplate: '%{text}<extra></extra>', meta: 'seg' });
        });
        // connectors help on a small chip; across 10+ pairs they cross the whole map
        if (listed.length <= 10) {
            listed.forEach(function (pair) {
                var segs = m.segments.filter(function (s) { return s.pair === pair && col[s.qubit_path] != null; });
                if (segs.length !== 2) return;
                var ia = col[segs[0].qubit_path], ib = col[segs[1].qubit_path];
                var left = ia <= ib ? segs[0] : segs[1], right = ia <= ib ? segs[1] : segs[0];
                var c = colour[pair] || 0, cn = connectors[c] || (connectors[c] = { x: [], y: [] });
                cn.x.push(Math.min(ia, ib) + 0.88, Math.max(ia, ib) + 0.12, null);
                cn.y.push(ghz(left.frequency_hz), ghz(right.frequency_hz), null);
            });
            Object.keys(connectors).forEach(function (c) {
                traces.push({ type: 'scatter', mode: 'lines', x: connectors[c].x, y: connectors[c].y, showlegend: false, hoverinfo: 'skip',
                              line: { color: P[c % P.length], width: 1, dash: 'dot' }, opacity: 0.6, meta: 'connector' });
            });
        }
        var planned = (m.planned || []).filter(function (p) { return col[p.qubit_path] != null; });
        if (planned.length) {
            traces.push({ type: 'scatter', mode: 'markers', x: planned.map(function (p) { return col[p.qubit_path] + 0.5; }),
                          y: planned.map(function (p) { return ghz(p.frequency_hz); }), showlegend: false,
                          text: planned.map(function (p) { return esc(p.pair) + ' ' + esc(p.what) + ' (' + esc(p.qubit) + '): ' + f3(p.frequency_hz) + ' GHz'; }),
                          marker: { color: planned.map(function (p) { return P[(colour[p.pair] || 0) % P.length]; }), size: 11,
                                    symbol: 'diamond-open', line: { width: 2 } },
                          hovertemplate: '%{text}<extra></extra>', meta: 'planned' });
        }
        traces.push({ type: 'scatter', mode: 'markers', x: qs.map(function (q, i) { return i + 0.5; }),
                      y: qs.map(function (q) { return ghz(q.f01_hz); }), name: 'idle f_01', showlegend: false,
                      text: qs.map(function (q) {
                          var e = curves[q.name];
                          return esc(q.name) + ' idle ' + f3(q.f01_hz) + ' GHz' + '<br>' + esc(q.direction) +
                              (e && e.scan ? '<br>loss scan: run #' + esc(e.scan.run_id) : (e && e.reason ? '<br>' + esc(e.reason) : '')) +
                              (q.loss_data ? '' : '<br>no population-loss data stored');
                      }),
                      marker: { color: ink, size: 9, symbol: 'circle' }, hovertemplate: '%{text}<extra></extra>', meta: 'idle' });
        // ticks only where the drawn scan itself runs past the scale (the outline is a reference)
        if (thin.x.length || G.clipX.length) {
            traces.push({ type: 'scatter', mode: 'markers', x: thin.x.concat(G.clipX), y: thin.y.concat(G.clipY),
                          text: thin.t.concat(G.clipT), showlegend: false,
                          marker: { color: ink, size: thin.x.map(function () { return 10; }).concat(G.clipX.map(function () { return 7; })),
                                    symbol: thin.x.map(function () { return 'line-ew'; }).concat(G.clipX.map(function () { return 'triangle-right'; })),
                                    line: { color: ink, width: 2 } },
                          hovertemplate: '%{text}<extra></extra>', meta: 'defects-clips' });
        }
        if (partnerX.length && colPx >= LABEL_GATE_PX) {
            traces.push({ type: 'scatter', mode: 'text', x: partnerX, y: partnerY, text: partnerT, textposition: 'top left',
                          textfont: { size: 9, color: muted }, hoverinfo: 'skip', showlegend: false, cliponaxis: false, meta: 'partners' });
        }

        // ---- the chip drawing: leaders, bars, nodes ----
        var barPts = { x: [], y: [], t: [], cd: [] };
        if (drawing) {
            qs.forEach(function (q, i) {
                var nd = lay.node[q.path];
                if (!nd) return;
                shapes.push({ type: 'line', xref: 'x', x0: nd.x, x1: i + 0.5, yref: 'paper', y0: paperY(nd.y), y1: colLo,
                              line: { color: muted, width: 0.8 }, layer: 'below', name: 'leader' });
            });
            Object.keys(pairs).sort(natCmp).forEach(function (pair) {
                var pp = pairs[pair], a = lay.node[pp.control_path], b = lay.node[pp.target_path];
                if (!a || !b) return;
                var segs = m.segments.filter(function (s) { return s.pair === pair; });
                var refused = (m.refused || []).filter(function (r) { return r.pair === pair; });
                var computed = segs.length > 0, color = P[(colour[pair] || 0) % P.length];
                shapes.push({ type: 'line', xref: 'x', yref: 'y2', x0: a.x, y0: a.y, x1: b.x, y1: b.y, layer: 'below', name: 'bar:' + pair,
                              line: computed ? { color: color, width: 6 } : { color: muted, width: 1.5, dash: 'dot' } });
                var gg = g(pair);
                gg.barX = [a.x, b.x]; gg.barY = [a.y, b.y];
                var movers = segs.filter(function (s) { return s.moves; }), label = '';
                if (computed) {
                    if (movers.length + refused.length >= 2) {
                        var own = movers.find(function (s) { return s.role === 'control'; });
                        var ownRef = refused.find(function (r) { return r.qubit === pp.control; });
                        label = own ? esc(own.qubit) + ' ' + f3(own.frequency_hz) : ownRef ? esc(ownRef.qubit) + ' ?'
                            : movers.length ? esc(movers[0].qubit) + ' ' + f3(movers[0].frequency_hz) : '?';
                    } else if (movers.length === 1) label = f3(movers[0].frequency_hz);
                    else if (refused.length) label = '?';
                }
                if (label) {
                    // a short bar has no room for its label: lift it off the bar, along the bar's
                    // upward normal (straight up for a level bar, up-left for a rising one)
                    var dx = (b.x - a.x) * colPx, dy = (b.y - a.y) * rowPx, len = Math.hypot(dx, dy) || 1;
                    var nx = -dy / len, ny = dx / len;
                    if (ny < 0 || (ny === 0 && nx > 0)) { nx = -nx; ny = -ny; }
                    var lift = len < SHORT_BAR_PX ? 12 : 0;
                    annotations.push({ xref: 'x', yref: 'y2', x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, text: label, showarrow: false,
                                       font: { size: 10, color: ink }, bgcolor: card, borderpad: 1, opacity: 0.92,
                                       xshift: Math.round(nx * lift), yshift: Math.round(ny * lift), name: 'bar-label:' + pair });
                }
                var op = segs.length ? segs[0] : null, t;
                if (computed) {
                    t = esc(pair) + ' · ' + esc(op.operation) + segs.map(function (s) {
                        return '<br>' + esc(s.qubit) + ' ' + f3(s.frequency_hz) + ' GHz' + (s.moves ? '' : ' (stays at idle)');
                    }).join('') + refused.map(function (r) { return '<br>' + esc(r.qubit) + ' not drawn: ' + esc(r.reason); }).join('') +
                        '<br>Δ ' + fmtMHz(op.detuning_hz, true) + (op.nearest ? ' · ' + esc(op.nearest.name) + ' ' + fmtMHz(op.nearest.distance_hz, true) : '');
                } else if (refused.length) {
                    t = esc(pair) + ' · ' + esc(refused[0].operation) + refused.map(function (r) { return '<br>' + esc(r.qubit) + ' not drawn: ' + esc(r.reason); }).join('');
                } else {
                    t = esc(pair) + ': no ' + esc(m.op || 'cz') + ' operation';
                }
                barPts.x.push((a.x + b.x) / 2); barPts.y.push((a.y + b.y) / 2); barPts.t.push(t);
                barPts.cd.push([op ? op.path : (refused[0] && refused[0].path) || '', pair]);
            });
            traces.push({ type: 'scatter', mode: 'markers', x: barPts.x, y: barPts.y, yaxis: 'y2', text: barPts.t, customdata: barPts.cd,
                          marker: { size: 16, color: 'rgba(0,0,0,0)' }, showlegend: false, hovertemplate: '%{text}<extra></extra>', meta: 'bars' });
            var placed = qs.filter(function (q) { return lay.node[q.path]; });
            traces.push({ type: 'scatter', mode: 'markers+text', yaxis: 'y2', x: placed.map(function (q) { return lay.node[q.path].x; }),
                          y: placed.map(function (q) { return lay.node[q.path].y; }), text: placed.map(function (q) { return esc(q.name); }),
                          hovertext: placed.map(function (q) { return esc(q.name) + (q.grid_location ? ' at ' + esc(q.grid_location) : '') + ' · idle ' + f3(q.f01_hz) + ' GHz'; }),
                          textfont: { size: 9, color: ink }, textposition: 'middle center', showlegend: false,
                          marker: { size: 24, color: card, line: { color: ink, width: 1.2 } }, hovertemplate: '%{hovertext}<extra></extra>', meta: 'nodes' });
            if (lay.mode === 'logical') {
                annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: dd, xanchor: 'left', yanchor: 'top', showarrow: false,
                                   text: esc(lay.note), font: { size: 10, color: muted }, name: 'logical-note' });
            }
        }
        // the hover highlight: two overlay traces, restyled by index (never a re-render)
        var segIdx = traces.length;
        traces.push({ type: 'scatter', mode: 'lines', x: [], y: [], hoverinfo: 'skip', showlegend: false, opacity: 0.45,
                      line: { color: 'rgba(0,0,0,0)', width: 9 }, meta: 'highlight-seg' });
        var barIdx = traces.length;
        traces.push({ type: 'scatter', mode: 'lines', x: [], y: [], yaxis: drawing ? 'y2' : 'y', hoverinfo: 'skip', showlegend: false,
                      opacity: 0.45, line: { color: 'rgba(0,0,0,0)', width: 14 }, meta: 'highlight-bar' });
        if (G.fillX.length || O.lineX.length) {
            annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 1, xanchor: 'right', yanchor: 'bottom', showarrow: false, yshift: 8,
                               text: 'loss 0–' + Math.round(100 * scale) + ' %', font: { size: 9, color: muted }, name: 'scale' });
        }

        // ---- notes under the map: one line per qubit, only when something is off ----
        var notes = [], detail = [];
        if (m.refused.length) notes.push('Not drawn: ' + m.refused.map(function (r) {
            return r.qubit + ' in ' + r.pair + ' ' + r.operation + ' (' + r.reason + ')'; }).join('; ') + '.');
        if (m.missing.length) notes.push('No ' + (m.op || 'such') + ' operation on ' + m.missing.join(', ') + '.');
        if (!drawing) notes.push('No chip drawing: ' + (lay.why || 'no qubits') + '.');
        if (loss && loss.reason) notes.push('Measured loss: ' + loss.reason + '.');
        if (root._czLossError) notes.push('Measured loss could not be loaded: ' + root._czLossError + '.');
        var stale = (loss && loss.staleness) || {};
        if (stale.mode === 'building' || stale.mode === 'preparing') notes.push('The change history is being prepared; the axis check of the loss scans follows.');
        var none = [];
        qs.forEach(function (q) {
            var e = curves[q.name], parts = [];
            if (!e) { if (!q.loss_data) none.push(q.name); return; }
            var mk = e.marker || {};
            if ((mk.status === 'no_match' || mk.status === 'unreadable') && (q.defects || []).length) parts.push(mk.label);
            if (mk.status === 'match' && !mk.same_as_scan && e.marker_curve) {
                parts.push('stored defects from run #' + mk.run_id + ' (dotted), newest scan ' + (e.scan ? '#' + e.scan.run_id : 'none'));
            }
            [e.scan, e.marker_curve].forEach(function (c) { if (c && c.idle_note) parts.push('run #' + c.run_id + ': ' + c.idle_note); });
            var lines = (stale.qubits || {})[q.name] || [];
            if (lines.length) parts.push(staleText(lines));
            if (parts.length) notes.push(q.name + ': ' + parts.join('; ') + '.');
            if (!e.scan && !e.marker_curve && !q.loss_data) none.push(q.name);
            detail.push({ q: q, e: e });
        });
        if (none.length) notes.push('No population-loss data for ' + (none.length === qs.length ? 'any qubit' : none.join(', ')) +
                                    ' (none stored, no scan in the data folders).');
        var nEl = root.querySelector('#czp-map-notes');
        if (nEl) nEl.textContent = notes.join('\n');
        var kEl = root.querySelector('#czp-map-key');
        if (kEl) {
            kEl.textContent = 'Key: ' + KEY + (G.fillX.length ? '; gray = measured population loss of the newest scan, ' +
                'dotted = the run the stored defects came from, short dash = a stored defect, ▸ = past the column scale' : '') + '.';
        }
        renderDetail(root, loss, detail);

        var L = layout({
            margin: MARGIN, height: H, showlegend: false, hovermode: 'closest',
            xaxis: { tickmode: 'array', tickvals: qs.map(function (q, i) { return i + 0.5; }), ticktext: qs.map(function (q) { return q.name; }),
                     range: [0, n], showgrid: false, zeroline: false, fixedrange: true, side: drawing ? 'top' : 'bottom',
                     anchor: 'y', tickfont: { size: 10 } },
            yaxis: { title: { text: 'frequency (GHz)' }, domain: [colLo, 1] },
            shapes: shapes, annotations: annotations
        });
        if (drawing) L.yaxis2 = { domain: [0, dd], range: y2r, visible: false, fixedrange: true, anchor: 'x', showgrid: false, zeroline: false };
        el.setAttribute('data-segments', String(m.segments.length));
        el.setAttribute('data-layout', lay.mode);
        var overlay = { seg: segIdx, bar: barIdx, geo: geo, base: shapes.slice(), ink: ink, hl: null, drawing: drawing };
        var sel = selectionShapes(overlay, root.getAttribute('data-selected'));
        L.shapes = sel.concat(L.shapes || []);
        overlay.sel = pairOf(root.getAttribute('data-selected'));
        root._czH = H;
        root._czW = plotW;
        function current() { return my === pseq && document.body.contains(root); }
        return window.requirePlotly().then(function () {
            if (!current()) return;
            return window._plotlyRender(el, traces, L, config(), current);
        }).then(function () {
            if (!current() || !el.data) return;
            overlay.epoch = el.__plotlyRenderEpoch || 0;
            el._czpOverlay = overlay;
            if (!el.on || el._czpMapClick === (el._ev || el.on)) return;
            el._czpMapClick = el._ev || el.on;
            el.on('plotly_click', function (ev) {
                var pt = ev && ev.points && ev.points[0], cd = pt && pt.customdata;
                var path = Array.isArray(cd) ? cd[0] : cd;
                if (typeof path === 'string' && path.indexOf('qubit_pairs.') === 0) {
                    root.dispatchEvent(new CustomEvent('czp:open', { detail: { op: path } }));
                }
            });
            el.on('plotly_hover', function (ev) {
                var pt = ev && ev.points && ev.points[0], cd = pt && pt.customdata;
                var pair = Array.isArray(cd) ? cd[1] : pairOf(cd);
                if (pair) highlight(root, pair);
            });
            el.on('plotly_unhover', function () { highlight(root, null); });
        });
    }

    /* The ledger's axis lines of one qubit (one per drawn curve, so up to two) as one: two
       runs whose axes predate the same change read "axis of runs #A and #B predate ...". */
    function staleText(lines) {
        var by = {}, order = [];
        lines.forEach(function (s) {
            var k = s.changed_run_id + '|' + (s.fields || []).length;
            if (!by[k]) { by[k] = { runs: [], n: (s.fields || []).length, m: s.changed_run_id, text: s.text }; order.push(k); }
            by[k].runs.push(s.run_id);
        });
        return order.map(function (k) {
            var g = by[k];
            if (g.runs.length === 1) return g.text;
            return 'axis of runs #' + g.runs.join(' and #') + ' predate ' + g.n + ' flux-model input' + (g.n === 1 ? '' : 's') +
                ' changed (run #' + g.m + ')';
        }).join('; ');
    }

    function selectionShapes(o, path) {
        var pair = pairOf(path), gg = pair && o.geo[pair], out = [];
        if (!gg) return out;
        for (var k = 0; k + 1 < gg.segX.length; k += 3) {
            out.push({ type: 'line', xref: 'x', yref: 'y', x0: gg.segX[k], x1: gg.segX[k + 1], y0: gg.segY[k], y1: gg.segY[k + 1],
                       line: { color: o.ink, width: 8 }, layer: 'below', name: 'selected:' + pair });
        }
        if (gg.barX.length) {
            out.push({ type: 'line', xref: 'x', yref: 'y2', x0: gg.barX[0], x1: gg.barX[1], y0: gg.barY[0], y1: gg.barY[1],
                       line: { color: o.ink, width: 11 }, layer: 'below', name: 'selected:' + pair });
        }
        return out;
    }

    /* Run fn after any render or restyle already queued on el (the _plotlyRender chain), and
       only while el still shows the figure the overlay was built for. */
    function chain(el, fn) {
        var o = el._czpOverlay;
        var prev = el.__plotlyRenderChain || Promise.resolve();
        el.__plotlyRenderChain = prev.catch(function () {}).then(function () {
            if (!document.body.contains(el) || el._czpOverlay !== o || !o || o.epoch !== (el.__plotlyRenderEpoch || 0) ||
                !el.data || typeof Plotly === 'undefined') return null;
            return fn(o);
        }).catch(function () {});
        return el.__plotlyRenderChain;
    }

    /* Hover on a segment, a bar or a table row: that pair's segments and bar glow. */
    function highlight(root, pair) {
        var el = root && root.querySelector('#czp-map'), o = el && el._czpOverlay;
        if (!o || o.hl === (pair || null)) return Promise.resolve();
        o.hl = pair || null;
        var gg = pair ? o.geo[pair] : null;
        return chain(el, function (ov) {
            return Plotly.restyle(el, {
                x: [gg ? gg.segX : [], gg ? gg.barX : []], y: [gg ? gg.segY : [], gg ? gg.barY : []],
                'line.color': [gg ? gg.color : 'rgba(0,0,0,0)', gg ? gg.color : 'rgba(0,0,0,0)']
            }, [ov.seg, ov.bar]);
        });
    }

    /* The operation selected in the table / detail stays outlined on the map. */
    function select(root, path) {
        var el = root && root.querySelector('#czp-map'), o = el && el._czpOverlay;
        if (!o) return Promise.resolve();
        var pair = pairOf(path);
        if (o.sel === pair) return Promise.resolve();
        o.sel = pair;
        return chain(el, function (ov) {
            var shapes = selectionShapes(ov, path).concat(ov.base);
            if (window.PlotTheme) shapes = window.PlotTheme.houseLayout({ shapes: shapes }).shapes;
            return Plotly.relayout(el, { shapes: shapes });
        });
    }

    function renderDetail(root, loss, rows) {
        var box = root.querySelector('#czp-map-detail-body'), sum = root.querySelector('#czp-map-detail-summary');
        if (!box) return;
        if (!loss) {
            box.innerHTML = '<p class="muted">' + (root._czLossError ? 'Could not be loaded: ' + esc(root._czLossError) : 'Loading the measured loss…') + '</p>';
            if (sum) sum.textContent = 'Measured loss per qubit';
            return;
        }
        var withScan = rows.filter(function (r) { return r.e.scan; }).length;
        if (sum) sum.textContent = 'Measured loss per qubit (' + withScan + ' of ' + rows.length + ' with a scan)';
        function curve(c) {
            return 'run #' + esc(c.run_id) + ' (' + (c.interaction_time_ns != null ? esc(c.interaction_time_ns) + ' ns' : 'duration unknown') +
                ', axis ' + esc(c.flux_conversion || '?') + ', ' + esc(c.n_samples) + ' samples in ' + (c.pieces || []).length +
                ' piece' + ((c.pieces || []).length === 1 ? '' : 's') +
                (c.range_hz ? ', ' + f3(c.range_hz[0]) + '–' + f3(c.range_hz[1]) + ' GHz' : '') +
                (c.loss_max != null ? ', loss ' + pct(c.loss_min) + ' … ' + pct(c.loss_max) : '') + ')' +
                ((c.notes || []).length ? ' — ' + c.notes.map(esc).join('; ') : '');
        }
        var h = ['<p class="muted">Searched: ' + ((loss.roots || []).map(function (r) { return esc(r.path) + ' (' + esc(r.source) + ')'; }).join(', ') || 'no data folder') +
                 ' · ' + esc(loss.runs_listed || 0) + ' population-loss runs found, ' + esc(loss.runs_read || 0) + ' read</p>', '<ul class="czp-map-detail-list">'];
        rows.forEach(function (r) {
            var e = r.e, mk = e.marker || {};
            var st = ((loss.staleness || {}).qubits || {})[r.q.name] || [];
            h.push('<li><strong>' + esc(r.q.name) + '</strong> — ' + (e.scan ? 'newest scan ' + curve(e.scan) : esc(e.reason || 'no scan')) +
                   '; ' + esc(mk.label || 'no stored defects') + (e.marker_curve ? ': dotted outline ' + curve(e.marker_curve) : '') +
                   (st.length ? '; ' + esc(staleText(st)) : '') + '</li>');
        });
        h.push('</ul>');
        if ((loss.skipped || []).length) {
            h.push('<p class="muted">Skipped: ' + loss.skipped.map(function (s) { return '#' + esc(s.run_id) + ' (' + esc(s.reason) + ')'; }).join('; ') + '</p>');
        }
        box.innerHTML = h.join('');
    }

    /* Ask for the map of the picked operation (the only refetch an operation switch costs). */
    function draw(root) {
        var el = root && root.querySelector('#czp-map');
        if (!el) return Promise.resolve();
        var my = ++fseq;
        var opSel = root.querySelector('#czp-map-op'), op = opSel ? opSel.value : '';
        return fetch('/cz-plan/map' + (op ? '?op=' + encodeURIComponent(op) : ''), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (m) {
                if (my !== fseq || !document.body.contains(root)) return;
                if (!m.ok) { root._czMap = null; pseq++; mapEmpty(el, m.error || 'The map could not be computed.'); return; }
                if (opSel && opSel.options.length <= 1) {
                    (m.operations || []).forEach(function (name) {
                        var o = document.createElement('option'); o.value = name; o.textContent = name; opSel.appendChild(o);
                    });
                }
                root._czMap = m;
                return render(root, m);
            })
            .catch(function (e) {
                if (my === fseq) { root._czMap = null; pseq++; mapEmpty(el, 'The map could not be loaded: ' + (e && e.message || e)); }
            });
    }

    /* Redraw from what the page already holds (column order, loss scale, the loss answer,
       a resize) -- no request. */
    function paint(root) {
        return root && root._czMap ? render(root, root._czMap) : Promise.resolve();
    }

    /* The measured loss: once per page; while the change history is being built the curves
       already draw and the axis check is asked again. */
    function loadLoss(root, tries) {
        return fetch('/cz-plan/loss', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!document.body.contains(root)) return;
                if (!d || !d.ok) { root._czLossError = (d && d.error) || 'refused'; return paint(root); }
                root._czLoss = d;
                root._czLossError = null;
                root.dispatchEvent(new CustomEvent('czp:loss'));
                var mode = (d.staleness || {}).mode;
                if ((mode === 'building' || mode === 'preparing') && (tries || 0) < 20) {
                    setTimeout(function () { if (document.body.contains(root)) loadLoss(root, (tries || 0) + 1); }, 3000);
                }
                return paint(root);
            })
            .catch(function (e) {
                if (!document.body.contains(root)) return;
                root._czLossError = (e && e.message) || String(e);
                return paint(root);
            });
    }

    function mount(root) {
        if (!root || root._czMapMounted) return;
        root._czMapMounted = true;
        var opSel = root.querySelector('#czp-map-op');
        if (opSel) opSel.addEventListener('change', function () { draw(root); });
        ['#czp-map-order', '#czp-map-scale'].forEach(function (s) {
            var c = root.querySelector(s);
            if (c) c.addEventListener('change', function () {
                paint(root);
                if (s === '#czp-map-scale') root.dispatchEvent(new CustomEvent('czp:scale'));
            });
        });
        draw(root);
        loadLoss(root, 0);
    }

    var resizeTimer = null;
    window.addEventListener('resize', function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
            var root = document.getElementById('czp-root'), el = root && root.querySelector('#czp-map');
            if (!el || !root._czMap) return;
            var w = Math.max(240, (el.clientWidth || 1200) - MARGIN.l - MARGIN.r);
            if (Math.abs(mapHeight(el) - (root._czH || 0)) >= 4 || Math.abs(w - (root._czW || 0)) > 30) paint(root);
        }, 150);
    });

    window.CzMap = {
        mount: mount, draw: draw, paint: paint, loadLoss: loadLoss, highlight: highlight, select: select,
        scale: scaleOf, loss: function (root) { return (root && root._czLoss) || null; },
        edgeColours: edgeColours, chipLayout: chipLayout, natCmp: natCmp, pairOf: pairOf, pairColors: pairColors,
        DRAW_PX: DRAW_PX, LOSS_W: LOSS_W, LABEL_GATE_PX: LABEL_GATE_PX, SHORT_BAR_PX: SHORT_BAR_PX, _esc: esc
    };
})();
