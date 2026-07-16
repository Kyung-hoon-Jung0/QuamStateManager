/* Chip Status — the page's client module (Foundation B of the redesign).
 * Lifted byte-for-byte from the _wiring.html inline IIFE so the logic lives
 * in one real module instead of a 1700-line template script. Loaded ONCE in
 * <head>; _wiring.html calls ChipStatus.mount(...) (and .liveDetection())
 * on every HTMX render, exactly as the old inline IIFEs ran per render.
 * The 5 server-injected values (topology, wiring, thresholds, findings,
 * chip_view) are threaded in via `opts` since a static file isn't Jinja-
 * processed. */
window.ChipStatus = window.ChipStatus || {};

/* DensityController (Phase 1) — the first factored core. Owns the tile-size
   control: a 0.55–1.15 scale written to .topo-dashboard's --topo-density-scale
   (CSS derives the cell sizes; fonts are untouched), with S/M/L presets + a fine
   slider, persisted to localStorage. Scoped to the dashboard so it never leaks. */
window.ChipStatus.density = (function () {
    var KEY = 'quam_chip_density', MIN = 0.55, MAX = 1.15;
    function clamp(v) { return Math.max(MIN, Math.min(MAX, v)); }
    function load() {
        var s = NaN;
        try { s = parseFloat(localStorage.getItem(KEY)); } catch (e) {}
        return (isFinite(s) && s > 0) ? clamp(s) : 1;
    }
    function apply(s) {
        var d = document.querySelector('.topo-dashboard');
        if (!d) return;
        s = clamp(s);
        d.style.setProperty('--topo-density-scale', s);
        var sl = document.getElementById('topo-density-slider');
        if (sl && parseFloat(sl.value) !== s) sl.value = s;
        var preds = document.querySelectorAll('.density-preset');
        for (var i = 0; i < preds.length; i++) {
            preds[i].classList.toggle('active',
                Math.abs(parseFloat(preds[i].getAttribute('data-density')) - s) < 1e-6);
        }
    }
    function set(s) {
        s = clamp(s);
        try { localStorage.setItem(KEY, String(s)); } catch (e) {}
        apply(s);
    }
    function init() {
        apply(load());
        var sl = document.getElementById('topo-density-slider');
        if (sl) sl.addEventListener('input', function () { set(parseFloat(sl.value)); });
        var preds = document.querySelectorAll('.density-preset');
        for (var i = 0; i < preds.length; i++) {
            (function (b) {
                b.addEventListener('click', function () { set(parseFloat(b.getAttribute('data-density'))); });
            })(preds[i]);
        }
    }
    return { init: init, set: set, apply: apply };
})();

/* LayoutController (Phase 1) — the two-stable-renderings rule. A single debounced
   ResizeObserver on #table-pane toggles ONE class .is-narrow on .topo-dashboard at
   a fixed threshold; CSS does the rest (bar chart stacks below the grid). No JS
   rebuild, no per-pixel work — exactly one Plotly.Plots.resize after the class
   settles. So dragging the split bar can't break or re-render the panels mid-drag. */
window.ChipStatus.layout = (function () {
    var NARROW = 900;            // px: below this, stack bar-below-grid
    var ro = null, lastNarrow = null, debTimer = null;
    function paneWidth() {
        var p = document.getElementById('table-pane');
        return p ? p.clientWidth : window.innerWidth;
    }
    function settle() {
        var narrow = paneWidth() < NARROW;
        if (narrow === lastNarrow) return;     // only act on a real threshold crossing
        lastNarrow = narrow;
        var d = document.querySelector('.topo-dashboard');
        if (d) d.classList.toggle('is-narrow', narrow);
        if (window.Plotly) {                   // ONE resize over already-built charts
            document.querySelectorAll(
                '.topo-metric-bar-chart .js-plotly-plot, .topo-hist-chart .js-plotly-plot'
            ).forEach(function (el) { try { Plotly.Plots.resize(el); } catch (e) {} });
        }
    }
    function onResize() { clearTimeout(debTimer); debTimer = setTimeout(settle, 150); }
    function init() {
        lastNarrow = null;
        settle();                              // apply current state immediately
        var p = document.getElementById('table-pane');
        if (window.ResizeObserver && p) {
            if (ro) { try { ro.disconnect(); } catch (e) {} }
            ro = new ResizeObserver(onResize);
            ro.observe(p);
        }
        window.addEventListener('resize', onResize);   // stable ref → no dup listeners
    }
    return { init: init };
})();

/* liveDiff (Phase 4) — tie Chip Status to the Explorer before/after. Fetches
   /state/live-diff (working copy vs Qualibrate's live files), maps each changed
   dot-path to its qubit/pair, and marks those cards/cells so you can SEE which
   qubits a fit touched; the live banner's "Review changes" opens the full
   before/after (openReview). decorate() re-applies the cached diff to cells that
   build lazily on scroll. */
window.ChipStatus.liveDiff = (function () {
    var byEntity = {};
    function _entityOf(dotPath) {
        var p = (dotPath || '').split('.');
        if ((p[0] === 'qubits' || p[0] === 'qubit_pairs') && p[1]) return p[1];
        if (p[0] === 'wiring' && (p[1] === 'qubits' || p[1] === 'qubit_pairs') && p[2]) return p[2];
        return null;
    }
    function decorate() {
        var prev = document.querySelectorAll('.topo-changed');
        for (var i = 0; i < prev.length; i++) prev[i].classList.remove('topo-changed');
        Object.keys(byEntity).forEach(function (id) {
            var n = byEntity[id];
            var _e = (window.CSS && CSS.escape) ? CSS.escape(id) : id;
            document.querySelectorAll('[data-qubit="' + _e + '"], [data-pair="' + _e + '"]').forEach(function (el) {
                var target = el.closest('.topo-node-card') || el;
                target.classList.add('topo-changed');
                var base = target.getAttribute('title') || '';
                if (base.indexOf('changed vs live') === -1) {
                    target.setAttribute('title', (base ? base + '\n' : '')
                        + n + ' field(s) changed vs live — "Review changes" shows before/after');
                }
            });
        });
    }
    function refresh() {
        fetch('/state/live-diff', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                byEntity = {};
                if (d && d.ok && d.entries) {
                    d.entries.forEach(function (e) {
                        var id = _entityOf(e.dot_path);
                        if (id) byEntity[id] = (byEntity[id] || 0) + 1;
                    });
                }
                decorate();
            })
            .catch(function () {});
    }
    return { refresh: refresh, decorate: decorate };
})();

