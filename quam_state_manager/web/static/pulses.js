/* Pulses page module — library table + pulse detail inspector + create form.
 *
 * House conventions: IIFE exposing window.PulsesPage (like generate.js /
 * chip-status.js); plots go through window._plotlyRender (theme-aware,
 * purge-on-swap handled by app.js's htmx:beforeSwap hook); idempotent init
 * guarded by a marker on the root node.
 *
 * Commit model: house-style instant per-field commit (Enter submits the row
 * form → /pulse/edit → full detail re-render + tray OOB). The LIVE preview
 * is decoupled from commit: typing/sliding fires a debounced, stateless
 * POST /api/pulse/synth with the current (uncommitted) values and draws the
 * result as a dashed overlay on top of the solid committed trace.
 */
window.PulsesPage = (function () {
    'use strict';

    var PREVIEW_DEBOUNCE_MS = 150;
    var _gen = 0;  // fetch-generation counter — stale responses are dropped

    /* docs/141 4e -- the committed plot follows undo / redo, from RAM when
       it can. The detail render stashes the committed waveform as
       root._committedPlot; an undo used to revert the VALUES in place and
       then draw that stale plot (the pre-undo waveform under the reverted
       numbers). Now every committed waveform this page has seen is cached
       by (pulse path + the committed value of every parameter), bounded to
       the last PLOT_CACHE_MAX states, so Ctrl+Z / Ctrl+Shift+Z back to a
       state already drawn costs no synth request -- a burst of presses is
       one debounced refresh, and a miss is ONE synth call for the final
       state (its own generation token: a preview fetch racing it can never
       drop it, and it can never draw over a newer one). */
    var PLOT_CACHE_MAX = 20;
    var COMMITTED_REFRESH_MS = 120;
    var _plotCache = new Map();          // key -> plot payload (insertion-ordered)

    function committedKey(root) {
        var vals = {};
        root.querySelectorAll('input[data-param]').forEach(function (input) {
            vals[input.getAttribute('data-param')] = input.getAttribute('data-committed') || '';
        });
        return (root.getAttribute('data-pulse-path') || '') + '|' + JSON.stringify(vals);
    }
    function cacheCommitted(root, plot) {
        if (!root || !plot || !plot.ok) return;
        var key = committedKey(root);
        if (_plotCache.has(key)) _plotCache.delete(key);   // refresh recency
        _plotCache.set(key, plot);
        while (_plotCache.size > PLOT_CACHE_MAX) {
            _plotCache.delete(_plotCache.keys().next().value);
        }
    }
    function refreshCommittedPlot(root) {
        if (!root || !document.body.contains(root)) return;
        var key = committedKey(root);
        var hit = _plotCache.get(key);
        if (hit) {
            root._committedPlot = hit;
            root._cpPending = false;
            schedulePreview(root);
            return;
        }
        var gen = (root._cpGen = (root._cpGen || 0) + 1);
        fetch('/api/pulse/synth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: root.getAttribute('data-pulse-path'), params: {} })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (gen !== root._cpGen || !document.body.contains(root)) return;
            if (data && data.ok && data.plot && data.plot.ok) {
                root._committedPlot = data.plot;
                cacheCommitted(root, data.plot);
            }
            root._cpPending = false;
            schedulePreview(root);
        }).catch(function () {
            if (gen !== root._cpGen) return;
            root._cpPending = false;         // keep the last plot; never wedge the preview
            schedulePreview(root);
        });
    }
    document.addEventListener('cellsReverted', function (evt) {
        var root = detailRoot();
        if (!root) return;
        var p = root.getAttribute('data-pulse-path') || '';
        var entries = ((evt && evt.detail) || {}).entries || [];
        var mine = entries.some(function (e) {
            var dp = (e && e.dot_path) || '';
            return dp === p || dp.indexOf(p + '.') === 0;
        });
        if (!mine) return;
        root._cpPending = true;              // the stale committed plot must not be drawn meanwhile
        _debounce('pulse-committed-refresh', function () { refreshCommittedPlot(root); },
                  COMMITTED_REFRESH_MS);
    });

    /* ------------------------------------------------------------------ */
    /* Plot rendering                                                      */
    /* ------------------------------------------------------------------ */

    function cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement)
            .getPropertyValue(name).trim();
        return v || fallback;
    }

    function traceColors() {
        return {
            // primary hue for I, a fixed both-theme-safe teal for Q
            i: cssVar('--pico-primary', '#1095c1'),
            q: '#2bb673'
        };
    }

    var OVERLAY_HUES = ['#e67e22', '#9b59b6', '#e74c3c', '#f1c40f', '#1abc9c'];

    // The detail plot's overlays live on the detail root (so every existing
    // renderPulsePlot call site — commit, preview, Esc, verify, settle —
    // draws them without threading a new argument through each).
    function activeOverlays(divId) {
        if (divId !== 'pulse-detail-plot') return [];
        var root = detailRoot();
        return (root && root._overlays) ? root._overlays.filter(function (o) { return o.on; }) : [];
    }

    // Short label for a pulse path: "q1-2 · cz_bipolar.coupler_flux_pulse",
    // "q1 · xy.x180_DragCosine".
    function overlayLabel(path) {
        var m = /^qubit_pairs\.([^.]+)\.macros\.([^.]+)\.([^.]+)$/.exec(path)
             || /^qubits\.([^.]+)\.([^.]+)\.operations\.([^.]+)$/.exec(path)
             || /^qubit_pairs\.([^.]+)\.([^.]+)\.operations\.([^.]+)$/.exec(path);
        return m ? m[1] + ' · ' + m[2] + '.' + m[3] : path;
    }

    function buildOverlayBar(root) {
        var bar = root.querySelector('.pulse-overlay-bar');
        if (!bar) return;
        var chips = bar.querySelector('.pulse-overlay-chips');
        var pick = bar.querySelector('.pulse-overlay-pick');
        var own = root.getAttribute('data-pulse-path');
        var overlays = root._overlays || [];
        chips.innerHTML = '';
        overlays.forEach(function (o, i) {
            var lab = document.createElement('label');
            lab.className = 'pulse-overlay-chip' + (o.on ? ' on' : '');
            lab.style.setProperty('--ov-hue', OVERLAY_HUES[i % OVERLAY_HUES.length]);
            var cb = document.createElement('input');
            cb.type = 'checkbox'; cb.checked = !!o.on;
            cb.setAttribute('data-overlay', o.path);
            cb.addEventListener('change', function () {
                o.on = cb.checked;
                lab.classList.toggle('on', o.on);
                schedulePreview(root);
            });
            lab.appendChild(cb);
            lab.appendChild(document.createTextNode(' ' + o.label));
            if (o.plot && !o.plot.ok) {
                var err = document.createElement('span');
                err.className = 'muted'; err.textContent = ' (no waveform)';
                err.title = o.plot.error || '';
                lab.appendChild(err);
            }
            if (o.source === 'picked') {
                var x = document.createElement('button');
                x.type = 'button'; x.className = 'pulse-overlay-x';
                x.title = 'Remove overlay'; x.textContent = '×';
                x.addEventListener('click', function () {
                    root._overlays = root._overlays.filter(function (q) { return q !== o; });
                    buildOverlayBar(root);
                    schedulePreview(root);
                });
                lab.appendChild(x);
            }
            chips.appendChild(lab);
        });
        // The picker offers every OTHER pulse the library table lists (same
        // page, live DOM) that is not already an overlay.
        var have = {};
        overlays.forEach(function (o) { have[o.path] = 1; });
        var opts = [];
        document.querySelectorAll('.pulse-sel-chk[data-path]').forEach(function (cb) {
            var p = cb.getAttribute('data-path');
            if (!p || p === own || have[p]) return;
            opts.push(p);
        });
        pick.innerHTML = '<option value="">+ add pulse…</option>';
        opts.forEach(function (p) {
            var op = document.createElement('option');
            op.value = p; op.textContent = overlayLabel(p);
            pick.appendChild(op);
        });
        pick.hidden = opts.length === 0;
        pick.value = '';
        if (!pick._bound) {
            pick._bound = true;
            pick.addEventListener('change', function () {
                var p = pick.value;
                if (!p) return;
                pick.value = '';
                addOverlay(root, p);
            });
        }
        bar.hidden = overlays.length === 0 && opts.length === 0;
    }

    function addOverlay(root, path) {
        var entry = { path: path, label: overlayLabel(path), plot: null, on: true, source: 'picked' };
        root._overlays = (root._overlays || []).concat([entry]);
        buildOverlayBar(root);
        // A plain fetch, not fetchSynth: the preview's generation counter must
        // not cancel this, nor this the preview.
        fetch('/api/pulse/synth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path, params: {} })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!document.body.contains(root)) return;
            entry.plot = (data && data.plot) ? data.plot : { ok: false, error: (data && data.error) || 'synth failed' };
            buildOverlayBar(root);
            schedulePreview(root);
        }).catch(function () {
            entry.plot = { ok: false, error: 'network error' };
            buildOverlayBar(root);
        });
    }

    /**
     * Render the detail/create plot. committed = {traces:[{name,x,y}],...};
     * preview / verify are optional same-shape overlays.
     */
    function renderPulsePlot(divId, committed, preview, verify) {
        var colors = traceColors();
        var data = [];

        function pushTraces(plot, suffix, dash, opacity) {
            if (!plot || !plot.ok || !plot.traces) return;
            plot.traces.forEach(function (t) {
                data.push({
                    x: t.x, y: t.y,
                    name: t.name + suffix,
                    mode: 'lines',
                    line: {
                        color: t.name === 'Q' ? colors.q : colors.i,
                        width: dash ? 1.6 : 2,
                        dash: dash || 'solid'
                    },
                    opacity: opacity || 1,
                    hovertemplate: t.name + suffix + ': %{y:.6g} V<br>%{x} ns<extra></extra>'
                });
            });
        }

        // Customer ask (2026-08-27): overlays — other pulses drawn on the same
        // time × voltage axes, beneath the committed trace. A CZ macro's
        // companion (qubit flux ↔ coupler flux) is on by default; picked
        // pulses join through the overlay bar. Each overlay keeps one hue.
        activeOverlays(divId).forEach(function (o, idx) {
            if (!o.plot || !o.plot.ok || !o.plot.traces) return;
            var hue = OVERLAY_HUES[idx % OVERLAY_HUES.length];
            var multi = o.plot.traces.length > 1;
            o.plot.traces.forEach(function (t) {
                var nm = o.label + (multi ? ' ' + t.name : '');
                data.push({
                    x: t.x, y: t.y, name: nm, mode: 'lines',
                    line: { color: hue, width: 1.8,
                            dash: (multi && t.name === 'Q') ? 'dot' : 'solid' },
                    opacity: 0.9,
                    hovertemplate: nm + ': %{y:.6g} V<br>%{x} ns<extra></extra>'
                });
            });
        });
        pushTraces(committed, '', null, 1);
        pushTraces(preview, ' (preview)', 'dash', 0.85);
        pushTraces(verify, ' (config)', 'dot', 0.9);

        // House Plotly conventions (showWaveformPlot / trend charts): plain
        // string axis titles, horizontal legend BELOW the plot (y < 0) with
        // the bottom margin reserving its room. The previous above-plot
        // legend (y: 1.12 with t: 8) pushed the axes + legend out of the
        // 260px container — Plotly does not auto-expand margins for legends.
        var layout = {
            margin: { l: 50, r: 10, t: 10, b: 40 },
            xaxis: { title: 'time (ns)', zeroline: false },
            yaxis: { title: 'amplitude (V)', zeroline: true },
            showlegend: data.length > 1,
            legend: { orientation: 'h', y: -0.25 },
            font: { size: 11, color: cssVar('--pico-color', '#888') },
            height: 260
        };
        return window._plotlyRender(divId, data, layout,
            { displayModeBar: false, responsive: true });
    }

    /* ------------------------------------------------------------------ */
    /* Shared preview engine                                               */
    /* ------------------------------------------------------------------ */

    function showSynthErr(root, msg) {
        var el = root.querySelector('.pulse-synth-err');
        if (!el) return;
        el.textContent = msg || '';
        el.hidden = !msg;
    }

    function fetchSynth(body, cb) {
        var gen = ++_gen;
        fetch('/api/pulse/synth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (gen !== _gen) return;  // a newer request superseded this one
            cb(data);
        }).catch(function () { /* keep the last plot on network errors */ });
    }

    /* ------------------------------------------------------------------ */
    /* Detail: lifecycle + live preview + interactions                     */
    /* ------------------------------------------------------------------ */

    function detailData() {
        var el = document.getElementById('pulse-detail-data');
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }

    function detailRoot() {
        return document.getElementById('pulse-detail-root');
    }

    function collectOverrides(root) {
        /* All [data-param] inputs whose value differs from data-committed —
           pointer-row inputs show the resolved value, so a typed change
           becomes a literal override for the preview only. */
        var overrides = {};
        var dirty = false;
        root.querySelectorAll('input[data-param]').forEach(function (input) {
            var committed = input.getAttribute('data-committed') || '';
            if (input.value === committed) return;
            dirty = true;
            if (input.getAttribute('data-synth') === '0') return;
            overrides[input.getAttribute('data-param')] = input.value;
        });
        return { overrides: overrides, dirty: dirty };
    }

    function updateDirtyUI(root, dirty) {
        var pill = root.querySelector('.pulse-dirty-pill');
        if (pill) pill.hidden = !dirty;
    }

    function schedulePreview(root) {
        _debounce('pulse-synth-preview', function () {
            var state = collectOverrides(root);
            updateDirtyUI(root, state.dirty);
            if (!state.dirty || Object.keys(state.overrides).length === 0) {
                // no shape-relevant changes — drop the overlay. Bump the fetch
                // generation so an ALREADY in-flight synth (fired before an Esc
                // reset elapsed the debounce) is dropped as stale when it
                // resolves, instead of re-drawing the discarded value's preview.
                _gen++;
                if (root._cpPending) return;   // a committed refresh is due (docs/141 4e); it renders
                renderPulsePlot('pulse-detail-plot', root._committedPlot);
                showSynthErr(root, '');
                return;
            }
            fetchSynth({
                path: root.getAttribute('data-pulse-path'),
                params: state.overrides
            }, function (data) {
                if (!document.body.contains(root)) return;
                if (data.ok && data.plot && data.plot.ok) {
                    renderPulsePlot('pulse-detail-plot',
                        root._committedPlot, data.plot);
                    showSynthErr(root, '');
                } else {
                    showSynthErr(root, data.error
                        || firstParamError(data.param_errors));
                }
            });
        }, PREVIEW_DEBOUNCE_MS);
    }

    function firstParamError(paramErrors) {
        if (!paramErrors) return 'preview failed';
        var keys = Object.keys(paramErrors);
        return keys.length ? keys[0] + ': ' + paramErrors[keys[0]]
                           : 'preview failed';
    }

    function initDetail() {
        var root = detailRoot();
        if (!root || root._pulsesInit) return;

        var data = detailData();
        if (!data) return;
        root._committedPlot = data.plot;
        cacheCommitted(root, data.plot);   // docs/141 4e: this state is drawn -- remember it
        // Same-component companions from the server (a CZ macro's qubit flux
        // + coupler flux), on by default; the picker adds any other pulse.
        root._overlays = (data.overlays || []).map(function (o) {
            return { path: o.path, label: o.label, plot: o.plot,
                     on: o.default_on !== false, source: 'component' };
        });
        try { buildOverlayBar(root); } catch (e) { console.error('overlay bar failed', e); }

        // Attach interaction listeners FIRST, independent of the plot render.
        // A render throw must NEVER leave the param inputs dead: the previous
        // version set _pulsesInit before rendering, so a first-render error
        // both skipped listener binding AND permanently blocked re-init —
        // the "number inputs become unclickable" symptom (feedback #5).
        root.addEventListener('input', function (evt) {
            if (!evt.target.matches || !evt.target.matches('input[data-param]')) return;
            schedulePreview(root);
        });
        root.addEventListener('keydown', function (evt) {
            if (evt.key !== 'Escape') return;
            var input = evt.target;
            if (!input.matches || !input.matches('input[data-param]')) return;
            input.value = input.getAttribute('data-committed') || '';
            schedulePreview(root);
            evt.preventDefault();
        });
        root._pulsesInit = true;   // only after listeners are bound

        if (data.plot && data.plot.ok) {
            if (data.plot.decimated) {
                var note = root.querySelector('.pulse-decimated-note');
                if (note) note.hidden = false;
            }
            // Fast first paint for responsiveness.
            requestAnimationFrame(function () {
                if (!document.body.contains(root)) return;
                try { renderPulsePlot('pulse-detail-plot', root._committedPlot); }
                catch (e) { console.error('pulse plot render failed', e); }
            });
            // Then ONE clean purge + re-render once the post-swap DOM has fully
            // settled. Rendering right after the pulse swap (which also triggers
            // a Split.js pane destroy/recreate) intermittently bound Plotly's
            // hover/drag layer against a transient geometry, leaving the plot
            // drawn but with collapsed axes + dead hover/zoom. A manual
            // purge + newPlot a moment later ALWAYS restored it in the browser
            // (PROBE5); this replicates exactly that, so it's correct regardless
            // of the precise mid-reflow cause. The ResizeObserver is attached
            // only here, after the final render, so it never disturbs it.
            setTimeout(function () {
                if (!document.body.contains(root)) return;
                try {
                    var el = document.getElementById('pulse-detail-plot');
                    if (el && window.Plotly) { try { window.Plotly.purge(el); } catch (e) {} }
                    var p = renderPulsePlot('pulse-detail-plot', root._committedPlot);
                    var attach = function () {
                        if (document.body.contains(root)) observePlotResize(root, 'pulse-detail-plot');
                    };
                    if (p && typeof p.then === 'function') p.then(attach); else attach();
                } catch (e) {
                    console.error('pulse plot settle render failed', e);
                }
            }, 250);
        } else {
            var plotEl = document.getElementById('pulse-detail-plot');
            if (plotEl) plotEl.classList.add('pulse-plot-empty');
        }
    }

    /* Keep the Plotly chart sized to its container. Split.js resizing the
       inspector pane does NOT fire a window resize, so Plotly (responsive)
       never re-measures and the axes/legend drift after a pane drag + pulse
       re-select. A ResizeObserver on the container re-lays-out on any size
       change; one per detail render, GC'd with the swapped-out root. */
    function observePlotResize(root, divId) {
        if (typeof ResizeObserver === 'undefined') return;
        var el = document.getElementById(divId);
        if (!el || root._plotObserver) return;
        // ResizeObserver ALWAYS fires once synchronously when observe() starts.
        // That initial callback would resize the just-rendered plot and break it
        // (axes/hover), so skip it — only react to REAL later size changes (e.g.
        // a Split.js pane drag, which doesn't fire a window resize).
        var primed = false;
        var ro = new ResizeObserver(function () {
            if (!document.body.contains(el)) { ro.disconnect(); return; }
            if (!primed) { primed = true; return; }
            // docs/122: Plots.resize was measured as a no-op on this app's
            // charts, so this observer has been firing correctly and achieving
            // nothing. The `primed` skip above is KEPT — it exists because
            // resizing the just-rendered plot broke its axes/hover, and that
            // reason is unaffected by which call does the resizing.
            if (window.PlotHost && el.data) window.PlotHost.resizeWithin(el);
        });
        ro.observe(el);
        root._plotObserver = ro;
    }

    /* ---- sliders ---- */

    function sliderBounds(input) {
        var kind = input.getAttribute('data-kind');
        var key = input.getAttribute('data-param') || '';
        var unit = input.getAttribute('data-unit') || '';
        var committed = parseFloat(input.getAttribute('data-committed'));
        if (!isFinite(committed)) committed = kind === 'int' ? 100 : 0.1;
        if (unit === 'cycles') {
            return { min: -1, max: 1, step: 0.001 };
        }
        if (unit === 'rad' || /angle|phase/.test(key)) {
            return { min: -Math.PI, max: Math.PI, step: 0.001 };
        }
        if (kind === 'int') {
            var hi = Math.max(8, Math.ceil(Math.abs(committed) * 4));
            return { min: 0, max: hi, step: 1 };
        }
        var span = Math.max(Math.abs(committed) * 2, 0.01);
        return { min: -span, max: span, step: span / 500 };
    }

    function toggleParamSlider(btn) {
        var row = btn.closest('tr');
        var root = detailRoot();
        if (!row || !root) return;
        var existing = row.parentNode.querySelector(
            '.pulse-slider-row[data-for="' + row.rowIndex + '"]');
        if (existing) { existing.remove(); return; }

        var input = row.querySelector('input[data-param]');
        if (!input) return;
        var bounds = sliderBounds(input);

        var tr = document.createElement('tr');
        tr.className = 'pulse-slider-row';
        tr.setAttribute('data-for', row.rowIndex);
        var td = document.createElement('td');
        td.colSpan = 2;
        var slider = document.createElement('input');
        slider.type = 'range';
        slider.min = bounds.min;
        slider.max = bounds.max;
        slider.step = bounds.step;
        slider.value = parseFloat(input.value) || 0;
        slider.addEventListener('input', function () {
            input.value = slider.value;
            schedulePreview(root);
        });
        var commit = document.createElement('button');
        commit.type = 'button';
        commit.className = 'btn-sm pulse-slider-commit';
        commit.textContent = '✓ commit';
        commit.title = 'Commit this value';
        commit.addEventListener('click', function () {
            input.closest('form').requestSubmit();
        });
        td.appendChild(slider);
        td.appendChild(commit);
        tr.appendChild(td);
        row.parentNode.insertBefore(tr, row.nextSibling);
    }

    /* ---- action toggles ---- */

    function toggleBlock(btn, selector) {
        var root = detailRoot();
        if (!root) return;
        var block = root.querySelector(selector);
        if (!block) return;
        block.hidden = !block.hidden;
        if (!block.hidden) {
            var input = block.querySelector('input[type="text"]');
            if (input) { input.focus(); input.select(); }
        }
    }

    function startRename(btn) { toggleBlock(btn, '.pulse-rename-form'); }
    function cancelRename(btn) { toggleBlock(btn, '.pulse-rename-form'); }
    function startDuplicate(btn) { toggleBlock(btn, '.pulse-duplicate-form'); }
    function cancelDuplicate(btn) { toggleBlock(btn, '.pulse-duplicate-form'); }
    function askDelete(btn) { toggleBlock(btn, '.pulse-delete-confirm'); }
    function cancelDelete(btn) { toggleBlock(btn, '.pulse-delete-confirm'); }

    /* ---- Verify vs generated config (ground truth) ---- */

    function verifyNote(root, html, level) {
        var el = root.querySelector('.pulse-verify-note');
        if (!el) return;
        el.innerHTML = html || '';
        el.hidden = !html;
        el.className = 'pulse-verify-note' + (level ? ' pulse-verify-' + level : '');
    }

    // HTML-escape server-derived strings before they are interpolated into the
    // verify note's innerHTML. The pulse operation name in data.error comes
    // straight from the (possibly shared / corrupted) state.json — without this
    // a crafted op name is a DOM-XSS vector (the CSP allows inline script).
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function verifyPulse(btn) {
        var root = detailRoot();
        if (!root) return;

        if (root._verifyPlot) {  // second click toggles the overlay off
            root._verifyPlot = null;
            renderPulsePlot('pulse-detail-plot', root._committedPlot);
            verifyNote(root, '');
            return;
        }

        verifyNote(root, 'fetching ground truth…');
        var path = root.getAttribute('data-pulse-path');
        fetch('/api/pulse/ground-truth?path=' + encodeURIComponent(path))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!document.body.contains(root)) return;
                if (!data.ok) {
                    if (data.status === 'absent' || data.status === 'not-found') {
                        // absent: no config yet. not-found: pulse newer than
                        // the cached config (created/renamed/duplicated). Both
                        // resolve by (re)generating from the loaded chip.
                        var verb = data.status === 'absent' ? 'Generate now' : 'Regenerate';
                        verifyNote(root,
                            esc(data.error || 'No config to compare against.') +
                            ' <button type="button" class="btn-sm" ' +
                            'onclick="PulsesPage.regenerateThenVerify(this)">' +
                            verb + '</button> <small>(~10–30 s)</small>',
                            'warn');
                    } else {
                        verifyNote(root, esc(data.error || 'lookup failed'), 'warn');
                    }
                    return;
                }
                root._verifyPlot = data.plot;
                renderPulsePlot('pulse-detail-plot', root._committedPlot,
                    null, data.plot);
                var bits = ['config from ' + esc(data.meta.at || '?')];
                if (data.meta.stale) {
                    bits.push('<strong>stale</strong> — generated before your ' +
                        'latest edits; <button type="button" class="btn-sm" ' +
                        'onclick="PulsesPage.regenerateThenVerify(this)">' +
                        'Regenerate</button>');
                    verifyNote(root, bits.join(' · '), 'warn');
                } else if (data.comparison) {
                    if (data.comparison.match) {
                        bits.push('✓ matches the synthesized preview');
                        verifyNote(root, bits.join(' · '), 'ok');
                    } else if (data.comparison.lengths_match === false) {
                        bits.push('length mismatch: synth ' +
                            data.comparison.synth_len + ' vs config ' +
                            data.comparison.truth_len + ' samples');
                        verifyNote(root, bits.join(' · '), 'warn');
                    } else {
                        bits.push('max |Δ| = ' +
                            Number(data.comparison.max_delta).toExponential(2) + ' V');
                        verifyNote(root, bits.join(' · '), 'warn');
                    }
                } else {
                    verifyNote(root, bits.join(' · '));
                }
            })
            .catch(function () { verifyNote(root, 'ground-truth fetch failed', 'warn'); });
    }

    function regenerateThenVerify(btn) {
        var root = detailRoot();
        if (!root) return;
        btn.disabled = true;
        verifyNote(root, 'regenerating config… (this runs the QM stack in a subprocess)');
        fetch('/config/regenerate', { method: 'POST' })
            .then(function (r) {
                return r.text().then(function (text) {
                    return { ok: r.ok, text: text };
                });
            })
            .then(function (res) {
                if (!document.body.contains(root)) return;
                if (!res.ok) {
                    // surface the error text (env not selected, 502, …) and
                    // always offer the escape hatch to pick a Python env, so a
                    // "no env selected" failure isn't a dead end.
                    var tmp = document.createElement('div');
                    tmp.innerHTML = res.text;
                    var errText = (tmp.textContent || 'regenerate failed').trim().slice(0, 300);
                    verifyNote(root, esc(errText) +
                        ' <a class="btn-sm" href="/generate">Choose environment →</a>',
                        'warn');
                    return;
                }
                root._verifyPlot = null;  // force a fresh fetch
                verifyPulse(btn);
            })
            .catch(function () {
                if (document.body.contains(root)) {
                    verifyNote(root, 'regenerate failed', 'warn');
                }
            });
    }

    function startLinkEdit(btn, dotPath, currentRaw) {
        /* Swap the row's value input into pointer-edit mode: prefill the raw
           pointer, switch the form's mode to "pointer". Enter submits. */
        var row = btn.closest('tr');
        if (!row) return;
        var form = row.querySelector('form.pulse-edit-form');
        if (!form) return;
        var modeEl = form.querySelector('input[name="mode"]');
        var valueEl = form.querySelector('input[name="value"]');
        if (!modeEl || !valueEl) return;
        if (modeEl.value === 'pointer') {  // toggle back
            modeEl.value = 'value';
            valueEl.value = valueEl.getAttribute('data-committed') || '';
            valueEl.classList.remove('pulse-pointer-editing');
            return;
        }
        modeEl.value = 'pointer';
        valueEl.value = currentRaw || '#./';
        valueEl.classList.add('pulse-pointer-editing');
        valueEl.focus();
        valueEl.select();
    }

    /* ------------------------------------------------------------------ */
    /* Create form                                                         */
    /* ------------------------------------------------------------------ */

    function createRoot() { return document.getElementById('pulse-create-root'); }

    function parseEmbeddedJson(id) {
        var el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }

    function buildFieldRows(spec) {
        var wrap = document.getElementById('pulse-create-fields');
        if (!wrap) return;
        wrap.innerHTML = '';
        var fs = document.createElement('fieldset');
        fs.className = 'pulse-create-grid';
        spec.params.forEach(function (p) {
            if (p.name === 'length' && spec.length_mode !== 'explicit') return;
            if (p.name === 'id' || p.name === 'digital_marker') return;
            if (!p.synth && !p.required && p.default === null) return;
            var label = document.createElement('label');
            label.className = 'pulse-create-field';
            var span = document.createElement('span');
            span.className = 'pulse-create-field-label';
            span.textContent = p.label + (p.unit ? ' (' + p.unit + ')' : '');
            var input = document.createElement('input');
            input.type = 'text';   // text, not number — pointer strings allowed
            input.name = p.name;
            input.setAttribute('data-kind', p.kind);
            if (p.default !== null && p.default !== undefined) {
                input.value = Array.isArray(p.default)
                    ? p.default.join(', ') : String(p.default);
            } else if (!p.required) {
                input.placeholder = 'none';
            }
            label.appendChild(span);
            label.appendChild(input);
            fs.appendChild(label);
        });
        wrap.appendChild(fs);
    }

    function createCollectParams(root) {
        var params = {};
        root.querySelectorAll('#pulse-create-fields input').forEach(function (input) {
            if (input.value === '') return;
            params[input.name] = input.value;
        });
        return params;
    }

    function schedulCreatePreview(root) {
        _debounce('pulse-synth-create', function () {
            var typeSel = document.getElementById('pulse-create-type');
            if (!typeSel) return;
            fetchSynth({
                qclass: typeSel.value,
                params: createCollectParams(root)
            }, function (data) {
                if (!document.body.contains(root)) return;
                if (data.ok && data.plot && data.plot.ok) {
                    renderPulsePlot('pulse-create-plot', data.plot);
                    showSynthErr(root, '');
                } else {
                    showSynthErr(root, data.error
                        || firstParamError(data.param_errors));
                }
            });
        }, PREVIEW_DEBOUNCE_MS);
    }

    function createTypeChanged(sel) {
        var root = createRoot();
        if (!root || !root._catalog) return;
        var spec = root._catalog[sel.value];
        if (!spec) return;
        var hint = document.getElementById('pulse-create-hint');
        if (hint) {
            hint.textContent = (spec.doc || '') +
                (spec.iq === 'always' ? ' · IQ' : '') +
                (spec.length_mode === 'inferred'
                    ? ' · length auto-inferred (' + '#./inferred_length' + ')' : '') +
                (spec.verify === 'missing'
                    ? ' · ⚠ NOT importable in the selected environment' : '');
            hint.classList.toggle('pulse-hint-envmissing',
                spec.verify === 'missing');
        }
        // r15 (docs/71 §2): class path is READ-ONLY — derived server-side
        // (chip evidence > prefix > env roster canonical > catalog); the
        // hidden input posts it, the <code> shows it.
        var qcInput = document.getElementById('pulse-create-qclass');
        if (qcInput) qcInput.value = spec.qclass || '';
        var qcDisplay = document.getElementById('pulse-create-qclass-display');
        if (qcDisplay) qcDisplay.textContent = spec.qclass || '';
        var qcHint = document.getElementById('pulse-create-qclass-hint');
        if (qcHint) {
            qcHint.textContent =
                spec.qclass_how === 'reused'
                    ? 'copied from an existing ' + sel.value + ' on this chip'
                    : (spec.qclass_how === 'env'
                        ? 'verified by the selected environment (its own import path)'
                        : (spec.qclass_how === 'prefix'
                            ? "derived from this chip's module prefix"
                            : 'catalog default'));
            qcHint.classList.toggle('pulse-qclass-caution',
                spec.qclass_how !== 'reused' && spec.qclass_how !== 'env');
        }
        // Env-only classes have no SM waveform transcription → no preview;
        // say so instead of showing a stale/empty plot.
        var plot = document.getElementById('pulse-create-plot');
        var plotBar = root.querySelector('.pulse-plot-bar');
        if (plot) plot.hidden = !!spec.env_only;
        if (plotBar) plotBar.hidden = !!spec.env_only;
        var envNote = document.getElementById('pulse-create-envnote');
        if (spec.env_only) {
            if (!envNote) {
                envNote = document.createElement('p');
                envNote.id = 'pulse-create-envnote';
                envNote.className = 'muted pulse-env-note';
                var fields = document.getElementById('pulse-create-fields');
                if (fields && fields.parentNode) {
                    fields.parentNode.insertBefore(envNote, fields.nextSibling);
                }
            }
            envNote.textContent = 'Discovered in the selected environment — ' +
                'SM has no waveform transcription for this class, so there ' +
                'is no live preview. Fields come from the env’s own ' +
                'dataclass schema.';
        } else if (envNote) {
            envNote.remove();
        }
        buildFieldRows(spec);
        if (!spec.env_only) schedulCreatePreview(root);
    }

    // Target kinds that name their op (vs the pair-gate flux SLOTS, whose
    // "name" is the slot itself). data-target-kind holds a space-separated
    // token list so one row (the op-name input) can serve several kinds.
    var NAMED_TARGET_KINDS = ['qubit', 'pair_channel'];

    function createTargetKind(radio) {
        var root = createRoot();
        if (!root) return;
        root.querySelectorAll('[data-target-kind]').forEach(function (el) {
            var kinds = el.getAttribute('data-target-kind').split(' ');
            el.hidden = kinds.indexOf(radio.value) === -1;
        });
        var nameInput = document.getElementById('pulse-create-name');
        if (nameInput) {
            var named = NAMED_TARGET_KINDS.indexOf(radio.value) !== -1;
            nameInput.required = named;
            if (!named) {
                // a stale duplicate-name validity on the now-hidden field
                // would silently block the whole form's submit
                nameInput.setCustomValidity('');
            } else {
                createValidateName();
            }
        }
        // r15 (docs/71 §3): pair mode narrows the type list to flux-capable
        // classes (z channel) + env-discovered ones; other modes restore all.
        _applyPairTypeFilter(radio.value === 'pair');
        var line = document.getElementById('pulse-create-pairline');
        var note = document.getElementById('pulse-create-slotnote');
        if (radio.value === 'pair') {
            var pairSel = document.getElementById('pulse-create-pair');
            if (pairSel) createPairSelected(pairSel);
        } else {
            if (line) line.hidden = true;
            if (note) note.hidden = true;
        }
    }

    function _applyPairTypeFilter(pairMode) {
        var root = createRoot();
        var typeSel = document.getElementById('pulse-create-type');
        if (!root || !root._catalog || !typeSel) return;
        var changed = false;
        Array.prototype.forEach.call(typeSel.options, function (opt) {
            var s = root._catalog[opt.value] || {};
            var fluxOk = (s.channels || []).indexOf('z') !== -1 || s.env_only;
            var hide = pairMode && !fluxOk;
            opt.hidden = hide;
            opt.disabled = hide;
            if (hide && opt.selected) changed = true;
        });
        if (changed) {
            for (var i = 0; i < typeSel.options.length; i++) {
                if (!typeSel.options[i].hidden) {
                    typeSel.selectedIndex = i;
                    createTypeChanged(typeSel);
                    break;
                }
            }
        }
    }

    function createPairGates(sel) {
        // Legacy name kept for back-compat pins; the r15 CZ-first flow lives
        // in createPairSelected (docs/71 §3).
        createPairSelected(sel);
    }

    /* -- r15 CZ-first pair flow (docs/71 §3) -------------------------- */

    function _fmtGHz(f) {
        return (typeof f === 'number' && isFinite(f))
            ? (f / 1e9).toFixed(3) + ' GHz' : '?';
    }

    function createPairSelected(sel) {
        var root = createRoot();
        var info = root && root._pairsInfo && root._pairsInfo[sel.value];
        var gateSel = document.getElementById('pulse-create-gate');
        if (!gateSel) {                       // pre-r15 markup — legacy fill
            var legacy = root && root._pairs;
            var gs = root && root.querySelector('select[name="gate"]');
            if (!legacy || !gs) return;
            gs.innerHTML = '';
            (legacy[sel.value] || []).forEach(function (g) {
                var opt = document.createElement('option');
                opt.textContent = g;
                gs.appendChild(opt);
            });
            return;
        }
        gateSel.innerHTML = '';
        var gates = (info && info.gates) || {};
        Object.keys(gates).forEach(function (g) {
            var opt = document.createElement('option');
            opt.value = g;
            opt.textContent = g;
            gateSel.appendChild(opt);
        });
        var defs = root._gateDefs || {};
        ((info && info.new_gates) || []).forEach(function (gid) {
            if (!defs[gid]) return;
            var opt = document.createElement('option');
            opt.value = '__new__:' + gid;
            opt.textContent = '+ new: ' + (defs[gid].label || gid);
            opt.className = 'pulse-opt-newgate';
            gateSel.appendChild(opt);
        });
        // freq / orientation line — control = higher f₀₁ is the CZ
        // convention (czAutoOrient / run_build._cz_order_warning twins);
        // display + warn only, a built pair's orientation is fixed pointers.
        var line = document.getElementById('pulse-create-pairline');
        if (line) {
            if (info && (info.control || info.target)) {
                var txt = 'control ' + (info.control || '?') + ' (' +
                    _fmtGHz(info.f_control) + ') · target ' +
                    (info.target || '?') + ' (' + _fmtGHz(info.f_target) + ')';
                if (info.orient_ok === false) {
                    txt += ' — ⚠ stored control is the LOWER-frequency qubit;' +
                        ' CZ convention is control = higher f₀₁.' +
                        ' Changing roles requires Re-generate.';
                }
                line.textContent = txt;
                line.classList.toggle('pulse-pair-line-warn',
                    info.orient_ok === false);
                line.hidden = false;
            } else {
                line.hidden = true;
            }
        }
        createGateSelected(gateSel);
    }

    function createGateSelected(gateSel) {
        var root = createRoot();
        if (!root) return;
        var pairSel = document.getElementById('pulse-create-pair');
        var slotSel = document.getElementById('pulse-create-slot');
        var nameInput = document.getElementById('pulse-create-newgate-name');
        if (!slotSel) return;
        var isNew = /^__new__:/.test(gateSel.value);
        var gid = isNew ? gateSel.value.slice(8) : null;
        var defs = root._gateDefs || {};
        if (nameInput) {
            nameInput.hidden = !isNew;
            nameInput.required = isNew;
        }
        slotSel.innerHTML = '';
        function addSlot(name, disabled, title) {
            var opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name + (disabled ? ' — holds ' + title : '');
            opt.disabled = !!disabled;
            slotSel.appendChild(opt);
        }
        if (isNew) {
            addSlot('flux_pulse_qubit', false, '');
            if (defs[gid] && defs[gid].has_coupler_slot) {
                addSlot('coupler_flux_pulse', false, '');
            }
        } else {
            var info = root._pairsInfo &&
                root._pairsInfo[pairSel ? pairSel.value : ''];
            var slots = (info && info.gates && info.gates[gateSel.value] &&
                         info.gates[gateSel.value].slots) || {};
            ['flux_pulse_qubit', 'coupler_flux_pulse'].forEach(function (s) {
                var st = slots[s] || { state: 'empty' };
                addSlot(s, st.state === 'held', st['class'] || '');
            });
            // land on the first EMPTY slot so the default submit is valid
            for (var i = 0; i < slotSel.options.length; i++) {
                if (!slotSel.options[i].disabled) {
                    slotSel.selectedIndex = i;
                    break;
                }
            }
        }
        createSlotSelected(slotSel);
    }

    function createSlotSelected(slotSel) {
        var root = createRoot();
        if (!root) return;
        var pairSel = document.getElementById('pulse-create-pair');
        var gateSel = document.getElementById('pulse-create-gate');
        var note = document.getElementById('pulse-create-slotnote');
        if (!note || !gateSel) return;
        var isNew = /^__new__:/.test(gateSel.value);
        var info = root._pairsInfo &&
            root._pairsInfo[pairSel ? pairSel.value : ''];
        var st = (!isNew && info && info.gates && info.gates[gateSel.value] &&
                  info.gates[gateSel.value].slots &&
                  info.gates[gateSel.value].slots[slotSel.value]) || null;
        if (st && st.state === 'held' && st.path) {
            note.innerHTML = '';
            note.appendChild(document.createTextNode(
                slotSel.value + ' holds ' + (st['class'] || 'a pulse') + ' — '));
            var a = document.createElement('a');
            a.href = '#';
            a.textContent = 'edit the existing pulse →';
            a.addEventListener('click', function (e) {
                e.preventDefault();
                if (window.htmx) {
                    window.htmx.ajax('GET',
                        '/pulse/detail?path=' + encodeURIComponent(st.path),
                        { target: '#inspector-pane', swap: 'innerHTML' });
                }
            });
            note.appendChild(a);
            note.hidden = false;
        } else {
            note.hidden = true;
        }
    }

    function createPairChannels(sel) {
        var root = createRoot();
        var chans = root && root._pairChannels;
        if (!chans) return;
        var chanSel = root.querySelector('select[name="pc_channel"]');
        if (!chanSel) return;
        chanSel.innerHTML = '';
        (chans[sel.value] || []).forEach(function (ch) {
            var opt = document.createElement('option');
            opt.textContent = ch;
            chanSel.appendChild(opt);
        });
        createValidateName();
    }

    // docs/136 — a QDAC-biased qubit's `z` IS the QDAC bias line: a DC level
    // with no `operations` dict, physically unable to play a pulse. The form
    // offered it anyway, and the created pulse landed on a component that
    // cannot use it. Disabled with the reason on it, never silently dropped —
    // and only for the QDAC-ONLY shape: a bias-tee qubit's `z` is a real flux
    // line and playing pulses on it is the whole point of the tee.
    function createSyncQdacChannel() {
        var root = createRoot();
        if (!root) return;
        var qdacOnly = root._qdacOnly || {};
        var qubitSel = root.querySelector('select[name="qubit"]');
        var chanSel = root.querySelector('select[name="channel"]');
        if (!qubitSel || !chanSel) return;
        var blocked = !!qdacOnly[qubitSel.value];
        Array.prototype.forEach.call(chanSel.options, function (opt) {
            if (opt.value !== 'z' && opt.textContent.trim() !== 'z') return;
            opt.disabled = blocked;
            opt.title = blocked
                ? qubitSel.value + '.z is a QDAC-II DC bias line — it holds a '
                  + 'voltage and has no operations, so it cannot play a pulse.'
                : '';
        });
        if (blocked && (chanSel.value === 'z' || chanSel.value === '')) {
            chanSel.value = 'xy';
        }
    }

    function createValidateName() {
        var root = createRoot();
        if (!root || !root._existing) return;
        createSyncQdacChannel();
        var nameInput = document.getElementById('pulse-create-name');
        if (!nameInput) return;
        var kindRadio = root.querySelector('input[name="target_kind"]:checked');
        var key, where;
        if (kindRadio && kindRadio.value === 'pair_channel') {
            var pair = (root.querySelector('select[name="pc_pair"]') || {}).value;
            var pchan = (root.querySelector('select[name="pc_channel"]') || {}).value;
            key = 'pair:' + pair + '/' + pchan;
            where = pair + '.' + pchan;
        } else {
            var qubit = (root.querySelector('select[name="qubit"]') || {}).value;
            var channel = (root.querySelector('select[name="channel"]') || {}).value;
            key = qubit + '/' + channel;
            where = qubit + '.' + channel;
        }
        var taken = root._existing[key] || [];
        nameInput.setCustomValidity(
            taken.indexOf(nameInput.value) !== -1
                ? 'An operation with this name already exists on ' + where
                : '');
    }

    function initCreate() {
        var root = createRoot();
        if (!root || root._pulsesInit) return;
        root._pulsesInit = true;
        root._catalog = parseEmbeddedJson('pulse-catalog-data') || {};
        root._existing = parseEmbeddedJson('pulse-existing-data') || {};
        root._pairs = parseEmbeddedJson('pulse-pairs-data') || {};
        root._pairChannels = parseEmbeddedJson('pulse-pair-channels-data') || {};
        root._pairsInfo = parseEmbeddedJson('pulse-pairs-info-data') || {};
        root._gateDefs = parseEmbeddedJson('pulse-gate-defs-data') || {};
        root._qdacOnly = parseEmbeddedJson('pulse-qdac-only-data') || {};
        createSyncQdacChannel();

        var typeSel = document.getElementById('pulse-create-type');
        if (typeSel) {
            // r15 (docs/71 §2): env verdicts on the options — a class the
            // selected env can NOT import is marked (creating it is still
            // possible, behind the explicit confirm below).
            Array.prototype.forEach.call(typeSel.options, function (opt) {
                var s = root._catalog[opt.value];
                if (s && s.verify === 'missing') {
                    opt.textContent += ' — ✗ not in this env';
                    opt.classList.add('pulse-opt-envmissing');
                }
            });
            createTypeChanged(typeSel);
        }

        // Never-silent env-compat confirm (server 409 is the backstop): a
        // missing-in-env class only submits after an explicit OK, which
        // re-fires the request with force=1.
        var form = root.querySelector('form.pulse-create-form');
        if (form && window.htmx) {
            form.addEventListener('htmx:configRequest', function (evt) {
                var sel = document.getElementById('pulse-create-type');
                var s = sel && root._catalog[sel.value];
                if (!s || s.verify !== 'missing') return;
                if (root._envForceOk === sel.value) {
                    evt.detail.parameters.force = '1';
                    root._envForceOk = null;
                    return;
                }
                evt.preventDefault();
                var ok = window.confirm(
                    '"' + sel.value + '" is NOT importable in the selected ' +
                    'environment — a state carrying it will not Quam.load ' +
                    'there.\n\nCreate anyway?');
                if (ok) {
                    root._envForceOk = sel.value;
                    window.htmx.trigger(form, 'submit');
                }
            });
        }

        root.addEventListener('input', function (evt) {
            if (evt.target.closest && evt.target.closest('#pulse-create-fields')) {
                schedulCreatePreview(root);
            }
        });
    }

    // Env-strip "Probe now" — rides the diagnostics probe (single-flighted;
    // installs the pulse-roster overlay on success), then re-polls the strip.
    function envStripProbe(btn) {
        btn.disabled = true;
        fetch('/diagnostics/env-probe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'force=0'
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d.ok) {
                if (window.showToast) window.showToast(d.error || 'probe failed', 'warning');
                btn.disabled = false;
                return;
            }
            if (window.htmx) {
                window.htmx.ajax('GET', '/pulse/new/env-strip',
                    { target: '#pulse-env-strip', swap: 'outerHTML' });
            }
        }).catch(function () { btn.disabled = false; });
    }

    /* ------------------------------------------------------------------ */
    /* Wiring                                                              */
    /* ------------------------------------------------------------------ */

    // Detail/create partials call initDetail()/initCreate() from an inline
    // <script>. The afterSwap hook is a safety net. Attached to document
    // (not body): this file loads in <head>, before <body> exists, and HTMX
    // events bubble to document anyway.
    // Opt the pulses table into the shared drag-resizable columns (B-columns).
    // Idempotent and cheap — safe to call after any swap that (re)renders it.
    function enhancePulsesTable() {
        if (window.enhanceColumnResize && document.getElementById('pulses-table')) {
            window.enhanceColumnResize('pulses-table', 'quam_pulses_col_widths');
        }
    }

    document.addEventListener('htmx:afterSwap', function (evt) {
        if (!evt.detail || !evt.detail.target) return;
        var tid = evt.detail.target.id;
        if (tid === 'inspector-pane') {
            initDetail();
            initCreate();
        } else if (tid === 'pulses-rows-wrap') {
            // Rows arrive ALREADY server-filtered (the search input and the
            // mutation-refresh both thread &q=). The legacy client filterTable
            // must NOT run here: it re-filters by visible row text and hides
            // server matches whose hit was in a title= attribute (summary,
            // alias target path, used_by) — blanking the table for those
            // queries, the exact "unfindable pulse" bug this feature fixed.
            // The swap rebuilt the checkboxes UNCHECKED and re-rendered the compare
            // bar hidden, but the JS _pulseSelection array (in app.js) wasn't reset —
            // it would silently strand a stale selection. Re-sync it to the (empty)
            // DOM so the compare bar/count match what the user sees.
            if (window.clearPulseSelection) window.clearPulseSelection();
            enhancePulsesTable();
        } else if (evt.detail.target.id === 'table-pane' ||
                   evt.detail.target.querySelector &&
                   evt.detail.target.querySelector('#pulses-table')) {
            // first navigation to /pulses (full table-pane swap)
            enhancePulsesTable();
        }
    });
    // server-rendered first paint (no swap fired)
    document.addEventListener('DOMContentLoaded', enhancePulsesTable);

    // Click-to-copy dot-paths (data attribute, never inline JS — the paths
    // contain untrusted state.json keys).
    document.addEventListener('click', function (evt) {
        var el = evt.target.closest && evt.target.closest('.pulse-copy-path');
        if (!el || !navigator.clipboard) return;
        navigator.clipboard.writeText(el.getAttribute('data-copy') || '');
    });

    return {
        initDetail: initDetail,
        initCreate: initCreate,
        renderPulsePlot: renderPulsePlot,
        toggleParamSlider: toggleParamSlider,
        startRename: startRename,
        cancelRename: cancelRename,
        startDuplicate: startDuplicate,
        cancelDuplicate: cancelDuplicate,
        askDelete: askDelete,
        cancelDelete: cancelDelete,
        verifyPulse: verifyPulse,
        regenerateThenVerify: regenerateThenVerify,
        startLinkEdit: startLinkEdit,
        createTypeChanged: createTypeChanged,
        createTargetKind: createTargetKind,
        createPairGates: createPairGates,
        createPairSelected: createPairSelected,
        createGateSelected: createGateSelected,
        createSlotSelected: createSlotSelected,
        createPairChannels: createPairChannels,
        createValidateName: createValidateName,
        createSyncQdacChannel: createSyncQdacChannel,
        envStripProbe: envStripProbe
    };
})();
