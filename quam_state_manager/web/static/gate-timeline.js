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
 * while a lane's own y zoom stays its own. Rendering goes through
 * window._plotlyRender, sizing through window.PlotHost, colours through
 * window.PlotTheme and CSS variables read at run time (no colour literals).
 */
(function () {
    "use strict";

    var LANE_PX = 96;          // fixed height per lane: 16 lanes scroll in the host, labels never overlap
    var GAP_PX = 14;
    var MARGIN = { l: 150, r: 16, t: 8, b: 40 };
    var HEAD_ROOM = 0.45;      // share of a lane's span kept above the data for the segment text

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

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    function num(x) { return typeof x === "number" && isFinite(x); }
    function fmtV(v) { return num(v) ? (+v.toPrecision(4)) + " V" : "?"; }

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

    function laneTitle(lane) {
        var lines = [lane.label || lane.key];
        if (lane.port) lines.push(lane.port);
        if (num(lane.offset_v) && lane.offset_v !== 0) lines.push("offset " + fmtV(lane.offset_v));
        if (!lane.limit_v) lines.push("range not known");
        return lines.join("<br>");
    }

    function laneExtent(lane, showSim) {
        var lo = 0, hi = 0;
        function take(v) { if (num(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
        (lane.segments || []).forEach(function (s) { (s.v || []).forEach(take); });
        if (lane.limit_v) { take(lane.limit_v[0]); take(lane.limit_v[1]); }
        if (showSim && lane.sim) (lane.sim.v || []).forEach(take);
        return [lo, hi];
    }
    function padded(ext) {
        var span = ext[1] - ext[0];
        if (!(span > 0)) span = 1;
        return [ext[0] - 0.08 * span, ext[1] + HEAD_ROOM * span];
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

        // Toolbar: vertical scale (kit segmented control) and the simulation switch.
        var bar = el("div", "ui-actions ui-actions--start");
        var seg = el("div", "ui-segmented");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "Vertical scale");
        var scaleBtns = [["lane", "Per lane"], ["common", "Common V scale"]].map(function (o) {
            var b = el("button", "ui-segmented-opt", o[1]);
            b.type = "button";
            b.setAttribute("data-scale", o[0]);
            on(b, "click", function () { handle.setScale(o[0]); });
            seg.appendChild(b);
            return b;
        });
        bar.appendChild(seg);
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
        root.appendChild(bar);

        var plot = el("div");
        plot.setAttribute("data-gate-timeline-plot", "");
        root.appendChild(plot);

        // Spectator group: folded it is one summary row with the unfold control.
        var foldRow = null, foldText = null, foldBtn = null;
        if (spect.length) {
            foldRow = el("div", "ui-actions ui-actions--start");
            foldRow.setAttribute("data-spectator-summary", "");
            foldText = el("span", "ui-note");
            foldBtn = el("button", "btn-sm secondary");
            foldBtn.type = "button";
            foldBtn.setAttribute("data-fold", "");
            on(foldBtn, "click", function () { handle.setFold(!state.fold); });
            foldRow.appendChild(foldText);
            foldRow.appendChild(foldBtn);
            root.appendChild(foldRow);
        }
        var notesBox = el("div");
        notesInto(notesBox);
        root.appendChild(notesBox);

        function drawnLanes() { return state.fold ? gate.slice() : gate.concat(spect); }

        function allTimes(drawn) {
            var seen = {}, out = [];
            drawn.forEach(function (l) {
                (l.segments || []).forEach(function (s) {
                    (s.t_ns || []).concat([s.start_ns, s.end_ns]).forEach(function (t) {
                        if (num(t) && !seen[t]) { seen[t] = 1; out.push(t); }
                    });
                });
            });
            return out.sort(function (a, b) { return a - b; });
        }

        function figure() {
            var c = theme();
            var drawn = drawnLanes();
            var n = drawn.length;
            var plotPx = n * LANE_PX + Math.max(0, n - 1) * GAP_PX;
            var traces = [], shapes = [], annotations = [];
            var layout = {
                margin: MARGIN,
                height: plotPx + MARGIN.t + MARGIN.b,
                showlegend: false,
                hovermode: "x unified",
                hoversubplots: "axis",
                font: { size: 11, color: c.text },
                xaxis: { title: { text: "time (ns)" }, zeroline: false, anchor: "y" + (n > 1 ? n : "") },
                shapes: shapes,
                annotations: annotations
            };
            var common = null;
            if (state.scale === "common") {
                var lo = 0, hi = 0;
                drawn.forEach(function (l) {
                    var e = laneExtent(l, state.showSim);
                    lo = Math.min(lo, e[0]); hi = Math.max(hi, e[1]);
                });
                common = padded([lo, hi]);
            }
            var times = allTimes(drawn);
            drawn.forEach(function (lane, i) {
                var ax = i === 0 ? "y" : "y" + (i + 1);
                var axKey = i === 0 ? "yaxis" : "yaxis" + (i + 1);
                var top = 1 - (i * (LANE_PX + GAP_PX)) / plotPx;
                var bottom = Math.max(0, top - LANE_PX / plotPx);
                layout[axKey] = {
                    domain: [bottom, top],
                    range: common ? common.slice() : padded(laneExtent(lane, state.showSim)),
                    autorange: false,
                    title: { text: laneTitle(lane), standoff: 6 },
                    zeroline: false, gridcolor: c.grid, automargin: true,
                    ticksuffix: " V", nticks: 4
                };
                // the 0 V line and, when known, the lane's limits: faint
                shapes.push({ type: "line", name: "zero", xref: "paper", x0: 0, x1: 1, yref: ax, y0: 0, y1: 0,
                              line: { color: c.grid, width: 1 }, layer: "below" });
                if (lane.limit_v) {
                    lane.limit_v.forEach(function (y) {
                        if (!num(y)) return;
                        shapes.push({ type: "line", name: "limit", xref: "paper", x0: 0, x1: 1, yref: ax, y0: y, y1: y,
                                      line: { color: c.muted, width: 1, dash: "dot" }, opacity: 0.6, layer: "below" });
                    });
                }
                (lane.segments || []).forEach(function (s) {
                    traces.push({
                        x: (s.t_ns || []).slice(), y: (s.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: c.trace, width: 2, shape: "hv" },
                        name: lane.key + " " + (s.operation || ""),
                        meta: { role: "segment", lane: lane.key, operation: s.operation },
                        hoverinfo: "skip"
                    });
                    var bits = [s.operation || "", "peak " + fmtV(s.peak_v), (num(s.length_ns) ? s.length_ns : "?") + " ns"];
                    var extra = (s.flags || []).map(function (f) { return FLAG_TEXT[f] || f; });
                    if (s.truncated) extra.push(TRUNCATED_TEXT);
                    annotations.push({
                        xref: "x", yref: ax + " domain", x: (s.start_ns + s.end_ns) / 2, y: 1,
                        yanchor: "top", showarrow: false,
                        text: bits.join(" 00b7 ") + (extra.length ? "<br>" + extra.join("; ") : ""),
                        font: { size: 10, color: extra.length ? c.flag : c.text },
                        meta: { role: "segment-text", lane: lane.key, operation: s.operation }
                    });
                    if ((s.flags || []).length) {
                        traces.push({
                            x: [(s.start_ns + s.end_ns) / 2], y: [num(s.peak_v) ? s.peak_v : 0],
                            xaxis: "x", yaxis: ax, type: "scatter", mode: "markers",
                            marker: { color: c.flag, size: 10, symbol: "x" },
                            meta: { role: "flag", lane: lane.key, operation: s.operation, flags: s.flags.slice() },
                            hoverinfo: "skip"
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
                    marker: { opacity: 0, size: 1, color: c.trace },
                    name: lane.label || lane.key,
                    meta: { role: "hover", lane: lane.key },
                    hovertemplate: "%{text}<extra>" + (lane.label || lane.key) + "</extra>"
                });
            });
            if (window.PlotTheme && window.PlotTheme.houseLayout) layout = window.PlotTheme.houseLayout(layout);
            return { data: traces, layout: layout, drawn: drawn };
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
            if (foldRow) {
                foldText.textContent = spect.length + " spectator line" + (spect.length === 1 ? "" : "s");
                foldBtn.textContent = state.fold ? "Show" : "Hide";
                foldBtn.setAttribute("aria-expanded", state.fold ? "false" : "true");
            }
            lastFigure = figure();
            if (window._plotlyRender) {
                window._plotlyRender(plot, lastFigure.data, lastFigure.layout,
                    { displayModeBar: false, responsive: true });
            }
        }

        var handle = {
            destroy: function () {
                if (destroyed) return;
                destroyed = true;
                listeners.forEach(function (x) { x[0].removeEventListener(x[1], x[2]); });
                listeners = [];
                if (window.PlotHost) {
                    try { window.PlotHost.unobserve(root); } catch (e) { /* not observed */ }
                    try { window.PlotHost.purgeWithin(root); } catch (e) { /* no plot */ }
                }
                if (root.parentNode) root.parentNode.removeChild(root);
            },
            setScale: function (m) { state.scale = m === "common" ? "common" : "lane"; draw(); },
            setFold: function (b) { state.fold = !!b; draw(); },
            setShowSim: function (b) { state.showSim = !!b; draw(); },
            valuesAt: function (t) { return lanes.map(function (l) { return laneValueAt(l, t); }); },
            laneCount: function () {
                return { gate: gate.length, spectator: spect.length, drawn: drawnLanes().length };
            },
            figure: function () { return lastFigure; }
        };
        draw();
        if (window.PlotHost) { try { window.PlotHost.observe(root); } catch (e) { /* no observer */ } }
        return handle;
    }

    window.GateTimeline = { render: render, valueAt: laneValueAt };
})();
