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
    var SPECT_KEY = "\u0001spectators";  // the legend key of the spectator group (a lane key equal to it is refused)
    var GUIDE_ROWS_PX = 32;    // two rows of edge-time labels above the overlay plot
    var BAR_PX = 3;            // the lane colour bar at the gutter's left edge
    var STACK_PLOT_PX = 200;   // a plot narrower than this beside the titles: titles go above their lanes
    var TITLE_LINE_PX = 22;    // a lane name chip's line when titles sit above the lane
    var FACT_LINE_PX = 15;     // one fact line under it
    var TICK_SPACE_PX = 70;    // stacked: at most one time tick per this many pixels
    var CHIP_ALPHA = 0.16;     // tint behind the lane name chip

    // bad_length: "short" is under the chip's floor (16 ns before QOP 3.3, 4 ns from QOP 3.3 on;
    // gate_timeline_view.length_flag); the gate check's length line names the number.
    var FLAG_TEXT = {
        over_range: "over the port range",
        bad_length: "length is not a multiple of 4 ns or is short"
    };
    /* Data contract, flag severity: a flag in REFUSING_FLAGS refuses the gate
       and draws in the kit danger tone; every other flag (unknown ones too) and a
       truncated segment only inform and draw in the warn tone. State never takes
       a palette colour; the lane chip keeps its identity colour. */
    var REFUSING_FLAGS = ["over_range", "bad_length"];
    /* Optional behaviour (data contract unchanged):
       - limit_v may be one number r (read as [-r, r]); a reversed pair is ordered;
         anything else counts as unknown. limit_v is the port range with the
         channel offset already taken off; the title says so when offset_v is set.
       - Two lanes with the same key cannot be drawn (every toggle and colour is
         keyed by it); the picture says so instead.
       - A segment with fewer than two samples is drawn as a marker.
       - Times closer than TIME_EPS ns are the same time. */
    var TIME_EPS = 1e-6;
    var MAX_BRACKETS = 12;     // brackets drawn in the overlay; the list holds every pair
    var MAX_SPOKEN = 6;        // lines and pairs named in the plot's text alternative
    var TRUNCATED_TEXT = "only part of this waveform was received";

    function severity(s) {
        var fl = s.flags || [];
        if (fl.some(function (f) { return REFUSING_FLAGS.indexOf(f) >= 0; })) return "danger";
        if (fl.length || s.truncated) return "warn";
        return null;
    }

    /* A kit colour token resolved by the browser (tokens may be var() or
       color-mix() expressions Plotly cannot parse); "" when not defined. */
    var RGB = /^rgba?\(/;
    function kitColor(name) {
        var own = cssVar(name);
        if (!own) return "";                       // not defined: the caller falls back
        try {
            var probe = document.createElement("span");
            probe.hidden = true;
            probe.style.setProperty("color", "var(" + name + ")");
            (document.body || document.documentElement).appendChild(probe);
            var col = getComputedStyle(probe).color;
            probe.remove();
            if (col && RGB.test(col)) return col;
        } catch (e) { /* no DOM */ }
        return RGB.test(own) ? own : "";           // never a var() or color-mix() string
    }
    /* The state tones: danger (refuses) and warn (informs), line and fill. */
    function stateTones() {
        var pt = window.PlotTheme || {};
        var pal = (pt.palette && pt.palette()) || [];
        var dLine = kitColor("--ui-danger-text") || (pt.lossColor && pt.lossColor()) || pal[6] || "";
        var wLine = kitColor("--ui-warn-text") || pal[4] || dLine;
        return {
            danger: { line: dLine, fill: kitColor("--ui-danger-bg") || faint(dLine) },
            warn: { line: wLine, fill: kitColor("--ui-warn-bg") || faint(wLine) }
        };
    }

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
            muted: cssVar("--pico-muted-color") || text,
            tones: stateTones()
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

    /* Plotly renders its text as a small HTML subset: names are escaped so a
       "<b>", "&" or "%{" in a lane or operation name shows literally. */
    function esc(t) {
        return String(t === undefined || t === null ? "" : t)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function isIndex(x) { return typeof x === "number" && isFinite(x) && Math.floor(x) === x && x >= 0; }

    /* Identity colours: the kit's line colours --ui-line-1..6; without them the
       plot palette in the order 1, 4, 6, 3, 5, 2 (blue, magenta, teal first, the
       status-like hues last). Index modulo the list length. */
    var PALETTE_ORDER = [0, 3, 5, 2, 4, 1];
    function identityColors() {
        // slot by slot: the kit colour when defined and resolvable, else the
        // palette colour that slot stands for (the kit uses the same order)
        var pt = window.PlotTheme || {};
        var pal = (pt.palette && pt.palette()) || [];
        var out = [];
        for (var n = 1; n <= 6; n++) {
            var c = kitColor("--ui-line-" + n) || pal[PALETTE_ORDER[n - 1]];
            if (c) out.push(c);
        }
        return out;
    }
    function laneColor(index) {
        var list = identityColors();
        if (!list.length) return theme().text;
        var i = isIndex(index) ? index : 0;
        return list[i % list.length];
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
    /* TIME (ns): exact to 0.001 ns, integers whole, trailing zeros trimmed, never
       scientific notation. */
    function fmt(t) {
        if (!num(t)) return "?";
        var r = Math.round(t * 1000) / 1000;
        var out = r.toFixed(3).replace(/\.?0+$/, "");
        return out === "-0" ? "0" : out;
    }
    /* VOLT: 4 significant digits, trailing zeros trimmed; within one picture two
       values more than VOLT_EPS apart never print the same (voltDigits grows). */
    var VOLT_EPS = 1e-6;
    var voltDigits = 4;
    function voltNum(v, digits) {
        if (!num(v)) return "?";
        var d = digits || voltDigits;
        if (Math.abs(v) < 1e-12) return "0";
        var mag = Math.floor(Math.log(Math.abs(v)) / Math.LN10);
        var dec = Math.max(0, Math.min(15, d - 1 - mag));
        var out = (Math.round(v * Math.pow(10, dec)) / Math.pow(10, dec)).toFixed(dec);
        if (out.indexOf(".") >= 0) out = out.replace(/\.?0+$/, "");
        return out === "-0" ? "0" : out;
    }
    function pickVoltDigits(values) {
        var vs = values.filter(num).sort(function (a, b) { return a - b; });
        for (var d = 4; d < 12; d++) {
            var clash = false;
            for (var i = 1; i < vs.length && !clash; i++) {
                if (vs[i] - vs[i - 1] > VOLT_EPS && voltNum(vs[i], d) === voltNum(vs[i - 1], d)) clash = true;
            }
            if (!clash) return d;
        }
        return 12;
    }
    function fmtV(v) { return num(v) ? voltNum(v) + " V" : "?"; }

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
        var out = { key: lane.key, operation: seg.operation || null, v: v };
        if (seg.truncated && ts.length && t > ts[ts.length - 1]) out.v = null;
        if (out.v === null) out.missing = (seg.truncated && ts.length && t > ts[ts.length - 1]) ? "not received" : "no value";
        return out;
    }

    function readout(val) {
        if (!val.operation) return "idle";
        if (val.v === null) return val.operation + ": " + (val.missing || "not received");
        return val.operation + ": " + fmtV(val.v);
    }

    function titleLines(lane) {
        var lines = [lane.label || lane.key];
        if (lane.port) lines.push(lane.port);
        var off = num(lane.offset_v) && lane.offset_v !== 0;
        if (lane.limit_v) {
            lines.push("limit " + voltNum(lane.limit_v[0]) + " to " + voltNum(lane.limit_v[1]) + " V");
            // limit_v already has the offset taken off: say so beside it
            if (off) lines.push("(port range less the " + fmtV(lane.offset_v) + " offset)");
        } else {
            lines.push("range not known");
            if (off) lines.push("offset " + fmtV(lane.offset_v));
        }
        return lines;
    }

    /* Line 2 of a segment's text, from the received samples only. */
    function levelText(s) {
        var ts = s.t_ns || [], vs = (s.v || []).filter(num);
        if (ts.length === 1) {
            return "1 sample at " + fmt(ts[0]) + " ns" + (num((s.v || [])[0]) ? ", " + fmtV(s.v[0]) : "");
        }
        // the drawn width; a different configured length is said, never hidden
        var drawn = num(s.start_ns) && num(s.end_ns) ? s.end_ns - s.start_ns : null;
        var len = drawn === null ? "" : fmt(drawn) + " ns";
        if (drawn !== null && num(s.length_ns) && Math.abs(s.length_ns - drawn) > TIME_EPS) {
            len = "config length " + fmt(s.length_ns) + " ns, drawn " + fmt(drawn) + " ns";
        }
        var tail = len ? ", " + len : "";
        if (!s.truncated && (s.v || []).length < ts.length && ts.length) {
            var lastIdx = Math.max(0, (s.v || []).length - 1);
            tail += ", samples end at " + fmt(ts[lastIdx]) + " ns";
        }
        var vv = s.v || [];
        if (vv.some(function (x, k) { return k < ts.length && !num(x); })) tail += ", no value in part";
        if (!vs.length) return "no samples" + tail;
        var mx = Math.max.apply(null, vs), mn = Math.min.apply(null, vs);
        if (mx === mn) return "level " + fmtV(mx) + tail;
        return "max " + fmtV(mx) + (mn < 0 ? ", min " + fmtV(mn) : "") + tail;
    }
    function shorten(name, maxChars) {
        if (name.length <= maxChars) return name;
        if (maxChars < 5) return name.slice(0, Math.max(1, maxChars - 1)) + "\u2026";
        var keep = maxChars - 1, head = Math.ceil(keep / 2);
        return name.slice(0, head) + "\u2026" + name.slice(name.length - (keep - head));
    }

    /* Segment texts that share a lane row must not overlap (r3 P3-3). Boxes are
       swept left to right; on a clash the text whose free side faces the other is
       cut with an ellipsis (a left-anchored earlier text loses its right end, a
       right-anchored later text its left end), and a text with too little room is
       dropped. A cut text keeps its full name and numbers in the hover text. */
    var MIN_CUT = 4;
    function cutEnd(text, maxChars) {
        return text.length <= maxChars ? text : text.slice(0, Math.max(1, maxChars - 1)) + "\u2026";
    }
    function textBox(a, xr, w) {
        var px = (a.x - xr[0]) * w / (xr[1] - xr[0]);
        var width = Math.max(a.meta.name.length, a.meta.shown2.length) * CHAR_PX;
        var left = a.xanchor === "right" ? px - width : px;
        return { left: left, right: left + width };
    }
    function cutTo(a, chars) {
        if (chars < MIN_CUT) return false;
        var name = shorten(a.meta.operation || "", chars), l2 = cutEnd(a.meta.line2, chars);
        a.meta.name = name;
        a.meta.shown2 = l2;
        a.text = esc(name) + "<br>" + esc(l2);
        a.hovertext = esc(a.meta.operation || "") + "<br>" + esc(a.meta.line2);
        return true;
    }
    function declutter(texts, xr, w) {
        var gap = CHAR_PX * 3;   // two blank characters at least: neighbours never read as one run
        var rows = {};
        texts.forEach(function (a) {
            var key = a.meta.lane + "|" + a.meta.row;
            (rows[key] || (rows[key] = [])).push(a);
        });
        var dropped = [];
        Object.keys(rows).forEach(function (key) {
            var row = rows[key].slice().sort(function (a, b) {
                return textBox(a, xr, w).left - textBox(b, xr, w).left;
            });
            var prev = null;
            row.forEach(function (cur) {
                if (prev) {
                    var p = textBox(prev, xr, w), q = textBox(cur, xr, w);
                    if (p.right + gap > q.left) {
                        var ok;
                        if (cur.xanchor === "right") {
                            ok = cutTo(cur, Math.floor((q.right - p.right - gap) / CHAR_PX));
                        } else if (prev.xanchor !== "right") {
                            ok = cutTo(prev, Math.floor((q.left - p.left - gap) / CHAR_PX));
                            if (!ok) { dropped.push(prev); prev = cur; return; }
                            ok = true;
                        } else {
                            ok = false;
                        }
                        if (!ok) { dropped.push(cur); return; }
                    }
                }
                prev = cur;
            });
        });
        return texts.filter(function (a) { return dropped.indexOf(a) < 0; });
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

    function normLimit(x) {
        if (num(x)) return [-Math.abs(x), Math.abs(x)];
        if (Array.isArray(x) && x.length === 2 && num(x[0]) && num(x[1])) return [Math.min(x[0], x[1]), Math.max(x[0], x[1])];
        return null;
    }
    /* A handle with every method, for a picture with no chart: the setters do
       nothing, timing() and valuesAt() are empty. */
    function emptyHandle(root, counts) {
        var noop = function () {};
        return {
            destroy: function () { if (root.parentNode) root.parentNode.removeChild(root); },
            setScale: noop, setFold: noop, setShowSim: noop, setView: noop, setHeight: noop, setLineHidden: noop,
            timing: function () { return []; },
            valuesAt: function () { return []; },
            readout: function () { return []; },
            laneCount: function () { return { gate: counts.gate, spectator: counts.spectator, drawn: 0 }; },
            figure: function () { return null; },
            relayout: noop
        };
    }
    function cannotDraw(host, statement, notes, counts) {
        var root = el("div");
        root.setAttribute("data-gate-timeline", "");
        root.appendChild(el("p", "ui-state ui-state--empty", statement));
        (notes || []).forEach(function (n) { root.appendChild(el("p", "ui-note", n)); });
        host.appendChild(root);
        return emptyHandle(root, counts);
    }

    function render(host, data, opts) {
        // one picture per host: a second render replaces the first
        if (host && host.__gateTimeline) { try { host.__gateTimeline.destroy(); } catch (e) { /* gone */ } }
        var h = renderOnce(host, data, opts);
        if (host) host.__gateTimeline = h;
        return h;
    }

    function renderOnce(host, data, opts) {
        data = data || {};
        var src = data.lanes || [];
        var counts = { gate: src.filter(function (l) { return l && l.group !== "spectator"; }).length,
                       spectator: src.filter(function (l) { return l && l.group === "spectator"; }).length };
        var seen = {}, dup = null;
        src.forEach(function (l) { if (l && seen[l.key] && dup === null) dup = l.key; if (l) seen[l.key] = true; });
        if (seen[SPECT_KEY] && !data.cannot_draw) {
            return cannotDraw(host, "cannot draw: a line key is reserved", data.notes, counts);
        }
        if (dup !== null && !data.cannot_draw) {
            return cannotDraw(host, "cannot draw: two lines share the key " + dup, data.notes, counts);
        }
        var clean = Object.assign({}, data, {
            lanes: src.map(function (l) { return Object.assign({}, l, { limit_v: normLimit(l.limit_v) }); })
        });
        var before = Array.prototype.slice.call(host.childNodes);
        var tb = opts && opts.toolbarHost;
        var tbBefore = tb ? Array.prototype.slice.call(tb.childNodes) : [];
        try {
            return renderChart(host, clean, opts);
        } catch (e) {
            // never leave a half-built picture: remove what this call added
            Array.prototype.slice.call(host.childNodes).forEach(function (n) { if (before.indexOf(n) < 0) host.removeChild(n); });
            if (tb) Array.prototype.slice.call(tb.childNodes).forEach(function (n) { if (tbBefore.indexOf(n) < 0) tb.removeChild(n); });
            return cannotDraw(host, "cannot draw: " + (e && e.message ? e.message : String(e)), data.notes, counts);
        }
    }

    function renderChart(host, data, opts) {
        opts = opts || {};
        var state = {
            scale: opts.scale === "common" ? "common" : "lane",
            fold: opts.foldSpectators !== false,
            showSim: opts.showSim !== false,
            view: opts.view === "overlay" ? "overlay" : "lanes",
            height: "real",
            hidden: {},
            highlight: null
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
            return emptyHandle(root, { gate: gate.length, spectator: spect.length });
        }

        // Toolbar: the view (lanes or overlay), the vertical scale of that view
        // (kit segmented controls), the spectator group switch and the simulation
        // switch. role=toolbar, not group: the base stylesheet stretches every
        // [role=group] to the full width.
        var bar = el("div", "ui-actions ui-actions--start");
        bar.setAttribute("data-gate-timeline-bar", "");
        function segmented(label, aria, attr, options, pick) {
            var lab = el("span", "ui-note", label);
            var g = el("div", "ui-segmented");
            g.setAttribute("role", "toolbar");
            g.setAttribute("aria-label", aria);
            var btns = options.map(function (o) {
                var b = el("button", "ui-segmented-opt", o[1]);
                b.type = "button";
                b.setAttribute(attr, o[0]);
                on(b, "click", function () { pick(o[0]); });
                g.appendChild(b);
                return b;
            });
            return { label: lab, group: g, buttons: btns, attr: attr };
        }
        var viewSeg = segmented("View", "View", "data-view", [["lanes", "Lanes"], ["overlay", "Overlay"]],
                                function (m) { handle.setView(m); });
        viewSeg.label.setAttribute("data-view-label", "");
        bar.appendChild(viewSeg.label);
        bar.appendChild(viewSeg.group);
        var scaleSeg = segmented("V scale", "Vertical scale", "data-scale", [["lane", "Per lane"], ["common", "Shared"]],
                                 function (m) { handle.setScale(m); });
        scaleSeg.label.setAttribute("data-scale-label", "");
        var seg = scaleSeg.group, scaleBtns = scaleSeg.buttons, scaleLab = scaleSeg.label;
        bar.appendChild(scaleLab);
        bar.appendChild(seg);
        var heightSeg = segmented("Height", "Line height", "data-height", [["real", "Real V"], ["same", "Same height"]],
                                  function (m) { handle.setHeight(m); });
        heightSeg.label.setAttribute("data-height-label", "");
        bar.appendChild(heightSeg.label);
        bar.appendChild(heightSeg.group);
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

        /* Overlay legend: ONE row of kit buttons, one per gate line (and one for the
           spectator group): the line's colour dot, its label and its summary;
           pressed = shown. Nothing interactive lives inside the chart. */
        function lineClass(l) { return "ui-line-" + ((colorIndex[l.key] || 0) % 6 + 1); }
        var legend = el("div", "ui-actions ui-actions--start");
        legend.setAttribute("data-gate-timeline-legend", "");
        var legendBtns = [];
        gate.concat(spect.length ? [{ key: SPECT_KEY, label: spect.length + (spect.length === 1 ? " spectator" : " spectators"), spectGroup: true }] : [])
            .forEach(function (l) {
                var b = el("button", "btn-sm secondary ui-legend-btn");
                b.type = "button";
                b.setAttribute("data-legend", l.key);
                var dot = null;
                if (!l.spectGroup) {
                    dot = el("span", "ui-line-dot " + lineClass(l));
                    dot.setAttribute("aria-hidden", "true");
                    b.appendChild(dot);
                }
                b.appendChild(document.createTextNode(l.label || l.key));
                var note = el("span", "ui-note", l.spectGroup ? "" : lineSummary(l));
                b.appendChild(note);
                on(b, "click", function () { handle.setLineHidden(l.key, !state.hidden[l.key]); });
                legend.appendChild(b);
                legendBtns.push({ key: l.key, btn: b, note: note, dot: dot, spectGroup: !!l.spectGroup });
            });
        root.appendChild(legend);

        var plot = el("div");
        plot.setAttribute("data-gate-timeline-plot", "");
        plot.setAttribute("role", "img");
        root.appendChild(plot);
        if ((data.marks || []).length) {
            var markList = el("details", "ui-disclosure");
            markList.appendChild(el("summary", null, "Sequence steps"));
            data.marks.forEach(function (m) {
                var text = (m.element ? m.element + ": " : "") + m.label + ", " +
                    (m.kind === "wait" ? fmt(m.start_ns) + " to " + fmt(m.end_ns) : fmt(m.t_ns)) + " ns";
                var item = el("p", "ui-note", text);
                item.title = text + (m.statement ? ": " + m.statement : "");
                item.setAttribute("data-step-description", "");
                markList.appendChild(item);
            });
            root.appendChild(markList);
        }

        /* Every pair of visible gate lines is listed here (both views), one row per
           pair in the order timing() returns; more than six rows: the first six and a
           kit disclosure with the rest. Hovering or focusing a row highlights its
           brackets and guides in the overlay. */
        var pairList = el("div");
        pairList.setAttribute("data-gate-timeline-pairs", "");
        root.appendChild(pairList);
        function pairKey(p) { return p.a + "\u0001" + p.b; }
        // one delegated set of listeners for every row the list ever holds
        function rowOf(e) {
            var n = e.target;
            while (n && n !== pairList && !(n.getAttribute && n.getAttribute("data-pair-key"))) n = n.parentNode;
            return n && n !== pairList ? n : null;
        }
        function lightRow(e) {
            var row = rowOf(e);
            if (!row) return;
            var key = row.getAttribute("data-pair-key");
            if (state.highlight !== key) { state.highlight = key; draw(); }
        }
        function unlightRow(e) {
            var row = rowOf(e);
            if (row && state.highlight === row.getAttribute("data-pair-key")) { state.highlight = null; draw(); }
        }
        on(pairList, "mouseover", lightRow); on(pairList, "focusin", lightRow);
        on(pairList, "mouseout", unlightRow); on(pairList, "focusout", unlightRow);
        function buildPairList(pairs) {
            pairList.textContent = "";
            if (!pairs || !pairs.length) return;
            var more = null;
            if (pairs.length > 6) {
                more = el("details", "ui-disclosure");
                more.appendChild(el("summary", "ui-disclosure-summary", "all " + pairs.length + " pairs"));
            }
            state.highlight = null;   // a rebuilt list starts with nothing highlighted
            pairs.forEach(function (p, k) {
                var row = el("div", "ui-pair-row");
                row.setAttribute("data-pair", String(k));
                row.setAttribute("data-pair-key", pairKey(p));
                row.setAttribute("tabindex", "0");
                [p.a, p.b].forEach(function (key, n) {
                    var l = lanes.filter(function (x) { return x.key === key; })[0];
                    var dot = el("span", "ui-line-dot " + lineClass(l || { key: key }));
                    dot.setAttribute("aria-hidden", "true");
                    row.appendChild(dot);
                    row.appendChild(el("code", "ui-chip", nameOf(key)));
                    if (n === 0) row.appendChild(el("span", "ui-note", "to"));
                });
                var what = p.kind === "gap" ? "gap " + fmt(p.ns) + " ns" : p.kind === "overlap" ? "overlap " + fmt(p.ns) + " ns" : "meet";
                if (p.kind === "overlap" && p.intervals.length > 1) what += " in " + p.intervals.length + " parts";
                row.appendChild(el("span", p.kind === "gap" ? "ui-badge ui-badge--warn" : "ui-badge", what));
                row.appendChild(el("span", "ui-note", fmt(p.from_ns) + " to " + fmt(p.to_ns) + " ns"));
                (more && k >= 6 ? more : pairList).appendChild(row);
            });
            if (more) pairList.appendChild(more);
        }

        var notesBox = el("div");
        notesInto(notesBox);
        root.appendChild(notesBox);

        var pictureDigits = pickVoltDigits(lanes.reduce(function (acc, l) {
            (l.segments || []).forEach(function (sg) { acc = acc.concat(sg.v || []); });
            if (l.limit_v) acc = acc.concat(l.limit_v);
            if (num(l.offset_v)) acc.push(l.offset_v);
            return acc;
        }, []));

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
            (data.marks || []).forEach(function (m) {
                [m.start_ns, m.end_ns, m.t_ns].forEach(function (t) {
                    if (num(t)) { lo = Math.min(lo, t); hi = Math.max(hi, t); }
                });
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

        // wait and align steps play nothing, so they are drawn as labelled marks
        // on both views: a wait is a shaded span, an align a dashed line.
        function stepMarks(shapes, c, drawn, overlay) {
            var notes = [];
            (data.marks || []).forEach(function (m) {
                var index = drawn.map(function (l) { return l.key; }).indexOf(m.element);
                if (m.element && index < 0) return;
                var yr = !overlay && index >= 0 ? "y" + (index ? index + 1 : "") + " domain" : "paper";
                var meta = { role: "step-mark", kind: m.kind, lane: m.element };
                if (m.kind === "wait" && num(m.start_ns) && num(m.end_ns)) {
                    shapes.push({ type: "rect", name: "step-mark", xref: "x", x0: m.start_ns, x1: m.end_ns,
                                  yref: yr, y0: 0, y1: 1, fillcolor: c.grid, opacity: 0.3,
                                  line: { width: 0 }, layer: "below", meta: meta });
                    notes.push({ xref: "x", yref: yr, x: (m.start_ns + m.end_ns) / 2, y: 0, yanchor: "bottom",
                                 showarrow: false, text: esc(m.label), font: { size: TEXT_PX, color: c.muted },
                                 hovertext: esc(m.statement || m.label), meta: meta });
                } else if (m.kind === "align" && num(m.t_ns)) {
                    shapes.push({ type: "line", name: "step-mark", xref: "x", x0: m.t_ns, x1: m.t_ns,
                                  yref: yr, y0: 0, y1: 1, line: { color: c.muted, width: 1, dash: "dashdot" },
                                  layer: "below", meta: meta });
                    notes.push({ xref: "x", yref: yr, x: m.t_ns, y: 0, yanchor: "bottom", showarrow: false,
                                 text: esc(m.label), font: { size: TEXT_PX, color: c.muted },
                                 hovertext: esc(m.statement || m.label), meta: meta });
                }
            });
            return notes;
        }
        function fitMarks(notes, xr, w, texts) {
            var used = {}, seen = {};
            // A mark sits at the bottom of its lane, inside the band of the lane's
            // bottom-row segment texts: it gives way to them (r3 P3-3).
            var bottom = (texts || []).filter(function (t) {
                return t.meta && t.meta.role === "segment-text" && t.meta.row === "bottom";
            }).map(function (t) { var b = textBox(t, xr, w); b.yref = t.yref; return b; });
            // A wait label carries a number the picture needs, so waits take their place
            // first; an align is already a dashed line, and one label serves marks that
            // share a place. Every mark stays in the Sequence steps list.
            var ordered = notes.map(function (a) { return Object.assign({}, a); });
            ordered.sort(function (a, b) {
                return ((a.meta || {}).kind === "wait" ? 0 : 1) - ((b.meta || {}).kind === "wait" ? 0 : 1);
            });
            return ordered.filter(function (a) {
                if (a.x < xr[0] || a.x > xr[1]) return false;
                var place = a.yref + "|" + a.x + "|" + a.text;
                if (seen[place]) return false;
                seen[place] = true;
                var width = a.text.length * CHAR_PX + 8;
                if (width > w) return false;
                var px = (a.x - xr[0]) * w / (xr[1] - xr[0]);
                a.xanchor = px < width / 2 ? "left" : px > w - width / 2 ? "right" : "center";
                var left = a.xanchor === "left" ? px : a.xanchor === "right" ? px - width : px - width / 2;
                if (bottom.some(function (b) {
                    return b.yref === a.yref && left < b.right && left + width > b.left;
                })) {
                    return false;
                }
                var rows = used[a.yref] || (used[a.yref] = [[], []]);
                for (var r = 0; r < rows.length; r++) {
                    if (rows[r].every(function (b) { return left >= b[1] || left + width <= b[0]; })) {
                        rows[r].push([left, left + width]);
                        a.yshift = 2 + r * 16;
                        return true;
                    }
                }
                return false;
            });
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
            var hostPx = host.clientWidth || 900;
            // A narrow host (a phone-width dialog): titles beside the lanes would leave the plot
            // a sliver, so each lane's title moves above its lane and the plot takes the row.
            var stacked = hostPx - margin.l - margin.r < STACK_PLOT_PX;
            var bands = drawn.map(function (l) {
                return stacked ? TITLE_LINE_PX + (titleLines(l).length - 1) * FACT_LINE_PX + 4 : 0;
            });
            if (stacked) {
                margin.l = TICK_ROOM + BAR_PX + 4;
                plotPx += bands.reduce(function (a, b) { return a + b; }, 0);
            }
            var widthPx = Math.max(1, (hostPx - margin.l - margin.r));
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
            if (stacked) layout.xaxis.nticks = Math.max(2, Math.floor(widthPx / TICK_SPACE_PX));
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
            var pulseLabels = labels(span, widthPx);
            var y = 0;
            drawn.forEach(function (lane, i) {
                var ax = i === 0 ? "y" : "y" + (i + 1);
                var axKey = i === 0 ? "yaxis" : "yaxis" + (i + 1);
                var lanePx = heights[i];
                y += bands[i];
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
                    xref: "paper", yref: ax + " domain", x: 0, y: stacked ? 1 : mid, xanchor: "left", yanchor: "bottom",
                    xshift: -margin.l + BAR_PX + 6, yshift: stacked ? (tl.length - 1) * FACT_LINE_PX + 4 : 2, align: "left", showarrow: false,
                    text: "<b>" + esc(tl[0]) + "</b>",
                    font: { size: LABEL_PX, color: idCol },
                    bgcolor: faint(idCol, spectLane ? 0.10 : CHIP_ALPHA), borderpad: 3, borderwidth: 0,
                    meta: { role: "lane-title", lane: lane.key, lines: tl, color: idCol }
                });
                // a thin bar in the lane colour along the lane's full height, at the gutter's left edge
                shapes.push({ type: "rect", name: "lane-bar", xref: "paper", xsizemode: "pixel", xanchor: 0,
                              x0: -margin.l, x1: -margin.l + BAR_PX, yref: ax + " domain", y0: 0, y1: 1,
                              fillcolor: idCol, line: { width: 0 }, layer: "above" });
                fixed.push({
                    xref: "paper", yref: ax + " domain", x: 0, y: stacked ? 1 : mid, xanchor: "left",
                    yanchor: stacked ? "bottom" : "top", xshift: -margin.l + BAR_PX + 6, yshift: stacked ? 2 : -2, align: "left", showarrow: false, text: tl.slice(1).map(esc).join("<br>"),
                    font: { size: TEXT_PX, color: c.muted },
                    meta: { role: "lane-facts", lane: lane.key }
                });
                // a late first pulse: say where the lane starts
                var first = plan[i].segs[0];
                // (only when the idle stretch is wide enough to hold the words)
                var startText = first ? "starts at " + fmt(first.start_ns) + " ns" : "";
                // a place clear of the 0 V line and the shown limit lines, or none at all
                var lineYs = [0].concat((lane.limit_v || []).filter(function (v) { return num(v) && v >= range[0] && v <= range[1]; }));
                var spots = [0.5, 0.35, 0.62].filter(function (fy) {
                    var vAt = range[0] + fy * (range[1] - range[0]);
                    return lineYs.every(function (ly) { return Math.abs(ly - vAt) * lanePx / (range[1] - range[0]) > 10; });
                });
                var spot = spots[0];
                if (spot !== undefined && first && num(first.start_ns) && first.start_ns > 0) {
                    fixed.push({
                        // anchored to the lane's own bar: right-aligned just before where the bar starts
                        xref: "x", yref: ax + " domain", x: first.start_ns, y: spot, xanchor: "right", yanchor: "middle",
                        xshift: -4, showarrow: false, text: startText,
                        font: { size: TEXT_PX, color: c.muted },
                        meta: { role: "starts-at", lane: lane.key, lanePx: lanePx, spots: spots }
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
                        if (exceeds(lane, lim)) fixed.push(limitLabel(lane, lim, ax, c));
                    });
                }
                plan[i].segs.forEach(function (s) {
                    var sev = severity(s), flagged = !!sev;
                    var col = sev ? c.tones[sev].line : idCol;   // state beats identity
                    var fillCol = sev ? c.tones[sev].fill : faint(idCol);
                    // the pulse body: filled to 0 V at low opacity, the line on top
                    traces.push({
                        x: (s.t_ns || []).slice(), y: (s.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: col, width: 0, shape: "hv" }, fill: "tozeroy", fillcolor: fillCol,
                        meta: { role: "fill", lane: lane.key, operation: s.operation, flagged: flagged, severity: sev },
                        hoverinfo: "skip"
                    });
                    traces.push({
                        x: (s.t_ns || []).slice(), y: (s.v || []).slice(),
                        xaxis: "x", yaxis: ax, type: "scatter", mode: "lines",
                        line: { color: col, width: 2, shape: "hv" }, marker: { color: col, size: 7 },
                        mode: (s.t_ns || []).length < 2 ? "markers" : "lines",
                        name: lane.key + " " + (s.operation || ""),
                        meta: { role: "segment", lane: lane.key, operation: s.operation, flagged: flagged, severity: sev },
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
                    text: times.map(function (t) { return esc(readout(laneValueAt(lane, t))); }),
                    customdata: times.map(function () { return esc(lane.label || lane.key); }),
                    xaxis: "x", yaxis: ax, type: "scatter", mode: "markers",
                    marker: { opacity: 0, size: 1, color: idCol },
                    line: { color: idCol },
                    name: lane.label || lane.key,
                    meta: { role: "hover", lane: lane.key },
                    hovertemplate: "%{text}<extra>%{customdata}</extra>"
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
                        var sev = severity(s);
                        var row = plan[i].bottom && k % 2 === 1 ? "bottom" : "top";
                        var x0 = Math.max(s.start_ns, xr[0]);
                        var next = segs[k + (plan[i].bottom ? 2 : 1)];
                        var stop = next ? Math.max(next.start_ns, xr[0]) : xr[1];
                        var room = Math.max(4, Math.floor((stop - x0) * pxPerNs / CHAR_PX) - 1);
                        var extra = (s.flags || []).map(function (f) { return FLAG_TEXT[f] || f; });
                        if (s.truncated) extra.push(TRUNCATED_TEXT);
                        var line2 = levelText(s) + (extra.length ? ", " + extra.join(", ") : "");
                        var anchorRight = line2.length > room;
                        var xa = x0;
                        if (anchorRight) {
                            // right-aligned at the segment's end, as far left as the row allows
                            xa = Math.min(s.end_ns, xr[1]);
                            var prev = segs[k - (plan[i].bottom ? 2 : 1)];
                            var left = prev ? Math.max(prev.end_ns, xr[0]) : xr[0];
                            room = Math.max(room, Math.floor((xa - left) * pxPerNs / CHAR_PX) - 1);
                        }
                        var name = shorten(s.operation || "", room);
                        var l2 = line2;   // cut only by declutter when a neighbour needs the room
                        out.push({
                            xref: "x", yref: ax + " domain", x: xa, xanchor: anchorRight ? "right" : "left", align: anchorRight ? "right" : "left",
                            y: row === "top" ? 1 : 0, yanchor: row === "top" ? "top" : "bottom",
                            showarrow: false, text: esc(name) + "<br>" + esc(l2),
                            hovertext: esc(s.operation || ""),
                            font: { size: TEXT_PX, color: sev ? c.tones[sev].line : c.text },
                            meta: { role: "segment-text", lane: lane.key, operation: s.operation, row: row,
                                    name: name, line2: line2, shown2: l2 }
                        });
                    });
                });
                return declutter(out, xr, w);
            }
            layout.annotations = fixed.filter(function (a) {
                return startsAtFits(a, pulseLabels, span, widthPx);
            }).concat(pulseLabels);
            var markNotes = stepMarks(shapes, c, drawn, false);
            layout.annotations = layout.annotations.concat(fitMarks(markNotes, span, widthPx, pulseLabels));
            if (window.PlotTheme && window.PlotTheme.houseLayout) layout = window.PlotTheme.houseLayout(layout);
            return { data: traces, layout: layout, drawn: drawn, fixedAnnotations: fixed, labels: labels, markNotes: markNotes,
                     span: span, widthPx: widthPx, hostPx: hostPx };
        }

        /* A lane exceeds a limit when a received sample lies beyond it. */
        function exceeds(l, lim) {
            return (l.segments || []).some(function (s) {
                return (s.v || []).some(function (v) { return num(v) && (lim >= 0 ? v > lim : v < lim); });
            });
        }
        function limitLabel(l, lim, ax, c) {
            return { xref: "paper", yref: ax, x: 1, y: lim, xanchor: "right", yanchor: "bottom", showarrow: false,
                     text: esc(l.label || l.key) + " limit " + voltNum(lim) + " V", font: { size: TEXT_PX, color: c.tones.danger.line },
                     meta: { role: "limit-label", lane: l.key } };
        }

        /* ---------------- Overlay view: every line on ONE time axis ---------------- */

        function maxAbs(l) {
            var m = 0;
            (l.segments || []).forEach(function (s) { (s.v || []).forEach(function (v) { if (num(v)) m = Math.max(m, Math.abs(v)); }); });
            return m;
        }
        function nameOf(key) {
            var l = lanes.filter(function (x) { return x.key === key; })[0];
            return l ? (l.label || l.key) : key;
        }
        /* "hidden" belongs to the Overlay view: in Lanes every gate line counts. */
        function visibleGate() {
            return gate.filter(function (l) { return (state.view !== "overlay" || !state.hidden[l.key]) && lineSpan(l); });
        }
        function overlayLines() {
            var out = visibleGate();
            if (!state.fold && !state.hidden[SPECT_KEY]) out = out.concat(spect);
            return out;
        }

        /* A line is the UNION of its segments' intervals (its own idle time between
           segments is not playing). Times closer than TIME_EPS ns are equal. */
        function lineIntervals(l) {
            var iv = (l.segments || []).filter(function (s) { return num(s.start_ns) && num(s.end_ns); })
                .map(function (s) { return [Math.min(s.start_ns, s.end_ns), Math.max(s.start_ns, s.end_ns)]; })
                .sort(function (a, b) { return a[0] - b[0]; });
            var out = [];
            iv.forEach(function (x) {
                var last = out[out.length - 1];
                if (last && x[0] <= last[1] + TIME_EPS) last[1] = Math.max(last[1], x[1]);
                else out.push(x.slice());
            });
            return out;
        }
        function lineSpan(l) {
            var iv = lineIntervals(l);
            return iv.length ? [iv[0][0], iv[iv.length - 1][1]] : null;
        }
        function pairTiming(A, B) {
            var ia = lineIntervals(A), ib = lineIntervals(B), inter = [];
            ia.forEach(function (x) {
                ib.forEach(function (y) {
                    var lo = Math.max(x[0], y[0]), hi = Math.min(x[1], y[1]);
                    if (hi - lo > TIME_EPS) inter.push([lo, hi]);
                });
            });
            inter.sort(function (a, b) { return a[0] - b[0]; });
            if (inter.length) {
                var total = inter.reduce(function (acc, x) { return acc + (x[1] - x[0]); }, 0);
                return { a: A.key, b: B.key, kind: "overlap", ns: total, from_ns: inter[0][0], to_ns: inter[inter.length - 1][1],
                         intervals: inter };
            }
            var best = null;
            ia.forEach(function (x) {
                ib.forEach(function (y) {
                    var from, to;
                    if (Math.max(x[0], y[0]) <= Math.min(x[1], y[1]) + TIME_EPS) { from = to = Math.max(x[0], y[0]); }
                    else if (x[1] < y[0]) { from = x[1]; to = y[0]; }
                    else { from = y[1]; to = x[0]; }
                    var d = Math.max(0, to - from);
                    if (!best || d < best.d - TIME_EPS || (Math.abs(d - best.d) <= TIME_EPS && from < best.from)) best = { d: d, from: from, to: Math.max(from, to) };
                });
            });
            if (!best) return null;
            if (best.d <= TIME_EPS) return { a: A.key, b: B.key, kind: "meet", ns: 0, from_ns: best.from, to_ns: best.from, intervals: [] };
            return { a: A.key, b: B.key, kind: "gap", ns: best.d, from_ns: best.from, to_ns: best.to, intervals: [[best.from, best.to]] };
        }
        /* Every pair of visible gate lines: overlaps first, then meets, then gaps,
           each group in time order. */
        function timing() {
            // a sequence step plays before or after the gate by design: its marks show it
            var ls = visibleGate().filter(function (l) { return !l.step; });
            var out = [];
            for (var i = 0; i < ls.length; i++) {
                for (var j = i + 1; j < ls.length; j++) {
                    var a = ls[i], b = ls[j];
                    var sa = lineSpan(a), sb = lineSpan(b);
                    if (sb[0] < sa[0] - TIME_EPS) { var t = a; a = b; b = t; }
                    var p = pairTiming(a, b);
                    if (p) out.push(p);
                }
            }
            var rank = { overlap: 0, meet: 1, gap: 2 };
            return out.sort(function (x, y) { return rank[x.kind] - rank[y.kind] || x.from_ns - y.from_ns || x.to_ns - y.to_ns; });
        }
        /* A gap is drawn as a bracket only when it is true silence: no other
           visible gate line plays inside it. */
        function silentGap(p) {
            return visibleGate().every(function (l) {
                if (l.key === p.a || l.key === p.b) return true;
                return lineIntervals(l).every(function (x) { return Math.min(x[1], p.to_ns) - Math.max(x[0], p.from_ns) <= TIME_EPS; });
            });
        }

        /* Edge guides of the visible gate lines with their times written at the
           top: a label that would run into the previous one goes to a second row;
           when that row is taken too, the two times share one label ("160 / 161"). */
        function guideLabels(times, xr, w, c) {
            var pxPerNs = w / Math.max(1e-9, xr[1] - xr[0]);
            var rows = [null, null], out = [];
            times.filter(function (t) { return t >= xr[0] && t <= xr[1]; }).forEach(function (t) {
                var text = fmt(t), px = (t - xr[0]) * pxPerNs, half = (text.length * CHAR_PX + 8) / 2;
                var row = -1;
                for (var r = 0; r < 2; r++) {
                    if (!rows[r] || rows[r].right < px - half) { row = r; break; }
                }
                if (row < 0) {
                    var prev = rows[1].ann;
                    prev.text += " / " + text;
                    prev.meta.times.push(t);
                    rows[1].right += (text.length + 3) * CHAR_PX;
                    return;
                }
                var ann = { xref: "x", yref: "paper", x: t, y: 1, yanchor: "bottom", yshift: 2 + row * 14, showarrow: false,
                            text: text, font: { size: TEXT_PX, color: c.muted },
                            meta: { role: "guide-label", times: [t], row: row } };
                rows[row] = { right: px + half, ann: ann };
                out.push(ann);
            });
            return out;
        }

        function overlayFigure() {
            var c = theme();
            var lines = overlayLines();
            var gl = visibleGate();
            var span = spanOf(gate.concat(state.fold ? [] : spect));
            var margin = { l: 64, r: 12, t: 0, b: 38 };
            var widthPx = Math.max(200, ((host.clientWidth || 900) - margin.l - margin.r));
            margin.t = GUIDE_ROWS_PX + 6;
            var same = state.height === "same";
            var family = uiFont();
            var traces = [], shapes = [], fixed = [];
            var factor = {};
            lines.forEach(function (l) {
                var m = maxAbs(l);
                factor[l.key] = same ? (m > 0 ? 1 / m : 1) : 1;
            });
            function scaled(l, vs) { var f = factor[l.key]; return vs.map(function (v) { return num(v) ? v * f : v; }); }
            // the V axis: real volts over all drawn lines, or normalised
            var lo = 0, hi = 0;
            lines.forEach(function (l) {
                var e = dataExtent(l, state.showSim);
                lo = Math.min(lo, e[0] * factor[l.key]); hi = Math.max(hi, e[1] * factor[l.key]);
            });
            var pairs = timing();
            var bracketRows = Math.min(MAX_BRACKETS, pairs.reduce(function (n, p) {
                return n + (p.kind === "overlap" ? p.intervals.length : (p.kind === "gap" && silentGap(p) ? 1 : 0));
            }, 0));
            var plotPx = 240 + 18 * bracketRows;
            var range = bandedRange([lo, hi], plotPx, 20 + 22 * bracketRows, 0);
            var layout = {
                margin: margin, height: plotPx + margin.t + margin.b, showlegend: false,
                hovermode: "x unified", hoverdistance: -1,
                font: { family: family, size: TEXT_PX, color: c.text },
                hoverlabel: { font: { family: family, size: TEXT_PX } },
                xaxis: { title: { text: "time (ns)", font: { size: TEXT_PX } }, zeroline: false, range: span.slice(),
                         autorange: false, showgrid: true, gridcolor: c.grid, tickfont: { size: TEXT_PX } },
                yaxis: { range: range, autorange: false, zeroline: false, showgrid: false, tickfont: { size: TEXT_PX },
                         title: same ? { text: "normalised", font: { size: TEXT_PX } } : undefined,
                         showticklabels: !same, ticksuffix: same ? "" : " V" },
                shapes: shapes
            };
            shapes.push({ type: "line", name: "zero", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 0, y1: 0,
                          line: { color: c.grid, width: 1 }, layer: "below" });
            lines.forEach(function (l) {
                var spectLine = l.group === "spectator";
                var idCol = laneCol(l);
                (l.segments || []).forEach(function (s) {
                    var sev = severity(s), flagged = !!sev;
                    var col = sev ? c.tones[sev].line : idCol;
                    if (!spectLine) {
                        traces.push({ x: (s.t_ns || []).slice(), y: scaled(l, (s.v || []).slice()), xaxis: "x", yaxis: "y",
                                      type: "scatter", mode: "lines", line: { color: col, width: 0, shape: "hv" },
                                      fill: "tozeroy", fillcolor: sev ? c.tones[sev].fill : faint(col), hoverinfo: "skip",
                                      meta: { role: "fill", lane: l.key, operation: s.operation, flagged: flagged, severity: sev } });
                    }
                    traces.push({ x: (s.t_ns || []).slice(), y: scaled(l, (s.v || []).slice()), xaxis: "x", yaxis: "y",
                                  type: "scatter", mode: (s.t_ns || []).length < 2 ? "markers" : "lines",
                                  marker: { color: col, size: 7 }, line: { color: col, width: spectLine ? 1 : 2, shape: "hv" },
                                  hoverinfo: "skip", meta: { role: "segment", lane: l.key, operation: s.operation, flagged: flagged, severity: sev } });
                    var ts = s.t_ns || [];
                    if (s.truncated && ts.length && ts[ts.length - 1] < s.end_ns) {
                        shapes.push({ type: "rect", name: "not-received", xref: "x", x0: ts[ts.length - 1], x1: s.end_ns,
                                      yref: "paper", y0: 0, y1: 1, fillcolor: c.grid, opacity: 0.35, line: { width: 0 }, layer: "below" });
                        fixed.push({ xref: "x", yref: "paper", x: (ts[ts.length - 1] + s.end_ns) / 2, y: 0.5, showarrow: false,
                                     text: "not received", font: { size: TEXT_PX, color: c.muted },
                                     meta: { role: "not-received", lane: l.key, operation: s.operation } });
                    }
                });
                if (l.sim && state.showSim) {
                    traces.push({ x: (l.sim.t_ns || []).slice(), y: scaled(l, (l.sim.v || []).slice()), xaxis: "x", yaxis: "y",
                                  type: "scatter", mode: "lines", line: { color: c.sim, width: 1.5, dash: "dot" },
                                  hoverinfo: "skip", meta: { role: "sim", lane: l.key } });
                }
                if (!same && l.limit_v && !spectLine) {
                    l.limit_v.forEach(function (lim) {
                        if (!num(lim) || lim < range[0] || lim > range[1] || !exceeds(l, lim)) return;
                        shapes.push({ type: "line", name: "limit", xref: "paper", x0: 0, x1: 1, yref: "y", y0: lim, y1: lim,
                                      line: { color: c.tones.danger.line, width: 1, dash: "dot" }, layer: "below",
                                      meta: { lane: l.key } });
                        fixed.push(limitLabel(l, lim, "y", c));
                    });
                }
            });
            // one hover trace per drawn line: the same readout as the lanes view, real volts
            var times = hoverTimes(lines);
            lines.forEach(function (l) {
                traces.push({ x: times.slice(), y: times.map(function () { return 0; }),
                              text: times.map(function (t) { return esc(readout(laneValueAt(l, t))); }),
                              customdata: times.map(function () { return esc(l.label || l.key); }),
                              xaxis: "x", yaxis: "y", type: "scatter", mode: "markers",
                              marker: { opacity: 0, size: 1, color: laneCol(l) }, name: l.label || l.key,
                              meta: { role: "hover", lane: l.key },
                              hovertemplate: "%{text}<extra>%{customdata}</extra>" });
            });
            // edge guides of the visible gate lines
            var edges = [];
            gl.forEach(function (l) {
                (l.segments || []).forEach(function (s) {
                    [s.start_ns, s.end_ns].forEach(function (t) { if (num(t) && edges.indexOf(t) < 0) edges.push(t); });
                });
            });
            edges.sort(function (a, b) { return a - b; });
            var hiPair = state.highlight === null ? null : pairs.filter(function (q) { return pairKey(q) === state.highlight; })[0] || null;
            var hiEdges = !hiPair ? [] : (hiPair.intervals.length ? hiPair.intervals : [[hiPair.from_ns, hiPair.to_ns]])
                .reduce(function (a, x) { return a.concat(x); }, []);
            edges.forEach(function (t) {
                var lit = hiEdges.some(function (e) { return Math.abs(e - t) <= TIME_EPS; });
                shapes.push({ type: "line", name: "guide", xref: "x", x0: t, x1: t, yref: "paper", y0: 0, y1: 1,
                              line: { color: c.muted, width: lit ? 2 : 1, dash: "dash" }, opacity: lit ? 1 : 0.6, layer: "below",
                              meta: { lit: lit } });
            });
            // brackets: every overlap interval; a gap only when it is true silence;
            // meets live in the list. Up to two brackets carry their text here.
            var warn = cssVar("--ui-warn-border") || cssVar("--ui-warn-text") || c.flag;
            var brackets = [];
            pairs.forEach(function (p, k) {
                if (p.kind === "overlap") p.intervals.forEach(function (x) { brackets.push({ p: p, k: k, from: x[0], to: x[1] }); });
                else if (p.kind === "gap" && silentGap(p)) brackets.push({ p: p, k: k, from: p.from_ns, to: p.to_ns });
            });
            var more = Math.max(0, brackets.length - MAX_BRACKETS);
            brackets = brackets.slice(0, MAX_BRACKETS);   // overlaps come first
            if (more) {
                fixed.push({ xref: "paper", yref: "paper", x: 1, y: 1, xanchor: "right", yanchor: "bottom", yshift: GUIDE_ROWS_PX - 14,
                             showarrow: false, text: more + " more in the list", font: { size: TEXT_PX, color: c.muted },
                             meta: { role: "brackets-more", count: more } });
            }
            var labelled = brackets.length <= 2;
            brackets.forEach(function (bk, n) {
                var p = bk.p;
                // a gap is the warn tone; overlaps stay quiet
                var col = p.kind === "gap" ? (c.tones.warn.line || c.muted) : c.muted;
                var lit = state.highlight !== null && pairKey(p) === state.highlight;
                var y = 1 - (14 + 22 * n) / plotPx;
                shapes.push({ type: "path", name: "bracket", xref: "x", yref: "paper",
                              path: "M " + bk.from + " " + (y - 0.02) + " L " + bk.from + " " + y + " L " + bk.to + " " + y +
                                    " L " + bk.to + " " + (y - 0.02),
                              line: { color: col, width: lit ? 3 : (labelled ? (p.kind === "gap" ? 1.5 : 1) : 1) },
                              meta: { kind: p.kind, index: bk.k, lit: lit, from: bk.from, to: bk.to } });
                if (!labelled) return;
                var what = p.kind === "gap" ? "gap " + fmt(p.ns) + " ns"
                    : "overlap " + fmt(bk.to - bk.from) + " ns" + (p.intervals.length > 1 ? " (" + fmt(p.ns) + " ns in all)" : "");
                fixed.push({ xref: "x", yref: "paper", x: (bk.from + bk.to) / 2, y: y, yanchor: "bottom", showarrow: false,
                             text: esc(nameOf(p.a)) + " to " + esc(nameOf(p.b)) + ": " + what, font: { size: TEXT_PX, color: col },
                             meta: { role: "bracket-label", kind: p.kind, a: p.a, b: p.b } });
            });
            var markNotes = stepMarks(shapes, c, lines, true);
            if (window.PlotTheme && window.PlotTheme.houseLayout) layout = window.PlotTheme.houseLayout(layout);
            function labels(xr, w) { return guideLabels(edges, xr, w, c); }
            var guideTexts = labels(span, widthPx);
            layout.annotations = fixed.concat(guideTexts).concat(fitMarks(markNotes, span, widthPx, guideTexts));
            return { data: traces, layout: layout, drawn: lines, fixedAnnotations: fixed, labels: labels, markNotes: markNotes,
                     span: span, widthPx: widthPx, view: "overlay", timing: pairs };
        }

        /* The overlay legend: one chip per gate line (and one for the spectator
           group) in the margin above the plot, in the line's identity colour, the
           same chip shape as the lane names. A chip toggles its line (click, or
           Enter / Space once focused); hidden lines leave the guides and brackets. */
        function lineSummaryByKey(key) {
            var l = lanes.filter(function (x) { return x.key === key; })[0];
            return l ? lineSummary(l) : "";
        }
        function lineSummary(l) {
            var sp = lineSpan(l), segs = l.segments || [];
            var parts = segs.length === 1 ? [levelText(segs[0])] : [segs.length + " pulses"];
            if (sp) parts.push("starts " + fmt(sp[0]) + " ns");
            return parts.join(", ");
        }

        /* The plot's text alternative: what each visible line plays and the timing,
           from the same data as the list. */
        function describe() {
            var lines = (state.view === "overlay" ? overlayLines() : drawnLanes()).filter(function (l) { return l.group !== "spectator"; });
            var extraLines = Math.max(0, lines.length - MAX_SPOKEN);
            lines = lines.slice(0, MAX_SPOKEN);
            var parts = lines.map(function (l) {
                var iv = lineIntervals(l).map(function (x) { return fmt(x[0]) + " to " + fmt(x[1]) + " ns"; }).join(" and ");
                var segs = l.segments || [];
                var lvl = segs.length === 1 ? levelText(segs[0]).split(",")[0] : segs.length + " pulses";
                return (l.label || l.key) + " plays " + (iv || "nothing") + (segs.length ? " at " + lvl.replace(/^level /, "") : "");
            });
            var ps = timing();
            ps.slice(0, MAX_SPOKEN).forEach(function (p) {
                parts.push(p.kind === "meet" ? nameOf(p.a) + " and " + nameOf(p.b) + " meet at " + fmt(p.from_ns) + " ns"
                    : p.kind + " " + fmt(p.ns) + " ns between " + nameOf(p.a) + " and " + nameOf(p.b));
            });
            var rest = extraLines + Math.max(0, ps.length - MAX_SPOKEN);
            return parts.join("; ") + (rest ? "; and " + rest + " more, see the list" : "");
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
            voltDigits = pictureDigits;
            var ov = state.view === "overlay";
            viewSeg.buttons.forEach(function (b) {
                var onNow = b.getAttribute("data-view") === state.view;
                b.classList.toggle("ui-segmented-on", onNow);
                b.setAttribute("aria-pressed", onNow ? "true" : "false");
            });
            heightSeg.buttons.forEach(function (b) {
                var onNow = b.getAttribute("data-height") === state.height;
                b.classList.toggle("ui-segmented-on", onNow);
                b.setAttribute("aria-pressed", onNow ? "true" : "false");
            });
            // each view shows only its own vertical-scale control
            [scaleLab, seg].forEach(function (n) { n.hidden = ov; });
            [heightSeg.label, heightSeg.group].forEach(function (n) { n.hidden = !ov; });
            // the list first: a rebuild clears the highlight before the picture is made
            var pairsNow = timing();
            var listKey = JSON.stringify(pairsNow);
            if (listKey !== pairList.__key) { pairList.__key = listKey; buildPairList(pairsNow); }
            lastFigure = ov ? overlayFigure() : figure();
            legend.hidden = !ov;
            legendBtns.forEach(function (lb) {
                lb.btn.setAttribute("aria-pressed", state.hidden[lb.key] ? "false" : "true");
                lb.btn.hidden = lb.spectGroup && state.fold;
                lb.btn.classList.toggle("ui-legend-btn--off", !!state.hidden[lb.key]);
                if (lb.dot) lb.dot.classList.toggle("ui-line-dot--off", !!state.hidden[lb.key]);
                // a line switched off reads "hidden" even while the button has focus
                lb.note.textContent = state.hidden[lb.key] ? "hidden" : (lb.spectGroup ? "" : lineSummaryByKey(lb.key));
            });
            plot.setAttribute("aria-label", describe());
            if (window._plotlyRender) {
                var p = window._plotlyRender(plot, lastFigure.data, lastFigure.layout,
                    { displayModeBar: false, responsive: true });
                if (p && p.then) p.then(function () { bindRelayout(); });
            }
        }

        /* Zoom, pan and resize: re-fit the segment texts to the real plot width
           and the visible time range, so names and rows never collide. */
        var relayoutBound = false;
        function startsAtFits(a, texts, xr, w) {
            if (!a.meta || a.meta.role !== 'starts-at') return true;
            var right = (a.x - xr[0]) * w / (xr[1] - xr[0]) - 4;
            var left = right - a.text.length * CHAR_PX;
            // Tick labels live in the left gutter, outside the plot. The words
            // plus 12 px rule already keeps this label inside the plot.
            if (left <= 8) return false;
            var spot = a.meta.spots.filter(function (fy) {
                return texts.every(function (t) {
                    if (t.meta.lane !== a.meta.lane) return true;
                    var x = (t.x - xr[0]) * w / (xr[1] - xr[0]);
                    var textWidth = Math.max(t.meta.name.length, t.meta.shown2.length) * CHAR_PX;
                    var titleLeft = t.xanchor === 'right' ? x - textWidth : x;
                    return right <= titleLeft || left >= titleLeft + textWidth ||
                        (t.meta.row === 'top' ? 1 - fy : fy) * a.meta.lanePx > BAND_PX + TEXT_PX / 2 + 12;
                });
            })[0];
            if (spot === undefined) return false;
            a.y = spot;
            return true;
        }
        function onRelayout(ev) {
            if (destroyed || !lastFigure || !ev) return;
            var keys = Object.keys(ev);
            if (!keys.some(function (k) { return /^xaxis|^width$|^autosize$/.test(k); })) return;
            if (state.view === "lanes" && host.clientWidth && host.clientWidth !== lastFigure.hostPx) {
                draw();
                return;
            }
            var fl = plot._fullLayout;
            var xr = fl && fl.xaxis && fl.xaxis.range ? fl.xaxis.range.slice() : lastFigure.span;
            var w = fl && fl.xaxis && fl.xaxis._length ? fl.xaxis._length : lastFigure.widthPx;
            var texts = lastFigure.labels(xr, w);
            var anns = lastFigure.fixedAnnotations.filter(function (a) {
                return startsAtFits(a, texts, xr, w);
            }).concat(texts).concat(fitMarks(lastFigure.markNotes || [], xr, w, texts));
            lastFigure.layout.annotations = anns;
            if (window.Plotly && window.Plotly.relayout) window.Plotly.relayout(plot, { annotations: anns });
        }
        function bindRelayout() {
            if (relayoutBound || destroyed || typeof plot.on !== "function") return;
            plot.on("plotly_relayout", onRelayout);
            relayoutBound = true;
        }

        /* What hover shows: the lines drawn in the current view; {all: true} for every line. */
        function shownLines(o) {
            if (o && o.all) return lanes.slice();
            return state.view === "overlay" ? overlayLines() : drawnLanes();
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
            setView: function (m) { state.view = m === "overlay" ? "overlay" : "lanes"; draw(); },
            setHeight: function (m) { state.height = m === "same" ? "same" : "real"; draw(); },
            setLineHidden: function (key, b) { state.hidden[key] = !!b; draw(); },
            timing: function () { return timing(); },
            valuesAt: function (t, o) { return shownLines(o).map(function (l) { return laneValueAt(l, t); }); },
            readout: function (t, o) {
                return shownLines(o).map(function (l) { return { key: l.key, text: readout(laneValueAt(l, t)) }; });
            },
            laneCount: function () {
                return { gate: gate.length, spectator: spect.length,
                         drawn: state.view === "overlay" ? overlayLines().length : drawnLanes().length };
            },
            figure: function () { return lastFigure; },
            relayout: onRelayout
        };
        draw();
        if (window.PlotHost) { try { window.PlotHost.observe(root); } catch (e) { /* no observer */ } }
        return handle;
    }

    /* The chart's volt text for a set of levels shown together (a table column, a readout): the digits
       are picked over the whole set, so two levels more than VOLT_EPS apart never print the same.
       A value that is not a number gives null (the caller keeps its own honest text). */
    function voltTexts(values) {
        var d = pickVoltDigits(values || []);
        var si = window.PlotTheme && typeof window.PlotTheme.siFormat === "function" ? window.PlotTheme.siFormat : null;
        return (values || []).map(function (v) {
            if (!num(v)) return null;
            // a level far below a millivolt reads with an SI prefix (355.87 nV), not a row of zeros
            if (si && v !== 0 && Math.abs(v) < 1e-3) return si(v, "V");
            return voltNum(v, d) + " V";
        });
    }
    window.GateTimeline = { render: render, valueAt: laneValueAt, readout: readout, laneColor: laneColor,
                            voltTexts: voltTexts };
})();
