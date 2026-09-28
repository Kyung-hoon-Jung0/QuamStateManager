/* w9/pulsesall -- the VIRTUAL per-page "All" view of the Pulses table.
 *
 * Why: big30x has ~8,800 pulse rows. Rendered whole, the table was 656k DOM
 * nodes, and every layout the page forced (an inspector swap after a commit,
 * a row patch, Split.js measuring its gutter) walked all of it: 22 s per
 * field commit, 5 s per Ctrl+Z, 14 s to open a pulse, measured in real
 * Chrome at 91c8aae. The 50-row view did the same work in ~1 s.
 *
 * What: when the server renders "All" for a big library it ships the rows as
 * DATA (<script id="pulses-vdata">: [path, digest, text-html] per row, the
 * thumbnails left as sized placeholders) and an empty <tbody
 * data-pulses-virtual>. This module keeps that model and renders only the
 * rows near the viewport, between two spacer rows sized from measured row
 * heights (an estimate for rows never rendered). Everything else is the
 * table's own markup, row for row -- _pulse_row.html renders each one.
 *
 *   - thumbnails are drawn for the rows on screen (POST /pulses/sparks),
 *     and redrawn when the chip moves (the server's stamp) or the row's text
 *     changed;
 *   - a value change (`pulses-rows-changed`) patches the named rows through
 *     /pulse/row, exactly as the paged table does;
 *   - any other change (`pulses-changed`: create, delete, rename, a pull,
 *     Take live, another window) re-lists the filter's (path, digest) pairs
 *     (GET /pulses/vids) and fetches only the rows whose digest moved (POST
 *     /pulses/vrows) -- the rows on screen keep their place;
 *   - a search / tab / owner pick swaps the table as before, asking for the
 *     digests only (vids=1) once the model holds the text;
 *   - sort, keyboard row navigation and the compare checkboxes work on the
 *     MODEL (app.js hands them over while this view is active), so they see
 *     every row, not the ~50 in the DOM.
 *
 * The paged views (25/50/100) and a small library's "All" never reach this
 * file's rendering: no #pulses-vdata, no virtual tbody, nothing changes.
 */
