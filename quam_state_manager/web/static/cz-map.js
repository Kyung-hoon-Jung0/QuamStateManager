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
    /* The drawing's height follows the chip's rows (fix3 P1-3: a fixed 150 px put 5 rows
       29 px apart, so every vertical neighbour's bar hid under its label): ROW_PX per row,
       at least DRAW_PX, never more than DRAW_MAX_FRAC of the plot. */
    var DRAW_PX = 150, ROW_PX = 44, DRAW_MAX_FRAC = 0.45, GAP_PX = 26, MIN_H = 460, MAX_H = 640;
    /* v1.5.1 (U P2-4): the top margin holds Plotly's toolbar (~28 px, top
       right) AND the column names above it -- at 30 px the toolbar sat on the
       last column's name */
    var MARGIN = { l: 64, r: 16, t: 52, b: 6 };
    var LOSS_W = 0.9;          // a column's loss scale spans 90 % of the column
    var SHORT_BAR_PX = 60;     // a bar shorter than this never carries its label on itself
    var ROW_LABEL_MIN_PX = 36; // below this row pitch a short bar's label is hover only
    var NODE_R = 12.5;         // a node's radius in the drawing (marker size 24 + its line)
    var BAR_HALF = 3;          // half the width of a drawn bar
    var LABEL_FONT = 10;
    var CLUSTER_PX = 6;        // segments of one column this close share the row side by side
    var PARTNER_H = 11;        // a partner name's height (9 px font)
    var RETRY_N = 20, RETRY_MS = 3000;   // re-asks while the change history is being built
    /* Each hue clears text contrast on its theme's surfaces; partner names and the framed
       frequency labels also identify pairs without relying on colour alone. */
    var PAIR_LIGHT = ['#006d2c', '#5440a0', '#ad326b', '#926000', '#007c78', '#b33737', '#b94d10', '#245ec4'];
    var PAIR_DARK = ['#57b65f', '#aa9bf4', '#f285b4', '#e2b34d', '#48c5ad', '#f58b8b', '#f49c67', '#79b4fa'];
    var MAP_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#17becf',
                      '#bcbd22', '#7f7f7f'];
    var KEY = 'solid accepted fitted model, dashed quad-term or derived estimate, thin stays at idle, diamond planned, ~ extrapolated estimate; ' +
              'colour = pair (unique around each qubit, not a time step); bar label = first-lobe operating frequency (GHz); nearest resonance = closest across played lobes, framed in the bar colour';

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
    function isDark() { return (document.documentElement.getAttribute('data-theme') === 'dark' || document.documentElement.getAttribute('data-theme') === 'qm'); }
    function pairColors() {
        var P = (isDark() ? PAIR_DARK : PAIR_LIGHT).slice();
        return P.concat(MAP_COLORS.filter(function (c) { return P.indexOf(c) < 0; }));
    }
    function plan() {
        return pairColors()[0];
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
    function opNameOf(path) {
        var m = /^qubit_pairs\..+\.macros\.([^.]+)$/.exec(String(path || ''));
        return m ? m[1] : null;
    }
    /* The operation the map draws for one pair: its segments' (or refusals') operation,
       else the picked name (null: each pair's default). */
    function drawnOp(m, pair) {
        var s = (m.segments || []).concat(m.refused || []).find(function (x) { return x.pair === pair && x.operation; });
        return s ? s.operation : (m.op || null);
    }
    /* fix3 P1-1: the operation name the map must switch to so that it draws what the reader
       selected -- only when that name is one of the map's operations, differs from what the
       map draws for the selected pair and is not already picked; else null. */
    function followName(root, m, path) {
        var name = opNameOf(path), pair = pairOf(path), sel = root && root.querySelector('#czp-map-op');
        if (!name || !pair || !sel || !m) return null;
        if ((m.operations || []).indexOf(name) < 0 || drawnOp(m, pair) === name || sel.value === name) return null;
        return Array.prototype.some.call(sel.options, function (o) { return o.value === name; }) ? name : null;
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

    /* ---- bar labels (fix3 P1-3): placed in the drawing's pixels, never on another bar, a
       node or another label; a short bar's label sits beside its own bar, never on it. ---- */
    /* A bar label's box (px): the annotation's text at LABEL_FONT plus its pad and border,
       a little larger than Chrome draws it ("q1 4.017": 41 x 17 px measured, 47.2 x 18 here). */
    function labelSize(text) {
        return { w: 5.4 * String(text).length + 4, h: 18 };
    }
    /* A partner label's width (px) at its 9 px font. */
    function partnerWidth(text) {
        return 5.2 * String(text).length + 2;
    }
    /* The partner names of one shared row as lines no wider than the column (one name a line
       at least): {lines: [[name, ...]], w, h} in px. */
    function partnerLines(names, maxW) {
        var lines = [], cur = [];
        names.forEach(function (nm) {
            if (cur.length && partnerWidth(cur.concat(nm).join(' ')) > maxW) { lines.push(cur); cur = []; }
            cur.push(nm);
        });
        if (cur.length) lines.push(cur);
        return { lines: lines, h: PARTNER_H * lines.length,
                 w: Math.max.apply(null, lines.map(function (l) { return partnerWidth(l.join(' ')); })) };
    }
    function pointSegDist(p, a, b) {
        var dx = b.X - a.X, dy = b.Y - a.Y, l2 = dx * dx + dy * dy;
        var t = l2 ? Math.max(0, Math.min(1, ((p.X - a.X) * dx + (p.Y - a.Y) * dy) / l2)) : 0;
        return Math.hypot(p.X - a.X - t * dx, p.Y - a.Y - t * dy);
    }
    function boxPointDist(r, p) {
        return Math.hypot(Math.max(r.x0 - p.X, 0, p.X - r.x1), Math.max(r.y0 - p.Y, 0, p.Y - r.y1));
    }
    function segHitsBox(a, b, r) {        // Liang-Barsky: does segment a-b cross box r?
        var t0 = 0, t1 = 1, dx = b.X - a.X, dy = b.Y - a.Y;
        var p = [-dx, dx, -dy, dy], q = [a.X - r.x0, r.x1 - a.X, a.Y - r.y0, r.y1 - a.Y];
        for (var i = 0; i < 4; i++) {
            if (p[i] === 0) { if (q[i] < 0) return false; continue; }
            var t = q[i] / p[i];
            if (p[i] < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; }
        }
        return true;
    }
    function boxSegDist(r, a, b) {
        if (segHitsBox(a, b, r)) return 0;
        var c = [{ X: r.x0, Y: r.y0 }, { X: r.x1, Y: r.y0 }, { X: r.x0, Y: r.y1 }, { X: r.x1, Y: r.y1 }];
        return Math.min(boxPointDist(r, a), boxPointDist(r, b), Math.min.apply(null, c.map(function (p) { return pointSegDist(p, a, b); })));
    }
    function boxesOverlap(r, s, pad) {
        return r.x0 < s.x1 + pad && s.x0 < r.x1 + pad && r.y0 < s.y1 + pad && s.y0 < r.y1 + pad;
    }
    /* items: [{pair, text, a, b}] (own bar's node centres, px, Y up); bars: [{pair, a, b,
       half}] every drawn bar; nodes: [{X, Y}]; opts: {rowPx, x0, x1, y0, y1} the box limits.
       A long bar keeps its label on itself when that box hits nothing else; otherwise (and
       always for a short bar) the box goes to the nearest free spot touching its own bar --
       never on another bar, a node or a label, preferring room from the other bars, and a
       short bar's own colour stays uncovered where it can. Under ROW_LABEL_MIN_PX of row
       pitch, or with no free spot, a short bar's label is hover only (null).
       Returns {pair: {dx, dy, w, h, box} | null}. */
    function placeLabels(items, bars, nodes, opts) {
        var out = {}, placed = [], R = 48, RY = 30, STEP = 2, ATTACH = 12;
        var info = items.map(function (it) {
            var dx = it.b.X - it.a.X, dy = it.b.Y - it.a.Y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
            var sz = labelSize(it.text), ext = sz.w * Math.abs(ux) + sz.h * Math.abs(uy);
            var cut = Math.min(NODE_R, len / 2);
            return { it: it, sz: sz, short: len < SHORT_BAR_PX || len - 2 * NODE_R - ext < 24,
                     mx: (it.a.X + it.b.X) / 2, my: (it.a.Y + it.b.Y) / 2,
                     va: { X: it.a.X + ux * cut, Y: it.a.Y + uy * cut }, vb: { X: it.b.X - ux * cut, Y: it.b.Y - uy * cut } };
        });
        info.sort(function (p, q) { return (p.short - q.short) || natCmp(p.it.pair, q.it.pair); });
        info.forEach(function (L) {
            var pair = L.it.pair, w = L.sz.w, h = L.sz.h;
            if (L.short && opts.rowPx < ROW_LABEL_MIN_PX) { out[pair] = null; return; }
            var reach = Math.max(R, RY) + w;
            var near = function (p) { return Math.abs(p.X - L.mx) < reach + NODE_R && Math.abs(p.Y - L.my) < reach + NODE_R; };
            var others = bars.filter(function (b) {
                return b.pair !== pair && (near(b.a) || near(b.b) || pointSegDist({ X: L.mx, Y: L.my }, b.a, b.b) < reach);
            });
            var ns = nodes.filter(near);
            function boxAt(ox, oy) {
                return { x0: L.mx + ox - w / 2, x1: L.mx + ox + w / 2, y0: L.my + oy - h / 2, y1: L.my + oy + h / 2 };
            }
            function free(r) {
                if (r.x0 < opts.x0 || r.x1 > opts.x1 || r.y0 < opts.y0 || r.y1 > opts.y1) return null;
                for (var i = 0; i < ns.length; i++) if (boxPointDist(r, ns[i]) < NODE_R + 1) return null;
                var room = 12;
                for (var j = 0; j < others.length; j++) {
                    var d = boxSegDist(r, others[j].a, others[j].b) - (others[j].half || BAR_HALF);
                    if (d < 1) return null;
                    room = Math.min(room, d);
                }
                for (var k = 0; k < placed.length; k++) if (boxesOverlap(r, placed[k], 1)) return null;
                return { room: room };
            }
            var best = null;
            if (!L.short && free(boxAt(0, 0))) best = { ox: 0, oy: 0 };
            else {
                var bc = Infinity;
                for (var ox = -R; ox <= R; ox += STEP) {
                    for (var oy = -RY; oy <= RY; oy += STEP) {
                        var r = boxAt(ox, oy), own = boxSegDist(r, L.va, L.vb);
                        if (own > ATTACH) continue;
                        var f = free(r);
                        if (!f) continue;
                        var c = Math.hypot(ox, oy) + (L.short && own < BAR_HALF + 0.5 ? 80 : 0) - 0.5 * f.room;
                        if (c < bc) { bc = c; best = { ox: ox, oy: oy }; }
                    }
                }
            }
            if (!best) { out[pair] = null; return; }
            var box = boxAt(best.ox, best.oy);
            placed.push(box);
            out[pair] = { dx: best.ox, dy: best.oy, w: w, h: h, box: box };
        });
        return out;
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
                    '<br>loss ' + pct(Ls[j]) + (finite(S[j]) ? ' ± ' + pct(S[j]) : '') +
                    (c.identity === 'unchecked' ? '<br>chip identity unchecked (no history, no chip name)' : '') });
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
        // fix3 P1-3: the drawing grows with the chip's rows, capped to a share of the plot
        var drawPx = drawing ? Math.round(Math.min(Math.max(DRAW_PX, ROW_PX * (lay.ymax + 1)), DRAW_MAX_FRAC * plotH)) : 0;
        var dd = drawing ? drawPx / plotH : 0, colLo = drawing ? (drawPx + GAP_PX) / plotH : 0;
        var colPx = plotW / n;
        var y2r = [-0.6, lay.ymax + 0.6];
        var rowPx = drawPx / (y2r[1] - y2r[0]);
        function paperY(v) { return (v - y2r[0]) / (y2r[1] - y2r[0]) * dd; }
        // fix3 P1-1: when the map draws another operation of the selected pair than the one
        // selected below (its name is not on the picker, or the reader picked another), the
        // outline's hover and the key name both
        var selPath = root.getAttribute('data-selected') || '', selPair = pairOf(selPath), selName = opNameOf(selPath);
        var shown = selPair ? drawnOp(m, selPair) : null;
        var mismatch = selName && shown && shown !== selName ? { pair: selPair, shown: shown, selected: selName } : null;
        root._czMismatch = mismatch ? selPair + '|' + shown + '|' + selName : '';
        var mmHover = mismatch ? '<br>selected below: ' + esc(selName) + ' (this map shows ' + esc(shown) + ')' : '';

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
                              fillcolor: plan(), line: { width: 0 }, layer: 'below' });
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
                    // fix3 P2-1: a column without a scan follows the same Loss scale select
                    var w = 0.08 + 0.82 * Math.min(1, Math.max(0, (b.peak_loss || 0) / scale));
                    bars.x.push(i + 0.05, i + 0.05 + w, null);
                    bars.y.push(f / 1e9, f / 1e9, null);
                    bars.t.push(t, t, null);
                }
            });
        });
        if (G.fillX.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: G.fillX, y: G.fillY, fill: 'toself', fillcolor: 'rgba(128,128,128,0.16)',
                          line: { width: 0, color: 'rgba(128,128,128,0)' }, hoverinfo: 'skip', showlegend: false, meta: 'loss-fill' });
            traces.push({ type: 'scatter', mode: 'lines', x: G.lineX, y: G.lineY, text: G.lineT, showlegend: false,
                          line: { color: muted, width: 0.8 }, hovertemplate: '%{text}<extra></extra>', meta: 'loss-line' });
        }
        if (O.lineX.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: O.lineX, y: O.lineY, text: O.lineT, showlegend: false,
                          line: { color: ink, width: 0.8, dash: 'dot' },
                          hovertemplate: '%{text}<br>(the run whose defects equal the stored list)<extra></extra>', meta: 'loss-source' });
        }
        if (bars.x.length) {
            traces.push({ type: 'scatter', mode: 'lines', x: bars.x, y: bars.y, text: bars.t, showlegend: false,
                          line: { color: muted, width: 3 }, hovertemplate: '%{text}<extra></extra>', meta: 'defect-bars' });
        }

        // ---- the columns' frequency range, set here (not autoranged) so a segment's pixel
        //      height is known: everything the columns draw, padded 4 % ----
        var lo = Infinity, hi = -Infinity;
        function addY(v) { if (v != null && isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
        [G.fillY, O.lineY, thin.y, bars.y, G.clipY].forEach(function (a) { a.forEach(addY); });
        qs.forEach(function (q) { addY(ghz(q.f01_hz)); (q.safe || []).forEach(function (b) { addY(ghz(b.lo_hz)); addY(ghz(b.hi_hz)); }); });
        m.segments.forEach(function (s) { if (col[s.qubit_path] != null) addY(ghz(s.frequency_hz)); });
        (m.planned || []).forEach(function (p) { if (col[p.qubit_path] != null) addY(ghz(p.frequency_hz)); });
        var yr = isFinite(lo) ? [lo - Math.max(0.01, 0.04 * (hi - lo)), hi + Math.max(0.01, 0.04 * (hi - lo))] : null;
        var pxPerGHz = yr ? plotH * (1 - colLo) / (yr[1] - yr[0]) : 1;

        // ---- segments (fix3 P1-4): one column's segments within CLUSTER_PX of each other
        //      share their row side by side, each in its own colour, under one partner label ----
        function partnerOf(s) {
            var pp = pairs[s.pair] || {};
            return pp.control_path === s.qubit_path ? pp.target : pp.control;
        }
        var place = {}, partners = [], wanted = [], byCol = {};
        m.segments.forEach(function (s, k) {
            var i = col[s.qubit_path];
            if (i == null || s.frequency_hz == null) return;
            (byCol[i] = byCol[i] || []).push(k);
        });
        Object.keys(byCol).forEach(function (ci) {
            var i = +ci, ks = byCol[ci].slice().sort(function (a, b) {
                return m.segments[a].frequency_hz - m.segments[b].frequency_hz || a - b;
            });
            var groups = [], cur = [];
            ks.forEach(function (k) {
                if (cur.length && (m.segments[k].frequency_hz - m.segments[cur[cur.length - 1]].frequency_hz) / 1e9 * pxPerGHz > CLUSTER_PX) {
                    groups.push(cur); cur = [];
                }
                cur.push(k);
            });
            if (cur.length) groups.push(cur);
            groups.forEach(function (gk) {
                gk.sort(function (a, b) {
                    return natCmp(partnerOf(m.segments[a]) || '', partnerOf(m.segments[b]) || '') || natCmp(m.segments[a].pair, m.segments[b].pair);
                });
                var W = 0.76 / gk.length, gap = gk.length > 1 ? 0.01 : 0;
                gk.forEach(function (k, j) { place[k] = { x0: i + 0.12 + j * W + gap, x1: i + 0.12 + (j + 1) * W - gap }; });
                var names = gk.map(function (k) { return partnerOf(m.segments[k]); }).filter(Boolean);
                if (!names.length) return;
                var top = Math.max.apply(null, gk.map(function (k) { return m.segments[k].frequency_hz / 1e9; }));
                // a list wider than the column wraps onto lines inside it (never into the next column)
                var pl = partnerLines(names.map(function (name) { return colPx < 40 ? name.replace(/^q(?=[A-Z0-9])/i, '') : name; }), 0.76 * colPx);
                if (pl.w > 0.76 * colPx) return;
                wanted.push({ i: i, y: top, ypx: (top - (yr ? yr[0] : 0)) * pxPerGHz, w: pl.w, h: pl.h,
                              t: pl.lines.map(function (l) { return l.map(esc).join(' '); }).join('<br>') });
            });
        });
        // partner names never print over each other (a long list runs into the next column): the
        // right end of the segment first, else its left end, else hover only
        var taken = [];
        wanted.forEach(function (pw) {
            var right = (pw.i + 0.88) * colPx, left = (pw.i + 0.12) * colPx;
            var spots = [{ x0: right - pw.w, x1: right, x: pw.i + 0.88, pos: 'top left' },
                         { x0: left, x1: left + pw.w, x: pw.i + 0.12, pos: 'top right' }];
            for (var k = 0; k < spots.length; k++) {
                var b = { x0: spots[k].x0, x1: spots[k].x1, y0: pw.ypx, y1: pw.ypx + pw.h };
                if (b.x0 < 0 || b.x1 > n * colPx || taken.some(function (t) { return boxesOverlap(b, t, 1); })) continue;
                taken.push(b);
                partners.push({ x: spots[k].x, y: pw.y, t: pw.t, pos: spots[k].pos });
                return;
            }
        });
        var segClass = {}, connectors = {}, geo = {};
        var listed = [];
        m.segments.concat(m.refused || [], m.planned || []).forEach(function (s) { if (listed.indexOf(s.pair) < 0) listed.push(s.pair); });
        function g(pair) {
            return geo[pair] || (geo[pair] = { segX: [], segY: [], barX: [], barY: [], color: P[colour[pair] % P.length], segs: [] });
        }
        m.segments.forEach(function (s, k) {
            var x = place[k];
            if (!x) return;
            var c = colour[s.pair] || 0, style = !s.moves ? 'idle' : (/quad|derived|estimate/.test(s.kind || '') ? 'dash' : 'solid');
            var key = c + ':' + style, f = s.frequency_hz / 1e9;
            var t = esc(s.pair) + ' · ' + esc(s.operation) + '<br>' + esc(s.qubit) + (s.moves ? '' : ' (stays at idle)') +
                ': ' + f.toFixed(4) + ' GHz<br>' + esc(s.kind || '') + (s.validity && s.validity !== 'idle' ? ' · ' + esc(s.validity) : '') +
                '<br>Δ ' + fmtMHz(s.detuning_hz, true) + (s.nearest ? ' · ' + esc(s.nearest.name) + (s.nearest.lobe ? ' (' + esc(s.nearest.lobe) + ')' : '') + ' ' + fmtMHz(s.nearest.distance_hz, true) : '') +
                (mismatch && s.pair === mismatch.pair ? mmHover : '');
            var cl = segClass[key] || (segClass[key] = { c: c, style: style, x: [], y: [], t: [], cd: [] });
            cl.x.push(x.x0, x.x1, null); cl.y.push(f, f, null); cl.t.push(t, t, null);
            cl.cd.push([s.path, s.pair], [s.path, s.pair], null);
            var gg = g(s.pair);
            gg.segX.push(x.x0, x.x1, null); gg.segY.push(f, f, null); gg.segs.push(s);
        });
        // the idle dots under the segments: a segment that stays at idle sits on its dot, and in a
        // shared row (fix3 P1-4) the dot must not hide the middle partner's colour
        traces.push({ type: 'scatter', mode: 'markers', x: qs.map(function (q, i) { return i + 0.5; }),
                      y: qs.map(function (q) { return ghz(q.f01_hz); }), name: 'idle f_01', showlegend: false,
                      text: qs.map(function (q) {
                          var e = curves[q.name];
                          return esc(q.name) + ' idle ' + f3(q.f01_hz) + ' GHz' + '<br>' + esc(q.direction) +
                              (e && e.scan ? '<br>loss scan: run #' + esc(e.scan.run_id) : (e && e.reason ? '<br>' + esc(e.reason) : '')) +
                              (q.loss_data ? '' : '<br>no population-loss data stored');
                      }),
                      marker: { color: ink, size: 9, symbol: 'circle' }, hovertemplate: '%{text}<extra></extra>', meta: 'idle' });
        Object.keys(segClass).sort().forEach(function (k) {
            var cl = segClass[k], color = P[cl.c % P.length];
            traces.push({ type: 'scatter', mode: 'lines', x: cl.x, y: cl.y, text: cl.t, customdata: cl.cd, showlegend: false,
                          line: { color: color, width: cl.style === 'idle' ? 1.6 : 3.2, dash: cl.style === 'dash' ? 'dash' : 'solid' },
                          hovertemplate: '%{text}<extra></extra>', meta: 'seg' });
        });
        // connectors help on a small chip; across 10+ pairs they cross the whole map
        if (listed.length <= 10) {
            listed.forEach(function (pair) {
                var ks = [];
                m.segments.forEach(function (s, k) { if (s.pair === pair && place[k]) ks.push(k); });
                if (ks.length !== 2) return;
                var ia = col[m.segments[ks[0]].qubit_path], ib = col[m.segments[ks[1]].qubit_path];
                var kl = ia <= ib ? ks[0] : ks[1], kr = ia <= ib ? ks[1] : ks[0];
                var c = colour[pair] || 0, cn = connectors[c] || (connectors[c] = { x: [], y: [] });
                cn.x.push(place[kl].x1, place[kr].x0, null);
                cn.y.push(ghz(m.segments[kl].frequency_hz), ghz(m.segments[kr].frequency_hz), null);
            });
            Object.keys(connectors).forEach(function (c) {
                traces.push({ type: 'scatter', mode: 'lines', x: connectors[c].x, y: connectors[c].y, showlegend: false, hoverinfo: 'skip',
                              line: { color: P[c % P.length], width: 1, dash: 'dot' }, meta: 'connector' });
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
        // ticks only where the drawn scan itself runs past the scale (the outline is a reference)
        if (thin.x.length || G.clipX.length) {
            traces.push({ type: 'scatter', mode: 'markers', x: thin.x.concat(G.clipX), y: thin.y.concat(G.clipY),
                          text: thin.t.concat(G.clipT), showlegend: false,
                          marker: { color: ink, size: thin.x.map(function () { return 10; }).concat(G.clipX.map(function () { return 7; })),
                                    symbol: thin.x.map(function () { return 'line-ew'; }).concat(G.clipX.map(function () { return 'triangle-right'; })),
                                    line: { color: ink, width: 2 } },
                          hovertemplate: '%{text}<extra></extra>', meta: 'defects-clips' });
        }
        if (partners.length) {
            traces.push({ type: 'scatter', mode: 'text', x: partners.map(function (p) { return p.x; }), y: partners.map(function (p) { return p.y; }),
                          text: partners.map(function (p) { return p.t; }), textposition: partners.map(function (p) { return p.pos; }),
                          textfont: { size: 9, color: ink }, hoverinfo: 'skip', showlegend: false, cliponaxis: false, meta: 'partners' });
        }

        // ---- the chip drawing: leaders, bars, nodes ----
        var barPts = { x: [], y: [], t: [], cd: [] }, hidden = 0;
        if (drawing) {
            qs.forEach(function (q, i) {
                var nd = lay.node[q.path];
                if (!nd) return;
                shapes.push({ type: 'line', xref: 'x', x0: nd.x, x1: i + 0.5, yref: 'paper', y0: paperY(nd.y), y1: colLo,
                              line: { color: muted, width: 0.8 }, layer: 'below', name: 'leader' });
            });
            var px = function (nd) { return { X: nd.x * colPx, Y: (nd.y - y2r[0]) * rowPx }; };
            var barList = [], items = [];
            Object.keys(pairs).sort(natCmp).forEach(function (pair) {
                var pp = pairs[pair], a = lay.node[pp.control_path], b = lay.node[pp.target_path];
                if (!a || !b) return;
                var segs = m.segments.filter(function (s) { return s.pair === pair; });
                var refused = (m.refused || []).filter(function (r) { return r.pair === pair; });
                var computed = segs.length > 0, color = P[(colour[pair] || 0) % P.length];
                shapes.push({ type: 'line', xref: 'x', yref: 'y2', x0: a.x, y0: a.y, x1: b.x, y1: b.y, layer: 'below', name: 'bar:' + pair,
                              line: computed ? { color: color, width: 6 } : { color: muted, width: 1.5, dash: 'dot' } });
                barList.push({ pair: pair, a: px(a), b: px(b), half: computed ? BAR_HALF : 1 });
                var gg = g(pair);
                gg.barX = [a.x, b.x]; gg.barY = [a.y, b.y];
                var movers = segs.filter(function (s) { return s.moves; }), label = '';
                if (computed) {
                    if (movers.length + refused.length >= 2) {
                        var own = movers.find(function (s) { return s.role === 'control'; });
                        var ownRef = refused.find(function (r) { return r.qubit === pp.control; });
                        label = own ? own.qubit + ' ' + f3(own.frequency_hz) : ownRef ? ownRef.qubit + ' ?'
                            : movers.length ? movers[0].qubit + ' ' + f3(movers[0].frequency_hz) : '?';
                    } else if (movers.length === 1) label = f3(movers[0].frequency_hz);
                    else if (refused.length) label = '?';
                }
                if (label && movers.some(function (s) { return /extrapolated/.test(s.kind || ''); })) label = '~' + label;
                if (label) items.push({ pair: pair, text: label, a: px(a), b: px(b), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, color: color });
                var op = segs.length ? segs[0] : null, t;
                if (computed) {
                    t = esc(pair) + ' · ' + esc(op.operation) + segs.map(function (s) {
                        return '<br>' + esc(s.qubit) + ' ' + f3(s.frequency_hz) + ' GHz ' + esc(s.kind || '') + (s.moves ? '' : ' (stays at idle)');
                    }).join('') + refused.map(function (r) { return '<br>' + esc(r.qubit) + ' not drawn: ' + esc(r.reason); }).join('') +
                        '<br>Δ ' + fmtMHz(op.detuning_hz, true) + (op.nearest ? ' · ' + esc(op.nearest.name) + (op.nearest.lobe ? ' (' + esc(op.nearest.lobe) + ')' : '') + ' ' + fmtMHz(op.nearest.distance_hz, true) : '');
                } else if (refused.length) {
                    t = esc(pair) + ' · ' + esc(refused[0].operation) + refused.map(function (r) { return '<br>' + esc(r.qubit) + ' not drawn: ' + esc(r.reason); }).join('');
                } else {
                    t = esc(pair) + ': no ' + esc(m.op || 'cz') + ' operation';
                }
                if (mismatch && pair === mismatch.pair) t += mmHover;
                barPts.x.push((a.x + b.x) / 2); barPts.y.push((a.y + b.y) / 2); barPts.t.push(t);
                barPts.cd.push([op ? op.path : (refused[0] && refused[0].path) || '', pair]);
            });
            var placed = qs.filter(function (q) { return lay.node[q.path]; });
            // fix3 P1-3: each label where it hides no other bar, node or label (see placeLabels);
            // its frame carries its bar's colour, so a label always names its own bar
            var spots = placeLabels(items, barList, placed.map(function (q) { return px(lay.node[q.path]); }),
                                    { rowPx: rowPx, x0: 0, x1: plotW, y0: 0, y1: drawPx + GAP_PX - 2 });
            items.forEach(function (it) {
                var sp = spots[it.pair];
                if (!sp) { hidden++; return; }
                annotations.push({ xref: 'x', yref: 'y2', x: it.x, y: it.y, text: esc(it.text), showarrow: false,
                                   font: { size: LABEL_FONT, color: ink }, bgcolor: card, bordercolor: it.color, borderwidth: 1,
                                   borderpad: 1, xshift: sp.dx, yshift: sp.dy, name: 'bar-label:' + it.pair });
            });
            traces.push({ type: 'scatter', mode: 'markers', x: barPts.x, y: barPts.y, yaxis: 'y2', text: barPts.t, customdata: barPts.cd,
                          marker: { size: 16, color: 'rgba(0,0,0,0)' }, showlegend: false, hovertemplate: '%{text}<extra></extra>', meta: 'bars' });
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
        var lossDrawn = !!(G.fillX.length || O.lineX.length);
        if (lossDrawn || bars.x.length) {
            annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 1, xanchor: 'right', yanchor: 'bottom', showarrow: false, yshift: 8,
                               text: 'loss 0–' + Math.round(100 * scale) + ' %', font: { size: 9, color: muted }, name: 'scale' });
        }

        // ---- notes under the map: one line per qubit, only when something is off; the
        //      ledger's axis changes merged across qubits (same fields, same change) ----
        var notes = [], detail = [], axisNotes = 0, pendingNotes = 0, sourceNotes = 0;
        if (m.refused.length) notes.push('Not drawn: ' + m.refused.map(function (r) {
            return r.qubit + ' in ' + r.pair + ' ' + r.operation + ' (' + r.reason + ')'; }).join('; ') + '.');
        if (m.missing.length) notes.push('No ' + (m.op || 'such') + ' operation on ' + m.missing.join(', ') + '.');
        if (!drawing) notes.push('No chip drawing: ' + (lay.why || 'no qubits') + '.');
        if (loss && loss.reason) notes.push('Measured loss: ' + loss.reason + '.');
        if (root._czLossError) notes.push('Measured loss could not be loaded: ' + root._czLossError + '.');
        var stale = (loss && loss.staleness) || {};
        if (stale.mode === 'building' || stale.mode === 'preparing') {
            pendingNotes++;   // the axis check is not done yet: never counted as one
            // fix3 P2-5: once the re-ask budget is spent the note says so, never "follows" forever
            notes.push(root._czLossGaveUp ? 'The change history was still being prepared after ' + Math.round(RETRY_N * RETRY_MS / 1000) +
                       ' s; the axis check of the loss scans was not done (reload the page to try again).'
                       : 'The change history is being prepared; the axis check of the loss scans follows.');
        }
        var none = [], unchecked = [], sg = {}, sgOrder = [];
        qs.forEach(function (q) {
            var e = curves[q.name], parts = [];
            if (!e) { if (!q.loss_data) none.push(q.name); return; }
            var mk = e.marker || {};
            if ((mk.status === 'no_match' || mk.status === 'unreadable') && (q.defects || []).length) parts.push(mk.label);
            if (mk.status === 'match' && !mk.same_as_scan && e.marker_curve) {
                parts.push('stored defects equal run #' + mk.run_id + '\'s (dotted), newest scan ' + (e.scan ? '#' + e.scan.run_id : 'none'));
            }
            if (mk.status === 'ambiguous' && e.marker_curve) {
                parts.push(mk.label + ' (dotted), newest scan ' + (e.scan ? '#' + e.scan.run_id : 'none'));
            }
            [e.scan, e.marker_curve].forEach(function (c) {
                if (!c) return;
                if (c.idle_note) parts.push('run #' + c.run_id + ': ' + c.idle_note);
                if (c.identity === 'unchecked' && unchecked.indexOf(q.name) < 0) unchecked.push(q.name);
            });
            ((stale.qubits || {})[q.name] || []).forEach(function (s) {
                var f = (s.fields || []).slice().sort();
                var k = f.join(',') + '|' + s.changed_run_id + '|' + (s.changed_provenance || '') + '|' + (s.changed_run_id == null ? s.changed_t : '');
                var gr = sg[k];
                if (!gr) { gr = sg[k] = { fields: f, words: changedWords(s), qs: {}, order: [], unproven: [] }; sgOrder.push(k); }
                if (!gr.qs[q.name]) { gr.qs[q.name] = []; gr.order.push(q.name); }
                if (gr.qs[q.name].indexOf(s.run_id) < 0) gr.qs[q.name].push(s.run_id);
                if (s.order_proven === false || / order not proven$/.test(s.text || '')) gr.unproven.push(s.run_id);
            });
            if (parts.length) { notes.push(q.name + ': ' + parts.join('; ') + '.'); sourceNotes++; }
            if (!e.scan && !e.marker_curve && !q.loss_data) none.push(q.name);
            detail.push({ q: q, e: e });
        });
        sgOrder.forEach(function (k) { notes.push(staleGroupLine(sg[k])); axisNotes++; });
        if (unchecked.length) {
            notes.push('Chip identity unchecked for the loss runs of ' + unchecked.join(', ') +
                       ' (no change history holds them and one side declares no chip name).');
        }
        if (none.length) notes.push('No population-loss data for ' + (none.length === qs.length ? 'any qubit' : none.join(', ')) +
                                    ' (none stored, no scan in the data folders).');
        var nEl = root.querySelector('#czp-map-notes');
        if (nEl) nEl.textContent = notes.join('\n');
        var notesBox = root.querySelector('#czp-map-notes-box');
        if (notesBox) notesBox.hidden = !notes.length;
        var summary = root.querySelector('#czp-map-notes-summary');
        if (summary) {
            var kinds = [];
            function kind(count, label) { if (count) kinds.push(count + ' ' + label + (count === 1 ? '' : 's')); }
            kind(axisNotes, 'axis check');
            kind(pendingNotes, 'pending check');
            kind(sourceNotes, 'data source');
            kind(unchecked.length ? 1 : 0, 'chip identity check');
            kind(none.length ? 1 : 0, 'data gap');
            kind(notes.length - axisNotes - pendingNotes - sourceNotes - (unchecked.length ? 1 : 0) - (none.length ? 1 : 0), 'other note');
            summary.textContent = 'Notes (' + notes.length + ')' + (kinds.length ? ': ' + kinds.join(', ') : '');
        }
        var kEl = root.querySelector('#czp-map-key');
        if (kEl) {
            kEl.textContent = 'Key: ' + KEY + (!m.op ? '; cz per pair = each pair\'s cz, else its first CZ operation' : '') + (lossDrawn ? '; gray = measured population loss of the newest scan, ' +
                'dotted = the run whose defects equal the stored list (matched by content), short dash = a stored defect, ' +
                '▸ = past the column scale' : '') + (hidden ? '; a bar without a label: hover it' : '') + '.' +
                (mismatch ? ' Outline: the map shows ' + mismatch.shown + ' for ' + mismatch.pair + '; selected below: ' +
                 mismatch.selected + '.' : '');
        }
        renderDetail(root, loss, detail);

        var L = layout({
            margin: MARGIN, height: H, showlegend: false, hovermode: 'closest',
            xaxis: { tickmode: 'array', tickvals: qs.map(function (q, i) { return i + 0.5; }),
                     // fix3 P2-7: tick text is pseudo-HTML like every hover
                     ticktext: qs.map(function (q) { return esc(q.name); }),
                     range: [0, n], showgrid: false, zeroline: false, fixedrange: true, side: drawing ? 'top' : 'bottom',
                     anchor: 'y', tickfont: { size: 10 } },
            yaxis: Object.assign({ title: { text: 'frequency (GHz)' }, domain: [colLo, 1] }, yr ? { range: yr, autorange: false } : {}),
            shapes: shapes, annotations: annotations
        });
        if (drawing) L.yaxis2 = { domain: [0, dd], range: y2r, visible: false, fixedrange: true, anchor: 'x', showgrid: false, zeroline: false };
        el.setAttribute('data-segments', String(m.segments.length));
        el.setAttribute('data-layout', lay.mode);
        el.setAttribute('data-drawn-op', shown || m.op || '');
        var overlay = { seg: segIdx, bar: barIdx, geo: geo, base: shapes.slice(), ink: ink, hl: null, drawing: drawing };
        var sel = selectionShapes(overlay, selPath);
        L.shapes = sel.concat(L.shapes || []);
        overlay.sel = selPath;
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

    /* How a ledger change is named (the server's cz_gate_plan.change_words): "changed by run
       #M" only for a run the ledger proves wrote it, "first seen in run #M's saved state" for
       a run that only carried it first. An answer without the words names the run neutrally. */
    function changedWords(s) {
        if (s.changed_words) return s.changed_words;
        return s.changed_run_id != null ? 'seen in run #' + s.changed_run_id : 'changed';
    }

    /* One merged axis line: "z.joint_offset changed after the scans of qA1 #824, qA2 #1822 and
       #676 (first seen in run #3450's saved state)"; a proven writer is named in the verb. */
    function staleGroupLine(gr) {
        var who = gr.order.map(function (q) { return q + ' #' + gr.qs[q].join(' and #'); }).join(', ');
        var nScans = gr.order.reduce(function (s, q) { return s + gr.qs[q].length; }, 0);
        var verb = /^changed/.test(gr.words), fields = gr.fields.join(', ') || 'a flux-model input';
        return 'Axis check: ' + fields + ' ' + (verb ? gr.words : 'changed') + ' after the scan' + (nScans === 1 ? '' : 's') +
            ' of ' + who + (verb ? '' : ' (' + gr.words + ')') +
            (gr.unproven.length ? '; order not proven for #' + gr.unproven.join(', #') : '') + '.';
    }

    /* The ledger's axis lines of one qubit (one per drawn curve, so up to two) as one: two
       runs whose axes predate the same change read "axis of runs #A and #B predate ...". */
    function staleText(lines) {
        var by = {}, order = [];
        lines.forEach(function (s) {
            var k = s.changed_run_id + '|' + (s.fields || []).length + '|' + (s.changed_provenance || '');
            if (!by[k]) { by[k] = { runs: [], n: (s.fields || []).length, words: changedWords(s), text: s.text }; order.push(k); }
            by[k].runs.push(s.run_id);
        });
        return order.map(function (k) {
            var gr = by[k];
            if (gr.runs.length === 1) return gr.text;
            return 'axis of runs #' + gr.runs.join(' and #') + ' predate ' + gr.n + ' flux-model input change' + (gr.n === 1 ? '' : 's') +
                ' (' + gr.words + ')';
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

    /* The operation selected in the table / detail stays outlined on the map. fix3 P1-1: the
       map follows the selection -- when the selected name is one of its operations and it
       draws another for that pair, it switches to that name (one refetch); when it cannot,
       a repaint names both in the outline's hover and the key. */
    function select(root, path) {
        if (!root) return Promise.resolve();
        var m = root._czMap, el = root.querySelector('#czp-map'), o = el && el._czpOverlay;
        root._czFollow = !m;          // a map still on its way follows the selection when it lands
        if (m) {
            var want = followName(root, m, path);
            if (want) { root.querySelector('#czp-map-op').value = want; return draw(root); }
            var shown = drawnOp(m, pairOf(path)), name = opNameOf(path);
            var mm = name && shown && shown !== name ? pairOf(path) + '|' + shown + '|' + name : '';
            if (mm !== (root._czMismatch || '')) return paint(root);
        }
        if (!o || o.sel === path) return Promise.resolve();
        o.sel = path;
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
                (c.loss_max != null ? ', loss ' + pct(c.loss_min) + ' … ' + pct(c.loss_max) : '') +
                (c.identity === 'unchecked' ? ', chip identity unchecked' : '') + ')' +
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
        // the table below lists only the operation the map draws (all when "cz per pair")
        root.dispatchEvent(new CustomEvent('czp:mapop', { detail: op }));
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
                if (root._czFollow) {
                    root._czFollow = false;
                    var want = followName(root, m, root.getAttribute('data-selected'));
                    if (want) { opSel.value = want; return draw(root); }
                }
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
                root._czLossGaveUp = false;
                if (mode === 'building' || mode === 'preparing') {
                    if ((tries || 0) < RETRY_N) {
                        setTimeout(function () { if (document.body.contains(root)) loadLoss(root, (tries || 0) + 1); }, RETRY_MS);
                    } else {
                        root._czLossGaveUp = true;        // fix3 P2-5: the note says it ended
                    }
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
        // a name the reader picks here stays until a new selection below asks for another
        if (opSel) opSel.addEventListener('change', function () { root._czFollow = false; draw(root); });
        root._czFollow = true;
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
        placeLabels: placeLabels, labelSize: labelSize, partnerWidth: partnerWidth, partnerLines: partnerLines, followName: followName, drawnOp: drawnOp,
        DRAW_PX: DRAW_PX, ROW_PX: ROW_PX, DRAW_MAX_FRAC: DRAW_MAX_FRAC, LOSS_W: LOSS_W,
        SHORT_BAR_PX: SHORT_BAR_PX, ROW_LABEL_MIN_PX: ROW_LABEL_MIN_PX, NODE_R: NODE_R, BAR_HALF: BAR_HALF,
        CLUSTER_PX: CLUSTER_PX, RETRY_N: RETRY_N, _esc: esc
    };
})();
