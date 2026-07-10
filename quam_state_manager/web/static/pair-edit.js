/* Pair live-edit grid (#bulk-pair-table) — stacked below the qubit table on /bulk.
 *
 * Qubit pairs carry lab-specific, gate-dependent schemas (CZ flux variants, CR,
 * custom macros, nested flux/coupler pulses), so the columns are SERVER-DERIVED
 * (core/pair_columns.py) — no hardcoded gate or leaf names. This module is a
 * focused, ISOLATED clone of the qubit grid's behaviour (dirty tracking, atomic
 * apply, search, sort, resize, column visibility, shared-port linking) that
 * commits through the SAME /field/edit-batch endpoint and reuses the SAME .bulk-*
 * CSS. It is deliberately kept SEPARATE from bulk-edit.js so the loved qubit
 * table's code is never touched (zero regression risk).
 *
 * The single shared surface is the #bulk-search box: this module adds its own
 * listener so one search filters BOTH tables (each classifies tokens against its
 * own columns/ids/values). Read-only cells (runtime self-refs, list/confusion
 * badges, blanks) render as .bulk-cell-ro readonly inputs — they join search/sort
 * but can never be dirty, so apply naturally skips them. Font is inherited from
 * #bulk-panel (set globally by bulk-edit.js), so no font code lives here.
 */
