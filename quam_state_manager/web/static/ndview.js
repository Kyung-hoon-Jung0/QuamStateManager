/* N-D data viewer client — builds themed Plotly views from a server cube.
 *
 * The cube (one fetch per variable) carries decimated data + coords +
 * semantics; EVERY interaction after that — entity chips, overlay curves,
 * sliders, I/Q/|z|/phase, theme — is a client-side re-render. Never crashes:
 * trace building is guarded, failures render an honest error card.
 */
(function () {
    'use strict';

    // HTML/attr escaping for DATASET-derived strings (coord values, units,
    // long_name, error text) — matches the plot-apply popup's _ppEscape bar.
    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    var state = null;   // {uid, which, var, cube, partnerCube, comp, sel:{dim:idx}, entityIdx}
    var cubeCache = {}; // "uid::which::var" -> cube (dropped on run switch/mount)
    var mountGen = 0;   // bumped per mount(); in-flight fetches from an older
                        // generation are DISCARDED (run-switch race guard)

    function root() { return document.getElementById('ndv-root'); }
    function el(id) { return document.getElementById(id); }

    /* ── flat indexing over the nested-array cube ─────────────────────── */
    function flatten(nested, dims) {
        var shape = dims.map(function (d) { return d.size; });
        var total = shape.reduce(function (a, b) { return a * b; }, 1);
        var flat = new Float64Array(total);
        var i = 0;
        (function walk(a, depth) {
            if (depth === shape.length - 1 || !Array.isArray(a)) {
                if (Array.isArray(a)) {
                    for (var k = 0; k < a.length; k++) flat[i++] = a[k] === null ? NaN : a[k];
                } else { flat[i++] = a === null ? NaN : a; }
                return;
            }
            for (var k2 = 0; k2 < a.length; k2++) walk(a[k2], depth + 1);
        })(nested, 0);
        var strides = new Array(shape.length); var s = 1;
        for (var d = shape.length - 1; d >= 0; d--) { strides[d] = s; s *= shape[d]; }
        return { flat: flat, shape: shape, strides: strides };
    }
    function pick(fx, fixed) {           // fixed: {dimIdx: coordIdx} → base offset
        var off = 0;
        for (var d in fixed) off += fixed[d] * fx.strides[d];
        return off;
    }
    function extract1D(fx, axis, fixed) {
        var out = new Array(fx.shape[axis]);
        var base = pick(fx, fixed);
        for (var i = 0; i < fx.shape[axis]; i++) out[i] = fx.flat[base + i * fx.strides[axis]];
        return out;
    }
    function extract2D(fx, yAxis, xAxis, fixed) {
        var out = new Array(fx.shape[yAxis]);
        var base = pick(fx, fixed);
        for (var r = 0; r < fx.shape[yAxis]; r++) {
            var row = new Array(fx.shape[xAxis]);
            var rb = base + r * fx.strides[yAxis];
            for (var c = 0; c < fx.shape[xAxis]; c++) row[c] = fx.flat[rb + c * fx.strides[xAxis]];
            out[r] = row;
        }
        return out;
    }

    /* ── component math (I/Q/|z|/phase) ───────────────────────────────── */
    // I and Q are decimated INDEPENDENTLY server-side (each keeps the indices its
    // own peaks fall on), so a large cube's siblings hold DIFFERENT source points.
    // Zipping them element-wise would combine value[i] of I with value[i] of Q from
    // a different frequency/amplitude → a physically WRONG |IQ|/phase that looks
    // plausible. Only combine when the two line up exactly (same dims, and identical
    // kept-index maps on every decimated dim); otherwise refuse.
    function _iqAligned(cube, partner) {
        var da = cube.dims || [], db = partner.dims || [];
        if (da.length !== db.length) return false;
        for (var i = 0; i < da.length; i++)
            if (da[i].name !== db[i].name || da[i].size !== db[i].size) return false;
        var ka = cube.kept || {}, kb = partner.kept || {}, names = {};
        Object.keys(ka).forEach(function (n) { names[n] = 1; });
        Object.keys(kb).forEach(function (n) { names[n] = 1; });
        for (var n in names) {
            var xa = ka[n] || [], xb = kb[n] || [];
            if (xa.length !== xb.length) return false;
            for (var j = 0; j < xa.length; j++) if (xa[j] !== xb[j]) return false;
        }
        return true;
    }
    function componentData(cube, partner, comp) {
        if (comp === 'base' || !partner) return cube._fx;
        var a = cube._fx, b = partner._fx;
        if (a.flat.length !== b.flat.length || !_iqAligned(cube, partner)) return null;
        var out = new Float64Array(a.flat.length);
        var isI = /^I/.test(cube.var);
        for (var i = 0; i < a.flat.length; i++) {
            var I = isI ? a.flat[i] : b.flat[i];
            var Q = isI ? b.flat[i] : a.flat[i];
            out[i] = comp === 'mag' ? Math.sqrt(I * I + Q * Q) : Math.atan2(Q, I);
        }
        return { flat: out, shape: a.shape, strides: a.strides };
    }

    /* ── labels ───────────────────────────────────────────────────────── */
    function axisTitle(dim) {
        var t = dim.long_name || dim.name;
        return dim.units ? t + ' [' + dim.units + ']' : t;
    }
    function valueLabel(cube, comp) {
        var base = cube.long_name || cube.var;
        if (comp === 'mag') base = '|' + base.replace(/^[IQ]/, 'IQ') + '|';
        if (comp === 'phase') base = 'arg(' + base + ')';
        return cube.units && comp === 'base' ? base + ' [' + cube.units + ']' : base;
    }

    /* ── controls ─────────────────────────────────────────────────────── */
    function dimByName(cube, name) {
        for (var i = 0; i < cube.dims.length; i++) if (cube.dims[i].name === name) return i;
        return -1;
    }
    function coordLabel(dim, idx) {
        if (!dim.coord) return String(idx);
        var v = dim.coord[idx];
        return typeof v === 'number' ? window.PlotTheme.siFormat(v, dim.units) : String(v);
    }

    function renderControls() {
        var c = el('ndv-controls');
        if (!c || !state || !state.cube || !state.cube.ok) { if (c) c.hidden = true; return; }
        var cube = state.cube, html = '';
        cube.dims.forEach(function (d, di) {
            if (d.size <= 1) return;
            var role = roleOf(d.name);
            if (role === 'entity' || role === 'slider') {
                var chips = '';
                for (var i = 0; i < d.size; i++) {
                    var on = (state.sel[di] || 0) === i;
                    chips += '<button type="button" class="ndv-chip' + (on ? ' active' : '') +
                        '" data-dim="' + di + '" data-idx="' + i + '">' +
                        esc(coordLabel(d, i)) + '</button>';
                }
                html += '<div class="ndv-ctl"><span class="ndv-ctl-label">' + esc(d.name) +
                        '</span>' + chips + '</div>';
            }
        });
        if (cube.iq_partner) {
            var comps = [['base', state.cube.var], ['mag', '|IQ|'], ['phase', 'phase']];
            html += '<div class="ndv-ctl"><span class="ndv-ctl-label">component</span>' +
                comps.map(function (p) {
                    return '<button type="button" class="ndv-chip' +
                        (state.comp === p[0] ? ' active' : '') +
                        '" data-comp="' + p[0] + '">' + p[1] + '</button>';
                }).join('') + '</div>';
        }
        var dec = cube.dims.some(function (d) { return d.decimated; });
        if (dec) html += '<span class="ndv-note muted">decimated view — peaks preserved</span>';
        c.innerHTML = html;
        c.hidden = !html;
    }

    function roleOf(name) {
        var v = state.cube.default_view || {};
        if (name === v.x || name === v.y) return 'axis';
        if ((v.overlay || []).indexOf(name) !== -1) return 'overlay';
        if (name === v.entity) return 'entity';
        return 'slider';
    }

    /* ── the render ───────────────────────────────────────────────────── */
    function render() {
        var plotEl = el('ndv-plot'), fb = el('ndv-fallback');
        if (!plotEl || !state || !state.cube) return;
        var cube = state.cube;
        fb.hidden = true; fb.innerHTML = '';
        if (!cube.ok) { return renderFallback(cube); }
        if (cube.scalar !== undefined && cube.data === null) {
            // siFormat guards on !isFinite, but JS isFinite coerces strings, so a
            // numeric-looking string scalar ("1.5") slips through to v.toPrecision()
            // and THROWS — uncaught on the cached-cube path (this sits before the
            // try). Only siFormat real numbers; show string scalars as their text
            // (the server took care to ship them) instead of throwing or '—'.
            var sval = (typeof cube.scalar === 'number')
                ? window.PlotTheme.siFormat(cube.scalar, cube.units)
                : esc(String(cube.scalar));
            plotEl.innerHTML = '<div class="ndv-scalar"><span>' + esc(cube.var) +
                '</span><strong>' + sval + '</strong></div>';
            return;
        }
        try { renderPlot(plotEl, cube); }
        catch (e) {
            console.error('ndview render failed', e);
            renderFallback({ error: 'Could not render this view (' + e.message + ').',
                             fallback: null });
        }
    }

    function renderPlot(plotEl, cube) {
        if (!cube._fx) cube._fx = flatten(cube.data, cube.dims);
        var fx = componentData(cube, state.partnerCube, state.comp);
        if (!fx) {
            // Honest degradation: I and Q couldn't be safely combined (decimated to
            // different sample points). Show the individual components, never a
            // wrong magnitude/phase.
            return renderFallback({
                error: 'Magnitude / phase is unavailable for this view: I and Q were '
                    + 'decimated to different sample points, so combining them here '
                    + 'would show physically wrong values. View the I or Q component '
                    + 'on its own, or open a smaller run.',
                fallback: null,
            });
        }
        var v = cube.default_view || {};
        var xI = dimByName(cube, v.x), yI = v.y ? dimByName(cube, v.y) : -1;
        // fixed = every non-axis, non-overlay dim at its selected index
        var overlays = (v.overlay || []).map(function (n) { return dimByName(cube, n); })
                                        .filter(function (i) { return i >= 0 && cube.dims[i].size > 1; });
        var fixedBase = {};
        cube.dims.forEach(function (d, di) {
            if (di === xI || di === yI || overlays.indexOf(di) !== -1) return;
            if (d.size <= 1) { fixedBase[di] = 0; return; }
            fixedBase[di] = state.sel[di] || 0;
        });

        var traces = [], layout;
        var T = window.PlotTheme;
        if (xI === -1) {   // no sweep axis → per-entity bar/values
            var eI = dimByName(cube, v.entity || '');
            if (eI >= 0) {
                var xs = cube.dims[eI].coord || Array.from({length: cube.dims[eI].size}, function (_, i) { return i; });
                var fixedE = Object.assign({}, fixedBase); delete fixedE[eI];
                traces.push({ type: 'bar', x: xs, y: extract1D(fx, eI, fixedE) });
                layout = T.houseLayout({ xaxis: { title: { text: v.entity } },
                                         yaxis: { title: { text: valueLabel(cube, state.comp) } } });
            } else {
                return renderFallback({ error: 'No plottable axis in this variable.',
                                        fallback: cube.fallback });
            }
        } else if (yI === -1) {   // 1-D lines (+ overlays)
            var xd = cube.dims[xI];
            var xArr = xd.coord || Array.from({length: xd.size}, function (_, i) { return i; });
            var combos = overlayCombos(cube, overlays);
            combos.forEach(function (combo) {
                var fixed = Object.assign({}, fixedBase, combo.fixed);
                traces.push({
                    type: 'scatter',
                    mode: xd.size > 400 ? 'lines' : 'lines+markers',
                    marker: { size: 5 }, line: { width: 1.6 },
                    x: xArr, y: extract1D(fx, xI, fixed),
                    name: combo.label, showlegend: !!combo.label,
                    customdata: (cube.kept && cube.kept[xd.name]) || undefined,
                });
            });
            layout = T.houseLayout({
                xaxis: { title: { text: axisTitle(xd) } },
                yaxis: { title: { text: valueLabel(cube, state.comp) } },
                showlegend: combos.length > 1, height: 380,
            });
        } else {   // heatmap
            var xd2 = cube.dims[xI], yd = cube.dims[yI];
            var overlayFixed = {};
            overlays.forEach(function (oi) { overlayFixed[oi] = state.sel[oi] || 0; });
            var z = extract2D(fx, yI, xI, Object.assign({}, fixedBase, overlayFixed));
            var zf = z.flat ? z.flat() : [].concat.apply([], z);
            var finite = zf.filter(function (x) { return isFinite(x); }).sort(function (a, b) { return a - b; });
            var lo = finite.length ? finite[Math.floor(0.02 * (finite.length - 1))] : 0;
            var hi = finite.length ? finite[Math.floor(0.98 * (finite.length - 1))] : 1;
            traces.push({
                type: 'heatmap', z: z,
                x: xd2.coord || undefined, y: yd.coord || undefined,
                colorscale: 'Viridis', zmin: lo, zmax: hi, zsmooth: false,
                colorbar: { thickness: 12, len: 0.9,
                            title: { text: valueLabel(cube, state.comp), side: 'right' } },
            });
            layout = T.houseLayout({
                xaxis: { title: { text: axisTitle(xd2) } },
                yaxis: { title: { text: axisTitle(yd) } },
                height: 420,
            });
            if (cube.dims[xI].bin_mean || cube.dims[yI].bin_mean) {
                layout.annotations = [{ text: 'bin-mean', showarrow: false,
                    xref: 'paper', yref: 'paper', x: 1, y: 1.05,
                    font: { size: 10 } }];
            }
        }
        // POSITIONAL calling convention — app.js's window._plotlyRender is
        // _plotlyRender(divId, data, layout, config). The local fallback (jsdom /
        // _plotlyRender absent) takes the SAME positional signature so tests
        // exercise the real convention.
        var renderFn = window._plotlyRender ||
            function (id, data, layout, config) { return window.Plotly.newPlot(id, data, layout, config); };
        var drawn = renderFn('ndv-plot', traces, layout, T.houseConfig());
        // Plotly binds `.on` to the div only once the (async) draw completes —
        // attach the click handler inside the render promise, never synchronously
        // (a fresh div has no `.on` yet and the handler would be silently lost).
        Promise.resolve(drawn).then(function () {
            attachClick(plotEl, cube, xI, yI);
        }).catch(function (e) { console.error('ndview click attach failed', e); });
    }

    function overlayCombos(cube, overlays) {
        if (!overlays.length) return [{ fixed: {}, label: '' }];
        var combos = [{ fixed: {}, label: '' }];
        overlays.forEach(function (oi) {
            var d = cube.dims[oi], next = [];
            combos.forEach(function (c) {
                for (var i = 0; i < d.size; i++) {
                    var f = Object.assign({}, c.fixed); f[oi] = i;
                    var lbl = d.name + '=' + coordLabel(d, i);
                    next.push({ fixed: f, label: c.label ? c.label + ', ' + lbl : lbl });
                }
            });
            combos = next;
        });
        return combos.slice(0, 8);   // hard cap
    }

    function renderFallback(cube) {
        var fb = el('ndv-fallback'), plotEl = el('ndv-plot');
        if (plotEl) { try { window.Plotly && window.Plotly.purge(plotEl); } catch (e) {} plotEl.innerHTML = ''; }
        if (!fb) return;
        var html = '<p class="muted">' + esc(cube.error || 'Cannot plot this variable.') + '</p>';
        if (cube.fallback && cube.fallback.kind === 'table') {
            html += '<div class="ndv-table-note muted">raw sample (' + cube.fallback.total +
                ' values, dims ' + esc(cube.fallback.dims.join('×')) + '):</div><code class="ndv-table">' +
                cube.fallback.sample.map(function (v) { return v === null ? '∅' : esc(v); }).join(', ') +
                '</code>';
        }
        fb.innerHTML = html; fb.hidden = false;
    }

    /* ── click → value chip (phase 2 wires candidates) ────────────────── */
    function attachClick(plotEl, cube, xI, yI) {
        if (!plotEl.on) return;
        plotEl.removeAllListeners && plotEl.removeAllListeners('plotly_click');
        plotEl.on('plotly_click', function (ev) {
            var p = ev.points && ev.points[0];
            if (!p) return;
            var rows = [];
            var xd = xI >= 0 ? cube.dims[xI] : null, yd = yI >= 0 ? cube.dims[yI] : null;
            if (xd) rows.push([axisTitle(xd), p.x, xd.units]);
            if (yd) rows.push([axisTitle(yd), p.y, yd.units]);
            if (p.z !== undefined) rows.push([valueLabel(cube, state.comp), p.z, cube.units]);
            else if (!yd) rows.push([valueLabel(cube, state.comp), p.y, cube.units]);
            if (window.NdViewChip) window.NdViewChip.show(ev.event, rows, cube, p);
        });
    }

    /* ── data loading ─────────────────────────────────────────────────── */
    function openVar(name) {
        var r = root(); if (!r) return;
        var uid = r.getAttribute('data-uid'), which = r.getAttribute('data-which');
        // uid-scoped key: run A's cube can never be served for run B's panel.
        var key = uid + '::' + which + '::' + name;
        var gen = mountGen;   // stale-fetch guard (run switched mid-flight)
        r.querySelectorAll('.ndv-var-card').forEach(function (b) {
            b.classList.toggle('active', b.getAttribute('data-var') === name);
        });
        var apply = function (cube) {
            state = { uid: uid, which: which, var: name, cube: cube,
                      partnerCube: null, comp: 'base', sel: {} };
            if (cube.ok && cube.iq_partner) prefetchPartner(uid, which, cube.iq_partner);
            renderControls(); render();
        };
        if (cubeCache[key]) return apply(cubeCache[key]);
        var plotEl = el('ndv-plot');
        if (plotEl) {
            // Purge before gutting the div — otherwise el.data stays set and the
            // next render takes the Plotly.react branch against a gutted node.
            try { window.Plotly && window.Plotly.purge(plotEl); } catch (e) {}
            plotEl.innerHTML = '<div class="ndv-loading muted">loading ' + esc(name) + '…</div>';
        }
        fetch('/dataset/' + encodeURIComponent(uid) + '/ndview/data?which=' +
              encodeURIComponent(which) + '&var=' + encodeURIComponent(name))
            .then(function (r2) { return r2.json(); })
            .then(function (cube) {
                if (gen !== mountGen) return;   // stale run — never cache/apply
                cubeCache[key] = cube; apply(cube);
            })
            .catch(function (e) {
                if (gen !== mountGen) return;
                renderFallback({ error: 'Network error loading the data (' + e + ').' });
            });
    }

    function prefetchPartner(uid, which, partner) {
        var key = uid + '::' + which + '::' + partner;
        var gen = mountGen;
        if (cubeCache[key]) { state.partnerCube = prepPartner(cubeCache[key]); return; }
        fetch('/dataset/' + encodeURIComponent(uid) + '/ndview/data?which=' +
              encodeURIComponent(which) + '&var=' + encodeURIComponent(partner))
            .then(function (r2) { return r2.json(); })
            .then(function (cube) {
                if (gen !== mountGen) return;   // stale run — never cache/apply
                cubeCache[key] = cube;
                if (state && state.cube && state.cube.iq_partner === partner) {
                    state.partnerCube = prepPartner(cube);
                }
            }).catch(function () { /* component selector just stays I/Q-only */ });
    }
    function prepPartner(cube) {
        if (cube && cube.ok && !cube._fx) cube._fx = flatten(cube.data, cube.dims);
        return (cube && cube.ok) ? cube : null;
    }

    /* ── mount + delegation ───────────────────────────────────────────── */
    function mount() {
        var r = root(); if (!r) return;
        cubeCache = {}; state = null;   // fresh per run/file (client memory policy)
        mountGen++;                     // invalidate every in-flight fetch
        if (!r._ndvBound) {
            r._ndvBound = true;
            r.addEventListener('click', function (evt) {
                var card = evt.target.closest('.ndv-var-card');
                if (card) return openVar(card.getAttribute('data-var'));
                var chip = evt.target.closest('.ndv-chip');
                if (chip && state) {
                    if (chip.hasAttribute('data-comp')) {
                        state.comp = chip.getAttribute('data-comp');
                    } else {
                        state.sel[+chip.getAttribute('data-dim')] = +chip.getAttribute('data-idx');
                    }
                    renderControls(); render();
                }
            });
        }
        // auto-open the first data variable (not a fit-coord card)
        var first = r.querySelector('.ndv-var-card:not(.ndv-var-fit)') ||
                    r.querySelector('.ndv-var-card');
        if (first) openVar(first.getAttribute('data-var'));
    }

    window.NdView = { mount: mount, _state: function () { return state; } };

    /* ── Value chip: click a point → copy + ranked state-field candidates ──
     * Current values are fetched at CLICK time via /field/peek (never cached —
     * an apply must be reflected on the very next click). [Stage] reuses the
     * audited plot-apply popup (/field/edit + chip-token 409 + tray + Ctrl+Z).
     * ndview axes carry RAW state-unit values (titles show units, ticks stay
     * raw), so the clicked coordinate needs NO unit reversal. */
    function entityValue(cube) {
        var v = cube.default_view || {};
        var ei = v.entity ? dimByName(cube, v.entity) : -1;
        if (ei < 0) {
            // A size-1 entity dim is squeezed out of the view but still names
            // the qubit/pair (coord ['qA1']) — the value chip needs it.
            for (var i = 0; i < cube.dims.length; i++) {
                if (cube.dims[i].kind === 'entity' && cube.dims[i].coord) { ei = i; break; }
            }
        }
        if (ei < 0) return null;
        var d = cube.dims[ei];
        var idx = (state && state.sel[ei]) || 0;
        return d.coord ? String(d.coord[idx]) : String(idx);
    }

    function chipEl() {
        var c = document.getElementById('ndv-value-chip');
        if (!c) {
            c = document.createElement('div');
            c.id = 'ndv-value-chip';
            document.body.appendChild(c);
            document.addEventListener('click', function (evt) {
                if (!c.hidden && !evt.target.closest('#ndv-value-chip')
                    && !evt.target.closest('.ndv-plot')) c.hidden = true;
            });
            document.addEventListener('keydown', function (evt) {
                if (evt.key === 'Escape') c.hidden = true;
            });
        }
        return c;
    }

    function show(domEvent, rows, cube, point) {
        var c = chipEl();
        var ent = entityValue(cube);
        var click = cube.click || {};
        var html = '<div class="ndv-chip-head">' +
            (ent ? '<span class="ndv-chip-entity">' + esc(ent) + '</span>' : '') +
            '<button type="button" class="ndv-chip-x" aria-label="Close">&times;</button></div>';
        rows.forEach(function (r) {
            var raw = r[1];
            html += '<div class="ndv-chip-row"><span>' + esc(r[0]) + '</span><strong>' +
                esc(window.PlotTheme.siFormat(raw, r[2])) + '</strong>' +
                '<button type="button" class="ndv-copy" data-raw="' + esc(String(raw)) +
                '" title="Copy the raw state-unit value">copy</button></div>';
        });
        var view = cube.default_view || {};
        var cands = (click.candidates || []).filter(function (cd) {
            return !ent || cd.path.indexOf('{p}') === -1 || view.entity === 'qubit_pair';
        });
        if (cands.length && ent) {
            html += '<div class="ndv-chip-cands" data-pending="1">' +
                '<span class="muted" style="font-size:0.7rem">loading current values…</span></div>';
        }
        html += '<div class="ndv-chip-foot">' +
            (ent ? '<button type="button" class="ndv-chip-explorer">Open in Explorer →</button>' : '') +
            '</div>';
        c.innerHTML = html;
        c.hidden = false;
        var ev = domEvent || {};
        var x = Math.min((ev.clientX || 200) + 12, window.innerWidth - 320);
        var y = Math.min((ev.clientY || 200) + 12, window.innerHeight - 220);
        c.style.left = x + 'px'; c.style.top = y + 'px';

        c.querySelector('.ndv-chip-x').onclick = function () { c.hidden = true; };
        c.querySelectorAll('.ndv-copy').forEach(function (b) {
            b.onclick = function () {
                var v = b.getAttribute('data-raw');
                (navigator.clipboard ? navigator.clipboard.writeText(v)
                                     : Promise.reject()).then(function () {
                    b.textContent = 'copied';
                    setTimeout(function () { b.textContent = 'copy'; }, 900);
                }).catch(function () { window.prompt('Copy value:', v); });
            };
        });

        if (cands.length && ent) {
            var paths = cands.map(function (cd) {
                return cd.path.replace('{q}', ent).replace('{p}', ent);
            });
            fetch('/field/peek?' + paths.map(function (pp) {
                return 'dot_path=' + encodeURIComponent(pp);   // server reads getlist("dot_path")
            }).join('&'))
                .then(function (r) { return r.json(); })
                .then(function (peek) {
                    var host = c.querySelector('.ndv-chip-cands');
                    if (!host || c.hidden) return;
                    var vals = (peek && peek.values) || {};
                    var errs = (peek && peek.errors) || {};
                    var body = '';
                    cands.forEach(function (cd, i) {
                        var pth = paths[i];
                        var cur = vals[pth];
                        // field absent on this chip (peek reports null + a
                        // per-path error) → no Stage row for it.
                        if (cur === undefined || errs[pth] !== undefined) return;
                        // Resolve the candidate's dim BINDING against the
                        // rendered axes by NAME — never by row index (x/y
                        // assignment is sweep-size driven and can swap; a
                        // y-bound candidate must take the y coordinate even
                        // when freq out-sizes power). No match → skip: a wrong
                        // suggestion is worse than none (click_targets
                        // contract) — there is deliberately NO fallback.
                        var clicked;
                        if (cd.dim && cd.dim === view.x) clicked = point.x;
                        else if (cd.dim && cd.dim === view.y) clicked = point.y;
                        if (clicked === undefined || clicked === null) return;
                        body += '<div class="ndv-cand">' +
                            '<span class="ndv-cand-label" title="' + esc(pth) + '">' + esc(cd.label) + '</span>' +
                            '<span class="ndv-cand-delta">' +
                            window.PlotTheme.siFormat(cur, '') + ' → ' +
                            window.PlotTheme.siFormat(clicked, '') + '</span>' +
                            '<button type="button" class="ndv-cand-stage" data-path="' + esc(pth) +
                            '" data-value="' + esc(clicked) + '">Stage</button></div>';
                    });
                    host.innerHTML = body ||
                        '<span class="muted" style="font-size:0.7rem">no matching fields on this chip</span>';
                    host.querySelectorAll('.ndv-cand-stage').forEach(function (b) {
                        b.onclick = function () {
                            c.hidden = true;
                            var chip = (cube.click && cube.click.chip) || null;
                            window._openPlotApplyPopup(
                                [{ dot_path: b.getAttribute('data-path'),
                                   value: b.getAttribute('data-value') }],
                                (cube.click && cube.click.experiment) || cube.var,
                                ent, [], chip);
                        };
                    });
                })
                .catch(function () {
                    var host = c.querySelector('.ndv-chip-cands');
                    if (host) host.innerHTML =
                        '<span class="muted" style="font-size:0.7rem">could not read current values</span>';
                });
        }

        var ex = c.querySelector('.ndv-chip-explorer');
        if (ex) ex.onclick = function () {
            c.hidden = true;
            if (window._navigateToExplorerPath) {
                window._navigateToExplorerPath('qubits.' + ent);
            }
        };
    }

    window.NdViewChip = { show: show };
})();
