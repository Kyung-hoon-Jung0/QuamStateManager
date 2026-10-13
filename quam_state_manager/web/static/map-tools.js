/* Map preview is display-only; server replay supplies every applied number. */
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
        header.appendChild(button); host.appendChild(strip);
        function control(name) { return strip.querySelector('[data-map="' + name + '"]'); }
        var caps = null, last = null, classification = null, generation = 0, timer = null, alive = true, initialized = false, kind = '';
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
        function selected() { return maps[Number(control('panel').value) || 0]; }
        function settings() {
            return {smooth: {kernel: control('kernel').value,
                sigma_x: control('smooth').checked ? Number(control('sigma_x').value) : 0,
                sigma_y: control('smooth').checked ? Number(control('sigma_y').value) : 0,
                override_cap: control('override').checked},
                contour: {on: control('contour').checked, mode: control('mode').value,
                    level: Number(control('level').value), band: Number(control('band').value)},
                fit: {model: control('model').value}};
        }
        function clamp() {
            ['x', 'y'].forEach(function (axis) {
                var slider = control('sigma_' + axis), cap = caps ? caps['cap_' + axis] : 8;
                var locked = kind === 'erroramp' && classification && classification.feature_axis === axis;
                slider.disabled = !!locked;
                if (locked) slider.value = 0;
                else if (!control('override').checked) slider.value = Math.min(Number(slider.value), Math.floor(cap * 10) / 10);
                control(axis + '_text').textContent = Number(slider.value).toFixed(1) + ' pt';
                control(axis + '_cap').textContent = 'cap ' + number(cap) + ' pt' + (locked ? ' (locked: count axis)' : '');
                slider.parentElement.style.setProperty('--map-cap-position', (Math.min(8, cap) / 8 * 9) + 'rem');
                control(axis + '_ticks').replaceChildren();
                var tick = document.createElement('option'); tick.value = Math.min(8, cap); tick.label = 'cap'; control(axis + '_ticks').appendChild(tick);
            });
            control('level').disabled = control('mode').value !== 'custom';
            control('warning').textContent = control('override').checked ? 'Cap override: stronger smoothing may bias the fit.' : '';
        }
        function draw(result, local) {
            if (!alive || !document.body.contains(plot) || !window.Plotly) return;
            var traces = copy(base), chosen = selected(), trace = traces[chosen.index];
            if (result && (control('smooth').checked || !local)) {
                var grid = result.smoothed;
                trace.z = grid.z; trace.x = grid.x; trace.y = grid.y;
                var norm = (chosen.trace.meta || {}).norm || {};
                if (norm.log) trace.z = grid.z.map(function (row) { return row.map(function (v) { return v > 0 ? Math.log10(v) : null; }); });
            }
            if (result && !local && control('contour').checked) {
                var color = window.getComputedStyle(plot).getPropertyValue('--plot-axis-text').trim() || window.getComputedStyle(plot).color;
                (result.contours || []).forEach(function (contour) { contour.lines.forEach(function (line) {
                    traces.push({type: 'scatter', mode: 'lines', x: line.map(function (p) { return p[0]; }), y: line.map(function (p) { return p[1]; }),
                        xaxis: result.xref, yaxis: result.yref, line: {color: color, width: 1.5}, name: 'Contour ' + contour.level, showlegend: false});
                }); });
            }
            var layout = copy(payload.layout || {});
            if (window.PlotTheme) layout = window.PlotTheme.houseLayout(layout);
            var mode = control('view').value;
            side.hidden = mode !== 'side';
            Promise.resolve(window.Plotly.react(plot, mode === 'smoothed' ? traces : copy(base), layout, {responsive: true})).catch(function () {});
            if (mode === 'side') Promise.resolve(window.Plotly.react(side, traces, layout, {responsive: true})).catch(function () {});
        }
        function invalidate() { generation++; last = null; control('apply').disabled = true; window.clearTimeout(timer); }
        function analyze(initial) {
            invalidate();
            var seq = generation, body = Object.assign({}, options.request || {}, {fig: options.fig || '', panel: Number(control('panel').value), source: options.source || 'replot'});
            if (!initial) Object.assign(body, settings());
            control('status').textContent = 'Analyzing full-resolution map...';
            return window.fetch('/dataset/' + encodeURIComponent(options.uid) + '/map/analyze', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
            }).then(function (response) { return response.json().then(function (value) { if (!response.ok) throw new Error(value.error || 'Analysis failed'); return value; }); })
                .then(function (result) {
                    if (!alive || seq !== generation || !document.body.contains(host)) return;
                    last = result; classification = result.classification; kind = classification.type; caps = copy(result.caps_applied);
                    if (caps.transposed) { var swap = caps.cap_x; caps.cap_x = caps.cap_y; caps.cap_y = swap; }
                    if (initial) {
                        ['x', 'y'].forEach(function (axis) { control('sigma_' + axis).value = result.caps_applied['sigma_' + (caps.transposed ? (axis === 'x' ? 'y' : 'x') : axis)]; });
                        control('model').replaceChildren();
                        result.classification.models.forEach(function (model) { var option = document.createElement('option'); option.value = model; option.textContent = model; control('model').appendChild(option); });
                        control('band').value = result.classification.circular ? 5 * result.classification.circular / 360 : 0;
                        try {
                            var saved = JSON.parse(window.localStorage.getItem('quam_map_tools_v1') || '{}')[kind];
                            if (saved) Object.keys(saved).forEach(function (name) { var c = control(name); if (c) { if (c.type === 'checkbox') c.checked = saved[name]; else c.value = saved[name]; } });
                        } catch (_) {}
                    }
                    clamp();
                    var c = result.compare;
                    control('status').textContent = 'node ' + c.node_name + ' ' + number(c.node_value) + ' | SM ' + number(c.sm_value) +
                        ' | delta ' + number(c.delta) + ' | ' + c.verdict + ' (' + number(c.tol) + ', ' + c.tol_source + ') | ' + c.reason +
                        ' | raw-to-smoothed delta ' + number(c.raw_smoothed_delta) + ' | ' + result.fit.verdict_line +
                        (result.display_only_reason ? ' | ' + result.display_only_reason : '');
                    control('provenance').textContent = result.provenance_text;
                    control('apply').disabled = !(result.updates && result.updates.length);
                    if (!initial) draw(result, false);
                    initialized = true;
                    if (initial) return analyze(false);
                }).catch(function (error) { if (alive && seq === generation) control('status').textContent = error.message; });
        }
        function changed() {
            var period = last && last.classification.circular;
            clamp(); invalidate();
            control('status').textContent = 'Preview only; waiting for server analysis.';
            var s = settings(), trace = selected().trace;
            if (s.smooth.kernel === 'gaussian' || s.smooth.kernel === 'box') {
                var shape = (trace.meta || {}).full_shape || [trace.z.length, trace.z[0].length];
                draw({smoothed: {z: localPreview(trace.z, s.smooth.sigma_x * trace.z[0].length / shape[1], s.smooth.sigma_y * trace.z.length / shape[0], s.smooth.kernel, period), x: trace.x, y: trace.y}}, true);
            }
            try {
                var saved = JSON.parse(window.localStorage.getItem('quam_map_tools_v1') || '{}'), fields = {};
                ['smooth', 'kernel', 'sigma_x', 'sigma_y', 'override', 'contour', 'mode', 'level', 'band', 'model', 'view'].forEach(function (name) { var c = control(name); fields[name] = c.type === 'checkbox' ? c.checked : c.value; });
                saved[kind] = fields; window.localStorage.setItem('quam_map_tools_v1', JSON.stringify(saved));
            } catch (_) {}
            timer = window.setTimeout(function () { analyze(false); }, 300);
        }
        button.onclick = function () {
            strip.hidden = !strip.hidden; button.setAttribute('aria-expanded', String(!strip.hidden));
            if (!strip.hidden && !initialized) analyze(true);
        };
        strip.addEventListener('input', changed);
        control('panel').addEventListener('change', function () { initialized = false; analyze(true); });
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
