/* Chip Status > CZ gate plan (bundle 'czplan').
 *
 * Draws what /cz-plan/data computed -- the physics, the estimate tiers and every refusal live
 * in core/cz_gate_plan.py. This file only: picks the operation, asks for its data, shows the
 * notes, draws (a) frequency vs flux amplitude, (b) frequency during the gate from the
 * operation's own waveforms, (c) the operation's card, and fills the table's "Last set" column
 * from /cz-plan/history. A response that arrives after a newer request is dropped (seq token).
 */
(function () {
    'use strict';

    var seq = 0, hseq = 0;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function colors() {
        var P = (window.PlotTheme && window.PlotTheme.palette()) || ['#4f9cf9', '#f97316', '#22c55e', '#a855f7'];
        var muted = getComputedStyle(document.documentElement).getPropertyValue('--pico-muted-color').trim() || '#8a8a8a';
        return { q: [P[0], P[1]], plan: P[2] || '#22c55e', res: P[3] || '#a855f7', muted: muted };
    }
    function layout(over) {
        var base = window.PlotTheme ? window.PlotTheme.houseLayout({}) : {};
        var L = window.PlotTheme ? window.PlotTheme.houseLayout(over) : over;
        ['xaxis', 'yaxis', 'yaxis2'].forEach(function (k) {
            if (over[k]) L[k] = Object.assign({}, base[k] || base.yaxis || {}, over[k]);
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
    function when(p) {
        if (!p || !p.t) return 'unknown';
        var t = String(p.t).replace('T', ' ').replace(/\.\d+/, '').replace('Z', ' UTC');
        return (p.run_id != null ? 'run #' + p.run_id + ' · ' : '') + t;
    }

    function active(root, token) {
        return token === seq && document.body.contains(root);
    }

    function empty(el, text, root, token) {
        if (!active(root, token)) return;
        if (window.PlotHost) window.PlotHost.purgeWithin(el);
        el.removeAttribute('data-dwell');
        el.removeAttribute('data-notes');
        el.innerHTML = '<p class="muted zline-empty">' + esc(text) + '</p>';
    }

    function renderNotes(root, d) {
        var ul = root.querySelector('#czp-notes');
        if (!ul) return;
        var items = (d.notes || []).map(function (t) { return { level: 'warn', text: t }; });
        (d.qubits || []).forEach(function (q) {
            if (!q.summary) return;
            (q.summary.notes || []).forEach(function (t) { items.push({ level: 'info', text: q.name + ': ' + t }); });
        });
        (d.calibration.warnings || []).forEach(function (w) {
            items.push({ level: 'warn', text: w.text + ' (changed ' + when(w) + ', after this operation was set)' });
        });
        ul.innerHTML = items.map(function (n) {
            var cls = n.level === 'warn' ? 'zline-note-warn' : 'zline-note-info';
            return '<li class="' + cls + '"><strong>' + (n.level === 'warn' ? 'check' : 'note') + '</strong> ' + esc(n.text) + '</li>';
        }).join('');
    }

    /* (a) f vs flux amplitude: each qubit's curve(s), its idle point, an arrow to the selected
       operation's operating point, the pair's other operations faintly, the resonance levels at
       the operating height, the planned interaction point and the defect bands. */
    function drawFlux(root, d, token) {
        var el = root.querySelector('#czp-flux');
        if (!el) return Promise.resolve();
        var C = colors(), traces = [], shapes = [], notes = [];
        var lines = d.row.lines, pts = lines.map(function (ln) { return ln.point; });
        d.qubits.forEach(function (q, k) {
            if (!q.summary || !q.curve) return;
            var col = C.q[k], c = q.curve, s = q.summary;
            if (c.model_hz) {
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.model_hz.map(ghz), name: q.name + ' · ' + s.model_kind,
                    line: { color: col, width: 2.2 }, hovertemplate: 'amplitude: %{x:.4f} V<br>' + esc(q.name) + ': %{y:.4f} GHz<extra>' + esc(s.model_kind) + '</extra>' });
            }
            if (c.quad_hz.some(function (v) { return v != null; })) {
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.quad_hz.map(ghz), name: q.name + ' · quad-term estimate',
                    line: { color: col, width: 1.4, dash: c.model_hz ? 'dash' : 'solid' },
                    hovertemplate: 'amplitude: %{x:.4f} V<br>' + esc(q.name) + ': %{y:.4f} GHz<extra>quad-term estimate</extra>' });
            }
            traces.push({ type: 'scatter', mode: 'markers', x: [0], y: [ghz(s.f01_hz)], name: q.name + ' idle', showlegend: false,
                marker: { color: col, size: 11, symbol: 'circle-open', line: { width: 2 } },
                hovertemplate: esc(q.name) + ' idle f_01: %{y:.4f} GHz<extra></extra>' });
            (q.tls || []).forEach(function (b) {
                shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(b.lo_hz), y1: ghz(b.hi_hz),
                    fillcolor: col, opacity: 0.18, line: { width: 0 }, layer: 'below' });
            });
            if ((q.tls || []).length) notes.push(q.name + ': ' + q.tls.length + ' measured defect band' + (q.tls.length === 1 ? '' : 's') + ' shaded');
        });
        d.pair_ops.forEach(function (op) {
            if (op.path === d.row.path) return;
            op.points.forEach(function (p, k) {
                if (p.frequency_hz == null || !p.amplitude_v) return;
                traces.push({ type: 'scatter', mode: 'markers', x: [p.amplitude_v], y: [ghz(p.frequency_hz)], showlegend: false,
                    marker: { color: C.q[k], size: 7, opacity: 0.35 },
                    hovertemplate: esc(op.operation) + '<br>amplitude: %{x:.4f} V<br>%{y:.4f} GHz<extra></extra>' });
            });
        });
        var annotations = [];
        lines.forEach(function (ln, k) {
            var p = ln.point, q = d.qubits[k];
            if (!ln.moves || p.frequency_hz == null || !q.summary) return;
            traces.push({ type: 'scatter', mode: 'markers', x: [ln.amplitude_v], y: [ghz(p.frequency_hz)], name: q.name + ' at ' + d.row.operation,
                showlegend: false, marker: { color: C.q[k], size: 12, symbol: 'diamond' },
                hovertemplate: esc(q.name) + ' at ' + esc(d.row.operation) + '<br>amplitude: %{x:.4f} V<br>%{y:.4f} GHz<extra>' + esc(p.kind) + '</extra>' });
            annotations.push({ x: ln.amplitude_v, y: ghz(p.frequency_hz), ax: 0, ay: ghz(q.summary.f01_hz), axref: 'x', ayref: 'y',
                xref: 'x', yref: 'y', showarrow: true, arrowhead: 3, arrowsize: 1.2, arrowwidth: 1.6, arrowcolor: C.q[k], text: '' });
        });
        // resonance levels for the moving control at the target's operating height
        var fc = pts[0].frequency_hz, ft = pts[1].frequency_hz;
        var ac = d.qubits[0].summary && d.qubits[0].summary.anharmonicity_hz, at = d.qubits[1].summary && d.qubits[1].summary.anharmonicity_hz;
        if (ft != null) {
            [['|11>-|20>', ac == null ? null : ft + Math.abs(ac)], ['iSWAP', ft], ['|11>-|02>', at == null ? null : ft - Math.abs(at)]].forEach(function (lv) {
                if (lv[1] == null) return;
                shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(lv[1]), y1: ghz(lv[1]),
                    line: { color: C.res, width: 1, dash: 'dot' } });
                annotations.push({ xref: 'paper', x: 1, xanchor: 'right', yref: 'y', y: ghz(lv[1]), yanchor: 'bottom', showarrow: false,
                    text: 'control on ' + lv[0], font: { size: 10, color: C.res } });
            });
        }
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
        var L = layout({
            margin: { l: 64, r: 16, t: 10, b: 90 }, showlegend: true,
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 11 } },
            xaxis: { title: { text: 'flux amplitude (V)' }, zeroline: true },
            yaxis: { title: { text: 'frequency (GHz)' } },
            shapes: shapes, annotations: annotations, hovermode: 'closest'
        });
        el.setAttribute('data-notes', notes.join('; '));
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
            return window._plotlyRender(el, traces, L, config());
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
            empty(el, 'Not drawn: SM cannot draw the waveform of a moving qubit (see the notes above).', root, token);
            return Promise.resolve();
        }
        var tt = []; for (var i = 0; i < n; i++) tt.push(i);
        var series = d.qubits.map(function (q, k) {
            var t = traj[k], f01 = q.summary ? q.summary.f01_hz : null, out = [];
            for (var i = 0; i < n; i++) out.push(t ? (i < t.frequency_hz.length ? t.frequency_hz[i] :
                (t.playback_known === false ? null : f01)) : (d.row.lines[k].moves ? null : f01));
            return out;
        });
        var traces = [];
        d.qubits.forEach(function (q, k) {
            traces.push({ type: 'scatter', mode: 'lines', x: tt, y: series[k].map(ghz), name: q.name, line: { color: C.q[k], width: 2, shape: 'hv' },
                hovertemplate: 't = %{x} ns<br>' + esc(q.name) + ': %{y:.4f} GHz<extra></extra>' });
            var t = traj[k];
            if (t && t.model_hz && t.quad_hz) {
                traces.push({ type: 'scatter', mode: 'lines', x: tt.slice(0, t.quad_hz.length), y: t.quad_hz.map(ghz), name: q.name + ' · quad-term estimate',
                    line: { color: C.q[k], width: 1, dash: 'dash', shape: 'hv' }, hovertemplate: 't = %{x} ns<br>%{y:.4f} GHz<extra>quad-term estimate</extra>' });
            }
        });
        var delta = series[0].map(function (fc, i) { var ft = series[1][i]; return fc == null || ft == null ? null : (fc - ft) / 1e6; });
        traces.push({ type: 'scatter', mode: 'lines', x: tt, y: delta, name: 'Δ = f_c − f_t', yaxis: 'y2', line: { color: C.muted, width: 1.8, shape: 'hv' },
            hovertemplate: 't = %{x} ns<br>Δ: %{y:.1f} MHz<extra></extra>' });
        var ac = d.qubits[0].summary && d.qubits[0].summary.anharmonicity_hz, at = d.qubits[1].summary && d.qubits[1].summary.anharmonicity_hz;
        var shapes = [], annotations = [];
        [['|11>-|20>', ac == null ? null : Math.abs(ac) / 1e6], ['iSWAP', 0], ['|11>-|02>', at == null ? null : -Math.abs(at) / 1e6]].forEach(function (lv) {
            if (lv[1] == null) return;
            shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: lv[1] - 5, y1: lv[1] + 5, fillcolor: C.res, opacity: 0.18, line: { width: 0 }, layer: 'below' });
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: lv[1], y1: lv[1], line: { color: C.res, width: 1, dash: 'dot' } });
            annotations.push({ xref: 'paper', x: 1, xanchor: 'right', yref: 'y2', y: lv[1], yanchor: 'bottom', showarrow: false, text: lv[0], font: { size: 10, color: C.res } });
        });
        // the time Delta(t) spends within 5 MHz of each resonance
        var dwell = [['|11>-|20>', ac == null ? null : Math.abs(ac) / 1e6], ['iSWAP', 0], ['|11>-|02>', at == null ? null : -Math.abs(at) / 1e6]].map(function (lv) {
            if (lv[1] == null) return null;
            var c = delta.filter(function (v) { return v != null && Math.abs(v - lv[1]) <= 5; }).length;
            return c ? lv[0] + ' ' + c + ' ns' : null;
        }).filter(Boolean);
        el.setAttribute('data-dwell', dwell.join('; '));
        var L = layout({
            margin: { l: 64, r: 16, t: 10, b: 90 }, showlegend: true,
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 11 } },
            xaxis: { title: { text: 'time (ns, 1 sample per ns)' } },
            yaxis: { title: { text: 'frequency (GHz)' }, domain: [0.42, 1] },
            yaxis2: { title: { text: 'Δ (MHz)' }, domain: [0, 0.34], anchor: 'x' },
            shapes: shapes, annotations: annotations, hovermode: 'x unified'
        });
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
            return window._plotlyRender(el, traces, L, config());
        });
    }

    /* (c) the operation's card. */
    function renderCard(root, d) {
        var el = root.querySelector('#czp-card');
        if (!el) return;
        var r = d.row, cal = d.calibration, h = [];
        h.push('<h4>' + esc(r.pair) + ' · ' + esc(r.operation) + ' <small class="muted">' + esc(r['class'] || '') + ' · ' + esc(r.kind) +
            (r.alias_of ? ' · alias of ' + esc(r.alias_of) : '') + '</small></h4>');
        h.push('<div class="czp-card-grid">');
        r.lines.forEach(function (ln, k) {
            var p = ln.point, q = d.qubits[k], s = q.summary || {};
            h.push('<div class="czp-card-q"><strong>' + esc(ln.role) + ' ' + esc(ln.name) + '</strong>' +
                (ln.moves ? ' <small class="muted">plays ' + esc((ln.pulse_path || '').split('.').pop()) + (ln.pulse_class ? ' (' + esc(ln.pulse_class) + ')' : '') + '</small>' : ' <small class="muted">does not move</small>') +
                '<dl>' +
                '<dt>Amplitude</dt><dd>' + fmtV(ln.amplitude_v) + '</dd>' +
                '<dt>Idle f_01</dt><dd>' + fmtGHz(s.f01_hz) + '</dd>' +
                '<dt>At the operation</dt><dd>' + fmtGHz(p.frequency_hz) + (p.kind ? ' <span class="czp-tier' + (/quad/.test(p.kind) ? ' czp-tier-quad' : '') + '">' + esc(p.kind) + '</span>' : '') +
                (p.reason ? '<br><span class="czp-warn-text">' + esc(p.reason) + '</span>' : '') + '</dd>' +
                (p.quad_hz != null && p.model_hz != null ? '<dt>Quad-term estimate</dt><dd>' + fmtGHz(p.quad_hz) + ' <small class="muted">(' + fmtMHz(p.error_hz) + ' from the model)</small></dd>' : '') +
                (p.validity ? '<dt>Validity</dt><dd>' + esc(p.validity) + (s.range && s.range.limit_hz ? ' <small class="muted">(limit ' + fmtMHz(s.range.limit_hz) + ', ' + esc(s.range.source || '') + ')</small>' : '') + '</dd>' : '') +
                '<dt>Direction</dt><dd>' + esc((s.frame || {}).reason || '—') + '</dd>' +
                (r.plan && r.plan.roles[ln.role] && r.plan.roles[ln.role].plan_v != null ? '<dt>Planned amplitude</dt><dd>' + fmtV(r.plan.roles[ln.role].plan_v) +
                    (r.plan.roles[ln.role].difference_v != null ? ' <small class="muted">(operation ' + (r.plan.roles[ln.role].difference_v >= 0 ? '+' : '') + (r.plan.roles[ln.role].difference_v * 1e3).toFixed(1) + ' mV)</small>' : '') + '</dd>' : '') +
                '<dt>Phase correction</dt><dd>' + (r.phases[ln.role] == null ? '—' : Number(r.phases[ln.role]).toFixed(4) + ' × 2π') + '</dd>' +
                '</dl></div>');
        });
        var fid = r.fidelity || {}, fl = [];
        ['InterleavedRB', 'StandardRB'].forEach(function (k) {
            if (fid[k]) fl.push(k + ' ' + Number(fid[k].value).toFixed(4) + (fid[k].load_id != null ? ' <small class="muted">(load id ' + esc(fid[k].load_id) + ')</small>' : ''));
        });
        var near = r.nearest;
        h.push('<div class="czp-card-q"><strong>Operation</strong><dl>' +
            '<dt>Δ at the operation</dt><dd>' + fmtMHz(r.detuning_hz, true) + ' <small class="muted">(idle ' + fmtMHz(r.idle_detuning_hz, true) + ')</small></dd>' +
            '<dt>Nearest resonance</dt><dd>' + (near ? esc(near.name) + ' ' + fmtMHz(near.distance_hz, true) + ' away' : '—') + '</dd>' +
            '<dt>Duration</dt><dd>' + (r.duration_ns == null ? 'unknown' : r.duration_ns + ' ns') +
            (r.duration_note ? ' <small class="muted">(' + esc(r.duration_note) + ')</small>' : '') + '</dd>' +
            (r.coupler_amplitude_v != null ? '<dt>Coupler pulse</dt><dd>' + fmtV(r.coupler_amplitude_v) + '</dd>' : '') +
            (r.plan ? '<dt>Planned ω_int</dt><dd>' + fmtGHz(r.plan.omega_int_hz) + (r.plan.path_ok === false ? ' <span class="czp-warn-text">path not ok</span>' : '') +
                (r.plan.reasons.length ? '<br><small class="muted">' + esc(r.plan.reasons.join(', ')) + '</small>' : '') + '</dd>' : '') +
            (r.interaction_frequency_hz ? '<dt>Stored interaction frequency</dt><dd>' + fmtGHz(r.interaction_frequency_hz) + '</dd>' : '') +
            '<dt>Fidelity</dt><dd>' + (fl.length ? fl.join('<br>') : '—') + '</dd>' +
            '<dt>Last set</dt><dd>' + (cal.last_set ? esc(when(cal.last_set)) + (cal.proven && cal.last_set.provenance === 'run_proven' ? ' <small class="muted">(written by that run)</small>' : ' <small class="muted">(' + esc(cal.last_set.provenance || 'writer not proven') + ')</small>') :
                '<span class="muted">unknown' + (cal.note ? ' — ' + esc(cal.note) : '') + '</span>') + '</dd>' +
            (cal.warnings.length ? '<dt>Staleness</dt><dd class="czp-warn-text">' + cal.warnings.map(function (w) { return esc(w.text); }).join('<br>') + '</dd>' : '') +
            '</dl></div>');
        h.push('</div>');
        el.innerHTML = h.join('');
    }

    /* The chip frequency map (cf. arXiv:2112.03708 Fig. 6c): one column per qubit on a shared
       frequency axis -- the idle frequency (dot), the stored population-loss defects (gray bars,
       length ~ peak loss), the safe intervals (faint band), and each pair's operating point as a
       short segment in each member's column (solid: fitted model, dashed: quad-term estimate),
       joined by a thin connector. Refused frequencies are listed under the figure, never drawn. */
    var mseq = 0;
    var MAP_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#17becf',
                      '#bcbd22', '#7f7f7f'];
    function mapColors() {
        var P = window.PlotTheme && window.PlotTheme.palette ? window.PlotTheme.palette() : null;
        return P && P.length >= 6 ? P.concat(MAP_COLORS.filter(function (c) { return P.indexOf(c) < 0; })) : MAP_COLORS;
    }
    function mapEmpty(el, text) {
        if (window.PlotHost) window.PlotHost.purgeWithin(el);
        el.innerHTML = '<p class="muted zline-empty">' + esc(text) + '</p>';
    }
    function drawMap(root) {
        var el = root.querySelector('#czp-map');
        if (!el) return Promise.resolve();
        var my = ++mseq;
        var opSel = root.querySelector('#czp-map-op'), ordSel = root.querySelector('#czp-map-order');
        var op = opSel ? opSel.value : '', order = ordSel ? ordSel.value : 'name';
        return fetch('/cz-plan/map' + (op ? '?op=' + encodeURIComponent(op) : ''), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (m) {
                if (my !== mseq || !document.body.contains(root)) return;
                if (!m.ok) { mapEmpty(el, m.error || 'The map could not be computed.'); return; }
                if (opSel && opSel.options.length <= 1) {
                    (m.operations || []).forEach(function (name) {
                        var o = document.createElement('option'); o.value = name; o.textContent = name; opSel.appendChild(o);
                    });
                }
                var qs = m.qubits.slice();
                if (order === 'f01') qs.sort(function (a, b) { return (a.f01_hz == null) - (b.f01_hz == null) || (a.f01_hz || 0) - (b.f01_hz || 0); });
                var col = {}; qs.forEach(function (q, i) { col[q.path] = i; });
                var C = colors(), P = mapColors(), traces = [], shapes = [];
                var ink = getComputedStyle(document.documentElement).getPropertyValue('--pico-color').trim() || '#222';
                qs.forEach(function (q, i) {
                    if (i) shapes.push({ type: 'line', xref: 'x', x0: i, x1: i, yref: 'paper', y0: 0, y1: 1,
                                         line: { color: C.muted, width: 0.6 }, layer: 'below' });
                    // safe intervals: a narrow strip at the column's right edge (a full-width band covers the column)
                    (q.safe || []).forEach(function (b) {
                        shapes.push({ type: 'rect', xref: 'x', x0: i + 0.93, x1: i + 0.98, yref: 'y', y0: ghz(b.lo_hz), y1: ghz(b.hi_hz),
                                      fillcolor: C.plan, opacity: 0.45, line: { width: 0 }, layer: 'below' });
                    });
                    (q.defects || []).forEach(function (b) {
                        var f = (b.lo_hz + b.hi_hz) / 2, w = 0.08 + 0.82 * Math.min(1, (b.peak_loss || 0) / 0.5);
                        traces.push({ type: 'scatter', mode: 'lines', x: [i + 0.05, i + 0.05 + w], y: [ghz(f), ghz(f)], showlegend: false,
                            line: { color: C.muted, width: 3 }, opacity: 0.75,
                            hovertemplate: esc(q.name) + ' defect %{y:.4f} GHz<br>FWHM ' + ((b.hi_hz - b.lo_hz) / 1e6).toFixed(1) + ' MHz' +
                                (b.peak_loss != null ? ', peak loss ' + (100 * b.peak_loss).toFixed(0) + ' %' : '') + '<extra></extra>' });
                    });
                });
                traces.push({ type: 'scatter', mode: 'markers', x: qs.map(function (q, i) { return i + 0.5; }),
                    y: qs.map(function (q) { return ghz(q.f01_hz); }), name: 'idle f_01',
                    marker: { color: ink, size: 9, symbol: 'circle' },
                    customdata: qs.map(function (q) { return q.name + (q.loss_data ? '' : ' (no population-loss data)') + '<br>' + q.direction; }),
                    hovertemplate: '%{customdata}<br>idle %{y:.4f} GHz<extra></extra>' });
                var pairs = [];
                m.segments.forEach(function (s) { if (pairs.indexOf(s.pair) < 0) pairs.push(s.pair); });
                pairs.forEach(function (pair, k) {
                    var color = P[k % P.length], segs = m.segments.filter(function (s) { return s.pair === pair; });
                    segs.forEach(function (s, j) {
                        var i = col[s.qubit_path];
                        if (i == null) return;
                        var quad = /quad/.test(s.kind || '');
                        traces.push({ type: 'scatter', mode: 'lines', x: [i + 0.12, i + 0.88], y: [ghz(s.frequency_hz), ghz(s.frequency_hz)],
                            name: pair, legendgroup: pair, showlegend: j === 0,
                            line: { color: color, width: s.moves ? 3.2 : 1.6, dash: quad ? 'dash' : 'solid' },
                            customdata: [s.path, s.path],
                            hovertemplate: esc(pair) + ' · ' + esc(s.operation) + '<br>' + esc(s.qubit) + (s.moves ? '' : ' (stays at idle)') +
                                ': %{y:.4f} GHz<br>' + esc(s.kind || '') + (s.validity && s.validity !== 'idle' ? ' · ' + esc(s.validity) : '') +
                                '<br>Δ ' + fmtMHz(s.detuning_hz, true) + (s.nearest ? ' · ' + esc(s.nearest.name) + ' ' + fmtMHz(s.nearest.distance_hz, true) : '') +
                                '<extra></extra>' });
                    });
                    // connectors help on a small chip; across 10+ pairs they cross the whole map
                    if (pairs.length <= 10 && segs.length === 2 && col[segs[0].qubit_path] != null && col[segs[1].qubit_path] != null) {
                        var a = segs[0], b = segs[1], ia = col[a.qubit_path], ib = col[b.qubit_path];
                        var left = ia <= ib ? a : b, right = ia <= ib ? b : a;
                        traces.push({ type: 'scatter', mode: 'lines', legendgroup: pair, showlegend: false, hoverinfo: 'skip',
                            x: [Math.min(ia, ib) + 0.88, Math.max(ia, ib) + 0.12], y: [ghz(left.frequency_hz), ghz(right.frequency_hz)],
                            line: { color: color, width: 1, dash: 'dot' }, opacity: 0.6 });
                    }
                    (m.planned || []).filter(function (p) { return p.pair === pair; }).forEach(function (p) {
                        var i = col[p.qubit_path];
                        if (i == null) return;
                        traces.push({ type: 'scatter', mode: 'markers', x: [i + 0.5], y: [ghz(p.frequency_hz)], legendgroup: pair, showlegend: false,
                            marker: { color: color, size: 11, symbol: 'diamond-open', line: { width: 2 } },
                            hovertemplate: esc(pair) + ' ' + esc(p.what) + ' (' + esc(p.qubit) + '): %{y:.4f} GHz<extra></extra>' });
                    });
                });
                var notes = [];
                if (m.refused.length) notes.push('Not drawn: ' + m.refused.map(function (r) {
                    return r.qubit + ' in ' + r.pair + ' ' + r.operation + ' (' + r.reason + ')'; }).join('; ') + '.');
                if (m.missing.length) notes.push('No ' + (m.op || 'such') + ' operation on ' + m.missing.join(', ') + '.');
                var noLoss = qs.filter(function (q) { return !q.loss_data; }).map(function (q) { return q.name; });
                if (noLoss.length) notes.push('No population-loss data stored for ' + (noLoss.length === qs.length ? 'any qubit' : noLoss.join(', ')) + '.');
                notes.push('Solid: fitted flux model; dashed: quad-term estimate; thin: a member that stays at idle; ◇ planned interaction frequency' +
                    (qs.some(function (q) { return (q.safe || []).length; }) ? '; right-edge strip: safe intervals' : '') +
                    (pairs.length > 10 ? '; click a legend entry to isolate a pair' : '') + '.');
                var nEl = root.querySelector('#czp-map-notes');
                if (nEl) nEl.textContent = notes.join(' ');
                var L = layout({
                    margin: { l: 64, r: 16, t: 10, b: 60 }, showlegend: pairs.length > 0,
                    legend: { orientation: 'h', y: -0.12, x: 0, font: { size: 11 } },
                    xaxis: { tickmode: 'array', tickvals: qs.map(function (q, i) { return i + 0.5; }), ticktext: qs.map(function (q) { return q.name; }),
                             range: [0, Math.max(1, qs.length)], showgrid: false, zeroline: false, fixedrange: true },
                    yaxis: { title: { text: 'frequency (GHz)' } },
                    shapes: shapes, hovermode: 'closest'
                });
                el.setAttribute('data-segments', String(m.segments.length));
                return window.requirePlotly().then(function () {
                    if (my !== mseq || !document.body.contains(root)) return;
                    return window._plotlyRender(el, traces, L, config());
                }).then(function () {
                    if (my !== mseq || !document.body.contains(root) || !el.on || el._czpMapClick) return;
                    el._czpMapClick = true;
                    el.on('plotly_click', function (ev) {
                        var pt = ev && ev.points && ev.points[0], path = pt && pt.customdata;
                        if (typeof path === 'string' && path.indexOf('qubit_pairs.') === 0) { load(root, path); revealFigures(root); }
                    });
                });
            })
            .catch(function (e) { if (my === mseq) mapEmpty(el, 'The map could not be loaded: ' + (e && e.message || e)); });
    }

    function fillOps(root, d) {
        var sel = root.querySelector('#czp-op');
        if (!sel) return;
        sel.innerHTML = (d.pair_ops || []).map(function (o) {
            return '<option value="' + esc(o.path) + '"' + (o.path === d.row.path ? ' selected' : '') + '>' + esc(o.operation) + '</option>';
        }).join('');
    }

    function load(root, op) {
        var my = ++seq;
        root.setAttribute('data-selected', op);
        root.setAttribute('data-loading', '1');
        root.removeAttribute('data-failed');
        Array.prototype.forEach.call(root.querySelectorAll('.czp-row'), function (tr) {
            var on = tr.getAttribute('data-op') === op;
            tr.classList.toggle('czp-row-selected', on);
            tr.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        var title = root.querySelector('#czp-title');
        var done = fetch('/cz-plan/data?op=' + encodeURIComponent(op), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (my !== seq || !document.body.contains(root)) return;
                root.removeAttribute('data-loading');
                if (!d.ok) {
                    root.setAttribute('data-failed', op);
                    if (title) title.textContent = op;
                    var ul = root.querySelector('#czp-notes');
                    if (ul) ul.innerHTML = '<li class="zline-note-block"><strong>not drawn</strong> ' + esc(d.error || 'failed') +
                        ' — the chip may have changed since this table was drawn; reload the page to refresh it.</li>';
                    ['#czp-flux', '#czp-time'].forEach(function (s) { var e = root.querySelector(s); if (e) empty(e, 'Not drawn.', root, my); });
                    var card = root.querySelector('#czp-card'); if (card) card.innerHTML = '';
                    return;
                }
                if (title) title.textContent = d.row.pair + '  ·  ' + d.row.operation;
                renderNotes(root, d);
                fillOps(root, d);
                renderCard(root, d);
                return Promise.all([drawFlux(root, d, my), drawTime(root, d, my)]).then(function () {
                    if (my === seq) root.setAttribute('data-rendered', op);
                });
            })
            .catch(function (e) {
                if (my !== seq) return;
                root.removeAttribute('data-loading');
                root.setAttribute('data-failed', op);
                ['#czp-flux', '#czp-time'].forEach(function (s) { var el = root.querySelector(s); if (el) empty(el, 'Not drawn.', root, my); });
                var ul = root.querySelector('#czp-notes');
                if (ul) ul.innerHTML = '<li class="zline-note-block"><strong>not drawn</strong> Could not load: ' + esc(e && e.message || e) + '</li>';
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

    /* The table's "Last set" column: one ledger read for every operation. While the chip's
       history is still being built, say so and ask again. */
    function loadHistory(root, tries) {
        var my = ++hseq, note = root.querySelector('#czp-history-note');
        return fetch('/cz-plan/history', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (h) {
                if (my !== hseq || !document.body.contains(root)) return;
                if (h.mode === 'building' || h.mode === 'preparing') {
                    if (note) note.textContent = 'This chip\'s change history is being prepared; the Last set column fills in when it is ready.';
                    if ((tries || 0) < 20) setTimeout(function () { loadHistory(root, (tries || 0) + 1); }, 3000);
                    return;
                }
                if (note) note.textContent = h.mode === 'ledger' ? '' : (h.note || 'This chip has no change history, so when each operation was set is unknown.');
                Array.prototype.forEach.call(root.querySelectorAll('td[data-last]'), function (td) {
                    var o = (h.ops || {})[td.getAttribute('data-last')];
                    if (!o || !o.last_set) {
                        // compact in the table; what is known (first record, a value that differs) on hover
                        var first = !!(o && o.note && /first recorded/.test(o.note));
                        td.innerHTML = '<span class="muted"' + (o && o.note ? ' title="' + esc(o.note) + '"' : '') + '>unknown' +
                            (first ? ' · first recorded only' : '') + '</span>';
                        return;
                    }
                    td.innerHTML = esc(when(o.last_set)) + (o.warnings.length ? ' <span class="czp-warn-text" title="' +
                        esc(o.warnings.map(function (w) { return w.text; }).join('\n')) + '">⚠ ' + o.warnings.length + ' newer</span>' : '');
                });
                root.setAttribute('data-history', h.mode || '');
            })
            .catch(function () { if (note) note.textContent = 'The change history could not be read.'; });
    }

    function revealFigures(root) {
        var t = root.querySelector('#czp-title');
        if (!t || !t.getBoundingClientRect) return;
        var r = t.getBoundingClientRect(), h = window.innerHeight || document.documentElement.clientHeight;
        if (r.top < 0 || r.top > h * 0.5) {
            try { t.scrollIntoView({ block: 'start' }); } catch (e) { t.scrollIntoView(true); }
        }
    }

    function mount() {
        var root = document.getElementById('czp-root');
        if (!root || root._czMounted) return;
        root._czMounted = true;
        var host = root.querySelector('.czp-plots');
        if (host && window.PlotHost) window.PlotHost.observe(host);
        var mapBox = root.querySelector('.czp-map-box');
        if (mapBox && window.PlotHost) window.PlotHost.observe(mapBox);
        root.addEventListener('click', function (ev) {
            var tr = ev.target.closest && ev.target.closest('.czp-row');
            if (tr && root.contains(tr)) { load(root, tr.getAttribute('data-op')); revealFigures(root); }
        });
        root.addEventListener('keydown', function (ev) {
            var tr = ev.target.closest && ev.target.closest('.czp-row');
            if (!tr) return;
            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); load(root, tr.getAttribute('data-op')); revealFigures(root); }
        });
        var sel = root.querySelector('#czp-op');
        if (sel) sel.addEventListener('change', function () { load(root, sel.value); });
        var first = root.getAttribute('data-selected');
        if (first) load(root, first);
        ['#czp-map-op', '#czp-map-order'].forEach(function (s) {
            var c = root.querySelector(s);
            if (c) c.addEventListener('change', function () { drawMap(root); });
        });
        drawMap(root);
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

    window.CzPlan = { mount: mount, refresh: refresh, drawMap: drawMap, _esc: esc };
    document.addEventListener('DOMContentLoaded', mount);
    document.addEventListener('htmx:afterSwap', mount);
    document.addEventListener('sm:wc-moved', function () {
        var root = document.getElementById('czp-root');
        if (root && root._czMounted) refresh();
    });
    if (document.readyState !== 'loading') mount();
})();
