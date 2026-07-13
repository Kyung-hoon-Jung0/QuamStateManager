/* Bulk-edit panel (/bulk) — a spreadsheet to retune many qubits at once.
 *
 * Commits route through the SAME /field/edit-batch endpoint the inspector + plot
 * popup use (atomic, working-copy, type-coercion-only) — no new mutation logic.
 * The cell value is full-digit + thousands-comma (units.group_digits); the server
 * strips the commas on parse and ECHOES the committed value so the cell always
 * re-renders from the server's truth, never the typed string.
 *
 * Apply semantics: per-row Apply (and Apply-all, which fires ONE atomic batch PER
 * ROW) are the unit — a typo isolates to its qubit, never nuking the others.
 *
 * This module owns: dirty tracking, apply + trusted re-render, persistent
 * modified marker + before/after hover, a Property-Selection column manager, a
 * unified search (column names / qubit ids / cell numbers), header sort + per-
 * column min/max, spreadsheet arrow-key navigation, and an unsaved-edits nav guard.
 */
(function () {
    'use strict';

    var HIDE_KEY = 'quam_bulk_hidden_cols';
    var SEARCH_KEY = 'quam_bulk_search';   // persist the search/filter box across visits
    var FREQSYNC_KEY = 'quam_bulk_freqsync';   // 🔗 mirror f_01↔RF on edit (default on)
    var COLS = [];                 // column model from the server: {key,label,section,unit,default_on}
    var BANDS = {};                // {"1":[lo,hi], ...} MW-FEM band ranges (from server)
    var sortKey = null, sortDir = 1;

    // f_01 ↔ RF_frequency column pairs (same row = same qubit). RF_frequency is the
    // carrier the hardware actually plays (config uses the inferred IF = RF − LO);
    // f_01 is physics bookkeeping. The calibration nodes write BOTH to the same fit
    // value, so editing one should follow to the other — but ONLY when they are
    // currently equal: an already-detuned pair (e.g. an optimized readout) is left
    // untouched. Soft (equality-keyed), not a hard structural link. See the project
    // memory note f01-vs-rf-frequency-semantics.
    var FREQ_PAIRS = [['f_01', 'xy_RF_frequency'], ['readout_frequency', 'readout_RF_frequency']];
    var FREQ_TWIN = {};
    FREQ_PAIRS.forEach(function (p) { FREQ_TWIN[p[0]] = p[1]; FREQ_TWIN[p[1]] = p[0]; });

    function _freqSyncOn() {
        try { return localStorage.getItem(FREQSYNC_KEY) !== '0'; } catch (e) { return true; }
    }

    function table() { return document.getElementById('bulk-table'); }
    function _cells(scope) { return Array.prototype.slice.call(scope.querySelectorAll('.bulk-cell')); }
    function _rows() { var t = table(); return t ? Array.prototype.slice.call(t.querySelectorAll('tbody tr')) : []; }
    function _isDirty(c) { return c.value !== c.getAttribute('data-orig'); }
    function _rowOf(c) { return c.closest('tr'); }
    function _grp(v) { return (window._groupDigits ? window._groupDigits(v) : String(v)); }
    // comma-insensitive numeric value of a cell's text (for sort + min/max + search)
    function _num(s) { var n = parseFloat(String(s).replace(/,/g, '')); return isFinite(n) ? n : null; }

    // ── persisted column visibility ──────────────────────────────────────────
    function _hidden() {
        try { return JSON.parse(localStorage.getItem(HIDE_KEY) || 'null'); } catch (e) { return null; }
    }
    function _saveHidden(set) {
        // NOTE: Array.prototype.slice.call(aSet) returns [] (a Set is not array-like),
        // which silently persisted an empty hidden-set and broke the column toggle.
        try { localStorage.setItem(HIDE_KEY, JSON.stringify(Array.from(set))); } catch (e) {}
    }
    // The effective hidden set: persisted choice if any, else the server defaults
    // (port columns start hidden — default_on=false).
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
        applySearch();   // re-evaluate the search against the new column set
        _updateTopScroll();
    }

    // ── user font size + weight + letter-spacing (persisted; applied globally) ─
    var FONT_KEY = 'quam_bulk_fs', BOLD_KEY = 'quam_bulk_bold', LS_KEY = 'quam_bulk_ls';
    function _readScale() {
        var fs = parseFloat(localStorage.getItem(FONT_KEY));
        if (!fs || isNaN(fs)) fs = 1;
        var bold = false;
        try { bold = localStorage.getItem(BOLD_KEY) === '1'; } catch (e) {}
        var ls = parseFloat(localStorage.getItem(LS_KEY));
        if (isNaN(ls)) ls = 0;
        return { fs: fs, bold: bold, ls: ls };
    }
    // Mirror the user's readability choices onto :root so body-level surfaces (the
    // Review modal's editable Live-chip inputs) read as the SAME dense table —
    // same font-size + weight + letter-spacing — as the Bulk Edit grid they float
    // over. Runs even when #bulk-panel isn't mounted (the modal can open anywhere).
    function _applyGlobalScale() {
        var s = _readScale(), root = document.documentElement;
        root.style.setProperty('--bulk-fs', s.fs);
        root.style.setProperty('--bulk-fw', s.bold ? 700 : 500);
        root.style.setProperty('--bulk-ls', s.ls + 'em');
        return s;
    }
    function _applyFont() {
        var s = _applyGlobalScale();
        var panel = document.getElementById('bulk-panel'); if (!panel) return;
        panel.style.setProperty('--bulk-fs', s.fs);
        panel.style.setProperty('--bulk-ls', s.ls + 'em');
        panel.classList.toggle('bulk-bold', s.bold);
        var sl = document.getElementById('bulk-font-slider'); if (sl) sl.value = s.fs;
        var lsl = document.getElementById('bulk-ls-slider'); if (lsl) lsl.value = s.ls;
        var bb = document.getElementById('bulk-bold');
        if (bb) { bb.setAttribute('aria-pressed', s.bold ? 'true' : 'false'); bb.classList.toggle('active', s.bold); }
        Array.prototype.slice.call(document.querySelectorAll('.bulk-font-preset')).forEach(function (b) {
            b.classList.toggle('active', Math.abs(parseFloat(b.getAttribute('data-fs')) - s.fs) < 0.001);
        });
        _updateTopScroll();
        _updateStickyOffset();   // band height changes with the font scale
    }
    // ── dismissible hint (persisted) ─────────────────────────────────────────
    var HINT_KEY = 'quam_bulk_hint_hidden';
    function _applyHint() {
        var panel = document.getElementById('bulk-panel'); if (!panel) return;
        var hidden = false;
        try { hidden = localStorage.getItem(HINT_KEY) === '1'; } catch (e) {}
        panel.classList.toggle('bulk-hint-hidden', hidden);
        var info = document.getElementById('bulk-hint-toggle');
        if (info) info.setAttribute('aria-pressed', hidden ? 'false' : 'true');
    }

    // ── synced top horizontal scrollbar ──────────────────────────────────────
    function _updateTopScroll() {
        var tbl = table(), inner = document.getElementById('bulk-scroll-top-inner');
        if (tbl && inner) inner.style.width = tbl.scrollWidth + 'px';
    }
    function _setupTopScroll() {
        var wrap = document.querySelector('.bulk-table-wrap');
        var top = document.getElementById('bulk-scroll-top');
        if (!wrap || !top || top._bound) return;
        top._bound = true;
        var lock = false;
        top.addEventListener('scroll', function () { if (lock) return; lock = true; wrap.scrollLeft = top.scrollLeft; lock = false; });
        wrap.addEventListener('scroll', function () { if (lock) return; lock = true; top.scrollLeft = wrap.scrollLeft; lock = false; });
        window.addEventListener('resize', _updateTopScroll);
    }

    // ── group-header band (spanning section headers) ─────────────────────────
    // Keep each group head's colspan equal to its number of VISIBLE columns (the
    // checkbox + search layers hide individual columns); an all-hidden group
    // collapses. Without this the band drifts out of alignment on every toggle.
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
    // The 2nd header row (column heads) sticks BELOW the group band, so offset its
    // sticky `top` by the band's measured height (varies with the font scale).
    function _updateStickyOffset() {
        var t = table(); if (!t) return;
        var grow = t.querySelector('.bulk-group-row');
        if (grow) t.style.setProperty('--bulk-grouphead-h', grow.offsetHeight + 'px');
    }

    // ── Property-Selection menu ──────────────────────────────────────────────
    function _buildColMenu() {
        var menu = document.getElementById('bulk-colvis-menu');
        if (!menu) return;
        var hide = _hiddenSet();
        var bySection = {};
        var order = [];
        COLS.forEach(function (c) {
            if (!bySection[c.section]) { bySection[c.section] = []; order.push(c.section); }
            bySection[c.section].push(c);
        });
        var html = '<div class="bulk-colvis-actions">' +
            '<button type="button" class="btn-xs" onclick="BulkEdit.showAllColumns()">Show all</button>' +
            '<button type="button" class="btn-xs outline" onclick="BulkEdit.resetColumns()">Reset</button></div>';
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
    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── search (columns by label, rows by id, cells by comma-insensitive value) ─
    function applySearch() {
        var t = table(); if (!t) return;
        var inp = document.getElementById('bulk-search');
        var q = inp ? inp.value.trim().toLowerCase() : '';
        var hide = _hiddenSet();
        var visCols = COLS.filter(function (c) { return !hide.has(c.key); });
        var tokens = q ? q.split(/\s+/) : [];

        // classify each token: matches a column label? a qubit id?
        var ids = _rows().map(function (r) { return (r.getAttribute('data-qubit') || '').toLowerCase(); });
        var tokInfo = tokens.map(function (tok) {
            var colHit = visCols.some(function (c) { return (c.label + ' ' + c.key).toLowerCase().indexOf(tok) >= 0; });
            var idHit = ids.some(function (id) { return id.indexOf(tok) >= 0; });
            return { tok: tok, isCol: colHit, isId: idHit, isVal: !colHit && !idHit };
        });

        // column passes if it satisfies every column-restricting token (label) and
        // every value token (a cell of this column contains it).
        function colVisible(key, colCells) {
            for (var i = 0; i < tokInfo.length; i++) {
                var ti = tokInfo[i];
                if (ti.isCol && !ti.isId) {
                    var c = COLS.filter(function (x) { return x.key === key; })[0];
                    if (!c || (c.label + ' ' + c.key).toLowerCase().indexOf(ti.tok) < 0) return false;
                } else if (ti.isVal) {
                    if (!colCells.some(function (h) { return h.indexOf(ti.tok) >= 0; })) return false;
                }
            }
            return true;
        }
        // row passes if it satisfies every id token (id matches) and every value
        // token (some cell contains it). Column-only tokens don't restrict rows.
        function rowVisible(id, rowHaystacks) {
            for (var i = 0; i < tokInfo.length; i++) {
                var ti = tokInfo[i];
                if (ti.isId && !ti.isCol) { if (id.indexOf(ti.tok) < 0) return false; }
                else if (ti.isVal) { if (!rowHaystacks.some(function (h) { return h.indexOf(ti.tok) >= 0; })) return false; }
            }
            return true;
        }

        // gather per-column cell haystacks (only over checkbox-visible columns)
        var rows = _rows();
        var colHay = {};   // key -> [haystack...]
        visCols.forEach(function (c) { colHay[c.key] = []; });
        var rowHay = rows.map(function (r) {
            var hs = [];
            _cells(r).forEach(function (cell) {
                var k = cell.closest('[data-col-key]').getAttribute('data-col-key');
                if (hide.has(k)) return;
                var disp = cell.value.toLowerCase();
                var bare = disp.replace(/,/g, '');
                var h = disp + ' ' + bare;
                hs.push(h);
                if (colHay[k]) colHay[k].push(h);
            });
            return hs;
        });

        // decide column visibility (search layer, on top of checkbox layer)
        var colSearchHide = {};
        visCols.forEach(function (c) { colSearchHide[c.key] = !colVisible(c.key, colHay[c.key] || []); });
        t.querySelectorAll('th.bulk-col-head, td[data-col-key]').forEach(function (el) {
            var k = el.getAttribute('data-col-key');
            if (k === '__id__' || hide.has(k)) return;   // checkbox-hidden handled elsewhere
            el.classList.toggle('bulk-search-hidden', !!colSearchHide[k]);
        });
        // decide row visibility
        var shown = 0;
        rows.forEach(function (r, i) {
            var id = (r.getAttribute('data-qubit') || '').toLowerCase();
            var vis = rowVisible(id, rowHay[i]);
            r.classList.toggle('bulk-row-hidden', !vis);
            if (vis) shown++;
        });
        var cnt = document.getElementById('bulk-search-count');
        if (cnt) cnt.textContent = q ? (shown + ' of ' + rows.length) : '';
        _updateGroupHeader();   // re-span the group band over what's now visible
    }

    // ── sort + per-column min/max ────────────────────────────────────────────
    function sort(key) {
        var t = table(); if (!t) return;
        var tbody = t.querySelector('tbody');
        if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = 1; }
        var rows = _rows();
        function keyOf(r) {
            if (key === '__id__') return (r.getAttribute('data-qubit') || '');
            // data-col-key is on the <td>, NOT the .bulk-cell <input>; the old guard
            // (`.bulk-cell[data-col-key]`) was always falsy so keyOf returned '' and
            // nothing ever sorted.
            var cell = r.querySelector('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] .bulk-cell');
            return cell ? cell.value : '';
        }
        rows.sort(function (a, b) {
            var va = keyOf(a), vb = keyOf(b);
            if (key === '__id__') return va < vb ? -sortDir : (va > vb ? sortDir : 0);
            var na = _num(va), nb = _num(vb);
            if (na === null && nb === null) return 0;
            if (na === null) return 1;            // missing sinks to the bottom
            if (nb === null) return -1;
            return na < nb ? -sortDir : (na > nb ? sortDir : 0);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        // carets
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
            // Colour the extreme cells, mirroring the /table comparison view
            // (max=cell-best, min=cell-worst) — these mark extremes, not quality.
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
    // Dedup key for counting AND writing. Only LINKABLE cells (resolvable → a real
    // shared leaf node) collapse onto one physical target, so they key on
    // data-resolved. A non-linkable cell (a dead-ended optional leaf whose
    // resolved_path falls back to the bare parent port-dict path — shared by
    // several distinct unset fields) MUST key on its own data-dot-path; otherwise
    // two independent fields dedup onto one and the second edit silently vanishes.
    // This keeps the dedup gate in lock-step with the linkable (mirror) gate.
    function _dedupKey(c) {
        return c.getAttribute('data-linkable') === '1'
            ? c.getAttribute('data-resolved')
            : c.getAttribute('data-dot-path');
    }
    // Physical-change count: linked siblings share one resolved node, so count
    // UNIQUE physical targets among dirty cells, not the raw cell count.
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
        var cnt = document.getElementById('bulk-dirty-count');
        if (cnt) cnt.textContent = n ? (n + ' un-applied ' + (n === 1 ? 'edit' : 'edits')) : '';
        var all = document.getElementById('bulk-apply-all'); if (all) all.disabled = n === 0;
        var aps = document.getElementById('bulk-apply-sync'); if (aps) aps.disabled = n === 0;
        var rst = document.getElementById('bulk-reset'); if (rst) rst.disabled = n === 0;
    }

    function _applyCells(cells, tr, silent, seenGlobal) {
        var errSlot = tr ? tr.querySelector('.bulk-row-error') : null;
        if (errSlot) { errSlot.hidden = true; errSlot.textContent = ''; }
        // Dedup by physical write-target: linked cells (qA1..qA6 on one shared port)
        // write that node ONCE, not N×. Non-linkable cells key on their own dot-path
        // so each posts independently (never collapsed onto a shared parent path).
        // seenGlobal carries the dedup across rows (applyAll); linked siblings are
        // then reconciled via _syncAppliedAcrossTable.
        //
        // Record this row's keys into seenGlobal ONLY on a SUCCESSFUL commit (A11):
        // if we marked them eagerly and THIS batch rolled back (a sibling typo in the
        // same row), a later row that shares one of these physical nodes would skip its
        // own re-post and be left silently dirty-but-stale though the shared value was
        // rolled back. So we collect the keys locally and merge them on the ok branch.
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
            body: JSON.stringify({ updates: updates, expect_chip: window.__chipToken || '' })
        }).then(function (resp) { return resp.json().then(function (j) { return { status: resp.status, body: j }; }); })
            .then(function (r) {
                var byPath = {};
                (r.body && r.body.results || []).forEach(function (res) { byPath[res.dot_path] = res; });
                if (r.body && r.body.ok) {
                    // Commit succeeded → only now claim these physical nodes in the
                    // cross-row dedup, so a failed row never strands a shared sibling (A11).
                    if (seenGlobal) batchKeys.forEach(function (k) { seenGlobal[k] = true; });
                    cells.forEach(function (c) {
                        var res = byPath[c.getAttribute('data-dot-path')] || {};
                        // before/after baseline: remember the pre-edit value the FIRST
                        // time a cell is committed, so the marker shows true original→now.
                        if (!c.hasAttribute('data-baseline')) c.setAttribute('data-baseline', c.getAttribute('data-orig'));
                        // re-render from the SERVER's committed value (never the typed string)
                        var disp = (res.display != null) ? res.display : c.value;
                        c.value = disp;
                        c.setAttribute('data-orig', disp);   // new apply baseline → not dirty
                        c.classList.remove('dirty', 'bulk-cell-bad');
                        c.classList.add('bulk-cell-modified', 'bulk-applied-flash');
                        var td = c.closest('.bulk-td'); var old = td && td.querySelector('.bulk-ba-old');
                        if (old) old.textContent = c.getAttribute('data-baseline');
                        setTimeout(function () { c.classList.remove('bulk-applied-flash'); }, 700);
                    });
                    // Clean the just-written node's OTHER cells (linked siblings in
                    // other rows) from the same server echo — so editing+applying one
                    // shared-port cell updates them all.
                    _syncAppliedAcrossTable(r.body.results);
                    if (!silent && r.body.tray_html && window._swapPendingTray) {
                        window._bulkSelfEdit = true;            // suppress our own cross-surface refresh
                        window._swapPendingTray(r.body.tray_html);
                        window._bulkSelfEdit = false;
                    }
                    // Re-run diagnostics unconditionally — a silent (applyAll) row or a
                    // dedup'd shared-port commit may not swap the tray, but the edit DID
                    // change the chip and the safety linter must reflect it (debounced).
                    if (window._diagChanged) window._diagChanged();
                    // Return the tray HTML so a batched caller (applyAll) can swap the
                    // pending tray / unsaved-changes banner ONCE at the end — it can't
                    // rely on the last row to do it, since a row whose cells all dedup
                    // to an already-written shared-port node posts nothing (so returns
                    // no tray_html of its own).
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

    // ── MW-FEM band validation (advisory: warn, never block) ─────────────────
    function _bandsCompatible(b1, b2) {
        b1 = String(b1); b2 = String(b2);
        if (b1 === b2) return true;
        return (b1 === '1' && b2 === '3') || (b1 === '3' && b2 === '1');
    }
    // Validate one LO cell (a band or a frequency) against its band range + LO peer.
    // Sets/clears a `bulk-band-warn` highlight + an inline message; returns true if warned.
    function _validateBand(cell) {
        var field = cell.getAttribute('data-lo-field');
        var td = cell.closest('.bulk-td');
        var msgEl = td && td.querySelector('.bulk-band-msg');
        if (!field) return false;
        var msg = '';
        if (field === 'freq') {
            var band = cell.getAttribute('data-band');
            var rng = BANDS[band];
            var f = _num(cell.value);
            if (rng && f !== null && (f < rng[0] || f > rng[1])) {
                msg = 'Outside Band ' + band + ' (' + _grp(rng[0]) + '–' + _grp(rng[1]) + ' Hz)';
            }
        } else if (field === 'band') {
            var nb = String(_num(cell.value) != null ? _num(cell.value) : cell.value.trim());
            var peerBand = cell.getAttribute('data-peer-band');
            var peer = cell.getAttribute('data-peer');
            var freq = _num(cell.getAttribute('data-freq'));
            if (peerBand && !_bandsCompatible(nb, peerBand)) {
                msg = 'Band ' + nb + ' conflicts with LO peer ' + (peer || '') + ' (band ' + peerBand + ')';
            }
            if (BANDS[nb] && freq !== null && (freq < BANDS[nb][0] || freq > BANDS[nb][1])) {
                msg = (msg ? msg + ' · ' : '') + 'freq ' + _grp(freq) + ' outside Band ' + nb;
            }
        }
        cell.classList.toggle('bulk-band-warn', !!msg);
        if (msgEl) { msgEl.textContent = msg; msgEl.hidden = !msg; }
        return !!msg;
    }
    function _updateBandWarnCount() {
        var t = table(); if (!t) return;
        var n = t.querySelectorAll('.bulk-cell.bulk-band-warn').length;
        var el = document.getElementById('bulk-band-warn');
        if (el) { el.textContent = n ? ('⚠ ' + n + ' band issue' + (n === 1 ? '' : 's')) : ''; el.hidden = !n; }
    }
    // Count, among the cells about to be committed, how many carry an active LO band
    // conflict (the .bulk-band-warn advisory state). Used to surface — not block — a
    // band conflict at commit time (A10); the project trusts the researcher, so this
    // only appends a line to the existing confirm() dialog.
    function _bandWarnCount(cells) {
        var n = 0;
        cells.forEach(function (c) { if (c.classList.contains('bulk-band-warn')) n++; });
        return n;
    }
    function _bandWarnLine(cells) {
        var n = _bandWarnCount(cells);
        if (!n) return '';
        return '\n\n⚠ ' + n + ' of these edit' + (n === 1 ? '' : 's') +
            ' create an LO band conflict — apply anyway?';
    }

    // ── shared physical-port linking ─────────────────────────────────────────
    // In this data model "physically one value" == "one state.json node": the QUAM
    // pointer system collapses a shared port (power / LO / band / sampling / gain)
    // to a single node, so every qubit on that port renders cells with the SAME
    // data-resolved. Per-qubit fields (IF / readout freq / threshold) resolve to
    // DISTINCT nodes. So cells sharing data-resolved are physically ONE value — link
    // them: editing one mirrors all, and apply writes the node exactly once.
    function _linkedSiblings(cell) {
        var rp = cell.getAttribute('data-resolved'), t = table();
        // Only cells resolving to a real, WRITABLE leaf node link. A missing /
        // dead-ended optional leaf falls back to the bare parent port-dict path,
        // which several distinct unset fields share — those must NOT mirror.
        if (!rp || !t || cell.getAttribute('data-linkable') !== '1') return [cell];
        var esc = (window.CSS && CSS.escape) ? CSS.escape(rp) : rp;
        var sel = '.bulk-cell[data-resolved="' + esc + '"][data-linkable="1"]';
        return Array.prototype.slice.call(t.querySelectorAll(sel));
    }
    function _mirrorLinked(cell) {
        var sibs = _linkedSiblings(cell);
        if (sibs.length < 2) return;
        var v = cell.value;
        sibs.forEach(function (s) {
            if (s === cell) return;
            if (s.value !== v) s.value = v;
            s.classList.remove('bulk-cell-bad');   // editing reconciles a divergent group
            _markCellDirty(s);
            if (s.hasAttribute('data-lo-field')) _validateBand(s);
            _refreshRow(_rowOf(s));
        });
    }

    // ── f_01 ↔ RF_frequency soft link ────────────────────────────────────────
    function _colKeyOf(cell) {
        var td = cell.closest('[data-col-key]');
        return td ? td.getAttribute('data-col-key') : '';
    }
    // The f_01/RF twin cell in the SAME row, or null if this isn't a freq cell or
    // the qubit doesn't carry the twin column.
    function _freqTwinCell(cell) {
        var twinKey = FREQ_TWIN[_colKeyOf(cell)];
        if (!twinKey) return null;
        var row = _rowOf(cell); if (!row) return null;
        var esc = (window.CSS && CSS.escape) ? CSS.escape(twinKey) : twinKey;
        return row.querySelector('[data-col-key="' + esc + '"] .bulk-cell');
    }
    function _setFreqLinkMark(cell, on) {
        var td = cell && cell.closest('.bulk-td');
        if (td) td.classList.toggle('bulk-td-freqlinked', !!on);
    }
    // A #/-pointer cell renders its RESOLVED number, so a pointer-encoded RF twin
    // (e.g. RF_frequency = "#./inferred_RF_frequency") looks equal to f_01 and would
    // be wrongly auto-coupled — then Apply would overwrite the pointer with a literal,
    // destroying the link. Never soft-link a pointer cell (matches the server guard in
    // _maybe_mirror_freq). The server emits data-is-pointer on the cell for this.
    function _isPointerCell(cell) {
        return !!cell && cell.getAttribute('data-is-pointer') === '1';
    }
    // On focus, capture whether this freq cell is "coupled" to its twin: only when the
    // global sync is on, the two are currently equal, and NEITHER is a #/ pointer.
    // Captured once at focus so continuous typing keeps mirroring even as the pair
    // moves together — and so an already-detuned pair is never silently re-coupled.
    //
    // The `_freqJustMirrored` guard lets a user SPLIT a freshly-mirrored pair in one
    // pass: editing f_01 mirrors RF to be equal, so naively focusing RF next would see
    // them equal → re-couple → every RF keystroke would copy back into f_01, fighting a
    // deliberate detune. So when a cell was just written by _softMirrorFreq, its very
    // next focus starts UN-coupled (the flag is then cleared). The 🔗 global toggle is
    // unaffected — turning sync off still stops all mirroring, on still allows it.
    function _freqFocus(cell) {
        var twin = _freqTwinCell(cell);
        var coupled = !!(twin && _freqSyncOn() && cell.value !== '' && cell.value === twin.value
            && !_isPointerCell(cell) && !_isPointerCell(twin));
        if (cell._freqJustMirrored) { coupled = false; }   // let a just-mirrored twin detune
        cell._freqJustMirrored = false;
        cell._freqCoupled = coupled;
        _setFreqLinkMark(cell, coupled);
        if (twin) _setFreqLinkMark(twin, coupled);
    }
    function _freqBlur(cell) {
        _setFreqLinkMark(cell, false);
        var twin = _freqTwinCell(cell);
        if (twin) _setFreqLinkMark(twin, false);
    }
    // While coupled (set at focus), mirror the edited value into the twin so they
    // stay in lock-step — the same "write both" the calibration nodes do. Marks the
    // twin dirty; the input handler's _refreshRow/_refreshGlobal then count it. Re-checks
    // the global toggle so turning 🔗 off mid-edit stops mirroring immediately.
    function _softMirrorFreq(cell) {
        if (!cell._freqCoupled || !_freqSyncOn()) return;
        var twin = _freqTwinCell(cell);
        if (!twin || twin.value === cell.value || _isPointerCell(twin)) return;
        twin.value = cell.value;
        twin.classList.remove('bulk-cell-bad');
        // Mark the twin so its NEXT focus starts un-coupled — the user can then click
        // into it to deliberately detune without the mirror snapping it back (A9).
        twin._freqJustMirrored = true;
        _markCellDirty(twin);
    }

    // Tag groups of >=2 same-node cells as linked (style + tooltip); the rare case
    // where a group's baseline values disagree (corrupt data) is flagged red.
    function _markLinkedCells() {
        var t = table(); if (!t) return;
        var groups = {};
        _cells(t).forEach(function (c) {
            var rp = c.getAttribute('data-resolved');
            if (!rp || c.getAttribute('data-linkable') !== '1') return;   // real writable leaves only
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
                if (!/shared physical-port/i.test(c.title || '')) {
                    c.title = (c.title ? c.title + ' · ' : '') +
                        'Shared physical-port value — editing any qubit on this port updates them all';
                }
            });
        });
    }
    // After an apply, clean EVERY LINKED cell that writes a just-committed node (not
    // only those in the batch), so applying one linked cell updates all its
    // table-wide siblings from the server's echoed value. Non-linkable cells are
    // skipped: they're never cross-synced (a dead-ended optional leaf shares its
    // bare parent path with distinct fields — snapping them would corrupt them).
    function _syncAppliedAcrossTable(results) {
        var t = table(); if (!t) return;
        var byResolved = {};
        (results || []).forEach(function (res) { if (res.resolved_path) byResolved[res.resolved_path] = res; });
        if (!Object.keys(byResolved).length) return;
        _cells(t).forEach(function (c) {
            if (c.getAttribute('data-linkable') !== '1') return;   // only linked siblings cross-sync
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

    // ── column drag-resize (override the value-fit width per column) ─────────
    // The cells stay size-attr value-fit by default; dragging a header's right
    // edge pins that one column's width via a managed <style> rule, double-click
    // reverts it to value-fit. Persisted per-browser. Mirrors the /datasets resize.
    var RESIZE_KEY = 'quam_bulk_col_widths';
    var _colWidths = {};
    var _bulkResize = null, _bulkResizeJustEnded = false;
    function _loadColWidths() { try { _colWidths = JSON.parse(localStorage.getItem(RESIZE_KEY) || '{}') || {}; } catch (e) { _colWidths = {}; } }
    function _saveColWidths() { try { localStorage.setItem(RESIZE_KEY, JSON.stringify(_colWidths)); } catch (e) {} }
    function _colWidthStyleEl() {
        var el = document.getElementById('bulk-col-width-style');
        if (!el) { el = document.createElement('style'); el.id = 'bulk-col-width-style'; document.head.appendChild(el); }
        return el;
    }
    function _applyColWidthStyle() {
        var css = '';
        for (var k in _colWidths) {
            var w = _colWidths[k];
            var ek = (window.CSS && CSS.escape) ? CSS.escape(k) : k;
            var wpx = w + 'px;min-width:' + w + 'px;max-width:' + w + 'px';
            // Constrain the th, the td wrapper AND the input — otherwise the td
            // grows to fit its content + padding and the column overshoots the
            // dragged width (and a narrow column would spill the input out).
            css += '#bulk-table th.bulk-col-head[data-col-key="' + ek + '"]{width:' + wpx + ';overflow:hidden}';
            css += '#bulk-table td[data-col-key="' + ek + '"]{width:' + wpx + ';overflow:hidden}';
            css += '#bulk-table td[data-col-key="' + ek + '"] .bulk-cell{width:' + w + 'px!important;min-width:' + w + 'px;max-width:' + w + 'px}';
        }
        _colWidthStyleEl().textContent = css;
        _updateTopScroll();
    }
    function _startColResize(e, key, th) {
        e.preventDefault(); e.stopPropagation();
        _bulkResize = { key: key, startX: e.clientX, startW: th ? th.offsetWidth : (_colWidths[key] || 80) };
        document.body.style.cursor = 'col-resize';
        document.addEventListener('mousemove', _onColResizeMove);
        document.addEventListener('mouseup', _onColResizeUp);
    }
    function _onColResizeMove(e) {
        if (!_bulkResize) return;
        var w = Math.max(30, _bulkResize.startW + (e.clientX - _bulkResize.startX));   // min, NO max
        _colWidths[_bulkResize.key] = w;
        _applyColWidthStyle();
    }
    function _onColResizeUp() {
        if (!_bulkResize) return;
        _saveColWidths();
        _bulkResize = null;
        _bulkResizeJustEnded = true;
        setTimeout(function () { _bulkResizeJustEnded = false; }, 0);   // swallow the post-drag click
        document.body.style.cursor = '';
        document.removeEventListener('mousemove', _onColResizeMove);
        document.removeEventListener('mouseup', _onColResizeUp);
    }
    function _autoFitColWidth(key) {   // double-click → drop the override, back to value-fit
        delete _colWidths[key];
        _saveColWidths();
        _applyColWidthStyle();
    }

    var BulkEdit = {
        mount: function (columns, bandMeta) {
            if (Array.isArray(columns)) COLS = columns;
            // An HTMX swap re-renders the tbody in server (default) order, so the
            // old sort no longer applies — clear it (the fresh header has no caret).
            sortKey = null; sortDir = 1;
            if (bandMeta && bandMeta.bands) BANDS = bandMeta.bands;
            var t = table();
            if (!t) return;
            // Restore the persisted search/filter before applySearch runs below.
            var sb0 = document.getElementById('bulk-search');
            if (sb0) { try { sb0.value = localStorage.getItem(SEARCH_KEY) || ''; } catch (e) {} }
            _loadColWidths();
            _applyColWidthStyle();   // re-apply persisted column widths after each (re)render
            _buildColMenu();
            _applyColumnVisibility();
            _recomputeStats();
            _setupTopScroll();
            _applyFont();
            _applyHint();
            _updateTopScroll();
            // flag any already-out-of-band ports on load
            Array.prototype.slice.call(t.querySelectorAll('.bulk-cell[data-lo-field]')).forEach(_validateBand);
            _updateBandWarnCount();
            _markLinkedCells();   // tag shared physical-port cells so edits mirror across the port
            var fsCb = document.getElementById('bulk-freq-sync');
            if (fsCb) fsCb.checked = _freqSyncOn();   // restore the 🔗 toggle across swaps
            if (t._bulkBound) { _refreshGlobal(); return; }
            t._bulkBound = true;

            // Discard-intent guard: a pointerdown on Reset fires BEFORE the focused
            // cell's focusout, so record it and let focusout skip its click-away
            // commit (otherwise "Reset" would commit the focused row, not discard it).
            var _rstBtn = document.getElementById('bulk-reset');
            if (_rstBtn && !_rstBtn._resetGuardBound) {
                _rstBtn._resetGuardBound = true;
                _rstBtn.addEventListener('pointerdown', function () { BulkEdit._resetPressTs = Date.now(); });
            }

            // Header sort is delegated (no inline onclick) so a click on a resize
            // handle — or a click right after a drag — never triggers a sort.
            t.addEventListener('click', function (e) {
                if (e.target.closest && e.target.closest('.bulk-resize-handle')) return;
                if (_bulkResizeJustEnded) return;
                var th = e.target.closest && e.target.closest('thead th[data-col-key]');
                if (th && th.getAttribute('data-col-key')) BulkEdit.sort(th.getAttribute('data-col-key'));
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
                if (!cell) return;
                cell.classList.remove('bulk-cell-bad');
                _markCellDirty(cell);
                if (cell.classList.contains('bulk-cell-linked')) _mirrorLinked(cell);
                _softMirrorFreq(cell);   // f_01 ↔ RF_frequency (soft, coupled-at-focus)
                _refreshRow(_rowOf(cell));
                _refreshGlobal();
                if (cell.hasAttribute('data-lo-field')) { _validateBand(cell); _updateBandWarnCount(); }
            });
            // f_01/RF coupling is decided at focus (see _freqFocus) and the 🔗 mark
            // shows only while a coupled freq cell is focused.
            t.addEventListener('focusin', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (cell && FREQ_TWIN[_colKeyOf(cell)]) _freqFocus(cell);
            });
            t.addEventListener('focusout', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell) return;
                if (FREQ_TWIN[_colKeyOf(cell)]) _freqBlur(cell);
                // Tab / click-away COMMITS the row (like Enter). Only when focus
                // leaves the row entirely — moving between cells in the SAME row
                // (or onto the row's own Apply button) does NOT commit, so a
                // multi-cell edit still batches on the final blur. applyRow is a
                // no-op when nothing is dirty and self-guards double-submit via
                // btn.disabled, so this is safe to fire on every row-exit blur.
                var row = _rowOf(cell);
                var to = e.relatedTarget;
                if (to && row && row.contains(to)) return;   // still inside the row
                // Focus went to an "Apply all" / "Apply to live" button → let IT
                // commit the whole dirty set; a per-row commit here would double-fire
                // the same row (two change-log entries for one edit). Same for the
                // Reset button: a click-away commit would turn "discard" into a
                // COMMIT of the focused row. relatedTarget is null in some engines,
                // so also honour a pointerdown-on-Reset flag that fires before blur.
                if (BulkEdit._resetPressTs && (Date.now() - BulkEdit._resetPressTs) < 1000) return;
                if (to && to.closest && to.closest('#bulk-apply-all, #bulk-apply-sync, #bulk-reset')) return;
                var b = row && row.querySelector('.bulk-row-apply');
                if (b && !b.disabled) BulkEdit.applyRow(b);
            });
            // Enter applies the row; arrow keys move between cells (spreadsheet nav).
            t.addEventListener('keydown', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell) return;
                if (e.key === 'Enter') {
                    e.preventDefault();
                    var b = _rowOf(cell).querySelector('.bulk-row-apply');
                    if (b && !b.disabled) BulkEdit.applyRow(b);
                    return;
                }
                var dir = { ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.key];
                // left/right only when caret is at the input edge, so in-cell editing still works
                if (e.key === 'ArrowLeft' && cell.selectionStart === 0) dir = [0, -1];
                if (e.key === 'ArrowRight' && cell.selectionStart === cell.value.length) dir = [0, 1];
                if (!dir) return;
                var move = _gridMove(cell, dir[0], dir[1]);
                if (move) { e.preventDefault(); move.focus(); move.select && move.select(); }
            });
            t.addEventListener('mouseover', function (e) { _hoverBA(e, true); });
            t.addEventListener('mouseout', function (e) { _hoverBA(e, false); });

            var search = document.getElementById('bulk-search');
            if (search) search.addEventListener('input', function () {
                try { localStorage.setItem(SEARCH_KEY, search.value); } catch (e) {}
                applySearch();
            });

            // nav guard: warn before losing unapplied edits
            if (!window._bulkNavGuard) {
                window._bulkNavGuard = true;
                window.addEventListener('beforeunload', function (ev) {
                    var tt = table();
                    if (tt && _cells(tt).some(_isDirty)) { ev.preventDefault(); ev.returnValue = ''; return ''; }
                });
                document.body.addEventListener('htmx:beforeSwap', function (ev) {
                    var tt = table();
                    if (tt && ev.detail && ev.detail.target && ev.detail.target.id === 'table-pane'
                        && _cells(tt).some(_isDirty)) {
                        if (!window.confirm('You have unapplied edits in Live State Edit. Leave and discard them?')) {
                            ev.preventDefault();
                        }
                    }
                });
            }
            // Cross-surface: when another surface (Review modal / inspector / plot
            // popup) edits the working copy, reflect it here. Skip our OWN apply and
            // never clobber in-progress typing (re-render only when clean).
            if (!window._bulkStateListener) {
                window._bulkStateListener = true;
                document.addEventListener('quam:state-changed', function () {
                    var tt = table();
                    if (!tt || window._bulkSelfEdit) return;
                    if (_cells(tt).some(_isDirty)) return;   // don't wipe unsaved qubit edits
                    // …and don't wipe unapplied edits in the PAIR grid or the
                    // All-values tab either: those live in #table-pane too, so a
                    // background re-GET would swap them out and trip pair-edit.js's
                    // nav guard, firing a surprise "discard?" confirm from an event
                    // the user never triggered. Both surfaces mark dirty cells/rows
                    // with a class, so the check stays decoupled.
                    if (document.querySelector('#bulk-pair-table .dirty')
                            || document.querySelector('.av-row-dirty')) return;
                    if (window.htmx) htmx.ajax('GET', '/bulk', { target: '#table-pane', swap: 'innerHTML' });
                });
            }
            _refreshGlobal();
        },

        applyRow: function (btn) {
            var tr = btn.closest('tr'); if (!tr) return;
            var dirty = _cells(tr).filter(_isDirty);
            if (!dirty.length) return;
            // Surface (don't block) an LO band conflict at commit. Only prompt when a
            // conflict is actually present — never nag a clean row (A10).
            var bw = _bandWarnLine(dirty);
            if (bw && !window.confirm('Apply this edit?' + bw)) return;
            btn.disabled = true; btn.textContent = '…';
            _applyCells(dirty, tr, false).then(function (res) {
                btn.textContent = res.ok ? '✓' : 'Apply';
                if (res.ok) setTimeout(function () { btn.textContent = 'Apply'; }, 900);
                _refreshRow(tr); _refreshGlobal(); _recomputeStats();
            });
        },

        // syncAfter (the ⚡ "Apply to live now" button): once these edits land in the
        // working state, immediately pull the live chip + re-apply them on top + push
        // to the live chip in one shot (doStateSync('apply')) — no review-modal trip.
        applyAll: function (syncAfter) {
            var t = table(); if (!t) return;
            var rows = _rows().filter(function (tr) { return _cells(tr).some(_isDirty); });
            if (!rows.length) return;
            var n = _dirtyCount(t);   // unique physical changes (linked siblings count once)
            // Surface any LO band conflict among ALL dirty cells in the apply set —
            // appended to the confirm, never a hard block (A10).
            var bw = _bandWarnLine(_cells(t).filter(_isDirty));
            if (!window.confirm('Apply ' + n + ' edit' + (n === 1 ? '' : 's') + ' across ' + rows.length +
                ' qubit' + (rows.length === 1 ? '' : 's') +
                (syncAfter ? ' and push to the live chip?' : ' to the working state?') + bw)) return;
            var all = document.getElementById('bulk-apply-all');
            if (all) { all.disabled = true; all.textContent = 'Applying…'; }
            var apsBtn = document.getElementById('bulk-apply-sync'); if (apsBtn) apsBtn.disabled = true;
            var i = 0, failures = 0, succeeded = 0, lastTray = null, firstFailRow = null;
            var seenGlobal = {};   // dedup a shared-port node across rows → written once
            function next() {
                if (i >= rows.length) {
                    // Swap the pending tray + unsaved-changes banner ONCE, with the
                    // final tray HTML. We can't let the last row's _applyCells do it:
                    // when its cells all dedup to a shared-port node an earlier row
                    // already wrote (the common linked-port case — power/LO/band/gain),
                    // that row posts nothing and returns no tray_html, so the banner
                    // would stay inactive even though edits ARE now pending. The last
                    // NON-empty response already reflects the full change log (deduped
                    // rows are server-side no-ops), so lastTray is the correct final state.
                    if (lastTray && window._swapPendingTray) {
                        window._bulkSelfEdit = true;
                        window._swapPendingTray(lastTray);
                        window._bulkSelfEdit = false;
                    }
                    if (all) all.textContent = failures ? ('Apply all (' + failures + ' failed)') : 'Apply all';
                    _refreshGlobal(); _recomputeStats();
                    // On a tall table the tiny "(N failed)" label + off-screen red rows
                    // are easy to miss. Surface a status-bar toast and scroll the first
                    // failing row into view so the failure can't be silently overlooked (A16).
                    if (failures) {
                        var msg = succeeded + ' applied, ' + failures + ' failed — see the red row' +
                            (failures === 1 ? '' : 's');
                        if (window.showToast) window.showToast(msg, 'warning');
                        if (firstFailRow && firstFailRow.scrollIntoView) {
                            firstFailRow.scrollIntoView({ block: 'center', behavior: 'smooth' });
                        }
                    }
                    // ⚡ one-click: only push to the live chip if every edit committed
                    // cleanly (never push a half-applied set). applyEditsToLive routes
                    // safely (pending-only → merge; saved-but-unapplied → steer to tray).
                    if (syncAfter && !failures && window.applyEditsToLive) window.applyEditsToLive();
                    return;
                }
                var tr = rows[i++];
                // Per-row atomic batch, ALL silent: the tray is swapped exactly once at
                // the end (above) with the final HTML, never N times mid-loop.
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
            // Clear any leftover per-row error from a previous failed Apply
            // (mirrors _applyCells, which clears it on the next apply).
            _rows().forEach(function (tr) {
                var e = tr.querySelector('.bulk-row-error');
                if (e) { e.hidden = true; e.textContent = ''; }
            });
            _rows().forEach(_refreshRow);
            _refreshGlobal();
        },

        sort: sort,
        setFreqSync: function (on) {
            try { localStorage.setItem(FREQSYNC_KEY, on ? '1' : '0'); } catch (e) {}
            var cb = document.getElementById('bulk-freq-sync'); if (cb) cb.checked = !!on;
        },
        setFont: function (scale) { try { localStorage.setItem(FONT_KEY, String(scale)); } catch (e) {} _applyFont(); },
        setLetterSpacing: function (ls) { try { localStorage.setItem(LS_KEY, String(ls)); } catch (e) {} _applyFont(); },
        toggleBold: function () {
            var on = false; try { on = localStorage.getItem(BOLD_KEY) === '1'; } catch (e) {}
            try { localStorage.setItem(BOLD_KEY, on ? '0' : '1'); } catch (e) {}
            _applyFont();
        },
        toggleHint: function () {
            var hidden = false; try { hidden = localStorage.getItem(HINT_KEY) === '1'; } catch (e) {}
            try { localStorage.setItem(HINT_KEY, hidden ? '0' : '1'); } catch (e) {}
            _applyHint();
        },
        showAllColumns: function () { _saveHidden(new Set()); _buildColMenu(); _applyColumnVisibility(); _recomputeStats(); },
        resetColumns: function () { try { localStorage.removeItem(HIDE_KEY); } catch (e) {} _buildColMenu(); _applyColumnVisibility(); _recomputeStats(); },

        // marker-only refresh from a server `modified` delta (keeps in-progress typing)
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
            var ncell = nr.querySelector('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"] .bulk-cell');
            return ncell;
        }
        if (dc) {
            var tds = Array.prototype.slice.call(tr.querySelectorAll('.bulk-td:not(.bulk-col-hidden):not(.bulk-search-hidden)'));
            var ci = tds.indexOf(td);
            var ntd = tds[ci + dc];
            return ntd ? ntd.querySelector('.bulk-cell') : null;
        }
        return null;
    }

    window.BulkEdit = BulkEdit;
    // Restore the persisted density scale onto :root at load (this script is eager
    // on every page), so the Review modal honors the user's font/bold choice even
    // if they never opened Bulk Edit this session.
    try { _applyGlobalScale(); } catch (e) {}
    if (document.getElementById('bulk-table') && !window.__bulkAutoMounted) {
        // full-page load path; the partial calls mount(columns) itself with the model
    }
})();
