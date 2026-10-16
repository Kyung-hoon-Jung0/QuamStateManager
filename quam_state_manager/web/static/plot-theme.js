/* House Plotly theme — ONE styling source for every plot surface.
 *
 * Colors come from CSS custom properties (the app's ~430-token theme system),
 * read at render time, so plots follow dark/light instantly via retheme().
 * Servers ship data + semantics; the CLIENT themes — never bake fonts/bgs
 * into server-built figures (the old h5 path hardcoded plotly_dark and washed
 * out in light mode).
 */
(function () {
    'use strict';

    function cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement).getPropertyValue(name);
        return (v && v.trim()) || fallback;
    }

    function palette() {
        var out = [];
        for (var i = 1; i <= 8; i++) {
            var c = cssVar('--plot-colorway-' + i, '');
            if (c) out.push(c);
        }
        return out.length ? out : [
            '#4f9cf9', '#f97316', '#22c55e', '#e879f9',
            '#facc15', '#2dd4bf', '#f43f5e', '#a3a3a3'];
    }

    function isDark() {
        return document.documentElement.getAttribute('data-theme') === 'dark';
    }

    function artistColor(color, foreground) {
        if (Array.isArray(color)) return color.map(function (c) { return artistColor(c, foreground); });
        if (typeof color !== 'string') return color;
        var value = color.trim().toLowerCase(), channels;
        if (value === 'black' || value === 'k') channels = [0, 0, 0];
        var hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(value);
        if (hex) {
            var digits = hex[1];
            if (digits.length <= 4) digits = digits.split('').map(function (c) { return c + c; }).join('');
            if (digits.length === 8 && parseInt(digits.slice(6), 16) === 0) return color;
            channels = [0, 2, 4].map(function (i) { return parseInt(digits.slice(i, i + 2), 16); });
        }
        var rgb = /^rgba?\(\s*([\d.]+)(%)?\s*,\s*([\d.]+)(%)?\s*,\s*([\d.]+)(%)?(?:\s*,\s*([\d.]+)(%)?)?\s*\)$/.exec(value)
            || /^rgba?\(\s*([\d.]+)(%)?\s+([\d.]+)(%)?\s+([\d.]+)(%)?(?:\s*\/\s*([\d.]+)(%)?)?\s*\)$/.exec(value);
        if (rgb) {
            if (rgb[7] !== undefined && +rgb[7] === 0) return color;
            channels = [1, 3, 5].map(function (i) { return +rgb[i] * (rgb[i + 1] ? 2.55 : 1); });
        }
        return channels && channels.every(function (c) { return c < 48; }) ? foreground : color;
    }

    function artistStyle(style, foreground) {
        if (!style) return style;
        var out = Object.assign({}, style);
        if ('color' in out) out.color = artistColor(out.color, foreground);
        if (out.line) out.line = artistStyle(out.line, foreground);
        return out;
    }

    /** Adapt explicit near-black artist colors without changing map scales. */
    function houseData(data) {
        if (!isDark()) return data;
        var foreground = cssVar('--plot-axis-text', cssVar('--pico-color', '#ccc'));
        return (data || []).map(function (trace) {
            var out = Object.assign({}, trace);
            ['line', 'marker', 'textfont'].forEach(function (key) {
                if (trace[key]) out[key] = artistStyle(trace[key], foreground);
            });
            return out;
        });
    }

    /* v1.5.1 (D3 class K): text written ON a heatmap cell sits on the lab's own colour
       scale, which the theme never changes -- the lab picked black for the bright cells and
       white for the dark ones. Mapping that black to the light foreground put light text on
       yellow cells (a confusion matrix's diagonal). Such text keeps the lab colour.
       In: a no-arrow annotation in the data coordinates of a heatmap's own axes, inside the
       heatmap's extent (category, cell edges, or centres plus half a cell). */
    function inExtent(v, coords, n, log) {
        if (!(n > 0)) return false;
        if (!Array.isArray(coords) || !coords.length) coords = null;
        if (coords && coords.some(function (c) { return typeof c === 'string'; })) {
            return coords.map(String).indexOf(String(v)) !== -1;
        }
        if (typeof v !== 'number' || !isFinite(v)) return false;
        if (!coords) {                                      // no coordinates: cell indices
            if (log) return false;
            return v >= -0.5 && v <= n - 0.5;
        }
        // a log axis places the cells (and the annotation, see the converter) in log10
        var nums = coords.filter(function (c) { return typeof c === 'number' && isFinite(c) && (!log || c > 0); })
            .map(function (c) { return log ? Math.log10(c) : c; });
        if (!nums.length) return false;
        var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums);
        if (coords.length !== n + 1) {                      // centres: half a cell beyond each end
            var half = nums.length > 1 ? (hi - lo) / (nums.length - 1) / 2 : 0.5;
            lo -= half; hi += half;
        }
        var tol = (hi - lo) * 1e-9;
        return v >= lo - tol && v <= hi + tol;
    }

    function onHeatmap(note, data, layout) {
        if (!note || note.showarrow !== false || !Array.isArray(data)) return false;
        var xref = note.xref || 'x', yref = note.yref || 'y';
        if (!/^x\d*$/.test(xref) || !/^y\d*$/.test(yref)) return false;   // paper / domain
        function isLog(ref) {
            var axis = layout[ref.charAt(0) + 'axis' + ref.slice(1)];
            return !!(axis && axis.type === 'log');
        }
        return data.some(function (t) {
            if (!t || t.type !== 'heatmap' || (t.xaxis || 'x') !== xref || (t.yaxis || 'y') !== yref) return false;
            var z = Array.isArray(t.z) ? t.z : [];
            var cols = z.reduce(function (m, row) { return Math.max(m, Array.isArray(row) ? row.length : 0); }, 0);
            return inExtent(note.x, t.x, cols, isLog(xref)) && inExtent(note.y, t.y, z.length, isLog(yref));
        });
    }

    /** Base layout — deep-merged under caller overrides. `data` (optional) are the traces
     *  the layout is drawn with: text on a heatmap of theirs keeps the lab colour. */
    function houseLayout(overrides, data) {
        var text = cssVar('--plot-axis-text', cssVar('--pico-color', '#ccc'));
        var grid = cssVar('--plot-grid', 'rgba(128,128,128,0.18)');
        var base = {
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { family: cssVar('--pico-font-family', 'system-ui, sans-serif'),
                    size: 12, color: text },
            margin: { l: 56, r: 16, t: 28, b: 44 },
            colorway: palette(),
            xaxis: { gridcolor: grid, zerolinecolor: grid, automargin: true },
            yaxis: { gridcolor: grid, zerolinecolor: grid, automargin: true },
            hoverlabel: {
                bgcolor: cssVar('--plot-hover-bg', cssVar('--pico-card-background-color', '#222')),
                font: { color: cssVar('--plot-hover-text', text), size: 12 },
                bordercolor: grid,
            },
            legend: { orientation: 'h', y: -0.22, font: { size: 11 } },
            showlegend: false,
        };
        var out = deepMerge(base, overrides || {});
        // docs/301 F28: a secondary axis (one that overlays another, e.g. the
        // "Detuning [MHz]" scale on top of a spectroscopy) got no theme, so
        // Plotly drew its grid in its own near-white default -- bright
        // vertical lines that read as markers on the data -- with a second
        // zero line. The primary axis already draws the grid; a secondary one
        // only labels it.
        Object.keys(out).forEach(function (k) {
            var ax = out[k];
            if (!/^[xy]axis\d+$/.test(k) || !ax || typeof ax !== 'object' || !ax.overlaying) return;
            var dflt = { showgrid: false, zeroline: false,
                         gridcolor: grid, zerolinecolor: grid, automargin: true };
            // docs/301 F37: a top x axis's labels sit on the plot's top edge,
            // where its first one (at the left corner) ran into the y axis's
            // top label. Lift them clear; automargin makes the room.
            if (/^xaxis/.test(k) && ax.side === 'top') dflt.ticklabelstandoff = 8;
            out[k] = Object.assign(dflt, ax);
        });
        // v1.5.1 (D3 class G): a SUBPLOT axis (xaxis2..N beside the first panel) got no
        // theme either, so every panel after the first drew Plotly's near-white default
        // grid. Same dim grid as the first panel; a grid colour the caller (the lab
        // figure) set itself stays.
        Object.keys(out).forEach(function (k) {
            var ax = out[k];
            if (!/^[xy]axis\d+$/.test(k) || !ax || typeof ax !== 'object' || ax.overlaying) return;
            out[k] = Object.assign({ gridcolor: grid, zerolinecolor: grid }, ax);
        });
        if (isDark()) {
            out.font = artistStyle(out.font, text);
            Object.keys(out).forEach(function (key) {
                if (!/^[xy]axis\d*$/.test(key)) return;
                var axis = out[key];
                axis.tickfont = artistStyle(axis.tickfont, text);
                if (axis.title) axis.title = Object.assign({}, axis.title,
                    { font: artistStyle(axis.title.font, text) });
            });
            if (out.title) out.title = Object.assign({}, out.title, { font: artistStyle(out.title.font, text) });
            if (out.annotations) out.annotations = out.annotations.map(function (note) {
                if (onHeatmap(note, data, out)) return Object.assign({}, note);
                return Object.assign({}, note, { font: artistStyle(note.font, text) });
            });
            if (out.shapes) out.shapes = out.shapes.map(function (shape) {
                return Object.assign({}, shape, { line: artistStyle(shape.line, text) });
            });
        }
        return out;
    }

    function houseConfig(overrides) {
        return Object.assign({
            displaylogo: false,
            responsive: true,
            modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d',
                                     'hoverClosestCartesian', 'hoverCompareCartesian',
                                     'toggleSpikelines'],
            // NOTE: paper/plot backgrounds are transparent CSS-token colors,
            // so exported PNGs are transparent too; a solid export bg would
            // need a layout override at download time, not a config flag.
            toImageButtonOptions: { format: 'png', scale: 2 },
        }, overrides || {});
    }

    function deepMerge(base, over) {
        var out = Array.isArray(base) ? base.slice() : Object.assign({}, base);
        Object.keys(over).forEach(function (k) {
            if (over[k] && typeof over[k] === 'object' && !Array.isArray(over[k])
                && base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) {
                out[k] = deepMerge(base[k], over[k]);
            } else { out[k] = over[k]; }
        });
        return out;
    }

    /** SI-format a number for axis/hover text (1.234 GHz style). */
    function siFormat(v, unit) {
        if (v === null || v === undefined || !isFinite(v)) return '—';
        var a = Math.abs(v);
        var scales = [[1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''],
                      [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p']];
        // Unitless / already-scaled units: plain precision.
        if (!unit || unit === 'a.u.' || unit === 'dB' || unit === 'rad'
            || unit.indexOf('π') !== -1 || unit.indexOf('pi') !== -1) {
            return (a !== 0 && (a >= 1e5 || a < 1e-4))
                ? v.toExponential(4) + (unit ? ' ' + unit : '')
                : (+v.toPrecision(6)) + (unit ? ' ' + unit : '');
        }
        for (var i = 0; i < scales.length; i++) {
            if (a >= scales[i][0] || i === scales.length - 1) {
                var scaled = v / scales[i][0];
                if (a === 0) { scaled = 0; i = 3; }
                return (+scaled.toPrecision(5)) + ' ' + scales[i][1] + unit;
            }
        }
        return String(v);
    }

    /** Re-theme every rendered plot after a dark/light toggle. */
    function retheme() {
        if (!window.Plotly) return;
        var L = houseLayout({});
        // STRUCTURAL selection (docs/124 M-6/§4.3): '.js-plotly-plot' is a
        // class and was proven strippable — a chart that lost it silently
        // never rethemed. PlotHost.graphDivs finds graph divs by what they
        // ARE (fullLayout / .plot-container child / root itself); the bare
        // class stays only as the no-PlotHost fallback.
        var divs = (window.PlotHost && window.PlotHost.graphDivs)
            ? window.PlotHost.graphDivs(document)
            : document.querySelectorAll('.js-plotly-plot');
        Array.prototype.forEach.call(divs, function (el) {
            try {
                var base = {
                    paper_bgcolor: L.paper_bgcolor, plot_bgcolor: L.plot_bgcolor,
                    'font.color': L.font.color,
                    'hoverlabel.bgcolor': L.hoverlabel.bgcolor,
                    'hoverlabel.font.color': L.hoverlabel.font.color,
                };
                // v1.5.1 (D3 class G): EVERY axis of the plot follows the toggle, not only
                // the first panel's -- the ones the plot has (layout or fullLayout) plus
                // xaxis/yaxis; a grid colour the source layout set itself stays.
                var source = el._houseSourceLayout || {}, names = { xaxis: 1, yaxis: 1 };
                [el.layout, el._fullLayout].forEach(function (lay) {
                    Object.keys(lay || {}).forEach(function (k) { if (/^[xy]axis\d*$/.test(k)) names[k] = 1; });
                });
                Object.keys(names).forEach(function (k) {
                    var own = source[k] || {};
                    if (!own.gridcolor) base[k + '.gridcolor'] = L.xaxis.gridcolor;
                    if (!own.zerolinecolor) base[k + '.zerolinecolor'] = L.xaxis.zerolinecolor;
                });
                window.Plotly.relayout(el, base);
                if (el._houseSourceData && window.Plotly.restyle) {
                    var data = houseData(el._houseSourceData);
                    data.forEach(function (trace, index) {
                        var updates = {};
                        ['line', 'marker', 'textfont'].forEach(function (key) {
                            if (trace[key] && 'color' in trace[key]) updates[key + '.color'] = [trace[key].color];
                        });
                        if (trace.marker && trace.marker.line && 'color' in trace.marker.line) {
                            updates['marker.line.color'] = [trace.marker.line.color];
                        }
                        if (Object.keys(updates).length) window.Plotly.restyle(el, updates, [index]);
                    });
                }
                if (el._houseSourceLayout) {
                    var themed = houseLayout(el._houseSourceLayout, el._houseSourceData), updates = {};
                    Object.keys(themed).forEach(function (key) {
                        if (!/^[xy]axis\d*$/.test(key)) return;
                        var axis = themed[key];
                        if (axis.tickfont) updates[key + '.tickfont.color'] = axis.tickfont.color || L.font.color;
                        if (axis.title && axis.title.font) updates[key + '.title.font.color'] = axis.title.font.color || L.font.color;
                    });
                    if (themed.title && themed.title.font) updates['title.font.color'] = themed.title.font.color || L.font.color;
                    (themed.annotations || []).forEach(function (note, index) {
                        updates['annotations[' + index + '].font.color'] = (note.font || {}).color || L.font.color;
                    });
                    (themed.shapes || []).forEach(function (shape, index) {
                        if (shape.line && 'color' in shape.line) updates['shapes[' + index + '].line.color'] = shape.line.color;
                    });
                    if (Object.keys(updates).length) window.Plotly.relayout(el, updates);
                }
            } catch (e) { /* a torn-down plot mid-swap — ignore */ }
        });
    }

    /** How every SM chart axis writes its numbers.
     *
     * Plotly's DEFAULT exponentformat is 'B', so a 4.9 GHz qubit frequency
     * draws as "4.9B" -- US billions, on an axis whose only other label is the
     * bare metric name. The first fix for that set tickformat '~s', which cured
     * the frequencies and broke everything DIMENSIONLESS: measured in real
     * Chrome on a real chip, `gate_fidelity_avg` drew 991m..996m for
     * 0.991..0.996 and `readout_amplitude` read "150m". Milli-what? There is no
     * unit to prefix. And `~s` caps a label at 6 significant digits, so a
     * zoomed axis collapsed three ticks onto one string.
     *
     * 'SI' is the setting that was actually wanted, and it is not a tickformat
     * at all. Measured on the same charts, full range and zoomed tight:
     *
     *     setting              fidelity     f_01       zoomed ticks
     *     ~s                   991m    X    4.8G  ok   3 -> 1 distinct  X
     *     '' + SI              0.991   ok   4.8G  ok   3 -> 3 distinct  ok
     *     ~s + SI              991m    X    4.8G  ok   3 -> 1 distinct  X
     *
     * So: no per-axis decision, no magnitude gate, no prefix on a bare ratio,
     * G rather than B on a frequency, and a zoomed axis that still says which
     * tick is which. Spread into a layout's yaxis (or xaxis).
     */
    function axisNumberFormat() {
        return { tickformat: '', exponentformat: 'SI' };
    }

    window.PlotTheme = {
        houseLayout: houseLayout,
        houseData: houseData,
        axisNumberFormat: axisNumberFormat,
        houseConfig: houseConfig,
        siFormat: siFormat,
        palette: palette,
        retheme: retheme,
    };
})();
