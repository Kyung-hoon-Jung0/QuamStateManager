/* GateTimeline: what several output channels play at the same time.
 *
 * One lane per channel, every lane on ONE shared time axis (ns), the config
 * samples drawn as they are (a constant pulse is a flat step, an arbitrary
 * pulse its real shape). The server builds the data; this file only draws it:
 * it never computes physics and never invents a value for a missing one.
 *
 *   var h = GateTimeline.render(hostElement, data, opts);
 *   h.setScale("lane" | "common"); h.setFold(bool); h.setShowSim(bool);
 *   h.valuesAt(t_ns) -> [{key, operation|null, v|null}]   (what hover shows)
 *   h.laneCount() -> {gate, spectator, drawn};  h.destroy();
 *
 * Layout: one Plotly figure, one y axis per drawn lane stacked by domain, all
 * traces on the single x axis, so a zoom or pan on any lane moves every lane
 * while a lane's own y zoom stays its own. Lane titles sit horizontally in the
 * left margin; each segment's two-line text sits in a band of the lane kept
 * free of data (top band, and a bottom band when a lane has several segments
 * and labels alternate rows). Rendering goes through window._plotlyRender,
 * sizing through window.PlotHost, colours through window.PlotTheme and CSS
 * variables read at run time (no colour literals).
 */