window.PulsesVT = (function () {
    'use strict';

    var OVERSCAN = 12;          // rows rendered past each edge of the viewport
    var DEFAULT_H = 37;         // px per row until real rows are measured
    var SPARK_BATCH = 120;      // thumbnails per /pulses/sparks request
    var SPARK_DELAY_MS = 60;    // coalesce a scroll's worth of thumbnail asks
    var NCOLS = 9;

    // ── the model: survives every swap of the table (keyed by path) ──────
    // entry = {v, h, s, sv, se, sn, keys}
    //   v  digest of the row's text      h  the text HTML (thumbnail placeholder)
    //   s  the waveform cell's innerHTML  sv the text digest it was drawn with
    //   se the thumbnail epoch it was drawn in   sn bumps when s changes
    //   keys  {v, cols} sort keys read off the text
    var cache = new Map();
    var stamp = null;           // the server stamp the thumbnails were drawn at
    var epoch = 0;              // bumps when that stamp moves: every thumbnail is stale
    var checked = new Set();    // the compare selection (paths)
    var st = null;              // the active view (null = no virtual table)
    var pendingSort = null;     // a sort asked for before init (app.js's re-apply)
    var refreshGen = 0;
    var rowGen = {};
    var tpl = document.createElement('template');

    function active() {
        return !!(st && st.tbody && st.tbody.isConnected);
    }

    function hasModel() { return cache.size > 0; }

    // ── helpers ─────────────────────────────────────────────────────────
    function closestScroller(el) {
        var n = el ? el.parentElement : null;
        while (n && n !== document.body && n !== document.documentElement) {
            var cs = getComputedStyle(n);
            if (/(auto|scroll)/.test(cs.overflowY)) return n;
            n = n.parentElement;
        }
        return document.scrollingElement || document.documentElement;
    }

    function parseRows(html) {
        tpl.innerHTML = html;
        return Array.prototype.filter.call(tpl.content.children, function (n) {
            return n.tagName === 'TR';
        });
    }

    /* A full row (as /pulse/row or /pulses/sparks render it) -> [text html
       with the placeholder, waveform cell innerHTML]. The placeholder is the
       one _pulse_row.html renders with lazy_spark, so the text re-renders
       exactly as the server's own text for that row. */
    function splitFull(html) {
        var tr = parseRows(String(html || '').trim())[0];
        if (!tr) return null;
        var td = tr.querySelector('td.pulse-spark-cell');
        var s = td ? td.innerHTML : '';
        if (td && !td.querySelector('.pulse-alias-target')) {
            td.setAttribute('data-spark-lazy', '1');
            td.innerHTML = '<span class="pulse-spark-pending" aria-hidden="true"></span>';
        }
        return [tr.outerHTML, s];
    }

    function liveFilter() {
        var inp = document.querySelector('.table-filter input[name="q"]');
        var tab = document.querySelector('#pulse-channel-tabs a.active');
        var ch = '';
        if (tab) {
            var m = (tab.getAttribute('hx-get') || '').match(/channel=([^&]+)/);
            if (m) ch = decodeURIComponent(m[1]);
        }
        var own = document.getElementById('pulses-owner-pick');
        return { q: inp ? inp.value.trim() : '', channel: ch,
                 owner: own ? (own.value || '').trim() : '' };
    }

    function filterQs(f) {
        return (f.channel ? '&channel=' + encodeURIComponent(f.channel) : '')
             + (f.q ? '&q=' + encodeURIComponent(f.q) : '')
             + (f.owner ? '&owner=' + encodeURIComponent(f.owner) : '');
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'HX-Request': 'true' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); });
    }

    function busy(on) {
        var w = document.getElementById('pulses-rows-wrap');
        if (!w) return;
        if (on) { w.classList.add('htmx-request'); w._pvtBusy = (w._pvtBusy || 0) + 1; }
        else {
            w._pvtBusy = Math.max(0, (w._pvtBusy || 1) - 1);
            if (!w._pvtBusy) w.classList.remove('htmx-request');
        }
    }

    function sparkFresh(e) {
        return e && e.s !== undefined && e.sv === e.v && e.se === epoch;
    }

    function setStamp(s) {
        if (s && s !== stamp) {
            if (stamp !== null) epoch++;
            stamp = s;
        }
    }

    function putText(p, v, h) {
        var e = cache.get(p);
        if (e && e.v === v) { if (h) e.h = h; return e; }
        // a row whose text moved never shows the waveform of the old text:
        // its cell waits for the new thumbnail (a placeholder, not a stale curve)
        var n = { v: v, h: h, s: undefined, sv: null, se: -1,
                  sn: e ? (e.sn || 0) + 1 : 0, keys: null };
        cache.set(p, n);
        return n;
    }

    // ── the view ────────────────────────────────────────────────────────
    function newState(tbody, data) {
        var table = tbody.closest('table');
        var s = {
            tbody: tbody, table: table, scrollEl: closestScroller(table),
            view: [], pos: new Map(), empty: data.empty || '',
            hmap: new Map(), est: DEFAULT_H, pre: null, preDirty: true,
            first: -1, last: -1, rendered: new Map(), sel: null,
            raf: 0, sparkTimer: 0, sparkWant: new Set(), sparkBusy: false,
            pressing: false, pendingFull: false, sort: null, n: data.n || 0
        };
        return s;
    }

    function setView(paths) {
        st.view = paths;
        st.pos = new Map();
        for (var i = 0; i < paths.length; i++) st.pos.set(paths[i], i);
        st.preDirty = true;
    }

    function heightOf(p) {
        var h = st.hmap.get(p);
        return h === undefined ? st.est : h;
    }

    function prefix() {
        if (!st.preDirty && st.pre && st.pre.length === st.view.length + 1) return st.pre;
        var n = st.view.length, pre = new Float64Array(n + 1);
        for (var i = 0; i < n; i++) pre[i + 1] = pre[i] + heightOf(st.view[i]);
        st.pre = pre; st.preDirty = false;
        return pre;
    }

    function indexAt(pre, y) {
        // the row whose [top, bottom) holds y
        var lo = 0, hi = pre.length - 2;
        if (hi < 0) return 0;
        while (lo < hi) {
            var mid = (lo + hi + 1) >> 1;
            if (pre[mid] <= y) lo = mid; else hi = mid - 1;
        }
        return lo;
    }

    /* How far into the row list the scroller is and how much of it shows --
       measured from the tbody's own box (the top spacer lives inside it), so
       it is right for any scroller and any content above the table. */
    function metrics() {
        var el = st.scrollEl, tb = st.tbody;
        var docScroller = (el === document.scrollingElement || el === document.documentElement);
        var elTop = docScroller ? 0 : el.getBoundingClientRect().top;
        var vh = docScroller ? window.innerHeight : el.clientHeight;
        var delta = tb.getBoundingClientRect().top - elTop;
        if (!vh) vh = 1000;           // not laid out yet (hidden pane): a screenful
        return { top: Math.max(0, -delta), viewport: Math.max(0, vh - Math.max(0, delta)),
                 delta: delta, elTop: elTop };
    }

    function spacer(cls) {
        var tr = document.createElement('tr');
        tr.className = 'pulse-vpad ' + cls;
        tr.setAttribute('aria-hidden', 'true');
        var td = document.createElement('td');
        td.colSpan = NCOLS;
        tr.appendChild(td);
        return tr;
    }

    function ensureSpacers() {
        var tb = st.tbody;
        if (!st.topPad || st.topPad.parentNode !== tb) {
            st.topPad = spacer('pulse-vpad-top');
            tb.insertBefore(st.topPad, tb.firstChild);
        }
        if (!st.botPad || st.botPad.parentNode !== tb) {
            st.botPad = spacer('pulse-vpad-bottom');
            tb.appendChild(st.botPad);
        }
    }

    function setPad(tr, h) {
        var px = Math.max(0, Math.round(h)) + 'px';
        if (tr.firstChild.style.height !== px) tr.firstChild.style.height = px;
        tr.hidden = !h;
    }

    function decorate(tr, p) {
        var e = cache.get(p);
        tr._pvt = { v: e ? e.v : null, sn: e ? e.sn : -1 };
        if (e && e.s !== undefined) {
            var td = tr.querySelector('td.pulse-spark-cell[data-spark-lazy]');
            if (td) { td.innerHTML = e.s; td.removeAttribute('data-spark-lazy'); }
        }
        var chk = tr.querySelector('.pulse-sel-chk');
        if (chk) chk.checked = checked.has(p);
        if (st.sel === p) tr.classList.add('row-selected');
        if (e && !sparkFresh(e) && tr.querySelector('td.pulse-spark-cell')
                && !tr.querySelector('.pulse-alias-target')) st.sparkWant.add(p);
    }

    function buildRows(paths) {
        // ONE parse for every new row of this render
        var html = [];
        paths.forEach(function (p) {
            var e = cache.get(p);
            html.push(e && e.h ? e.h
                : '<tr class="pulse-vrow-pending" data-vpath="' + p.replace(/[&"<>]/g, function (c) {
                    return { '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' }[c]; })
                  + '"><td colspan="' + NCOLS + '">&nbsp;</td></tr>');
        });
        var trs = parseRows(html.join(''));
        var out = new Map();
        for (var i = 0; i < paths.length && i < trs.length; i++) {
            var tr = trs[i];
            tr._pvtPath = paths[i];
            decorate(tr, paths[i]);
            out.set(paths[i], tr);
        }
        return out;
    }

    function processRows(trs) {
        if (!window.htmx) return;
        trs.forEach(function (tr) {
            if (tr.classList.contains('pulse-vrow-pending')) return;
            try { window.htmx.process(tr); } catch (e) {}
        });
    }

    function anchorOf(m) {
        // the first rendered row whose bottom is below the top of the view
        var best = null;
        st.rendered.forEach(function (tr, p) {
            if (best) return;
            var r = tr.getBoundingClientRect();
            if (r.bottom - m.elTop > 0 && r.height) best = { p: p, y: r.top - m.elTop };
        });
        return best;
    }

    function measure() {
        var changed = false, hs = [];
        st.rendered.forEach(function (tr, p) {
            if (tr.classList.contains('pulse-vrow-pending')) return;
            var h = tr.offsetHeight;
            if (!h) return;
            hs.push(h);
            if (st.hmap.get(p) !== h) { st.hmap.set(p, h); changed = true; }
        });
        if (hs.length && !st.measuredOnce) {
            hs.sort(function (a, b) { return a - b; });
            var med = hs[hs.length >> 1];
            if (med && Math.abs(med - st.est) >= 1) { st.est = med; changed = true; }
            st.measuredOnce = true;
        }
        if (changed) st.preDirty = true;
        return changed;
    }

    /* The rendered table sized its columns to ALL its rows; this one renders
       ~50. The server names the few rows with the longest text per column
       (`wide`), and they are laid out too -- in their own tbody, with
       `visibility: collapse`: no height, no pointer, no path, but counted by
       the table's column widths before app.js freezes them. Without it a long
       amplitude further down wrapped onto two lines. */
    function renderSizer() {
        if (!st || !st.table) return;
        var body = st.table.querySelector('tbody.pulse-vsizer');
        if (!body) {
            body = document.createElement('tbody');
            body.className = 'pulse-vsizer';
            body.setAttribute('aria-hidden', 'true');
            st.table.appendChild(body);
        }
        var html = (st.wide || []).map(function (p) {
            var e = cache.get(p); return e && e.h ? e.h : '';
        }).join('');
        var trs = parseRows(html);
        trs.forEach(function (tr) {
            ['data-pulse-path', 'hx-get', 'hx-target', 'hx-swap', 'hx-indicator', 'title', 'class']
                .forEach(function (a) { tr.removeAttribute(a); });
            var c0 = tr.cells[0];
            if (c0) { c0.innerHTML = ''; c0.removeAttribute('onclick'); }
            Array.prototype.forEach.call(tr.querySelectorAll('[data-path], [title]'), function (n) {
                n.removeAttribute('data-path'); n.removeAttribute('title');
            });
        });
        while (body.firstChild) body.removeChild(body.firstChild);
        trs.forEach(function (tr) { body.appendChild(tr); });
        tpl.innerHTML = '';
    }

    function empty() {
        st.rendered.forEach(function (tr) { if (tr.parentNode) tr.parentNode.removeChild(tr); });
        st.rendered = new Map();
        st.first = st.last = 0;
        var tb = st.tbody;
        while (tb.firstChild) tb.removeChild(tb.firstChild);
        st.topPad = st.botPad = null;
        if (st.empty) tb.innerHTML = st.empty;
    }

    /* Render the rows near the viewport. `full` rebuilds every row (the model
       changed under them); otherwise rows still in the window are KEPT --
       never moved -- and only the edges change, so the row under the pointer
       survives a scroll. */
    function render(full) {
        if (!active()) return;
        if (!st.view.length) { empty(); return; }
        if (full && st.pressing) { st.pendingFull = true; full = false; }
        var tb = st.tbody;
        if (tb.firstChild && !st.topPad) { while (tb.firstChild) tb.removeChild(tb.firstChild); }
        ensureSpacers();
        var m = metrics();
        var anchor = st.rendered.size ? anchorOf(m) : null;
        var pre = prefix();
        var n = st.view.length;
        var a = indexAt(pre, m.top), b = indexAt(pre, m.top + m.viewport);
        var first = Math.max(0, a - OVERSCAN), last = Math.min(n, b + 1 + OVERSCAN);
        if (!full && first === st.first && last === st.last) {
            // same window: a patched row may have changed height
            if (st.needMeasure) {
                st.needMeasure = false;
                if (measure()) {
                    pre = prefix();
                    setPad(st.topPad, pre[first]);
                    setPad(st.botPad, pre[n] - pre[last]);
                    reanchor(anchor, m);
                }
            }
            fillSparksSoon();
            return;
        }
        st.needMeasure = false;
        var want = st.view.slice(first, last);
        var wantSet = new Set(want);
        var old = st.rendered, keep = [], stale = [];
        // rows whose text moved since they were built are rebuilt
        old.forEach(function (tr, p) {
            var e = cache.get(p);
            var fresh = !full && wantSet.has(p) && tr._pvt && e && tr._pvt.v === e.v
                        && !tr.classList.contains('pulse-vrow-pending');
            if (fresh) keep.push(p); else stale.push(p);
        });
        // kept rows must keep their relative order, or it is a rebuild
        var keptOrder = want.filter(function (p) { return keep.indexOf(p) >= 0; });
        var domOrder = [];
        for (var c = st.topPad.nextSibling; c && c !== st.botPad; c = c.nextSibling) {
            if (c._pvtPath && keep.indexOf(c._pvtPath) >= 0) domOrder.push(c._pvtPath);
        }
        if (keptOrder.join('\u0000') !== domOrder.join('\u0000')) { stale = stale.concat(keep); keep = []; }
        var keepSet = new Set(keep);
        stale.forEach(function (p) { var tr = old.get(p); if (tr && tr.parentNode) tr.parentNode.removeChild(tr); });
        // any stray node between the pads (an empty-state row) goes too
        for (var x = st.topPad.nextSibling; x && x !== st.botPad;) {
            var nx = x.nextSibling;
            if (!x._pvtPath || !keepSet.has(x._pvtPath)) x.parentNode.removeChild(x);
            x = nx;
        }
        var need = want.filter(function (p) { return !keepSet.has(p); });
        var made = need.length ? buildRows(need) : new Map();
        var rendered = new Map();
        // walk the wanted order: insert each new row before the next kept one
        var cursor = st.topPad.nextSibling;
        want.forEach(function (p) {
            if (keepSet.has(p)) {
                var trk = old.get(p);
                rendered.set(p, trk);
                // an updated thumbnail on a kept row
                var e = cache.get(p);
                if (e && trk._pvt && trk._pvt.sn !== e.sn && e.s !== undefined) {
                    var td = trk.querySelector('td.pulse-spark-cell');
                    if (td && !td.querySelector('.pulse-alias-target')) {
                        td.innerHTML = e.s; td.removeAttribute('data-spark-lazy');
                    }
                    trk._pvt.sn = e.sn;
                }
                if (e && !sparkFresh(e) && !trk.querySelector('.pulse-alias-target')) st.sparkWant.add(p);
                cursor = trk.nextSibling;
                return;
            }
            var tr = made.get(p);
            if (!tr) return;
            tr._pvtPath = p;
            tb.insertBefore(tr, cursor);
            rendered.set(p, tr);
        });
        st.rendered = rendered;
        st.first = first; st.last = last;
        processRows(Array.from(made.values()));
        setPad(st.topPad, pre[first]);
        setPad(st.botPad, pre[n] - pre[last]);
        // measured heights re-derive the spacers; the first visible row stays put
        if (measure()) {
            pre = prefix();
            setPad(st.topPad, pre[first]);
            setPad(st.botPad, pre[n] - pre[last]);
        }
        reanchor(anchor, m);
        fillSparksSoon();
    }

    function reanchor(anchor, m) {
        if (!anchor) return;
        var atr = st.rendered.get(anchor.p);
        if (!atr) return;
        var dy = (atr.getBoundingClientRect().top - m.elTop) - anchor.y;
        if (Math.abs(dy) >= 1) st.scrollEl.scrollTop += dy;
    }

    function schedule(full) {
        if (!st) return;
        if (full) st.wantFull = true;
        if (st.raf) return;
        st.raf = requestAnimationFrame(function () {
            if (!st) return;
            st.raf = 0;
            var f = st.wantFull; st.wantFull = false;
            render(!!f);
        });
    }

    function onScroll() {
        if (!active()) { detach(this); return; }
        schedule(false);
    }

    // ── thumbnails ──────────────────────────────────────────────────────
    function fillSparksSoon() {
        if (!st || !st.sparkWant.size || st.sparkTimer) return;
        st.sparkTimer = setTimeout(fetchSparks, SPARK_DELAY_MS);
    }

    function fetchSparks() {
        if (!active()) return;
        st.sparkTimer = 0;
        if (st.sparkBusy) { fillSparksSoon(); return; }
        // only rows still on screen
        var paths = [];
        st.sparkWant.forEach(function (p) { if (st.rendered.has(p) && paths.length < SPARK_BATCH) paths.push(p); });
        paths.forEach(function (p) { st.sparkWant.delete(p); });
        st.sparkWant.forEach(function (p) { if (!st.rendered.has(p)) st.sparkWant.delete(p); });
        if (!paths.length) return;
        var my = st, ep = epoch;
        my.sparkBusy = true;
        postJson('/pulses/sparks', { paths: paths }).then(function (d) {
            my.sparkBusy = false;
            if (!d || !d.ok) return;
            setStamp(d.stamp);
            var textMoved = false;
            Object.keys(d.rows || {}).forEach(function (p) {
                var pair = d.rows[p];
                var sp = splitFull(pair[1]);
                if (!sp) return;
                var e = cache.get(p);
                if (!e || e.v !== pair[0]) { e = putText(p, pair[0], sp[0]); textMoved = true; }
                e.s = sp[1]; e.sv = pair[0]; e.se = (ep === epoch) ? epoch : -1; e.sn = (e.sn || 0) + 1;
            });
            if (st === my && active()) {
                // warming lab thumbnails: one more ask once their own code drew them
                var warm = [];
                (d.warming || []).forEach(function (p) {
                    var e = cache.get(p);
                    if (e && !e._warmAsked) { e._warmAsked = true; warm.push(p); }
                });
                if (warm.length) setTimeout(function () {
                    if (st !== my || !active()) return;
                    warm.forEach(function (p) { var e = cache.get(p); if (e) e.se = -1; });
                    refreshRendered();
                }, 4000);
                if (textMoved) st.preDirty = true;
                refreshRendered();
            }
            if (my.sparkWant.size) fillSparksSoon();
        }, function () { my.sparkBusy = false; });
    }

    /* Bring the rendered rows up to the model in place: a row whose TEXT moved
       is rebuilt, a row whose thumbnail landed gets just its waveform cell --
       nothing else is touched, so the row under the pointer stays. */
    function refreshRendered() {
        if (!active()) return;
        var rebuilt = [];
        st.rendered.forEach(function (tr, p) {
            var e = cache.get(p);
            if (!e || !tr._pvt) return;
            if (tr._pvt.v !== e.v || tr.classList.contains('pulse-vrow-pending')) {
                if (!e.h) return;
                var fresh = buildRows([p]).get(p);
                if (!fresh || !tr.parentNode) return;
                tr.parentNode.replaceChild(fresh, tr);
                st.rendered.set(p, fresh);
                rebuilt.push(fresh);
                return;
            }
            if (tr._pvt.sn !== e.sn && e.s !== undefined) {
                var td = tr.querySelector('td.pulse-spark-cell');
                if (td && !td.querySelector('.pulse-alias-target')) {
                    td.innerHTML = e.s; td.removeAttribute('data-spark-lazy');
                }
                tr._pvt.sn = e.sn;
            }
            if (!sparkFresh(e) && !tr.querySelector('.pulse-alias-target')) st.sparkWant.add(p);
        });
        processRows(rebuilt);
        st.needMeasure = true;
        schedule(false);
    }

    // ── sort (app.js's header click hands it here) ──────────────────────
    var _coll = null;
    function collator() {
        if (!_coll) _coll = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
        return _coll;
    }

    /* The sort text of every row in the view, read off the row's own cells
       exactly as the rendered table's sort reads them (data-sort, else the
       trimmed textContent), once per row version. */
    function ensureKeys(paths) {
        var need = paths.filter(function (p) {
            var e = cache.get(p);
            return e && e.h && !(e.keys && e.keys.v === e.v);
        });
        for (var i = 0; i < need.length; i += 1500) {
            var chunk = need.slice(i, i + 1500);
            var trs = parseRows(chunk.map(function (p) { return cache.get(p).h; }).join(''));
            for (var j = 0; j < chunk.length && j < trs.length; j++) {
                var cols = {};
                var cells = trs[j].cells;
                for (var k = 0; k < cells.length; k++) {
                    cols[k] = cells[k].getAttribute('data-sort') || cells[k].textContent.trim();
                }
                var e = cache.get(chunk[j]);
                e.keys = { v: e.v, cols: cols };
            }
        }
        tpl.innerHTML = '';
    }

    function applySort(spec) {
        if (!spec || !st) return;
        var view = st.view.slice();
        ensureKeys(view);
        var col = spec.col, isNum = spec.isNum, dir = spec.dir, coll = collator();
        function txt(p) { var e = cache.get(p); return (e && e.keys && e.keys.cols[col] !== undefined) ? e.keys.cols[col] : ''; }
        view.sort(function (a, b) {
            var aText = txt(a), bText = txt(b);
            if (isNum) {
                var aVal = parseFloat(aText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                var bVal = parseFloat(bText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                if (aText === '-' || aText === '') aVal = -Infinity;
                if (bText === '-' || bText === '') bVal = -Infinity;
                return dir === 'asc' ? aVal - bVal : bVal - aVal;
            }
            return dir === 'asc' ? coll.compare(aText, bText) : coll.compare(bText, aText);
        });
        setView(view);
        st.sort = spec;
    }

    function sort(table, col, isNum, dir) {
        var spec = { col: col, isNum: isNum, dir: dir };
        if (active() && st.table === table) {
            applySort(spec);
            render(true);
        } else {
            pendingSort = { table: table, spec: spec };
        }
    }

    // ── keyboard (app.js's arrow-key handler hands it here) ─────────────
    function scrollToIndex(i) {
        var pre = prefix();
        var m = metrics();
        var top = pre[i], bot = pre[i + 1];
        var dy = 0;
        if (top < m.top) dy = top - m.top;
        else if (bot > m.top + m.viewport) dy = bot - (m.top + m.viewport);
        if (dy) st.scrollEl.scrollTop += dy;
        render(false);
    }

    function key(k) {
        if (!active() || !st.view.length) return false;
        var idx = st.sel !== null && st.pos.has(st.sel) ? st.pos.get(st.sel) : -1;
        if (k === 'Enter') {
            if (idx < 0) return false;
            scrollToIndex(idx);
            var tr = st.rendered.get(st.sel);
            if (tr && tr.scrollIntoView) { tr.scrollIntoView({ block: 'nearest' }); render(false); }
            tr = st.rendered.get(st.sel);
            if (tr) tr.click();
            return true;
        }
        if (k === 'ArrowDown') idx = Math.min(idx + 1, st.view.length - 1);
        else if (k === 'ArrowUp') idx = Math.max(idx - 1, 0);
        else return false;
        var prev = st.sel !== null ? st.rendered.get(st.sel) : null;
        if (prev) prev.classList.remove('row-selected');
        st.sel = st.view[idx];
        scrollToIndex(idx);
        var cur = st.rendered.get(st.sel);
        if (cur) {
            cur.classList.add('row-selected');
            // the model got it rendered; the browser places it exactly, as the
            // rendered table's own handler does (block: 'nearest')
            if (cur.scrollIntoView) { cur.scrollIntoView({ block: 'nearest' }); render(false); }
        }
        return true;
    }

    // ── the compare selection (app.js's pulseSelChanged hands it here) ──
    function check(el, cap) {
        var p = el && el.getAttribute ? el.getAttribute('data-path') : null;
        var over = false;
        if (p) {
            if (el.checked) checked.add(p); else checked.delete(p);
            if (checked.size > cap) { el.checked = false; checked.delete(p); over = true; }
        }
        return { paths: checkedPaths(), over: over };
    }

    function checkedPaths() {
        var out = Array.from(checked);
        if (st && st.pos) {
            out.sort(function (a, b) {
                var ia = st.pos.has(a) ? st.pos.get(a) : 1e9, ib = st.pos.has(b) ? st.pos.get(b) : 1e9;
                return ia - ib;
            });
        }
        return out;
    }

    function clearChecked() {
        checked.clear();
        if (st) st.rendered.forEach(function (tr) {
            var c = tr.querySelector('.pulse-sel-chk'); if (c) c.checked = false;
        });
    }

    // ── fetching text ───────────────────────────────────────────────────
    function fetchRows(paths) {
        if (!paths.length) return Promise.resolve();
        return postJson('/pulses/vrows', { paths: paths }).then(function (d) {
            if (!d || !d.ok) return;
            (d.rows || []).forEach(function (r) { putText(r[0], r[1], r[2]); });
        });
    }

    function adopt(data) {
        // [path, digest, html?] per row: the text is taken, or checked against
        // what the model holds; the rows it cannot vouch for are returned
        var paths = [], miss = [];
        (data.rows || []).forEach(function (r) {
            var p = r[0], v = r[1];
            paths.push(p);
            if (r.length > 2) { putText(p, v, r[2]); return; }
            var e = cache.get(p);
            if (!e || e.v !== v || !e.h) miss.push(p);
            if (!e) cache.set(p, { v: null, h: null, s: undefined, sv: null, se: -1, sn: 0, keys: null });
        });
        return { paths: paths, miss: miss };
    }

    function updateCounts(n) {
        var tot = document.getElementById('pulses-total');
        if (tot) tot.textContent = '(' + n + ')';
        var info = document.querySelector('#pulses-rows-wrap .page-info');
        if (info) {
            var t = info.textContent, nt = t.replace(/\(\d+ total\)/, '(' + n + ' total)');
            if (nt !== t) info.textContent = nt;
        }
    }

    function detach(el) {
        try { (el || (st && st.scrollEl)).removeEventListener('scroll', onScroll); } catch (e) {}
        if (st && st.ro && !active()) { try { st.ro.disconnect(); } catch (e) {} st.ro = null; }
    }

    function onPointerDown(evt) {
        if (!st || !st.tbody.contains(evt.target)) return;
        st.pressing = true;
        clearTimeout(st.pressTimer);
        st.pressTimer = setTimeout(release, 1500);
    }
    function release() {
        if (!st || !st.pressing) return;
        st.pressing = false;
        clearTimeout(st.pressTimer);
        if (st.pendingFull) {
            st.pendingFull = false;
            // after the click this press becomes has been dispatched
            setTimeout(function () { schedule(true); }, 0);
        }
    }
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointerup', release, true);
    document.addEventListener('pointercancel', release, true);

    /* Take over a freshly swapped (or first-painted) virtual table. Returns
       true when this root holds one. Idempotent per payload node. */
    function init(root) {
        var scope = root && root.querySelector ? root : document;
        var tbody = scope.querySelector('tbody[data-pulses-virtual]')
                    || document.querySelector('#pulses-table tbody[data-pulses-virtual]');
        var node = document.getElementById('pulses-vdata');
        if (!tbody || !node) {
            if (st && !active()) { detach(); st = null; }
            return false;
        }
        if (node._pvtData && st && st.tbody === tbody) return true;
        var data = node._pvtData;
        if (!data) {
            try { data = JSON.parse(node.textContent || '{}'); } catch (e) { return false; }
            node._pvtData = data;
            node.textContent = '';             // the model holds it now
        }
        if (st) { detach(); if (st.ro) try { st.ro.disconnect(); } catch (e) {} }
        if (cache.size > 60000) cache.clear();
        setStamp(data.stamp);
        var got = adopt(data);
        st = newState(tbody, data);
        st.wide = data.wide || [];
        setView(got.paths);
        // prune the compare selection to what this table can show
        checked.forEach(function (p) { if (!st.pos.has(p)) checked.delete(p); });
        if (pendingSort && pendingSort.table === st.table) applySort(pendingSort.spec);
        else if (st.table && st.table.getAttribute('data-sorted-col') !== null
                 && st.table.getAttribute('data-sorted-dir')) {
            var th = st.table.querySelector('th.sortable[data-col="' + st.table.getAttribute('data-sorted-col') + '"]');
            applySort({ col: parseInt(st.table.getAttribute('data-sorted-col'), 10),
                        isNum: !!(th && th.dataset.type === 'num'),
                        dir: st.table.getAttribute('data-sorted-dir') });
        }
        pendingSort = null;
        st.scrollEl.addEventListener('scroll', onScroll, { passive: true });
        if (typeof ResizeObserver === 'function') {
            var my = st;
            st.ro = new ResizeObserver(function () {
                if (st !== my) return;
                if (!active()) { detach(); return; }
                schedule(false);
            });
            try { st.ro.observe(st.scrollEl); } catch (e) {}
        }
        render(true);
        renderSizer();
        if (got.miss.length) {
            var mine = st;
            busy(true);
            fetchRows(got.miss).then(function () {
                busy(false);
                if (st !== mine) return;
                // the sort read those rows' keys as blank: read it again
                if (st.sort) applySort(st.sort);
                render(true);
                renderSizer();
            }, function () { busy(false); if (st === mine) refetchWhole(); });
        }
        return true;
    }

    /* The last resort when the model cannot be brought up to date (a row
       fetch failed): drop it and let the table re-fetch whole, text and all. */
    function refetchWhole() {
        var w = document.getElementById('pulses-rows-wrap');
        if (!w || !window.htmx) return;
        cache.clear();
        var pp = (String(w.getAttribute('hx-get') || '').match(/per_page=(\d+)/) || [])[1];
        window.htmx.ajax('GET', '/pulses?rows=1' + (pp ? '&per_page=' + pp : ''),
                         { source: w, target: w, swap: 'innerHTML' });
    }

    /* A change the rows cannot patch one by one (`pulses-changed`): re-list
       the filter's digests, fetch the rows whose digest moved, and re-render
       in place -- the table is not swapped, the rows on screen keep their
       place, and nothing is shown until the new rows are in. */
    function refresh() {
        if (!active()) return false;
        var gen = ++refreshGen, my = st;
        var f = liveFilter();
        busy(true);
        fetch('/pulses/vids?' + filterQs(f).replace(/^&/, ''), {
            credentials: 'same-origin', headers: { 'HX-Request': 'true' }
        }).then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); })
          .then(function (d) {
            if (gen !== refreshGen || st !== my) return null;
            var got = adopt(d);
            return fetchRows(got.miss).then(function () {
                if (gen !== refreshGen || st !== my || !active()) return;
                setStamp(d.stamp);
                if (d.empty) st.empty = d.empty;
                if (d.wide) st.wide = d.wide;
                setView(got.paths);
                if (st.sort) applySort(st.sort);
                checked.forEach(function (p) { if (!st.pos.has(p)) checked.delete(p); });
                if (st.sel !== null && !st.pos.has(st.sel)) st.sel = null;
                st.n = d.n;
                updateCounts(d.n);
                // the compare bar follows a selection a delete pruned
                if (window.pulseSelChanged) window.pulseSelChanged(null);
                render(true);
                renderSizer();
            });
        }).then(function () { busy(false); }, function () {
            busy(false);
            // the re-list failed: the table re-fetches whole
            if (gen === refreshGen && st === my) refetchWhole();
        });
        return true;
    }

    /* `pulses-rows-changed`: the value change named its rows; each is
       re-rendered through /pulse/row (the page's filter rides along -- a row
       that stopped matching answers 204 and leaves). A named row the view does
       not hold means the table itself is stale: the refresh above. */
    function rowsChanged(paths) {
        if (!active()) return false;
        var f = liveFilter(), seen = {}, missing = false, my = st;
        paths.forEach(function (p) {
            if (!p || seen[p]) return;
            seen[p] = 1;
            if (!st.pos.has(p)) { missing = true; return; }
            var gen = (rowGen[p] = (rowGen[p] || 0) + 1);
            busy(true);
            fetch('/pulse/row?path=' + encodeURIComponent(p) + '&vt=1' + filterQs(f), {
                credentials: 'same-origin', headers: { 'HX-Request': 'true' }
            }).then(function (r) {
                if (r.status === 204) return { gone: true };
                if (!r.ok) return Promise.reject(new Error('HTTP ' + r.status));
                var ver = r.headers.get('X-Pulse-Ver');
                return r.text().then(function (t) { return { html: t, ver: ver }; });
            }).then(function (res) { busy(false); return res; },
                    function (err) { busy(false); throw err; })
              .then(function (res) {
                if (rowGen[p] !== gen || st !== my || !active()) return;
                if (res.gone) {
                    var v = st.view.filter(function (x) { return x !== p; });
                    setView(v);
                    st.n = Math.max(0, st.n - 1);
                    updateCounts(st.n);
                    render(true);
                    return;
                }
                var sp = splitFull(res.html);
                if (!sp || !res.ver) { refresh(); return; }
                var e = putText(p, res.ver, sp[0]);
                e.h = sp[0];
                e.s = sp[1]; e.sv = res.ver; e.se = epoch; e.sn = (e.sn || 0) + 1;
                var tr = st.rendered.get(p);
                if (tr) {
                    // the whole row: its text may have moved (a new digest)
                    var fresh = buildRows([p]).get(p);
                    tr.parentNode.replaceChild(fresh, tr);
                    st.rendered.set(p, fresh);
                    processRows([fresh]);
                    // one measure for a burst of patched rows, next frame
                    st.needMeasure = true;
                    schedule(false);
                }
            }).catch(function () { if (st === my) refresh(); });
        });
        if (missing) refresh();
        return true;
    }

    return {
        active: active,
        hasModel: hasModel,
        init: init,
        refresh: refresh,
        rowsChanged: rowsChanged,
        sort: sort,
        key: key,
        check: check,
        checkedPaths: checkedPaths,
        clearChecked: clearChecked,
        // for the selfcheck / journeys
        _scrollToPath: function (p) {
            if (!active() || !st.pos.has(p)) return false;
            // the row's top to the scroller's top (delta = the tbody's offset)
            var pre = prefix(), m = metrics();
            st.scrollEl.scrollTop += pre[st.pos.get(p)] + m.delta;
            render(false);
            return true;
        },
        _state: function () { return st; },
        _cache: cache,
        _render: render
    };
})();

