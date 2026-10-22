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
        var tags = d.notes_figure || [], status = [], cave = { flux: [], time: [] };
        (d.notes || []).forEach(function (t, i) {
            var tag = tags[i] || 'both';
            if (tag === 'status') status.push(t);
            else if (tag === 'flux') cave.flux.push(t);
            else if (tag === 'time') cave.time.push(t);
            else { cave.flux.push(t); cave.time.push(t); }
        });
        (d.qubits || []).forEach(function (q) {
            if (!q.summary) return;
            (q.summary.notes || []).forEach(function (t) { cave.flux.push(q.name + ': ' + t); });
        });
        setStatus(root, status);
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
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.model_hz.map(ghz), name: q.name + ' · ' + s.model_kind,
                    line: { color: col, width: 2.2, dash: /derived/.test(s.model_kind || '') ? 'dash' : 'solid' }, hovertemplate: 'amplitude: %{x:.4f} V<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>' + hoverEsc(s.model_kind) + '</extra>' });
            }
            if (c.quad_hz.some(function (v) { return v != null; })) {
                // beside a fitted model the quad term is the secondary curve: it never sets the frequency span
                traces.push({ type: 'scatter', mode: 'lines', x: c.amplitude_v, y: c.quad_hz.map(ghz), name: q.name + ' · quad-term estimate (extrapolated outside validity range)',
                    line: { color: col, width: 1.4, dash: 'dash' }, _czpSecondary: !!c.model_hz,
                    hovertemplate: 'amplitude: %{x:.4f} V<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>quad-term estimate (extrapolated outside validity range)</extra>' });
            }
            traces.push({ type: 'scatter', mode: 'markers', x: [0], y: [ghz(s.f01_hz)], name: q.name + ' idle', showlegend: false,
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
                traces.push({ type: 'scatter', mode: 'markers', x: [p.amplitude_v], y: [ghz(p.frequency_hz)], showlegend: false,
                    marker: { color: C.q[k], size: 7, symbol: 'circle-open', line: { color: C.q[k], width: 1 } },
                    hovertemplate: hoverEsc(op.operation) + ' ' + hoverEsc(p.kind || '') + '<br>amplitude: %{x:.4f} V<br>%{y:.4f} GHz<extra></extra>' });
            });
        });
        var annotations = [];
        lines.forEach(function (ln, k) {
            var p = ln.point, q = d.qubits[k];
            if (!ln.moves || p.frequency_hz == null || !q.summary) return;
            traces.push({ type: 'scatter', mode: 'markers', x: [ln.amplitude_v], y: [ghz(p.frequency_hz)], name: q.name + ' at ' + d.row.operation + ' ' + (p.kind || ''),
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
        var ft = pts[1].frequency_hz;
        var ac = d.qubits[0].summary && d.qubits[0].summary.anharmonicity_hz, at = d.qubits[1].summary && d.qubits[1].summary.anharmonicity_hz;
        if (ft != null) {
            [['|11>-|20>', ac == null ? null : ft + Math.abs(ac)], ['iSWAP', ft], ['|11>-|02>', at == null ? null : ft - Math.abs(at)]].forEach(function (lv) {
                if (lv[1] == null) return;
                shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: ghz(lv[1]), y1: ghz(lv[1]),
                    line: { color: C.res, width: 1, dash: 'dot' } });
                annotations.push({ xref: 'paper', x: right, xanchor: 'right', yref: 'y', y: ghz(lv[1]), yanchor: 'bottom', showarrow: false,
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
        var over = {
            margin: { l: 64, r: 16, t: 32, b: 90 }, showlegend: true,   // t: clear of the toolbar (U P2-4)
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 11 } },
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
            return window._plotlyRender(el, traces, L, config(), function () { return active(root, token); });
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
                hovertemplate: 't = %{x} ns<br>' + hoverEsc(q.name) + ': %{y:.4f} GHz<extra>' + hoverEsc(d.row.lines[k].point.kind || 'unknown') + '</extra>' });
            var t = traj[k];
            if (t && t.model_hz && t.quad_hz) {
                traces.push({ type: 'scatter', mode: 'lines', x: tt.slice(0, t.quad_hz.length), y: t.quad_hz.map(ghz), name: q.name + ' · quad-term estimate (extrapolated outside validity range)',
                    line: { color: C.q[k], width: 1, dash: 'dash', shape: 'hv' }, hovertemplate: 't = %{x} ns<br>%{y:.4f} GHz<extra>quad-term estimate (extrapolated outside validity range)</extra>' });
            }
        });
        var delta = series[0].map(function (fc, i) { var ft = series[1][i]; return fc == null || ft == null ? null : (fc - ft) / 1e6; });
        traces.push({ type: 'scatter', mode: 'lines', x: tt, y: delta, name: 'Δ = f_c − f_t', yaxis: 'y2', line: { color: C.muted, width: 1.8, shape: 'hv' },
            hovertemplate: 't = %{x} ns<br>Δ: %{y:.1f} MHz<extra></extra>' });
        var ac = d.qubits[0].summary && d.qubits[0].summary.anharmonicity_hz, at = d.qubits[1].summary && d.qubits[1].summary.anharmonicity_hz;
        var shapes = [], annotations = [];
        [['|11>-|20>', ac == null ? null : Math.abs(ac) / 1e6], ['iSWAP', 0], ['|11>-|02>', at == null ? null : -Math.abs(at) / 1e6]].forEach(function (lv, index) {
            if (lv[1] == null) return;
            shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: lv[1] - 5, y1: lv[1] + 5, fillcolor: tint(C.res, 0.08), line: { width: 0 }, layer: 'below' });
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: lv[1], y1: lv[1], line: { color: C.res, width: 1, dash: 'dot' } });
            // Spread the labels along their own levels: a large idle detuning packs them vertically.
            annotations.push({ xref: 'paper', x: 1 - index * 0.2, xanchor: 'right', yref: 'y2', y: lv[1], yanchor: 'bottom', showarrow: false, text: lv[0], font: { size: 10, color: C.res } });
        });
        // the time Delta(t) spends within 5 MHz of each resonance
        var dwell = [['|11>-|20>', ac == null ? null : Math.abs(ac) / 1e6], ['iSWAP', 0], ['|11>-|02>', at == null ? null : -Math.abs(at) / 1e6]].map(function (lv) {
            if (lv[1] == null) return null;
            var c = delta.filter(function (v) { return v != null && Math.abs(v - lv[1]) <= 5; }).length;
            return c ? lv[0] + ' ' + c + ' ns' : null;
        }).filter(Boolean);
        el.setAttribute('data-dwell', dwell.join('; '));
        var L = layout({
            margin: { l: 64, r: 16, t: 32, b: 90 }, showlegend: true,   // t: clear of the toolbar (U P2-4)
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 11 } },
            xaxis: { title: { text: 'time (ns, 1 sample per ns)' }, anchor: 'y2' },
            yaxis: { title: { text: 'frequency (GHz)' }, domain: [0.42, 1] },
            yaxis2: { title: { text: 'Δ (MHz)' }, domain: [0, 0.34], anchor: 'x' },
            shapes: shapes, annotations: annotations, hovermode: 'x unified'
        });
        return window.requirePlotly().then(function () {
            if (!active(root, token)) return;
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
            (sw ? '<br><small class="muted">excursion ' + fmtGHz(sw.f_min_hz) + ' – ' + fmtGHz(sw.f_max_hz) + ' ' + esc(sw.label || '') +
                  (gl.marker && gl.marker.label ? '; ' + esc(gl.marker.label) : '') + (sw.note ? '; ' + esc(sw.note) : '') + '</small>' : '') + '</p>');
        return h.join('');
    }

    /* (c) the operation's card. */
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
                '<dt>Z amp (V)</dt><dd title="' + esc(ln.amp_title || '') + '">' + esc(ln.amp_text || '\u2014') + '</dd>' +
                '<dt>Idle f_01</dt><dd>' + fmtGHz(s.f01_hz) + '</dd>' +
                '<dt>At the operation</dt><dd>' + fmtGHz(p.frequency_hz) + (p.kind ? ' <span class="czp-tier' + (/quad/.test(p.kind) ? ' czp-tier-quad' : '') + '">' + esc(p.kind) + '</span>' : '') +
                (p.reason ? '<br><span class="czp-warn-text">' + esc(p.reason) + '</span>' : '') + '</dd>' +
                (p.quad_hz != null && p.model_hz != null && !s.model_rejection ? '<dt>' + esc(p.quad_kind || 'Quad-term estimate') + '</dt><dd>' + fmtGHz(p.quad_hz) + ' <small class="muted">(' + fmtMHz(p.error_hz) + ' from the model)</small></dd>' : '') +
                (s.model_rejection && p.model_hz != null ? '<dt>Re-anchored estimate [derived]</dt><dd>' + fmtGHz(p.model_hz) + '</dd>' : '') +
                (p.validity ? '<dt>Validity</dt><dd><span class="' + (/outside|beyond/.test(p.validity) ? 'czp-warn-text' : '') + '">' + esc(p.validity) + '</span>' + (/quadratic/.test(p.validity) && s.range && s.range.limit_hz ? ' <small class="muted">(limit ' + fmtMHz(s.range.limit_hz) + ', ' + esc(s.range.source || '') + ')</small>' : '') + '</dd>' : '') +
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
            '<dt>Duration</dt><dd>' + (r.duration_ns != null ? r.duration_ns + ' ns' + (r.duration_note ? ' <small class="muted">(' + esc(r.duration_note) + ')</small>' : '')
                : (r.duration_note ? esc(r.duration_note) : 'unknown')) + '</dd>' +
            (r.coupler_amplitude_v != null ? '<dt>Coupler pulse</dt><dd>' + fmtV(r.coupler_amplitude_v) + '</dd>' : '') +
            (r.plan ? '<dt>Planned ω_int</dt><dd>' + fmtGHz(r.plan.omega_int_hz) + (r.plan.path_ok === false ? ' <span class="czp-warn-text">path not ok</span>' : '') +
                (r.plan.reasons.length ? '<br><small class="muted">' + esc(r.plan.reasons.join(', ')) + '</small>' : '') + '</dd>' : '') +
            (r.interaction_frequency_hz ? '<dt>Stored interaction frequency</dt><dd>' + fmtGHz(r.interaction_frequency_hz) + '</dd>' : '') +
            '<dt>Fidelity</dt><dd>' + (fl.length ? fl.join('<br>') : '—') + '</dd>' +
            '<dt>Last set</dt><dd>' + (cal.last_set ? esc(when(cal.last_set)) + ' <small class="muted">(' +
                esc(setWords(cal.last_set, cal.proven)) + ')</small>' :
                '<span class="muted">unknown' + (cal.note ? ' — ' + esc(zoneText(cal.note)) : '') + '</span>') + '</dd>' +
            (count ? '<dt>Staleness</dt><dd><a href="#czp-notes-box" class="czp-notes-link czp-warn-text">' +
                plural(count, 'input changed', 'inputs changed') + ' after this operation was set</a></dd>' : '') +
            '</dl></div>');
        h.push('</div>');
        el.innerHTML = h.join('');
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
                return Promise.all([drawFlux(root, d, my), drawTime(root, d, my)]).then(function () {
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
                if (note) note.textContent = h.mode === 'ledger' ? '' : (h.note || 'This chip has no change history, so when each operation was set is unknown.');
                Array.prototype.forEach.call(root.querySelectorAll('td[data-last]'), function (td) {
                    var o = (h.ops || {})[td.getAttribute('data-last')];
                    if (!o || !o.last_set) {
                        // compact in the table; what is known (first record, a value that differs) on hover
                        var first = !!(o && o.note && /first recorded/.test(o.note));
                        td.innerHTML = '<span class="muted"' + (o && o.note ? ' title="' + esc(zoneText(o.note)) + '"' : '') + '>unknown' +
                            (first ? ' · first recorded only' : '') + '</span>';
                        return;
                    }
                    // fix3 P1-2: a run that only carried the value first is not named as its writer
                var saved = o.last_set.provenance === 'run_saved' || o.last_set.provenance === 'run_uncertain_chip';
                td.innerHTML = (saved ? '<span title="' + esc(setWords(o.last_set, false)) + '">saved in ' + stampCell(when(o.last_set)) + '</span>'
                                      : stampCell(when(o.last_set))) + (o.warnings.length ? ' <span class="czp-warn-text czp-nowrap" title="' +
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

    /* v1.5.1 (r6, D4a P2-5): a table wider than its wrap scrolls inside it with pair and
       operation held in view; the operation sticks just right of the pair column, whose drawn
       width follows the pair names and the UI scale. */
    function stickOperation(table) {
        var pair = table && table.querySelector('th.czp-stick');
        if (!pair) return;
        function sync() { table.style.setProperty('--czp-stick-op', pair.getBoundingClientRect().width + 'px'); }
        sync();
        if (window.ResizeObserver) new window.ResizeObserver(sync).observe(pair, { box: 'border-box' });
        // the held columns get an edge only while other columns pass under them
        var wrap = table.parentElement;
        if (wrap) wrap.addEventListener('scroll', function () {
            wrap.classList.toggle('czp-scrolled-x', wrap.scrollLeft > 0);
        }, { passive: true });
    }

    /* A date and its time stay on one line in the Last set column (a narrow cell broke the
       date at its hyphen); the text itself is unchanged. */
    function stampCell(text) {
        return esc(text).replace(/\d{4}-\d\d-\d\d \d\d:\d\d(?::\d\d)?/, '<span class="czp-nowrap">$&</span>');
    }

    /* The table follows the map's Operation picker: a named operation lists only its rows,
       "cz per pair" lists every row. The selected row stays visible. */
    function filterRows(root, op) {
        var rows = root.querySelectorAll('.czp-row'), shown = 0, sel = root.getAttribute('data-selected');
        Array.prototype.forEach.call(rows, function (tr) {
            var on = !op || tr.getAttribute('data-opname') === op;
            tr.hidden = !on;
            if (on) shown++;
        });
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
        var first = root.getAttribute('data-selected');
        if (first) load(root, first);
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
