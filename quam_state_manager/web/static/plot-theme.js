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

    /** Base layout — deep-merged under caller overrides. */
    function houseLayout(overrides) {
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
        return deepMerge(base, overrides || {});
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
        document.querySelectorAll('.js-plotly-plot').forEach(function (el) {
            try {
                window.Plotly.relayout(el, {
                    paper_bgcolor: L.paper_bgcolor, plot_bgcolor: L.plot_bgcolor,
                    'font.color': L.font.color,
                    'xaxis.gridcolor': L.xaxis.gridcolor,
                    'yaxis.gridcolor': L.yaxis.gridcolor,
                    'hoverlabel.bgcolor': L.hoverlabel.bgcolor,
                    'hoverlabel.font.color': L.hoverlabel.font.color,
                });
            } catch (e) { /* a torn-down plot mid-swap — ignore */ }
        });
    }

    window.PlotTheme = {
        houseLayout: houseLayout,
        houseConfig: houseConfig,
        siFormat: siFormat,
        palette: palette,
        retheme: retheme,
    };
})();