/* htmx's history restore (Back into /pulses served from its own cache) puts
   back a SNAPSHOT of the table -- the rows that were rendered when the user
   left, over a payload this view had already consumed -- and fires no swap
   event. That snapshot is neither current nor alive (nothing re-renders it on
   scroll), so the rows are re-fetched through the table's own request; the
   model still holds their text, so it is the digest list only. */
document.addEventListener('htmx:historyRestore', function () {
    setTimeout(function () {
        var tb = document.querySelector('#pulses-table tbody[data-pulses-virtual]');
        var w = document.getElementById('pulses-rows-wrap');
        if (!tb || !w || !window.htmx) return;
        var s = window.PulsesVT._state();
        if (s && s.tbody === tb && window.PulsesVT.active()) return;
        var pp = (String(w.getAttribute('hx-get') || '').match(/per_page=(\d+)/) || [])[1];
        window.htmx.ajax('GET', '/pulses?rows=1' + (pp ? '&per_page=' + pp : ''),
                         { source: w, target: w, swap: 'innerHTML' });
    }, 0);
});

/* The table's own `pulses-changed` re-fetch (hx-trigger on #pulses-rows-wrap,
   debounced 400 ms by htmx) becomes the in-place refresh while the virtual
   view is up: htmx asks `htmx:confirm` right before it would send, and a
   cancelled confirm sends nothing. A search, a tab, an owner pick and
   PaneState's re-apply are not `pulses-changed` and swap the table as before. */
document.addEventListener('htmx:confirm', function (evt) {
    var d = evt.detail;
    if (!d || !d.elt || d.elt.id !== 'pulses-rows-wrap') return;
    var te = d.triggeringEvent;
    if (!te || te.type !== 'pulses-changed') return;
    if (!window.PulsesVT.active()) return;
    evt.preventDefault();
    window.PulsesVT.refresh();
});
