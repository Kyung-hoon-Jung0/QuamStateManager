/* Live State Edit > Z-line distortion (bundle 'zline').
 *
 * Draws what /zline/data computed -- the physics and every refusal live in
 * core/zline_filters.py. This file only: picks the line, asks for its data,
 * shows the notes, and draws two figures in the house Plotly theme.
 * A response that arrives after a newer request is dropped (seq token), so a
 * fast click through the table never paints q3's curve under q5's title.
 */
(function () {
    'use strict';

    var seq = 0;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function colors() {
        var P = (window.PlotTheme && window.PlotTheme.palette()) || ['#4f9cf9', '#f97316', '#22c55e'];
        var muted = getComputedStyle(document.documentElement).getPropertyValue('--pico-muted-color').trim() || '#8a8a8a';
        return { out: P[0], iir: P[1], fir: P[2], ideal: muted };
    }

    function layout(over) {
        var base = window.PlotTheme ? window.PlotTheme.houseLayout({}) : {};
        var fmt = window.PlotTheme ? window.PlotTheme.axisNumberFormat() : {};
        var L = window.PlotTheme ? window.PlotTheme.houseLayout(over) : over;
        L.xaxis = Object.assign({}, base.xaxis || {}, fmt, over.xaxis || {});
        L.yaxis = Object.assign({}, base.yaxis || {}, fmt, over.yaxis || {});
        return L;
    }

    function config() {
        return window.PlotTheme ? window.PlotTheme.houseConfig({}) : { displaylogo: false, responsive: true };
    }

    function fmt(v, d) {
        return (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toFixed(d == null ? 4 : d);
    }

    function renderNotes(root, notes) {
        var ul = root.querySelector('#zline-notes');
        if (!ul) return;
        ul.innerHTML = (notes || []).map(function (n) {
            var cls = n.level === 'block' ? 'zline-note-block' : n.level === 'warn' ? 'zline-note-warn' : 'zline-note-info';
            var tag = n.level === 'block' ? 'not drawn' : n.level === 'warn' ? 'check' : 'note';
            return '<li class="' + cls + '" data-code="' + esc(n.code) + '"><strong>' + tag + '</strong> ' + esc(n.text) + '</li>';
        }).join('');
    }

    function empty(el, text) {
        if (window.PlotHost) window.PlotHost.purgeWithin(el);
        el.innerHTML = '<p class="muted zline-empty">' + esc(text) + '</p>';
    }

    /* Decade ticks from 1 ns up to tmax (seconds), labelled with their unit. */
    function decades(tmax) {
        var units = [[1e-3, 'ms'], [1e-6, 'µs'], [1e-9, 'ns']];
        var v = [], t = [];
        for (var e = -9; Math.pow(10, e) <= tmax * 1.0001; e++) {
            var x = Math.pow(10, e);
            for (var i = 0; i < units.length; i++) {
                if (x >= units[i][0] * 0.999) { v.push(x); t.push(Math.round(x / units[i][0]) + ' ' + units[i][1]); break; }
            }
        }
        return { v: v, t: t };
    }

    function drawStep(root, d) {
        var el = root.querySelector('#zline-step');
        var stats = root.querySelector('#zline-step-stats');
        if (!el) return;
        if (!d.step) {
            empty(el, d.port_path ? 'No filtered curve: see the notes above.' : 'This line has no usable port.');
            if (stats) stats.textContent = '';
            return;
        }
        var s = d.step, C = colors();
        // time in SECONDS so the SI tick prefix names the unit (1ns, 10µs, 1ms);
        // hover keeps the ns value the lab thinks in
        var ts = s.t_ns.map(function (v) { return v * 1e-9; });
        var hov = 't = %{customdata:.4~g} ns<br>%{y:.5f}<extra>%{fullData.name}</extra>';
        var dec = decades(ts[ts.length - 1]);
        var traces = [
            { x: ts, customdata: s.t_ns, y: s.ideal, name: 'ideal step', mode: 'lines',
              line: { color: C.ideal, dash: 'dash', width: 1.5 }, hovertemplate: hov },
        ];
        if (s.iir_only && s.fir_only) {
            traces.push({ x: ts, customdata: s.t_ns, y: s.iir_only, name: 'exponential only', mode: 'lines',
                          line: { color: C.iir, width: 1.25, dash: 'dot' }, hovertemplate: hov });
            traces.push({ x: ts, customdata: s.t_ns, y: s.fir_only, name: 'FIR only', mode: 'lines',
                          line: { color: C.fir, width: 1.25, dash: 'dot' }, hovertemplate: hov });
        }
        var which = s.iir_only && s.fir_only ? 'exponential + FIR' : s.iir_only ? 'exponential' : s.fir_only ? 'FIR' : 'no filter';
        traces.push({ x: ts, customdata: s.t_ns, y: s.both, name: 'output (' + which + ')', mode: 'lines',
                      line: { color: C.out, width: 2 }, hovertemplate: hov });
        var L = layout({
            showlegend: true,
            legend: { orientation: 'h', y: -0.28 },
            margin: { l: 60, r: 16, t: 12, b: 70 },
            // explicit decade ticks: Plotly's SI format writes 1e-3 s as
            // "0.001s" (it has no milli), so the unit is spelled out here
            xaxis: { type: 'log', tickmode: 'array', tickvals: dec.v, ticktext: dec.t,
                     title: { text: 'time since the step (log)' } },
            yaxis: { title: { text: 'output / step height' } },
        });
        var done = window.requirePlotly().then(function () {
            return window._plotlyRender(el, traces, L, config());
        });
        if (stats) {
            var t = 'first sample ' + fmt(s.first) + ' · peak ' + fmt(s.peak) + ' at ' + fmt(s.peak_t_ns, 1) + ' ns · at '
                + (window.PlotTheme ? window.PlotTheme.siFormat(s.horizon_ns * 1e-9, 's') : s.horizon_ns + ' ns') + ' ' + fmt(s.final, 5);
            t += s.dc_limit == null ? ' · no DC limit (integrating high-pass)' : ' · DC limit ' + fmt(s.dc_limit, 5);
            if (s.truncated) t += ' · the slowest decay is longer than the 1 ms drawn';
            stats.textContent = t;
        }
        return done;
    }

    function drawPulse(root, d) {
        var el = root.querySelector('#zline-pulse');
        var stats = root.querySelector('#zline-pulse-stats');
        var sub = root.querySelector('#zline-pulse-sub');
        if (!el) return;
        if (sub) sub.textContent = d.op ? '— ' + d.op + ', ideal vs through both filters' : '';
        if (!d.pulse) {
            empty(el, d.pulse_note || (d.step ? 'This line has no flux operation to draw.' : 'No filtered curve: see the notes above.'));
            if (stats) stats.textContent = '';
            return;
        }
        var p = d.pulse, C = colors();
        var hov = 't = %{x:.1f} ns<br>%{y:.5f} V<extra>%{fullData.name}</extra>';
        var traces = [
            { x: p.t_ns, y: p.ideal, name: 'ideal (as played)', mode: 'lines', line: { color: C.ideal, dash: 'dash', width: 1.5, shape: 'hv' }, hovertemplate: hov },
            { x: p.t_ns, y: p.both, name: 'output (through both filters)', mode: 'lines', line: { color: C.out, width: 2, shape: 'hv' }, hovertemplate: hov },
        ];
        var shapes = [];
        if (p.peak > 0.8 * p.range_v) {
            [p.range_v, -p.range_v].forEach(function (y) {
                shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y,
                              line: { color: '#e5484d', width: 1, dash: 'dot' } });
            });
        }
        var L = layout({
            showlegend: true,
            legend: { orientation: 'h', y: -0.28 },
            margin: { l: 60, r: 16, t: 12, b: 70 },
            xaxis: { title: { text: 'time (ns)' } },
            yaxis: { title: { text: 'amplitude (V)' } },
            shapes: shapes,
        });
        var done = window.requirePlotly().then(function () {
            return window._plotlyRender(el, traces, L, config());
        });
        if (stats) {
            stats.textContent = 'ideal peak ' + fmt(p.ideal_peak) + ' V · output peak ' + fmt(p.peak) + ' V'
                + (p.ratio ? ' (×' + fmt(p.ratio, 3) + ')' : '') + ' · left at the end ' + fmt(p.residual_after, 5) + ' V'
                + ' · port range ±' + p.range_v + ' V';
        }
        return done;
    }

    function fillOps(root, d) {
        var sel = root.querySelector('#zline-op');
        if (!sel) return;
        sel.innerHTML = (d.ops || []).map(function (o) {
            return '<option value="' + esc(o) + '"' + (o === d.op ? ' selected' : '') + '>' + esc(o) + '</option>';
        }).join('');
        sel.disabled = !(d.ops && d.ops.length);
    }

    function shortLine(line) {
        return String(line || '').replace(/^qubits\./, '').replace(/^qubit_pairs\./, '');
    }

    /* A failed request must not leave the PREVIOUS line's picture under the
       new selection: title names the line asked for, both figures + stats +
       operations are cleared, and data-rendered is dropped. */
    function clearFor(root, line, text) {
        var title = root.querySelector('#zline-title');
        if (title) title.textContent = shortLine(line);
        ['#zline-step', '#zline-pulse'].forEach(function (sel) {
            var el = root.querySelector(sel);
            if (el) empty(el, 'Not drawn: see the note above.');
        });
        ['#zline-step-stats', '#zline-pulse-stats', '#zline-pulse-sub'].forEach(function (sel) {
            var el = root.querySelector(sel);
            if (el) el.textContent = '';
        });
        fillOps(root, { ops: [], op: '' });
        root.removeAttribute('data-rendered');
        root.setAttribute('data-failed', line);
        renderNotes(root, [{ level: 'block', code: 'error', text: text }]);
    }

    /* The figures sit below the table; on a big chip a lower row's figures
       are off-screen. Bring the title into view only when it is not visible. */
    function revealFigures(root) {
        var t = root.querySelector('#zline-title');
        if (!t || !t.getBoundingClientRect) return;
        var r = t.getBoundingClientRect(), h = window.innerHeight || document.documentElement.clientHeight;
        if (r.top < 0 || r.top > h * 0.5) {
            try { t.scrollIntoView({ block: 'start' }); } catch (e) { t.scrollIntoView(true); }
        }
    }

    function load(root, line, op) {
        var my = ++seq;
        var model = (root.querySelector('#zline-model') || {}).value || 'sum';
        root.setAttribute('data-selected', line);
        root.setAttribute('data-loading', '1');
        root.removeAttribute('data-failed');
        Array.prototype.forEach.call(root.querySelectorAll('.zline-row'), function (tr) {
            var on = tr.getAttribute('data-line') === line;
            tr.classList.toggle('zline-row-selected', on);
            tr.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        var title = root.querySelector('#zline-title');
        var q = '/zline/data?line=' + encodeURIComponent(line) + '&model=' + model + (op ? '&op=' + encodeURIComponent(op) : '');
        var done = fetch(q, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (my !== seq || !document.body.contains(root)) return;   // a newer click won
                root.removeAttribute('data-loading');
                if (!d.ok) {
                    clearFor(root, line, (d.error || 'failed')
                        + ' -- the chip may have changed since this table was drawn; reload the page to refresh it.');
                    return;
                }
                if (title) title.textContent = shortLine(line) + (d.port_path ? '  ·  ' + d.port_path.replace('ports.analog_outputs.', '').split('.').join('/') : '');
                var notes = (d.notes || []).concat(d.pulse ? (d.pulse.notes || []) : []);
                renderNotes(root, notes);
                fillOps(root, d);
                // data-rendered is set once BOTH figures are on screen (Plotly
                // loads lazily) -- what a test or a person waits on is the picture
                return Promise.all([drawStep(root, d), drawPulse(root, d)]).then(function () {
                    if (my === seq) root.setAttribute('data-rendered', line + '|' + (d.op || '') + '|' + model);
                });
            })
            .catch(function (e) {
                if (my !== seq) return;
                root.removeAttribute('data-loading');
                clearFor(root, line, 'Could not load: ' + (e && e.message || e));
            });
        try {
            var u = new URL(window.location.href);
            if ((window.SM ? window.SM.path(u.pathname) : u.pathname) === '/zline') {   // docs/226
                u.searchParams.set('line', line);
                history.replaceState(history.state, '', u.pathname + u.search);
            }
        } catch (e) { /* no URL support -- the page still works */ }
        return done;
    }

    /* w7 final-QA P2: the working copy moved under an open page (Take live,
       a tray undo, an approval written, an Auto-Sync pull, another window's
       edit) -- `sm:wc-moved` (wc-moved.js). The table and the drawn line
       were computed from the OLD values and nothing else re-renders this
       page (the sync's in-place patch finds no cell here), so re-GET the
       table and re-load the line the reader has selected, with the same
       operation and model. The figures are redrawn in place (no blank
       frame); the table body is swapped; focus stays on its row. A table
       whose shape changed (lines added / removed) is swapped whole. */
    var rseq = 0;
    function refresh(root) {
        var my = ++rseq;
        var want = root.getAttribute('data-selected') || '';
        root.setAttribute('data-refreshing', '1');
        return fetch('/zline' + (want ? '?line=' + encodeURIComponent(want) : ''),
                     { headers: { 'HX-Request': 'true' } })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                if (my !== rseq || !document.body.contains(root)) return;
                var box = document.createElement('div');
                box.innerHTML = html;
                var fresh = box.querySelector('#zline-root');
                if (!fresh) return;
                // what the reader has NOW (a click during the fetch wins)
                var cur = root.getAttribute('data-selected') || '';
                var opSel = root.querySelector('#zline-op');
                var op = opSel ? opSel.value : '';
                var mSel = root.querySelector('#zline-model');
                var model = mSel ? mSel.value : '';
                var a = document.activeElement;
                var focusLine = (a && a.classList && a.classList.contains('zline-row') && root.contains(a))
                    ? a.getAttribute('data-line') : null;
                var has = function (line) {
                    return !!line && Array.prototype.some.call(fresh.querySelectorAll('.zline-row'),
                        function (tr) { return tr.getAttribute('data-line') === line; });
                };
                var line = has(cur) ? cur : (fresh.getAttribute('data-selected') || '');
                var oldT = root.querySelector('#zline-table');
                var newT = fresh.querySelector('#zline-table');
                var oldRows = oldT ? Array.prototype.map.call(oldT.querySelectorAll('.zline-row'), function (tr) { return tr.getAttribute('data-line'); }).join('|') : '';
                var newRows = newT ? Array.prototype.map.call(newT.querySelectorAll('.zline-row'), function (tr) { return tr.getAttribute('data-line'); }).join('|') : '';
                var target = root;
                if (oldT && newT && oldRows === newRows) {
                    oldT.replaceChild(newT.querySelector('tbody'), oldT.querySelector('tbody'));
                } else {
                    // the chip's flux lines changed: the page is a different page
                    var h = fresh.querySelector('#zline-model');
                    if (h && model) h.value = model;
                    fresh.setAttribute('data-selected', line);
                    if (window.PlotHost) window.PlotHost.purgeWithin(root);
                    root.parentNode.replaceChild(fresh, root);
                    target = fresh;
                    mount();                        // binds + loads `line`
                    target.setAttribute('data-refreshed', String(my));
                    target.removeAttribute('data-refreshing');
                    return;
                }
                if (focusLine) {
                    Array.prototype.some.call(root.querySelectorAll('.zline-row'), function (tr) {
                        if (tr.getAttribute('data-line') !== focusLine) return false;
                        tr.focus();
                        return true;
                    });
                }
                if (!line) {
                    root.setAttribute('data-refreshed', String(my));
                    root.removeAttribute('data-refreshing');
                    return;
                }
                return Promise.resolve(load(root, line, line === cur ? op : undefined)).then(function () {
                    if (my !== rseq) return;
                    root.setAttribute('data-refreshed', String(my));
                    root.removeAttribute('data-refreshing');
                });
            })
            .catch(function () { if (my === rseq) root.removeAttribute('data-refreshing'); });
    }

    function mount() {
        var root = document.getElementById('zline-root');
        if (!root || root._zlMounted) return;
        root._zlMounted = true;
        var host = root.querySelector('.zline-plots');
        if (host && window.PlotHost) window.PlotHost.observe(host);
        root.addEventListener('click', function (ev) {
            var tr = ev.target.closest && ev.target.closest('.zline-row');
            if (tr && root.contains(tr)) { load(root, tr.getAttribute('data-line')); revealFigures(root); }
        });
        root.addEventListener('keydown', function (ev) {
            var tr = ev.target.closest && ev.target.closest('.zline-row');
            if (!tr) return;
            if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); load(root, tr.getAttribute('data-line')); revealFigures(root); }
        });
        var opSel = root.querySelector('#zline-op');
        if (opSel) opSel.addEventListener('change', function () { load(root, root.getAttribute('data-selected'), opSel.value); });
        var mSel = root.querySelector('#zline-model');
        if (mSel) mSel.addEventListener('change', function () {
            load(root, root.getAttribute('data-selected'), (root.querySelector('#zline-op') || {}).value);
        });
        var first = root.getAttribute('data-selected');
        if (first) load(root, first);
    }

    window.ZLine = { mount: mount, refresh: refresh, _esc: esc, _clearFor: clearFor, _reveal: revealFigures };
    document.addEventListener('DOMContentLoaded', mount);
    document.addEventListener('htmx:afterSwap', mount);
    document.addEventListener('sm:wc-moved', function () {
        var root = document.getElementById('zline-root');
        if (root && root._zlMounted) refresh(root);
    });
    if (document.readyState !== 'loading') mount();
})();
