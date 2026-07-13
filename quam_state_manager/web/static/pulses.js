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
            if (window.Plotly && el.data) {
                try { window.Plotly.Plots.resize(el); } catch (e) {}
            }
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
                    ? ' · length auto-inferred (#./inferred_length)' : '');
        }
        buildFieldRows(spec);
        schedulCreatePreview(root);
    }

    function createTargetKind(radio) {
        var root = createRoot();
        if (!root) return;
        root.querySelectorAll('[data-target-kind]').forEach(function (el) {
            el.hidden = el.getAttribute('data-target-kind') !== radio.value;
        });
        var nameInput = document.getElementById('pulse-create-name');
        if (nameInput) {
            nameInput.required = radio.value === 'qubit';
            if (radio.value !== 'qubit') {
                // a stale duplicate-name validity on the now-hidden field
                // would silently block the whole form's submit
                nameInput.setCustomValidity('');
            } else {
                createValidateName();
            }
        }
    }

    function createPairGates(sel) {
        var root = createRoot();
        var pairs = root && root._pairs;
        if (!pairs) return;
        var gateSel = root.querySelector('select[name="gate"]');
        if (!gateSel) return;
        gateSel.innerHTML = '';
        (pairs[sel.value] || []).forEach(function (g) {
            var opt = document.createElement('option');
            opt.textContent = g;
            gateSel.appendChild(opt);
        });
    }

    function createValidateName() {
        var root = createRoot();
        if (!root || !root._existing) return;
        var nameInput = document.getElementById('pulse-create-name');
        if (!nameInput) return;
        var qubit = (root.querySelector('select[name="qubit"]') || {}).value;
        var channel = (root.querySelector('select[name="channel"]') || {}).value;
        var taken = root._existing[qubit + '/' + channel] || [];
        nameInput.setCustomValidity(
            taken.indexOf(nameInput.value) !== -1
                ? 'An operation with this name already exists on ' + qubit + '.' + channel
                : '');
    }

    function initCreate() {
        var root = createRoot();
        if (!root || root._pulsesInit) return;
        root._pulsesInit = true;
        root._catalog = parseEmbeddedJson('pulse-catalog-data') || {};
        root._existing = parseEmbeddedJson('pulse-existing-data') || {};
        root._pairs = parseEmbeddedJson('pulse-pairs-data') || {};

        var typeSel = document.getElementById('pulse-create-type');
        if (typeSel) createTypeChanged(typeSel);

        root.addEventListener('input', function (evt) {
            if (evt.target.closest && evt.target.closest('#pulse-create-fields')) {
                schedulCreatePreview(root);
            }
        });
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
        createValidateName: createValidateName
    };
})();
