/* Chip Status — the page's client module (Foundation B of the redesign).
 * Lifted byte-for-byte from the _wiring.html inline IIFE so the logic lives
 * in one real module instead of a 1700-line template script. Loaded ONCE in
 * <head>; _wiring.html calls ChipStatus.mount(...) (and .liveDetection())
 * on every HTMX render, exactly as the old inline IIFEs ran per render.
 * The 5 server-injected values (topology, wiring, thresholds, findings,
 * chip_view) are threaded in via `opts` since a static file isn't Jinja-
 * processed. */
window.ChipStatus = window.ChipStatus || {};

/* DensityController — per-PANEL tile size (docs/141 §4o, user-directed). Each
   metric panel's title carries its own S / M / L; the chosen scale is written
   to THAT panel's --topo-density-scale (CSS derives the cell sizes per
   .topo-section; fonts are untouched) and persisted per panel key. The
   dashboard-wide scale stays 1 — the old Health-row "Tiles" slider is gone. */
window.ChipStatus.density = (function () {
    // MIN 0.55 -> 0.35 (user: the floor was not low enough); S/M/L presets unchanged
    var KEY = 'quam_chip_density_panels', MIN = 0.35, MAX = 1.15;
    var PRESETS = [['S', 0.7], ['M', 0.85], ['L', 1]];
    var _store = null;
    function clamp(v) { return Math.max(MIN, Math.min(MAX, v)); }
    function load() {
        if (_store) return _store;
        _store = {};
        try {
            var o = JSON.parse(localStorage.getItem(KEY) || '{}');
            if (o && typeof o === 'object' && !Array.isArray(o)) _store = o;
        } catch (e) {}
        return _store;
    }
    function get(key) {
        var v = parseFloat(load()[key]);
        return (isFinite(v) && v > 0) ? clamp(v) : 1;
    }
    function _cssKey(key) { return String(key).replace(/["\\]/g, ''); }
    function panelEl(key) {
        return document.querySelector('.topo-section[data-density-panel="' + _cssKey(key) + '"]');
    }
    var _rsT = null;
    function applyPanel(key, el) {
        el = el || panelEl(key);
        if (!el) return;
        var v = get(key);
        if (v === 1) el.style.removeProperty('--topo-density-scale');
        else el.style.setProperty('--topo-density-scale', v);
        el.querySelectorAll('.density-preset[data-density-panel]').forEach(function (b) {
            b.classList.toggle('active', Math.abs(parseFloat(b.getAttribute('data-density')) - v) < 1e-6);
        });
        var sl = el.querySelector('.topo-density-pslider[data-density-panel]');
        if (sl && Math.abs(parseFloat(sl.value) - v) > 1e-6) sl.value = v;
        // The metric bar charts are the one Chip Status surface no observer
        // can serve: a size change reflows SIBLINGS without moving any outer
        // container's box (docs/123 §7). Debounced; resizeWithin hands each
        // chart's layout back untouched (docs/125 fix 5).
        clearTimeout(_rsT);
        _rsT = setTimeout(function () {
            if (window.PlotHost) { try { window.PlotHost.resizeWithin(el); } catch (e) {} }
        }, 150);
    }
    function set(key, v) {
        load()[String(key)] = clamp(v);
        try { localStorage.setItem(KEY, JSON.stringify(_store)); } catch (e) {}
        applyPanel(String(key));
    }
    // apply every remembered size under `root` (a freshly built container)
    function applyAll(root) {
        (root || document).querySelectorAll('.topo-section[data-density-panel]').forEach(function (el) {
            applyPanel(el.getAttribute('data-density-panel'), el);
        });
    }
    // the S / M / L control a builder puts beside a panel's title
    function controlHtml(key) {
        var k = _cssKey(key), v = get(k);
        var out = '<span class="topo-density-ctl topo-density-ctl-panel" title="Tile size for this panel (the numbers stay the same size)">';
        PRESETS.forEach(function (pr) {
            out += '<button type="button" class="btn-sm outline density-preset' + (Math.abs(pr[1] - v) < 1e-6 ? ' active' : '')
                + '" data-density-panel="' + k + '" data-density="' + pr[1] + '" aria-label="' + pr[0] + ' tiles">' + pr[0] + '</button>';
        });
        // the fine slider the user asked back (it sat beside S/M/L on the old Health row)
        out += '<input type="range" class="topo-density-pslider" data-density-panel="' + k + '" min="' + MIN + '" max="' + MAX
            + '" step="0.05" value="' + v.toFixed(2) + '" aria-label="Tile size (fine)" title="Tile size (fine)">';
        return out + '</span>';
    }
    function init() {
        var d = document.querySelector('.topo-dashboard');
        if (d && !d._densityBound) {
            d._densityBound = true;          // ONE delegated listener: panels are built lazily
            d.addEventListener('click', function (e) {
                var b = e.target.closest && e.target.closest('.density-preset[data-density-panel]');
                if (!b) return;
                e.preventDefault();
                set(b.getAttribute('data-density-panel'), parseFloat(b.getAttribute('data-density')));
            });
            d.addEventListener('input', function (e) {
                var sl = e.target;
                if (!sl || !sl.classList || !sl.classList.contains('topo-density-pslider')) return;
                set(sl.getAttribute('data-density-panel'), parseFloat(sl.value));
            });
        }
        applyAll(d);
    }
    return { init: init, set: set, get: get, applyAll: applyAll, controlHtml: controlHtml, presets: PRESETS };
})();

/* JumpGuard (docs/141 4o) — Trends sits above Fidelity / Coherence / … and is
   fetched lazily, so a jump to a section below it (a sidebar sub-link, ?view=)
   landed on the charts that arrived a moment later and pushed everything down.
   Remember the last jump; when Trends lands, put that section back at the top
   of the pane. A core with no DOM assumptions of its own: the caller hands it
   the selector for a view. */
window.ChipStatus.jumpGuard = (function () {
    var last = null, WINDOW_MS = 8000, armedPane = null;
    var BELOW = ['fidelity2q', 'fidelity1q', 'readout',
                 'coherence', 'frequencies', 'calibration'];   // rendered below Trends
    /* docs/141 4ac: the guard must not fight the USER. It used to re-anchor on
       nothing but the age of the jump, so a deliberate scroll made inside the
       8 s window was yanked back the moment the lazy Trends charts landed
       (measured: the user scrolled up 900 px and the pane jumped back 7 s
       later). Position cannot answer "did the user move?" -- the jump itself
       moves the pane by thousands of px, and `note()` runs BEFORE the smooth
       scroll starts. So ask for INTENT: a wheel, a touch or a key on the pane
       cancels the pending re-anchor. A smooth scrollIntoView emits none of
       those three. */
    function cancel() { last = null; }
    function arm(pane) {
        if (!pane || armedPane === pane) return;
        armedPane = pane;
        ['wheel', 'touchstart', 'keydown'].forEach(function (t) {
            pane.addEventListener(t, cancel, { passive: true });
        });
    }
    return {
        note: function (view, pane) { last = { view: view, at: Date.now() }; arm(pane); },
        below: BELOW,
        cancel: cancel,
        reanchor: function (selOf) {
            if (!last || Date.now() - last.at > WINDOW_MS) return false;
            if (BELOW.indexOf(last.view) < 0) return false;
            var sel = selOf ? selOf(last.view) : null;
            var el = sel && document.querySelector(sel);
            if (!el || !el.scrollIntoView) return false;
            el.scrollIntoView({ behavior: 'auto', block: 'start' });
            return true;
        }
    };
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
        // docs/122: PlotHost, not Plots.resize + the bare class. The old form
        // was doubly wrong here — the selector was a DESCENDANT match
        // ('.topo-metric-bar-chart .js-plotly-plot') while the chart IS the
        // .topo-metric-bar-chart element, so it selected nothing even when the
        // class survived.
        if (window.PlotHost) {
            document.querySelectorAll('.topo-metric-bar-chart')
                .forEach(function (el) { window.PlotHost.resizeWithin(el); });
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
            // docs/120 item 11: `data-hero-*` joins the selector. These
            // attributes were the CARD diagram's, so with the cards deleted
            // this marker would have matched nothing on the page and the
            // "changed vs live" highlight would have quietly stopped appearing
            // — a feature lost to a deletion rather than to a decision.
            document.querySelectorAll(
                '[data-qubit="' + _e + '"], [data-pair="' + _e + '"], '
                + '[data-hero-qubit="' + _e + '"], [data-hero-pair="' + _e + '"]'
            ).forEach(function (el) {
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
    function labelHtml(k, useAbbr, overrideText, noArrow) {
        var txt = overrideText != null ? overrideText : (useAbbr ? metricAbbr(k) : metricLabel(k));
        var ar = noArrow ? '' : arrow(k);
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
    // unphysical fit shows its raw value struck-through ("bad fit"), never a
    // heat colour. nullLabel is what a genuinely-missing value renders as
    // ('—' everywhere: cards, popups and heatmaps say absence the same way).
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

    // `range` is a flat [hi, lo] calibration pair — hi was the old "good"
    // cutoff of the retired red/amber/green table, lo the old "bad" one.
    // Review feedback: "pass/fail is ultimately just a magnitude read
    // anyway", so the tile accent is a continuous normalize-against-range
    // read on the SAME palette as the heatmap (dCfg.colorScale, Blues by
    // default, or whatever the user has picked) instead of a separate
    // hardcoded RAG scheme. interpolateColor clamps t to [0, 1].
    function cardColor(value, range) {
        if (value == null) return '#999';
        var hi = range[0], lo = range[range.length - 1];
        var t = hi === lo ? 1 : (value - lo) / (hi - lo);
        return interpolateColor(t, dCfg.colorScale);
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
    function outlierScorer(arr, opts) {
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
            isOutlier: function(v) {
                var s = this.score(v);
                if (s == null || s < _OUTLIER_K) return false;
                // docs/126 (customer report): a value that PASSES its spec is
                // never an outlier. On a tight chip the MAD collapses — every
                // 1Q fidelity within 99.85–99.92% put a 99.67% qubit at
                // 16.8× MAD, flagging an excellent result ("over
                // calculation"). No spec for the metric ⇒ a practical floor:
                // the deviation must be ≥1% of |median| to be worth a mark.
                if (opts && opts.verdict) {
                    var vr = opts.verdict(v);
                    if (vr === 'pass') return false;
                    if (vr != null) return true;   // warn/fail + statistical
                }
                return med !== 0 && Math.abs(v - med) >= 0.01 * Math.abs(med);
            }
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
        'GnBu':     {label:'GnBu',          stops:['#e0f3db','#a8ddb5','#7bccc4','#43a2ca','#0868ac']},
        'Viridis':  {label:'Viridis',       stops:['#440154','#31688e','#35b779','#90d743','#fde725']},
        'Plasma':   {label:'Plasma',        stops:['#0d0887','#7e03a8','#cc4778','#f89540','#f0f921']},
        'Inferno':  {label:'Inferno',       stops:['#000004','#57106e','#bc3754','#f98c0a','#fcffa4']},
        'Magma':    {label:'Magma',         stops:['#000004','#51127c','#b73779','#fb8761','#fcfdbf']},
        'Cividis':  {label:'Cividis',       stops:['#002051','#3d4f7c','#7b7b78','#b8a94f','#fdea45']},
        'RdYlGn':   {label:'Red-Yellow-Green',stops:['#d73027','#fc8d59','#fee08b','#91cf60','#1a9850']},
        'YlOrRd':   {label:'Yellow-Orange-Red',stops:['#ffffb2','#fecc5c','#fd8d3c','#f03b20','#bd0026']},
        'Blues':    {label:'Blues (default)',stops:['#eff3ff','#bdd7e7','#6baed6','#3182bd','#08519c']},
        'Citrus':   {label:'Citrus',        stops:['#f7fcb9','#d9f0a3','#addd8e','#78c679','#31a354']},
        'DkGreen':  {label:'Dark \u2192 Bright Green',stops:['#00230e','#0a5226','#1a8a42','#3cc264','#66ff8c']},
    };

    // Restore saved palettes, else default to the simple/modern app-blue
    // gradient (review feedback: the old GnBu default "wasn't a pretty
    // color") — pale blue (low) to navy (high), matching the app's own
    // primary blue rather than an arbitrary ColorBrewer scheme.
    var _savedPalette = null;
    try { _savedPalette = localStorage.getItem('quam_heatmap_palette'); } catch(e) {}
    var _currentPaletteName = (_savedPalette && PALETTES[_savedPalette]) ? _savedPalette : 'Blues';
    dCfg.colorScale = PALETTES[_currentPaletteName].stops;

    var _savedBarPalette = null;
    try { _savedBarPalette = localStorage.getItem('quam_bar_palette'); } catch(e) {}
    var _currentBarPaletteName = (_savedBarPalette && PALETTES[_savedBarPalette]) ? _savedBarPalette : 'Blues';
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
        {key:'assignment_fidelity_gef',fmtFn:function(v){return fmtPct(v,2);}},
        {key:'ro_fidelity_gef_g', fmtFn:function(v){return fmtPct(v,2);}},
        {key:'ro_fidelity_gef_e', fmtFn:function(v){return fmtPct(v,2);}},
        {key:'ro_fidelity_gef_f', fmtFn:function(v){return fmtPct(v,2);}},
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
        // Per-pair best of an arbitrary numeric FIELD on matching rows (used for
        // the docs/138 run-derived values: derived_gate_fidelity + the divisor).
        function collect2QField(match, field) {
            var out = [];
            topo.edges.forEach(function(e) {
                if (!e.gate_fidelities) return;
                var best = null;
                e.gate_fidelities.forEach(function(gf) {
                    if (!match(gf.metric)) return;
                    var v = typeof gf[field] === 'number' ? gf[field] : null;
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
        // docs/150: `stat` picks which aggregate the BIG number shows —
        // 'median' (the historical default), 'avg', 'min' or 'max'. The sub
        // line always states the complementary aggregates, so nothing hides;
        // a non-default stat is tagged next to the number.
        function metricTile(title, agg, fmtFn, range, metricKey, stat) {
            stat = stat || 'median';
            if (!agg || agg.count === 0) {
                return {title: title, metricKey: metricKey, value: '—', sub: 'no data', muted: true};
            }
            var sub;
            if (stat === 'avg') {
                sub = 'med ' + fmtFn(agg.median) + '  ·  ' + fmtFn(agg.min) + '–' + fmtFn(agg.max) + '  ·  (' + agg.count + ')';
            } else if (stat === 'min' || stat === 'max') {
                sub = 'med ' + fmtFn(agg.median) + '  ·  avg ' + fmtFn(agg.avg) + '  ·  (' + agg.count + ')';
            } else {
                sub = 'avg ' + fmtFn(agg.avg) + '  ·  ' + fmtFn(agg.min) + '–' + fmtFn(agg.max) + '  ·  (' + agg.count + ')';
            }
            return {
                title: title,
                metricKey: metricKey,
                stat: stat,
                value: fmtFn(agg[stat]),
                sub: sub,
                color: cardColor(agg[stat], range)
            };
        }

        // docs/150 helpers: tile identity + the stored display preferences.
        function _tid(id, tile) { if (tile) tile.id = id; return tile; }
        var ovPrefs = _ovLoad();
        function _ovStat(id) { return (ovPrefs.stats && ovPrefs.stats[id]) || 'median'; }

        // [hi, lo] calibration range for cardColor's magnitude→palette read —
        // hi was the old "good" cutoff, lo the old "bad" cutoff; the middle
        // "warn" cutoff no longer has a distinct role now that this is a
        // continuous gradient, not a 3-tier verdict.
        var fidRange = [0.99, 0];
        var roRange  = [0.97, 0];
        var tRange   = [30e-6, 0];
        // docs/150: a user-ADDED tile over any real metric key. Aggregation is
        // the same computeAggregates as every default tile; a key with no
        // known calibration range gets a neutral accent (never an invented
        // verdict), and no values renders the honest muted "no data" tile.
        function _ovFmtFor(key) {
            if (/fidelity|^ro_/.test(key)) return pct;
            var p = null;
            for (var i = 0; i < ALL_CARD_PROPS.length; i++) {
                if (ALL_CARD_PROPS[i].key === key) { p = ALL_CARD_PROPS[i]; break; }
            }
            return p ? p.fmtFn : function(v) { return fmtNum(v, 4); };
        }
        function _ovRangeFor(key) {
            if (key === 'cz_fidelity') return [0.95, 0];
            if (/^gate_fidelity/.test(key)) return fidRange;
            if (/fidelity|^ro_/.test(key)) return roRange;
            if (key === 'T1' || key === 'T2ramsey' || key === 'T2echo') return tRange;
            return null;
        }
        function _ovCustomTile(key, stat) {
            if (!key) return null;
            var agg = key === 'cz_fidelity'
                ? computeAggregates(topo.edges.map(function(e) { return e.cz_fidelity; }))
                : nodeAgg(key);
            var range = _ovRangeFor(key);
            var t = metricTile(metricLabel(key), agg, _ovFmtFor(key), range || [1, 0], key, stat || 'median');
            if (!range && !t.muted) t.color = '#76b7b2';
            return t;
        }
        // Both RB tiles state their error rates in BOTH units (user-directed,
        // docs/139 follow-up): EPC = 1 - Clifford fidelity, EPG = 1 - gate
        // fidelity. The bridge is the node's own average_gates_per_clifford,
        // read from the SRB run's data.json (docs/138 derived enrichment) -
        // never invented, so with no run on disk the converted line is absent.
        var isSRB = function(m) { return m === 'StandardRB'; };
        var isIRB = function(m) { return m === 'InterleavedRB' || m === 'IRB'; };
        var srbAgg = computeAggregates(collect2Q(isSRB));
        var srbDer = computeAggregates(collect2QField(isSRB, 'derived_gate_fidelity'));
        var divAgg = computeAggregates(collect2QField(isSRB, 'average_gates_per_clifford'));
        var irbAgg = computeAggregates(collect2Q(isIRB));
        // Per-edge Clifford-equivalent of the IRB gate error: epc = epg x divisor
        // (the same identity fidelity.py uses in the other direction).
        var irbEpcF = [];
        topo.edges.forEach(function(e) {
            if (!e.gate_fidelities) return;
            var bestF = null, div = null;
            e.gate_fidelities.forEach(function(gf) {
                if (isIRB(gf.metric)) {
                    var v = typeof gf.value === 'number' ? gf.value
                          : typeof gf.average_gate_fidelity === 'number' ? gf.average_gate_fidelity : null;
                    if (v != null && (bestF == null || v > bestF)) bestF = v;
                }
                if (typeof gf.average_gates_per_clifford === 'number') div = gf.average_gates_per_clifford;
            });
            if (bestF != null && div) irbEpcF.push(1 - (1 - bestF) * div);
        });
        var irbEpcAgg = computeAggregates(irbEpcF);

        // Four RB tiles (user-directed, docs/139 follow-up): each protocol
        // shows BOTH its measured number and the one converted through the
        // run's own divisor. EPC line on the Clifford tiles, EPG line on the
        // gate tiles, so the unit is always stated.
        var divNote = divAgg.count > 0 ? divAgg.median.toFixed(2) : null;
        function withErrLine(tile, agg, label, conv) {
            if (agg.count > 0) {
                tile.sub += '<br>' + label + ' ' + fmtPct(1 - agg.median, 2) + '%'
                          + (conv && divNote ? ' (' + conv + divNote + ')' : '');
            }
            return tile;
        }
        // -- docs/139 follow-up r2 (user-directed): four context tiles + 1Q EPG --
        // RB coverage: how many pairs the four RB tiles actually speak for.
        var rbPairs = topo.edges.filter(function(e) {
            return (e.gate_fidelities || []).some(function(gf) {
                return isSRB(gf.metric) || isIRB(gf.metric); });
        }).length;
        // 2Q gate length (ns): the edge's best gate's flux-pulse length,
        // falling back to the first gate that states one.
        var gateLens = [];
        topo.edges.forEach(function(e) {
            var gd = e.gate_details || [], pick = null;
            gd.forEach(function(g) {
                if (typeof g.length !== 'number') return;
                if (e.best_gate && g.name === e.best_gate) pick = g.length;
                else if (pick == null) pick = g.length;
            });
            if (pick != null) gateLens.push(pick);
        });
        var lenAgg = computeAggregates(gateLens);
        function nsF(v) { return (Math.round(v * 10) / 10) + ' ns'; }
        // Calibration freshness: node last_calibrated + edge cz timestamps.
        var stamps = [];
        topo.nodes.forEach(function(n) {
            if (typeof n.last_calibrated === 'number') stamps.push(n.last_calibrated); });
        topo.edges.forEach(function(e) {
            if (typeof e.cz_fidelity_updated_at === 'number') stamps.push(e.cz_fidelity_updated_at); });
        var freshTile;
        if (stamps.length) {
            var newest = Math.max.apply(null, stamps), oldest = Math.min.apply(null, stamps);
            freshTile = { id: 'cal_age', composite: true, title: 'Calibration Age', value: _ageLabel(newest),
                sub: 'oldest ' + _ageLabel(oldest) + '  ·  (' + stamps.length + ' stamped)',
                color: '#76b7b2' };
        } else {
            freshTile = { id: 'cal_age', composite: true, title: 'Calibration Age', value: '—', sub: 'no timestamps', muted: true };
        }
        // In-spec count: the SAME verdict walk buildHealthSummary does (a
        // qubit with no data is UNJUDGED, never "in spec"; a null-valued
        // MetricRecord never counts as below spec).
        var SPEC_METRICS = ['gate_fidelity_avg', 'assignment_fidelity', 'T1', 'T2ramsey', 'T2echo'];
        var below = {}, specMeasured = 0;
        topo.nodes.forEach(function(n) {
            var any = false;
            SPEC_METRICS.forEach(function(m) {
                var rec = n.metrics && n.metrics[m];
                var v = rec ? rec.value : n[m];
                if (typeof v === 'number' && isFinite(v)) any = true;
                var vr = _verdict(v, thresholds[m]);
                if (vr === 'fail') below[n.id] = 'fail';
                else if (vr === 'warn' && below[n.id] !== 'fail') below[n.id] = 'warn';
            });
            if (any) specMeasured++;
        });
        var belowN = Object.keys(below).length;
        var failN = Object.keys(below).filter(function(k) { return below[k] === 'fail'; }).length;
        var warnN = belowN - failN;
        var unjudged = topo.nodes.length - specMeasured;
        var specTile;
        if (specMeasured > 0) {
            specTile = { id: 'in_spec', composite: true, title: 'Qubits In Spec',
                value: (specMeasured - belowN) + '/' + specMeasured,
                sub: warnN + ' warn  ·  ' + failN + ' fail'
                   + (unjudged ? '  ·  ' + unjudged + ' unjudged' : ''),
                color: failN ? '#e15759' : (warnN ? '#f28e2b' : '#59a14f') };
        } else {
            specTile = { id: 'in_spec', composite: true, title: 'Qubits In Spec', value: '—', sub: 'no data', muted: true };
        }

        var srbTile = _tid('srb', withErrLine(
            metricTile('2Q Clifford fid. (SRB)', srbAgg, pct, fidRange, null, _ovStat('srb')), srbAgg, 'EPC', null));
        var srbGateTile = _tid('srb_gate', withErrLine(
            metricTile('2Q gate fid. (SRB÷)', srbDer, pct, fidRange, null, _ovStat('srb_gate')), srbDer, 'EPG', '÷'));
        var irbTile = _tid('irb', withErrLine(
            metricTile('2Q gate fid. (IRB)', irbAgg, pct, fidRange, null, _ovStat('irb')), irbAgg, 'EPG', null));
        var irbCliffTile = _tid('irb_cliff', withErrLine(
            metricTile('2Q Clifford fid. (IRB×)', irbEpcAgg, pct, fidRange, null, _ovStat('irb_cliff')), irbEpcAgg, 'EPC', '×'));

        var tiles = [
            {id: 'chip_size', composite: true, title: 'Chip Size', value: topo.nodes.length + ' qubits, ' + topo.edges.length + ' pairs', color: '#4e79a7'},
            // gate_fidelity.averaged IS per-gate: the lab's 1Q RB node stores
            // 1 - error_per_gate (27_single_qubit_randomized_benchmarking.py).
            _tid('gate1q', withErrLine(metricTile('1Q Gate Fidelity', nodeAgg('gate_fidelity_avg'), pct, fidRange, 'gate_fidelity_avg', _ovStat('gate1q')),
                        nodeAgg('gate_fidelity_avg'), 'EPG', null)),
            _tid('ro_ge', metricTile('Readout Fidelity (GE)', nodeAgg('assignment_fidelity'), pct, roRange, 'assignment_fidelity', _ovStat('ro_ge'))),
            // three-state readout, only on a chip that measured it (no permanent "no data" tile)
            (nodeAgg('assignment_fidelity_gef').count > 0
                ? _tid('ro_gef', metricTile('Readout Fidelity (GEF)', nodeAgg('assignment_fidelity_gef'), pct, roRange, 'assignment_fidelity_gef', _ovStat('ro_gef')))
                : null),
            // Standard RB fits the CLIFFORD fidelity (1-EPC); interleaved RB
            // fits the GATE fidelity (1-EPG). A Clifford is ~5.4 two-qubit
            // gates here, so the titles say which is which and each tile also
            // states both error rates, bridged only by the run's own divisor.
            srbTile, srbGateTile, irbCliffTile, irbTile,
            // The edge number. Its SOURCE varies by chip — Bell_State, an
            // interleaved-RB gate fidelity, or the CR channel — so the tile is
            // named for what it measures, not for one of the three ways it can
            // be measured. Calling it "2Q Bell" was already wrong on a CR chip
            // and became wrong on an RB chip too (docs/138).
            // user 2026-09-01: "(Best)" instead of the direction arrow -- the
            // per-edge number IS the best of the pair's candidate gates.
            (function() { var t = metricTile('2Q gate fidelity (Best)', computeAggregates(topo.edges.map(function(e) { return e.cz_fidelity; })), pct, [0.95, 0], 'cz_fidelity', _ovStat('gate2q'));
                          t.noArrow = true; return _tid('gate2q', t); })(),
            // Length has no good/bad direction - neutral colour, not a verdict.
            (function() { var t = metricTile('2Q Gate Length', lenAgg, nsF, [1, 0], null, _ovStat('gate2q_len'));
                          if (lenAgg.count > 0) t.color = '#b07aa1'; return _tid('gate2q_len', t); })(),
            {id: 'rb_cov', composite: true, title: 'RB Coverage',
             value: rbPairs + '/' + topo.edges.length,
             sub: 'pairs with an RB measurement', color: '#4e79a7',
             muted: topo.edges.length === 0},
            specTile,
            freshTile,
            _tid('t1', metricTile('T1', nodeAgg('T1'), us, tRange, 'T1', _ovStat('t1'))),
            _tid('t2ramsey', metricTile('T2 Ramsey', nodeAgg('T2ramsey'), us, tRange, 'T2ramsey', _ovStat('t2ramsey')))
        ];

        // docs/150: apply the stored preferences -- drop removed tiles, then
        // append user-added ones. With nothing stored this is a no-op.
        tiles = tiles.filter(Boolean).filter(function(t) {
            return !(t.id && ovPrefs.removed.indexOf(t.id) >= 0);
        });
        ovPrefs.added.forEach(function(a, i) {
            var t = _ovCustomTile(a.key, a.stat);
            if (t) { t.id = 'custom:' + i; t.custom = true; tiles.push(t); }
        });

        var html = '';
        tiles.forEach(function(c) {
            var border = c.muted ? 'var(--pico-muted-border-color)' : (c.color || 'var(--pico-muted-border-color)');
            var titleHtml = c.metricKey ? labelHtml(c.metricKey, false, c.title, c.noArrow) : _esc(c.title);
            var statTag = (c.stat && c.stat !== 'median')
                ? ' <span class="ov-stat-tag">' + _esc(c.stat) + '</span>' : '';
            html += '<div class="topo-card' + (c.muted ? ' topo-card-empty' : '') + '"'
                  + (c.id ? ' data-tile-id="' + _esc(c.id) + '"' : '')
                  + (c.composite ? ' data-tile-composite="1"' : '')
                  + ' style="border-top-color:' + border + '">'
                  + (c.id ? '<button type="button" class="ov-tile-menu" data-tile-id="' + _esc(c.id) + '" title="Customize this panel" aria-label="Customize this panel">\u22ee</button>' : '')
                  + '<div class="topo-card-title">' + titleHtml + '</div>'
                  + '<div class="topo-card-value">' + c.value + statTag + '</div>'
                  + (c.sub ? '<div class="topo-card-sub">' + c.sub + '</div>' : '')
                  + '</div>';
        });
        html += '<div class="topo-card ov-add-tile" id="ov-add-tile" role="button" tabindex="0" title="Add a panel over any metric">+ Add panel</div>';
        container.innerHTML = html;
        _ovRefreshNote(ovPrefs);
        _ovWire(container);
    }

    // ── docs/150: Overview customization plumbing (storage + popover) ──────
    // Display preferences ONLY: which tiles show, and which aggregate each
    // big number states. The statistics themselves are the untouched
    // computeAggregates outputs. v2 follow-ups (custom expressions,
    // server-side save/share, drag reorder) are documented in docs/150.
    var OV_PREFS_KEY = 'quam_overview_tiles_v1';
    function _ovLoad() {
        try {
            var p = JSON.parse(localStorage.getItem(OV_PREFS_KEY) || '{}') || {};
            return { removed: p.removed || [], stats: p.stats || {}, added: p.added || [] };
        } catch (e) { return { removed: [], stats: {}, added: [] }; }
    }
    function _ovSave(p) {
        try {
            if (!_ovCustomized(p)) localStorage.removeItem(OV_PREFS_KEY);
            else localStorage.setItem(OV_PREFS_KEY, JSON.stringify(p));
        } catch (e) {}
    }
    function _ovCustomized(p) {
        return !!((p.removed && p.removed.length) || (p.stats && Object.keys(p.stats).length)
                  || (p.added && p.added.length));
    }
    function _ovRefreshNote(prefs) {
        var n = document.getElementById('ov-custom-note');
        if (n) n.hidden = !_ovCustomized(prefs);
    }
    function _ovAvailableKeys() {
        var seen = {};
        (topo.nodes || []).forEach(function(n) {
            Object.keys(n.metrics || {}).forEach(function(k) { seen[k] = 1; });
        });
        if ((topo.edges || []).length) seen['cz_fidelity'] = 1;
        return Object.keys(seen).sort();
    }
    function _ovWire(container) {
        if (container.dataset.ovWired) return;
        container.dataset.ovWired = '1';
        container.addEventListener('click', function(ev) {
            var btn = ev.target.closest ? ev.target.closest('.ov-tile-menu') : null;
            if (btn) { ev.stopPropagation(); _ovOpenPopover(btn, btn.getAttribute('data-tile-id')); return; }
            var add = ev.target.closest ? ev.target.closest('#ov-add-tile') : null;
            if (add) { ev.stopPropagation(); _ovOpenPopover(add, null); }
        });
    }
    function _ovDocClose(ev) {
        var p = document.getElementById('ov-tile-popover');
        if (p && !p.contains(ev.target)) _ovClosePopover();
    }
    function _ovClosePopover() {
        var p = document.getElementById('ov-tile-popover');
        if (p) p.remove();
        document.removeEventListener('mousedown', _ovDocClose, true);
    }
    window._ovResetTiles = function() {
        _ovSave({ removed: [], stats: {}, added: [] });
        _ovClosePopover();
        buildOverviewTiles();
    };
    function _ovOpenPopover(anchor, tileId) {
        _ovClosePopover();
        var prefs = _ovLoad();
        var isAdd = tileId == null;
        var isCustom = !!tileId && tileId.indexOf('custom:') === 0;
        var customIdx = isCustom ? parseInt(tileId.slice(7), 10) : -1;
        var tileEl = anchor.closest ? anchor.closest('.topo-card') : null;
        var composite = !!(tileEl && tileEl.getAttribute('data-tile-composite'));
        var curKey = isCustom ? ((prefs.added[customIdx] || {}).key || '') : '';
        var curStat = isAdd ? 'median'
                    : isCustom ? ((prefs.added[customIdx] || {}).stat || 'median')
                    : ((prefs.stats || {})[tileId] || 'median');
        var keyOpts = _ovAvailableKeys().map(function(k) {
            return '<option value="' + _esc(k) + '"' + (k === curKey ? ' selected' : '') + '>'
                 + _esc(metricLabel(k)) + '</option>';
        }).join('');
        var statOpts = ['median', 'avg', 'min', 'max'].map(function(s) {
            return '<option value="' + s + '"' + (s === curStat ? ' selected' : '') + '>' + s + '</option>';
        }).join('');
        var body = '';
        if (composite) {
            body += '<p class="muted ov-pop-note">Computed tile \u2014 its number is not one metric, so key/statistic cannot change here.</p>';
        } else {
            if (isAdd || isCustom) {
                body += '<label>Metric<select id="ov-pop-key">' + keyOpts + '</select></label>';
            }
            body += '<label>Statistic<select id="ov-pop-stat">' + statOpts + '</select></label>';
        }
        body += '<div class="ov-pop-actions">'
             + (isAdd ? '<button type="button" class="btn-sm" id="ov-pop-add">Add panel</button>'
                      : '<button type="button" class="btn-sm outline" id="ov-pop-remove">Remove panel</button>')
             + '<button type="button" class="btn-sm outline" id="ov-pop-reset"' + (_ovCustomized(prefs) ? '' : ' hidden') + '>Reset all</button>'
             + '</div>';
        var pop = document.createElement('div');
        pop.id = 'ov-tile-popover';
        pop.innerHTML = body;
        document.body.appendChild(pop);
        var r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { bottom: 0, left: 0 };
        pop.style.top = Math.max(4, Math.min(r.bottom + 4, (window.innerHeight || 800) - (pop.offsetHeight || 120) - 8)) + 'px';
        pop.style.left = Math.max(8, Math.min(r.left, (window.innerWidth || 1200) - (pop.offsetWidth || 240) - 8)) + 'px';

        function apply(fn) { fn(); _ovSave(prefs); _ovClosePopover(); buildOverviewTiles(); }
        var statSel = pop.querySelector('#ov-pop-stat');
        if (statSel && !isAdd) {
            statSel.addEventListener('change', function() {
                var v = statSel.value;
                apply(function() {
                    if (isCustom) { if (prefs.added[customIdx]) prefs.added[customIdx].stat = v; }
                    else if (v === 'median') delete prefs.stats[tileId];
                    else prefs.stats[tileId] = v;
                });
            });
        }
        var keySel = pop.querySelector('#ov-pop-key');
        if (keySel && isCustom) {
            keySel.addEventListener('change', function() {
                var v = keySel.value;
                apply(function() { if (prefs.added[customIdx]) prefs.added[customIdx].key = v; });
            });
        }
        var addBtn = pop.querySelector('#ov-pop-add');
        if (addBtn) {
            addBtn.addEventListener('click', function() {
                var k = keySel ? keySel.value : '';
                var s = statSel ? statSel.value : 'median';
                if (!k) { _ovClosePopover(); return; }
                apply(function() { prefs.added.push({ key: k, stat: s }); });
            });
        }
        var rmBtn = pop.querySelector('#ov-pop-remove');
        if (rmBtn) {
            rmBtn.addEventListener('click', function() {
                apply(function() {
                    if (isCustom) prefs.added.splice(customIdx, 1);
                    else if (prefs.removed.indexOf(tileId) < 0) prefs.removed.push(tileId);
                    if (!isCustom) delete prefs.stats[tileId];
                });
            });
        }
        var rsBtn = pop.querySelector('#ov-pop-reset');
        if (rsBtn) rsBtn.addEventListener('click', function() { window._ovResetTiles(); });
        document.addEventListener('mousedown', _ovDocClose, true);
    }

    // ══════════════════════════════════════════════════════════════════
    // Section 2: HTML/SVG Topology with always-visible property cards
    // ══════════════════════════════════════════════════════════════════

    // ONE source for how an edge's CZ state colours a topology line — read by
    // BOTH the card diagram and the hero map so they can never disagree.
    function _edgePaint(e) {
        var color = tCfg.edgeFidelityNone, width = 2;
        if (e.has_cz && e.cz_fidelity != null) {
            color = e.cz_fidelity >= 0.95 ? tCfg.edgeFidelityGood
                  : (e.cz_fidelity >= 0.85 ? tCfg.edgeFidelityWarn : tCfg.edgeFidelityBad);
            width = 3;
        }
        return { color: color, width: width };
    }

    // Shared qubit-popup bridge: buildTopology owns the ONE popup
    // implementation (property rows, sparklines, pinning, document-level
    // teardown); the hero map opens the same popup through this handle —
    // never a second implementation.
    var _sharedQubitPopup = null;

    /* ── The qubit detail popup (docs/120 item 11) ────────────────────────
     *
     * This used to be the tail of buildTopology, the pre-hero CARD diagram
     * that rendered a second chip map underneath the hero one. The customer
     * reported the duplication ("the qubit layout appears twice ... why does
     * the first one exist?") and preferred the hero, so the cards are gone.
     *
     * The popup could NOT go with them. `_sharedQubitPopup` is what the hero
     * opens on hover, and it was only ever ASSIGNED inside that IIFE — so
     * deleting the block wholesale would have left it null forever and the
     * hero's hover popup would have stopped opening with NO error and NO
     * failing test (bindHover simply guards on it). It lives here now, owning
     * nothing but itself: the single activePopup, its positioning, its
     * document-click and htmx:beforeSwap teardown, and openQubitMore.
     *
     * `positionPopup` still prefers a `.topo-node-card` ancestor when one
     * exists and falls back to the element it was handed, which is what makes
     * it work unchanged for the hero's circular nodes.
     */
    (function buildQubitPopup() {
        if (!topo || !topo.nodes || !topo.nodes.length) return;

        // ── Popup management ─────────────────────────────────────────
        var activePopup = null;
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
                // '—', not Python's None spelled out — see the heatmap note.
                html += _propRowHtml(n, p, '—');
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


        // ── Pair detail popup (docs/126 ②) ───────────────────────────
        // Same singleton + positioning + teardown as the qubit popup; the
        // pair flavor renders the edge payload the topology already carries
        // (2Q fidelities per gate, detuning, coupler offsets, roles).
        function openPairPopup(e, anchorEl, pinned) {
            closePopup();
            var popup = document.createElement('div');
            popup.className = 'topo-pair-popup';
            function pct(v) { return (v * 100).toFixed(2) + '%'; }
            function row(label, val, win) {
                return '<div class="topo-popup-row' + (win ? ' topo-popup-row-win' : '') + '">'
                     + '<span class="topo-popup-row-label">' + label + '</span>'
                     + '<span>' + val + '</span></div>';
            }
            var kind = e.gate_kind === 'cr' ? 'CR' : (e.gate_kind === 'cz' ? 'CZ' : '');
            var html = '<div class="topo-popup-header"><span>' + _esc(e.pair_id)
                + (kind ? ' <span class="topo-popup-kind">' + kind + '</span>' : '')
                + (e.active === false ? ' <span class="topo-popup-kind topo-popup-off">off</span>' : '')
                + '</span><button class="topo-popup-close">✕</button></div>';

            // docs/138 — these rows do NOT all measure the same thing, and
            // stacking them under one "Gate fidelity" heading said they did.
            // StandardRB is 1-EPC (per CLIFFORD, ~1.5 gates); InterleavedRB is
            // 1-EPG (per GATE); *_alpha is the RB decay base, not a fidelity at
            // all — it was rendering as "93.8%" beside two real fidelities.
            var fidRows = '';
            var fitRows = '';
            (e.gate_fidelities || []).forEach(function(gf) {
                var v = typeof gf.value === 'number' ? gf.value
                      : typeof gf.average_gate_fidelity === 'number' ? gf.average_gate_fidelity : null;
                if (v == null) return;
                var name = _esc(gf.gate) + ' · ' + _esc(gf.metric);
                if (gf.level === 'decay') {
                    // A decay base is a bare number, never a percentage.
                    fitRows += row(name, v.toFixed(5));
                    return;
                }
                if (gf.level === 'clifford') name += ' <span class="topo-popup-kind">per Clifford</span>';
                else if (gf.level === 'gate') name += ' <span class="topo-popup-kind">per gate</span>';
                var shown = pct(v);
                // docs/138 — the per-GATE number this Clifford fit implies. The
                // run computed it (epc / average_gates_per_clifford) and stored
                // only the Clifford one; SM read it back out of that run. Shown
                // BESIDE the Clifford value, never instead of it: they are two
                // different quantities and the divisor belongs on screen.
                if (typeof gf.derived_gate_fidelity === 'number') {
                    shown += ' <span class="topo-popup-kind">→ ' + pct(gf.derived_gate_fidelity)
                          + ' per gate'
                          + (typeof gf.average_gates_per_clifford === 'number'
                              ? ' ÷' + gf.average_gates_per_clifford.toFixed(2) : '')
                          + '</span>';
                }
                fidRows += row(name, shown,
                               e.best_gate && gf.gate === e.best_gate);
            });
            if (!fidRows && e.cz_fidelity != null) fidRows = row('2Q fidelity', pct(e.cz_fidelity));
            if (fidRows) {
                html += '<div class="topo-popup-section">'
                     + '<div class="topo-popup-section-title">Gate fidelity</div>' + fidRows;
                if (typeof e.cz_fidelity_updated_at === 'number') {
                    html += '<div class="topo-popup-recency"><span class="topo-recency '
                         + _ageClass(e.cz_fidelity_updated_at) + '">measured '
                         + _ageLabel(e.cz_fidelity_updated_at) + '</span></div>';
                }
                html += '</div>';
            }
            // A separate section, because these are fit parameters rather than
            // measurements of the gate. Shown even when no fidelity row
            // survived: an alpha with no fidelity beside it is worth seeing.
            if (fitRows) {
                html += '<div class="topo-popup-section">'
                     + '<div class="topo-popup-section-title">RB fit (decay α)</div>'
                     + fitRows + '</div>';
            }

            var parRows = '';
            if (typeof e.detuning === 'number') parRows += row('detuning', fmt(e.detuning, 'MHz'));
            if (typeof e.mutual_flux_bias === 'number') parRows += row('mutual flux bias', e.mutual_flux_bias);
            if (e.has_coupler && typeof e.coupler_decouple_offset === 'number') {
                parRows += row('coupler decouple offset', e.coupler_decouple_offset.toFixed(4) + ' V');
            }
            if (e.moving_qubit) {
                parRows += row('moving qubit (M)', e.moving_qubit === 'control'
                               ? 'control — ' + _esc(e.source) : 'target — ' + _esc(e.target));
            }
            if (e.confusion_size) {
                parRows += row('confusion', e.confusion_size + '×' + e.confusion_size
                               + (e.confusion_diag ? ' (diag ' + e.confusion_diag.map(function(d) {
                                     return (d * 100).toFixed(1) + '%'; }).join(' / ') + ')' : ''));
            }
            if (parRows) {
                html += '<div class="topo-popup-section">'
                     + '<div class="topo-popup-section-title">Parameters</div>' + parRows + '</div>';
            }
            if (!fidRows && !parRows) {
                html += '<div class="topo-popup-section muted">no recorded pair data</div>';
            }

            popup.innerHTML = html;
            popup.querySelector('.topo-popup-close').addEventListener('click', function(ev) {
                ev.stopPropagation(); closePopup();
            });
            popup._pinned = !!pinned;
            popup.addEventListener('mouseenter', function() { clearTimeout(_moreLeaveTimer); });
            popup.addEventListener('mouseleave', _scheduleMoreClose);
            activePopup = popup;
            positionPopup(popup, anchorEl);
        }

        // Hand the ONE popup implementation to the hero map (see the bridge
        // declaration above buildTopology) — same singleton, same teardown.
        _sharedQubitPopup = {
            open: openQubitMore,
            openPair: openPairPopup,
            scheduleClose: _scheduleMoreClose,
            cancelClose: function() { clearTimeout(_moreHoverTimer); clearTimeout(_moreLeaveTimer); },
        };
    })();

    // ══════════════════════════════════════════════════════════════════
    // Section 2-hero: the chip map (docs/92 P1). Geometry + honesty mode come
    // from TopoGraph.layoutFor (docs/91 §2.1): 'physical' only when the chip
    // declares every position; 'logical' from pair connectivity, labelled ON
    // the map (LOGICAL_LAYOUT_NOTE); 'none' -> one honest line, no map. Node
    // fill = the selected metric through the SAME physical gate + palette as
    // the heatmaps (_mv / propBgColor / nullCellColor), edges through the same
    // _edgePaint as the card diagram — the map can never disagree with the
    // cards below it. Numbers stay ON the map (docs/91 §2.4: THIS surface
    // integrates values; the component-page maps deliberately carry none).
    // ══════════════════════════════════════════════════════════════════
    var _rebuildHeroMap = null;   // set by buildHeroMap; palette switch re-renders
    (function buildHeroMap() {
        var host = document.getElementById('topo-hero');
        if (!host || !window.TopoGraph) return;
        var TG = window.TopoGraph;
        var lay = TG.layoutFor(topo.nodes, topo.edges);
        if (lay.mode === 'none') {
            host.innerHTML = '<p class="muted topo-hero-none">No chip map — this chip declares no positions and no pairs.</p>';
            return;
        }

        // docs/138 — versioned, deliberately. The old default on a chip with
        // no Bell_State was the CLIFFORD number, under the label "2Q RB",
        // which did not say which of the two things it was. Nobody chose that
        // on purpose; they got it. A stored key would otherwise pin the wrong
        // number forever for exactly the people who never picked it. Same
        // precedent as the `_cols_v2` visibility flip (docs/85): bump the key
        // so a corrected default reaches everyone, and a choice made AFTER
        // seeing the honest label survives from then on.
        var HERO_KEY = 'quam_topo_hero_metric_v2';
        var ZOOM_KEY = 'quam_topo_hero_zoom';
        // docs/126 ② — the metric patches ARE the map's controls: clicking one
        // paints the value inside every stone (or, for a 2Q metric, ON every
        // edge) immediately, no scrolling. Frequencies join the row, and 2Q
        // metrics become first-class EDGE metrics (scope:'edge').
        var METRICS = [
            // Customer ask (2026-08-27): the freq patch alone said too little —
            // anharmonicity matters as much when scanning a chip, so it rides
            // under f01 as a smaller MHz sub-line (α = f12 − f01).
            { key: 'f_01',                label: 'Qubit freq',
              fmtFn: function(v) { return fmt(v, 'GHz'); },
              subKey: 'anharmonicity',
              subFmt: function(v) { return 'α ' + fmt(v, 'MHz'); },
              // Customer ask (2026-08-27, round 2): every pair edge carries
              // the two stones' frequency difference — Δf, the number the
              // docs/93 chevron only encodes as an inequality.
              edgeDelta: { key: 'f_01', label: 'Δf' } },
            { key: 'readout_frequency',   label: 'Readout freq',
              fmtFn: function(v) { return fmt(v, 'GHz'); } },
            { key: 'gate_fidelity_avg',   fmtFn: function(v) { return fmtPct(v, 2) + '%'; } },
            { key: 'assignment_fidelity', fmtFn: function(v) { return fmtPct(v, 2) + '%'; } },
            { key: 'assignment_fidelity_gef', fmtFn: function(v) { return fmtPct(v, 2) + '%'; } },
            { key: 'T1',                  fmtFn: function(v) { return fmt(v, 'us'); } },
            { key: 'T2echo',              fmtFn: function(v) { return fmt(v, 'us'); } },
        ].filter(function(m) {
            return topo.nodes.some(function(n) { return _mv(n, m.key) != null; });
        });

        // Per-edge value for a named 2Q RB metric (the same per-pair-best +
        // physical (0,1] gate buildOverviewTiles' collect2Q applies). docs/126:
        // RB numbers exist PER GATE (cz_flattop's RB vs cz_gaussian_bipolar's
        // …), so the caller can pin one gate — 'best' keeps the old best-of.
        function edgeBest2Q(e, match, gate) {
            if (!e.gate_fidelities) return null;
            var best = null;
            e.gate_fidelities.forEach(function(gf) {
                if (!match(gf.metric)) return;
                if (gate && gate !== 'best' && gf.gate !== gate) return;
                var v = typeof gf.value === 'number' ? gf.value
                      : typeof gf.average_gate_fidelity === 'number'
                          ? gf.average_gate_fidelity : null;
                if (v != null && v > 0 && v <= 1 && (best == null || v > best)) best = v;
            });
            return best;
        }
        // docs/138 — the derived per-gate value on a Clifford row, per pulse.
        function edgeBestDerived(e, gate) {
            if (!e.gate_fidelities) return null;
            var best = null;
            e.gate_fidelities.forEach(function (gf) {
                if (gf.level !== 'clifford') return;
                if (gate && gate !== 'best' && gf.gate !== gate) return;
                var v = gf.derived_gate_fidelity;
                if (typeof v === 'number' && v > 0 && v <= 1 && (best == null || v > best)) {
                    best = v;
                }
            });
            return best;
        }
        function gatesFor2Q(match) {
            var names = {};
            topo.edges.forEach(function(e) {
                (e.gate_fidelities || []).forEach(function(gf) {
                    if (match(gf.metric) && gf.gate) names[gf.gate] = 1;
                });
            });
            return Object.keys(names).sort();
        }
        var GATE_KEY = 'quam_topo_hero_gate';
        var heroGate = 'best';
        try { heroGate = localStorage.getItem(GATE_KEY) || 'best'; } catch (e) {}
        var EDGE_METRICS = [
            // Same rename as the tile: this is the edge's gate number, and
            // its source is Bell_State / interleaved RB / the CR channel
            // depending on the chip (docs/138).
            { key: 'cz_fidelity', scope: 'edge', label: '2Q gate fid.',
              fmtFn: function(v) { return fmtPct(v, 1) + '%'; },
              valFn: function(e) { return _mv(e, 'cz_fidelity'); } },
            // ORDER IS THE DEFAULT. The first surviving metric is what the
            // hero opens on, and on a chip with no Bell_State the old order
            // made that the CLIFFORD number: the map said 97.1% while the
            // per-pulse sections below it said 99.09% and 99.34%. Same pair,
            // two numbers, and the smaller one on top. The gate number goes
            // first now; the Clifford number stays available, last, and says
            // what it is (docs/138).
            { key: 'rb2q_interleaved', scope: 'edge', label: '2Q gate (IRB)', perGate: true,
              match: function(m) { return m === 'InterleavedRB' || m === 'IRB'; },
              fmtFn: function(v) { return fmtPct(v, 1) + '%'; },
              valFn: function(e) { return edgeBest2Q(e, function(m) { return m === 'InterleavedRB' || m === 'IRB'; }, heroGate); } },
            // The per-gate number a Standard-RB fit implies, recovered from
            // that run's own data.json. Present only when the run folder is
            // loaded — otherwise the patch simply is not offered, rather than
            // showing a Clifford number under a gate label.
            { key: 'rb2q_standard_gate', scope: 'edge', label: '2Q gate (SRB÷)', perGate: true,
              match: function(m) { return m === 'StandardRB'; },
              fmtFn: function(v) { return fmtPct(v, 1) + '%'; },
              valFn: function(e) { return edgeBestDerived(e, heroGate); } },
            { key: 'rb2q_standard', scope: 'edge', label: '2Q Clifford (SRB)', perGate: true,
              match: function(m) { return m === 'StandardRB'; },
              fmtFn: function(v) { return fmtPct(v, 1) + '%'; },
              valFn: function(e) { return edgeBest2Q(e, function(m) { return m === 'StandardRB'; }, heroGate); } },
        ].filter(function(m) {
            return topo.edges.some(function(e) { return m.valFn(e) != null; });
        });
        METRICS = METRICS.concat(EDGE_METRICS);
        if (topo.nodes.some(function(n) { return n.last_calibrated != null; })) {
            METRICS.push({ key: 'last_calibrated', mode: 'age', label: 'Last calibrated' });
        }
        METRICS.push({ key: 'diag', mode: 'diag', label: 'Diagnostics' });

        var current = null;
        try { current = localStorage.getItem(HERO_KEY); } catch (e) {}
        if (!METRICS.some(function(m) { return m.key === current; })) current = METRICS[0].key;

        // Map zoom — a multiplier over the pane-fit width (docs/126 ②: "make
        // the whole topology ~2× bigger"). 1 = fit-to-pane. The scroll
        // container owns the overflow, so a zoomed or tall map scrolls INSIDE
        // the section instead of pushing the page around. The default is
        // resolved on first render (needs the map's W/H + the pane width).
        var zoom = null;
        try {
            var zRaw = parseFloat(localStorage.getItem(ZOOM_KEY));
            if (zRaw >= 0.25 && zRaw <= 4) zoom = zRaw;   // floor 0.5 -> 0.25 (user-directed)
        } catch (e) {}
        // docs/126: compact mode trades stone/marker footprint for TEXT — the
        // small-monitor answer. Fonts grow relative to the cell (CSS on
        // .hero-compact) so a zoomed-out whole-chip view keeps readable
        // numbers; the half-overlapped role markers free the edge middles.
        var COMPACT_KEY = 'quam_topo_hero_compact';
        var compactMode = false;
        try { compactMode = localStorage.getItem(COMPACT_KEY) === '1'; } catch (e) {}

        // Per-qubit open findings, attributed by jump_path (same address
        // grammar as liveDiff's _entityOf). Pair findings are out of scope
        // here — node fill only.
        var diagBy = {};
        (diagFindings || []).forEach(function(f) {
            var p = String(f.jump_path || '').split('.');
            var id = null;
            if (p[0] === 'qubits' && p[1]) id = p[1];
            else if (p[0] === 'wiring' && p[1] === 'qubits' && p[2]) id = p[2];
            if (!id) return;
            var b = diagBy[id] = diagBy[id] || { error: 0, warning: 0, total: 0 };
            if (f.severity === 'error') b.error++;
            else if (f.severity === 'warning') b.warning++;
            b.total++;
        });

        function _metric(key) {
            for (var i = 0; i < METRICS.length; i++) if (METRICS[i].key === key) return METRICS[i];
            return METRICS[0];
        }

        // -> {fill?, fg?, cls?, text, title} for one node under one metric.
        // Continuous metrics ride propBgColor (chip-relative normalize, the
        // active palette, nullCellColor for missing); age + diagnostics ride
        // the app's pass/warn/fail classes. A bad fit is shown distinctly.
        function nodePaint(n, key) {
            var m = _metric(key);
            if (m.mode === 'diag') {
                var c = diagBy[n.id];
                if (!c || !c.total) return { cls: 'hs-pass', text: '✓', title: 'no open findings' };
                return { cls: c.error ? 'hs-fail' : (c.warning ? 'hs-warn' : 'hs-pass'),
                         text: String(c.total), title: c.total + ' open finding(s) — see Diagnostics' };
            }
            if (m.mode === 'age') {
                var ms = n.last_calibrated;
                if (ms == null) return { fill: dCfg.nullCellColor, fg: 'var(--pico-muted-color)',
                                         text: '—', title: 'no calibration timestamps' };
                var ac = _ageClass(ms);
                if (!ac) return { fill: dCfg.nullCellColor, fg: 'var(--pico-muted-color)', text: '—', title: '' };
                return { cls: 'hs-' + ac, text: _ageLabel(ms), title: 'last calibrated ' + _ageLabel(ms) };
            }
            if (_badFit(n, m.key)) {
                return { fill: dCfg.nullCellColor, fg: 'var(--pico-muted-color)', cls: 'hs-badfit',
                         text: m.fmtFn(n.metrics[m.key].raw),
                         title: 'unphysical (likely a failed fit) — excluded from stats & colour' };
            }
            var v = _mv(n, m.key);
            var c2 = propBgColor({ key: m.key }, v);
            // The metric's sub-value (e.g. anharmonicity under f01), read
            // through the SAME physical gate as every displayed number — a
            // missing or unphysical value renders nothing, never a dash.
            // (_mv alone is the gate: it returns the record's QUARANTINED
            // value, which is already null for a bad fit.)
            var sub = null;
            if (m.subKey) {
                var sv = _mv(n, m.subKey);
                if (sv != null) sub = m.subFmt(sv);
            }
            return { fill: c2.bg, fg: c2.fg, text: v == null ? '—' : m.fmtFn(v),
                     sub: sub,
                     title: v == null ? 'no data' : '' };
        }

        // docs/120 item 11: the hero is now the ONLY chip map on this page, so
        // it gets the room the card diagram used to take. The cell grew with
        // it (96 -> 132) — every glyph, the id, the metric value and the new
        // role markers all scale off CELL, so one number widens the whole map
        // rather than each piece needing its own bump. The rendered size is
        // responsive (see .topo-hero-svg): these are the intrinsic dimensions
        // and the viewBox aspect, which CSS then fits to the pane.
        var CELL = 132;
        var W = Math.max(CELL, Math.round(lay.cols * CELL));
        var H = Math.max(CELL, Math.round(lay.rows * CELL));
        function cx(p) { return (p.col + 0.5) * CELL; }
        function cy(p) { return (p.row + 0.5) * CELL; }

        // Coincident DECLARED cells happen on real chips (a 10Q chip declares
        // q2 and q10 both at "4,0"): fan the stones around the shared centre so
        // BOTH stay visible — TopoGraph.spreadCoincident is the ONE fan-out
        // (the component maps use the same). Dashed ring marks shared members.
        var offsets = {};
        (function() {
            var offC = TG.spreadCoincident(lay.positions, 0.22);
            Object.keys(offC).forEach(function(id) {
                offsets[id] = { dx: offC[id].dx * CELL, dy: offC[id].dy * CELL, shared: true };
            });
        })();

        function _edgeAgg(m) {
            return computeAggregates(topo.edges.map(function(e) { return m.valFn(e); }));
        }

        function legendHtml(key) {
            var m = _metric(key);
            var out = '<div class="topo-hero-legend">';
            if (m.scope === 'edge') {
                var eagg = _edgeAgg(m);
                var estops = dCfg.colorScale;
                out += '<span class="topo-hero-lg-item">' + (eagg.min != null ? m.fmtFn(eagg.min) : '')
                     + '<span class="topo-hero-lg-grad" style="background:linear-gradient(90deg,' + estops.join(',') + ')"></span>'
                     + (eagg.max != null ? m.fmtFn(eagg.max) : '') + '</span>'
                     + '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch" style="background:'
                     + dCfg.nullCellColor + '"></span>no data</span>';
                return out + '</div>';
            }
            if (m.mode === 'diag' || m.mode === 'age') {
                out += '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch hs-pass"></span>ok</span>'
                     + '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch hs-warn"></span>'
                     + (m.mode === 'age' ? '&gt;14 days' : 'warning') + '</span>'
                     + '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch hs-fail"></span>'
                     + (m.mode === 'age' ? '&gt;30 days' : 'error') + '</span>';
                if (m.mode === 'age') {
                    out += '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch" style="background:'
                         + dCfg.nullCellColor + '"></span>no data</span>';
                }
            } else {
                var agg = propAggs[m.key] || {};
                var stops = dCfg.colorScale;
                out += '<span class="topo-hero-lg-item">' + (agg.min != null ? m.fmtFn(agg.min) : '')
                     + '<span class="topo-hero-lg-grad" style="background:linear-gradient(90deg,' + stops.join(',') + ')"></span>'
                     + (agg.max != null ? m.fmtFn(agg.max) : '') + '</span>'
                     + '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch" style="background:'
                     + dCfg.nullCellColor + '"></span>no data</span>'
                     + '<span class="topo-hero-lg-item"><span class="topo-hero-lg-swatch hs-badfit-swatch"></span>bad fit</span>';
            }
            return out + '</div>';
        }

        function render() {
            var mCurPre = _metric(current);
            if (zoom == null) {
                // Default ≈ 1.7× what the OLD render showed (the customer
                // dialed the initial 2× back a notch). Pre-docs/126 the map
                // was fit-to-pane AND letterboxed by a 70vh max-height, so
                // the factor multiplies min(fit, cap). Unmeasurable layout
                // (jsdom, display:none) falls back to a plain 1.7× pane.
                zoom = 1.7;
                try {
                    var _pw = host.clientWidth || 0, _vh = window.innerHeight || 0;
                    if (_pw > 0 && _vh > 0 && H > 0) {
                        zoom = Math.min(4, Math.max(
                            0.9, 1.7 * Math.min(1, 0.7 * _vh * W / (H * _pw))));
                    }
                } catch (e) {}
            }
            var bar = '<div class="topo-hero-bar" role="tablist" aria-label="Chip map metric">';
            METRICS.forEach(function(m) {
                bar += '<button type="button" role="tab" aria-selected="' + (m.key === current) + '"'
                     + ' class="topo-hero-mbtn' + (m.key === current ? ' active' : '')
                     + (m.scope === 'edge' ? ' topo-hero-mbtn-edge' : '') + '"'
                     + ' data-hero-metric="' + _esc(m.key) + '">'
                     + (m.label ? _esc(m.label) : labelHtml(m.key, true)) + '</button>';
            });
            // no role="group": Pico styles [role=group] as a full-width
            // button bar (width:100% + flex:1 children) — real-browser caught.
            bar += '<span class="topo-hero-zoomctl" aria-label="Map size">'
                 + '<button type="button" class="topo-hero-zbtn" data-hero-zoom="out" title="Smaller">&minus;</button>'
                 + '<input type="range" class="topo-hero-zslider" min="0.25" max="4" step="0.05"'
                 + ' value="' + zoom.toFixed(2) + '" aria-label="Map size" title="Map size">'
                 + '<button type="button" class="topo-hero-zbtn" data-hero-zoom="in" title="Bigger">+</button>'
                 + '<button type="button" class="topo-hero-zbtn" data-hero-zoom="fit" title="Fit the pane width">Fit</button>'
                 + '<button type="button" class="topo-hero-zbtn topo-hero-compact-btn'
                 + (compactMode ? ' active' : '') + '" data-hero-compact="1"'
                 + ' title="Compact — smaller stones and markers, bigger numbers (fits a small monitor)"'
                 + ' aria-pressed="' + compactMode + '">Aa</button>'
                 + '</span></div>';
            // docs/126: RB numbers exist per GATE — a second row picks which
            // pulse variant's number the edges show ('best' = the old best-of).
            if (mCurPre && mCurPre.perGate) {
                var gnames = gatesFor2Q(mCurPre.match);
                if (gnames.length > 1) {
                    bar += '<div class="topo-hero-gatebar" role="tablist" aria-label="Gate variant">'
                         + '<span class="muted topo-hero-gatelabel">pulse:</span>';
                    ['best'].concat(gnames).forEach(function(g) {
                        var on = (heroGate === g) || (g === 'best' && gnames.indexOf(heroGate) < 0);
                        bar += '<button type="button" class="topo-hero-mbtn topo-hero-gbtn'
                             + (on ? ' active' : '') + '" data-hero-gate="' + _esc(g) + '">'
                             + _esc(g) + '</button>';
                    });
                    bar += '</div>';
                }
            }

            var mCur = mCurPre;
            var edgeMode = mCur.scope === 'edge';
            var eagg = edgeMode ? _edgeAgg(mCur) : null;

            var svg = '';
            var evalSvg = '';
            var R = compactMode ? 33 : 37;   // docs/126 compact: smaller stones
            // Customer ask (2026-08-27, round 2): while a metric with
            // `edgeDelta` is selected (Qubit freq), each pair edge prints the
            // difference between its two stones' values — Δf on the line's
            // midpoint (the chevron sits off-line at +0.16·CELL, M on the
            // other side, so the midpoint is free; the halo keeps it
            // readable). Same gated read as the stones (_mv), so an edge
            // never shows a Δf its stones cannot back — either end missing
            // or unphysical means no label, never a fabricated 0. Deduped
            // per physical pair (a CR chip carries both directions as
            // separate edges).
            var _nById = {};
            topo.nodes.forEach(function(n) { _nById[n.id] = n; });
            var _deltaDone = {};
            topo.edges.forEach(function(e) {
                var a = lay.positions[e.source], b = lay.positions[e.target];
                if (!a || !b) return;
                var oA = offsets[e.source] || { dx: 0, dy: 0 }, oB = offsets[e.target] || { dx: 0, dy: 0 };
                var x1 = cx(a) + oA.dx, y1 = cy(a) + oA.dy, x2 = cx(b) + oB.dx, y2 = cy(b) + oB.dy;
                if (!edgeMode && mCur.edgeDelta) {
                    var dKey = String(e.source) < String(e.target)
                        ? e.source + '|' + e.target : e.target + '|' + e.source;
                    if (!_deltaDone[dKey]) {
                        _deltaDone[dKey] = true;
                        var nA = _nById[e.source], nB = _nById[e.target];
                        var fA = nA != null ? _mv(nA, mCur.edgeDelta.key) : null;
                        var fB = nB != null ? _mv(nB, mCur.edgeDelta.key) : null;
                        if (typeof fA === 'number' && typeof fB === 'number') {
                            var ad = Math.abs(fA - fB);
                            // Round 3: anharmonicity-sized, and compact —
                            // whole MHz once the difference is ≥ 10 MHz
                            // (0.1 MHz still shows below that, where it
                            // matters), GHz from 1 GHz up.
                            var dNum = ad >= 1e9 ? (ad / 1e9).toFixed(2)
                                     : ad >= 1e7 ? (ad / 1e6).toFixed(0)
                                     : (ad / 1e6).toFixed(1);
                            var dUnit = ad >= 1e9 ? 'GHz' : 'MHz';
                            var dmx = (x1 + x2) / 2, dmy = (y1 + y2) / 2;
                            if (Math.abs(x2 - x1) >= Math.abs(y2 - y1)) {
                                // A HORIZONTAL edge has only the stone gap
                                // to write in, so the label stacks: Δf /
                                // number / unit on three short lines.
                                evalSvg += '<text class="topo-hero-edelta topo-hero-edelta-stack" x="' + dmx
                                    + '" y="' + (dmy - 6) + '">'
                                    + '<tspan x="' + dmx + '">' + _esc(mCur.edgeDelta.label) + '</tspan>'
                                    + '<tspan x="' + dmx + '" dy="9">' + _esc(dNum) + '</tspan>'
                                    + '<tspan x="' + dmx + '" dy="9">' + _esc(dUnit) + '</tspan>'
                                    + '</text>';
                            } else {
                                evalSvg += '<text class="topo-hero-edelta" x="' + dmx
                                    + '" y="' + (dmy + 3) + '">'
                                    + _esc(mCur.edgeDelta.label + ' ' + dNum + ' ' + dUnit)
                                    + '</text>';
                            }
                        }
                    }
                }
                var pdx = 0, pdy = 0;   // unit perpendicular (label offset)
                {
                    var ddx = x2 - x1, ddy = y2 - y1, LL = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
                    pdx = -ddy / LL; pdy = ddx / LL;
                }
                if (e.directed) {
                    // anti-parallel offset so BOTH directions of a CR pair show
                    var off = 6;
                    x1 += pdx * off; y1 += pdy * off;
                    x2 += pdx * off; y2 += pdy * off;
                }
                // docs/126 ②: an EDGE metric paints the edges the way a node
                // metric paints the stones — chip-relative palette + a value
                // printed ON the edge; a node metric keeps the fidelity
                // good/warn/bad paint (thicker than before — the customer
                // could barely click the old 3px lines).
                var stroke, width, evTxt = null;
                if (edgeMode) {
                    var ev = mCur.valFn(e);
                    if (ev != null && eagg.count >= 1) {
                        var t = eagg.count < 2 ? 0.5
                              : (ev - eagg.min) / ((eagg.max - eagg.min) || 1);
                        stroke = interpolateColor(t, dCfg.colorScale);
                        width = 9;
                        evTxt = mCur.fmtFn(ev);
                    } else {
                        stroke = dCfg.nullCellColor; width = 5;
                    }
                } else {
                    var ep = _edgePaint(e);
                    stroke = ep.color; width = ep.width + 3;
                }
                var tt = e.pair_id + (e.cz_fidelity != null ? ' — ' + (e.cz_fidelity * 100).toFixed(1) + '%' : '')
                       + (e.active === false ? ' · off' : '');
                var coords = ' x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"';
                var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
                svg += '<g class="topo-hero-edge" data-hero-pair="' + _esc(e.pair_id) + '"'
                     + (e.active === false ? ' opacity="0.35"' : '') + '>'
                     + '<line' + coords + ' stroke="' + stroke + '" stroke-width="' + width
                     + '" stroke-linecap="round"/>'
                     + '<line class="topo-hero-edge-hit"' + coords + ' stroke="transparent" stroke-width="22">'
                     + '</line><title>' + _esc(tt) + '</title></g>';
                // docs/126: the value draws in a TOP layer, after the stones
                // and the role markers — on the real chip the C/T circles sat
                // exactly on the midpoint and covered every printed number.
                if (evTxt != null) {
                    evalSvg += '<text class="topo-hero-eval" x="' + (mx + pdx * (e.directed ? 15 : 0))
                        + '" y="' + (my + pdy * (e.directed ? 15 : 0) + 4) + '">'
                        + _esc(evTxt) + '</text>';
                }
            });
            // docs/120 item 11 → amended by docs/126: the C/T/M markers now
            // draw AFTER the stones (compactRoles centres them ON the rim —
            // half-overlapping the stone, smaller — so the edge middle stays
            // free for the metric value; same shared TopoGraph.pairGlyphs,
            // so the component maps keep their own classic placement).
            var glyphSvg = '';
            if (TG.pairGlyphs) {
                glyphSvg = TG.pairGlyphs(topo.nodes, topo.edges, {
                    cell: CELL, roles: true, compactRoles: true,
                    px: function (id) {
                        var p = lay.positions[id];
                        if (!p) return null;
                        var o = offsets[id] || { dx: 0, dy: 0 };
                        return { x: cx(p) + o.dx, y: cy(p) + o.dy };
                    },
                });
            }
            topo.nodes.forEach(function(n) {
                var p = lay.positions[n.id];
                if (!p) return;
                var o = offsets[n.id] || { dx: 0, dy: 0 };
                if (edgeMode) {
                    // The edges carry the numbers — stones go neutral so the
                    // painted edges read as the subject, not the background.
                    svg += '<g class="topo-hero-node topo-hero-node-neutral" data-hero-qubit="' + _esc(n.id)
                         + '" transform="translate(' + (cx(p) + o.dx) + ',' + (cy(p) + o.dy) + ')" tabindex="0">'
                         + '<circle class="topo-hero-stone" r="' + R + '"'
                         + (o.shared ? ' stroke-dasharray="3 2"' : '') + '></circle>'
                         + '<text class="topo-hero-id" y="4">' + _esc(n.id) + '</text>'
                         + '</g>';
                    return;
                }
                var pt = nodePaint(n, current);
                // A metric with a sub-value shifts the whole stone to a
                // three-line layout (uniform per METRIC, so ids line up
                // across the map even where one qubit's sub is missing).
                var subMode = !!mCur.subKey;
                svg += '<g class="topo-hero-node' + (pt.cls ? ' ' + pt.cls : '') + '" data-hero-qubit="' + _esc(n.id)
                     + '" transform="translate(' + (cx(p) + o.dx) + ',' + (cy(p) + o.dy) + ')" tabindex="0">'
                     + '<circle class="topo-hero-stone" r="' + R + '"'
                     + (pt.fill ? ' style="fill:' + pt.fill + '"' : '')
                     + (o.shared ? ' stroke-dasharray="3 2"' : '') + '></circle>'
                     + '<text class="topo-hero-id" y="' + (subMode ? -14 : -5) + '"' + (pt.fg ? ' style="fill:' + pt.fg + '"' : '') + '>'
                     + _esc(n.id) + '</text>'
                     + '<text class="topo-hero-val" y="' + (subMode ? 1 : 11) + '"' + (pt.fg ? ' style="fill:' + pt.fg + '"' : '') + '>'
                     + _esc(pt.text) + '</text>'
                     + (pt.sub != null
                        ? '<text class="topo-hero-sub" y="14"' + (pt.fg ? ' style="fill:' + pt.fg + '"' : '') + '>'
                          + _esc(pt.sub) + '</text>'
                        : '')
                     + (pt.title ? '<title>' + _esc(pt.title) + '</title>' : '')
                     + '</g>';
            });

            var note = lay.mode === 'logical'
                ? '<div class="topo-hero-note">' + _esc(TG.LOGICAL_LAYOUT_NOTE) + '</div>' : '';
            // Zoom: percentage of the scroll pane's width (1 = the old fit).
            // The container scrolls both axes, so a big map never buries the
            // sections below (docs/126 ② — "~2× bigger" is the default).
            host.innerHTML = bar
                + '<div class="topo-hero-map">' + note
                + '<div class="topo-hero-scroll"><svg class="topo-hero-svg'
                + (compactMode ? ' hero-compact' : '') + '" width="' + W + '" height="' + H
                + '" viewBox="0 0 ' + W + ' ' + H + '"'
                + ' style="width:' + Math.round(zoom * 100) + '%">'
                + svg + glyphSvg + evalSvg + '</svg></div></div>'
                + legendHtml(current);
            bindHover();
        }

        // Delegated clicks (bound ONCE on the host — innerHTML re-renders keep
        // working): metric switch / qubit single-click inspect + double-click
        // JSON / edge click -> pair inspector.
        var _heroClickTime = 0, _heroClickId = null;
        if (!host._heroBound) {
            host._heroBound = true;
            // Zoom only changes the svg's CSS width — applied in place, no
            // rebuild (a full re-render mid-slider-drag would destroy the
            // slider under the pointer).
            function _applyZoom(z) {
                zoom = Math.min(4, Math.max(0.25, z));
                try { localStorage.setItem(ZOOM_KEY, String(zoom)); } catch (e) {}
                var el = host.querySelector('svg.topo-hero-svg');
                if (el) el.style.width = Math.round(zoom * 100) + '%';
                var sl = host.querySelector('.topo-hero-zslider');
                if (sl && document.activeElement !== sl) sl.value = zoom.toFixed(2);
            }
            host.addEventListener('input', function(ev) {
                if (ev.target && ev.target.classList
                        && ev.target.classList.contains('topo-hero-zslider')) {
                    _applyZoom(parseFloat(ev.target.value));
                }
            });
            host.addEventListener('click', function(ev) {
                var zb = ev.target.closest && ev.target.closest('[data-hero-zoom]');
                if (zb) {
                    var act = zb.getAttribute('data-hero-zoom');
                    _applyZoom(act === 'fit' ? 1 : zoom + (act === 'in' ? 0.25 : -0.25));
                    return;
                }
                var cb = ev.target.closest && ev.target.closest('[data-hero-compact]');
                if (cb) {
                    compactMode = !compactMode;
                    try { localStorage.setItem(COMPACT_KEY, compactMode ? '1' : '0'); } catch (e) {}
                    render();
                    return;
                }
                var gb = ev.target.closest && ev.target.closest('[data-hero-gate]');
                if (gb) {
                    heroGate = gb.getAttribute('data-hero-gate') || 'best';
                    try { localStorage.setItem(GATE_KEY, heroGate); } catch (e) {}
                    render();
                    return;
                }
                var mb = ev.target.closest && ev.target.closest('[data-hero-metric]');
                if (mb) {
                    current = mb.getAttribute('data-hero-metric');
                    try { localStorage.setItem(HERO_KEY, current); } catch (e) {}
                    render();
                    return;
                }
                var qg = ev.target.closest && ev.target.closest('[data-hero-qubit]');
                if (qg) {
                    var qid = qg.getAttribute('data-hero-qubit');
                    var now = Date.now();
                    if (now - _heroClickTime < 400 && _heroClickId === qid) {
                        _heroClickTime = 0; _heroClickId = null;
                        showQubitJsonPanel(qid, rawWiring);
                    } else {
                        _heroClickTime = now; _heroClickId = qid;
                        setTimeout(function() {
                            if (_heroClickTime !== 0 && _heroClickId === qid) window._inspectQubit(qid);
                        }, 420);
                    }
                    return;
                }
                var eg = ev.target.closest && ev.target.closest('[data-hero-pair]');
                if (eg) window._inspectPair(eg.getAttribute('data-hero-pair'));
            });
        }

        // Hover -> the SHARED qubit popup (property rows + sparklines) through
        // the bridge; intent delay + grace like the cards. Skipped on touch.
        var _coarseHero = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
        var _heroHoverTimer = null;
        function bindHover() {
            if (_coarseHero) return;
            var nodesById = {};
            topo.nodes.forEach(function(n) { nodesById[n.id] = n; });
            host.querySelectorAll('[data-hero-qubit]').forEach(function(g) {
                g.addEventListener('mouseenter', function() {
                    if (!_sharedQubitPopup) return;
                    _sharedQubitPopup.cancelClose();
                    clearTimeout(_heroHoverTimer);
                    _heroHoverTimer = setTimeout(function() {
                        var n = nodesById[g.getAttribute('data-hero-qubit')];
                        if (n) _sharedQubitPopup.open(n, g, false);
                    }, 260);
                });
                g.addEventListener('mouseleave', function() {
                    clearTimeout(_heroHoverTimer);
                    if (_sharedQubitPopup) _sharedQubitPopup.scheduleClose();
                });
            });
            // docs/126 ②: pairs hover too — same singleton popup, pair flavor.
            var edgesById = {};
            topo.edges.forEach(function(e) { edgesById[e.pair_id] = e; });
            host.querySelectorAll('[data-hero-pair]').forEach(function(g) {
                g.addEventListener('mouseenter', function() {
                    if (!_sharedQubitPopup || !_sharedQubitPopup.openPair) return;
                    _sharedQubitPopup.cancelClose();
                    clearTimeout(_heroHoverTimer);
                    _heroHoverTimer = setTimeout(function() {
                        var e = edgesById[g.getAttribute('data-hero-pair')];
                        if (e) _sharedQubitPopup.openPair(e, g, false);
                    }, 260);
                });
                g.addEventListener('mouseleave', function() {
                    clearTimeout(_heroHoverTimer);
                    if (_sharedQubitPopup) _sharedQubitPopup.scheduleClose();
                });
            });
        }

        _rebuildHeroMap = render;
        render();
    })();

    // ── Build HTML legend ────────────────────────────────────────────
    var legendEl = document.getElementById('topology-legend');
    if (legendEl) {
        var items = [];
        // docs/120 item 11: the per-chain colour swatches are GONE. The card
        // headers were the only thing ever painted a chain colour, so with the
        // cards deleted this legend described an encoding that appears nowhere
        // on the page — worse than a legend you have to memorise, because it
        // sends the reader looking for something that is not there.
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

    // docs/120 item 11: toggleEdgeLabels + its localStorage restore lived
    // here to show/hide the CARD diagram's `.topo-edge-label` elements. The
    // hero draws no text on its edges (direction is the C/T/M markers), so
    // both the function and the checkbox that called it are gone rather than
    // left as a control that does nothing.


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

    // (docs/126 ②: the Distributions section was removed on customer request —
    // the histograms duplicated what the map + per-metric panels already show.)

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
                // docs/126 ②: every spec chart rides the house theme — the
                // specs set sizes/margins but never a font COLOR, so dark mode
                // rendered every axis label in Plotly's default gray (the
                // customer: "cannot read the axis numbers at all"). houseLayout
                // deep-merges UNDER the spec's own overrides.
                if (window.PlotTheme && window.PlotTheme.houseLayout) {
                    layout = window.PlotTheme.houseLayout(layout);
                }
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
        // docs/141 4o: the first block of the Fidelity section (the wrapper's
        // <h3> says Fidelity); this is its sub-heading.
        var html = ['<h4 class="topo-section-title topo-fidelity-subtitle" style="margin-top:0.5rem;font-size:1.05em">2Q Gate Fidelity \u2014 RB</h4>'];
        var specs = [];

        // ── Render panels per RB type, then per gate ────────────────
        ['StandardRB', 'InterleavedRB'].forEach(function(rbType) {
            var gates = rbData[rbType];
            if (!gates) return;

            var rbLabel = rbType === 'StandardRB' ? 'Standard RB' : 'Interleaved RB';
            html.push('<h5 class="topo-section-title" style="margin-top:0.8rem;font-size:1em">' + rbLabel + '</h5>');

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
                var scorer = outlierScorer(_physCz, {   // robust MAD flag (this gate),
                    // spec-gated: an in-spec fidelity is never branded
                    verdict: function(v) { return _verdict(v, thresholds['cz_fidelity']); },
                });
                var gateLabel = _esc(gateName.replace(/^cz_/, ''));   // only ever rendered as HTML
                var secId = 'rb-' + rbType + '-' + gateName.replace(/[^a-zA-Z0-9]/g, '-');
                var chartId = secId + '-chart';

                var dKey = '2q:' + rbType + ':' + gateName;
                var sectionHtml = '<div class="topo-section" id="' + secId + '" data-density-panel="' + _esc(dKey) + '">';
                // Stat line is its OWN block below the title (not inside the <h4>) so
                // at narrow width it wraps cleanly instead of lapping onto the grid.
                sectionHtml += '<h4 class="topo-metric-panel-title">' + gateLabel
                    + window.ChipStatus.density.controlHtml(dKey) + '</h4>';
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
                // no RB data for THIS gate show a grey em dash instead of leaving a
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
                            // Python's None reached the screen as a WORD. The
                            // title beside it already says "not measured" and the
                            // panel header says "(1/20 qubits)", so three places
                            // described the same absence and one of them used a
                            // foreign language's null literal. An em dash is what
                            // the hero map already prints for exactly this.
                            + '<div class="heatmap-cell-value">—</div></div>';
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
                        // The grid now shows every pair (grey em dash), so matching it blew
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
        window.ChipStatus.density.applyAll(container);

        container.querySelectorAll('.heatmap-cell[data-pair]').forEach(function(cell) {
            cell.addEventListener('click', function() {
                var pid = cell.getAttribute('data-pair');
                if (pid) htmx.ajax('GET', '/pair/' + pid, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            });
        });

        _renderChartSpecsProgressively(specs);
        if (window._recolorTopology) window._recolorTopology();   // repaint the new cells with the active palette
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
            {key:'gate_fidelity_avg', title:'1Q Gate Fidelity \u2014 RB avg (%)',group:'fid1q'},
            {key:'gate_fidelity_x180',title:'1Q Gate Fidelity x180 (%)',group:'fid1q'},
            {key:'gate_fidelity_x90', title:'1Q Gate Fidelity x90 (%)', group:'fid1q'},
            // docs/141 4o (user-directed): the IQ-blob metric is named for what
            // it IS everywhere in SM — readout fidelity, two-state (GE) from the
            // confusion matrix, three-state (GEF) from gef_confusion_matrix.
            // docs/148b (customer): the readout block reads GE then its
            // per-state diagonals, THEN the GEF block with its own g/e/f --
            // two matrices, two blocks, in physical reading order.
            {key:'assignment_fidelity',title:'Readout Fidelity (GE) (%)', group:'fidro',
             source:'confusion_matrix'},
            {key:'ro_fidelity_g',     title:'Readout Fidelity |g\u27E9 (GE) (%)',group:'fidro',
             source:'confusion_matrix'},
            {key:'ro_fidelity_e',     title:'Readout Fidelity |e\u27E9 (GE) (%)',group:'fidro',
             source:'confusion_matrix'},
            {key:'assignment_fidelity_gef',title:'Readout Fidelity (GEF) (%)', group:'fidro',
             source:'gef_confusion_matrix'},
            {key:'ro_fidelity_gef_g', title:'Readout Fidelity |g\u27E9 (GEF) (%)',group:'fidro',
             source:'gef_confusion_matrix'},
            {key:'ro_fidelity_gef_e', title:'Readout Fidelity |e\u27E9 (GEF) (%)',group:'fidro',
             source:'gef_confusion_matrix'},
            {key:'ro_fidelity_gef_f', title:'Readout Fidelity |f\u27E9 (GEF) (%)',group:'fidro',
             source:'gef_confusion_matrix'},
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
        // docs/141 4o: the fidelity group lives in the Fidelity section (after
        // the 2Q RB panels) when the page has one; the other groups keep
        // this container.
        var fid1qHost = document.getElementById('topo-fidelity-1q-panels');
        var fidRoHost = document.getElementById('topo-fidelity-ro-panels');
        var fid1qHtml = [], fidRoHtml = [];
        var sink = function (def) {
            if (fid1qHost && def.group === 'fid1q') return fid1qHtml;
            if (fidRoHost && def.group === 'fidro') return fidRoHtml;
            return html;
        };

        PANEL_DEFS.forEach(function(def) {
            var prop = findProp(def.key);
            if (!prop) return;

            // Gated values: an unphysical fit (−473µs T2) is None, so it never feeds
            // avg/min/max, never colours red, never stretches the relative range.
            var vals = topo.nodes.map(function(n) { return _mv(n, def.key); });
            var agg = computeAggregates(vals);
            if (agg.count === 0) {
                // docs/148 (customer: "why is GEF missing?"): inside the
                // dedicated fidelity sections an absent metric renders an
                // HONEST empty line naming the leaf it fills from -- a
                // silently skipped panel reads as a missing feature
                // (docs/94 rule). The shared metrics container keeps the
                // old skip: coherence/frequency panels absent from a chip
                // are not a question anyone asked.
                if (def.group === 'fid1q' || def.group === 'fidro') {
                    sink(def).push('<div class="topo-section topo-metric-empty" data-group="'
                        + def.group + '"><h4 class="topo-metric-panel-title">' + def.title
                        + '</h4><p class="muted" style="margin:0.2rem 0 0.8rem">no values on this chip yet'
                        + (def.source ? ' \u2014 fills from <code>' + def.source + '</code> once a run writes it' : '')
                        + '</p></div>');
                }
                return;
            }

            // Group header (the dedicated fidelity sections carry their own
            // <h3> in the template -- no injected header there)
            if (def.group !== prevGroup) {
                prevGroup = def.group;
                var groupLabel = {coherence:'Coherence', frequency:'Frequencies', calibration:'Calibration'}[def.group];
                if (groupLabel) {
                    html.push('<h3 class="topo-section-title" data-group="' + def.group + '" style="margin-top:1.5rem">' + groupLabel + '</h3>');
                }
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
            var scorer = outlierScorer(vals, {
                verdict: function(v) { return _verdict(v, thresholds[def.key]); },
            });

            var sectionHtml = '<div class="topo-section" data-group="' + def.group + '" id="' + secId + '" data-density-panel="' + def.key + '">';
            // Keep the curated title text (it carries the unit suffix) but pull the
            // good-direction arrow + plain-language tooltip from META — so direction
            // and blurb have one source, even though the display string stays bespoke.
            // docs/141 4o: the panel's own S / M / L sits right of its title.
            sectionHtml += '<h4 class="topo-metric-panel-title">' + labelHtml(def.key, false, def.title)
                + window.ChipStatus.density.controlHtml(def.key) + '</h4>';
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
                    + '<div class="heatmap-cell-value">'
                    + (v != null ? prop.fmtFn(v) : '—') + '</div></div>';
            });
            sectionHtml += '</div>'; // close grid

            // Bar chart (right side)
            sectionHtml += '<div class="topo-metric-bar-chart" id="' + chartId + '"></div>';
            sectionHtml += '</div>'; // close panel-row
            sectionHtml += '</div>'; // close section
            sink(def).push(sectionHtml);

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

        // Single DOM write per host, then re-query click handlers, then render charts.
        container.innerHTML = html.join('');
        if (fid1qHost) fid1qHost.innerHTML = fid1qHtml.join('');
        if (fidRoHost) fidRoHost.innerHTML = fidRoHtml.join('');
        window.ChipStatus.density.applyAll(container);
        if (fid1qHost) window.ChipStatus.density.applyAll(fid1qHost);
        if (fidRoHost) window.ChipStatus.density.applyAll(fidRoHost);
        var hosts = [container].concat(fid1qHost ? [fid1qHost] : []).concat(fidRoHost ? [fidRoHost] : []);
        var eachCell = function (sel, fn) { hosts.forEach(function (h) { h.querySelectorAll(sel).forEach(fn); }); };

        eachCell('.heatmap-cell[data-qubit]', function(cell) {
            cell.addEventListener('click', function() {
                var qid = cell.getAttribute('data-qubit');
                if (qid) htmx.ajax('GET', '/qubit/' + qid, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            });
        });

        _renderChartSpecsProgressively(specs);
        if (window._recolorTopology) window._recolorTopology();   // repaint the new cells with the active palette
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
        if (_rebuildHeroMap) _rebuildHeroMap();   // hero map repaints on the new palette
        updateLegendSwatches();
    };

    // Switch bar chart palette
    window.switchBarPalette = function(paletteName) {
        if (!PALETTES[paletteName]) return;
        _barColorScale = PALETTES[paletteName].stops;
        _currentBarPaletteName = paletteName;
        try { localStorage.setItem('quam_bar_palette', paletteName); } catch(e) {}
        recolorBarCharts();
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
    // docs/141 4o (user-directed order): Overview · Health · Topology · Trends ·
    // Fidelity (2Q RB first, then 1Q, then readout) · Coherence · Frequencies ·
    // Calibration. 'gate' survives as an alias of 'fidelity' (old links, the
    // remembered view).
    var TAB_SPEC = {
        overview:     { build: null,           sel: '[data-topo-section="overview"]' },
        health:       { build: null,           sel: '[data-topo-section="health"]' },
        topology:     { build: null,           sel: '#sec-topology' },
        // docs/148: 2Q / 1Q / readout are separate menu entries + sections.
        fidelity2q:   { build: '2qrb',          sel: '[data-topo-section="fidelity"]' },
        fidelity1q:   { build: 'metrics',       sel: '#sec-fidelity-1q' },
        readout:      { build: 'metrics',       sel: '#sec-readout' },
        coherence:    { build: 'metrics',       sel: '#topo-metric-panels [data-group="coherence"]' },
        frequencies:  { build: 'metrics',       sel: '#topo-metric-panels [data-group="frequency"]' },
        calibration:  { build: 'metrics',       sel: '#topo-metric-panels [data-group="calibration"]' },
        // docs/120 items 5+9 — every qubit on ONE plot per metric.
        trends:       { build: 'trends',        sel: '[data-topo-section="trends"]' },
    };
    var _chipSectionBuilt = {};   // section key -> built once (lazy heavy builders)
    var _suppressSpyUntil = 0;    // ignore scroll-spy briefly after a click-jump
    // docs/141 4o: Trends now sits ABOVE Fidelity / Coherence / … and is fetched
    // lazily, so a jump to a section below it (a sidebar sub-link, ?view=)
    // landed on Trends once its charts arrived and pushed everything down
    // (real Chrome, PJ chip). Remember the last jump; when Trends lands, put
    // that section back at the top of the pane.
    var _jump = {
        note: function (view) { window.ChipStatus.jumpGuard.note(view, _scrollPane()); },
        reanchor: function () {
            var did = window.ChipStatus.jumpGuard.reanchor(function (v) { return TAB_SPEC[v] && TAB_SPEC[v].sel; });
            if (did) _suppressSpyUntil = Date.now() + 800;
            return did;
        }
    };

    function _ensureSectionBuilt(key) {
        if (Array.isArray(key)) { key.forEach(_ensureSectionBuilt); return; }
        if (!key || _chipSectionBuilt[key]) return;
        else if (key === '2qrb') { _chipSectionBuilt[key] = true; build2QRBPanels(); }
        else if (key === 'metrics') { _chipSectionBuilt[key] = true; buildMetricPanels(); }
        // docs/148: the 1Q / readout sections' panels come from the metrics build
        else if (key === 'fid1q' || key === 'fidro') { _ensureSectionBuilt('metrics'); }
        // Trends fetches its own data (the history index is not cheap enough to
        // ride the page render), so building it means asking for it once.
        else if (key === 'trends') {
            var host = document.getElementById('topo-trends');
            if (!host || !window.htmx) return;
            /* docs/122 item 4 — the flag used to be set BEFORE the request, so a
               single failed fetch retired the section for the life of the page:
               reproduced by aborting the first /topology/trends, after which
               #topo-trends stayed empty (innerHTML length 0) and scrolling away
               and back never retried. Mark built only on success; a failure
               leaves the section eligible for the next intersection. */
            _chipSectionBuilt[key] = true;
            var p = htmx.ajax('GET', '/topology/trends',
                              { source: '#topo-trends', target: '#topo-trends',
                                swap: 'outerHTML' });
            if (p && typeof p.then === 'function') {
                p.then(function () {
                    if (!document.querySelector('.topo-trend-box')
                        && !document.querySelector('.topo-trends-controls')) {
                        _chipSectionBuilt[key] = false;   // nothing arrived — retry later
                        return;
                    }
                    // the charts just pushed everything below them down: a
                    // jump made a moment ago goes back to where it pointed
                    requestAnimationFrame(function () { _jump.reanchor(); });
                }, function () { _chipSectionBuilt[key] = false; });
            }
        }
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
        if (view === 'gate' || view === 'fidelity') view = 'fidelity2q';   // docs/141 4o + docs/148 aliases
        var spec = TAB_SPEC[view] || TAB_SPEC.overview;
        try { localStorage.setItem('quam_chipstatus_view', view); } catch (e) {}
        if (scroll !== false) _jump.note(view);
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
            ['2qrb', 'metrics', 'trends'].forEach(_ensureSectionBuilt); return;
        }
        var io = new IntersectionObserver(function(entries) {
            entries.forEach(function(e) {
                if (e.isIntersecting) _ensureSectionBuilt(e.target.getAttribute('data-topo-section'));
            });
        }, { root: _scrollPane(), rootMargin: '400px 0px 400px 0px' });
        // Tear it down on nav-away. ChipStatus.mount runs on every /topology
        // render, so without this each visit stranded an observer holding its
        // [data-topo-section] subtrees alive — and docs/120 added a 4th section
        // whose charts carry ~1 MB of data and live Plotly divs. Mirrors the
        // scroll-spy teardown below; pre-existing, but this change is what made
        // it expensive.
        document.body.addEventListener('htmx:beforeSwap', function _ioTeardown(evt) {
            if (evt.detail && evt.detail.target && evt.detail.target.id === 'table-pane') {
                try { io.disconnect(); } catch (e) {}
                document.body.removeEventListener('htmx:beforeSwap', _ioTeardown);
            }
        });
        ['2qrb', 'metrics', 'trends', 'fid1q', 'fidro'].forEach(function(k) {
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
    // docs/120 item 11: _refitTopology / _maybeRefit lived here to scale the
    // CARD diagram's .topo-inner down to the pane width. The cards are gone and
    // the hero is responsive by construction — a viewBox plus `width:100%` in
    // CSS — so there is no JS re-fit any more, and no resize listener to leak.

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
        // A qubit with NO data is UNJUDGED, not "in spec".
        //
        // `_verdict(null, …)` returns neither 'fail' nor 'warn', so a qubit
        // that has never been measured simply never enters `below` — and on a
        // chip in early bring-up, where T1 / T2 / fidelity are null CHIP-WIDE
        // (exactly the real customer chip), that made belowCount 0 and the
        // banner announce "Chip looks healthy — all 20 qubits in spec" over a
        // chip with no calibration data at all. The map's own legend already
        // had a distinct "No data" swatch; only the verdict was pretending.
        var measuredCount = nodes.filter(function (n) {
            return NODE_METRICS.some(function (m) {
                var v = _mval(n, m);
                return typeof v === 'number' && isFinite(v);
            });
        }).length;
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
        html += _hTile('qubits below spec', belowCount,
                       belowCount ? (failCount ? 'fail' : 'warn')
                                  : (measuredCount ? 'pass' : 'neutral'),
                       failCount ? (failCount + ' failing &middot; ' + (belowCount - failCount) + ' warn')
                                 : (belowCount ? 'to watch'
                                    : (measuredCount === 0 ? 'no data yet'
                                       : (measuredCount < nodes.length
                                          ? measuredCount + ' of ' + nodes.length + ' measured'
                                          : 'all in spec'))));
        // Same rule on the pair side: cz_fidelity is null chip-wide on a chip
        // that has not run 2Q calibration yet, and "all pairs in spec" over
        // zero measurements is the same lie as its qubit twin above.
        var czMeasured = edges.filter(function (e) {
            var v = _mval(e, 'cz_fidelity');
            return typeof v === 'number' && isFinite(v);
        }).length;
        html += _hTile('CZ below spec', czBelow,
                       czBelow ? (czFailCount ? 'fail' : 'warn')
                               : (czMeasured ? 'pass' : 'neutral'),
                       czBelow ? 'of ' + edges.length + ' pairs'
                               : (czMeasured === 0 ? 'no data yet'
                                  : (czMeasured < edges.length
                                     ? czMeasured + ' of ' + edges.length + ' measured'
                                     : 'all pairs in spec')));
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
            if (verdict === 'pass' && measuredCount === 0) {
                // Nothing was measured, so nothing passed. Say that.
                verdict = 'unknown';
                icon = 'ⓘ';
                headline = 'No coherence or fidelity data on this chip yet — '
                         + 'nothing to judge'
                         + (diagTotal ? '' : ', and no structural issues found') + '.';
            } else if (verdict === 'pass' && measuredCount < nodes.length) {
                headline = 'All ' + measuredCount + ' measured qubit'
                         + (measuredCount === 1 ? '' : 's') + ' in spec — '
                         + (nodes.length - measuredCount) + ' of ' + nodes.length
                         + ' not measured yet'
                         + (diagTotal ? '' : ', no structural issues') + '.';
            } else if (verdict === 'pass') {
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
    };
    // Reset ONE metric back to its spec default (mirrors applyThresholds' commit
    // order: persist → editor rebuild → summary).
    window.resetMetricThreshold = function(k) {
        var d = _defaultThresholds[k];
        if (!d || !thresholds[k]) return;
        thresholds[k].warn = d.warn;
        thresholds[k].fail = d.fail;
        _saveThresholds(thresholds);
        window._chipThresholds = thresholds;
        buildThresholdEditor();
        buildHealthSummary();
    };

    // ── Cell colour: ONE continuous app-blue magnitude read, everywhere ──
    // The retired "spec colour" mode painted every thresholded value with a
    // discrete green/amber/red verdict while the "... more" hover panel kept
    // the continuous palette — the default cards clashed with the panel right
    // next to them (user feedback: the RAG mix looked dated). Colour now
    // ALWAYS means relative magnitude on the shared palette (Blues by
    // default), identical across cards, hover panel and heatmap cells.
    // Pass/warn/fail verdicts still live on the dedicated status surfaces
    // (Health tiles, verdict banner, worst-qubit list, report card) — they
    // just no longer repaint the numbers. Reads the stored data-heat-t; no
    // rebuild.
    function _paintCell(cell) {
        var t = parseFloat(cell.getAttribute('data-heat-t'));
        if (!isNaN(t)) { var bg = interpolateColor(t, dCfg.colorScale); cell.style.background = bg; cell.style.color = textColorForBg(bg); }
        else { cell.style.background = ''; cell.style.color = ''; }   // missing/unphysical → neutral (None style shows)
    }
    window._recolorTopology = function() {
        var pv = document.querySelectorAll('.topo-prop-value');
        for (var i = 0; i < pv.length; i++) _paintCell(pv[i]);
        var hc = document.querySelectorAll('.heatmap-cell[data-metric]');
        for (var j = 0; j < hc.length; j++) _paintCell(hc[j]);
    };

    // Build the health summary + Overview tiles eagerly (cheap, no Plotly).
    buildHealthSummary();
    buildOverviewTiles();

    // Phase C: wire the unified dashboard — lazy build on scroll + scroll-spy
    // tab highlight. The resize re-fit that used to live here went with the card
    // diagram (docs/120 item 11): the hero scales itself through its viewBox, so
    // there is no longer a window-resize listener or ResizeObserver to register
    // — or, as this block existed to guarantee, to tear down on nav-away.
    _setupLazyBuild();
    _setupScrollSpy();

    // A deep-link ?view= (left-nav sub-item or a shared link) scrolls to that
    // section; a bare /topology load stays at the top (topology), by design — we
    // do NOT resume the last-used localStorage view.
    // docs/141 4ac: normalise the alias HERE. 4o kept accepting ?view=gate for
    // old links and maps it onto Fidelity inside setChipStatusView -- but this
    // guard tests the RAW value against TAB_SPEC, from which the same commit
    // deliberately removed `gate`, so the branch was skipped and the mapping
    // never ran: an old bookmark landed on Topology with no sign anything was
    // ignored.
    var _deepView = (_serverChipView === 'gate' || _serverChipView === 'fidelity')
        ? 'fidelity2q' : _serverChipView;
    if (_deepView && TAB_SPEC[_deepView]) {
        window.setChipStatusView(_deepView, null, true);
    } else {
        _setActiveTab('topology');
    }

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
        // docs/120 item 11: `.topo-node-card` no longer exists, so on the
        // Topology section this selector matched NOTHING and arrow/Enter
        // navigation of the chip map silently died — while the tip line under
        // the map still promised it. The hero's nodes take its place.
        var SEL = '.heatmap-cell, [data-hero-qubit]';
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
                // `offsetParent` is HTMLElement-only. docs/120 item 11 repointed
                // this selector at the hero stones, which are SVG <g> nodes, so
                // the guard scored all 20 as hidden and nearest() always returned
                // null — arrow keys could never move, on any chip. getClientRects()
                // is defined on Element and agrees with offsetParent on all 120
                // real heatmap cells (measured, 0 disagreements).
                if (c === from || !c.getClientRects().length) return;   // skip hidden
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
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                // Same cause: SVGElement has no .click(), so Enter threw
                // "cell.click is not a function" and the inspector never opened.
                // Every consumer listens via addEventListener, so a bubbling
                // MouseEvent is identical for the HTML cells and the only thing
                // that works for the SVG ones.
                cell.dispatchEvent(new MouseEvent('click',
                    { bubbles: true, cancelable: true, view: window }));
                return;
            }
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
    document.body.addEventListener('pulses-rows-changed', onStateMutated);   // a value change (docs/141 4j)
    document.body.addEventListener('diagnostics-changed', onStateMutated);

    // Cleanup on navigation away from the topology view.
    document.body.addEventListener('htmx:beforeSwap', function cleanup(evt) {
        if (evt.detail.target && evt.detail.target.id === 'table-pane') {
            clearInterval(pollTimer);
            clearTimeout(debounceTimer);
            clearTimeout(metricsRefreshTimer);
            document.body.removeEventListener('pulses-changed', onStateMutated);
            document.body.removeEventListener('pulses-rows-changed', onStateMutated);
            document.body.removeEventListener('diagnostics-changed', onStateMutated);
            document.body.removeEventListener('htmx:beforeSwap', cleanup);
        }
    });
};

/* ── docs/120 items 5+9: chip-wide Trends ────────────────────────────────
 *
 * Customer: "to see T1/RB/T2 trends today you go to Param History, but that
 * shows PER-QUBIT trends, not an INTEGRATED one. Add a Trends tab under Chip
 * Status where ALL qubits' T1 appear in a SINGLE plot. A multiple-line plot,
 * legend = all qubits."
 *
 * One chart per metric, one trace per qubit, legend = the qubit ids. The
 * colorway is UI_CONFIG.plotly.colorway, which app.js documents as being for
 * exactly this ("Plotly cycles through these colors to draw each qubit's
 * line"), so the chip-wide view matches every other multi-qubit surface.
 */
window.ChipTrends = (function () {
    function _params() {
        var box = document.getElementById('topo-trends');
        var sel = [];
        if (box) {
            Array.prototype.slice.call(box.querySelectorAll('.topo-trend-chip.active'))
                .forEach(function (b) { sel.push(b.getAttribute('data-trend-metric')); });
        }
        var pathEl = document.getElementById('topo-trend-path');
        var q = 'metrics=' + encodeURIComponent(sel.join(','));
        if (pathEl && pathEl.value.trim()) q += '&path=' + encodeURIComponent(pathEl.value.trim());
        return q;
    }
    /* docs/122 item 4 — `source` is not optional here.
       Without it htmx runs the request with elt = document.body and queues it
       against every other body-sourced request in the app; measured, a metric
       toggle's /topology/trends was simply NEVER SENT while two /workspace/tree
       polls held the body queue, and the chips lit with no chart behind them.
       Sourcing it on the element it targets also gives htmx the per-element
       bookkeeping that stops a fast second toggle racing the first. */
    var _reloadSeq = 0;
    function _reload() {
        if (!window.htmx || !document.getElementById('topo-trends')) return;
        var mine = ++_reloadSeq;
        var p = htmx.ajax('GET', '/topology/trends?' + _params(),
                          { source: '#topo-trends', target: '#topo-trends',
                            swap: 'outerHTML' });
        // A late response swaps into a target that no longer exists (htmx
        // resolves the target eagerly), and its charts are silently lost. If we
        // are no longer the newest request, or the section went away, re-render
        // from whatever the DOM now holds rather than leaving empty boxes.
        var settle = function () {
            if (mine !== _reloadSeq) return;
            var host = document.getElementById('topo-trends');
            if (!host) return;
            var data = document.getElementById('topo-trends-data');
            if (!data) return;
            // Chain-presence, not class sniffing (docs/124 minor): the
            // fragment's inline script renders through the ASYNC chain, so at
            // settle time the class/svg may simply not exist yet — the old
            // '.js-plotly-plot' sniff double-rendered every toggle (and the
            // class itself was proven strippable, docs/124 §1.1). The render
            // entry sets __plotlyRenderChain SYNCHRONOUSLY at call time, so
            // its presence on any chart host is the deterministic "a render
            // is already owed" signal; the fallback fires only when the
            // inline script genuinely never ran (the late-response case this
            // fallback exists for).
            var started = false;
            host.querySelectorAll('.topo-trend-chart').forEach(function (el) {
                if (el.__plotlyRenderChain || el._fullLayout) started = true;
            });
            if (!started) {
                try { render(JSON.parse(data.textContent)); } catch (e) {}
            }
        };
        if (p && typeof p.then === 'function') p.then(settle, settle);
    }
    function toggle(metric) {
        var b = document.querySelector('.topo-trend-chip[data-trend-metric="' + metric + '"]');
        if (b) {
            var on = b.classList.toggle('active');
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        }
        _reload();
    }
    function setPath(p) {
        var el = document.getElementById('topo-trend-path');
        if (el) el.value = p || '';
        var s = document.getElementById('topo-trend-suggest');
        if (s) s.hidden = true;
        _reload();
    }
    var _sugTimer = null;
    function suggest(q) {
        clearTimeout(_sugTimer);
        var box = document.getElementById('topo-trend-suggest');
        if (!box) return;
        if (!q || q.trim().length < 2) { box.hidden = true; return; }
        _sugTimer = setTimeout(function () {
            fetch('/topology/trends/paths?q=' + encodeURIComponent(q.trim()))
                .then(function (r) { return r.json(); })
                .then(function (rows) {
                    if (!rows || !rows.length) { box.hidden = true; return; }
                    box.innerHTML = rows.map(function (r) {
                        var p = (typeof r === 'string') ? r : (r.path || r.dot_path || '');
                        return '<button type="button" class="topo-trend-sug"'
                             + ' onclick="ChipTrends.setPath(this.textContent)">'
                             + p.replace(/[&<>"]/g, function (c) {
                                 return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
                               }) + '</button>';
                    }).join('');
                    box.hidden = false;
                })
                .catch(function () { box.hidden = true; });
        }, 220);
    }
    /* Charts arrive as [{metric, series:[{entity, points:[[snapId, value]]}]}].

       The instant is DERIVED here, not shipped: it is a pure reformat of the
       snapshot id, and sending it too cost 61 bytes/point — up to 2.3 MB of
       HTML for one section on a 419-snapshot chip. `_iso` is the character-for-
       character twin of `routes._snap_iso`, including its refusal to guess:
       an id that does not parse returns null and keeps its raw label rather
       than being placed at a fabricated instant. */
    var _TS_RE = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/;
    function _iso(ts) {
        var m = _TS_RE.exec(String(ts == null ? '' : ts));
        if (!m) return null;
        return m[1] + '-' + m[2] + '-' + m[3] + 'T' + m[4] + ':' + m[5] + ':' + m[6];
    }

    /* The axis is TIME, not the snapshot sequence. A category axis spaces 433
       snapshots evenly, which silently redraws three quiet weeks and two
       minutes of frantic retuning as the same distance — on the one page whose
       question is "when did this drift". The snapshot id stays in the hover,
       because that is what a user carries over to State History.

       An id that does not parse to an instant keeps its raw label rather than
       being placed at an invented one; such a chart falls back to the category
       axis WHOLESALE, since mixing the two would put the unparsed points at
       epoch zero. */
    /* Above this many drawn nodes SVG genuinely hurts (the server caps a series
       at 400 points, so 20 qubits x 400 = 8,000 is the real worst case). Below
       it, SVG is faster to first paint and CANNOT fail the way GL can. */
    var GL_MIN_NODES = 4000;
    var _glBudget = 0;          // WebGL contexts we are willing to spend, per render

    /* Plotly does not throw when a WebGL context is refused — it writes "WebGL
       is not supported by your browser" into the div and returns normally. So
       the only way to know is to LOOK, and the only honest response is to draw
       the same data again as SVG rather than leave the user staring at a chart
       that silently contains no data. */
    function _healIfBlank(host, traces, layout, config) {
        if (!host || !/WebGL is not supported/i.test(host.innerHTML || '')) return;
        var svg = traces.map(function (t) {
            var c = {}; for (var k in t) if (Object.prototype.hasOwnProperty.call(t, k)) c[k] = t[k];
            c.type = 'scatter';
            return c;
        });
        try { window._plotlyRender(host, svg, layout, config); } catch (e) { /* nothing left to try */ }
    }

    function _axisFor(series) {
        var allDated = true;
        series.forEach(function (s) {
            s.points.forEach(function (p) { if (!_iso(p[0])) allDated = false; });
        });
        return allDated ? 'date' : 'category';
    }
    function render(charts) {
        if (!window._plotlyRender || !charts) return;
        // One budget per render pass, spent by the first genuinely dense chart.
        _glBudget = 1;
        charts.forEach(function (c, idx) {
            // docs/122 item 4: the host id is POSITIONAL, so a response from an
            // older generation would draw into whatever now sits at that index.
            // Prefer the box that carries this chart's own metric and fall back
            // to the index only when the markup predates that attribute.
            var box = c.metric && document.querySelector(
                '.topo-trend-box[data-trend-metric="' + String(c.metric).replace(/"/g, '\\"') + '"]');
            var host = (box && box.querySelector('.topo-trend-chart'))
                    || document.getElementById('topo-trend-' + idx);
            if (!host || !c.series || !c.series.length) return;
            // WebGL is EARNED BY NODE COUNT, and only while contexts remain.
            //
            // The first cut gated on `series.length > 8` — the qubit count —
            // so every chip bigger than 8 qubits went to WebGL no matter how
            // little data it had. On the real 20-qubit chip that meant 20
            // series x 7 points = 140 nodes rendered through GL, and, worse,
            // THREE scattergl charts on one page. Each takes ~3 WebGL contexts,
            // browsers cap the total (~16), and Plotly's failure mode when a
            // context is refused is to REPLACE THE CHART with the text "WebGL
            // is not supported by your browser". Measured in real Chrome with a
            // working GPU (Intel Arc / ANGLE D3D11): all three Trends charts
            // blank, axes and legend drawn, not one data point visible.
            //
            // So: GL only for a genuinely large chart, and at most one per
            // render — plus the post-draw fallback below, because a silent
            // blank chart on the page whose whole job is showing values move is
            // the worst failure this surface can have.
            var longest = c.series.reduce(function (m, s) {
                return Math.max(m, s.points.length); }, 0);
            var nodes = c.series.length * longest;
            var dense = nodes > GL_MIN_NODES && _glBudget > 0;
            if (dense) _glBudget--;
            var axisType = _axisFor(c.series);
            // A parameter that has NOT moved is the common case on a healthy
            // chip, and Plotly's autorange for a set of identical small values
            // spans zero: readout_amplitude = 0.00447 chip-wide drew a flat
            // line at 0 on a -0.5..1 axis, which reads as "this is zero".
            // Give a constant series a range around ITS OWN value instead.
            var _lo = Infinity, _hi = -Infinity;
            c.series.forEach(function (s2) {
                s2.points.forEach(function (p) {
                    if (typeof p[1] === 'number' && isFinite(p[1])) {
                        if (p[1] < _lo) _lo = p[1];
                        if (p[1] > _hi) _hi = p[1];
                    }
                });
            });
            var _flat = null;
            if (isFinite(_lo) && isFinite(_hi)) {
                var _span = _hi - _lo;
                var _scale = Math.max(Math.abs(_lo), Math.abs(_hi));
                if (_scale > 0 && _span <= _scale * 1e-9) {
                    var _pad = _scale * 0.05;
                    _flat = [_lo - _pad, _hi + _pad];
                }
            }
            var traces = c.series.map(function (s) {
                return {
                    x: s.points.map(function (p) {
                        return axisType === 'date' ? _iso(p[0]) : p[0]; }),
                    y: s.points.map(function (p) { return p[1]; }),
                    // The snapshot id, carried per point so the hover can name
                    // the snapshot the value came from even on a date axis.
                    customdata: s.points.map(function (p) { return p[0]; }),
                    mode: longest > 120 ? 'lines' : 'lines+markers',
                    type: dense ? 'scattergl' : 'scatter', name: s.entity,
                    connectgaps: false, marker: { size: 5 },
                    hovertemplate: '%{fullData.name}<br>%{x}<br>%{y}'
                                 + '<br><span style="font-size:.85em">%{customdata}</span>'
                                 + '<extra></extra>',
                };
            });
            var layout = {
                margin: { l: 58, r: 12, t: 8, b: 64 },
                height: 300,
                showlegend: true,
                legend: { orientation: 'h', y: -0.28, font: { size: 10 } },
                colorway: (window.UI_CONFIG && UI_CONFIG.plotly && UI_CONFIG.plotly.colorway) || undefined,
                xaxis: { type: axisType, tickangle: axisType === 'date' ? 0 : -40,
                         tickfont: { size: 9 }, automargin: true },
                yaxis: { title: { text: c.metric + (c.unit ? ' (' + c.unit + ')' : ''),
                                  font: { size: 11 } },
                         // SI prefixes, not US-billions: a 4.333 GHz qubit read
                         // "4.3B" on an axis whose only other label was the bare
                         // metric name. `~s` gives 4.3G, and the unit now sits
                         // in the title, so the axis says what it means.
                         tickformat: '~s',
                         tickfont: { size: 10 }, automargin: true,
                         range: _flat || undefined,
                         autorange: _flat ? false : true },
                plot_bgcolor: 'transparent', paper_bgcolor: 'transparent',
            };
            // Zoom/pan matter MORE here than anywhere else in the app: the
            // question this page answers is "when did this drift", which needs
            // a closer look at a region.
            var cfg = {
                displayModeBar: 'hover', responsive: true,
                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                displaylogo: false };
            // House theme AT RENDER (docs/124 M-6): these charts used to ship
            // Plotly's light defaults and rely on a later retheme() pass to
            // recolor them — but the one-shot retheme runs at DOMContentLoaded
            // +800ms and Trends builds later (lazy observer + fetch), so a
            // dark-theme user saw #444-on-light charts until the next theme
            // toggle. houseLayout deep-merges UNDER the overrides above, so
            // every explicit choice here (tickformat, ranges, transparent
            // backgrounds) stands.
            if (window.PlotTheme && window.PlotTheme.houseLayout) {
                layout = window.PlotTheme.houseLayout(layout);
            }
            var drawn = window._plotlyRender(host, traces, layout, cfg);
            // Plotly writes the WebGL message asynchronously, after the promise
            // its renderer returns; check on the far side of it, and once more
            // on the next frame for the case where it lands later still.
            if (drawn && typeof drawn.then === 'function') {
                drawn.then(function () {
                    _healIfBlank(host, traces, layout, cfg);
                    setTimeout(function () { _healIfBlank(host, traces, layout, cfg); }, 250);
                });
            } else {
                setTimeout(function () { _healIfBlank(host, traces, layout, cfg); }, 250);
            }
        });
        /* docs/122 item 4 — keep the figures matched to their container.
           Plotly's `responsive:true` listens to WINDOW resize only (verified on
           2.35.2): collapsing the sidebar left a 609 px SVG in a 742 px holder
           and it was still 609 px six seconds later, while a window resize
           healed it instantly. A split-gutter drag is the same story at 56 px.
           Observed on the grid, so one observer covers every chart in it. */
        if (window.PlotHost) {
            var grid = document.querySelector('.topo-trends-grid')
                    || document.getElementById('topo-trends');
            if (grid) window.PlotHost.observe(grid);
        }
    }
    return { toggle: toggle, setPath: setPath, suggest: suggest, render: render };
})();
