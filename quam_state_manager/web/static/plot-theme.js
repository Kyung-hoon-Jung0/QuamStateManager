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

    /** The tick format for an axis whose largest magnitude is `maxAbs`.
     *
     * AN SI PREFIX IS A UNIT PREFIX (customer, 2026-09-10). Plotly's default
     * writes 4.9e9 as "4.9B" -- US billions -- so the Trends charts set
     * `~s` to get 4.9G instead. That fixed the frequencies and broke every
     * DIMENSIONLESS metric: measured in real Chrome on a real chip,
     * `gate_fidelity_avg` drew ticks reading 991m 992m ... 996m for values
     * 0.991..0.996, and `readout_amplitude` read "150m". Milli-what? There is
     * no unit to prefix, so the prefix says nothing and the reader has to
     * undo it.
     *
     * No single format serves both -- also measured, on those same charts:
     *
     *     format   fidelity     f_01         T1           amplitude
     *     ~s       991m    X    4.9G   ok    20u    ok    150m   X
     *     ''       0.991   ok   4.9B   X     20u    ok    0.15   ok
     *     .4~g     0.991   ok   4.9e+9       0.00002  X   0.15   ok
     *     .3~f     0.991   ok   4800000000 X 0 0 0    X   0.15   ok
     *
     * So the choice is per axis and it is about MAGNITUDE. The band is
     * siFormat's own, one function above: inside it a person reads the number
     * unaided, outside it the scaled form earns its keep. An empty tickformat
     * is Plotly's default rather than "plain decimal" -- it still writes 2k
     * for 2000, which is unambiguous, and 4.9B for 4.9e9, which is not; the
     * >= 1e5 arm is what keeps the B away.
     *
     * Pass the LARGEST magnitude, never the smallest: ticks are placed by the
     * axis range, so one tiny outlier must not drag the whole axis into
     * prefixes. `readout_amplitude` spans 0.0007..0.22 and its ticks are
     * 0.05..0.2 -- keyed on the smallest value those rendered as 50m..200m.
     */
    function axisTickFormat(maxAbs) {
        if (!(maxAbs > 0) || !isFinite(maxAbs)) return '';
        return (maxAbs < 1e5 && maxAbs >= 1e-4) ? '' : '~s';
    }

    window.PlotTheme = {
        houseLayout: houseLayout,
        axisTickFormat: axisTickFormat,
        houseConfig: houseConfig,
        siFormat: siFormat,
        palette: palette,
        retheme: retheme,
    };
})();
