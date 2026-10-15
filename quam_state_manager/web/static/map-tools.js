/* Map preview is display-only; server replay supplies every applied number.
 *
 * One strip per reproduced figure, placed under the figure's title (above the plot, so a tall
 * figure never pushes the controls off-screen). Smoothing and contours apply to EVERY map panel of
 * the figure, all panels in one request (the server resolves the figure once); "Readout" picks the
 * panel whose fit, comparison and Apply are shown. A strength cap is a bias limit, not a wall: the
 * slider goes past it, the request always asks for the shown strength (override_cap) and the server
 * marks only a panel actually above its own limit "cap overridden".
 */
(function () {
    'use strict';
    var serial = 0;
    function copy(v) { return JSON.parse(JSON.stringify(v)); }
    function number(v) { return v == null ? 'n/a' : Number(v).toPrecision(6); }
    function localPreview(z, sx, sy, kernel, period) {
        function pass(a, sigma, vertical) {
            if (!sigma) return a;
            var radius = Math.min(24, Math.ceil((kernel === 'box' ? 1.732 : 3) * sigma));
            return a.map(function (row, y) { return row.map(function (_, x) {
                var sum = 0, cosine = 0, weight = 0;
                for (var k = -radius; k <= radius; k++) {
                    var yy = Math.max(0, Math.min(a.length - 1, y + (vertical ? k : 0)));
                    var xx = Math.max(0, Math.min(row.length - 1, x + (vertical ? 0 : k)));
                    var value = a[yy][xx];
                    if (value == null || !Number.isFinite(value)) continue;
                    var w = kernel === 'box' ? 1 : Math.exp(-0.5 * k * k / (sigma * sigma));
                    if (period) { sum += w * Math.sin(value * 2 * Math.PI / period); cosine += w * Math.cos(value * 2 * Math.PI / period); }
                    else sum += w * value;
                    weight += w;
                }
                if (!weight) return null;
                return period ? ((Math.atan2(sum, cosine) * period / (2 * Math.PI)) % period + period) % period : sum / weight;
            }); });
        }
        return pass(pass(z, sx, false), sy, true);
    }
    function mount(host, plot, payload, options) {
        options = options || {};
        var previous = host._mapTools;
        if (previous) previous.destroy();
        var maps = (payload.data || []).map(function (t, i) { return {trace: t, index: i}; }).filter(function (t) { return t.trace.type === 'heatmap'; });
        if (!maps.length) return;
        var template = document.getElementById('map-tools-template');
        if (!template) return;
        var strip = template.content.firstElementChild.cloneNode(true), base = copy(payload.data);
        var id = 'map-tools-' + (++serial), button = document.createElement('button');
        button.type = 'button'; button.textContent = 'Map tools'; button.className = 'map-tools-button secondary';
        button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', id); strip.id = id;
        var header = host.querySelector('.ds-interactive-fig-title') || options.header || host;
        header.appendChild(button);
        // Under the title, above the plot: the controls stay next to what they change.
        if (header !== host && header.parentNode && host.contains(header)) header.parentNode.insertBefore(strip, header.nextSibling);
        else host.insertBefore(strip, host.firstChild);
        function control(name) { return strip.querySelector('[data-map="' + name + '"]'); }
        // Per panel: the last server result and its caps in FIGURE axes (a transposed model swaps them).
        var results = [], caps = [], last = null, classification = null, generation = 0, timer = null, alive = true, initialized = false, kind = '';
        var pending = [];
        var side = strip.querySelector('.map-tools-side');
        var statusChip = document.createElement('span'); statusChip.className = 'map-tools-chip';
        statusChip.textContent = payload.status_chip || 'not verified on this run'; header.appendChild(statusChip);
        var contractChip = document.createElement('span'); contractChip.className = 'map-tools-chip';
        contractChip.textContent = payload.clickable && payload.clickable.evidence_tier === 'proposed' ? 'proposed, not applied' :
            (payload.display_only_reason || (!payload.clickable ? 'not verified on this run' : 'verified on this run'));
        header.appendChild(contractChip);
        maps.forEach(function (_, index) { var opt = document.createElement('option'); opt.value = index; opt.textContent = 'Map ' + (index + 1); control('panel').appendChild(opt); });
        control('panel').parentElement.hidden = maps.length === 1;
        ['x', 'y'].forEach(function (axis) {
            var ticks = control(axis + '_ticks'); ticks.id = id + '-' + axis; control('sigma_' + axis).setAttribute('list', ticks.id);
        });
        function readout() { return Number(control('panel').value) || 0; }
        function locked(axis) { return kind === 'erroramp' && classification && classification.feature_axis === axis; }
        function capOf(i, axis) { return caps[i] ? caps[i]['cap_' + axis] : null; }
        function above(i) {
            if (!control('smooth').checked || !caps[i]) return false;
            return ['x', 'y'].some(function (axis) {
                return !locked(axis) && Number(control('sigma_' + axis).value) > capOf(i, axis) + 1e-9;
            });
        }
        function modelFor(i) {
            var chosen = control('model').value, models = results[i] && results[i].classification.models;
            return i === readout() || !models || models.indexOf(chosen) !== -1 ? chosen : 'auto';
        }
        function settings(model) {
            return {smooth: {kernel: control('kernel').value,
                sigma_x: control('smooth').checked ? Number(control('sigma_x').value) : 0,
                sigma_y: control('smooth').checked ? Number(control('sigma_y').value) : 0,
                override_cap: control('smooth').checked},
                contour: {on: control('contour').checked, mode: control('mode').value,
                    level: Number(control('level').value), band: Number(control('band').value)},
                fit: {model: model || control('model').value}};
        }
        // panels grouped by the model each can take (a panel of another type falls back to auto)
        function groups() {
            var out = {};
            maps.forEach(function (_, i) { var m = modelFor(i); (out[m] = out[m] || []).push(i); });
            return out;
        }
        function clamp() {
            var over = maps.map(function (_, i) { return above(i) ? 'Map ' + (i + 1) : null; }).filter(Boolean);
            ['x', 'y'].forEach(function (axis) {
                var slider = control('sigma_' + axis), cap = capOf(readout(), axis);
                slider.disabled = !!locked(axis);
                if (locked(axis)) slider.value = 0;
                control(axis + '_text').textContent = Number(slider.value).toFixed(1) + ' pt';
                control(axis + '_cap').textContent = cap == null ? '' : 'bias limit ' + Number(cap).toFixed(2) + ' pt' + (locked(axis) ? ' (locked: count axis)' : '');
                control(axis + '_cap').title = 'Above this strength the smoothing can move the fitted feature by more than its tolerance; the slider still goes further and the result is marked "cap overridden".';
                control(axis + '_ticks').replaceChildren();
                if (cap != null) {
                    slider.parentElement.style.setProperty('--map-cap-position', (Math.min(8, cap) / 8 * 9) + 'rem');
                    var tick = document.createElement('option'); tick.value = Math.min(8, cap); tick.label = 'bias limit'; control(axis + '_ticks').appendChild(tick);
                }
            });
            control('level').disabled = control('mode').value !== 'custom';
            control('warning').textContent = over.length ? 'Above the bias limit on ' + over.join(', ') + ': stronger smoothing may bias the fit (marked "cap overridden").' : '';
        }
        function draw(local) {
            if (!alive || !document.body.contains(plot) || !window.Plotly) return;
            var traces = copy(base);
            maps.forEach(function (m, i) {
                var result = local ? local[i] : results[i], trace = traces[m.index];
                if (!result || !(control('smooth').checked || !local)) return;
                var grid = result.smoothed;
                trace.z = grid.z; trace.x = grid.x; trace.y = grid.y;
                var norm = (m.trace.meta || {}).norm || {};
                if (norm.log) trace.z = grid.z.map(function (row) { return row.map(function (v) { return v > 0 ? Math.log10(v) : null; }); });
            });
            if (!local && control('contour').checked) {
                var color = window.getComputedStyle(plot).getPropertyValue('--plot-axis-text').trim() || window.getComputedStyle(plot).color;
                results.forEach(function (result) { if (result) (result.contours || []).forEach(function (contour) { contour.lines.forEach(function (line) {
                    traces.push({type: 'scatter', mode: 'lines', x: line.map(function (p) { return p[0]; }), y: line.map(function (p) { return p[1]; }),
                        xaxis: result.xref, yaxis: result.yref, line: {color: color, width: 1.5}, name: 'Contour ' + contour.level, showlegend: false,
                        hoverinfo: 'skip'});
                }); }); });
            }
            // the figure as drawn now (spikes, hover labels), only its traces replaced
            var layout = plot.layout ? copy(plot.layout) : copy(payload.layout || {});
            if (!plot.layout && window.PlotTheme) layout = window.PlotTheme.houseLayout(layout);
            var mode = control('view').value;
            side.hidden = mode !== 'side';
            Promise.resolve(window.Plotly.react(plot, mode === 'smoothed' ? traces : copy(base), layout, {responsive: true})).catch(function () {});
            if (mode === 'side') Promise.resolve(window.Plotly.react(side, traces, layout, {responsive: true})).catch(function () {});
        }
        function invalidate() {
            generation++; last = null; control('apply').disabled = true; window.clearTimeout(timer);
            pending.forEach(function (request) { request.controller.abort(); window.clearTimeout(request.timeout); });
            pending = [];
        }
        function post(panels, model) {
            var body = Object.assign({}, options.request || {}, {fig: options.fig || '', panels: panels, source: options.source || 'replot'}, settings(model));
            var request = {controller: new window.AbortController(), timeout: null}; pending.push(request);
            var deadline = new Promise(function (_, reject) {
                request.timeout = window.setTimeout(function () {
                    request.controller.abort(); reject(new Error('Map analysis timed out after 45 seconds; reopen Map tools to retry.'));
                }, 45000);
            });
            var response = window.fetch('/dataset/' + encodeURIComponent(options.uid) + '/map/analyze', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: request.controller.signal
            }).then(function (response) { return response.json().then(function (value) { if (!response.ok) throw new Error(value.error || 'Analysis failed'); return value; }); });
            return Promise.race([response, deadline]).then(function (value) {
                window.clearTimeout(request.timeout);
                return panels.map(function (panel, k) { return {panel: panel, result: (value.results || [])[k]}; });
            },
                function (error) { window.clearTimeout(request.timeout); throw error; });
        }
        function showReadout() {
            var result = results[readout()];
            last = result || null;
            if (!result) return;
            classification = result.classification; kind = classification.type;
            control('status').textContent = (maps.length > 1 ? 'Map ' + (readout() + 1) + ': ' : '') + (!classification.models.length ? (classification.reason || result.fit.verdict_line) : (result.comparisons || [result.compare]).map(function (c) { return 'node ' + c.node_name + ' ' + number(c.node_value) + ' | SM ' + number(c.sm_value) +
                ' | delta ' + number(c.delta) + ' | ' + c.verdict + ' (' + number(c.tol) + ', ' + c.tol_source + ') | ' + c.reason +
                (c.quantity ? ' | ' + c.quantity : '') +
                ' | raw-to-smoothed delta ' + number(c.raw_smoothed_delta); }).join(' ; ') + ' | fit: ' + result.fit.verdict_line.replace(/^VERDICT:\s*/i, '') +
                (result.display_only_reason ? ' | ' + result.display_only_reason : ''));
            control('provenance').textContent = result.provenance_text;
            control('apply').disabled = !(result.updates && result.updates.length);
            control('apply').textContent = control('apply').disabled ? 'Apply unavailable: no verified target' : 'Apply SM value';
            control('apply').title = control('apply').disabled ? (result.display_only_reason || result.fit.verdict_line) : '';
        }
        function analyze(initial) {
            invalidate();
            var seq = generation, wanted = initial ? {auto: maps.map(function (_, i) { return i; })} : groups();
            control('status').textContent = 'Analyzing ' + (maps.length > 1 ? maps.length + ' full-resolution maps...' : 'full-resolution map...');
            return Promise.all(Object.keys(wanted).map(function (model) { return post(wanted[model], initial ? 'auto' : model); }))
                .then(function (lists) {
                    pending = [];
                    if (!alive || seq !== generation || !document.body.contains(host)) return;
                    var values = [];
                    lists.forEach(function (list) { list.forEach(function (item) { values[item.panel] = item.result; }); });
                    if (values.length !== maps.length || values.some(function (v) { return !v; })) throw new Error('Analysis returned no result for a map.');
                    results = values;
                    caps = values.map(function (result) {
                        var c = copy(result.caps_applied);
                        if (c.transposed) { var swap = c.cap_x; c.cap_x = c.cap_y; c.cap_y = swap; }
                        return c;
                    });
                    var mine = values[readout()];
                    classification = mine.classification; kind = classification.type;
                    if (initial) {
                        var sent = JSON.stringify(settings('auto'));
                        ['x', 'y'].forEach(function (axis) { control('sigma_' + axis).value = mine.caps_applied['default_' + (mine.caps_applied.transposed ? (axis === 'x' ? 'y' : 'x') : axis)]; });
                        control('model').replaceChildren();
                        classification.models.forEach(function (model) { var option = document.createElement('option'); option.value = model; option.textContent = model; control('model').appendChild(option); });
                        control('model').disabled = !classification.models.length;
                        if (control('model').disabled) {
                            var noModel = document.createElement('option'); noModel.value = 'none'; noModel.textContent = 'Smoothing and contours only'; control('model').appendChild(noModel);
                        }
                        control('band').value = classification.circular ? 5 * classification.circular / 360 : 0;
                        try {
                            var saved = JSON.parse(window.localStorage.getItem('quam_map_tools_v1') || '{}')[kind];
                            if (saved) Object.keys(saved).forEach(function (name) { var c = control(name); if (c && name !== 'panel') { if (c.type === 'checkbox') c.checked = saved[name]; else c.value = saved[name]; } });
                        } catch (_) {}
                        if (!control('model').value) control('model').value = (classification.models || [])[0] || 'none';
                        clamp();
                        initialized = true;
                        // one round trip on open: ask again only when restored settings change the
                        // request (auto already IS the first model of the type)
                        var first = (classification.models || [])[0] || 'none';
                        if (JSON.stringify(settings(control('model').value === first ? 'auto' : null)) !== sent) return analyze(false);
                        showReadout();
                        return;
                    }
                    clamp();
                    showReadout();
                    draw(null);
                    initialized = true;
                }).catch(function (error) {
                    if (alive && seq === generation) {
                        invalidate();
                        last = null; initialized = false; control('apply').disabled = true;
                        control('apply').textContent = 'Apply unavailable: analysis failed'; control('apply').title = error.message;
                        control('status').textContent = error.message;
                    }
                });
        }
        function changed() {
            var period = last && last.classification.circular;
            clamp(); invalidate();
            control('status').textContent = 'Preview only; waiting for server analysis.';
            var s = settings();
            if (s.smooth.kernel === 'gaussian' || s.smooth.kernel === 'box') {
                draw(maps.map(function (m) {
                    var trace = m.trace, shape = (trace.meta || {}).full_shape || [trace.z.length, trace.z[0].length];
                    return {smoothed: {z: localPreview(trace.z, s.smooth.sigma_x * trace.z[0].length / shape[1], s.smooth.sigma_y * trace.z.length / shape[0], s.smooth.kernel, period), x: trace.x, y: trace.y}};
                }));
            }
            try {
                var saved = JSON.parse(window.localStorage.getItem('quam_map_tools_v1') || '{}'), fields = {};
                ['smooth', 'kernel', 'sigma_x', 'sigma_y', 'contour', 'mode', 'level', 'band', 'model', 'view'].forEach(function (name) { var c = control(name); fields[name] = c.type === 'checkbox' ? c.checked : c.value; });
                saved[kind] = fields; window.localStorage.setItem('quam_map_tools_v1', JSON.stringify(saved));
            } catch (_) {}
            timer = window.setTimeout(function () { analyze(false); }, 300);
        }
        button.onclick = function () {
            strip.hidden = !strip.hidden; button.setAttribute('aria-expanded', String(!strip.hidden));
            if (!strip.hidden && !initialized) analyze(true);
        };
        strip.addEventListener('input', function (event) {
            if (event.target === control('panel')) return;      // a readout switch re-reads, never re-analyzes
            changed();
        });
        control('panel').addEventListener('change', function () { clamp(); showReadout(); });
        strip.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') { event.stopPropagation(); strip.hidden = true; button.setAttribute('aria-expanded', 'false'); button.focus(); }
            if (event.key === 'Enter' && event.target === control('level')) { event.preventDefault(); analyze(false); }
            if (event.shiftKey && event.target.type === 'range' && /^Arrow/.test(event.key)) {
                event.preventDefault(); event.target.value = Number(event.target.value) + (event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 1); changed();
            }
        });
        control('apply').onclick = function () {
            if (!last || !last.updates || !last.updates.length) return;
            var digest = last.request_hash, seq = generation, shown = JSON.stringify(settings());
            control('apply').disabled = true;
            window.fetch('/dataset/' + encodeURIComponent(options.uid) + '/map/analyze/' + encodeURIComponent(digest))
                .then(function (r) { if (!r.ok) throw new Error('Analysis expired; analyze again.'); return r.json(); })
                .then(function (r) {
                    if (!alive || generation !== seq || !document.body.contains(host)) return;
                    var root = window._dsRootFor ? window._dsRootFor(plot) : host;
                    var chip = options.chip || (window._runChipExpect ? window._runChipExpect(root) : null);
                    var updates = r.updates.map(function (row) { return Object.assign({}, row, {
                        mapValid: function () { return alive && generation === seq && shown === JSON.stringify(settings()) && document.body.contains(host); }
                    }); });
                    window._openPlotApplyPopup(updates, options.experiment || '', (payload.clickable || {}).qubit || null,
                        [{label: 'Comparison', value: control('status').textContent}], chip, options.uid);
                    control('apply').disabled = false;
                }).catch(function (error) { if (alive && seq === generation) control('status').textContent = error.message; });
        };
        host._mapTools = {destroy: function () { alive = false; invalidate(); if (window.Plotly) window.Plotly.purge(side); strip.remove(); button.remove(); statusChip.remove(); contractChip.remove(); }};
        return host._mapTools;
    }
    window.MapTools = {mount: mount, localPreview: localPreview};
})();