window.ChipStatus.mount = function (opts) {
    opts = opts || {};
    var topo = opts.topo || {nodes: [], edges: []};
    var rawWiring = opts.rawWiring || {};
    var _defaultThresholds = opts.defaultThresholds || {};
    var diagFindings = opts.diagFindings || [];
    var _serverChipView = opts.chipView || '';
    var _historyCount = opts.historyCount || 0;   // gates the lazy sparkline fetch

    // ── Metric glossary (single source: chip_health.METRIC_META) ─────────────
    // {key → {label, abbr, direction, blurb}}. The tooltips, the good-direction
    // arrows AND the threshold-editor row labels all read this — none re-derive,
    // so the arrow can never disagree with the verdict colour.
    var META = opts.metricMeta || {};
    function _meta(k) { return META[k] || {label: k, abbr: k, direction: 'neutral', blurb: ''}; }
    function metricLabel(k) { return _meta(k).label || k; }
    function metricAbbr(k) { return _meta(k).abbr || _meta(k).label || k; }
    function metricBlurb(k) { return _meta(k).blurb || ''; }
    // '↑' higher-is-better, '↓' lower-is-better, '' neutral/unknown — a missing
    // arrow never implies a spec verdict on an uncoloured (informational) metric.
    function arrow(k) { var d = _meta(k).direction; return d === 'higher' ? '↑' : (d === 'lower' ? '↓' : ''); }
    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    // <span class=metric-label data-metric=k title=BLURB>TEXT <span class=metric-dir>↑</span></span>
    // useAbbr → terse card label; else the long label. Direction arrow appended
    // (empty for neutral). Both text + blurb are escaped.
    function labelHtml(k, useAbbr, overrideText) {
        var txt = overrideText != null ? overrideText : (useAbbr ? metricAbbr(k) : metricLabel(k));
        var ar = arrow(k);
        var blurb = metricBlurb(k);
        return '<span class="metric-label" data-metric="' + _esc(k) + '"' +
               (blurb ? ' title="' + _esc(blurb) + '"' : '') + '>' + _esc(txt) +
               (ar ? ' <span class="metric-dir">' + ar + '</span>' : '') + '</span>';
    }

    window._rawWiring = rawWiring;
    window.ChipStatus.density.init();   // tile-size control (Phase 1)
    window.ChipStatus.layout.init();    // full/narrow two-rendering breakpoint (Phase 1)
    window.ChipStatus.liveDiff.refresh(); // mark qubits/pairs changed vs live (Phase 4)

    // Health layer (Chip Status overhaul): structural findings + spec thresholds.
    // The client owns the live verdict/colour; the in-UI editor mutates
    // `thresholds`, persists to localStorage, and re-runs buildHealthSummary().
    var THRESH_KEY = 'quam_chip_thresholds';
    function _loadThresholds(defaults) {
        var t = JSON.parse(JSON.stringify(defaults || {}));
        try {
            var saved = JSON.parse(localStorage.getItem(THRESH_KEY) || '{}');
            Object.keys(saved).forEach(function(k) {
                if (t[k]) {
                    if (typeof saved[k].warn === 'number') t[k].warn = saved[k].warn;
                    if (typeof saved[k].fail === 'number') t[k].fail = saved[k].fail;
                }
            });
        } catch (e) {}
        return t;
    }
    function _saveThresholds(t) {
        var out = {};
        Object.keys(t).forEach(function(k) { out[k] = { warn: t[k].warn, fail: t[k].fail }; });
        try { localStorage.setItem(THRESH_KEY, JSON.stringify(out)); } catch (e) {}
    }
    var thresholds = _loadThresholds(_defaultThresholds);

    if (!topo.nodes || topo.nodes.length === 0) return;

    var tCfg = UI_CONFIG.plotly.topology;
    var dCfg = tCfg.dashboard;
    var chainColors = tCfg.chainColors;

    // ══════════════════════════════════════════════════════════════════
    // Utility functions
    // ══════════════════════════════════════════════════════════════════

    function fmt(v, unit) {
        if (v === null || v === undefined) return '\u2014';
        if (unit === 'GHz') return (v / 1e9).toFixed(4) + ' GHz';
        if (unit === 'MHz') return (v / 1e6).toFixed(1) + ' MHz';
        if (unit === 'us') return v != null ? (v * 1e6).toFixed(1) + ' \u00b5s' : '\u2014';
        if (unit === 'ns') return v != null ? v + ' ns' : '\u2014';
        return String(v);
    }
    function fmtNum(v, d) { return (v != null && typeof v === 'number') ? v.toFixed(d) : '\u2014'; }
    function fmtPct(v, d) { return (v != null && typeof v === 'number') ? (v * 100).toFixed(d) : '\u2014'; }

    // Physical-gated metric read: the MetricRecord's quarantined value — None for
    // an unphysical (e.g. −473µs T2) or unresolved fit. EVERY display surface must
    // read THIS, not the raw scalar n[key], so a failed fit never colours red,
    // pollutes an average, or stretches the colour range. Falls back to the raw
    // scalar only when there's no record (older payloads).
    function _mv(entity, key) {
        var r = entity.metrics && entity.metrics[key];
        return r ? r.value : entity[key];
    }
    // A measured-but-unphysical value (raw number present, gated value None, not a
    // dangling pointer) — a "likely failed fit", shown distinctly, not as a bad qubit.
    function _badFit(entity, key) {
        var r = entity.metrics && entity.metrics[key];
        return !!(r && r.value == null && !r.unresolved
                  && typeof r.raw === 'number' && isFinite(r.raw));
    }
    // One qubit-card / popup property row with the physical gate applied: an
    // unphysical fit shows its raw value struck-through ("bad fit"), never a heat
    // colour or a data-heat-v that the spec painter would turn red. nullLabel is
    // what a genuinely-missing value renders as ('—' on cards, 'None' in popups).
    function _propRowHtml(n, p, nullLabel) {
        if (_badFit(n, p.key)) {
            return '<div class="topo-prop-row" data-prop="' + p.key + '">'
                + '<span class="topo-prop-label">' + labelHtml(p.key, true) + '</span>'
                + '<span class="topo-prop-value topo-prop-bad" title="unphysical (likely a failed fit) — excluded from stats &amp; colour">'
                + p.fmtFn(n.metrics[p.key].raw) + '</span></div>';
        }
        var v = _mv(n, p.key);
        var c = propBgColor(p, v);
        var tAttr = c.t != null ? ' data-heat-t="' + c.t.toFixed(6) + '"' : '';
        var vAttr = (typeof v === 'number') ? ' data-heat-v="' + v + '"' : '';
        return '<div class="topo-prop-row" data-prop="' + p.key + '">'
            + '<span class="topo-prop-label">' + labelHtml(p.key, true) + '</span>'
            + '<span class="topo-prop-value"' + tAttr + vAttr + ' style="background:' + c.bg + ';color:' + c.fg + '">'
            + (v != null ? p.fmtFn(v) : nullLabel) + '</span></div>';
    }

    function computeAggregates(arr) {
        var vals = arr.filter(function(v) { return v != null && typeof v === 'number'; });
        if (vals.length === 0) return {avg: null, median: null, min: null, max: null, count: 0, values: []};
        var sum = 0, mn = vals[0], mx = vals[0];
        for (var i = 0; i < vals.length; i++) { sum += vals[i]; if (vals[i] < mn) mn = vals[i]; if (vals[i] > mx) mx = vals[i]; }
        var sorted = vals.slice().sort(function(a, b) { return a - b; });
        var mid = Math.floor(sorted.length / 2);
        var median = sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
        return {avg: sum / vals.length, median: median, min: mn, max: mx, count: vals.length, values: vals};
    }

    function cardColor(value, thresholds) {
        if (value == null) return '#999';
        for (var i = 0; i < thresholds.length; i++) { if (value >= thresholds[i][0]) return thresholds[i][1]; }
        return thresholds[thresholds.length - 1][1];
    }

    // Robust outlier detector — modified z-score off the MEDIAN and MAD (median
    // absolute deviation), NOT mean/σ, so one bad qubit can't hide the rest by
    // inflating the spread. Returns a scorer or null when there aren't enough
    // points / there's no spread (an honest "can't say", never a false flag).
    var _OUTLIER_K = 3.5;   // standard modified-z cutoff
    function _median(sorted) {
        var n = sorted.length; if (!n) return null;
        var m = Math.floor(n / 2);
        return n % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2;
    }
    function outlierScorer(arr) {
        var clean = arr.filter(function(v) { return typeof v === 'number' && isFinite(v); });
        if (clean.length < 5) return null;   // too few points for a robust call
        var sorted = clean.slice().sort(function(a, b) { return a - b; });
        var med = _median(sorted);
        var devs = clean.map(function(v) { return Math.abs(v - med); }).sort(function(a, b) { return a - b; });
        var mad = _median(devs);
        // MAD==0 when ≥50% of points share one value (e.g. many qubits at an
        // identical default amplitude). We return null = "no call" rather than
        // flag the minority — a known conservative miss (favours no-false-positive
        // over catching a deviation from a tight mode); fine for the headline
        // fidelity/coherence metrics this guards.
        if (!mad) return null;
        return {
            median: med,
            score: function(v) {
                if (typeof v !== 'number' || !isFinite(v)) return null;
                return Math.abs(v - med) / (1.4826 * mad);
            },
            isOutlier: function(v) { var s = this.score(v); return s != null && s >= _OUTLIER_K; }
        };
    }

    function hexToRgb(hex) {
        hex = hex.replace('#', '');
        return [parseInt(hex.substring(0,2),16), parseInt(hex.substring(2,4),16), parseInt(hex.substring(4,6),16)];
    }

    function interpolateColor(t, stops) {
        t = Math.max(0, Math.min(1, t));
        var idx = t * (stops.length - 1);
        var lo = Math.floor(idx), hi = Math.min(lo + 1, stops.length - 1);
        var f = idx - lo;
        var c1 = hexToRgb(stops[lo]), c2 = hexToRgb(stops[hi]);
        return 'rgb(' + Math.round(c1[0]+(c2[0]-c1[0])*f) + ',' + Math.round(c1[1]+(c2[1]-c1[1])*f) + ',' + Math.round(c1[2]+(c2[2]-c1[2])*f) + ')';
    }

    function textColorForBg(rgbStr) {
        var m = rgbStr.match(/\d+/g);
        if (!m) return '#222';
        return (0.299*parseInt(m[0]) + 0.587*parseInt(m[1]) + 0.114*parseInt(m[2])) < 160 ? '#fff' : '#222';
    }

    // ── Color palette definitions ────────────────────────────────────
    var PALETTES = {
        'GnBu':     {label:'GnBu (default)',stops:['#e0f3db','#a8ddb5','#7bccc4','#43a2ca','#0868ac']},
        'Viridis':  {label:'Viridis',       stops:['#440154','#31688e','#35b779','#90d743','#fde725']},
        'Plasma':   {label:'Plasma',        stops:['#0d0887','#7e03a8','#cc4778','#f89540','#f0f921']},
        'Inferno':  {label:'Inferno',       stops:['#000004','#57106e','#bc3754','#f98c0a','#fcffa4']},
        'Magma':    {label:'Magma',         stops:['#000004','#51127c','#b73779','#fb8761','#fcfdbf']},
        'Cividis':  {label:'Cividis',       stops:['#002051','#3d4f7c','#7b7b78','#b8a94f','#fdea45']},
        'RdYlGn':   {label:'Red-Yellow-Green',stops:['#d73027','#fc8d59','#fee08b','#91cf60','#1a9850']},
        'YlOrRd':   {label:'Yellow-Orange-Red',stops:['#ffffb2','#fecc5c','#fd8d3c','#f03b20','#bd0026']},
        'Blues':    {label:'Blues',          stops:['#eff3ff','#bdd7e7','#6baed6','#3182bd','#08519c']},
        'Citrus':   {label:'Citrus',        stops:['#f7fcb9','#d9f0a3','#addd8e','#78c679','#31a354']},
        'DkGreen':  {label:'Dark \u2192 Bright Green',stops:['#00230e','#0a5226','#1a8a42','#3cc264','#66ff8c']},
    };

    // Restore saved palettes or use defaults
    var _savedPalette = null;
    try { _savedPalette = localStorage.getItem('quam_heatmap_palette'); } catch(e) {}
    if (_savedPalette && PALETTES[_savedPalette]) {
        dCfg.colorScale = PALETTES[_savedPalette].stops;
    }
    var _currentPaletteName = (_savedPalette && PALETTES[_savedPalette]) ? _savedPalette : 'GnBu';

    var _savedBarPalette = null;
    try { _savedBarPalette = localStorage.getItem('quam_bar_palette'); } catch(e) {}
    var _currentBarPaletteName = (_savedBarPalette && PALETTES[_savedBarPalette]) ? _savedBarPalette : 'GnBu';
    var _barColorScale = PALETTES[_currentBarPaletteName].stops;

    // ── Card property definitions ──────────────────────────────────
    // Just {key, fmtFn}. Label + good-direction now come from META (the server
    // glossary) via metricAbbr(key)/arrow(key) — fmtFn stays here because it's a
    // JS formatting function, not metadata. (These used to also hardcode a label
    // + dir; that was a parallel metadata map and was removed.)
    // Primary: always visible on qubit cards
    var PRIMARY_CARD_PROPS = [
        {key:'T1',                fmtFn:function(v){return fmt(v,'us');}},
        {key:'T2ramsey',          fmtFn:function(v){return fmt(v,'us');}},
        {key:'T2echo',            fmtFn:function(v){return fmt(v,'us');}},
        {key:'gate_fidelity_avg', fmtFn:function(v){return fmtPct(v,2);}},
        {key:'ro_fidelity_g',     fmtFn:function(v){return fmtPct(v,2);}},
        {key:'ro_fidelity_e',     fmtFn:function(v){return fmtPct(v,2);}},
    ];
    // Secondary: collapsible
    var SECONDARY_CARD_PROPS = [
        {key:'f_01',              fmtFn:function(v){return fmt(v,'GHz');}},
        {key:'f_12',              fmtFn:function(v){return fmt(v,'GHz');}},
        {key:'readout_frequency', fmtFn:function(v){return fmt(v,'GHz');}},
        {key:'anharmonicity',     fmtFn:function(v){return fmt(v,'MHz');}},
        {key:'chi',               fmtFn:function(v){return fmt(v,'MHz');}},
        {key:'gate_fidelity_x180',fmtFn:function(v){return fmtPct(v,2);}},
        {key:'gate_fidelity_x90', fmtFn:function(v){return fmtPct(v,2);}},
        {key:'assignment_fidelity',fmtFn:function(v){return fmtPct(v,2);}},
        {key:'x180_amplitude',    fmtFn:function(v){return fmtNum(v,4);}},
        {key:'x180_length',       fmtFn:function(v){return fmt(v,'ns');}},
        {key:'x180_alpha',        fmtFn:function(v){return fmtNum(v,4);}},
        {key:'x90_amplitude',     fmtFn:function(v){return fmtNum(v,4);}},
        {key:'saturation_amplitude',fmtFn:function(v){return fmtNum(v,4);}},
        {key:'readout_amplitude', fmtFn:function(v){return fmtNum(v,4);}},
        {key:'readout_length',    fmtFn:function(v){return fmt(v,'ns');}},
        {key:'readout_threshold', fmtFn:function(v){return fmtNum(v,4);}},
    ];

    // Combined (all) — used by metric panels and heatmap
    var ALL_CARD_PROPS = PRIMARY_CARD_PROPS.concat(SECONDARY_CARD_PROPS);

    // Filter each group to properties with at least 1 non-null value
    var PRIMARY_PROPS = PRIMARY_CARD_PROPS.filter(function(p) {
        return topo.nodes.some(function(n) { return n[p.key] != null; });
    });
    var SECONDARY_PROPS = SECONDARY_CARD_PROPS.filter(function(p) {
        return topo.nodes.some(function(n) { return n[p.key] != null; });
    });
    var CARD_PROPS = PRIMARY_PROPS.concat(SECONDARY_PROPS);

    // Pre-compute aggregates per property
    var propAggs = {};
    CARD_PROPS.forEach(function(p) {
        propAggs[p.key] = computeAggregates(topo.nodes.map(function(n) { return _mv(n, p.key); }));
    });

    // (Highlight dropdown removed — all metrics shown inline on cards)

    // ── Heatmap coloring helper ──────────────────────────────────────
    function propBgColor(prop, value) {
        var agg = propAggs[prop.key];
        if (value == null || agg.count < 2) return {bg: dCfg.nullCellColor, fg: '#666', t: null};
        var t = (value - agg.min) / (agg.max - agg.min || 1);
        var stops = dCfg.colorScale;
        var bg = interpolateColor(t, stops);
        return {bg: bg, fg: textColorForBg(bg), t: t};
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 1: Summary Stat Cards
    // ══════════════════════════════════════════════════════════════════

    // Overview: a concise but rich headline. Median is the big number; avg +
    // min-max range + count form the sub-line. Built eagerly at init (cheap, no
    // Plotly). An absent metric renders an empty tile rather than erroring.
    function buildOverviewTiles() {
        var container = document.getElementById('topo-overview-tiles');
        if (!container) return;

        // Per-pair best value for a 2Q metric, scanning each edge's gate_fidelities.
        // value is set server-side; average_gate_fidelity is the fallback.
        function collect2Q(match) {
            var out = [];
            topo.edges.forEach(function(e) {
                if (!e.gate_fidelities) return;
                var best = null;
                e.gate_fidelities.forEach(function(gf) {
                    if (!match(gf.metric)) return;
                    var v = typeof gf.value === 'number' ? gf.value
                          : typeof gf.average_gate_fidelity === 'number' ? gf.average_gate_fidelity : null;
                    if (v != null && (best == null || v > best)) best = v;
                });
                if (best != null) out.push(best);
            });
            return out;
        }
        function nodeAgg(key) { return computeAggregates(topo.nodes.map(function(n) { return _mv(n, key); })); }
        function pct(v) { return fmtPct(v, 2) + '%'; }
        function us(v) { return fmt(v, 'us'); }

        // metricKey (optional) → the title gets the META good-direction arrow +
        // blurb tooltip; composite tiles (Chip Size, 2Q RB, CZ Coverage) pass none.
        function metricTile(title, agg, fmtFn, stops, metricKey) {
            if (!agg || agg.count === 0) {
                return {title: title, metricKey: metricKey, value: '—', sub: 'no data', muted: true};
            }
            return {
                title: title,
                metricKey: metricKey,
                value: fmtFn(agg.median),
                sub: 'avg ' + fmtFn(agg.avg) + '  ·  ' + fmtFn(agg.min) + '–' + fmtFn(agg.max) + '  ·  (' + agg.count + ')',
                color: cardColor(agg.median, stops)
            };
        }

        var fidStops = [[0.99,'#2ca02c'],[0.95,'#ff7f0e'],[0,'#d62728']];
        var roStops  = [[0.97,'#2ca02c'],[0.90,'#ff7f0e'],[0,'#d62728']];
        var tStops   = [[30e-6,'#2ca02c'],[10e-6,'#ff7f0e'],[0,'#d62728']];
        var czCount = topo.edges.filter(function(e) { return e.has_cz; }).length;
        var czCoverage = topo.edges.length > 0 ? czCount / topo.edges.length : 0;
        // Gate-neutral vocabulary: "CZ" on flux chips, "CR" on cross-resonance
        // chips, "2Q" on mixed (server-derived; metric KEYS stay cz_fidelity).
        var gateVocab = (topo.summary && topo.summary.gate_vocab) || 'CZ';

        var tiles = [
            {title: 'Chip Size', value: topo.nodes.length + ' qubits, ' + topo.edges.length + ' pairs', color: '#4e79a7'},
            metricTile('1Q Gate Fidelity', nodeAgg('gate_fidelity_avg'), pct, fidStops, 'gate_fidelity_avg'),
            metricTile('Readout Fidelity', nodeAgg('assignment_fidelity'), pct, roStops, 'assignment_fidelity'),
            metricTile('2Q RB (Standard)', computeAggregates(collect2Q(function(m) { return m === 'StandardRB'; })), pct, fidStops),
            metricTile('2Q RB (Interleaved)', computeAggregates(collect2Q(function(m) { return m === 'InterleavedRB' || m === 'IRB'; })), pct, fidStops),
            metricTile('2Q Bell', computeAggregates(topo.edges.map(function(e) { return e.cz_fidelity; })), pct, [[0.95,'#2ca02c'],[0.85,'#ff7f0e'],[0,'#d62728']], 'cz_fidelity'),
            metricTile('T1', nodeAgg('T1'), us, tStops, 'T1'),
            metricTile('T2 echo', nodeAgg('T2echo'), us, tStops, 'T2echo'),
            metricTile('T2 Ramsey', nodeAgg('T2ramsey'), us, tStops, 'T2ramsey'),
            {title: gateVocab + ' Coverage', value: czCount + '/' + topo.edges.length + ' (' + (czCoverage * 100).toFixed(0) + '%)',
             sub: czCount + ' pairs with ' + gateVocab + ' gate', color: cardColor(czCoverage, [[0.9,'#2ca02c'],[0.5,'#ff7f0e'],[0,'#d62728']])}
        ];

        var html = '';
        tiles.forEach(function(c) {
            var border = c.muted ? 'var(--pico-muted-border-color)' : (c.color || 'var(--pico-muted-border-color)');
            var titleHtml = c.metricKey ? labelHtml(c.metricKey, false, c.title) : _esc(c.title);
            html += '<div class="topo-card' + (c.muted ? ' topo-card-empty' : '') + '" style="border-top-color:' + border + '">'
                  + '<div class="topo-card-title">' + titleHtml + '</div>'
                  + '<div class="topo-card-value">' + c.value + '</div>'
                  + (c.sub ? '<div class="topo-card-sub">' + c.sub + '</div>' : '')
                  + '</div>';
        });
        container.innerHTML = html;
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 2: HTML/SVG Topology with always-visible property cards
    // ══════════════════════════════════════════════════════════════════

    var idToIdx = {};

    (function buildTopology() {
        var wrap = document.getElementById('topo-html-wrap');
        if (!wrap) return;

        // ── Parse grid positions ─────────────────────────────────────
        var positions = topo.nodes.map(function(n, i) {
            var parts = (n.grid_location || '').split(',');
            idToIdx[n.id] = i;
            return {
                col: parts.length === 2 ? parseFloat(parts[0]) : (i % 4),
                row: parts.length === 2 ? parseFloat(parts[1]) : Math.floor(i / 4)
            };
        });

        var minCol = Infinity, maxCol = -Infinity, minRow = Infinity, maxRow = -Infinity;
        positions.forEach(function(p) {
            if (p.col < minCol) minCol = p.col;
            if (p.col > maxCol) maxCol = p.col;
            if (p.row < minRow) minRow = p.row;
            if (p.row > maxRow) maxRow = p.row;
        });

        // ── Layout constants (from UI_CONFIG.plotly.topology.layout) ─
        var L = UI_CONFIG.plotly.topology.layout;
        var CARD_W     = L.cardWidth;
        var ROW_H      = L.rowHeight;
        var HDR_H      = L.headerHeight;
        var BODY_PAD   = L.bodyPadding;
        var MORE_ROW_H = L.moreRowHeight;
        // Visible card height = primary props + "... more" button only
        var cardH = HDR_H + PRIMARY_PROPS.length * ROW_H + MORE_ROW_H + BODY_PAD;
        var SPACING_X = CARD_W + L.gapX;
        var SPACING_Y = cardH + L.gapY;
        var PAD = L.padding;

        // Convert grid coords to pixel positions
        // Flip row axis: QUAM convention has row 0 at the bottom of the chip,
        // but screen y=0 is at the top, so invert.
        positions.forEach(function(p) {
            p.x = (p.col - minCol) * SPACING_X + PAD;
            p.y = (maxRow - p.row) * SPACING_Y + PAD;
        });

        var containerW = (maxCol - minCol) * SPACING_X + CARD_W + PAD * 2;
        var containerH = (maxRow - minRow) * SPACING_Y + cardH + PAD * 2;

        // ── Build SVG edges ──────────────────────────────────────────
        // Directed (CR) edges: each direction is its own calibration target —
        // offset the two anti-parallel lines perpendicular to the run so they
        // never overpaint, add an arrowhead at the target end, and dim pairs
        // outside active_qubit_pair_names.
        var svgLines = '';
        topo.edges.forEach(function(e) {
            var si = idToIdx[e.source], ti = idToIdx[e.target];
            if (si === undefined || ti === undefined) return;
            var x1 = positions[si].x + CARD_W / 2;
            var y1 = positions[si].y + cardH / 2;
            var x2 = positions[ti].x + CARD_W / 2;
            var y2 = positions[ti].y + cardH / 2;

            var color = tCfg.edgeFidelityNone;
            var width = 2;
            if (e.has_cz && e.cz_fidelity != null) {
                color = e.cz_fidelity >= 0.95 ? tCfg.edgeFidelityGood : (e.cz_fidelity >= 0.85 ? tCfg.edgeFidelityWarn : tCfg.edgeFidelityBad);
                width = 3;
            }
            var opacity = (e.active === false) ? 0.35 : 1;
            var arrow = '';
            if (e.directed) {
                var dx = x2 - x1, dy = y2 - y1;
                var len = Math.sqrt(dx * dx + dy * dy) || 1;
                var ux = dx / len, uy = dy / len;
                // perpendicular offset — travel-direction-relative, so the
                // reverse pair lands on the opposite side automatically
                var off = 4;
                x1 += -uy * off; y1 += ux * off;
                x2 += -uy * off; y2 += ux * off;
                // arrowhead chevron 12px before the target center
                var ax = x2 - ux * 28, ay = y2 - uy * 28;
                var s = 6;
                arrow = '<polygon points="'
                    + (ax + ux * s) + ',' + (ay + uy * s) + ' '
                    + (ax - ux * s - uy * s) + ',' + (ay - uy * s + ux * s) + ' '
                    + (ax - ux * s + uy * s) + ',' + (ay - uy * s - ux * s)
                    + '" fill="' + color + '" opacity="' + opacity + '"/>';
            }
            svgLines += '<line x1="'+x1+'" y1="'+y1+'" x2="'+x2+'" y2="'+y2+'" stroke="'+color+'" stroke-width="'+width+'" stroke-linecap="round" opacity="'+opacity+'"/>' + arrow;
        });

        // ── Build edge labels ────────────────────────────────────────
        var edgeLabelsHtml = '';
        topo.edges.forEach(function(e) {
            var si = idToIdx[e.source], ti = idToIdx[e.target];
            if (si === undefined || ti === undefined) return;
            // Place label at geometric midpoint between the two connected cards.
            // For horizontal edges this centers in the gapX between the two cards.
            // For vertical edges this centers in the gapY between the two rows.
            // CSS transform: translate(-50%,-50%) handles visual centering.
            var mx = (positions[si].x + positions[ti].x) / 2 + CARD_W / 2;
            var my = (positions[si].y + positions[ti].y) / 2 + cardH / 2;
            if (e.directed) {
                // mirror the line's perpendicular offset (larger, so the two
                // direction labels of one physical edge don't overlap)
                var ddx = positions[ti].x - positions[si].x;
                var ddy = positions[ti].y - positions[si].y;
                var dlen = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
                mx += -(ddy / dlen) * 14;
                my += (ddx / dlen) * 14;
            }

            var color = tCfg.edgeFidelityNone;
            if (e.has_cz && e.cz_fidelity != null) {
                color = e.cz_fidelity >= 0.95 ? tCfg.edgeFidelityGood : (e.cz_fidelity >= 0.85 ? tCfg.edgeFidelityWarn : tCfg.edgeFidelityBad);
            }
            var label = _esc(e.pair_id);
            if (e.directed) label = _esc(e.source) + '→' + _esc(e.target);
            if (e.cz_fidelity != null) label += ' (' + (e.cz_fidelity * 100).toFixed(1) + '%)';
            if (e.best_gate) label += ' ' + _esc(e.best_gate.replace(/^cz_/, ''));
            if (e.active === false) label += ' · off';

            edgeLabelsHtml += '<div class="topo-edge-label" data-pair="' + _esc(e.pair_id) + '" '
                + 'style="left:' + mx + 'px;top:' + my + 'px;border-color:' + color + ';color:' + color
                + (e.active === false ? ';opacity:.45' : '') + '">'
                + label + '</div>';
        });

        // ── Build qubit cards ────────────────────────────────────────
        var cardsHtml = '';
        topo.nodes.forEach(function(n, i) {
            var chain = chainColors[n.chain] || tCfg.chainFallback;
            var left = positions[i].x;
            var top = positions[i].y;

            cardsHtml += '<div class="topo-node-card" data-qubit="' + _esc(n.id) + '" '
                + 'style="left:' + left + 'px;top:' + top + 'px;width:' + CARD_W + 'px">';
            cardsHtml += '<div class="topo-node-header" style="background:' + chain + '">' + _esc(n.id) + '</div>';
            cardsHtml += '<div class="topo-node-body">';

            // Primary props (always visible)
            PRIMARY_PROPS.forEach(function(p) {
                cardsHtml += _propRowHtml(n, p, '\u2014');
            });

            // "... more" button (opens popup overlay)
            if (SECONDARY_PROPS.length > 0) {
                cardsHtml += '<div class="topo-card-more-btn" data-qubit-more="' + n.id + '">'
                    + '\u2026 more <span style="font-size:1.1em">\u203A</span></div>';
            }

            cardsHtml += '</div></div>';
        });

        // ── Assemble ─────────────────────────────────────────────────
        // Build inner content at natural size, then auto-scale to fit
        var innerHtml = '<div class="topo-inner" style="width:' + containerW + 'px;height:' + containerH + 'px;position:relative">'
            + '<svg class="topo-edges-svg" width="' + containerW + '" height="' + containerH + '">'
            + svgLines + '</svg>' + edgeLabelsHtml + cardsHtml + '</div>';
        wrap.innerHTML = innerHtml;

        // Auto-fit: scale inner content to fill available width without scrollbar
        if (L.autoFit !== false) {
            var availW = wrap.parentElement ? wrap.parentElement.clientWidth - 2 : wrap.clientWidth;
            if (availW > 0 && containerW > availW) {
                var scale = availW / containerW;
                var inner = wrap.querySelector('.topo-inner');
                inner.style.transformOrigin = 'top left';
                inner.style.transform = 'scale(' + scale + ')';
                wrap.style.width = availW + 'px';
                wrap.style.height = (containerH * scale) + 'px';
                wrap.style.overflow = 'hidden';
            } else {
                wrap.style.width = containerW + 'px';
                wrap.style.height = containerH + 'px';
                wrap.style.overflow = 'hidden';
            }
        } else {
            wrap.style.width = containerW + 'px';
            wrap.style.minHeight = containerH + 'px';
        }

        // ── Popup management ─────────────────────────────────────────
        var activePopup = null;
        var topoInner = wrap.querySelector('.topo-inner');
        function closePopup() {
            if (activePopup) { activePopup.remove(); activePopup = null; }
        }
        function positionPopup(popup, anchorEl) {
            // Append to <body> as position:fixed so the panel is NEVER clipped by
            // the diagram's overflow:hidden or hidden behind the section below
            // (bottom-row cards used to open downward into the next section).
            document.body.appendChild(popup);
            popup.style.position = 'fixed';
            popup.style.transformOrigin = 'top left';
            // Anchor on the whole card for the qubit popup so it opens BESIDE the
            // tile, not off the tiny "... more" button.
            var anchor = (anchorEl.closest && anchorEl.closest('.topo-node-card')) || anchorEl;
            var ar = anchor.getBoundingClientRect();
            var pr = popup.getBoundingClientRect();
            var gap = 8, pad = 6, vw = window.innerWidth, vh = window.innerHeight;
            // Horizontal: prefer to the RIGHT of the tile; flip LEFT if it would
            // overflow the right wall; clamp if neither side fits cleanly.
            var left;
            if (ar.right + gap + pr.width <= vw - pad) left = ar.right + gap;
            else if (ar.left - gap - pr.width >= pad) left = ar.left - gap - pr.width;
            else left = Math.max(pad, vw - pr.width - pad);
            // Vertical: align to the tile top, clamp so the panel stays on screen.
            var top = ar.top;
            if (top + pr.height > vh - pad) top = Math.max(pad, vh - pr.height - pad);
            popup.style.left = Math.round(left) + 'px';
            popup.style.top = Math.round(top) + 'px';
        }
        // Close on click outside. This is a *document-level* listener and this whole
        // script re-runs on every HTMX load of the Chip Status page, so it MUST be
        // removed on navigation away — otherwise each visit leaves another permanent
        // document click handler behind and they pile up, slowing every click across
        // the app (the "gets slow / stuck after clicking menu to menu" symptom).
        function _topoDocClick(ev) {
            if (activePopup && !activePopup.contains(ev.target)) {
                var moreBtn = ev.target.closest('[data-qubit-more]');
                var edgeLabel = ev.target.closest('.topo-edge-label');
                if (!moreBtn && !edgeLabel) closePopup();
            }
        }
        document.addEventListener('click', _topoDocClick);
        document.body.addEventListener('htmx:beforeSwap', function _topoCleanup(evt) {
            if (!evt.detail.target || evt.detail.target.id !== 'table-pane') return;
            document.removeEventListener('click', _topoDocClick);
            closePopup();   // the popup now lives in <body>, not in the swapped pane
            clearTimeout(_moreHoverTimer); clearTimeout(_moreLeaveTimer);
            // An innerHTML swap drops the plot nodes but not Plotly's global (window
            // resize) registrations for them — purge so they don't accumulate per visit.
            if (window.Plotly) {
                document.querySelectorAll('.topo-dashboard .js-plotly-plot').forEach(function(p) {
                    try { Plotly.purge(p); } catch (e) {}
                });
            }
            document.body.removeEventListener('htmx:beforeSwap', _topoCleanup);
        });

        // ── Qubit "... more" details popup ───────────
        // Opens on HOVER (after a ~260ms intent delay) as a transient preview,
        // or PINNED by clicking the "... more" button. Reuses the single
        // activePopup singleton + its document-click / htmx:beforeSwap teardown.
        var _moreHoverTimer = null, _moreLeaveTimer = null;
        function _scheduleMoreClose() {
            clearTimeout(_moreLeaveTimer);
            _moreLeaveTimer = setTimeout(function() {
                if (activePopup && !activePopup._pinned) closePopup();
            }, 260);
        }
        function openQubitMore(n, anchorEl, pinned) {
            closePopup();
            var popup = document.createElement('div');
            popup.className = 'topo-card-popup';
            var html = '<div class="topo-popup-header"><span>' + n.id + ' \u2014 details</span>'
                + '<button class="topo-popup-close">\u2715</button></div>';
            SECONDARY_PROPS.forEach(function(p) {
                html += _propRowHtml(n, p, 'None');
            });
            // Per-metric recency: the 1Q gate fidelity carries its own measurement
            // time — show it honestly (only metric on the qubit that has one).
            var gfRec = n.metrics && n.metrics.gate_fidelity_avg;
            if (gfRec && typeof gfRec.updated_at === 'number') {
                html += '<div class="topo-popup-section topo-popup-recency"><span class="topo-recency '
                    + _ageClass(gfRec.updated_at) + '">gate fidelity measured ' + _ageLabel(gfRec.updated_at) + '</span></div>';
            }
            popup.innerHTML = html;
            popup.querySelector('.topo-popup-close').addEventListener('click', function(ev) { ev.stopPropagation(); closePopup(); });
            popup._pinned = !!pinned;
            popup.addEventListener('mouseenter', function() { clearTimeout(_moreLeaveTimer); });
            popup.addEventListener('mouseleave', _scheduleMoreClose);
            activePopup = popup;
            positionPopup(popup, anchorEl);

            // Lazy Param-History trends (sparklines + Δ) — fetched on open, only when
            // the chip actually has snapshots. Guarded against the popup being closed/
            // replaced before the request lands; reposition once the taller content is in.
            if (_historyCount > 0) {
                var sparkSlot = document.createElement('div');
                sparkSlot.className = 'topo-popup-section muted';
                sparkSlot.style.cssText = 'font-size:0.72em;text-align:center';
                sparkSlot.textContent = 'loading trends…';
                popup.appendChild(sparkSlot);
                fetch('/api/topology/sparklines/' + encodeURIComponent(n.id), {cache: 'no-store'})
                    .then(function(r) { return r.text(); })
                    .then(function(htmlStr) {
                        if (activePopup !== popup || !popup.isConnected) return;   // popup gone
                        var tmp = document.createElement('div');
                        tmp.innerHTML = htmlStr || '';
                        sparkSlot.replaceWith.apply(sparkSlot, tmp.childNodes.length ? Array.prototype.slice.call(tmp.childNodes) : [document.createComment('no-trend')]);
                        positionPopup(popup, anchorEl);   // re-clamp now it's taller
                    })
                    .catch(function() { if (sparkSlot.parentNode) sparkSlot.remove(); });
            }
        }
        topo.nodes.forEach(function(n) {
            var btn = wrap.querySelector('[data-qubit-more="' + n.id + '"]');
            if (btn) btn.addEventListener('click', function(ev) {
                ev.stopPropagation();
                openQubitMore(n, btn, true);     // click the button = pin it open
            });
        });

        // ── Click + hover handlers ───────────────────────────────────
        var _coarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
        topo.nodes.forEach(function(n) {
            var card = wrap.querySelector('[data-qubit="' + ((window.CSS && CSS.escape) ? CSS.escape(n.id) : n.id) + '"]');
            if (!card) return;
            // hover-to-preview the "... more" details (intent-delay in, grace out);
            // skipped on touch (tap the "... more" button instead).
            if (!_coarse) {
                card.addEventListener('mouseenter', function() {
                    clearTimeout(_moreLeaveTimer);
                    clearTimeout(_moreHoverTimer);
                    _moreHoverTimer = setTimeout(function() {
                        if (activePopup && activePopup._pinned) return;   // don't disturb a pinned popup
                        var b = wrap.querySelector('[data-qubit-more="' + n.id + '"]');
                        openQubitMore(n, b || card, false);
                    }, 260);
                });
                card.addEventListener('mouseleave', function() {
                    clearTimeout(_moreHoverTimer);
                    _scheduleMoreClose();
                });
            }
            var _clickTime = 0;
            card.addEventListener('click', function(ev) {
                if (ev.target.closest('[data-qubit-more]')) return;  // handled by popup
                var now = Date.now();
                if (now - _clickTime < 400) {
                    _clickTime = 0;
                    showQubitJsonPanel(n.id, rawWiring);
                } else {
                    _clickTime = now;
                    setTimeout(function() {
                        if (_clickTime !== 0) {
                            htmx.ajax('GET', '/qubit/' + n.id, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
                        }
                    }, 420);
                }
            });
        });

        // ── Pair popup on edge labels ────────────────────────────────
        topo.edges.forEach(function(e) {
            var lbl = wrap.querySelector('.topo-edge-label[data-pair="' + ((window.CSS && CSS.escape) ? CSS.escape(e.pair_id) : e.pair_id) + '"]');
            if (!lbl) return;
            lbl.addEventListener('click', function(ev) {
                ev.stopPropagation();
                closePopup();
                var popup = document.createElement('div');
                popup.className = 'topo-pair-popup';
                var html = '<div class="topo-popup-header"><span>' + e.pair_id + '</span>'
                    + '<button class="topo-popup-close">\u2715</button></div>';

                // Gate fidelities — grouped one row per CANDIDATE gate (the old
                // code emitted one row per metric, all labelled with the same gate
                // name → indistinguishable). The pair's CZ number is the "best of
                // N" of these; mark the winner (e.best_gate) and give each gate its
                // own RB-source deep-link.
                var anyRowLoadId = false;
                if (e.gate_fidelities && e.gate_fidelities.length > 0) {
                    var byGate = {}, gateOrder = [];
                    e.gate_fidelities.forEach(function(gf) {
                        var g = gf.gate;
                        if (!byGate[g]) { byGate[g] = {metrics: {}, load_id: null}; gateOrder.push(g); }
                        var val = (gf.Fidelity != null) ? gf.Fidelity : gf.value;
                        if (val != null && byGate[g].metrics[gf.metric] == null) byGate[g].metrics[gf.metric] = val;
                        if (gf.load_id != null) byGate[g].load_id = gf.load_id;
                    });
                    var nGates = gateOrder.length;
                    var title = 'Gate Fidelity' + (nGates > 1 ? ' — best of ' + nGates : '');
                    // Per-metric recency: when THIS number's own measurement time is
                    // recorded, show "measured Nd ago" coloured by staleness — the
                    // honest per-metric signal, not the pair's freshest calibration.
                    var czAge = (typeof e.cz_fidelity_updated_at === 'number')
                        ? '<span class="topo-recency ' + _ageClass(e.cz_fidelity_updated_at) + '">measured ' + _ageLabel(e.cz_fidelity_updated_at) + '</span>'
                        : '';
                    html += '<div class="topo-popup-section"><div class="topo-popup-section-title">' + title + czAge + '</div>';
                    gateOrder.forEach(function(g) {
                        var info = byGate[g];
                        var isWin = (g === e.best_gate);
                        var parts = [];
                        // Headline fidelities only — the compact popup is a "best of
                        // N" summary; RB fit byproducts (StandardRB_alpha, …) are
                        // clutter here and live in the full /pair inspector.
                        var HEADLINE = [['Bell_State','Bell'],['StandardRB','SRB'],['InterleavedRB','IRB']];
                        HEADLINE.forEach(function(pair) {
                            if (info.metrics[pair[0]] != null) parts.push(pair[1] + ' ' + (info.metrics[pair[0]]*100).toFixed(1) + '%');
                        });
                        // Fallback: a gate with none of the three still shows one number.
                        if (parts.length === 0) {
                            Object.keys(info.metrics).forEach(function(mk) {
                                if (!/_alpha$/.test(mk)) parts.push(mk + ' ' + fmtNum(info.metrics[mk], 4));
                            });
                        }
                        var src = '';
                        if (info.load_id != null) {
                            anyRowLoadId = true;
                            src = ' <a href="/dataset/by-run/' + info.load_id + '" style="font-size:0.85em" title="Open the RB run that produced these numbers">RB #' + info.load_id + ' →</a>';
                        }
                        html += '<div class="topo-popup-row' + (isWin ? ' topo-popup-row-win' : '') + '">'
                            + '<span class="topo-popup-row-label">' + (isWin ? '★ ' : '') + g.replace(/^cz_/, '') + '</span>'
                            + '<span>' + parts.join(' · ') + src + '</span></div>';
                    });
                    html += '</div>';
                }

                // Gate details (amplitudes, lengths, phase shifts)
                if (e.gate_details && e.gate_details.length > 0) {
                    html += '<div class="topo-popup-section"><div class="topo-popup-section-title">Gate Parameters</div>';
                    e.gate_details.forEach(function(gd) {
                        var label = gd.name.replace(/^cz_/, '');
                        var parts = [];
                        if (gd.amplitude != null) parts.push('amp=' + gd.amplitude.toFixed(4));
                        if (gd.coupler_amp != null) parts.push('c_amp=' + gd.coupler_amp.toFixed(4));
                        if (gd.length != null) parts.push('len=' + gd.length);
                        if (gd.flat_length != null) parts.push('flat=' + gd.flat_length);
                        if (gd.phase_ctrl != null) parts.push('p\u2081=' + gd.phase_ctrl.toFixed(3));
                        if (gd.phase_tgt != null) parts.push('p\u2082=' + gd.phase_tgt.toFixed(3));
                        // CR/ZZ drive levers (query._extract_cr_details rows)
                        if (gd.drive_amplitude_scaling != null) parts.push('drv\u00d7=' + gd.drive_amplitude_scaling.toFixed(4));
                        if (gd.drive_phase != null) parts.push('drv\u03c6=' + gd.drive_phase.toFixed(4));
                        if (gd.cancel_amplitude_scaling != null) parts.push('cnl\u00d7=' + gd.cancel_amplitude_scaling.toFixed(4));
                        if (gd.cancel_phase != null) parts.push('cnl\u03c6=' + gd.cancel_phase.toFixed(4));
                        if (gd.qc_correction_phase != null) parts.push('\u03c6c=' + gd.qc_correction_phase.toFixed(3));
                        if (gd.qt_correction_phase != null) parts.push('\u03c6t=' + gd.qt_correction_phase.toFixed(3));
                        if (gd.eff_if_mhz != null) parts.push('IF=' + gd.eff_if_mhz + 'MHz');
                        html += '<div class="topo-popup-row"><span class="topo-popup-row-label">' + label + '</span><span>' + parts.join(', ') + '</span></div>';
                    });
                    html += '</div>';
                }

                // Confusion matrix
                if (e.confusion_diag || e.confusion_offdiag) {
                    html += '<div class="topo-popup-section"><div class="topo-popup-section-title">' + e.confusion_size + '\u00d7' + e.confusion_size + ' Confusion Matrix</div>';
                    if (e.confusion_diag) {
                        html += '<div class="topo-popup-row"><span class="topo-popup-row-label">diag</span><span>' + e.confusion_diag.map(function(v){return (v*100).toFixed(1)+'%';}).join(', ') + '</span></div>';
                    }
                    if (e.confusion_offdiag) {
                        var maxOff = Math.max.apply(null, e.confusion_offdiag);
                        var avgOff = e.confusion_offdiag.reduce(function(a,b){return a+b;},0) / e.confusion_offdiag.length;
                        html += '<div class="topo-popup-row"><span class="topo-popup-row-label">offdiag</span><span>max=' + (maxOff*100).toFixed(1) + '%, avg=' + (avgOff*100).toFixed(1) + '%</span></div>';
                    }
                    html += '</div>';
                }

                // Pair-level parameters
                var pairParams = [];
                if (e.detuning != null) pairParams.push(['detuning', fmtNum(e.detuning, 4)]);
                if (e.coupler_decouple_offset != null) pairParams.push(['decouple', fmtNum(e.coupler_decouple_offset, 4)]);
                if (e.mutual_flux_bias != null) {
                    var mfb = Array.isArray(e.mutual_flux_bias) ? e.mutual_flux_bias.map(function(v){return fmtNum(v,3);}).join(', ') : fmtNum(e.mutual_flux_bias, 4);
                    pairParams.push(['flux_bias', mfb]);
                }
                if (pairParams.length > 0) {
                    html += '<div class="topo-popup-section"><div class="topo-popup-section-title">Pair Parameters</div>';
                    pairParams.forEach(function(pp) {
                        html += '<div class="topo-popup-row"><span class="topo-popup-row-label">' + pp[0] + '</span><span>' + pp[1] + '</span></div>';
                    });
                    html += '</div>';
                }

                // Provenance fallback: the per-gate rows above already carry their
                // own "RB #<id> \u2192" links. Only fall back to an edge-level line when
                // NO gate row had a load_id \u2014 either the legacy single edge-level
                // cz_load_id, or (honest-missing) an explicit "not recorded" so a
                // silent gap never reads as "broken" (same rule as the grey None tiles).
                if (!anyRowLoadId && e.cz_load_id != null) {
                    html += '<div class="topo-popup-section" style="text-align:center;padding-top:2px">'
                        + '<a href="/dataset/by-run/' + e.cz_load_id + '" style="font-size:0.72em">source: RB run #'
                        + e.cz_load_id + ' \u2192</a></div>';
                } else if (!anyRowLoadId && e.cz_fidelity != null) {
                    html += '<div class="topo-popup-section muted" style="text-align:center;padding-top:2px;font-size:0.72em">'
                        + 'source: RB run not recorded</div>';
                }
                // "Open in inspector" link \u2014 id in an escaped data-attr, read back
                // at click time (not interpolated into the JS string / URL).
                html += '<div class="topo-popup-section" style="text-align:center;padding-top:4px">'
                    + '<a href="#" style="font-size:0.72em" data-inspect-id="' + _esc(e.pair_id) + '"'
                    + ' onclick="event.preventDefault();window._inspectPair(this.getAttribute(\'data-inspect-id\'))">Open in inspector \u2192</a></div>';

                popup.innerHTML = html;
                popup.querySelector('.topo-popup-close').addEventListener('click', function(ev) { ev.stopPropagation(); closePopup(); });
                activePopup = popup;
                positionPopup(popup, lbl);
            });
        });
    })();

    // ── Build HTML legend ────────────────────────────────────────────
    var legendEl = document.getElementById('topology-legend');
    if (legendEl) {
        var items = [];
        var seenChains = {};
        topo.nodes.forEach(function(n) { if (n.chain) seenChains[n.chain] = chainColors[n.chain] || tCfg.chainFallback; });
        Object.keys(seenChains).sort().forEach(function(ch) {
            items.push('<span class="topology-legend-item"><span class="topology-legend-swatch" style="background:' + seenChains[ch] + '"></span>Chain ' + ch + '</span>');
        });
        if (topo.edges.length > 0) {
            items.push('<span class="topology-legend-item"><span class="topology-legend-line" style="background:' + tCfg.edgeFidelityGood + '"></span>CZ \u226595%</span>');
            items.push('<span class="topology-legend-item"><span class="topology-legend-line" style="background:' + tCfg.edgeFidelityWarn + '"></span>CZ \u226585%</span>');
            items.push('<span class="topology-legend-item"><span class="topology-legend-line" style="background:' + tCfg.edgeFidelityBad + '"></span>CZ &lt;85%</span>');
            items.push('<span class="topology-legend-item"><span class="topology-legend-line" style="background:' + tCfg.edgeFidelityNone + '"></span>No data</span>');
        }
        legendEl.innerHTML = items.join('');
    }

    // ── Highlight metric (used by heatmap grid click) ──────────────
    // (single-metric heatmap grid removed; the Overview section carries the
    // headline metrics now — see buildOverviewTiles.)

    // ── Edge label toggle ────────────────────────────────────────────
    var _edgeLabelsVisible = true;
    try { var st = localStorage.getItem('quam_topo_edge_labels'); if (st === '0') _edgeLabelsVisible = false; } catch(e) {}

    window.toggleEdgeLabels = function(show) {
        _edgeLabelsVisible = show;
        document.querySelectorAll('.topo-edge-label').forEach(function(el) {
            el.style.display = show ? '' : 'none';
        });
        try { localStorage.setItem('quam_topo_edge_labels', show ? '1' : '0'); } catch(e) {}
    };

    var toggleEl = document.getElementById('topo-labels-toggle');
    if (!_edgeLabelsVisible) {
        if (toggleEl) toggleEl.checked = false;
        toggleEdgeLabels(false);
    }

    // ── JSON panel function ──────────────────────────────────────────
    function showQubitJsonPanel(name, raw) {
        var subtree = ((raw.wiring || {}).qubits || {})[name];
        var panel = document.getElementById('json-panel');
        var treeEl = document.getElementById('json-panel-tree');
        if (!panel || !treeEl) return;
        document.getElementById('json-panel-title').textContent = 'Wiring JSON \u2014 ' + name;
        treeEl.innerHTML = '';
        if (subtree) renderJsonTree('json-panel-tree', subtree, {defaultDepth: 2});
        panel.classList.remove('hidden');
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 3: Qubit Heatmap Grid
    // ══════════════════════════════════════════════════════════════════

    // (Section 3's single-metric heatmap grid + updateHeatmapGrid were removed;
    // buildOverviewTiles renders the rich headline instead. The colorscale/palette
    // selectors still apply to the per-metric panel grids via their data-heat-t.)

    // ══════════════════════════════════════════════════════════════════
    // Section 4: Distribution Histograms
    // ══════════════════════════════════════════════════════════════════

    // Distribution histogram definitions (colors assigned from bar palette)
    // Histograms read the gated value (_mv) too \u2014 an unphysical fit never adds a
    // phantom bar at \u2212473\u00b5s.
    var _histDefs = [
        {id:'hist-gate-fidelity', boxId:'hist-box-gate-fidelity', metricKey:'gate_fidelity_avg',
         values: topo.nodes.map(function(n){return _mv(n,'gate_fidelity_avg');}), stopIdx:3, xaxis:'Fidelity'},
        {id:'hist-t1', boxId:'hist-box-t1', metricKey:'T1',
         values: topo.nodes.map(function(n){var v=_mv(n,'T1');return v!=null?v*1e6:null;}), stopIdx:2, xaxis:'T1 (\u00b5s)'},
        {id:'hist-cz-fidelity', boxId:'hist-box-cz-fidelity', metricKey:'cz_fidelity',
         values: topo.edges.map(function(e){return _mv(e,'cz_fidelity');}), stopIdx:4, xaxis:'CZ Fidelity'},
        {id:'hist-f01', boxId:'hist-box-f01', metricKey:'f_01',
         values: topo.nodes.map(function(n){var v=_mv(n,'f_01');return v!=null?v/1e9:null;}), stopIdx:1, xaxis:'f_01 (GHz)'}
    ];

    function renderHistograms() {
        _histDefs.forEach(function(h) {
            var vals = h.values.filter(function(v){return v!=null && typeof v==='number';});
            var box = document.getElementById(h.boxId);
            if (vals.length === 0) { if (box) box.style.display = 'none'; return; }
            var color = _barColorScale[Math.min(h.stopIdx, _barColorScale.length - 1)];
            // Plotly titles are plain strings (no HTML) → append the good-direction
            // glyph only; the hover blurb lives on the HTML surfaces, not here.
            var ar = h.metricKey ? arrow(h.metricKey) : '';
            var axisTitle = h.xaxis + (ar ? '  ' + ar : '');
            _plotlyRender(h.id, [{
                x: vals, type: 'histogram',
                nbinsx: Math.min(20, Math.max(8, Math.ceil(vals.length/2))),
                marker: {color: color, line: {color:'#fff', width:1}}
            }], {
                margin:{l:45,r:15,t:10,b:35},
                xaxis:{title:{text:axisTitle,font:{size:11}},tickfont:{size:10}},
                yaxis:{title:{text:'Count',font:{size:11}},tickfont:{size:10}},
                plot_bgcolor:'transparent', paper_bgcolor:'transparent', bargap:0.08
            }, {responsive:true, displayModeBar:false});
        });
    }
    // renderHistograms() is invoked lazily by setChipStatusView (the
    // Distributions view), not eagerly — keeps the default view Plotly-free.

    // ══════════════════════════════════════════════════════════════════
    // Shared: Pre-compute grid layout from topology positions
    // (Used by Section 5 2Q RB panels and Section 6 metric panels)
    // ══════════════════════════════════════════════════════════════════

    var gridPositions = {};   // qubit id → {col, row}
    var gridCols = 0, gridRows = 0;
    var minGC = Infinity, minGR = Infinity;
    topo.nodes.forEach(function(n) {
        var parts = (n.grid_location || '').split(',');
        if (parts.length === 2) {
            var c = parseInt(parts[0], 10), r = parseInt(parts[1], 10);
            if (!isNaN(c) && !isNaN(r)) {
                gridPositions[n.id] = {col: c, row: r};
                if (c < minGC) minGC = c;
                if (r < minGR) minGR = r;
            }
        }
    });
    // Normalize to 0-based, find dimensions, and flip row axis
    // (QUAM convention: row 0 = bottom of chip; screen: row 0 = top)
    var hasGrid = Object.keys(gridPositions).length === topo.nodes.length;
    var maxGR = -Infinity;
    if (hasGrid) {
        for (var qid in gridPositions) {
            gridPositions[qid].col -= minGC;
            gridPositions[qid].row -= minGR;
            if (gridPositions[qid].col + 1 > gridCols) gridCols = gridPositions[qid].col + 1;
            if (gridPositions[qid].row + 1 > gridRows) gridRows = gridPositions[qid].row + 1;
            if (gridPositions[qid].row > maxGR) maxGR = gridPositions[qid].row;
        }
        // Flip rows so row 0 in data appears at the bottom of the grid
        for (var qid2 in gridPositions) {
            gridPositions[qid2].row = maxGR - gridPositions[qid2].row;
        }
    }

    // Render a list of chart specs progressively so a burst of Plotly.newPlot
    // calls never janks the main thread. Each spec is
    // {chartId, data, layout, config, computeLayout?(chartEl, baseLayout)}.
    // Heights that depend on the grid's offsetHeight are computed HERE, after the
    // single innerHTML assignment, via the optional computeLayout hook — reading
    // offsetHeight mid-build (the old per-panel innerHTML += pattern) is what
    // re-serialized the growing DOM and froze the page.
    function _renderChartSpecsProgressively(specs) {
        var i = 0, BATCH = 3;
        function pump() {
            var end = Math.min(i + BATCH, specs.length);
            for (; i < end; i++) {
                var s = specs[i];
                var el = document.getElementById(s.chartId);
                if (!el) continue;
                var layout = s.computeLayout ? s.computeLayout(el, s.layout) : s.layout;
                _plotlyRender(el, s.data, layout, s.config);
            }
            if (i < specs.length) {
                (window.requestAnimationFrame || function(f) { setTimeout(f, 16); })(pump);
            }
        }
        if (specs.length) pump();
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 5: Gate Fidelity — 2Q RB (pair-based panels)
    // ══════════════════════════════════════════════════════════════════

    function build2QRBPanels() {
        var container = document.getElementById('topo-2q-rb-panels');
        if (!container) return;

        // ── Collect RB data from edges, grouped by RB type then gate ──
        // Normalize the metric name: LabA labels interleaved RB "IRB" and stores
        // StandardRB as a nested dict whose fidelity is average_gate_fidelity (the
        // canonical `value` is set server-side in _extract_pair_gate_fidelities).
        // Fall back to average_gate_fidelity here too so a value always lands.
        var rbData = {};  // { "StandardRB": { "cz_flattop": [{pair_id, source, target, value},...] }, ... }
        topo.edges.forEach(function(e) {
            if (!e.gate_fidelities) return;
            e.gate_fidelities.forEach(function(gf) {
                var rbType = gf.metric === 'StandardRB' ? 'StandardRB'
                           : (gf.metric === 'InterleavedRB' || gf.metric === 'IRB') ? 'InterleavedRB'
                           : null;
                if (!rbType) return;
                var val = typeof gf.value === 'number' ? gf.value
                        : typeof gf.average_gate_fidelity === 'number' ? gf.average_gate_fidelity : null;
                if (val == null) return;
                if (!rbData[rbType]) rbData[rbType] = {};
                if (!rbData[rbType][gf.gate]) rbData[rbType][gf.gate] = [];
                rbData[rbType][gf.gate].push({
                    pair_id: e.pair_id, source: e.source, target: e.target, value: val
                });
            });
        });

        // Exit early if no RB data
        if (!rbData.StandardRB && !rbData.InterleavedRB) return;

        // ── Compute pair grid positions (doubled-coordinate scheme) ──
        // Pair midpoint between source & target qubits.
        // gridPositions are 0-based integers (already row-flipped), so
        // source.col + target.col gives the doubled-coordinate directly.
        var pairGridPositions = {};
        var pairGridCols = 0, pairGridRows = 0;
        topo.edges.forEach(function(e) {
            var sp = gridPositions[e.source], tp = gridPositions[e.target];
            if (!sp || !tp) return;
            var mc = sp.col + tp.col;
            var mr = sp.row + tp.row;
            pairGridPositions[e.pair_id] = {col: mc, row: mr};
            if (mc + 1 > pairGridCols) pairGridCols = mc + 1;
            if (mr + 1 > pairGridRows) pairGridRows = mr + 1;
        });
        var hasPairGrid = Object.keys(pairGridPositions).length > 0;

        var stops = dCfg.colorScale;

        // Pass 1: accumulate HTML + render specs, then ONE innerHTML write below
        // (per-panel `+=` re-serialized the growing DOM each panel = the freeze).
        var html = ['<h3 class="topo-section-title" style="margin-top:1.5rem">Gate Fidelity \u2014 2Q RB</h3>'];
        var specs = [];

        // ── Render panels per RB type, then per gate ────────────────
        ['StandardRB', 'InterleavedRB'].forEach(function(rbType) {
            var gates = rbData[rbType];
            if (!gates) return;

            var rbLabel = rbType === 'StandardRB' ? 'Standard RB' : 'Interleaved RB';
            html.push('<h4 class="topo-section-title" style="margin-top:1rem;font-size:1.05em">' + rbLabel + '</h4>');

            var gateNames = Object.keys(gates).sort(function(a, b) {
                return gates[b].length - gates[a].length;  // most results first
            });
            gateNames.forEach(function(gateName) {
                var pairs = gates[gateName];
                if (!pairs.length) return;

                var vals = pairs.map(function(p) { return p.value; });
                // Physical-gate BEFORE aggregating — a broken RB fit (>1 or ≤0) must
                // not pollute the stat line avg/min/max, skew the colour range, or
                // enter the outlier median/MAD that other pairs are judged against.
                // (It's a separate bad-fit signal, shown raw in its own cell.) Mirrors
                // the 1Q panel, which gates via the record's physical value.
                var _physCz = vals.filter(function(v) { return typeof v === 'number' && v > 0 && v <= 1.0000001; });
                var agg = computeAggregates(_physCz);
                if (agg.count === 0) return;

                var range = agg.max - agg.min || 1;
                var scorer = outlierScorer(_physCz);   // robust MAD outlier flag (this gate)
                var gateLabel = _esc(gateName.replace(/^cz_/, ''));   // only ever rendered as HTML
                var secId = 'rb-' + rbType + '-' + gateName.replace(/[^a-zA-Z0-9]/g, '-');
                var chartId = secId + '-chart';

                var sectionHtml = '<div class="topo-section" id="' + secId + '">';
                // Stat line is its OWN block below the title (not inside the <h4>) so
                // at narrow width it wraps cleanly instead of lapping onto the grid.
                sectionHtml += '<h4 class="topo-metric-panel-title">' + gateLabel + '</h4>';
                sectionHtml += '<div class="topo-metric-panel-stat">'
                    + 'avg ' + (agg.avg * 100).toFixed(2) + '% <span>med ' + (agg.median * 100).toFixed(2)
                    + '%</span> <span>min ' + (agg.min * 100).toFixed(2) + '%</span> <span>max ' + (agg.max * 100).toFixed(2)
                    + '%</span> <span>(' + agg.count + '/' + topo.edges.length + ' pairs)</span>'
                    + '</div>';

                // ── Side-by-side: grid (left) + bar chart (right) ────
                sectionHtml += '<div class="topo-metric-panel-row">';

                // Pair topology grid
                if (hasPairGrid) {
                    sectionHtml += '<div class="topo-2q-pair-grid" style="grid-template-columns:repeat(' + pairGridCols + ',var(--topo-panel-cell-size));grid-template-rows:repeat(' + pairGridRows + ',auto)">';
                } else {
                    sectionHtml += '<div class="topo-heatmap-grid">';
                }

                // Render a cell for EVERY pair at its grid position — pairs with
                // no RB data for THIS gate show a grey "None" instead of leaving a
                // gap, so the topology shape is preserved and an uncalibrated pair
                // is a visible to-do, not invisible. (Without a grid we can only
                // place the data pairs.)
                var valueByPair = {};
                pairs.forEach(function(p) { valueByPair[p.pair_id] = p; });
                var cellPairs = hasPairGrid
                    ? topo.edges.filter(function(e) { return pairGridPositions[e.pair_id]; })
                    : pairs;
                cellPairs.forEach(function(e) {
                    var pid = e.pair_id || e.id;
                    var pidE = _esc(pid);
                    var pos = pairGridPositions[pid];
                    var posStyle = pos ? ('grid-column:' + (pos.col + 1) + ';grid-row:' + (pos.row + 1) + ';') : '';
                    var p = valueByPair[pid];
                    if (!p) {
                        sectionHtml += '<div class="heatmap-cell heatmap-cell-none" data-pair="' + pidE + '" '
                            + 'title="' + pidE + ' \u2014 ' + gateLabel + ': not measured \u00b7 click to inspect" '
                            + 'style="' + posStyle + '">'
                            + '<div class="heatmap-cell-name">' + pidE + '</div>'
                            + '<div class="heatmap-cell-value">None</div></div>';
                        return;
                    }
                    var bg, fg, ht;
                    if (agg.count > 1) {
                        ht = (p.value - agg.min) / range;
                        bg = interpolateColor(ht, stops);
                        fg = textColorForBg(bg);
                    } else { ht = 0.5; bg = stops[2]; fg = textColorForBg(stops[2]); }
                    var _physOk = p.value > 0 && p.value <= 1.0000001;
                    var _isOut = scorer && _physOk && scorer.isOutlier(p.value);
                    var _outTip = _isOut ? ' \u00b7 \u26a0 outlier (' + scorer.score(p.value).toFixed(1) + '\u00d7 MAD from chip median ' + (scorer.median * 100).toFixed(2) + '%)' : '';
                    sectionHtml += '<div class="heatmap-cell' + (_isOut ? ' topo-outlier' : '') + '" data-pair="' + pidE + '" data-metric="cz_fidelity" data-heat-v="' + p.value + '" '
                        + 'title="' + pidE + ' \u2014 ' + gateLabel + ': ' + (p.value * 100).toFixed(2) + '%' + _outTip + ' \u00b7 click to inspect" '
                        + 'data-heat-t="' + ht.toFixed(6) + '" '
                        + 'style="' + posStyle + 'background-color:' + bg + ';color:' + fg + '">'
                        + '<div class="heatmap-cell-name">' + pidE + '</div>'
                        + '<div class="heatmap-cell-value">' + (p.value * 100).toFixed(2) + '%</div></div>';
                });
                sectionHtml += '</div>'; // close grid

                // Bar chart (right side)
                sectionHtml += '<div class="topo-metric-bar-chart" id="' + chartId + '"></div>';
                sectionHtml += '</div>'; // close panel-row
                sectionHtml += '</div>'; // close section
                html.push(sectionHtml);

                // Defer the bar chart: data is pure JS, but height needs the
                // grid's offsetHeight, only measurable after the single write.
                var sorted = pairs.slice().sort(function(a, b) { return (b.value || 0) - (a.value || 0); });
                var barColors = sorted.map(function(p) {
                    return interpolateColor((p.value - agg.min) / range, _barColorScale);
                });
                var displayVals = sorted.map(function(p) { return p.value * 100; });

                specs.push({
                    chartId: chartId,
                    data: [{
                        y: sorted.map(function(p) { return p.pair_id; }),
                        x: displayVals,
                        text: displayVals.map(function(v) { return v.toFixed(2); }),
                        textposition: 'outside',
                        textfont: {size: 11},
                        type: 'bar', orientation: 'h',
                        marker: {color: barColors, line: {color: '#fff', width: 1}},
                        hovertemplate: '%{y}: %{text}%<extra></extra>',
                        cliponaxis: false
                    }],
                    layout: {
                        margin: {l: 80, r: 60, t: 5, b: 28},
                        xaxis: {title: {text: rbLabel + ' \u2014 ' + gateLabel + ' (%)', font: {size: 10}}, tickfont: {size: 9}},
                        yaxis: {tickfont: {size: 10}, autorange: 'reversed'},
                        plot_bgcolor: 'transparent', paper_bgcolor: 'transparent', bargap: 0.2
                    },
                    config: {responsive: true, displayModeBar: false},
                    computeLayout: function(chartEl, base) {
                        // Size to the bar COUNT (capped) — NOT the topology grid height.
                        // The grid now shows every pair (grey "None"), so matching it blew
                        // the chart up to ~1000px and overlapped the next panel.
                        base.height = Math.min(640, Math.max(160, sorted.length * 26));
                        return base;
                    }
                });
            });
        });

        // ── Click handlers: pair cell → inspector ────────────────────
        // Single DOM write, then re-query click handlers, then render charts.
        container.innerHTML = html.join('');

        container.querySelectorAll('.heatmap-cell[data-pair]').forEach(function(cell) {
            cell.addEventListener('click', function() {
                var pid = cell.getAttribute('data-pair');
                if (pid) htmx.ajax('GET', '/pair/' + pid, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            });
        });

        _renderChartSpecsProgressively(specs);
        if (window._recolorTopology) window._recolorTopology();   // apply Spec/relative to the new cells
        if (window.ChipStatus && window.ChipStatus.liveDiff) window.ChipStatus.liveDiff.decorate();
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 6: Per-Metric Detail Panels (grid + bar chart each)
    // ══════════════════════════════════════════════════════════════════

    function buildMetricPanels() {
        var container = document.getElementById('topo-metric-panels');
        if (!container) return;

        // Define which metrics get their own full panel, in display order
        var PANEL_DEFS = [
            {key:'gate_fidelity_avg', title:'Gate Fidelity \u2014 RB avg (%)',group:'fidelity'},
            {key:'gate_fidelity_x180',title:'Gate Fidelity x180 (%)',   group:'fidelity'},
            {key:'gate_fidelity_x90', title:'Gate Fidelity x90 (%)',    group:'fidelity'},
            {key:'assignment_fidelity',title:'IQ Blob (%)',             group:'fidelity'},
            {key:'ro_fidelity_g',     title:'Readout Fidelity |g\u27E9 (%)',group:'fidelity'},
            {key:'ro_fidelity_e',     title:'Readout Fidelity |e\u27E9 (%)',group:'fidelity'},
            {key:'T1',                title:'T1 (\u00b5s)',             group:'coherence'},
            {key:'T2ramsey',          title:'T2 Ramsey (\u00b5s)',      group:'coherence'},
            {key:'T2echo',            title:'T2 Echo (\u00b5s)',        group:'coherence'},
            {key:'f_01',              title:'Qubit Frequency f\u2080\u2081', group:'frequency'},
            {key:'readout_frequency', title:'Readout Frequency',        group:'frequency'},
            {key:'anharmonicity',     title:'Anharmonicity',            group:'frequency'},
            {key:'x180_amplitude',    title:'x180 Amplitude',           group:'calibration'},
            {key:'x90_amplitude',     title:'x90 Amplitude',            group:'calibration'},
            {key:'readout_amplitude', title:'Readout Amplitude',        group:'calibration'},
        ];

        function findProp(key) {
            for (var i = 0; i < ALL_CARD_PROPS.length; i++) {
                if (ALL_CARD_PROPS[i].key === key) return ALL_CARD_PROPS[i];
            }
            return null;
        }

        // Helper: convert display value to annotation text
        // Bar labels use already-converted display values (μs, GHz, %, etc.)
        function fmtBarLabel(v, key) {
            if (key === 'T1' || key === 'T2ramsey' || key === 'T2echo') return (v).toFixed(1);
            if (key === 'f_01' || key === 'readout_frequency') return (v).toFixed(4);
            if (key === 'anharmonicity') return (v).toFixed(1);
            if (typeof v === 'number') return v.toFixed(2);
            return String(v);
        }

        var prevGroup = '';
        // Pass 1: accumulate HTML + render specs; ONE innerHTML write below.
        var html = [];
        var specs = [];

        PANEL_DEFS.forEach(function(def) {
            var prop = findProp(def.key);
            if (!prop) return;

            // Gated values: an unphysical fit (−473µs T2) is None, so it never feeds
            // avg/min/max, never colours red, never stretches the relative range.
            var vals = topo.nodes.map(function(n) { return _mv(n, def.key); });
            var agg = computeAggregates(vals);
            if (agg.count === 0) return;

            // Group header
            if (def.group !== prevGroup) {
                prevGroup = def.group;
                var groupLabel = {fidelity:'1Q RB & Readout Fidelity', coherence:'Coherence', frequency:'Frequencies', calibration:'Calibration'}[def.group] || def.group;
                html.push('<h3 class="topo-section-title" data-group="' + def.group + '" style="margin-top:1.5rem">' + groupLabel + '</h3>');
            }

            var secId = 'mp-' + def.key.replace(/[^a-zA-Z0-9]/g, '-');
            var stops = dCfg.colorScale;
            var range = agg.max - agg.min || 1;
            // Outliers off the same gated values (`vals`). For metrics with a
            // physicality bound (fidelities (0,1], T1/T2 >0) an unphysical fit is
            // already None, so it neither moves the median nor gets flagged — it's a
            // separate bad-fit signal. Unbounded metrics (frequencies, amplitudes)
            // have no such gate; every finite value participates, which is correct
            // (there's no "unphysical" frequency). Null when <5 pts / no spread.
            var scorer = outlierScorer(vals);

            var sectionHtml = '<div class="topo-section" data-group="' + def.group + '" id="' + secId + '">';
            // Keep the curated title text (it carries the unit suffix) but pull the
            // good-direction arrow + plain-language tooltip from META — so direction
            // and blurb have one source, even though the display string stays bespoke.
            sectionHtml += '<h4 class="topo-metric-panel-title">' + labelHtml(def.key, false, def.title) + '</h4>';
            sectionHtml += '<div class="topo-metric-panel-stat">'
                + 'avg ' + prop.fmtFn(agg.avg) + ' <span>med ' + prop.fmtFn(agg.median)
                + '</span> <span>min ' + prop.fmtFn(agg.min) + '</span> <span>max ' + prop.fmtFn(agg.max)
                + '</span> <span>(' + agg.count + '/' + topo.nodes.length + ' qubits)</span>'
                + '</div>';

            // ── Side-by-side: grid (left) + bar chart (right) ────────
            var chartId = secId + '-chart';
            sectionHtml += '<div class="topo-metric-panel-row">';

            // Grid arranged by topology position
            if (hasGrid) {
                sectionHtml += '<div class="topo-metric-topo-grid" style="grid-template-columns:repeat(' + gridCols + ',var(--topo-panel-cell-size));grid-template-rows:repeat(' + gridRows + ',auto)">';
            } else {
                sectionHtml += '<div class="topo-heatmap-grid">';
            }

            topo.nodes.forEach(function(n) {
                var posStyle = '';
                if (hasGrid && gridPositions[n.id]) {
                    posStyle = 'grid-column:' + (gridPositions[n.id].col + 1) + ';grid-row:' + (gridPositions[n.id].row + 1) + ';';
                }

                // Measured-but-unphysical (e.g. \u2212473\u00b5s T2): a distinct "bad fit" cell
                // showing the raw value struck through \u2014 NOT a red "fail" (that would
                // call a failed fit a bad qubit) and excluded from stats/colour above.
                var nidE = _esc(n.id);
                if (_badFit(n, def.key)) {
                    var _raw = n.metrics[def.key].raw;
                    sectionHtml += '<div class="heatmap-cell heatmap-cell-bad" data-qubit="' + nidE + '" data-metric="' + def.key + '" '
                        + 'title="' + nidE + ' \u2014 ' + def.title + ': ' + prop.fmtFn(_raw) + ' is unphysical (likely a failed fit) \u2014 excluded from stats &amp; colour \u00b7 click to inspect" '
                        + 'style="' + posStyle + '">'
                        + '<div class="heatmap-cell-name">' + nidE + '</div>'
                        + '<div class="heatmap-cell-value">' + prop.fmtFn(_raw) + '</div></div>';
                    return;
                }

                var v = _mv(n, def.key);
                var bg, fg, ht = null;
                if (v != null && agg.count > 1) {
                    ht = (v - agg.min) / range;
                    bg = interpolateColor(ht, stops);
                    fg = textColorForBg(bg);
                } else { bg = dCfg.nullCellColor; fg = '#666'; }

                var tAttr = ht != null ? 'data-heat-t="' + ht.toFixed(6) + '" ' : '';
                var noneCls = (v == null) ? ' heatmap-cell-none' : '';
                var _isOut = scorer && v != null && scorer.isOutlier(v);
                var outlCls = _isOut ? ' topo-outlier' : '';
                var _t1 = nidE + ' \u2014 ' + def.title + ': ' + (v != null ? prop.fmtFn(v) : 'not measured')
                    + (_isOut ? ' \u00b7 \u26a0 outlier (' + scorer.score(v).toFixed(1) + '\u00d7 MAD from chip median ' + prop.fmtFn(scorer.median) + ')' : '')
                    + ' \u00b7 click to inspect';
                var _vAttr = (typeof v === 'number') ? ' data-heat-v="' + v + '"' : '';
                sectionHtml += '<div class="heatmap-cell' + noneCls + outlCls + '" data-qubit="' + nidE + '" data-metric="' + def.key + '"' + _vAttr + ' title="' + _t1 + '" '
                    + tAttr
                    + 'style="' + posStyle + (v == null ? '' : 'background-color:' + bg + ';color:' + fg + ';') + '">'
                    + '<div class="heatmap-cell-name">' + nidE + '</div>'
                    + '<div class="heatmap-cell-value">' + (v != null ? prop.fmtFn(v) : 'None') + '</div></div>';
            });
            sectionHtml += '</div>'; // close grid

            // Bar chart (right side)
            sectionHtml += '<div class="topo-metric-bar-chart" id="' + chartId + '"></div>';
            sectionHtml += '</div>'; // close panel-row
            sectionHtml += '</div>'; // close section
            html.push(sectionHtml);

            // ── Render bar chart (compact + annotated) ───────────────
            // Gated: an unphysical fit is excluded from the bars too (no −473µs bar).
            var sorted = topo.nodes.slice()
                .filter(function(n) { return _mv(n, def.key) != null; })
                .sort(function(a, b) { return (_mv(b, def.key) || 0) - (_mv(a, def.key) || 0); });

            if (sorted.length > 0) {
                var barColors = sorted.map(function(n) {
                    var t = (_mv(n, def.key) - agg.min) / range;
                    return interpolateColor(t, _barColorScale);
                });

                var displayVals = sorted.map(function(n) { return _mv(n, def.key); });
                var xTitle = def.title;
                if (def.key === 'T1' || def.key === 'T2ramsey' || def.key === 'T2echo') {
                    displayVals = sorted.map(function(n) { return _mv(n, def.key) * 1e6; });
                } else if (def.key === 'f_01' || def.key === 'readout_frequency') {
                    displayVals = sorted.map(function(n) { return _mv(n, def.key) / 1e9; });
                } else if (def.key === 'anharmonicity') {
                    displayVals = sorted.map(function(n) { return _mv(n, def.key) / 1e6; });
                } else if (/fidelity|ro_fidelity/.test(def.key)) {
                    displayVals = sorted.map(function(n) { return _mv(n, def.key) * 100; });
                }

                // Value annotations on each bar
                var barText = displayVals.map(function(v) { return fmtBarLabel(v, def.key); });

                specs.push({
                    chartId: chartId,
                    data: [{
                        y: sorted.map(function(n) { return n.id; }),
                        x: displayVals,
                        text: barText,
                        textposition: 'outside',
                        textfont: {size: 11},
                        type: 'bar', orientation: 'h',
                        marker: {color: barColors, line: {color: '#fff', width: 1}},
                        hovertemplate: '%{y}: %{text}<extra></extra>',
                        cliponaxis: false
                    }],
                    layout: {
                        margin: {l: 50, r: 60, t: 5, b: 28},
                        xaxis: {title: {text: xTitle, font: {size: 10}}, tickfont: {size: 9}},
                        yaxis: {tickfont: {size: 10}, autorange: 'reversed'},
                        plot_bgcolor: 'transparent', paper_bgcolor: 'transparent', bargap: 0.2
                    },
                    config: {responsive: true, displayModeBar: false},
                    computeLayout: function(chartEl, base) {
                        // Size to the bar COUNT (capped) — not the topology grid height.
                        base.height = Math.min(640, Math.max(160, sorted.length * 26));
                        return base;
                    }
                });
            }
        });

        // Single DOM write, then re-query click handlers, then render charts.
        container.innerHTML = html.join('');

        container.querySelectorAll('.heatmap-cell[data-qubit]').forEach(function(cell) {
            cell.addEventListener('click', function() {
                var qid = cell.getAttribute('data-qubit');
                if (qid) htmx.ajax('GET', '/qubit/' + qid, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            });
        });

        _renderChartSpecsProgressively(specs);
        if (window._recolorTopology) window._recolorTopology();   // apply Spec/relative to the new cells
        if (window.ChipStatus && window.ChipStatus.liveDiff) window.ChipStatus.liveDiff.decorate();
    }

    // ══════════════════════════════════════════════════════════════════
    // Color palette switcher
    // ══════════════════════════════════════════════════════════════════

    function updateLegendSwatches() {
        var s = dCfg.colorScale;
        var low = document.getElementById('cs-low');
        var mid = document.getElementById('cs-mid');
        var high = document.getElementById('cs-high');
        if (low) low.style.color = s[0];
        if (mid) mid.style.color = s[Math.floor(s.length / 2)];
        if (high) high.style.color = s[s.length - 1];
        var bs = _barColorScale;
        var blow = document.getElementById('cs-bar-low');
        var bmid = document.getElementById('cs-bar-mid');
        var bhigh = document.getElementById('cs-bar-high');
        if (blow) blow.style.color = bs[0];
        if (bmid) bmid.style.color = bs[Math.floor(bs.length / 2)];
        if (bhigh) bhigh.style.color = bs[bs.length - 1];
    }

    // Helper: recolor all Plotly bar charts using _barColorScale
    function recolorBarCharts() {
        var stops = _barColorScale;
        var charts = document.querySelectorAll('.topo-metric-bar-chart.js-plotly-plot');
        for (var j = 0; j < charts.length; j++) {
            try {
                var data = charts[j].data;
                if (!data || !data[0] || !data[0].marker) continue;
                if (!Array.isArray(data[0].marker.color)) continue;
                var gridEl = charts[j].closest('.topo-metric-panel-row');
                if (!gridEl) continue;
                var cells = gridEl.querySelectorAll('[data-heat-t]');
                var yLabels = data[0].y;
                var cellMap = {};
                cells.forEach(function(c) {
                    var name = c.querySelector('.heatmap-cell-name');
                    if (name) cellMap[name.textContent.trim()] = parseFloat(c.getAttribute('data-heat-t'));
                });
                var newColors = yLabels.map(function(label) {
                    var t = cellMap[label];
                    return (t != null && !isNaN(t)) ? interpolateColor(t, stops) : stops[2];
                });
                Plotly.restyle(charts[j], {'marker.color': [newColors]}, [0]);
            } catch(e) {}
        }
    }

    // Populate both palette selector dropdowns
    (function initPaletteSelectors() {
        var sel = document.getElementById('palette-selector');
        var barSel = document.getElementById('bar-palette-selector');
        Object.keys(PALETTES).forEach(function(key) {
            if (sel) {
                var opt = document.createElement('option');
                opt.value = key;
                opt.textContent = PALETTES[key].label;
                if (key === _currentPaletteName) opt.selected = true;
                sel.appendChild(opt);
            }
            if (barSel) {
                var opt2 = document.createElement('option');
                opt2.value = key;
                opt2.textContent = PALETTES[key].label;
                if (key === _currentBarPaletteName) opt2.selected = true;
                barSel.appendChild(opt2);
            }
        });
        updateLegendSwatches();
    })();

    // Switch heatmap palette (cells only)
    window.switchPalette = function(paletteName) {
        if (!PALETTES[paletteName]) return;
        dCfg.colorScale = PALETTES[paletteName].stops;
        _currentPaletteName = paletteName;
        try { localStorage.setItem('quam_heatmap_palette', paletteName); } catch(e) {}

        var stops = dCfg.colorScale;
        var els = document.querySelectorAll('[data-heat-t]');
        for (var i = 0; i < els.length; i++) {
            var t = parseFloat(els[i].getAttribute('data-heat-t'));
            if (isNaN(t)) continue;
            var bg = interpolateColor(t, stops);
            els[i].style.backgroundColor = bg;
            els[i].style.color = textColorForBg(bg);
            if (els[i].classList.contains('topo-prop-value')) {
                els[i].style.background = bg;
                els[i].style.color = textColorForBg(bg);
            }
        }
        updateLegendSwatches();
    };

    // Switch bar chart palette
    window.switchBarPalette = function(paletteName) {
        if (!PALETTES[paletteName]) return;
        _barColorScale = PALETTES[paletteName].stops;
        _currentBarPaletteName = paletteName;
        try { localStorage.setItem('quam_bar_palette', paletteName); } catch(e) {}
        recolorBarCharts();
        if (_chipSectionBuilt.distributions) renderHistograms();
        updateLegendSwatches();
    };

    // ══════════════════════════════════════════════════════════════════
    // Sub-views: build only the selected section(s) on demand
    // ══════════════════════════════════════════════════════════════════

    // view -> { sections shown, metric-panel groups shown }. The summary cards
    // + topology diagram are always shown (cheap, the "at a glance" header).
    // Phase C — ONE scrolling dashboard. Every section is always shown; heavy
    // (Plotly) sections build lazily as they near the viewport. The sub-nav is a
    // scroll-spy jump bar. Each tab → the section/group it scrolls to (the 4
    // metric-family tabs share the 'metrics' section, scrolling to their own
    // [data-group] sub-panel).
    var TAB_SPEC = {
        topology:     { build: null,           sel: '#sec-topology' },
        overview:     { build: null,           sel: '[data-topo-section="overview"]' },
        distributions:{ build: 'distributions', sel: '[data-topo-section="distributions"]' },
        gate:         { build: '2qrb',          sel: '[data-topo-section="2qrb"]' },
        fidelity:     { build: 'metrics',       sel: '#topo-metric-panels [data-group="fidelity"]' },
        coherence:    { build: 'metrics',       sel: '#topo-metric-panels [data-group="coherence"]' },
        frequencies:  { build: 'metrics',       sel: '#topo-metric-panels [data-group="frequency"]' },
        calibration:  { build: 'metrics',       sel: '#topo-metric-panels [data-group="calibration"]' },
    };
    var _chipSectionBuilt = {};   // section key -> built once (lazy heavy builders)
    var _suppressSpyUntil = 0;    // ignore scroll-spy briefly after a click-jump

    function _ensureSectionBuilt(key) {
        if (!key || _chipSectionBuilt[key]) return;
        _chipSectionBuilt[key] = true;
        if (key === 'distributions') renderHistograms();
        else if (key === '2qrb') build2QRBPanels();
        else if (key === 'metrics') buildMetricPanels();
    }

    function _throttle(fn, ms) {
        var last = 0, timer = null;
        return function() {
            var now = Date.now();
            if (now - last >= ms) { last = now; fn(); }
            else { clearTimeout(timer); timer = setTimeout(function() { last = Date.now(); fn(); }, ms); }
        };
    }
    function _scrollPane() { return document.getElementById('table-pane'); }

    function _setActiveTab(view) {
        document.querySelectorAll('.topo-subnav-btn').forEach(function(b) {
            var on = b.getAttribute('data-view') === view;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('#chip-status-subnav a[data-view]').forEach(function(a) {
            a.classList.toggle('active', a.getAttribute('data-view') === view);
        });
    }

    // Click a tab → build its section if needed, then smooth-scroll to it.
    window.setChipStatusView = function(view, btn, scroll) {
        var spec = TAB_SPEC[view] || TAB_SPEC.topology;
        try { localStorage.setItem('quam_chipstatus_view', view); } catch (e) {}
        _setActiveTab(view);
        _suppressSpyUntil = Date.now() + 800;     // don't let the spy fight the jump
        _ensureSectionBuilt(spec.build);
        if (scroll === false) return;
        requestAnimationFrame(function() {        // let a just-built section lay out
            var el = document.querySelector(spec.sel);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            else { var p = _scrollPane(); if (p) p.scrollTo({ top: 0, behavior: 'smooth' }); }
        });
    };

    // Lazy build: materialise a heavy section as it approaches the viewport.
    function _setupLazyBuild() {
        if (!window.IntersectionObserver) {       // fallback: build everything now
            ['distributions', '2qrb', 'metrics'].forEach(_ensureSectionBuilt); return;
        }
        var io = new IntersectionObserver(function(entries) {
            entries.forEach(function(e) {
                if (e.isIntersecting) _ensureSectionBuilt(e.target.getAttribute('data-topo-section'));
            });
        }, { root: _scrollPane(), rootMargin: '400px 0px 400px 0px' });
        ['distributions', '2qrb', 'metrics'].forEach(function(k) {
            var el = document.querySelector('[data-topo-section="' + k + '"]');
            if (el) io.observe(el);
        });
    }

    // Scroll-spy: highlight the tab whose section sits at the top of the pane.
    function _setupScrollSpy() {
        var pane = _scrollPane();
        function onScroll() {
            if (Date.now() < _suppressSpyUntil) return;
            var paneTop = pane ? pane.getBoundingClientRect().top : 0;
            var best = null, bestTop = -Infinity;
            Object.keys(TAB_SPEC).forEach(function(v) {
                var el = document.querySelector(TAB_SPEC[v].sel);
                if (!el) return;
                var top = el.getBoundingClientRect().top - paneTop;
                if (top <= 130 && top > bestTop) { bestTop = top; best = v; }
            });
            if (best) _setActiveTab(best);
        }
        if (pane) {
            var _spyHandler = _throttle(onScroll, 120);
            pane.addEventListener('scroll', _spyHandler, { passive: true });
            // Teardown: #table-pane is the PERSISTENT HTMX swap target (survives
            // navigation), so without removing this the scroll listener accumulates
            // one per Chip Status visit — every OTHER per-mount listener here has this
            // beforeSwap teardown; the scroll-spy was the one that was missed.
            function _spyTeardown(evt) {
                if (evt.detail && evt.detail.target && evt.detail.target.id === 'table-pane') {
                    pane.removeEventListener('scroll', _spyHandler);
                    document.body.removeEventListener('htmx:beforeSwap', _spyTeardown);
                }
            }
            document.body.addEventListener('htmx:beforeSwap', _spyTeardown);
        }
    }

    // Re-fit the topology diagram to the current pane width (no rebuild) — runs
    // on pane/window resize so docking the inspector or dragging the split gutter
    // doesn't leave it stale/clipped.
    var _lastFitW = 0;
    window._refitTopology = function() {
        var wrap = document.getElementById('topo-html-wrap');
        if (!wrap) return;
        var inner = wrap.querySelector('.topo-inner');
        if (!inner) return;
        var cw = parseFloat(inner.style.width), ch = parseFloat(inner.style.height);
        if (!cw || !ch) return;
        var availW = wrap.parentElement ? wrap.parentElement.clientWidth - 2 : wrap.clientWidth;
        if (availW > 0 && cw > availW) {
            var s = availW / cw;
            inner.style.transformOrigin = 'top left';
            inner.style.transform = 'scale(' + s + ')';
            wrap.style.width = availW + 'px';
            wrap.style.height = (ch * s) + 'px';
        } else {
            inner.style.transform = '';
            wrap.style.width = cw + 'px';
            wrap.style.height = ch + 'px';
        }
    };
    function _maybeRefit() {
        var wrap = document.getElementById('topo-html-wrap');
        if (!wrap || !wrap.parentElement) return;
        var w = wrap.parentElement.clientWidth;
        if (w === _lastFitW) return;              // width-only guard (no resize loop)
        _lastFitW = w;
        window._refitTopology();
    }

    // ══════════════════════════════════════════════════════════════════
    // Initialization
    // ══════════════════════════════════════════════════════════════════

    // ══════════════════════════════════════════════════════════════════
    // Chip health summary — broken? (diagnostics) · stale? (recency) ·
    // in spec? (verdict vs thresholds). Built from topo.summary + thresholds.
    // ══════════════════════════════════════════════════════════════════
    function _daysAgo(ms) { return ms ? Math.floor((Date.now() - ms) / 86400000) : null; }
    function _ageLabel(ms) {
        var d = _daysAgo(ms);
        if (d === null) return '—';
        if (d <= 0) return 'today';
        if (d === 1) return 'yesterday';
        if (d < 30) return d + ' days ago';
        var mo = Math.round(d / 30); return mo + (mo === 1 ? ' month' : ' months') + ' ago';
    }
    function _ageClass(ms) {
        var d = _daysAgo(ms);
        if (d === null) return '';
        return d > 30 ? 'fail' : (d > 14 ? 'warn' : 'pass');
    }
    // pass/warn/fail for a value vs a threshold spec (mirrors core/chip_health.verdict)
    function _verdict(v, th) {
        if (v == null || typeof v !== 'number' || !th) return null;
        var warn = +th.warn, fail = +th.fail;
        if (isNaN(warn) || isNaN(fail)) return null;
        if ((th.direction || 'higher') !== 'lower') return v >= warn ? 'pass' : (v >= fail ? 'warn' : 'fail');
        return v <= warn ? 'pass' : (v <= fail ? 'warn' : 'fail');
    }
    window._chipThresholds = thresholds;   // Phase D editor mutates this + re-runs the summary
    window._inspectQubit = function(id) {
        if (window.htmx) htmx.ajax('GET', '/qubit/' + encodeURIComponent(id), {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
    };
    window._inspectPair = function(id) {
        if (window.htmx) htmx.ajax('GET', '/pair/' + encodeURIComponent(id), {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
    };
    // ONE delegated handler for every "inspect this qubit/pair" chip (verdict
    // banner, worst-offenders). The id lives in an escaped data-attr, never in an
    // inline onclick — so a crafted qubit name can't break out of a JS string.
    function _setupInspectDelegation() {
        var dash = document.querySelector('.topo-dashboard');
        if (!dash || dash._inspectBound) return;
        dash._inspectBound = true;
        dash.addEventListener('click', function(e) {
            var btn = e.target.closest && e.target.closest('[data-inspect-id]');
            if (!btn) return;
            e.preventDefault();
            var id = btn.getAttribute('data-inspect-id');
            if (btn.getAttribute('data-inspect-kind') === 'p') window._inspectPair(id);
            else window._inspectQubit(id);
        });
    }
    _setupInspectDelegation();

    function _hTile(label, value, cls, sub) {
        return '<div class="topo-health-tile ' + (cls || '') + '">' +
               '<div class="tile-val">' + value + '</div>' +
               '<div class="tile-label">' + label + '</div>' +
               (sub ? '<div class="tile-sub">' + sub + '</div>' : '') + '</div>';
    }

    function buildHealthSummary() {
        var tiles = document.getElementById('topo-health-tiles');
        var worst = document.getElementById('topo-health-worst');
        if (!tiles) return;
        var summ = topo.summary || {};
        var nodes = topo.nodes || [], edges = topo.edges || [];
        var NODE_METRICS = ['gate_fidelity_avg', 'assignment_fidelity', 'T1', 'T2ramsey', 'T2echo'];
        // Consume the MetricRecord: value is null for unphysical/unresolved, so a
        // failed fit (−473µs T2) does NOT count as "below spec" — that's a separate
        // trust signal, not a bad qubit. Falls back to the scalar if no record.
        function _mval(entity, key) {
            var rec = entity.metrics && entity.metrics[key];
            return rec ? rec.value : entity[key];
        }

        // Per-qubit worst verdict across its metrics → "below spec" count.
        var below = {};
        nodes.forEach(function(n) {
            NODE_METRICS.forEach(function(m) {
                var vr = _verdict(_mval(n, m), thresholds[m]);
                if (vr === 'fail') below[n.id] = 'fail';
                else if (vr === 'warn' && below[n.id] !== 'fail') below[n.id] = 'warn';
            });
        });
        var belowCount = Object.keys(below).length;
        var failCount = Object.keys(below).filter(function(k) { return below[k] === 'fail'; }).length;
        var czBelow = edges.filter(function(e) {
            var v = _verdict(_mval(e, 'cz_fidelity'), thresholds.cz_fidelity); return v === 'warn' || v === 'fail';
        }).length;
        // Track CZ FAILS separately so a failing CZ pair drives the overall verdict
        // to 'fail' — the exported report card already fails on a cz fail, so the
        // on-screen banner must too (they were disagreeing: banner warn vs card fail).
        var czFailCount = edges.filter(function(e) {
            return _verdict(_mval(e, 'cz_fidelity'), thresholds.cz_fidelity) === 'fail';
        }).length;
        var diagErr = 0, diagWarn = 0;
        (diagFindings || []).forEach(function(f) {
            if (f.severity === 'error') diagErr++; else if (f.severity === 'warning') diagWarn++;
        });

        var oc = summ.oldest_calibration;
        var html = '';
        html += _hTile('qubits', nodes.length, 'neutral', edges.length + ' pairs');
        html += _hTile('oldest calibration', _ageLabel(oc), _ageClass(oc),
                       summ.newest_calibration ? 'newest ' + _ageLabel(summ.newest_calibration) : 'no timestamps');
        html += _hTile('qubits below spec', belowCount, belowCount ? (failCount ? 'fail' : 'warn') : 'pass',
                       failCount ? (failCount + ' failing &middot; ' + (belowCount - failCount) + ' warn')
                                 : (belowCount ? 'to watch' : 'all in spec'));
        html += _hTile('CZ below spec', czBelow, czBelow ? (czFailCount ? 'fail' : 'warn') : 'pass',
                       czBelow ? 'of ' + edges.length + ' pairs' : 'all pairs in spec');
        var diagTotal = diagErr + diagWarn;
        html += '<a class="topo-health-tile ' + (diagErr ? 'fail' : (diagWarn ? 'warn' : 'pass')) + '" ' +
                'href="/diagnostics" hx-get="/diagnostics" hx-target="#table-pane" hx-push-url="true" ' +
                'style="text-decoration:none">' +
                '<div class="tile-val">' + (diagTotal || '✓') + '</div>' +
                '<div class="tile-label">structural issues</div>' +
                '<div class="tile-sub">' + (diagTotal ? (diagErr + ' err &middot; ' + diagWarn + ' warn') : 'none found') +
                '</div></a>';
        tiles.innerHTML = html;
        if (window.htmx) htmx.process(tiles);

        // ── Plain-language verdict banner (traffic light) ────────────
        var banner = document.getElementById('topo-verdict-banner');
        if (banner) {
            var failQubits = Object.keys(below).filter(function(k) { return below[k] === 'fail'; });
            var verdict = (diagErr > 0 || failCount > 0 || czFailCount > 0) ? 'fail'
                        : (belowCount > 0 || czBelow > 0 || diagWarn > 0) ? 'warn' : 'pass';
            var icon = verdict === 'fail' ? '⛔' : (verdict === 'warn' ? '⚠' : '✓');
            var headline;
            if (verdict === 'pass') {
                headline = 'Chip looks healthy — all ' + nodes.length + ' qubits in spec'
                         + (diagTotal ? '' : ', no structural issues') + '.';
            } else {
                var parts = [(nodes.length - belowCount) + ' of ' + nodes.length + ' qubits in spec'];
                if (czBelow) parts.push(czBelow + ' of ' + edges.length + ' CZ pairs below spec');
                if (diagTotal) parts.push(diagTotal + ' structural issue' + (diagTotal === 1 ? '' : 's'));
                if (oc) parts.push('oldest calibration ' + _ageLabel(oc));
                headline = parts.join(' · ');
            }
            var avoid = '';
            if (failQubits.length) {
                var moreN = failQubits.length - 8;
                avoid = ' <span class="verdict-avoid">avoid: ' + failQubits.slice(0, 8).map(function(id) {
                    // Escaped data-attr + delegated handler (no id in a JS string / onclick)
                    // so a hostile qubit name can't break out and execute. See _setupInspectDelegation.
                    return '<button type="button" class="verdict-avoid-chip" data-inspect-id="' + _esc(id) + '" data-inspect-kind="q">' + _esc(id) + '</button>';
                }).join('')
                    + (moreN > 0 ? ' <span class="verdict-avoid-more">+' + moreN + ' more</span>' : '')
                    + '</span>';
            }
            banner.className = 'topo-verdict-banner ' + verdict;
            banner.innerHTML = '<span class="verdict-icon">' + icon + '</span>'
                + '<span class="verdict-text">' + headline + avoid + '</span>';
            banner.hidden = false;
        }

        // "Needs attention" — worst offenders, click to inspect.
        if (worst) {
            function lowest(arr, key) {
                var c = arr.filter(function(x) { return typeof _mval(x, key) === 'number'; });
                return c.length ? c.reduce(function(a, b) { return _mval(b, key) < _mval(a, key) ? b : a; }) : null;
            }
            function pct(v) { return (v * 100).toFixed(2) + '%'; }
            function us(v) { return (v * 1e6).toFixed(1) + ' µs'; }
            var items = [];
            var lf = lowest(nodes, 'gate_fidelity_avg');
            if (lf) items.push({ id: lf.id, v: pct(_mval(lf, 'gate_fidelity_avg')), t: 'lowest 1Q fidelity',
                                 vr: _verdict(_mval(lf, 'gate_fidelity_avg'), thresholds.gate_fidelity_avg), kind: 'q' });
            var lt = lowest(nodes, 'T1');
            if (lt) items.push({ id: lt.id, v: us(_mval(lt, 'T1')), t: 'lowest T1',
                                 vr: _verdict(_mval(lt, 'T1'), thresholds.T1), kind: 'q' });
            var lc = lowest(edges, 'cz_fidelity');
            if (lc) items.push({ id: lc.pair_id, v: pct(_mval(lc, 'cz_fidelity')),
                                 t: 'lowest ' + (((topo.summary || {}).gate_vocab) || 'CZ') + ' Bell',
                                 vr: _verdict(_mval(lc, 'cz_fidelity'), thresholds.cz_fidelity), kind: 'p' });
            var oq = nodes.filter(function(n) { return n.last_calibrated; });
            if (oq.length) {
                var o = oq.reduce(function(a, b) { return b.last_calibrated < a.last_calibrated ? b : a; });
                items.push({ id: o.id, v: _ageLabel(o.last_calibrated), t: 'oldest calibration',
                             vr: _ageClass(o.last_calibrated), kind: 'q' });
            }
            var wh = '<span class="worst-label muted">Needs attention:</span>';
            items.forEach(function(it) {
                wh += '<button type="button" class="worst-chip ' + (it.vr || '') + '" ' +
                      'data-inspect-id="' + _esc(it.id) + '" data-inspect-kind="' + (it.kind === 'p' ? 'p' : 'q') + '" ' +
                      'title="' + _esc(it.t) + ' — click to inspect"><b>' + _esc(it.id) + '</b> ' + it.v +
                      ' <span class="worst-metric">' + _esc(it.t) + '</span></button>';
            });
            worst.innerHTML = wh;
        }
    }
    window._buildHealthSummary = buildHealthSummary;

    // ── Threshold editor (set pass/warn/fail in the UI; persisted to this
    //    browser; live-recomputes the verdicts + the spec colour mode) ──────
    // Display units: thresholds are stored in SI (T1/T2 seconds, fidelity
    // fraction) but edited in researcher units (µs, %).
    var METRIC_DISPLAY = {
        gate_fidelity_avg:   { unit: '%',  scale: 100, dec: 2 },
        assignment_fidelity: { unit: '%',  scale: 100, dec: 2 },
        cz_fidelity:         { unit: '%',  scale: 100, dec: 2 },
        T1:                  { unit: 'µs', scale: 1e6, dec: 1 },
        T2ramsey:            { unit: 'µs', scale: 1e6, dec: 1 },
        T2echo:              { unit: 'µs', scale: 1e6, dec: 1 }
    };
    var THRESH_ORDER = ['gate_fidelity_avg', 'assignment_fidelity', 'cz_fidelity', 'T1', 'T2ramsey', 'T2echo'];

    // True when a metric's active warn/fail differs from the seed spec default
    // (epsilon compare so re-typing the exact default value stays "default").
    function _threshEdited(k) {
        var t = thresholds[k], d = _defaultThresholds[k];
        if (!t || !d) return false;
        function diff(a, b) { return Math.abs(a - b) > 1e-12 * Math.max(1, Math.abs(b)); }
        return diff(t.warn, d.warn) || diff(t.fail, d.fail);
    }
    function buildThresholdEditor() {
        var host = document.getElementById('topo-thresh-editor');
        if (!host) return;
        var anyEdited = THRESH_ORDER.some(_threshEdited);
        // Grid: metric | warn | fail | unit | provenance(default/edited + reset).
        // Inputs edit a DRAFT — nothing applies until Apply (explicit commit).
        var html = '<div class="thresh-grid">' +
                   '<span class="thresh-h"></span><span class="thresh-h">warn ≥</span>' +
                   '<span class="thresh-h">fail &lt;</span><span class="thresh-h"></span><span class="thresh-h">spec</span>';
        THRESH_ORDER.forEach(function(k) {
            var th = thresholds[k]; if (!th) return;
            var disp = METRIC_DISPLAY[k] || { unit: '', scale: 1, dec: 3 };
            var edited = _threshEdited(k);
            // Provenance cell: "default", or "edited" + a ↺ reset-this-row button.
            var prov = edited
                ? '<span class="thresh-prov edited">edited <button type="button" class="thresh-reset-row" data-metric="' + k +
                  '" title="Reset this metric to the spec default" aria-label="Reset to spec default">↺</button></span>'
                : '<span class="thresh-prov default">default</span>';
            html += '<label class="thresh-label">' + labelHtml(k, false) + '</label>' +
                    '<input class="thresh-in" type="number" step="any" data-metric="' + k + '" data-bound="warn" value="' +
                    (th.warn * disp.scale).toFixed(disp.dec) + '">' +
                    '<input class="thresh-in" type="number" step="any" data-metric="' + k + '" data-bound="fail" value="' +
                    (th.fail * disp.scale).toFixed(disp.dec) + '">' +
                    '<span class="thresh-unit">' + disp.unit + '</span>' + prov;
        });
        html += '</div><div class="thresh-actions">' +
                '<button type="button" class="btn-sm thresh-apply" onclick="applyThresholds()">Update colour bands</button>' +
                '<button type="button" class="btn-sm outline" onclick="resetThresholds()"' + (anyEdited ? '' : ' disabled') + '>Reset all to spec</button>' +
                '<span class="muted thresh-hint" id="thresh-status">' +
                (anyEdited ? 'some thresholds edited' : 'all at spec default') +
                ' · saved to this browser</span></div>';
        host.innerHTML = html;
        // Enter in any field applies; Esc closes.
        host.querySelectorAll('.thresh-in').forEach(function(inp) {
            inp.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') { e.preventDefault(); applyThresholds(); }
                else if (e.key === 'Escape') { toggleThresholdEditor(); }
            });
        });
        // Per-row reset buttons.
        host.querySelectorAll('.thresh-reset-row').forEach(function(btn) {
            btn.addEventListener('click', function() { resetMetricThreshold(btn.getAttribute('data-metric')); });
        });
    }
    window.applyThresholds = function() {
        var host = document.getElementById('topo-thresh-editor'); if (!host) return;
        host.querySelectorAll('.thresh-in').forEach(function(inp) {
            var k = inp.getAttribute('data-metric'), bound = inp.getAttribute('data-bound');
            var disp = METRIC_DISPLAY[k] || { scale: 1 };
            var v = parseFloat(inp.value);
            if (!isNaN(v) && thresholds[k]) thresholds[k][bound] = v / disp.scale;
        });
        _saveThresholds(thresholds);
        window._chipThresholds = thresholds;
        buildThresholdEditor();   // refresh the default/edited markers + reset state
        buildHealthSummary();
        if (window._recolorTopology) window._recolorTopology();
        var st = document.getElementById('thresh-status');
        if (st) { st.textContent = '✓ applied'; st.classList.add('applied');
                  setTimeout(function() { if (st) { st.classList.remove('applied'); } }, 1600); }
    };
    window.toggleThresholdEditor = function() {
        var host = document.getElementById('topo-thresh-editor');
        if (!host) return;
        if (host.hidden) { buildThresholdEditor(); host.hidden = false; } else { host.hidden = true; }
    };
    window.resetThresholds = function() {
        thresholds = JSON.parse(JSON.stringify(_defaultThresholds));
        window._chipThresholds = thresholds;
        try { localStorage.removeItem(THRESH_KEY); } catch (e) {}
        buildThresholdEditor();
        buildHealthSummary();
        if (window._recolorTopology) window._recolorTopology();
    };
    // Reset ONE metric back to its spec default (mirrors applyThresholds' commit
    // order: persist → editor rebuild → summary → recolour).
    window.resetMetricThreshold = function(k) {
        var d = _defaultThresholds[k];
        if (!d || !thresholds[k]) return;
        thresholds[k].warn = d.warn;
        thresholds[k].fail = d.fail;
        _saveThresholds(thresholds);
        window._chipThresholds = thresholds;
        buildThresholdEditor();
        buildHealthSummary();
        if (window._recolorTopology) window._recolorTopology();
    };

    // ── Spec colour mode: paint each diagram cell by its absolute verdict
    //    (pass/warn/fail) instead of the per-metric min/max heatmap. Repaints
    //    in place from the stored data-heat-v (raw) / data-heat-t (relative). ──
    var SPEC_COLORS = { pass: '#2e7d32', warn: '#e69500', fail: '#c62828' };
    // Spec (absolute verdict) is the HONEST DEFAULT (Phase 3) — relative per-metric
    // min/max flatters a bad chip (the least-bad qubit glows top-palette green).
    // Opt out only if the user explicitly chose relative before.
    var specColorMode = true;
    try { if (localStorage.getItem('quam_chip_spec_color') === '0') specColorMode = false; } catch (e) {}

    // Unified recolour across EVERY coloured surface (diagram prop-values AND the
    // metric/2Q heatmap cells). Spec mode → verdict colour where a threshold
    // exists; metrics with no spec (frequencies, amplitudes) fall back to the
    // relative heatmap so they're still informative. Reads the stored data-heat-v
    // (raw) / data-heat-t (relative) — no rebuild.
    function _paintCell(cell, key) {
        var th = key ? thresholds[key] : null;
        if (specColorMode && th) {
            var raw = cell.getAttribute('data-heat-v');
            var val = (raw == null || raw === '') ? null : parseFloat(raw);
            var vr = _verdict(val, th);
            if (vr) { cell.style.background = SPEC_COLORS[vr]; cell.style.color = '#fff'; return; }
            cell.style.background = ''; cell.style.color = '';   // missing/unphysical → neutral (None style shows)
            return;
        }
        var t = parseFloat(cell.getAttribute('data-heat-t'));
        if (!isNaN(t)) { var bg = interpolateColor(t, dCfg.colorScale); cell.style.background = bg; cell.style.color = textColorForBg(bg); }
        else { cell.style.background = ''; cell.style.color = ''; }
    }
    window._recolorTopology = function() {
        var pv = document.querySelectorAll('.topo-prop-value');
        for (var i = 0; i < pv.length; i++) {
            var row = pv[i].closest('.topo-prop-row');
            _paintCell(pv[i], row ? row.getAttribute('data-prop') : null);
        }
        var hc = document.querySelectorAll('.heatmap-cell[data-metric]');
        for (var j = 0; j < hc.length; j++) {
            _paintCell(hc[j], hc[j].getAttribute('data-metric'));
        }
    };
    window.setSpecColorMode = function(on) {
        specColorMode = !!on;
        try { localStorage.setItem('quam_chip_spec_color', on ? '1' : '0'); } catch (e) {}
        var cb = document.getElementById('topo-spec-color'); if (cb) cb.checked = specColorMode;
        window._recolorTopology();
    };

    // Build the health summary + Overview tiles eagerly (cheap, no Plotly).
    buildHealthSummary();
    buildOverviewTiles();

    // Phase C: wire the unified dashboard — lazy build on scroll, scroll-spy tab
    // highlight, and a resize re-fit for the topology diagram.
    _setupLazyBuild();
    _setupScrollSpy();
    (function() {
        var wrap = document.getElementById('topo-html-wrap');
        _lastFitW = (wrap && wrap.parentElement) ? wrap.parentElement.clientWidth : 0;
        // Keep refs so BOTH the window-resize listener and the ResizeObserver are
        // torn down on nav-away. Without this they were re-added on every mount and
        // never removed → after N visits a single resize fires N stale handlers →
        // the progressive "gets sluggish after bouncing menus" stutter. Mirrors the
        // poll-timer + LayoutController.ro teardown.
        var _refitRO = null;
        var _refitResize = _throttle(_maybeRefit, 120);
        if (window.ResizeObserver && wrap && wrap.parentElement) {
            try { _refitRO = new ResizeObserver(_throttle(_maybeRefit, 100)); _refitRO.observe(wrap.parentElement); } catch (e) {}
        }
        window.addEventListener('resize', _refitResize);
        function _refitTeardown(evt) {
            if (evt.detail && evt.detail.target && evt.detail.target.id === 'table-pane') {
                window.removeEventListener('resize', _refitResize);
                if (_refitRO) { try { _refitRO.disconnect(); } catch (e) {} }
                document.body.removeEventListener('htmx:beforeSwap', _refitTeardown);
            }
        }
        document.body.addEventListener('htmx:beforeSwap', _refitTeardown);
    })();

    // A deep-link ?view= (left-nav sub-item or a shared link) scrolls to that
    // section; a bare /topology load stays at the top (topology), by design — we
    // do NOT resume the last-used localStorage view.
    if (_serverChipView && TAB_SPEC[_serverChipView]) {
        window.setChipStatusView(_serverChipView, null, true);
    } else {
        _setActiveTab('topology');
    }

    // Apply the persisted spec-colour mode now the diagram cards exist.
    (function() {
        var cb = document.getElementById('topo-spec-color');
        if (cb) cb.checked = specColorMode;
        if (specColorMode && window._recolorTopology) window._recolorTopology();
    })();

    _setupKeyboardNav();

    // ── Keyboard navigation (accessibility + power-user speed) ──────────
    // Roving-tabindex grid over the diagram cards + heatmap cells: arrows move
    // focus (by geometry, so it works on the irregular topology AND the regular
    // panels), Enter/Space opens the focused qubit/pair in the inspector (routes
    // through the SAME click handler — no duplicate ajax), Esc closes the open
    // popup / JSON panel. ONE document listener, torn down on table-pane swap so
    // it can't pile up across Chip Status visits.
    function _setupKeyboardNav() {
        var dash = document.querySelector('.topo-dashboard');
        if (!dash || dash._kbdBound) return;
        dash._kbdBound = true;
        var SEL = '.heatmap-cell, .topo-node-card';
        function decorate() {
            var cs = dash.querySelectorAll(SEL), seeded = false;
            cs.forEach(function(c) {
                if (!c.hasAttribute('data-kbd-cell')) {
                    c.setAttribute('data-kbd-cell', '');
                    c.setAttribute('tabindex', '-1');
                    if (!c.getAttribute('role')) c.setAttribute('role', 'button');
                }
                if (c.getAttribute('tabindex') === '0') seeded = true;
            });
            if (!seeded && cs.length) cs[0].setAttribute('tabindex', '0');   // one Tab-in point
        }
        function cells() { return Array.prototype.slice.call(dash.querySelectorAll('[data-kbd-cell]')); }
        function nearest(from, dir) {
            var r = from.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            var best = null, bestScore = Infinity;
            cells().forEach(function(c) {
                if (c === from || !c.offsetParent) return;   // skip hidden
                var b = c.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
                var dx = x - cx, dy = y - cy;
                var ok = dir === 'down' ? dy > 4 : dir === 'up' ? dy < -4 : dir === 'right' ? dx > 4 : dx < -4;
                if (!ok) return;
                var along = (dir === 'up' || dir === 'down') ? Math.abs(dy) : Math.abs(dx);
                var cross = (dir === 'up' || dir === 'down') ? Math.abs(dx) : Math.abs(dy);
                var score = along + cross * 3;   // strongly prefer staying in the row/column
                if (score < bestScore) { bestScore = score; best = c; }
            });
            return best;
        }
        function moveTo(cell, next) {
            if (!next) return;
            cell.setAttribute('tabindex', '-1');
            next.setAttribute('tabindex', '0');
            next.focus();
            next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
        // Keep the roving "0" on whatever the user focused.
        dash.addEventListener('focusin', function(e) {
            var cell = e.target.closest && e.target.closest('[data-kbd-cell]');
            if (!cell) return;
            cells().forEach(function(c) { if (c !== cell) c.setAttribute('tabindex', '-1'); });
            cell.setAttribute('tabindex', '0');
        });
        var ARROWS = { ArrowRight: 'right', ArrowLeft: 'left', ArrowUp: 'up', ArrowDown: 'down' };
        function onKey(e) {
            var t = e.target;
            // Esc closes popup / JSON panel anywhere on the page (but let inputs keep their own Esc).
            if (e.key === 'Escape' && !(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable))) {
                // Close the hover/pinned popup via the DOM — `activePopup`/`closePopup`
                // live inside the buildTopology IIFE and are NOT in this handler's
                // scope, so referencing them here threw `ReferenceError: activePopup
                // is not defined` on EVERY Escape press. The popup is a body-level
                // .topo-card-popup / .topo-pair-popup; removing the node is equivalent
                // (the IIFE's stale activePopup is handled by its isConnected guards).
                var pop = document.querySelector('.topo-card-popup, .topo-pair-popup');
                if (pop) { pop.remove(); e.preventDefault(); return; }
                var jp = document.getElementById('json-panel');
                if (jp && !jp.classList.contains('hidden')) { window.closeJsonPanel(); e.preventDefault(); return; }
            }
            if (!t || !t.closest || !t.closest('.topo-dashboard')) return;
            if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable) return;
            // Sub-nav (tablist) arrow nav.
            var navBtn = t.closest('.topo-subnav-btn');
            if (navBtn && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
                var btns = Array.prototype.slice.call(document.querySelectorAll('.topo-subnav-btn'));
                var ni = btns.indexOf(navBtn) + (e.key === 'ArrowRight' ? 1 : -1);
                if (btns[ni]) { btns[ni].focus(); e.preventDefault(); }
                return;
            }
            var cell = t.closest('[data-kbd-cell]');
            if (!cell) return;
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cell.click(); return; }
            var dir = ARROWS[e.key];
            if (!dir) return;
            decorate();                    // pick up any lazily-built panel cells
            moveTo(cell, nearest(cell, dir));
            e.preventDefault();
        }
        document.addEventListener('keydown', onKey);
        function teardown(evt) {
            if (evt.detail && evt.detail.target && evt.detail.target.id === 'table-pane') {
                document.removeEventListener('keydown', onKey);
                document.body.removeEventListener('htmx:beforeSwap', teardown);
            }
        }
        document.body.addEventListener('htmx:beforeSwap', teardown);
        decorate();
    }
};

window.closeJsonPanel = function() {
    var p = document.getElementById('json-panel');
    if (p) p.classList.add('hidden');
};

// Carry the user's live (UI-edited, localStorage) thresholds into the report
// download URL so the exported card's verdicts match the on-screen header.
window.ChipStatus.reportHref = function (linkEl, fmt) {
    try {
        var th = window._chipThresholds || {};
        linkEl.href = '/topology/report?format=' + encodeURIComponent(fmt)
            + '&thresholds=' + encodeURIComponent(JSON.stringify(th));
    } catch (e) { /* fall back to the plain href */ }
    return true;   // allow the default download with the thresholds-carrying href
};

window.ChipStatus.liveDetection = function () {
    var POLL_MS = (UI_CONFIG.topoLivePollInterval || 3) * 1000;
    if (POLL_MS <= 0) return;  // disabled
    var DEBOUNCE_MS = 2000;

    // Idempotency: liveDetection() is re-invoked on every /topology HTMX render.
    // Without clearing the prior interval, each visit leaks another 3 s poller.
    if (window.ChipStatus._livePollTimer) clearInterval(window.ChipStatus._livePollTimer);

    var pollTimer = null, debounceTimer = null;
    var banner = null, dismissed = false;

    function ensureBanner() {
        if (banner) return banner;
        banner = document.createElement('div');
        banner.className = 'topo-change-banner';
        banner.innerHTML =
            '<span style="font-size:1.2em">⚠</span>' +
            '<span class="topo-change-banner-text">Live chip state changed on disk</span>' +
            '<button class="topo-change-banner-btn">Review changes</button>' +
            '<button class="topo-change-banner-dismiss">✕</button>';
        banner.querySelector('.topo-change-banner-btn').addEventListener('click', function() {
            dismissed = true;
            hideBanner();
            if (window.openReview) window.openReview();
        });
        banner.querySelector('.topo-change-banner-dismiss').addEventListener('click', function() {
            dismissed = true;
            hideBanner();
        });
        return banner;
    }
    function showBanner() {
        var dash = document.querySelector('.topo-dashboard');
        if (!dash) return;
        var b = ensureBanner();
        if (!b.parentNode) dash.insertBefore(b, dash.firstChild);
        b.style.display = '';
        // mark which qubits/pairs the live change touched (Phase 4 before/after)
        if (window.ChipStatus && window.ChipStatus.liveDiff) window.ChipStatus.liveDiff.refresh();
    }
    function hideBanner() {
        if (banner) banner.style.display = 'none';
    }

    // Poll loop -- the server stats the live files only (no content read).
    // Skip while a request is in flight or the tab is hidden so a slow stat can't
    // stack requests (the standardized poll pattern used elsewhere in the app).
    function poll() {
        if (poll._inFlight || document.hidden) return;
        poll._inFlight = true;
        fetch('/api/topology-mtime')
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                if (data.changed) {
                    if (!dismissed) {
                        clearTimeout(debounceTimer);
                        debounceTimer = setTimeout(showBanner, DEBOUNCE_MS);
                    }
                } else {
                    dismissed = false;  // a later change should prompt again
                    clearTimeout(debounceTimer);
                    hideBanner();
                }
            })
            .catch(function() {})
            .then(function() { poll._inFlight = false; });  // finally
    }

    pollTimer = setInterval(poll, POLL_MS);
    window.ChipStatus._livePollTimer = pollTimer;
    poll();

    // In-app edits (inspector commit, diagnostics apply-fix, pulse create/delete)
    // mutate the WORKING COPY, which the live-file poll above never sees — the
    // health tiles + verdict + Overview would keep pre-edit numbers until the user
    // navigates away and back. Re-derive them from a fresh /api/topology whenever
    // the app signals a state mutation. Debounced so rapid edits coalesce into one
    // fetch, and guarded on the dashboard still being mounted (the events fire
    // app-wide, including from pages that don't own these tiles).
    var metricsRefreshTimer = null;
    function refreshMetrics() {
        if (!document.getElementById('topo-health-tiles')) return;  // not mounted
        fetch('/api/topology', { cache: 'no-store' })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !document.getElementById('topo-health-tiles')) return;
                topo = data;                       // reassign the closure's topo…
                buildHealthSummary();              // …then re-derive every consumer
                buildOverviewTiles();              //    so tiles + graph stay in sync
                if (window._recolorTopology) window._recolorTopology();
            })
            .catch(function() {});
    }
    function onStateMutated() {
        clearTimeout(metricsRefreshTimer);
        metricsRefreshTimer = setTimeout(refreshMetrics, 250);
    }
    document.body.addEventListener('pulses-changed', onStateMutated);
    document.body.addEventListener('diagnostics-changed', onStateMutated);

    // Cleanup on navigation away from the topology view.
    document.body.addEventListener('htmx:beforeSwap', function cleanup(evt) {
        if (evt.detail.target && evt.detail.target.id === 'table-pane') {
            clearInterval(pollTimer);
            clearTimeout(debounceTimer);
            clearTimeout(metricsRefreshTimer);
            document.body.removeEventListener('pulses-changed', onStateMutated);
            document.body.removeEventListener('diagnostics-changed', onStateMutated);
            document.body.removeEventListener('htmx:beforeSwap', cleanup);
        }
    });
};
