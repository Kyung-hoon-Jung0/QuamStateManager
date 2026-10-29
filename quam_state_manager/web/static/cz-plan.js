/* Chip Status > CZ gate plan (bundle 'czplan'; the chip map is cz-map.js, window.CzMap).
 *
 * Draws what /cz-plan/data computed -- the physics, the estimate tiers and every refusal live
 * in core/cz_gate_plan.py. This file only: picks the operation, asks for its data, draws
 * (a) frequency vs flux amplitude with the qubits' measured loss beside it, (b) frequency
 * during the gate from the operation's own waveforms, (c) the operation's card, puts each
 * caveat under the figure it explains, keeps the staleness and drawing notes in a collapsed
 * box below the card, and fills the table's "Last set" column from /cz-plan/history. A
 * response that arrives after a newer request is dropped (seq token).
 */
(function () {
    'use strict';

    // [derived], check S2-EXPOSE: consume server numbers without recomputation.
    function operatingPoint(root, op, line, fallback) {
        var blob = root.querySelector('#cz-operating-points');
        if (!blob) return fallback;
        var points = blob._points || (blob._points = JSON.parse(blob.textContent));
        return points.find(function (p) { return p.op === op && p.line === line; }) || fallback;
    }
    // [derived], check S2-EXPOSE: keep previews and map positions inspectable.
    function savePoints(root) {
        var blob = root.querySelector('#cz-operating-points');
        if (blob && blob._points) blob.textContent = JSON.stringify(blob._points);
    }
    // [derived], check S2-EXPOSE: bind the drawn marker to its source object.
    function stampPoints(el) {
        var groups = el.querySelectorAll('.scatterlayer .trace');
        groups.forEach(function (group, i) {
            // Empty traces have no SVG group. Plotly's bound calcdata identifies
            // the input trace even when its DOM index differs from its data index.
            var calc = group.__data__, full = calc && calc[0] && calc[0].trace;
            var trace = full ? (full._input || (el.data || [])[full.index]) : (el.data || [])[i];
            if (!trace || !trace._czOperating) return;
            var p = trace._czOperating;
            group.querySelectorAll('.point').forEach(function (marker) {
                ['pair', 'op', 'line', 'method', 'path', 'coupler_shift_hz', 'shifted_frequency_hz', 'coupler_method', 'coupler_frequency_hz', 'zz_hz', 't_flat_ns'].forEach(function (k) { marker.setAttribute('data-' + k.replace(/_/g, '-'), p[k] == null ? '' : p[k]); });
                marker.setAttribute('data-freq-hz', p.frequency_hz);
                if (p.preview) marker.setAttribute('data-preview', '1');
            });
        });
    }
    // [derived], S3-EXPOSE: select a precomputed distance from the shared blob.
    function nearestPoint(root, point) {
        var distances = root._czIncludeCoupler ? point.shifted_resonances : point.bare_resonances;
        return (distances || []).filter(function (r) { return r.distance_hz != null; })
            .sort(function (a, b) { return Math.abs(a.distance_hz) - Math.abs(b.distance_hz); })[0];
    }
    window.CzOperatingPoints = { get: operatingPoint, save: savePoints, stamp: stampPoints, nearest: nearestPoint };

    function renderMatch(root, result) {
        var match = root.querySelector('#czp-match');
        if (!match) return;
        var qubits = ((root._czLast || {}).d || {}).qubits || [];
        var higher = qubits.filter(function (q) { return q.summary && q.summary.f01_hz != null; })
            .sort(function (a, b) { return b.summary.f01_hz - a.summary.f01_hz; })[0];
        var summary = result.summary || result.reason || '';
        match.innerHTML = esc(summary.replace(/\(= .*? of H per node 30\)/,
            '(= \u2212anharmonicity of ' + (higher ? higher.name : 'the higher qubit') + (higher ? ', the higher qubit)' : ')'))) +
            help('same rule as official node 30');
        ['target_detuning_hz', 'amplitude_v', 'stored_amplitude_v', 'difference_v', 'tier', 'line'].forEach(function (key) {
            match.setAttribute('data-' + key.replace(/_/g, '-'), result[key] == null ? '' : result[key]);
        });
    }

    // [derived], check S2-STAGE: proposals use the existing Review edit door.
    function renderSolve(root, row) {
        var el = root.querySelector('#czp-solve');
        if (!el) return;
        var solveBox = el.closest('details');
        if (row.a4) {
            el._op = row.path; el._solveResult = null; el.innerHTML = '';
            if (solveBox) solveBox.hidden = true;
            renderA4Match(root, row);
            return;
        }
        if (solveBox) solveBox.hidden = false;
        if (el._op === row.path) {
            if (el._solveResult) renderMatch(root, el._solveResult);
            return;
        }
        el._op = row.path;
        el._solveResult = null;
        var solveBox = el.closest('details');
        if (solveBox) solveBox.ontoggle = function () {
            root._czSolvePreview = solveBox.open && el._solveResult && el._solveResult.operating_point || null;
            redrawFlux(root);
        };
        var multi = row.kind === 'two-flux' || row.kind === 'both movers';
        var movers = row.lines.filter(function (ln) { return ln.moves && (multi || ln.pulse_field === 'flux_pulse_qubit'); });
        el.innerHTML = '<div class="czp-controls"><strong>Solve amplitude</strong> <label>Tier <select class="czp-solve-tier">' +
            ['auto', 'quad', 'model', 'direct'].map(function (t) { return '<option>' + t + '</option>'; }).join('') +
            '</select></label><label' + (!multi ? ' hidden' : '') + '>Line <select class="czp-solve-line">' +
            movers.map(function (ln) { return '<option value="' + esc(ln.role) + '">' + esc(ln.role) + '</option>'; }).join('') +
            '</select></label><label>Direct amplitude (V) <input class="czp-solve-amp" type="number" step="any" disabled></label></div>' +
            '<output class="czp-solve-target"></output><div class="czp-solve-result" role="status"></div>' +
            '<p class="czp-solve-notes"></p><button type="button" class="czp-solve-stage" disabled>Stage</button>';
        var generation = 0, result, button = el.querySelector('button');
        // [derived], checks S2-ROUTE/S2-EXPOSE: discard late operation answers.
        function preview() {
            var mine = ++generation, tier = el.querySelector('.czp-solve-tier').value;
            el.querySelector('input').disabled = tier !== 'direct';
            button.disabled = true; result = null;
            root._czSolvePreview = null;
            var params = new URLSearchParams({pair: row.pair, op: row.path,
                line: el.querySelector('.czp-solve-line').value, tier: tier, amp: el.querySelector('input').value});
            fetch('/cz-plan/solve?' + params).then(function (r) { return r.json(); }).then(function (d) {
                if (mine !== generation || el._op !== row.path || root.getAttribute('data-selected') !== row.path || !document.body.contains(el)) return;
                result = d;
                el._solveResult = d;
                renderMatch(root, d);
                el.querySelector('.czp-solve-target').textContent = d.target_hz != null ? 'Target: ' + fmtGHz(d.target_hz) + ' — ' + d.rule : '';
                el.querySelector('.czp-solve-result').textContent = d.ok ? fmtV(d.amplitude_v) + ' → ' + fmtGHz(d.frequency_hz) + ' · ' + (d.validity || '') +
                    (d.rel_diff == null ? '' : ' · quad ' + fmtV(d.quad_v) + ', model ' + fmtV(d.model_v) + ', relative difference ' + d.rel_diff) : d.reason;
                el.querySelector('.czp-solve-notes').textContent = (d.notes || []).join('; ');
                button.disabled = !d.ok;
                var point = operatingPoint(root, row.path, params.get('line'));
                if (point) { point.solve = d; savePoints(root); }
                if (d.ok && d.operating_point) {
                    var p = d.operating_point;
                    el.querySelector('.czp-solve-result').insertAdjacentHTML('beforeend', '<table><tr><th>Preview amplitude (V)</th><th>Preview frequency (GHz)</th></tr><tr><td>' + esc(p.amplitude_v) + '</td><td data-preview="1" data-pair="' + esc(p.pair) + '" data-op="' + esc(p.op) + '" data-line="' + esc(p.line) + '" data-freq-hz="' + esc(p.frequency_hz) + '" data-method="' + esc(p.method) + '" data-path="' + esc(p.path) + '">' + esc(p.y) + '</td></tr></table>');
                    root._czSolvePreview = el.closest('details') && el.closest('details').open ? p : null;
                }
                redrawFlux(root);
            }).catch(function (e) { el.querySelector('.czp-solve-result').textContent = e.message; });
        }
        el.onchange = preview;
        button.onclick = function () {
            if (!result || !result.ok) return;
            button.disabled = true;
            var payload = {updates: [{dot_path: result.path, value: result.amplitude_v}],
                expect_chip: result.chip, group: 'new'};
            var seen = window._keepMineSeen && window._keepMineSeen(null);
            if (seen) { payload.seen_changes = seen.n; payload.seen_sig = seen.sig; }
            fetch('/field/edit-batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})
                .then(function (r) { return r.json(); }).then(function (d) {
                    if (!d.ok) throw new Error(d.error || 'Stage failed');
                    if (d.tray_html && window._swapPendingTray) window._swapPendingTray(d.tray_html);
                    if (window._restoreTrayState) window._restoreTrayState();
                    if (window._diagChanged) window._diagChanged();
                    if (window.htmx) window.htmx.ajax('GET', '/cz-plan?op=' + encodeURIComponent(row.path), {target: '#table-pane', swap: 'innerHTML'});
                }).catch(function (e) { button.disabled = false; el.querySelector('.czp-solve-result').textContent = e.message; });
        };
        preview();
    }

    // [derived], S3-FORM: one atomic Review group, identity and drift guards.
    function stageCoupler(form) {
        var model = {}, numeric = ['fc_max_hz', 'ec_c_hz', 'd_c', 'phi0_v', 'phi_idle', 'g_c_hz', 'g_t_hz', 'g_ct_hz'];
        numeric.forEach(function (key) { model[key] = Number(form.elements.namedItem(key).value); });
        model.source = form.elements.namedItem('source').value;
        model.created_at = new Date().toISOString();
        var createExtras = form.dataset.createExtras === '1';
        var payload = {updates: [{dot_path: 'qubit_pairs.' + form.dataset.pair + (createExtras ? '.extras' : '.extras.sm_coupler_model'), value: createExtras ? {sm_coupler_model: model} : model, create: true}],
            expect_chip: window.__chipToken || '', group: 'new'};
        var seen = window._keepMineSeen && window._keepMineSeen(null);
        if (seen) { payload.seen_changes = seen.n; payload.seen_sig = seen.sig; }
        return fetch('/field/edit-batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})
            .then(function (r) { return r.json(); }).then(function (d) {
                if (!d.ok) throw new Error(d.error || 'Stage failed');
                if (d.tray_html && window._swapPendingTray) window._swapPendingTray(d.tray_html);
                refresh();
            }).catch(function (e) { form.querySelector('output').textContent = e.message; });
    }
    window.CzStageCoupler = stageCoupler;

    // [derived], S3-EXPOSE: select server-computed distances, no client physics.
    function couplerView(root) {
        var toggle = root.querySelector('#czp-include-coupler');
        root._czIncludeCoupler = !!(toggle && toggle.checked);
        root.querySelectorAll('.czp-row').forEach(function (tr) {
            var p = operatingPoint(root, tr.dataset.op, 'control');
            if (!p) return;
            var near = nearestPoint(root, p);
            if (tr.cells && tr.cells[6]) tr.cells[6].textContent = near ? near.name + ' ' + (near.distance_hz / 1e6).toFixed(1) + ' MHz' : 'coupler shift unknown';
        });
        if (window.CzMap) window.CzMap.draw(root);
    }

    var seq = 0, hseq = 0;
    var NOTES_OPEN_KEY = 'czp-notes-open';
    var RETRY_N = 20, RETRY_MS = 3000;     // re-asks while the chip's change history is being built
    var GAVE_UP = 'This chip\'s change history was still being prepared after a minute; reload the page to see when each operation was set.';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function hoverEsc(s) {
        return esc(s).replace(/%/g, '&#37;');
    }
    function colors() {
        var dark = (document.documentElement.getAttribute('data-theme') === 'dark' || document.documentElement.getAttribute('data-theme') === 'qm');
        var P = dark ? ['#79b4fa', '#f49c67', '#57b65f', '#f285b4'] : ['#245ec4', '#a34712', '#006d2c', '#ad326b'];
        var muted = getComputedStyle(document.documentElement).getPropertyValue('--pico-muted-color').trim() || '#8a8a8a';
        return { q: [P[0], P[1]], plan: P[2] || '#22c55e', res: P[3] || '#a855f7', muted: muted };
    }
    function tint(hex, alpha) {
        return 'rgba(' + [1, 3, 5].map(function (i) { return parseInt(hex.slice(i, i + 2), 16); }).join(',') + ',' + alpha + ')';
    }
    function layout(over) {
        var base = window.PlotTheme ? window.PlotTheme.houseLayout({}) : {};
        var L = window.PlotTheme ? window.PlotTheme.houseLayout(over) : over;
        ['xaxis', 'xaxis2', 'yaxis', 'yaxis2'].forEach(function (k) {
            if (over[k]) L[k] = Object.assign({}, base[k] || base[k.charAt(0) + 'axis'] || base.yaxis || {}, over[k]);
        });
        return L;
    }
    function config() {
        return window.PlotTheme ? window.PlotTheme.houseConfig({}) : { displaylogo: false, responsive: true };
    }
    function ghz(v) { return v == null ? null : v / 1e9; }
    function fmtGHz(v) { return v == null ? '—' : (v / 1e9).toFixed(4) + ' GHz'; }
    function fmtMHz(v, sign) { return v == null ? '—' : (sign && v > 0 ? '+' : '') + (v / 1e6).toFixed(1) + ' MHz'; }
    function fmtV(v) { return v == null ? '—' : Number(v).toFixed(4) + ' V'; }
    function pct(v) { return v == null ? '—' : (100 * v).toFixed(1) + ' %'; }
    /* v1.5.1 (T1-F3): a time is shown in the app's display zone with its
       offset (SnapTime, docs/244) like every other surface; the UTC text is
       only the fallback when SnapTime is not loaded. */
    function zoned(t, minutes) {
        var S = window.SnapTime, d = S && S.instant ? S.instant(t) : null;
        if (!d) return null;
        if (!minutes) return S.display(d);
        return S.format(d).slice(0, 16) + ' (' + S.offsetLabel(d) + ')';
    }
    /* an ISO time inside a server note ("first recorded <t>") reads the same */
    function zoneText(s) {
        return String(s == null ? '' : s).replace(/\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)/g,
            function (m) { return zoned(m, false) || m; });
    }
    function when(p) {
        if (!p || !p.t) return 'unknown';
        var t = zoned(p.t, false) || String(p.t).replace('T', ' ').replace(/\.\d+/, '').replace('Z', ' UTC');
        return (p.run_id != null ? 'run #' + p.run_id + ' · ' : '') + t;
    }
    function stamp(t) {
        if (!t) return 'time unknown';
        return zoned(t, true) || String(t).replace('T', ' ').replace(/:\d\d(\.\d+)?Z$/, ' UTC').replace(/\.\d+/, '');
    }
    function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

    function active(root, token) {
        return token === seq && document.body.contains(root);
    }

    function empty(el, text, root, token) {
        if (!active(root, token)) return;
        el.__plotlyRenderEpoch = (el.__plotlyRenderEpoch || 0) + 1;
        if (window.PlotHost) window.PlotHost.purgeWithin(el);
        el.removeAttribute('data-dwell');
        el.removeAttribute('data-notes');
        el.innerHTML = '<p class="muted zline-empty">' + esc(text) + '</p>';
    }

    /* The visible line under the title: why a figure is empty or the load failed. */
    function setStatus(root, lines) {
        var p = root.querySelector('#czp-status');
        if (!p) return;
        p.innerHTML = lines.map(esc).join('<br>');
        p.hidden = !lines.length;
    }

    /* The staleness warnings grouped per qubit and per run (newest run first). The route
       sends warning_groups; older answers carry only the flat list. */
    function warningGroups(cal) {
        if (cal.warning_groups) return cal.warning_groups;
        var by = {}, order = [];
        (cal.warnings || []).forEach(function (w) {
            var parts = String(w.path || '').split('.'), q = parts[0] === 'qubits' ? parts[1] : '?';
            var field = parts[0] === 'qubits' ? parts.slice(2).join('.') : String(w.path || '');
            if (!by[q]) { by[q] = {}; order.push(q); }
            var key = String(w.run_id) + '|' + (w.t || '') + '|' + (w.provenance || '');
            (by[q][key] = by[q][key] || { run_id: w.run_id, t: w.t, provenance: w.provenance, words: w.words, fields: [] }).fields.push(field);
        });
        return order.map(function (q) {
            var runs = Object.keys(by[q]).map(function (k) { return by[q][k]; });
            runs.sort(function (a, b) { return String(b.t || '').localeCompare(String(a.t || '')); });
            return { qubit: q, runs: runs };
        });
    }

    /* fix3 P1-2: how a change is named -- the route's words (cz_gate_plan.change_words: "changed
       by run #M" only for a run the ledger proves wrote it, "first seen in run #M's saved state"
       for one that only carried it first); an answer without them names the run neutrally. */
    function changeWords(r) {
        if (r.words) return r.words;
        return r.run_id != null ? 'seen in run #' + r.run_id : 'changed';
    }
    /* Members whose staleness reads the same (same runs, same fields) share one line. */
    function mergeGroups(groups) {
        var out = [], at = {};
        groups.forEach(function (g) {
            var key = JSON.stringify((g.runs || []).map(function (r) {
                return [r.run_id, r.t, r.provenance || null, (r.fields || []).slice().sort()];
            }));
            if (at[key] != null) { out[at[key]].qubits.push(g.qubit); return; }
            at[key] = out.length;
            out.push({ qubits: [g.qubit], runs: g.runs || [] });
        });
        return out;
    }
    /* The Last set provenance in words: only run_proven names a writer. */
    function setWords(p, proven) {
        var prov = p && p.provenance;
        if (prov === 'run_proven') return proven ? 'written by that run' : 'writer not proven';
        if (prov === 'run_saved') return 'first seen in that run\'s saved state; writer not proven';
        if (prov === 'run_uncertain_chip') return 'saved by a run whose chip is uncertain; writer not proven';
        if (prov === 'sm') return 'set in SM';
        return 'writer not proven';
    }

    function notesOpen() {
        try { return window.sessionStorage.getItem(NOTES_OPEN_KEY) === '1'; } catch (e) { return false; }
    }
    function rememberNotes(open) {
        try { window.sessionStorage.setItem(NOTES_OPEN_KEY, open ? '1' : '0'); } catch (e) { /* storage off: still works */ }
    }
    function openNotes(root) {
        var box = root.querySelector('#czp-notes-box');
        if (!box) return;
        box.open = true;
        rememberNotes(true);
        try { box.scrollIntoView({ block: 'nearest' }); } catch (e) { /* jsdom */ }
    }

    /* Notes: each caveat stays visible under the figure it explains (tagged by the route;
       the qubits' own summary notes explain the flux figure); the chip's unreadable entries go
       to the status line; the staleness warnings and the notes on how a figure was drawn go
       into the collapsed box below the card, with a chip in the title row that opens it. */
    function renderNotes(root, d) {
        var tags = d.notes_figure || [], status = [], unknown = [], nodes = [], cave = { flux: [], time: [] };
        (d.notes || []).forEach(function (t, i) {
            if (t.startsWith('coupler shift unknown')) { unknown.push(t); return; }
            if (t.startsWith('official nodes 30-34b')) { nodes.push(t); return; }
            var tag = tags[i] || 'both';
            if (tag === 'status') status.push(t);
            else if (tag === 'flux') cave.flux.push(t);
            else if (tag === 'time') cave.time.push(t);
            else { cave.flux.push(t); cave.time.push(t); }
        });
        (d.qubits || []).forEach(function (q) {
            if (!q.summary) return;
            var curv = (q.summary.curvature || {}).note;
            (q.summary.notes || []).forEach(function (t) {
                cave.flux.push(t === curv ? t + ' (' + q.name + ')' : q.name + ': ' + t);
            });
        });
        setStatus(root, status);
        var note = root.querySelector('#czp-pair-coupler-note');
        if (note) {
            // one short line per kind, the full sentence behind (?) (UX-10)
            var parts = [];
            if (unknown.length) parts.push('coupler shift unknown' + help(Array.from(new Set(unknown)).join('; ')));
            if (nodes.length) parts.push('official nodes 30-34b do not apply' + help(Array.from(new Set(nodes)).join('; ')));
            note.innerHTML = parts.join(' · ');
            note.hidden = !parts.length;
        }
        ['flux', 'time'].forEach(function (f) {
            var p = root.querySelector('#czp-' + f + '-caveat');
            if (!p) return;
            p.innerHTML = cave[f].map(esc).join('<br>');
            p.hidden = !cave[f].length;
        });
        var cal = d.calibration || {}, groups = warningGroups(cal);
        var count = cal.warning_count != null ? cal.warning_count : (cal.warnings || []).length;
        var info = d.info || [];
        var chip = root.querySelector('#czp-stale-chip');
        if (chip) {
            chip.textContent = plural(count, 'input changed', 'inputs changed') + ' after this operation was set';
            chip.hidden = !count;
        }
        var ul = root.querySelector('#czp-notes');
        if (ul) {
            ul.innerHTML = mergeGroups(groups).map(function (g) {
                return '<li class="zline-note-warn"><strong class="czp-note-q">' + esc(g.qubits.join(', ')) + '</strong> ' + g.runs.map(function (r) {
                    return esc(changeWords(r)) + ' (' + esc(stamp(r.t)) + '): ' + esc((r.fields || []).join(', '));
                }).join('; ') + '</li>';
            }).concat(info.map(function (t) {
                return '<li class="zline-note-info czp-note-muted"><strong>note</strong> ' + esc(t) + '</li>';
            })).join('');
        }
        var sum = root.querySelector('#czp-notes-summary');
        if (sum) {
            var parts = [];
            if (count) parts.push(plural(count, 'input changed', 'inputs changed') + ' after this operation was set');
            if (info.length) parts.push(plural(info.length, 'note', 'notes') + ' on how the figures were drawn');
            sum.textContent = 'Notes — ' + (parts.length ? parts.join(' · ') : 'none');
        }
        var box = root.querySelector('#czp-notes-box');
        if (box) box.open = notesOpen();
    }

    /* The qubits' measured loss (the page's /cz-plan/loss answer, held by the map) as a
       narrow panel to the right of the flux figure: where the levels meet beside how lossy
       that frequency was in the scan. */
    function lossPanel(root, d, C) {
        var loss = window.CzMap ? window.CzMap.loss(root) : null;
        var scale = window.CzMap ? window.CzMap.scale(root) : 0.25;
        var curves = (loss && loss.curves) || {}, traces = [], shapes = [];
        var clip = { x: [], y: [], t: [], c: [] };
        d.qubits.forEach(function (q, k) {
            var e = curves[q.name], c = e && e.scan;
            if (!c) return;
            var xs = [], ys = [], ts = [];
            (c.pieces || []).forEach(function (pc) {
                var run = [];
                function flush() {
                    if (run.length) {
                        xs.push(0); ys.push(run[0][1]); ts.push(null);
                        run.forEach(function (s) { xs.push(s[0]); ys.push(s[1]); ts.push(s[2]); });
                        xs.push(0); ys.push(run[run.length - 1][1]); ts.push(null);
                        xs.push(null); ys.push(null); ts.push(null);
                        // fix3 P2-2: a stretch past the panel's scale gets an edge tick at its peak
                        var j = 0;
                        while (j < run.length) {
                            if (run[j][3] <= scale) { j++; continue; }
                            var top = j;
                            while (j < run.length && run[j][3] > scale) { if (run[j][3] > run[top][3]) top = j; j++; }
                            clip.x.push(100 * scale); clip.y.push(run[top][1]); clip.c.push(C.q[k]);
                            clip.t.push(esc(q.name) + ' run #' + esc(c.run_id) + ': loss ' + pct(run[top][3]) + ' at ' + run[top][1].toFixed(4) +
                                        ' GHz<br>past the panel scale (0–' + Math.round(100 * scale) + ' %): clipped');
                        }
                    }
                    run = [];
                }
                for (var j = 0; j < (pc.frequency_hz || []).length; j++) {
                    var f = pc.frequency_hz[j], l = (pc.loss || [])[j];
                    if (typeof f !== 'number' || typeof l !== 'number' || !isFinite(f) || !isFinite(l)) { flush(); continue; }
                    run.push([100 * Math.min(scale, Math.max(0, l)), f / 1e9,
                              esc(q.name) + ' run #' + esc(c.run_id) + '<br>' + (f / 1e9).toFixed(4) + ' GHz · loss ' + pct(l), l]);
                }
                flush();
            });
            if (!xs.length) return;
            traces.push({ type: 'scatter', mode: 'lines', xaxis: 'x2', x: xs, y: ys, text: ts, fill: 'toself', fillcolor: tint(C.q[k], 0.16),
                          line: { color: C.q[k], width: 1 }, name: q.name + ' · scan loss (run #' + c.run_id + ')',
                          hovertemplate: '%{text}<extra></extra>', showlegend: false });
        });
        if (!traces.length) return null;
        if (clip.x.length) {
            traces.push({ type: 'scatter', mode: 'markers', xaxis: 'x2', x: clip.x, y: clip.y, text: clip.t, showlegend: false,
                          marker: { symbol: 'triangle-right', size: 8, color: clip.c, line: { width: 1, color: C.muted } },
                          hovertemplate: '%{text}<extra></extra>', name: 'clipped', cliponaxis: false, _czpClip: true });
        }
        d.row.lines.forEach(function (ln, k) {
            var f = ln.point && ln.point.frequency_hz;
            if (!ln.moves || f == null) return;
            shapes.push({ type: 'line', xref: 'paper', x0: 0.80, x1: 1, yref: 'y', y0: ghz(f), y1: ghz(f),
                          line: { color: C.q[k], width: 2.5 }, name: 'operating:' + ln.name });
        });
        return { traces: traces, shapes: shapes, scale: scale };
    }

    /* (a) f vs flux amplitude: each qubit's curve(s), its idle point, an arrow to the selected
       operation's operating point, the pair's other operations faintly, the resonance levels at
       the operating height, the planned interaction point and the defect bands; beside it the
       qubits' measured loss. */
    function drawFlux(root, d, token) {
        var el = root.querySelector('#czp-flux');
        if (!el) return Promise.resolve();
        var C = colors(), traces = [], shapes = [], notes = [];
        var lines = d.row.lines, pts = lines.map(function (ln) { return ln.point; });
        var side = lossPanel(root, d, C), right = side ? 0.76 : 1;
        d.qubits.forEach(function (q, k) {
            if (!q.summary || !q.curve) return;
            var col = C.q[k], c = q.curve, s = q.summary;
            if (c.model_hz) {
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.model_hz.map(ghz), name: q.name + ' · ' + s.model_kind + ' · ' + (lines[k].point.validity || 'validity unknown'),
                    line: { color: col, width: 2.2, dash: /derived/.test(s.model_kind || '') ? 'dash' : 'solid' }, hovertemplate: 'amplitude: %{x:.4f} V<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>' + hoverEsc(s.model_kind) + '</extra>' });
            }
            // with no quad term the curve holds only the idle point: one point draws no line, so no legend entry
            if (c.quad_hz.filter(function (v) { return v != null; }).length > 1) {
                // beside a fitted model the quad term is the secondary curve: it never sets the frequency span
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.quad_hz.map(ghz), name: q.name + ' · quad-term estimate · ' + (lines[k].point.validity || 'validity unknown'),
                    line: { color: col, width: 1.4, dash: 'dash' }, _czpSecondary: !!c.model_hz,
                    hovertemplate: 'amplitude: %{x:.4f} V<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>' + hoverEsc(lines[k].point.validity || 'validity unknown') + '</extra>' });
            }
            var idle = !lines[k].moves && operatingPoint(root, d.row.path, lines[k].role, lines[k].operating_point);
            traces.push({ type: 'scatter', mode: 'markers', x: [idle ? idle.x : 0], y: [idle ? idle.y : ghz(s.f01_hz)], _czOperating: idle, name: q.name + ' idle', showlegend: false,
                marker: { color: col, size: 11, symbol: 'circle-open', line: { width: 2 } },
                hovertemplate: hoverEsc(q.name) + ' idle f_01: %{y:.4f} GHz<extra></extra>' });
            (q.tls || []).forEach(function (b) {
                shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(b.lo_hz), y1: ghz(b.hi_hz),
                    fillcolor: tint(col, 0.08), line: { color: col, width: 1 }, layer: 'below' });
            });
            if ((q.tls || []).length) notes.push(q.name + ': ' + q.tls.length + ' measured defect band' + (q.tls.length === 1 ? '' : 's') + ' shaded');
        });
        d.pair_ops.forEach(function (op) {
            if (op.path === d.row.path) return;
            op.points.forEach(function (p, k) {
                if (p.frequency_hz == null || !p.amplitude_v) return;
                var shared = operatingPoint(root, op.path, k === 0 ? 'control' : 'target');
                traces.push({ type: 'scatter', mode: 'markers', x: [shared ? shared.x : p.amplitude_v], y: [shared ? shared.y : ghz(p.frequency_hz)], _czOperating: shared, showlegend: false,
                    marker: { color: C.q[k], size: 7, symbol: 'circle-open', line: { color: C.q[k], width: 1 } },
                    hovertemplate: hoverEsc(op.operation) + ' ' + hoverEsc(p.kind || '') + '<br>amplitude: %{x:.4f} V<br>%{y:.4f} GHz<extra></extra>' });
            });
        });
        var annotations = [];
        var preview = root._czSolvePreview;
        if (preview && preview.op === d.row.path) {
            traces.push({type: 'scatter', mode: 'markers', x: [preview.x], y: [preview.y],
                name: 'Solve preview', _czOperating: preview, marker: {size: 14, symbol: 'diamond-open'}});
        }
        lines.forEach(function (ln, k) {
            var p = ln.point, q = d.qubits[k];
            if (!ln.moves || p.frequency_hz == null || !q.summary) return;
            var shared = operatingPoint(root, d.row.path, ln.role, ln.operating_point);
            traces.push({ type: 'scatter', mode: 'markers', x: [shared ? shared.x : ln.amplitude_v], y: [shared ? shared.y : ghz(p.frequency_hz)], _czOperating: shared, name: q.name + ' at ' + d.row.operation + ' ' + (p.kind || ''),
                showlegend: false, marker: { color: C.q[k], size: 12, symbol: 'diamond' },
                hovertemplate: hoverEsc(q.name) + ' at ' + hoverEsc(d.row.operation) + '<br>amplitude: %{x:.4f} V<br>%{y:.4f} GHz<extra>' + hoverEsc(p.kind) + '</extra>' });
            (ln.lobes || []).slice(1).forEach(function (l) {
                if (l.point.frequency_hz == null) return;
                traces.push({type: 'scatter', mode: 'markers', x: [l.amplitude_v], y: [ghz(l.point.frequency_hz)],
                    name: q.name + ' ' + l.name + ' ' + (l.point.kind || ''), showlegend: false,
                    marker: {color: C.q[k], size: 9, symbol: 'diamond-open'},
                    hovertemplate: hoverEsc(q.name + ' ' + l.name) + '<br>%{y:.4f} GHz<extra>' + hoverEsc(l.point.kind) + '</extra>'});
            });
            annotations.push({ x: ln.amplitude_v, y: ghz(p.frequency_hz), ax: 0, ay: ghz(q.summary.f01_hz), axref: 'x', ayref: 'y',
                xref: 'x', yref: 'y', showarrow: true, arrowhead: 3, arrowsize: 1.2, arrowwidth: 1.6, arrowcolor: C.q[k], text: '' });
        });
        // resonance levels for the moving control at the target's operating height
        el.setAttribute('data-resonance-levels', JSON.stringify(d.time_panel.levels));
        d.time_panel.levels.forEach(function (level) {
                var lv = [level.name, level.frequency_hz];
                if (lv[1] == null) return;
                shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(lv[1]), y1: ghz(lv[1]),
                    line: { color: C.res, width: 1, dash: 'dash' }, layer: 'below' });
                annotations.push({ xref: 'paper', x: 1, xanchor: 'left', xshift: 8, yref: 'y', y: ghz(lv[1]), yanchor: 'bottom', showarrow: false,
                    text: 'control on ' + lv[0], font: { size: 10, color: C.res } });
        });
        var plan = d.row.plan;
        if (plan && plan.omega_int_hz) {
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(plan.omega_int_hz), y1: ghz(plan.omega_int_hz),
                line: { color: C.plan, width: 1.4, dash: 'dash' } });
            annotations.push({ xref: 'paper', x: 0, xanchor: 'left', yref: 'y', y: ghz(plan.omega_int_hz), yanchor: 'bottom', showarrow: false,
                text: 'planned ω_int (' + esc(plan.leakage_qubit || '?') + ' carries |2>)', font: { size: 10, color: C.plan } });
        }
        if (d.row.interaction_frequency_hz) {
            var fi = d.row.interaction_frequency_hz;
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(fi), y1: ghz(fi), line: { color: C.plan, width: 1.4, dash: 'dash' } });
            annotations.push({ xref: 'paper', x: 0, xanchor: 'left', yref: 'y', y: ghz(fi), yanchor: 'bottom', showarrow: false,
                text: 'stored interaction frequency', font: { size: 10, color: C.plan } });
        }
        var over = {
            margin: { l: 64, r: 142, t: 44, b: 120 }, showlegend: true,
            legend: { orientation: 'h', y: -0.42, x: 0, font: { size: 11 } },
            xaxis: { title: { text: 'flux amplitude (V)' }, zeroline: true, domain: [0, right] },
            yaxis: { title: { text: 'frequency (GHz)' } },
            shapes: shapes, annotations: annotations, hovermode: 'closest'
        };
        // The frequency span follows what is read here -- the main curves, the idle and operating points
        // and the resonance / plan levels; a quad-term estimate drawn beside a fitted model leaves the
        // span (it ran to ~10 GHz and flattened the operating region); the loss panel is cut to the span.
        var lo = Infinity, hi = -Infinity;
        traces.forEach(function (t) {
            if (t._czpSecondary) return;
            (t.y || []).forEach(function (v) { if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
        });
        shapes.forEach(function (s) { if (s.yref === 'y' && s.type === 'line') { lo = Math.min(lo, s.y0); hi = Math.max(hi, s.y0); } });
        if (isFinite(lo) && isFinite(hi)) {
            var pad = Math.max(0.01, (hi - lo) * 0.04);
            over.yaxis.range = [lo - pad, hi + pad];
        }
        el.setAttribute('data-yspan', isFinite(lo) && isFinite(hi) ? lo.toFixed(4) + ' ' + hi.toFixed(4) : '');
        if (side) {
            traces = traces.concat(side.traces);
            over.shapes = shapes.concat(side.shapes);
            over.xaxis2 = { domain: [0.80, 1], anchor: 'y', range: [0, 100 * side.scale], fixedrange: true,
                            title: { text: 'scan loss (0–' + Math.round(100 * side.scale) + ' %)', font: { size: 11 } }, zeroline: false };
            el.setAttribute('data-loss-panel', String(side.traces.filter(function (t) { return !t._czpClip; }).length));
        } else {
            el.removeAttribute('data-loss-panel');
        }
        var L = layout(over);
        el.setAttribute('data-notes', notes.join('; '));
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
            return Promise.resolve(window._plotlyRender(el, traces, L, config(), function () { return active(root, token); })).then(function () { stampPoints(el); });
        });
    }

    /* (b) f(t) of both qubits from the operation's waveforms, and Delta(t) against the three
       resonance conditions, so it shows when and how long the pair sits on the intended one. */
    function drawTime(root, d, token) {
        var el = root.querySelector('#czp-time');
        if (!el) return Promise.resolve();
        var C = colors(), traj = d.trajectory, n = 0;
        traj.forEach(function (t) { if (t) n = Math.max(n, t.frequency_hz.length); });
        if (!n) {
            empty(el, 'Not drawn: SM cannot draw the waveform of a moving qubit (the reason is above the figure).', root, token);
            return Promise.resolve();
        }
        var panel = d.time_panel, tt = panel.time_ns, series = panel.series_hz;
        var traces = [];
        d.qubits.forEach(function (q, k) {
            traces.push({ type: 'scatter', mode: 'lines', x: tt, y: series[k].map(ghz), name: q.name, line: { color: C.q[k], width: 2, shape: 'hv' },
                hovertemplate: 't = %{x} ns<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>' + hoverEsc(d.row.lines[k].point.kind || 'unknown') + '</extra>' });
            var t = traj[k];
            if (t && t.model_hz && t.quad_hz) {
                traces.push({ type: 'scatter', mode: 'lines', x: tt.slice(0, t.quad_hz.length), y: t.quad_hz.map(ghz), name: q.name + ' · quad-term estimate · ' + (d.row.lines[k].point.validity || 'validity unknown'),
                    line: { color: C.q[k], width: 1, dash: 'dash', shape: 'hv' }, hovertemplate: 't = %{x} ns<br>%{y:.4f} GHz<extra>' + hoverEsc(d.row.lines[k].point.validity || 'validity unknown') + '</extra>' });
            }
        });
        var delta = panel.detuning_mhz;
        traces.push({ type: 'scatter', mode: 'lines', x: tt, y: delta, name: 'Δ = f_c − f_t', yaxis: 'y2', line: { color: C.q[0], width: 4, shape: 'hv' },
            hovertemplate: 't = %{x} ns<br>Δ: %{y:.1f} MHz<extra></extra>' });
        var shapes = [], annotations = [];
        el.setAttribute('data-resonance-levels', JSON.stringify(panel.levels));
        panel.levels.forEach(function (level, index) {
            var lv = [level.name, level.detuning_mhz];
            shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: level.lo_mhz, y1: level.hi_mhz, fillcolor: tint(C.res, 0.08), line: { width: 0 }, layer: 'below' });
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: lv[1], y1: lv[1], line: { color: C.res, width: 1, dash: 'dash' }, layer: 'below' });
            // Spread the labels along their own levels: a large idle detuning packs them vertically.
            annotations.push({ xref: 'paper', x: 1 - index * 0.2, xanchor: 'right', yref: 'y2', y: lv[1], yanchor: 'bottom', showarrow: false, text: lv[0], font: { size: 10, color: C.res } });
        });
        el.setAttribute('data-dwell', panel.dwell);
        var frequencyTraces = traces.filter(function (t) { return t.yaxis !== 'y2'; });
        traces = traces.filter(function (t) { return t.yaxis === 'y2'; });
        var L = layout({
            margin: { l: 64, r: 16, t: 32, b: 90 }, showlegend: true,   // t: clear of the toolbar (U P2-4)
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 11 } },
            xaxis: { title: { text: 'time (ns, 1 sample per ns)' }, anchor: 'y2' },
            yaxis: { visible: false, domain: [0, 1] },
            yaxis2: { title: { text: 'Δ (MHz)' }, domain: [0, 1], anchor: 'x' },
            shapes: shapes, annotations: annotations, hovermode: 'x unified'
        });
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
            var frequency = root.querySelector('#czp-frequency');
            var box = root.querySelector('#czp-frequency-box');
            function drawFrequency() {
                if (frequency && box && box.open) window._plotlyRender(frequency, frequencyTraces, layout({
                    margin: {l: 64, r: 20, t: 32, b: 100}, showlegend: true,
                    xaxis: {title: {text: 'time (ns)'}}, yaxis: {title: {text: 'frequency (GHz)'}},
                    legend: {orientation: 'h', y: -0.35}
                }), config(), function () { return active(root, token); });
            }
            if (box) box.ontoggle = drawFrequency;
            drawFrequency();
            return window._plotlyRender(el, traces, L, config(), function () { return active(root, token); });
        });
    }

    /* What a "not measured at this frequency (...)" reason adds after the headline that already
       says it: "the scan covers 4.241-4.761 GHz", not the headline twice. */
    function notMeasuredWhy(reason) {
        return String(reason || '').replace(/^not measured at this frequency\s*/, '').replace(/^\((.*)\)$/, '$1');
    }

    /* The scan's loss at the operating frequency and the stored defects the gate's own
       excursion touches ([derived] on the server: cz_loss.loss_at / defects_swept). */
    function gateLossHtml(gl) {
        if (!gl || !gl.moves) return '';
        if (gl.reason) return '<p class="czp-card-loss"><strong>Scan loss at the operating frequency:</strong> <span class="muted">' + esc(gl.reason) + '</span></p>';
        var h = [], s = gl.scan || {};
        var why = s.interaction_time_ns != null ? esc(s.interaction_time_ns) + ' ns' : 'scan duration unknown';
        if (s.loss != null) {
            h.push('<p class="czp-card-loss"><strong>Scan loss at the operating frequency:</strong> ' + pct(s.loss) +
                   (s.loss < 0 ? ' <small class="muted">(below zero: no loss resolved)</small>' : '') +
                   ' <small class="muted">(run #' + esc(s.run_id) + ', ' + why + ' flux excursion from idle: dwell there plus every defect crossed on the way, with the scan\'s ramp)</small>' +
                   ((s.pieces || []).length > 1 ? '<br><small class="muted">' + s.pieces.map(function (p) {
                       return 'piece ' + esc(p.piece) + ': ' + (p.loss != null ? pct(p.loss) : esc(p.reason || 'not measured')); }).join('; ') + '</small>' : '') + '</p>');
        } else {
            h.push('<p class="czp-card-loss"><strong>Scan loss at the operating frequency:</strong> ' +
                   (s.run_id != null ? 'not measured at this frequency <small class="muted">(run #' + esc(s.run_id) + (notMeasuredWhy(s.reason) ? '; ' + esc(notMeasuredWhy(s.reason)) : '') + ')</small>'
                                     : '<span class="muted">' + esc(s.reason || 'no scan') + '</span>') +
                   ((s.pieces || []).length ? '<br><small class="muted">' + s.pieces.map(function (p) {
                       return 'piece ' + esc(p.piece) + ': ' + (p.loss != null ? pct(p.loss) : esc(p.reason || 'not measured')); }).join('; ') + '</small>' : '') + '</p>');
        }
        var sw = gl.sweep, touched = gl.defects_touched || [];
        h.push('<p class="czp-card-loss"><strong>Defects touched during the gate:</strong> ' + (touched.length ? touched.map(function (t) {
            return fmtGHz(t.frequency_hz) + ' <small class="muted">(FWHM ' + fmtMHz(t.fwhm_hz) + (t.peak_loss != null ? ', peak ' + pct(t.peak_loss) : '') +
                (t.inside ? '' : ', ' + fmtMHz(t.distance_hz) + ' outside') + ')</small>' + (t.strong ? ' <span class="czp-warn-text">strong</span>' : '');
        }).join('; ') : 'none') +
            (sw ? help('excursion ' + fmtGHz(sw.f_min_hz) + ' – ' + fmtGHz(sw.f_max_hz) + ' ' + (sw.label || '') + (gl.marker && gl.marker.label ? '; ' + gl.marker.label : '') + (sw.note ? '; ' + sw.note : '')) : '') + '</p>');
        return h.join('');
    }

    /* (c) the operation's card. */
    function help(text) {
        return ' <span class="czp-help-hover" tabindex="0" role="note" aria-label="' + esc(text) + '" title="' + esc(text) + '">(?)</span>';
    }

    function renderCard(root, d) {
        var el = root.querySelector('#czp-card');
        if (!el) return;
        var r = d.row, cal = d.calibration, h = [], gls = d.gate_loss || [];
        h.push('<h4>' + esc(r.pair) + ' · ' + esc(r.operation) + ' <small class="muted">' + esc(r['class'] || '') + ' · ' + esc(r.kind) +
            (r.alias_of ? ' · alias of ' + esc(r.alias_of) : '') + '</small></h4>');
        h.push('<div class="czp-card-grid">');
        r.lines.forEach(function (ln, k) {
            var p = ln.point, q = d.qubits[k], s = q.summary || {};
            var gl = gls.find(function (g) { return g.role === ln.role; }) || null;
            h.push('<div class="czp-card-q"><strong>' + esc(ln.role) + ' ' + esc(ln.name) + '</strong>' +
                (ln.moves ? ' <small class="muted">plays ' + esc((ln.pulse_path || '').split('.').pop()) + (ln.pulse_class ? ' (' + esc(ln.pulse_class) + ')' : '') + '</small>' : ' <small class="muted">does not move</small>') +
                '<dl>' +
                '<dt>Z amp</dt><dd title="' + esc(ln.amp_title || '') + '">' + esc(ln.amp_text || '\u2014') + '</dd>' +
                '<dt>Idle f_01</dt><dd>' + fmtGHz(s.f01_hz) + '</dd>' +
                '<dt>At the operation</dt><dd>' + fmtGHz(p.frequency_hz) + (p.kind ? ' <span class="czp-tier' + (/quad/.test(p.kind) ? ' czp-tier-quad' : '') + '">' + esc(p.kind) + '</span>' : '') +
                (p.reason ? '<br><span class="czp-warn-text">' + esc(p.reason) + '</span>' : '') + '</dd>' +
                (p.quad_hz != null && p.model_hz != null && !s.model_rejection ? '<dt>' + esc(p.quad_kind || 'Quad-term estimate') + '</dt><dd>' + fmtGHz(p.quad_hz) + ' <small class="muted">(' + fmtMHz(p.error_hz) + ' from the model)</small></dd>' : '') +
                (s.model_rejection && p.model_hz != null ? '<dt>Re-anchored estimate [derived]</dt><dd>' + fmtGHz(p.model_hz) + '</dd>' : '') +
                (p.validity ? '<dt>Validity</dt><dd><span class="' + (/outside|beyond/.test(p.validity) ? 'czp-warn-text' : '') + '">' + esc(p.validity) + '</span>' + (/quadratic/.test(p.validity) && s.range && s.range.limit_hz ? help('limit ' + fmtMHz(s.range.limit_hz) + ', ' + (s.range.source || '')) : '') + '</dd>' : '') +
                (ln.lobes || []).slice(1).map(function (l) {
                    return '<dt>' + esc(l.name) + '</dt><dd>' + fmtGHz(l.point.frequency_hz) +
                        ' <small>' + esc(l.point.kind || l.point.reason || 'unknown') + '</small></dd>';
                }).join('') +
                '<dt>Direction</dt><dd>' + esc((s.frame || {}).reason || '—') + '</dd>' +
                (r.plan && r.plan.roles[ln.role] && r.plan.roles[ln.role].plan_v != null ? '<dt>Planned amplitude</dt><dd>' + fmtV(r.plan.roles[ln.role].plan_v) +
                    (r.plan.roles[ln.role].difference_v != null ? ' <small class="muted">(operation ' + (r.plan.roles[ln.role].difference_v >= 0 ? '+' : '') + (r.plan.roles[ln.role].difference_v * 1e3).toFixed(1) + ' mV)</small>' : '') + '</dd>' : '') +
                '<dt>Phase correction</dt><dd>' + (r.phases[ln.role] == null ? '—' : Number(r.phases[ln.role]).toFixed(4) + ' × 2π') + '</dd>' +
                '</dl>' + gateLossHtml(gl) + '</div>');
        });
        var fid = r.fidelity || {}, fl = [];
        ['InterleavedRB', 'StandardRB'].forEach(function (k) {
            if (fid[k]) fl.push(k + ' ' + Number(fid[k].value).toFixed(4) + (fid[k].load_id != null ? ' <small class="muted">(load id ' + esc(fid[k].load_id) + ')</small>' : ''));
        });
        var near = r.nearest;
        var count = cal.warning_count != null ? cal.warning_count : (cal.warnings || []).length;
        h.push('<div class="czp-card-q"><strong>Operation</strong><dl>' +
            '<dt>Δ at the operation</dt><dd>' + fmtMHz(r.detuning_hz, true) + ' <small class="muted">(idle ' + fmtMHz(r.idle_detuning_hz, true) + ')</small></dd>' +
            '<dt>Nearest resonance</dt><dd>' + (near ? esc(near.name) + ' ' + fmtMHz(near.distance_hz, true) + ' away' + (near.lobe ? ' (' + esc(near.lobe) + ', closest across played lobes)' : '') : '—') + '</dd>' +
             '<dt>Duration</dt><dd>' + (r.duration_ns != null ? r.duration_ns + ' ns' : 'unknown') + (r.duration_note ? help(r.duration_note) : '') + '</dd>' +
            (r.coupler ? '<dt>Coupler pulse</dt><dd class="czp-card-coupler" title="' + esc(r.coupler.amp_title) + '">' + esc(r.coupler.amp_text) + (r.coupler.amp_reason ? ' <small class="muted">(' + esc(r.coupler.amp_reason) + ')</small>' : '') + '</dd>' : '') +
            (r.plan ? '<dt>Planned ω_int</dt><dd>' + fmtGHz(r.plan.omega_int_hz) + (r.plan.path_ok === false ? ' <span class="czp-warn-text">path not ok</span>' : '') +
                (r.plan.reasons.length ? '<br><small class="muted">' + esc(r.plan.reasons.join(', ')) + '</small>' : '') + '</dd>' : '') +
            (r.interaction_frequency_hz ? '<dt>Stored interaction frequency</dt><dd>' + fmtGHz(r.interaction_frequency_hz) + '</dd>' : '') +
            '<dt>Fidelity</dt><dd>' + (fl.length ? fl.join('<br>') : '—') + '</dd>' +
             '<dt>Last set</dt><dd>' + (cal.last_set ? esc(when(cal.last_set)) + help(setWords(cal.last_set, cal.proven)) :
                'unknown' + (cal.note ? help(zoneText(cal.note)) : '')) + '</dd>' +
            (count ? '<dt>Staleness</dt><dd><a href="#czp-notes-box" class="czp-notes-link czp-warn-text">' +
                plural(count, 'input changed', 'inputs changed') + ' after this operation was set</a></dd>' : '') +
            '</dl></div>');
        h.push('</div>');
        el.innerHTML = h.join('');
        renderSolve(root, r);
    }

    /* A4 (coupler-only CZ): one top line, coupler amplitude -> coupler frequency -> ZZ ->
       starting flat time. Every number is the server's (row.a4 / the operating-point blob). */
    function a4Point(root, row) {
        return operatingPoint(root, row.path, 'coupler', null) || {};
    }
    function fmtMHzZ(v) { return v == null ? '—' : (v / 1e6).toFixed(3) + ' MHz'; }
    function renderA4Match(root, row) {
        var match = root.querySelector('#czp-match');
        if (!match) return;
        var a = row.a4, p = a4Point(root, row);
        var stored = a.stored_flat_length_ns != null ? a.stored_flat_length_ns : a.stored_length_ns;
        var amp = a.amplitude_v == null ? 'unknown' : (a.amplitude_v >= 0 ? '+' : '') + (a.amplitude_v * 1e3).toFixed(1) + ' mV';
        var text = 'coupler amplitude ' + amp + ' → coupler ' + fmtGHz(p.coupler_frequency_hz) + ' → ZZ ' + fmtMHzZ(p.zz_hz) +
            ' → gate time ' + (p.t_flat_ns != null ? p.t_flat_ns + ' ns' : '—') +
            ' | stored flat ' + (stored != null ? stored + ' ns' : '—') + ' (' + (a.method || 'no model') + ')';
        match.innerHTML = esc(text) + (a.refusal ? ' <span class="czp-warn-text">' + esc(a.refusal) + '</span>' : '') +
            ((a.warnings || []).length ? ' <span class="czp-warn-text">' + esc(a.warnings.join('; ')) + '</span>' : '') +
            help('t = 1/(2|ZZ|) for a pi conditional phase, rounded up to 4 ns: a starting value, the ramps add phase; calibrate. ' +
                 'ZZ from diagonalising the qubit-coupler-qubit Hamiltonian (3 levels per mode).');
        var attrs = {'a4-amplitude-v': a.amplitude_v, 'a4-coupler-frequency-hz': p.coupler_frequency_hz, 'a4-zz-hz': p.zz_hz,
                     'a4-t-flat-ns': p.t_flat_ns, 'a4-stored-flat-ns': stored, 'a4-method': a.method, 'a4-path': a.amplitude_path,
                     'a4-refusal': a.refusal};
        Object.keys(attrs).forEach(function (k) { match.setAttribute('data-' + k, attrs[k] == null ? '' : attrs[k]); });
    }
    function drawA4(root, d, token) {
        var row = d.row, a = row.a4, p = a4Point(root, row), C = colors();
        var flux = root.querySelector('#czp-flux'), time = root.querySelector('#czp-time');
        var capF = flux && flux.closest('figure') && flux.closest('figure').querySelector('figcaption');
        var capT = time && time.closest('figure') && time.closest('figure').querySelector('figcaption');
        [[capF, 'Coupler frequency vs coupler amplitude'], [capT, 'ZZ vs coupler amplitude']].forEach(function (c) {
            if (!c[0]) return;
            if (c[0]._czOrig == null) c[0]._czOrig = c[0].innerHTML;
            c[0].textContent = c[1];
        });
        var curve = a.curve || [];
        if (!curve.length) {
            if (flux) empty(flux, a.refusal || 'Not drawn.', root, token);
            if (time) empty(time, a.refusal || 'Not drawn.', root, token);
            return Promise.resolve();
        }
        var xs = curve.map(function (c) { return c.amplitude_v; });
        var marker = { x: [p.x], name: 'operating point', mode: 'markers', type: 'scatter', _czOperating: p,
                       marker: { color: C.q[0], size: 13, symbol: 'diamond', line: { width: 1, color: C.q[0] } } };
        var qubitLines = [], qubitLabels = [];
        (d.qubits || []).forEach(function (q, k) {
            var f = q.summary && q.summary.f01_hz;
            if (f == null) return;
            qubitLines.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: f / 1e9, y1: f / 1e9,
                              line: { color: C.q[k] || C.res, width: 1, dash: 'dash' }, layer: 'below' });
            qubitLabels.push({ xref: 'paper', x: 1, xanchor: 'left', xshift: 8, yref: 'y', y: f / 1e9, showarrow: false,
                               text: q.name + ' idle', font: { size: 10, color: C.q[k] || C.res } });
        });
        var fluxTraces = [{ type: 'scatter', mode: 'lines', x: xs, y: curve.map(function (c) { return c.coupler_frequency_hz / 1e9; }),
                            name: 'coupler (stored model)', line: { color: C.muted, width: 2 } },
                          Object.assign({}, marker, { y: [p.y] })];
        var zz = curve.map(function (c) { return c.zz_hz == null ? null : c.zz_hz / 1e6; });
        var timeTraces = [{ type: 'scatter', mode: 'lines', x: xs, y: zz, name: 'ZZ (diagonalised)', connectgaps: false,
                            line: { color: C.q[0], width: 3 } },
                          Object.assign({}, marker, { y: [p.zz_hz == null ? null : p.zz_hz / 1e6] })];
        var common = { margin: { l: 64, r: 120, t: 24, b: 90 }, showlegend: true, hovermode: 'closest',
                       legend: { orientation: 'h', y: -0.32, x: 0, font: { size: 11 } } };
        var LF = layout(Object.assign({}, common, { xaxis: { title: { text: 'coupler amplitude (V)' } },
                                                    yaxis: { title: { text: 'frequency (GHz)' } },
                                                    shapes: qubitLines, annotations: qubitLabels }));
        var LT = layout(Object.assign({}, common, { xaxis: { title: { text: 'coupler amplitude (V)' } },
                                                    yaxis: { title: { text: 'ZZ (MHz)' } } }));
        [[flux, a.refusal], [time, a.refusal]].forEach(function (pair) {
            if (pair[0]) { pair[0].setAttribute('data-a4', '1'); pair[0].setAttribute('data-notes', pair[1] || ''); }
        });
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
            var still = function () { return active(root, token); };
            return Promise.all([
                flux ? Promise.resolve(window._plotlyRender(flux, fluxTraces, LF, config(), still)).then(function () { stampPoints(flux); }) : null,
                time ? Promise.resolve(window._plotlyRender(time, timeTraces, LT, config(), still)).then(function () { stampPoints(time); }) : null
            ]);
        });
    }

    function fillOps(root, d) {
        var sel = root.querySelector('#czp-op');
        if (!sel) return;
        sel.innerHTML = (d.pair_ops || []).map(function (o) {
            return '<option value="' + esc(o.path) + '"' + (o.path === d.row.path ? ' selected' : '') + '>' + esc(o.operation) + '</option>';
        }).join('');
    }

    function failed(root, op, text, token) {
        root.setAttribute('data-failed', op);
        var match = root.querySelector('#czp-match'); if (match) match.textContent = text;
        setStatus(root, ['Not drawn: ' + text]);
        ['#czp-flux', '#czp-time'].forEach(function (s) { var e = root.querySelector(s); if (e) empty(e, 'Not drawn.', root, token); });
        ['#czp-flux-caveat', '#czp-time-caveat'].forEach(function (s) { var e = root.querySelector(s); if (e) { e.textContent = ''; e.hidden = true; } });
        var chip = root.querySelector('#czp-stale-chip');
        if (chip) chip.hidden = true;
        // fix3 P2-4: nothing of the previous operation stays under the failed one's title
        var ul = root.querySelector('#czp-notes'), sum = root.querySelector('#czp-notes-summary'), card = root.querySelector('#czp-card');
        if (ul) ul.innerHTML = '';
        if (sum) sum.textContent = 'Notes — none';
        if (card) card.innerHTML = '';
        root._czLast = null;
    }

    function load(root, op) {
        var my = ++seq;
        root.setAttribute('data-selected', op);
        root.setAttribute('data-loading', '1');
        var match = root.querySelector('#czp-match');
        if (match) { match.textContent = 'Solving amplitude…'; ['target-detuning-hz', 'amplitude-v', 'stored-amplitude-v', 'difference-v', 'tier', 'line'].forEach(function (k) { match.removeAttribute('data-' + k); }); }
        root.removeAttribute('data-failed');
        Array.prototype.forEach.call(root.querySelectorAll('.czp-row'), function (tr) {
            var on = tr.getAttribute('data-op') === op;
            tr.classList.toggle('czp-row-selected', on);
            tr.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (window.CzMap) window.CzMap.select(root, op);
        var title = root.querySelector('#czp-title');
        var done = fetch('/cz-plan/data?op=' + encodeURIComponent(op), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (my !== seq || !document.body.contains(root)) return;
                root.removeAttribute('data-loading');
                if (!d.ok) {
                    if (title) title.textContent = op;
                    failed(root, op, (d.error || 'failed') + ' — the chip may have changed since this table was drawn; reload the page to refresh it.', my);
                    var card = root.querySelector('#czp-card'); if (card) card.innerHTML = '';
                    return;
                }
                if (title) title.textContent = d.row.pair + '  ·  ' + d.row.operation;
                root._czLast = { d: d, token: my };
                renderNotes(root, d);
                fillOps(root, d);
                renderCard(root, d);
                var mode = d.calibration && d.calibration.mode;
                if (mode === 'building' || mode === 'preparing') recheck(root, op, my, 0);
                Array.prototype.forEach.call(root.querySelectorAll('.czp-fig figcaption'), function (cap) {
                    if (cap._czOrig != null && !d.row.a4) { cap.innerHTML = cap._czOrig; cap._czOrig = null; }
                });
                ['#czp-flux', '#czp-time'].forEach(function (sel) { var e = root.querySelector(sel); if (e && !d.row.a4) e.removeAttribute('data-a4'); });
                var draws = d.row.a4 ? drawA4(root, d, my) : Promise.all([drawFlux(root, d, my), drawTime(root, d, my)]);
                return draws.then(function () {
                    if (my === seq) root.setAttribute('data-rendered', op);
                });
            })
            .catch(function (e) {
                if (my !== seq) return;
                root.removeAttribute('data-loading');
                failed(root, op, 'could not load: ' + (e && e.message || e), my);
            });
        try {
            var u = new URL(window.location.href);
            if (u.pathname === '/cz-plan') {
                u.searchParams.set('op', op);
                history.replaceState(history.state, '', u.pathname + u.search);
            }
        } catch (e) { /* no URL support -- the page still works */ }
        return done;
    }

    /* The chip's change history was still being built when the operation loaded: ask again
       and refresh only what it feeds (the staleness chip, the notes box, the card) while the
       same selection is shown -- the figures do not depend on it. */
    function recheck(root, op, token, tries) {
        if (tries >= RETRY_N) {
            // fix3 P2-5: the budget is spent -- the card says so instead of "being built" forever
            var last = root._czLast;
            if (!last || !active(root, token)) return;
            var d = JSON.parse(JSON.stringify(last.d));
            d.calibration = Object.assign({}, d.calibration, { mode: 'gave_up', note: GAVE_UP });
            renderCard(root, d);
            root.setAttribute('data-history-gave-up', '1');
            return;
        }
        setTimeout(function () {
            if (!active(root, token)) return;
            fetch('/cz-plan/data?op=' + encodeURIComponent(op), { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!active(root, token) || !d.ok) return;
                    var mode = d.calibration && d.calibration.mode;
                    if (mode === 'building' || mode === 'preparing') { recheck(root, op, token, tries + 1); return; }
                    root._czLast = { d: d, token: token };
                    renderNotes(root, d);
                    renderCard(root, d);
                })
                .catch(function () { /* the next selection asks again */ });
        }, 3000);
    }

    /* The loss answer arrived or the loss scale changed: redraw the flux figure of the shown
       operation from what the page holds (no request; a newer selection wins). */
    function redrawFlux(root) {
        var last = root._czLast;
        if (!last || !active(root, last.token)) return Promise.resolve();
        return drawFlux(root, last.d, last.token);
    }

    // Theme-specific marks and label backings must follow a toggle as well as the axes.
    new MutationObserver(function (changes) {
        if (!changes.some(function (c) { return c.attributeName === 'data-theme'; })) return;
        setTimeout(function () {
            var root = document.getElementById('czp-root'), last = root && root._czLast;
            if (window.CzMap) window.CzMap.paint(root);
            if (last && active(root, last.token)) {
                drawFlux(root, last.d, last.token);
                drawTime(root, last.d, last.token);
            }
        }, 0);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    /* The table's "Last set" column: one ledger read for every operation. While the chip's
       history is still being built, say so and ask again. */
    function loadHistory(root, tries) {
        var my = ++hseq, note = root.querySelector('#czp-history-note');
        return fetch('/cz-plan/history', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (h) {
                if (my !== hseq || !document.body.contains(root)) return;
                if (h.mode === 'building' || h.mode === 'preparing') {
                    if ((tries || 0) < RETRY_N) {
                        if (note) note.textContent = 'This chip\'s change history is being prepared; the Last set column fills in when it is ready.';
                        setTimeout(function () { loadHistory(root, (tries || 0) + 1); }, RETRY_MS);
                    } else if (note) {
                        note.textContent = GAVE_UP;       // fix3 P2-5: a final line, not "fills in" forever
                    }
                    return;
                }
                if (note) {
                    note.textContent = h.note || (h.mode === 'ledger' ? '' : 'This chip has no change history, so when each operation was set is unknown.');
                    if (h.note && h.link && h.link.url === '/hub/link-folder') {
                        note.insertAdjacentHTML('beforeend', ' <button type="button" class="outline btn-sm" hx-get="/hub/link-folder" hx-target="#hub-folder-body" hx-swap="innerHTML" onclick="HubFolders.open(this)">Link the data folder</button>');
                        if (window.htmx) window.htmx.process(note);
                    }
                }
                Array.prototype.forEach.call(root.querySelectorAll('td[data-last]'), function (td) {
                    var o = (h.ops || {})[td.getAttribute('data-last')];
                    var badge = td.parentElement.querySelector('.czp-no-quad');
                    if (badge) { if (!badge._reason) badge._reason = badge.title; badge.title = badge._reason + '; ' + zoneText((o && o.note) || h.note || 'setting time unknown'); }
                    if (!o || !o.last_set) {
                        // compact in the table; what is known (first record, a value that differs) on hover
                        var first = !!(o && o.note && /first recorded/.test(o.note));
                        td.innerHTML = '<span class="muted" title="' + esc(td.getAttribute('data-last') + '; ' + zoneText((o && o.note) || h.note || 'setting time unknown')) + '">-</span>';
                        return;
                    }
                    // fix3 P1-2: a run that only carried the value first is not named as its writer
                var saved = o.last_set.provenance === 'run_saved' || o.last_set.provenance === 'run_uncertain_chip';
                td.innerHTML = '<span class="czp-nowrap" title="' + esc(td.getAttribute('data-last') + '; ' + setWords(o.last_set, false) + '; ' + when(o.last_set) + '; ' + (o.warnings || []).length + ' newer; ' + (o.warnings || []).map(function (w) { return w.text; }).join('; ')) + '">' + esc((when(o.last_set).match(/\d{4}-\d\d-\d\d/) || ['-'])[0]) + '</span>';
                });
                root.setAttribute('data-history', h.mode || '');
            })
            .catch(function () { if (note) note.textContent = 'The change history could not be read.'; });
    }

    function revealFigures(root) {
        var detail = root.querySelector('.czp-detail');
        if (detail) detail.hidden = false;
        var t = detail || root.querySelector('#czp-title');
        if (!t || !t.getBoundingClientRect || typeof t.scrollIntoView !== 'function') return;
        var r = t.getBoundingClientRect(), h = window.innerHeight || document.documentElement.clientHeight;
        if (detail || r.top < 0 || r.top > h * 0.5) {
            try { t.scrollIntoView({ block: 'start' }); } catch (e) { t.scrollIntoView(true); }
        }
    }

    /* v1.5.1 (r6, D4a P2-5): a table wider than its wrap scrolls inside it with pair and
       operation held in view; the operation sticks just right of the pair column, whose drawn
       width follows the pair names and the UI scale. */
    function stickOperation(table) {
        var pair = table && table.querySelector('th.czp-stick');
        if (!pair) return;
        var wrap = table.parentElement, head = table.tHead;
        if (!wrap || !head) return;
        var floating = document.createElement('div'), copy = document.createElement('table');
        floating.className = 'czp-float-head';
        floating.setAttribute('aria-hidden', 'true');
        floating.hidden = true;
        copy.className = table.className;
        floating.appendChild(copy);
        wrap.parentElement.appendChild(floating);
        var pane = wrap.parentElement;
        while (pane && !/(auto|scroll|overlay)/.test(window.getComputedStyle(pane).overflowY)) pane = pane.parentElement;
        var frame = 0, stopped = false, resizeObserver, headObserver, removalObserver;
        function teardown() {
            if (stopped) return;
            stopped = true;
            if (frame) window.cancelAnimationFrame(frame);
            if (resizeObserver) resizeObserver.disconnect();
            if (headObserver) headObserver.disconnect();
            if (removalObserver) removalObserver.disconnect();
            document.removeEventListener('scroll', schedule, true);
            window.removeEventListener('resize', sync);
            wrap.removeEventListener('scroll', horizontal);
            floating.removeEventListener('click', forward);
            floating.remove();
        }
        function alive() {
            if (!document.contains(table)) teardown();
            return !stopped;
        }
        function horizontal() {
            if (!alive()) return;
            var x = wrap.scrollLeft;
            wrap.classList.toggle('czp-scrolled-x', x > 0);
            copy.style.transform = 'translateX(' + (-x) + 'px)';
            Array.prototype.forEach.call(copy.querySelectorAll('.czp-stick, .czp-stick-op'), function (cell) {
                cell.style.transform = 'translateX(' + x + 'px)';
            });
        }
        function update() {
            frame = 0;
            if (!alive()) return;
            // Let computed style resolve rem, calc() and other CSS lengths to pixels.
            floating.style.top = window.getComputedStyle(document.documentElement).getPropertyValue('--topbar-height').trim() || '0px';
            var bar = parseFloat(window.getComputedStyle(floating).top) || 0;
            var top = Math.max(pane ? pane.getBoundingClientRect().top : 0, bar);
            var box = wrap.getBoundingClientRect();
            floating.style.top = top + 'px';
            floating.style.left = box.left + 'px';
            floating.style.width = wrap.clientWidth + 'px';
            // Measure while displayed: a hidden clone has no layout height.
            floating.hidden = false;
            var height = copy.getBoundingClientRect().height;
            floating.hidden = wrap.classList.contains('czp-wrap-fits') || !(head.getBoundingClientRect().bottom < top &&
                table.getBoundingClientRect().bottom > top + height);
            horizontal();
        }
        function schedule() {
            if (alive() && !frame) frame = window.requestAnimationFrame(update);
        }
        function sync() {
            if (!alive()) return;
            var pairWidth = pair.getBoundingClientRect().width + 'px';
            table.style.setProperty('--czp-stick-op', pairWidth);
            copy.style.setProperty('--czp-stick-op', pairWidth);
            table.style.setProperty('--czp-head-h', head.getBoundingClientRect().height + 'px');
            var style = window.getComputedStyle(wrap);
            var content = wrap.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
            var width = table.getBoundingClientRect().width;
            wrap.classList.toggle('czp-wrap-fits', width <= content);
            copy.style.width = width + 'px';
            var originals = head.querySelectorAll('th');
            Array.prototype.forEach.call(copy.querySelectorAll('th'), function (cell, i) {
                var size = originals[i].getBoundingClientRect().width + 'px';
                cell.style.width = size;
                cell.style.minWidth = size;
                cell.style.maxWidth = size;
            });
            horizontal();
            schedule();
        }
        function rebuild() {
            if (!alive()) return;
            var clone = head.cloneNode(true);
            clone.removeAttribute('id');
            Array.prototype.forEach.call(clone.querySelectorAll('[id]'), function (el) { el.removeAttribute('id'); });
            Array.prototype.forEach.call(clone.querySelectorAll('[data-sort-key]'), function (el) {
                el.setAttribute('data-czp-float-key', el.getAttribute('data-sort-key'));
                el.removeAttribute('data-sort-key');
            });
            Array.prototype.forEach.call(clone.querySelectorAll('button'), function (button) { button.tabIndex = -1; });
            copy.replaceChildren(clone);
            sync();
        }
        function forward(ev) {
            if (!alive()) return;
            var button = ev.target.closest && ev.target.closest('button.czp-sort');
            var cell = button && button.closest('th[data-czp-float-key]');
            if (!cell) return;
            var key = cell.getAttribute('data-czp-float-key');
            var original = Array.prototype.find.call(head.querySelectorAll('th[data-sort-key]'), function (th) {
                return th.getAttribute('data-sort-key') === key;
            });
            if (original && original.querySelector('button')) original.querySelector('button').click();
            rebuild();
        }
        rebuild();
        if (window.ResizeObserver) {
            resizeObserver = new window.ResizeObserver(sync);
            // the pair column by its border box: its padding follows the UI scale
            new window.ResizeObserver(sync).observe(pair, { box: 'border-box' });
            [wrap, head, table].forEach(function (el) { resizeObserver.observe(el); });
        }
        headObserver = new MutationObserver(rebuild);
        headObserver.observe(head, { attributes: true, subtree: true, childList: true });
        removalObserver = new MutationObserver(alive);
        removalObserver.observe(document.documentElement, { childList: true, subtree: true });
        document.addEventListener('scroll', schedule, { passive: true, capture: true });
        window.addEventListener('resize', sync);
        wrap.addEventListener('scroll', horizontal, { passive: true });
        floating.addEventListener('click', forward);
    }

    /* A date and its time stay on one line in the Last set column (a narrow cell broke the
       date at its hyphen); the text itself is unchanged. */
    function stampCell(text) {
        return esc(text).replace(/\d{4}-\d\d-\d\d \d\d:\d\d(?::\d\d)?/, '<span class="czp-nowrap">$&</span>');
    }

    /* The table follows the map's Operation picker: a named operation lists only its rows,
       "cz per pair" lists every row. The selected row stays visible. */
    function groupRows(root) {
        var previous = null;
        root.querySelectorAll('.czp-row').forEach(function (tr) {
            if (tr.hidden) return;
            var pair = tr.getAttribute('data-pair');
            if (pair == null) return;
            tr.setAttribute('data-pair-start', pair === previous ? '0' : '1');
            previous = pair;
        });
    }

    function filterRows(root, op) {
        var rows = root.querySelectorAll('.czp-row'), shown = 0, sel = root.getAttribute('data-selected');
        Array.prototype.forEach.call(rows, function (tr) {
            var on = !op || tr.getAttribute('data-opname') === op;
            tr.hidden = !on;
            if (on) shown++;
        });
        groupRows(root);
        var note = root.querySelector('#czp-filter-note');
        if (note) {
            note.hidden = !op;
            note.textContent = op ? 'Showing ' + shown + ' of ' + rows.length + ' operations: ' + op +
                ' only, as on the map. Pick "cz per pair" to list all.' : '';
        }
        if (op && sel) {
            var cur = root.querySelector('.czp-row[data-op="' + (window.CSS && CSS.escape ? CSS.escape(sel) : sel) + '"]');
            if (cur && cur.hidden) {
                var first = Array.prototype.find.call(rows, function (tr) { return !tr.hidden; });
                if (first) load(root, first.getAttribute('data-op'));
            }
        }
    }

    /* Click a header to sort by it; again to reverse. Rows without a value stay last. */
    function sortBy(root, th) {
        var table = root.querySelector('#czp-table'), tbody = table && table.tBodies[0];
        if (!tbody) return;
        var key = th.getAttribute('data-sort-key'), num = th.hasAttribute('data-sort-num');
        var dir = th.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
        Array.prototype.forEach.call(table.querySelectorAll('th[data-sort-key]'), function (h) { h.setAttribute('aria-sort', 'none'); });
        th.setAttribute('aria-sort', dir > 0 ? 'ascending' : 'descending');
        function val(tr) {
            if (key === 'pair') return tr.getAttribute('data-pair') || '';
            if (key === 'op') return tr.getAttribute('data-opname') || '';
            var s = tr.getAttribute('data-s-' + key);
            return s === null || s === '' ? null : parseFloat(s);
        }
        var nat = function (a, b) { return String(a).localeCompare(String(b), undefined, { numeric: true }); };
        var rows = Array.prototype.slice.call(tbody.rows).map(function (tr, i) { return { tr: tr, v: val(tr), i: i }; });
        rows.sort(function (a, b) {
            var na = a.v === null || (num && isNaN(a.v)), nb = b.v === null || (num && isNaN(b.v));
            if (na || nb) return na === nb ? a.i - b.i : (na ? 1 : -1);
            var c = num ? a.v - b.v : nat(a.v, b.v);
            return c ? c * dir : a.i - b.i;
        });
        rows.forEach(function (r) { tbody.appendChild(r.tr); });
        groupRows(root);
    }

    function pairOfRow(tr) {
        var m = /^qubit_pairs\.(.+)\.macros\./.exec(tr ? tr.getAttribute('data-op') || '' : '');
        return m ? m[1] : null;
    }

    function mount() {
        var root = document.getElementById('czp-root');
        if (!root || root._czMounted) return;
        root._czMounted = true;
        var host = root.querySelector('.czp-plots');
        if (host && window.PlotHost) window.PlotHost.observe(host);
        var mapBox = root.querySelector('.czp-map-box');
        if (mapBox && window.PlotHost) window.PlotHost.observe(mapBox);
        var listed = root.querySelector('#czp-table-op'), mapOp = root.querySelector('#czp-map-op');
        function syncListed() {
            if (listed && mapOp) {
                listed.replaceChildren.apply(listed, Array.prototype.map.call(mapOp.options, function (o) { return o.cloneNode(true); }));
                listed.value = mapOp.value;
            }
            filterRows(root, mapOp ? mapOp.value : '');
        }
        root.addEventListener('czp:mapop', syncListed);
        if (listed && mapOp) listed.addEventListener('change', function () {
            mapOp.value = listed.value;
            mapOp.dispatchEvent(new Event('change', { bubbles: true }));
        });
        syncListed();
        root.addEventListener('click', function (ev) {
            var sortTh = ev.target.closest && ev.target.closest('th[data-sort-key]');
            if (sortTh && root.contains(sortTh)) { sortBy(root, sortTh); return; }
            var link = ev.target.closest && ev.target.closest('.czp-notes-link, #czp-stale-chip');
            if (link && root.contains(link)) { ev.preventDefault(); openNotes(root); return; }
            var tr = ev.target.closest && ev.target.closest('.czp-row');
            if (tr && root.contains(tr)) { load(root, tr.getAttribute('data-op')); revealFigures(root); }
        });
        root.addEventListener('keydown', function (ev) {
            var tr = ev.target.closest && ev.target.closest('.czp-row');
            if (!tr) return;
            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); load(root, tr.getAttribute('data-op')); revealFigures(root); }
        });
        // a table row and its pair on the map are one surface: hovering the row lights the pair
        var table = root.querySelector('#czp-table');
        stickOperation(table);
        if (table && window.CzMap) {
            table.addEventListener('mouseover', function (ev) {
                var tr = ev.target.closest && ev.target.closest('.czp-row');
                window.CzMap.highlight(root, tr ? pairOfRow(tr) : null);
            });
            table.addEventListener('mouseleave', function () { window.CzMap.highlight(root, null); });
        }
        var box = root.querySelector('#czp-notes-box');
        if (box) box.addEventListener('toggle', function () { rememberNotes(box.open); });
        root.addEventListener('czp:open', function (ev) {
            var op = ev.detail && ev.detail.op;
            if (op) { load(root, op); revealFigures(root); }
        });
        root.addEventListener('czp:loss', function () { redrawFlux(root); });
        root.addEventListener('czp:scale', function () { redrawFlux(root); });
        var sel = root.querySelector('#czp-op');
        if (sel) sel.addEventListener('change', function () { load(root, sel.value); });
        root.querySelectorAll('.czp-coupler-form').forEach(function (form) {
            form.onsubmit = function (event) { event.preventDefault(); stageCoupler(form); };
        });
        var couplerToggle = root.querySelector('#czp-include-coupler');
        if (couplerToggle) couplerToggle.onchange = function () { couplerView(root); };
        var first = root.getAttribute('data-selected');
        if (first) load(root, first).then(function () {
            if (new URLSearchParams(window.location.search).has('pair')) revealFigures(root);
        });
        if (window.CzMap) window.CzMap.mount(root);
        loadHistory(root, 0);
    }

    /* The working copy moved under the open page: re-GET the page and redraw the operation the
       reader has selected (the numbers were computed from the old values). */
    function refresh() {
        var root = document.getElementById('czp-root');
        if (!root) return;
        var want = root.getAttribute('data-selected') || '';
        return fetch('/cz-plan' + (want ? '?op=' + encodeURIComponent(want) : ''), { headers: { 'HX-Request': 'true' } })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var box = document.createElement('div');
                box.innerHTML = html;
                var fresh = box.querySelector('#czp-root');
                if (!fresh || !document.body.contains(root)) return;
                if (window.PlotHost) window.PlotHost.purgeWithin(root);
                root.parentNode.replaceChild(fresh, root);
                mount();
            });
    }

    window.CzPlan = { mount: mount, refresh: refresh, _esc: esc,
                      drawMap: function (root) { return window.CzMap ? window.CzMap.draw(root) : Promise.resolve(); } };
    document.addEventListener('DOMContentLoaded', mount);
    document.addEventListener('htmx:afterSwap', mount);
    document.addEventListener('sm:wc-moved', function () {
        var root = document.getElementById('czp-root');
        if (root && root._czMounted) refresh();
    });
    if (document.readyState !== 'loading') mount();
})();