(function () {
    "use strict";

    var GATE_PX = 110;         // fixed lane heights: many lanes scroll in the host, labels never overlap
    var SPECT_PX = 64;
    var GAP_PX = 14;
    var BAND_PX = 36;          // a two-line segment text band
    var TOP_AXIS_LANES = 6;    // more drawn lanes than this: time ticks at the top too
    var MAX_HOVER_POINTS = 4000;
    var CHAR_PX = 5.8;         // average width of an 11 px UI-font character, for fitting text
    var LABEL_PX = 12;         // lane label size; every other chart text is TEXT_PX (kit small text)
    var TEXT_PX = 11;
    var TICK_ROOM = 52;        // room for the y tick labels ("-0.05 V") between gutter and plot
    var FILL_OPACITY = 0.18;
    var BAR_PX = 3;            // the lane colour bar at the gutter's left edge
    var CHIP_ALPHA = 0.16;     // tint behind the lane name chip

    var FLAG_TEXT = {
        over_range: "over the port range",
        bad_length: "length is not a multiple of 4 ns"
    };
    var TRUNCATED_TEXT = "only part of this waveform was received";

    function cssVar(name) {
        try {
            var v = getComputedStyle(document.documentElement).getPropertyValue(name);
            return (v && v.trim()) || "";
        } catch (e) { return ""; }
    }
    function theme() {
        var pt = window.PlotTheme || {};
        var pal = (pt.palette && pt.palette()) || [];
        var text = cssVar("--plot-axis-text") || cssVar("--pico-color");
        var grid = cssVar("--plot-grid") || text;
        return {
            trace: pal[0] || text,
            sim: pal[1] || text,
            flag: (pt.lossColor && pt.lossColor()) || pal[2] || text,
            text: text,
            grid: grid,
            muted: cssVar("--pico-muted-color") || text
        };
    }

    /* A see-through copy of a theme colour for a pulse body: PlotTheme.withAlpha
       for the forms it reads; any other form is resolved by the browser first. */
    function faint(col, alpha) {
        var a = alpha === undefined ? FILL_OPACITY : alpha;
        var pt = window.PlotTheme || {};
        var out = pt.withAlpha ? pt.withAlpha(col, a) : col;
        if (out !== col) return out;
        try {
            var probe = document.createElement("span");
            probe.style.setProperty("color", col);
            document.body.appendChild(probe);
            var rgb = getComputedStyle(probe).color;
            probe.remove();
            out = pt.withAlpha ? pt.withAlpha(rgb, a) : rgb;
        } catch (e) { /* no DOM */ }
        return out || col;
    }

    function isIndex(x) { return typeof x === "number" && isFinite(x) && Math.floor(x) === x && x >= 0; }

    /* The identity colour for an index: the house palette, modulo its length. */
    function laneColor(index) {
        var pt = window.PlotTheme || {};
        var pal = (pt.palette && pt.palette()) || [];
        if (!pal.length) return theme().text;
        var i = isIndex(index) ? index : 0;
        return pal[i % pal.length];
    }
    /* Spectator lanes are context, not the subject: one quiet colour for all. */
    function quietColor() {
        var pal = (window.PlotTheme && window.PlotTheme.palette && window.PlotTheme.palette()) || [];
        return cssVar("--pico-muted-color") || pal[pal.length - 1] || theme().text;
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    function num(x) { return typeof x === "number" && isFinite(x); }
    function fmt(v) { return String(+v.toPrecision(3)); }
    function fmtV(v) { return num(v) ? fmt(v) + " V" : "?"; }

    /* The value a lane plays at time t, from its samples only.
       Inside a segment (start <= t < end, or t == end of the last one that ends
       there) the value is the last received sample at or before t (samples are
       steps on the shared axis). Past the received samples of a truncated
       segment the operation is named and v is null. Outside every segment the
       lane is idle: operation null, v null. */
    function laneValueAt(lane, t) {
        var segs = lane.segments || [];
        var seg = null, i;
        for (i = 0; i < segs.length; i++) {
            if (t >= segs[i].start_ns && t < segs[i].end_ns) { seg = segs[i]; break; }
        }
        if (!seg) {
            for (i = 0; i < segs.length; i++) {
                if (t === segs[i].end_ns) { seg = segs[i]; break; }
            }
        }
        if (!seg) return { key: lane.key, operation: null, v: null };
        var ts = seg.t_ns || [], vs = seg.v || [], v = null;
        for (i = 0; i < ts.length; i++) {
            if (ts[i] <= t) v = num(vs[i]) ? vs[i] : null;
            else break;
        }
        if (seg.truncated && ts.length && t > ts[ts.length - 1]) v = null;
        return { key: lane.key, operation: seg.operation || null, v: v };
    }

    function readout(val) {
        if (!val.operation) return "idle";
        if (val.v === null) return val.operation + ": not received";
        return val.operation + ": " + fmtV(val.v);
    }

    function titleLines(lane) {
        var lines = [lane.label || lane.key];
        if (lane.port) lines.push(lane.port);
        var lim = lane.limit_v && num(lane.limit_v[0]) && num(lane.limit_v[1])
            ? "limit " + fmt(lane.limit_v[0]) + " to " + fmt(lane.limit_v[1]) + " V" : "range not known";
        lines.push(lim);
        if (num(lane.offset_v) && lane.offset_v !== 0) lines.push("offset " + fmtV(lane.offset_v));
        return lines;
    }

    /* Line 2 of a segment's text, from the received samples only. */
    function levelText(s) {
        var vs = (s.v || []).filter(num);
        var len = (num(s.length_ns) ? s.length_ns : "?") + " ns";
        if (!vs.length) return "no samples, " + len;
        var mx = Math.max.apply(null, vs), mn = Math.min.apply(null, vs);
        if (mx === mn) return "level " + fmtV(mx) + ", " + len;
        return "max " + fmtV(mx) + (mn < 0 ? ", min " + fmtV(mn) : "") + ", " + len;
    }
    function shorten(name, maxChars) {
        if (name.length <= maxChars) return name;
        if (maxChars < 5) return name.slice(0, Math.max(1, maxChars - 1)) + "\u2026";
        var keep = maxChars - 1, head = Math.ceil(keep / 2);
        return name.slice(0, head) + "\u2026" + name.slice(name.length - (keep - head));
    }

    function dataExtent(lane, showSim) {
        var lo = Infinity, hi = -Infinity;
        function take(v) { if (num(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
        (lane.segments || []).forEach(function (s) { (s.v || []).forEach(take); });
        if (showSim && lane.sim) (lane.sim.v || []).forEach(take);
        if (lo === Infinity) { lo = 0; hi = 0; }
        return [Math.min(lo, 0), Math.max(hi, 0)];   // 0 V stays visible
    }
    /* The y range that puts the data in the lane's middle band and leaves the
       text bands (px) free above and, when used, below. */
    function bandedRange(ext, lanePx, topPx, botPx) {
        var span = ext[1] - ext[0];
        if (!(span > 0)) span = Math.max(Math.abs(ext[0]), Math.abs(ext[1]), 0.01);
        var dataPx = Math.max(12, lanePx - topPx - botPx - 6);
        var perPx = span / dataPx;
        return [ext[0] - (botPx + 3) * perPx, ext[1] + (topPx + 3) * perPx];
    }

    function render(host, data, opts) {
        opts = opts || {};
        data = data || {};
        var state = {
            scale: opts.scale === "common" ? "common" : "lane",
            fold: opts.foldSpectators !== false,
            showSim: opts.showSim !== false
        };
        var lanes = (data.lanes || []).slice();
        var gate = lanes.filter(function (l) { return l.group !== "spectator"; });
        /* Identity colour: lane.color (an index) when given; otherwise gate lanes
           take 0, 1, 2 ... in order and every spectator one quiet colour. */
        var colorIndex = {};
        gate.forEach(function (l, i) { colorIndex[l.key] = isIndex(l.color) ? l.color : i; });
        lanes.forEach(function (l) { if (l.group === "spectator" && isIndex(l.color)) colorIndex[l.key] = l.color; });
        function laneCol(l) {
            return colorIndex.hasOwnProperty(l.key) ? laneColor(colorIndex[l.key]) : quietColor();
        }
        var spect = lanes.filter(function (l) { return l.group === "spectator"; });
        var anySim = lanes.some(function (l) { return !!l.sim; });
        var listeners = [];
        var destroyed = false;

        function on(node, type, fn) {
            node.addEventListener(type, fn);
            listeners.push([node, type, fn]);
        }

        var root = el("div");
        root.setAttribute("data-gate-timeline", "");
        host.appendChild(root);

        function notesInto(parent) {
            (data.notes || []).forEach(function (n) { parent.appendChild(el("p", "ui-note", n)); });
        }

        // No chart at all: the statement and the notes, nothing guessed.
        if (data.cannot_draw || !lanes.length) {
            if (data.cannot_draw) root.appendChild(el("p", "ui-state ui-state--empty", data.cannot_draw));
            else root.appendChild(el("p", "ui-state ui-state--empty", "Nothing plays on any channel."));
            notesInto(root);
            return {
                destroy: function () { if (root.parentNode) root.parentNode.removeChild(root); destroyed = true; },
                setScale: function (m) { state.scale = m === "common" ? "common" : "lane"; },
                setFold: function (b) { state.fold = !!b; },
                setShowSim: function (b) { state.showSim = !!b; },
                valuesAt: function () { return []; },
                laneCount: function () { return { gate: gate.length, spectator: spect.length, drawn: 0 }; }
            };
        }

        // Toolbar: vertical scale (kit segmented control), the spectator group
        // summary with its unfold control, and the simulation switch.
        // role=toolbar, not group: the base stylesheet stretches every
        // [role=group] to the full width.
        var bar = el("div", "ui-actions ui-actions--start");
        bar.setAttribute("data-gate-timeline-bar", "");
        var seg = el("div", "ui-segmented");
        seg.setAttribute("role", "toolbar");
        seg.setAttribute("aria-label", "Vertical scale");
        var scaleBtns = [["lane", "Per lane"], ["common", "Shared"]].map(function (o) {
            var b = el("button", "ui-segmented-opt", o[1]);
            b.type = "button";
            b.setAttribute("data-scale", o[0]);
            on(b, "click", function () { handle.setScale(o[0]); });
            seg.appendChild(b);
            return b;
        });
        var scaleLab = el("span", "ui-note", "V scale");
        scaleLab.setAttribute("data-scale-label", "");
        bar.appendChild(scaleLab);
        bar.appendChild(seg);
        // the spectator group: the kit checkbox, unchecked = folded
        var foldBox = null;
        if (spect.length) {
            var flab = el("label", "ui-check");
            flab.setAttribute("data-spectator-summary", "");
            foldBox = el("input");
            foldBox.type = "checkbox";
            foldBox.setAttribute("data-fold", "");
            on(foldBox, "change", function () { handle.setFold(!foldBox.checked); });
            flab.appendChild(foldBox);
            flab.appendChild(document.createTextNode(
                "Show " + spect.length + " spectator line" + (spect.length === 1 ? "" : "s")));
            bar.appendChild(flab);
        }
        var simBox = null;
        if (anySim) {
            var lab = el("label", "ui-check");
            simBox = el("input");
            simBox.type = "checkbox";
            simBox.setAttribute("data-show-sim", "");
            on(simBox, "change", function () { handle.setShowSim(simBox.checked); });
            lab.appendChild(simBox);
            lab.appendChild(document.createTextNode("Show simulated output"));
            bar.appendChild(lab);
        }
        // where the controls go: above the chart (default), into the page's own
        // element (opts.toolbarHost), or nowhere (opts.toolbar === false; the
        // handle's setters still work)
        if (opts.toolbar !== false) {
            if (opts.toolbarHost && opts.toolbarHost.appendChild) opts.toolbarHost.appendChild(bar);
            else root.appendChild(bar);
        }

        var plot = el("div");
        plot.setAttribute("data-gate-timeline-plot", "");
        root.appendChild(plot);

        var notesBox = el("div");
        notesInto(notesBox);
        root.appendChild(notesBox);

        function drawnLanes() { return state.fold ? gate.slice() : gate.concat(spect); }

        /* Hover grid: every 1 ns over the drawn span (coarser only past
           MAX_HOVER_POINTS) plus every sample and edge, so the unified readout
           appears at any time on the axis. */
        function hoverTimes(drawn) {
            var lo = Infinity, hi = -Infinity, seen = {}, out = [];
            function add(t) { if (num(t) && !seen[t]) { seen[t] = 1; out.push(t); } }
            drawn.forEach(function (l) {
                (l.segments || []).forEach(function (s) {
                    if (num(s.start_ns)) lo = Math.min(lo, s.start_ns);
                    if (num(s.end_ns)) hi = Math.max(hi, s.end_ns);
                    (s.t_ns || []).forEach(add);
                    add(s.start_ns); add(s.end_ns);
                });
            });
            if (lo !== Infinity) {
                lo = Math.min(0, lo);   // the axis starts at program time 0
                var step = Math.max(1, Math.ceil((hi - lo) / MAX_HOVER_POINTS));
                for (var t = Math.floor(lo); t <= hi; t += step) add(t);
            }
            return out.sort(function (a, b) { return a - b; });
        }

        function spanOf(drawn) {
            var lo = Infinity, hi = -Infinity;
            drawn.forEach(function (l) {
                (l.segments || []).forEach(function (s) {
                    lo = Math.min(lo, s.start_ns); hi = Math.max(hi, s.end_ns);
                });
                if (state.showSim && l.sim && (l.sim.t_ns || []).length) {
                    lo = Math.min(lo, l.sim.t_ns[0]); hi = Math.max(hi, l.sim.t_ns[l.sim.t_ns.length - 1]);
                }
            });
            // program time starts at 0: a late first pulse (a port delay) must show as a gap
            if (lo === Infinity) return [0, 1];
            lo = Math.min(0, lo);
            hi = Math.max(hi, lo + 1);
            return [lo, hi + Math.max(2, 0.03 * (hi - lo))];
        }

        function uiFont() {
            try { return getComputedStyle(document.body).fontFamily || cssVar("--pico-font-family"); }
            catch (e) { return cssVar("--pico-font-family"); }
        }

        /* Up to three y ticks: the data's minimum (when below 0), 0 V and its
           maximum (when above 0); a spectator lane only 0 V and its level. */
        function laneTicks(lane, spectLane) {
            var e = dataExtent(lane, state.showSim), vals = [0];
            if (spectLane) {
                var lvl = Math.abs(e[0]) > Math.abs(e[1]) ? e[0] : e[1];
                if (lvl !== 0) vals.push(lvl);
            } else {
                if (e[0] < 0) vals.unshift(e[0]);
                if (e[1] > 0) vals.push(e[1]);
            }
            return { tickvals: vals, ticktext: vals.map(fmtV) };
        }

        function figure() {
            var c = theme();
            var drawn = drawnLanes();
            var n = drawn.length;
            var heights = drawn.map(function (l) { return l.group === "spectator" ? SPECT_PX : GATE_PX; });
            var plotPx = heights.reduce(function (a, b) { return a + b; }, 0) + Math.max(0, n - 1) * GAP_PX;
            var longest = 0;
            drawn.forEach(function (l) {
                titleLines(l).forEach(function (s, k) {
                    longest = Math.max(longest, s.length * (k === 0 ? LABEL_PX / TEXT_PX : 1));
                });
            });
            var gutter = Math.ceil(longest * CHAR_PX) + BAR_PX + 16;
            var topAxis = n > TOP_AXIS_LANES;
            var margin = { l: gutter + TICK_ROOM, r: 12, t: topAxis ? 30 : 6, b: 38 };
            var span = spanOf(drawn);
            var widthPx = Math.max(200, ((host.clientWidth || 900) - margin.l - margin.r));
            var family = uiFont();
            var traces = [], shapes = [], fixed = [];
            var layout = {
                margin: margin,
                height: plotPx + margin.t + margin.b,
                showlegend: false,
                hovermode: "x unified",
                hoversubplots: "axis",
                hoverdistance: -1,
                font: { family: family, size: TEXT_PX, color: c.text },
                hoverlabel: { font: { family: family, size: TEXT_PX } },
                xaxis: { title: { text: "time (ns)", font: { size: TEXT_PX } }, zeroline: false,
                         anchor: "y" + (n > 1 ? n : ""), range: span.slice(), autorange: false, showgrid: true,
                         gridcolor: c.grid, tickfont: { size: TEXT_PX } },
                shapes: shapes
            };
            if (topAxis) {
                layout.xaxis2 = { overlaying: "x", matches: "x", side: "top", anchor: "y", showgrid: false,
                                  zeroline: false, tickfont: { size: TEXT_PX } };
                // an axis with no trace is not drawn: one empty trace carries the top ticks
                traces.push({ x: span.slice(), y: [null, null], xaxis: "x2", yaxis: "y", type: "scatter",
                              mode: "lines", hoverinfo: "skip", meta: { role: "top-axis" } });
            }
            // per lane: which text rows it uses (several segments alternate top / bottom)
            var plan = drawn.map(function (lane) {
                var segs = (lane.segments || []).slice().sort(function (a, b) { return a.start_ns - b.start_ns; });
                return { segs: segs, bottom: segs.length > 1 };
            });
            var commonExt = null;
            if (state.scale === "common") {
                var lo = 0, hi = 0;
                drawn.forEach(function (l) {
                    var e = dataExtent(l, state.showSim);
                    lo = Math.min(lo, e[0]); hi = Math.max(hi, e[1]);
                });
                commonExt = [lo, hi];
            }
            var times = hoverTimes(drawn);
            var y = 0;
            drawn.forEach(function (lane, i) {
                var ax = i === 0 ? "y" : "y" + (i + 1);
                var axKey = i === 0 ? "yaxis" : "yaxis" + (i + 1);
                var lanePx = heights[i];
                var top = 1 - y / plotPx;
                var bottom = Math.max(0, 1 - (y + lanePx) / plotPx);
                y += lanePx + GAP_PX;
                var spectLane = lane.group === "spectator";
                // head room for the texts is per lane, in both scales
                var range = bandedRange(commonExt || dataExtent(lane, state.showSim), lanePx, BAND_PX,
                                        plan[i].bottom ? BAND_PX : 0);
                var ticks = laneTicks(lane, spectLane);
                if (commonExt) ticks = { tickvals: [commonExt[0], 0, commonExt[1]].filter(function (v, k, a) { return a.indexOf(v) === k; }),
                                         ticktext: null };
                if (!ticks.ticktext) ticks.ticktext = ticks.tickvals.map(fmtV);
                layout[axKey] = {
                    domain: [bottom, top], range: range, autorange: false, fixedrange: false,
                    zeroline: false, showgrid: false, automargin: false,
                    tickmode: "array", tickvals: ticks.tickvals, ticktext: ticks.ticktext,
                    tickfont: { size: TEXT_PX }
                };
                // the lane title, left aligned at the chart's left edge, one fact per line
                var tl = titleLines(lane);
                var mid = spectLane ? 0.75 : 0.62;
                var idCol = laneCol(lane);
                // the lane name leads: a chip in the lane's identity colour (text in
                // the colour on a see-through tint of it); spectators keep their one
                // quiet colour with a fainter tint so the gate lanes stand out
                fixed.push({
                    xref: "paper", yref: ax + " domain", x: 0, y: mid, xanchor: "left", yanchor: "bottom",
                    xshift: -margin.l + BAR_PX + 6, yshift: 2, align: "left", showarrow: false,
                    text: "<b>" + tl[0] + "</b>",
                    font: { size: LABEL_PX, color: idCol },
                    bgcolor: faint(idCol, spectLane ? 0.10 : CHIP_ALPHA), borderpad: 3, borderwidth: 0,
                    meta: { role: "lane-title", lane: lane.key, lines: tl, color: idCol }
                });
                // a thin bar in the lane colour along the lane's full height, at the gutter's left edge
                shapes.push({ type: "rect", name: "lane-bar", xref: "paper", xsizemode: "pixel", xanchor: 0,
                              x0: -margin.l, x1: -margin.l + BAR_PX, yref: ax + " domain", y0: 0, y1: 1,
                              fillcolor: idCol, line: { width: 0 }, layer: "above" });
                fixed.push({
                    xref: "paper", yref: ax + " domain", x: 0, y: mid, xanchor: "left", yanchor: "top",
                    xshift: -margin.l + BAR_PX + 6, yshift: -2, align: "left", showarrow: false, text: tl.slice(1).join("<br>"),
                    font: { size: TEXT_PX, color: c.muted },
                    meta: { role: "lane-facts", lane: lane.key }
                });
                // a late first pulse: say where the lane starts
                var first = plan[i].segs[0];
                // (only when the idle stretch is wide enough to hold the words)
                var startText = first ? "starts at " + fmt(first.start_ns) + " ns" : "";
                if (first && num(first.start_ns) && first.start_ns > 0 &&
                    (first.start_ns - span[0]) * widthPx / (span[1] - span[0]) > startText.length * CHAR_PX + 12) {
                    fixed.push({
                        xref: "paper", yref: ax + " domain", x: 0, y: 0.5, xanchor: "left", yanchor: "middle",
                        xshift: 4, showarrow: false, text: "starts at " + fmt(first.start_ns) + " ns",
                        font: { size: TEXT_PX, color: c.muted },
                        meta: { role: "starts-at", lane: lane.key }
                    });
                }
                // the 0 V line and the lane's limits when they fall inside what is shown
                shapes.push({ type: "line", name: "zero", xref: "paper", x0: 0, x1: 1, yref: ax, y0: 0, y1: 0,
                              line: { color: c.grid, width: 1 }, layer: "below" });
                if (lane.limit_v) {
                    lane.limit_v.forEach(function (lim) {
                        if (!num(lim) || lim < range[0] || lim > range[1]) return;
                        shapes.push({ type: "line", name: "limit", xref: "paper", x0: 0, x1: 1, yref: ax, y0: lim, y1: lim,
                                      line: { color: c.muted, width: 1, dash: "dot" }, opacity: 0.7, layer: "below" });
                    });
                }
                plan[i].segs.forEach(function (s) {
                    var flagged = (s.flags || []).length > 0;
                    var col = flagged ? c.flag : idCol;   // state beats identity
                    // the pulse body: filled to 0 V at low opacity, the line on top
                    traces.push({
                        x: (s.t_ns || []).slice(), y: (s.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: col, width: 0, shape: "hv" }, fill: "tozeroy", fillcolor: faint(col),
                        meta: { role: "fill", lane: lane.key, operation: s.operation, flagged: flagged },
                        hoverinfo: "skip"
                    });
                    traces.push({
                        x: (s.t_ns || []).slice(), y: (s.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: col, width: 2, shape: "hv" },
                        name: lane.key + " " + (s.operation || ""),
                        meta: { role: "segment", lane: lane.key, operation: s.operation, flagged: flagged },
                        hoverinfo: "skip"
                    });
                    // truncated: a faint band over the part that was not received, no line
                    var ts = s.t_ns || [];
                    if (s.truncated && ts.length && ts[ts.length - 1] < s.end_ns) {
                        shapes.push({ type: "rect", name: "not-received", xref: "x", x0: ts[ts.length - 1], x1: s.end_ns,
                                      yref: ax + " domain", y0: 0, y1: 1, fillcolor: c.grid, opacity: 0.35,
                                      line: { width: 0 }, layer: "below" });
                        fixed.push({
                            xref: "x", yref: ax + " domain", x: (ts[ts.length - 1] + s.end_ns) / 2, y: 0.5,
                            showarrow: false, text: "not received", font: { size: TEXT_PX, color: c.muted },
                            meta: { role: "not-received", lane: lane.key, operation: s.operation }
                        });
                    }
                });
                if (lane.sim && state.showSim) {
                    traces.push({
                        x: (lane.sim.t_ns || []).slice(), y: (lane.sim.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: c.sim, width: 1.5, dash: "dot" },
                        meta: { role: "sim", lane: lane.key }, hoverinfo: "skip"
                    });
                }
                // one invisible hover trace per lane: the unified readout is valuesAt's text
                traces.push({
                    x: times.slice(), y: times.map(function () { return 0; }),
                    text: times.map(function (t) { return readout(laneValueAt(lane, t)); }),
                    xaxis: "x", yaxis: ax, type: "scatter", mode: "markers",
                    marker: { opacity: 0, size: 1, color: idCol },
                    line: { color: idCol },
                    name: lane.label || lane.key,
                    meta: { role: "hover", lane: lane.key },
                    hovertemplate: "%{text}<extra>" + (lane.label || lane.key) + "</extra>"
                });
            });

            /* The segment texts for a visible time range and plot width: two lines
               at the segment start (or the left edge when the start is scrolled
               off), alternating rows when a lane has several segments, and a name
               or line 2 shortened so it ends before the next text in its row. */
            function labels(xr, w) {
                var pxPerNs = w / Math.max(1e-9, xr[1] - xr[0]);
                var out = [];
                drawn.forEach(function (lane, i) {
                    var ax = i === 0 ? "y" : "y" + (i + 1);
                    var segs = plan[i].segs.filter(function (s) { return s.end_ns > xr[0] && s.start_ns < xr[1]; });
                    segs.forEach(function (s, k) {
                        var flagged = (s.flags || []).length > 0;
                        var row = plan[i].bottom && k % 2 === 1 ? "bottom" : "top";
                        var x0 = Math.max(s.start_ns, xr[0]);
                        var next = segs[k + (plan[i].bottom ? 2 : 1)];
                        var stop = next ? Math.max(next.start_ns, xr[0]) : xr[1];
                        var room = Math.max(4, Math.floor((stop - x0) * pxPerNs / CHAR_PX) - 1);
                        var extra = (s.flags || []).map(function (f) { return FLAG_TEXT[f] || f; });
                        if (s.truncated) extra.push(TRUNCATED_TEXT);
                        var line2 = levelText(s) + (extra.length ? ", " + extra.join(", ") : "");
                        var name = shorten(s.operation || "", room);
                        var l2 = line2.length > room ? line2.slice(0, Math.max(3, room - 1)) + "\u2026" : line2;
                        out.push({
                            xref: "x", yref: ax + " domain", x: x0, xanchor: "left", align: "left",
                            y: row === "top" ? 1 : 0, yanchor: row === "top" ? "top" : "bottom",
                            showarrow: false, text: name + "<br>" + l2,
                            font: { size: TEXT_PX, color: flagged ? c.flag : c.text },
                            meta: { role: "segment-text", lane: lane.key, operation: s.operation, row: row,
                                    name: name, line2: line2, shown2: l2 }
                        });
                    });
                });
                return out;
            }
            layout.annotations = fixed.concat(labels(span, widthPx));
            if (window.PlotTheme && window.PlotTheme.houseLayout) layout = window.PlotTheme.houseLayout(layout);
            return { data: traces, layout: layout, drawn: drawn, fixedAnnotations: fixed, labels: labels,
                     span: span, widthPx: widthPx };
        }

        var lastFigure = null;
        function draw() {
            if (destroyed) return;
            scaleBtns.forEach(function (b) {
                var onNow = b.getAttribute("data-scale") === state.scale;
                b.classList.toggle("ui-segmented-on", onNow);
                b.setAttribute("aria-pressed", onNow ? "true" : "false");
            });
            if (simBox) simBox.checked = state.showSim;
            if (foldBox) foldBox.checked = !state.fold;
            lastFigure = figure();
            if (window._plotlyRender) {
                var p = window._plotlyRender(plot, lastFigure.data, lastFigure.layout,
                    { displayModeBar: false, responsive: true });
                if (p && p.then) p.then(bindRelayout);
            }
        }

        /* Zoom, pan and resize: re-fit the segment texts to the real plot width
           and the visible time range, so names and rows never collide. */
        var relayoutBound = false;
        function onRelayout(ev) {
            if (destroyed || !lastFigure || !ev) return;
            var keys = Object.keys(ev);
            if (!keys.some(function (k) { return /^xaxis|^width$|^autosize$/.test(k); })) return;
            var fl = plot._fullLayout;
            var xr = fl && fl.xaxis && fl.xaxis.range ? fl.xaxis.range.slice() : lastFigure.span;
            var w = fl && fl.xaxis && fl.xaxis._length ? fl.xaxis._length : lastFigure.widthPx;
            var anns = lastFigure.fixedAnnotations.concat(lastFigure.labels(xr, w));
            lastFigure.layout.annotations = anns;
            if (window.Plotly && window.Plotly.relayout) window.Plotly.relayout(plot, { annotations: anns });
        }
        function bindRelayout() {
            if (relayoutBound || destroyed || typeof plot.on !== "function") return;
            plot.on("plotly_relayout", onRelayout);
            relayoutBound = true;
        }

        var handle = {
            destroy: function () {
                if (destroyed) return;
                destroyed = true;
                listeners.forEach(function (x) { x[0].removeEventListener(x[1], x[2]); });
                listeners = [];
                if (relayoutBound && typeof plot.removeListener === "function") {
                    plot.removeListener("plotly_relayout", onRelayout);
                }
                relayoutBound = false;
                if (window.PlotHost) {
                    try { window.PlotHost.unobserve(root); } catch (e) { /* not observed */ }
                    try { window.PlotHost.purgeWithin(root); } catch (e) { /* no plot */ }
                }
                if (root.parentNode) root.parentNode.removeChild(root);
                if (bar.parentNode) bar.parentNode.removeChild(bar);
            },
            setScale: function (m) { state.scale = m === "common" ? "common" : "lane"; draw(); },
            setFold: function (b) { state.fold = !!b; draw(); },
            setShowSim: function (b) { state.showSim = !!b; draw(); },
            valuesAt: function (t) { return lanes.map(function (l) { return laneValueAt(l, t); }); },
            readout: function (t) {
                return lanes.map(function (l) { return { key: l.key, text: readout(laneValueAt(l, t)) }; });
            },
            laneCount: function () {
                return { gate: gate.length, spectator: spect.length, drawn: drawnLanes().length };
            },
            figure: function () { return lastFigure; },
            relayout: onRelayout
        };
        draw();
        if (window.PlotHost) { try { window.PlotHost.observe(root); } catch (e) { /* no observer */ } }
        return handle;
    }

    window.GateTimeline = { render: render, valueAt: laneValueAt, readout: readout, laneColor: laneColor };
})();