(function () {
    'use strict';

    var HIDE_KEY = 'quam_bulk_hidden_cols_pair';
    var WIDTH_KEY = 'quam_bulk_col_widths_pair';
    var SEARCH_KEY = 'quam_bulk_search';   // shared with the qubit grid
    var COLS = [];
    var sortKey = null, sortDir = 1;
    var _colWidths = {};
    var _resize = null, _resizeJustEnded = false;

    function table() { return document.getElementById('bulk-pair-table'); }
    function _cells(scope) { return Array.prototype.slice.call(scope.querySelectorAll('.bulk-cell')); }
    function _rows() { var t = table(); return t ? Array.prototype.slice.call(t.querySelectorAll('tbody tr')) : []; }
    function _isDirty(c) { return c.value !== c.getAttribute('data-orig'); }
    function _rowOf(c) { return c.closest('tr'); }
    function _grp(v) { return (window._groupDigits ? window._groupDigits(v) : String(v)); }
    function _num(s) { var n = parseFloat(String(s).replace(/,/g, '')); return isFinite(n) ? n : null; }
    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── persisted column visibility ──────────────────────────────────────────
    function _hidden() { try { return JSON.parse(localStorage.getItem(HIDE_KEY) || 'null'); } catch (e) { return null; } }
    function _saveHidden(set) { try { localStorage.setItem(HIDE_KEY, JSON.stringify(Array.from(set))); } catch (e) {} }
    function _hiddenSet() {
        var saved = _hidden();
        if (saved) return new Set(saved);
        var s = new Set();
        COLS.forEach(function (c) { if (!c.default_on) s.add(c.key); });
        return s;
    }
    function _applyColumnVisibility() {
        var t = table(); if (!t) return;
        var hide = _hiddenSet();
        t.querySelectorAll('[data-col-key]').forEach(function (el) {
            var k = el.getAttribute('data-col-key');
            if (k === '__id__') return;
            el.classList.toggle('bulk-col-hidden', hide.has(k));
        });
        applySearch();
    }

    // ── group-header band (spanning section headers) ─────────────────────────
    function _updateGroupHeader() {
        var t = table(); if (!t) return;
        var heads = t.querySelectorAll('.bulk-group-head');
        if (!heads.length) return;
        Array.prototype.forEach.call(heads, function (gh) {
            var sec = (gh.getAttribute('data-group') || '').replace(/"/g, '\\"');
            var n = 0;
            t.querySelectorAll('.bulk-col-head[data-section="' + sec + '"]').forEach(function (ch) {
                if (!ch.classList.contains('bulk-col-hidden') && !ch.classList.contains('bulk-search-hidden')) n++;
            });
            if (n > 0) { gh.colSpan = n; gh.classList.remove('bulk-col-hidden'); }
            else { gh.classList.add('bulk-col-hidden'); }
        });
        _updateStickyOffset();
    }
    function _updateStickyOffset() {
        var t = table(); if (!t) return;
        var grow = t.querySelector('.bulk-group-row');
        if (grow) t.style.setProperty('--bulk-grouphead-h', grow.offsetHeight + 'px');
    }

    // ── Property-Selection menu ──────────────────────────────────────────────
    function _buildColMenu() {
        var menu = document.getElementById('bulk-pair-colvis-menu');
        if (!menu) return;
        var hide = _hiddenSet();
        var bySection = {}, order = [];
        COLS.forEach(function (c) {
            if (!bySection[c.section]) { bySection[c.section] = []; order.push(c.section); }
            bySection[c.section].push(c);
        });
        var html = '<div class="bulk-colvis-actions">' +
            '<button type="button" class="btn-xs" onclick="BulkPairEdit.showAllColumns()">Show all</button>' +
            '<button type="button" class="btn-xs outline" onclick="BulkPairEdit.resetColumns()">Reset</button></div>';
        order.forEach(function (sec) {
            html += '<div class="bulk-colvis-sec">' + _esc(sec) + '</div>';
            bySection[sec].forEach(function (c) {
                var on = !hide.has(c.key);
                html += '<label class="bulk-colvis-item"><input type="checkbox" data-col-toggle="' + _esc(c.key) + '"' +
                    (on ? ' checked' : '') + '> ' + _esc(c.label) + (c.unit ? ' <span class="unit muted">(' + _esc(c.unit) + ')</span>' : '') + '</label>';
            });
        });
        menu.innerHTML = html;
        menu.querySelectorAll('[data-col-toggle]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                var hide = _hiddenSet();
                if (cb.checked) hide.delete(cb.getAttribute('data-col-toggle'));
                else hide.add(cb.getAttribute('data-col-toggle'));
                _saveHidden(hide);
                _applyColumnVisibility();
                _recomputeStats();
            });
        });
    }

    // ── search (pair-scoped: columns by label, rows by pair id, cells by value) ─
    function applySearch() {
        var t = table(); if (!t) return;
        var inp = document.getElementById('bulk-search');
        var q = inp ? inp.value.trim().toLowerCase() : '';
        var hide = _hiddenSet();
        var visCols = COLS.filter(function (c) { return !hide.has(c.key); });
        var tokens = q ? q.split(/\s+/) : [];

        var ids = _rows().map(function (r) { return (r.getAttribute('data-qubit') || '').toLowerCase(); });
        var tokInfo = tokens.map(function (tok) {
            var colHit = visCols.some(function (c) { return (c.label + ' ' + c.key + ' ' + c.section).toLowerCase().indexOf(tok) >= 0; });
            var idHit = ids.some(function (id) { return id.indexOf(tok) >= 0; });
            return { tok: tok, isCol: colHit, isId: idHit, isVal: !colHit && !idHit };
        });

        function colVisible(key, colCells) {
            for (var i = 0; i < tokInfo.length; i++) {
                var ti = tokInfo[i];
                if (ti.isCol && !ti.isId) {
                    var c = COLS.filter(function (x) { return x.key === key; })[0];
                    if (!c || (c.label + ' ' + c.key + ' ' + c.section).toLowerCase().indexOf(ti.tok) < 0) return false;
                } else if (ti.isVal) {
                    if (!colCells.some(function (h) { return h.indexOf(ti.tok) >= 0; })) return false;
                }
            }
            return true;
        }
        function rowVisible(id, rowHaystacks) {
            for (var i = 0; i < tokInfo.length; i++) {
                var ti = tokInfo[i];
                if (ti.isId && !ti.isCol) { if (id.indexOf(ti.tok) < 0) return false; }
                else if (ti.isVal) { if (!rowHaystacks.some(function (h) { return h.indexOf(ti.tok) >= 0; })) return false; }
            }
            return true;
        }

        var rows = _rows();
        var colHay = {};
        visCols.forEach(function (c) { colHay[c.key] = []; });
        var rowHay = rows.map(function (r) {
            var hs = [];
            _cells(r).forEach(function (cell) {
                var k = cell.closest('[data-col-key]').getAttribute('data-col-key');
                if (hide.has(k)) return;
                var disp = cell.value.toLowerCase();
                var h = disp + ' ' + disp.replace(/,/g, '');
                hs.push(h);
                if (colHay[k]) colHay[k].push(h);
            });
            return hs;
        });

        var colSearchHide = {};
        visCols.forEach(function (c) { colSearchHide[c.key] = !colVisible(c.key, colHay[c.key] || []); });
        t.querySelectorAll('th.bulk-col-head, td[data-col-key]').forEach(function (el) {
            var k = el.getAttribute('data-col-key');
            if (k === '__id__' || hide.has(k)) return;
            el.classList.toggle('bulk-search-hidden', !!colSearchHide[k]);
        });
        var shown = 0;
        rows.forEach(function (r, i) {
            var id = (r.getAttribute('data-qubit') || '').toLowerCase();
            var vis = rowVisible(id, rowHay[i]);
            r.classList.toggle('bulk-row-hidden', !vis);
            if (vis) shown++;
        });
        var cnt = document.getElementById('bulk-pair-search-count');
        if (cnt) cnt.textContent = q ? (shown + ' of ' + rows.length + ' pairs') : '';
        _updateGroupHeader();
    }

    // ── sort + per-column min/max ────────────────────────────────────────────
    function sort(key) {
        var t = table(); if (!t) return;
        var tbody = t.querySelector('tbody');
        if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }
        var rows = _rows();
        function keyOf(r) {
            if (key === '__id__') return (r.getAttribute('data-qubit') || '');
            var cell = r.querySelector('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] .bulk-cell');
            return cell ? cell.value : '';
        }
        rows.sort(function (a, b) {
            var va = keyOf(a), vb = keyOf(b);
            if (key === '__id__') return va < vb ? -sortDir : (va > vb ? sortDir : 0);
            var na = _num(va), nb = _num(vb);
            if (na === null && nb === null) return 0;
            if (na === null) return 1;
            if (nb === null) return -1;
            return na < nb ? -sortDir : (na > nb ? sortDir : 0);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        t.querySelectorAll('.bulk-sort-caret').forEach(function (el) { el.textContent = ''; });
        var th = t.querySelector('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] .bulk-sort-caret, [data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"].bulk-corner .bulk-sort-caret');
        if (th) th.textContent = sortDir > 0 ? ' ▲' : ' ▼';
    }

    function _recomputeStats() {
        var t = table(); if (!t) return;
        var hide = _hiddenSet();
        COLS.forEach(function (c) {
            var stat = t.querySelector('[data-col-stats="' + (window.CSS && CSS.escape ? CSS.escape(c.key) : c.key) + '"]');
            if (!stat) return;
            if (hide.has(c.key)) { stat.textContent = ''; return; }
            var cells = Array.prototype.slice.call(
                t.querySelectorAll('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(c.key) : c.key) + '"] .bulk-cell'));
            var nums = [];
            cells.forEach(function (cell) { var n = _num(cell.value); if (n !== null) nums.push(n); });
            cells.forEach(function (cell) { cell.classList.remove('cell-best', 'cell-worst'); });
            if (nums.length < 2) { stat.textContent = ''; return; }
            var mn = Math.min.apply(null, nums), mx = Math.max.apply(null, nums);
            stat.textContent = 'min ' + _grp(mn) + ' · max ' + _grp(mx);
            if (mn !== mx) cells.forEach(function (cell) {
                var n = _num(cell.value);
                if (n === mx) cell.classList.add('cell-best');
                else if (n === mn) cell.classList.add('cell-worst');
            });
        });
    }

    // ── dirty + apply ────────────────────────────────────────────────────────
    function _markCellDirty(cell) { cell.classList.toggle('dirty', _isDirty(cell)); }
    function _refreshRow(tr) {
        var dirty = _cells(tr).some(_isDirty);
        var btn = tr.querySelector('.bulk-row-apply');
        if (btn) btn.disabled = !dirty;
        return dirty;
    }
    function _dedupKey(c) {
        return c.getAttribute('data-linkable') === '1'
            ? c.getAttribute('data-resolved')
            : c.getAttribute('data-dot-path');
    }
    function _dirtyCount(scope) {
        var seen = {}, n = 0;
        _cells(scope || table()).filter(_isDirty).forEach(function (c) {
            var k = _dedupKey(c);
            if (!seen[k]) { seen[k] = true; n++; }
        });
        return n;
    }
    function _refreshGlobal() {
        var t = table(); if (!t) return;
        var n = _dirtyCount(t);
        var cnt = document.getElementById('bulk-pair-dirty-count');
        if (cnt) cnt.textContent = n ? (n + ' un-applied ' + (n === 1 ? 'edit' : 'edits')) : '';
        var all = document.getElementById('bulk-pair-apply-all'); if (all) all.disabled = n === 0;
        var aps = document.getElementById('bulk-pair-apply-sync'); if (aps) aps.disabled = n === 0;
        var rst = document.getElementById('bulk-pair-reset'); if (rst) rst.disabled = n === 0;
    }

    function _applyCells(cells, tr, silent, seenGlobal) {
        var errSlot = tr ? tr.querySelector('.bulk-row-error') : null;
        if (errSlot) { errSlot.hidden = true; errSlot.textContent = ''; }
        var seen = {}, updates = [], batchKeys = [];
        cells.forEach(function (c) {
            var k = _dedupKey(c);
            if (seen[k] || (seenGlobal && seenGlobal[k])) return;
            seen[k] = true; batchKeys.push(k);
            updates.push({ dot_path: c.getAttribute('data-dot-path'), value: c.value });
        });
        if (!updates.length) return Promise.resolve({ ok: true, tray_html: null });
        return fetch('/field/edit-batch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates: updates })
        }).then(function (resp) { return resp.json().then(function (j) { return { status: resp.status, body: j }; }); })
            .then(function (r) {
                var byPath = {};
                (r.body && r.body.results || []).forEach(function (res) { byPath[res.dot_path] = res; });
                if (r.body && r.body.ok) {
                    if (seenGlobal) batchKeys.forEach(function (k) { seenGlobal[k] = true; });
                    cells.forEach(function (c) {
                        var res = byPath[c.getAttribute('data-dot-path')] || {};
                        if (!c.hasAttribute('data-baseline')) c.setAttribute('data-baseline', c.getAttribute('data-orig'));
                        var disp = (res.display != null) ? res.display : c.value;
                        c.value = disp;
                        c.setAttribute('data-orig', disp);
                        c.classList.remove('dirty', 'bulk-cell-bad');
                        c.classList.add('bulk-cell-modified', 'bulk-applied-flash');
                        var td = c.closest('.bulk-td'); var old = td && td.querySelector('.bulk-ba-old');
                        if (old) old.textContent = c.getAttribute('data-baseline');
                        setTimeout(function () { c.classList.remove('bulk-applied-flash'); }, 700);
                    });
                    _syncAppliedAcrossTable(r.body.results);
                    if (!silent && r.body.tray_html && window._swapPendingTray) {
                        window._bulkSelfEdit = true;
                        window._swapPendingTray(r.body.tray_html);
                        window._bulkSelfEdit = false;
                    }
                    // Re-run diagnostics unconditionally (see bulk-edit.js) — a silent
                    // or dedup'd pair-grid commit must still refresh the safety linter.
                    if (window._diagChanged) window._diagChanged();
                    return { ok: true, tray_html: r.body.tray_html || null };
                }
                var msg = '';
                cells.forEach(function (c) {
                    var info = byPath[c.getAttribute('data-dot-path')];
                    if (info && info.applied === false && info.error) { c.classList.add('bulk-cell-bad'); msg = info.error; }
                });
                if (errSlot) { errSlot.hidden = false; errSlot.textContent = msg || (r.body && r.body.error) || 'edit failed'; }
                return { ok: false };
            }).catch(function (e) {
                if (errSlot) { errSlot.hidden = false; errSlot.textContent = String(e); }
                return { ok: false };
            });
    }

    // ── before/after hover ───────────────────────────────────────────────────
    function _hoverBA(e, show) {
        var td = e.target.closest && e.target.closest('.bulk-td');
        if (!td) return;
        var cell = td.querySelector('.bulk-cell');
        if (!cell || !cell.classList.contains('bulk-cell-modified')) return;
        var newEl = td.querySelector('.bulk-ba-new');
        if (newEl) newEl.textContent = cell.value;
        td.classList.toggle('bulk-ba-show', show);
    }

    // ── shared physical-node linking (e.g. examplechip coupler.operations aliases the
    //    macro's coupler_flux_pulse — same resolved node → edit one, mirror all) ─
    function _linkedSiblings(cell) {
        var rp = cell.getAttribute('data-resolved'), t = table();
        if (!rp || !t || cell.getAttribute('data-linkable') !== '1') return [cell];
        var esc = (window.CSS && CSS.escape) ? CSS.escape(rp) : rp;
        return Array.prototype.slice.call(t.querySelectorAll('.bulk-cell[data-resolved="' + esc + '"][data-linkable="1"]'));
    }
    function _mirrorLinked(cell) {
        var sibs = _linkedSiblings(cell);
        if (sibs.length < 2) return;
        var v = cell.value;
        sibs.forEach(function (s) {
            if (s === cell) return;
            if (s.value !== v) s.value = v;
            s.classList.remove('bulk-cell-bad');
            _markCellDirty(s);
            _refreshRow(_rowOf(s));
        });
    }
    function _markLinkedCells() {
        var t = table(); if (!t) return;
        var groups = {};
        _cells(t).forEach(function (c) {
            var rp = c.getAttribute('data-resolved');
            if (!rp || c.getAttribute('data-linkable') !== '1') return;
            (groups[rp] = groups[rp] || []).push(c);
        });
        Object.keys(groups).forEach(function (rp) {
            var cells = groups[rp];
            if (cells.length < 2) return;
            var v0 = cells[0].getAttribute('data-orig');
            var divergent = cells.some(function (c) { return c.getAttribute('data-orig') !== v0; });
            cells.forEach(function (c) {
                c.classList.add('bulk-cell-linked');
                if (divergent) c.classList.add('bulk-cell-bad');
                if (!/shared node/i.test(c.title || '')) {
                    c.title = (c.title ? c.title + ' · ' : '') +
                        'Shared node — editing this updates every cell that writes the same value';
                }
            });
        });
    }
    function _syncAppliedAcrossTable(results) {
        var t = table(); if (!t) return;
        var byResolved = {};
        (results || []).forEach(function (res) { if (res.resolved_path) byResolved[res.resolved_path] = res; });
        if (!Object.keys(byResolved).length) return;
        _cells(t).forEach(function (c) {
            if (c.getAttribute('data-linkable') !== '1') return;
            var res = byResolved[c.getAttribute('data-resolved')];
            if (!res || res.applied === false) return;
            if (!c.hasAttribute('data-baseline')) c.setAttribute('data-baseline', c.getAttribute('data-orig'));
            var disp = (res.display != null) ? res.display : c.value;
            c.value = disp;
            c.setAttribute('data-orig', disp);
            c.classList.remove('dirty', 'bulk-cell-bad');
            c.classList.add('bulk-cell-modified');
            var td = c.closest('.bulk-td'); var old = td && td.querySelector('.bulk-ba-old');
            if (old) old.textContent = c.getAttribute('data-baseline');
        });
    }

    // ── column drag-resize ───────────────────────────────────────────────────
    function _loadColWidths() { try { _colWidths = JSON.parse(localStorage.getItem(WIDTH_KEY) || '{}') || {}; } catch (e) { _colWidths = {}; } }
    function _saveColWidths() { try { localStorage.setItem(WIDTH_KEY, JSON.stringify(_colWidths)); } catch (e) {} }
    function _colWidthStyleEl() {
        var el = document.getElementById('bulk-pair-col-width-style');
        if (!el) { el = document.createElement('style'); el.id = 'bulk-pair-col-width-style'; document.head.appendChild(el); }
        return el;
    }
    function _applyColWidthStyle() {
        var css = '';
        for (var k in _colWidths) {
            var w = _colWidths[k];
            var ek = (window.CSS && CSS.escape) ? CSS.escape(k) : k;
            var wpx = w + 'px;min-width:' + w + 'px;max-width:' + w + 'px';
            css += '#bulk-pair-table th.bulk-col-head[data-col-key="' + ek + '"]{width:' + wpx + ';overflow:hidden}';
            css += '#bulk-pair-table td[data-col-key="' + ek + '"]{width:' + wpx + ';overflow:hidden}';
            css += '#bulk-pair-table td[data-col-key="' + ek + '"] .bulk-cell{width:' + w + 'px!important;min-width:' + w + 'px;max-width:' + w + 'px}';
        }
        _colWidthStyleEl().textContent = css;
    }
    function _startColResize(e, key, th) {
        e.preventDefault(); e.stopPropagation();
        _resize = { key: key, startX: e.clientX, startW: th ? th.offsetWidth : (_colWidths[key] || 80) };
        document.body.style.cursor = 'col-resize';
        document.addEventListener('mousemove', _onColResizeMove);
        document.addEventListener('mouseup', _onColResizeUp);
    }
    function _onColResizeMove(e) {
        if (!_resize) return;
        var w = Math.max(30, _resize.startW + (e.clientX - _resize.startX));
        _colWidths[_resize.key] = w;
        _applyColWidthStyle();
    }
    function _onColResizeUp() {
        if (!_resize) return;
        _saveColWidths();
        _resize = null;
        _resizeJustEnded = true;
        setTimeout(function () { _resizeJustEnded = false; }, 0);
        document.body.style.cursor = '';
        document.removeEventListener('mousemove', _onColResizeMove);
        document.removeEventListener('mouseup', _onColResizeUp);
    }
    function _autoFitColWidth(key) { delete _colWidths[key]; _saveColWidths(); _applyColWidthStyle(); }

    var BulkPairEdit = {
        mount: function (columns) {
            if (Array.isArray(columns)) COLS = columns;
            sortKey = null; sortDir = 1;
            var t = table();
            if (!t) return;
            _loadColWidths();
            _applyColWidthStyle();
            _buildColMenu();
            _applyColumnVisibility();
            _recomputeStats();
            _markLinkedCells();
            if (t._pairBound) { _refreshGlobal(); return; }
            t._pairBound = true;

            t.addEventListener('click', function (e) {
                if (e.target.closest && e.target.closest('.bulk-resize-handle')) return;
                if (_resizeJustEnded) return;
                if (e.target.closest && e.target.closest('.bulk-ro-link')) return;
                var th = e.target.closest && e.target.closest('thead th[data-col-key]');
                if (th && th.getAttribute('data-col-key')) sort(th.getAttribute('data-col-key'));
            });
            t.addEventListener('mousedown', function (e) {
                var h = e.target.closest && e.target.closest('.bulk-resize-handle');
                if (h) _startColResize(e, h.getAttribute('data-col-key'), h.closest('th'));
            });
            t.addEventListener('dblclick', function (e) {
                var h = e.target.closest && e.target.closest('.bulk-resize-handle');
                if (h) { e.preventDefault(); e.stopPropagation(); _autoFitColWidth(h.getAttribute('data-col-key')); }
            });
            t.addEventListener('input', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell || cell.classList.contains('bulk-cell-ro')) return;
                cell.classList.remove('bulk-cell-bad');
                _markCellDirty(cell);
                if (cell.classList.contains('bulk-cell-linked')) _mirrorLinked(cell);
                _refreshRow(_rowOf(cell));
                _refreshGlobal();
            });
            t.addEventListener('keydown', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell) return;
                if (e.key === 'Enter') {
                    e.preventDefault();
                    var b = _rowOf(cell).querySelector('.bulk-row-apply');
                    if (b && !b.disabled) BulkPairEdit.applyRow(b);
                    return;
                }
                var dir = { ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.key];
                if (e.key === 'ArrowLeft' && cell.selectionStart === 0) dir = [0, -1];
                if (e.key === 'ArrowRight' && cell.selectionStart === cell.value.length) dir = [0, 1];
                if (!dir) return;
                var move = _gridMove(cell, dir[0], dir[1]);
                if (move) { e.preventDefault(); move.focus(); move.select && move.select(); }
            });
            // Tab / click-away COMMITS the row (like Enter), unless focus stays
            // within the row (cell-to-cell) or lands on the row's own Apply button.
            // applyRow no-ops when nothing is dirty + self-guards double-submit.
            t.addEventListener('focusout', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell || cell.classList.contains('bulk-cell-ro')) return;
                var row = _rowOf(cell);
                var to = e.relatedTarget;
                if (to && row && row.contains(to)) return;
                // Let an "Apply all" / "Apply to live" button commit the whole set.
                if (to && to.closest && to.closest('#bulk-pair-apply-all, #bulk-pair-apply-sync')) return;
                var b = row && row.querySelector('.bulk-row-apply');
                if (b && !b.disabled) BulkPairEdit.applyRow(b);
            });
            t.addEventListener('mouseover', function (e) { _hoverBA(e, true); });
            t.addEventListener('mouseout', function (e) { _hoverBA(e, false); });

            // Shared search box: add our own listener (the qubit grid has its own),
            // so one box filters BOTH tables. Persist is handled by the qubit grid;
            // we only re-filter the pair table.
            var search = document.getElementById('bulk-search');
            if (search && !search._pairBound) {
                search._pairBound = true;
                search.addEventListener('input', applySearch);
            }
            // restore persisted query into our filter on (re)mount
            try { if (search && localStorage.getItem(SEARCH_KEY)) applySearch(); } catch (e) {}

            // nav guard for unapplied PAIR edits (the qubit guard only sees its table)
            if (!window._bulkPairNavGuard) {
                window._bulkPairNavGuard = true;
                window.addEventListener('beforeunload', function (ev) {
                    var tt = table();
                    if (tt && _cells(tt).some(_isDirty)) { ev.preventDefault(); ev.returnValue = ''; return ''; }
                });
                document.body.addEventListener('htmx:beforeSwap', function (ev) {
                    var tt = table();
                    if (tt && ev.detail && ev.detail.target && ev.detail.target.id === 'table-pane'
                        && _cells(tt).some(_isDirty)) {
                        if (!window.confirm('You have unapplied pair edits in Live State Edit. Leave and discard them?')) {
                            ev.preventDefault();
                        }
                    }
                });
            }
            _refreshGlobal();
        },

        applyRow: function (btn) {
            var tr = btn.closest('tr'); if (!tr) return;
            var dirty = _cells(tr).filter(_isDirty);
            if (!dirty.length) return;
            btn.disabled = true; btn.textContent = '…';
            _applyCells(dirty, tr, false).then(function (res) {
                btn.textContent = res.ok ? '✓' : 'Apply';
                if (res.ok) setTimeout(function () { btn.textContent = 'Apply'; }, 900);
                _refreshRow(tr); _refreshGlobal(); _recomputeStats();
            });
        },

        // syncAfter (the ⚡ "Apply to live now" button): commit to the working state,
        // then pull+re-apply+push to the live chip in one shot (doStateSync('apply')).
        applyAll: function (syncAfter) {
            var t = table(); if (!t) return;
            var rows = _rows().filter(function (tr) { return _cells(tr).some(_isDirty); });
            if (!rows.length) return;
            var n = _dirtyCount(t);
            if (!window.confirm('Apply ' + n + ' pair edit' + (n === 1 ? '' : 's') + ' across ' + rows.length +
                ' pair' + (rows.length === 1 ? '' : 's') +
                (syncAfter ? ' and push to the live chip?' : ' to the working state?'))) return;
            var all = document.getElementById('bulk-pair-apply-all');
            if (all) { all.disabled = true; all.textContent = 'Applying…'; }
            var apsBtn = document.getElementById('bulk-pair-apply-sync'); if (apsBtn) apsBtn.disabled = true;
            var i = 0, failures = 0, succeeded = 0, lastTray = null, firstFailRow = null;
            var seenGlobal = {};
            function next() {
                if (i >= rows.length) {
                    if (lastTray && window._swapPendingTray) {
                        window._bulkSelfEdit = true;
                        window._swapPendingTray(lastTray);
                        window._bulkSelfEdit = false;
                    }
                    if (all) all.textContent = failures ? ('Apply all (' + failures + ' failed)') : 'Apply all (pairs)';
                    _refreshGlobal(); _recomputeStats();
                    if (failures) {
                        var msg = succeeded + ' applied, ' + failures + ' failed — see the red row' + (failures === 1 ? '' : 's');
                        if (window.showToast) window.showToast(msg, 'warning');
                        if (firstFailRow && firstFailRow.scrollIntoView) firstFailRow.scrollIntoView({ block: 'center', behavior: 'smooth' });
                    }
                    if (syncAfter && !failures && window.applyEditsToLive) window.applyEditsToLive();
                    return;
                }
                var tr = rows[i++];
                _applyCells(_cells(tr).filter(_isDirty), tr, true, seenGlobal).then(function (res) {
                    if (!res.ok) { failures++; if (!firstFailRow) firstFailRow = tr; }
                    else { succeeded++; if (res.tray_html) lastTray = res.tray_html; }
                    _refreshRow(tr); next();
                });
            }
            next();
        },

        resetDirty: function () {
            var t = table(); if (!t) return;
            _cells(t).forEach(function (c) {
                if (_isDirty(c)) c.value = c.getAttribute('data-orig');
                c.classList.remove('dirty', 'bulk-cell-bad');
            });
            _rows().forEach(function (tr) {
                var e = tr.querySelector('.bulk-row-error');
                if (e) { e.hidden = true; e.textContent = ''; }
            });
            _rows().forEach(_refreshRow);
            _refreshGlobal();
        },

        sort: sort,
        applySearch: applySearch,
        showAllColumns: function () { _saveHidden(new Set()); _buildColMenu(); _applyColumnVisibility(); _recomputeStats(); },
        resetColumns: function () { try { localStorage.removeItem(HIDE_KEY); } catch (e) {} _buildColMenu(); _applyColumnVisibility(); _recomputeStats(); },
        openPair: function (id) {
            var url = '/pair/' + encodeURIComponent(id);
            if (window.htmx && document.getElementById('inspector-pane')) {
                htmx.ajax('GET', url, { source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML' });
            } else if (window.htmx && document.getElementById('table-pane')) {
                htmx.ajax('GET', url, { target: '#table-pane', swap: 'innerHTML' });
            } else { window.location.href = url; }
            return false;
        },

        applyModifiedDelta: function (modified) {
            if (!Array.isArray(modified)) return;
            var byResolved = {};
            modified.forEach(function (m) { byResolved[m.resolved_path] = m; });
            var t = table(); if (!t) return;
            _cells(t).forEach(function (c) {
                var rp = c.getAttribute('data-resolved');
                if (byResolved[rp]) {
                    c.classList.add('bulk-cell-modified');
                    if (!c.hasAttribute('data-baseline')) c.setAttribute('data-baseline', byResolved[rp].old_display);
                    var td = c.closest('.bulk-td'); var old = td && td.querySelector('.bulk-ba-old');
                    if (old) old.textContent = byResolved[rp].old_display;
                }
            });
        }
    };

    function _gridMove(cell, dr, dc) {
        var td = cell.closest('td');
        var tr = cell.closest('tr');
        var rows = _rows().filter(function (r) { return !r.classList.contains('bulk-row-hidden'); });
        var ri = rows.indexOf(tr);
        if (dr) {
            var nr = rows[ri + dr];
            if (!nr) return null;
            var key = td.getAttribute('data-col-key');
            return nr.querySelector('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] .bulk-cell');
        }
        if (dc) {
            var tds = Array.prototype.slice.call(tr.querySelectorAll('.bulk-td:not(.bulk-col-hidden):not(.bulk-search-hidden)'));
            var ci = tds.indexOf(td);
            var ntd = tds[ci + dc];
            return ntd ? ntd.querySelector('.bulk-cell') : null;
        }
        return null;
    }

    window.BulkPairEdit = BulkPairEdit;
})();
