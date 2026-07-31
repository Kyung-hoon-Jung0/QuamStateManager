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
    // r7: dynamic columns default to ALL VISIBLE (the r6 opt-in model buried
    // fields the search couldn't find) — so this persists the HIDDEN set, not
    // an enabled set; empty/absent means "hide nothing".
    var DYNHIDDEN_KEY = 'quam_bulk_dynhidden';
    // ⚏ Qubits (row) picker: per-CHIP persisted hidden-qubit set — chips differ
    // in qubit sets, and hidden-set semantics mean new qubits default to visible
    // (same trick as the column picker's HIDE_KEY).
    var QHIDE_PREFIX = 'quam_bulk_qhidden:';
    var COLS = [];                 // column model from the server: {key,label,section,unit,default_on[,dyn]}
    var BANDS = {};                // {"1":[lo,hi], ...} MW-FEM band ranges (from server)
    var DYN = [];                  // FULL dynamic model: {key,label,section,unit,kind}
    var QMETA = { chip: '', qubits: [] };   // ⚏ Qubits payload: {id, grid:"c,r"|null} per qubit
    var _dynHintKeys = [];         // dyn keys matching the current search but hidden
    var _reopenColvis = false;     // r7: keep the Properties menu open across a dyn-toggle reload
    // ── search-typing performance (audit: "typing in Live Edit is slow") ─────
    // A real chip renders ~150 columns × ~30 rows ≈ 2000 cells; re-scanning all
    // of them AND re-toggling their classes per keystroke froze typing.
    var _searchTimer = null;       // debounce: one applySearch per typing pause
    var _hayCache = null;          // { key, rowMap: WeakMap(row→[hay]), colHay } across keystrokes
    var _lastDirtySig = null;      // ⚏ picker refresh gate: dirty-ID set signature
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

    // ── dynamic columns (r6 item 4, r7 default-visible): persisted HIDDEN set
    // + /bulk patching ── The hidden dyn-column keys live in localStorage and
    // ride EVERY /bulk GET via a document-level configRequest listener (the
    // pulses-page filter-persistence precedent) — so htmx pane reloads,
    // cross-surface refreshes and sidebar nav all keep the user's opt-OUTs
    // without threading dynhide through each caller. ONLY the exact /bulk
    // path is patched (never /bulk/all-values), and the param is
    // stripped+set so no duplicate appends. Absent/empty ⇒ hide nothing ⇒
    // every derived column renders — the default.
    function _dynHidden() {
        try {
            var a = JSON.parse(localStorage.getItem(DYNHIDDEN_KEY) || '[]');
            return Array.isArray(a) ? a : [];
        } catch (e) { return []; }
    }
    function _saveDynHidden(arr) {
        try { localStorage.setItem(DYNHIDDEN_KEY, JSON.stringify(arr)); } catch (e) {}
    }
    function _bulkSetQueryParam(path, key, value) {
        var qIdx = path.indexOf('?');
        var base = qIdx >= 0 ? path.slice(0, qIdx) : path;
        var qs = qIdx >= 0 ? path.slice(qIdx + 1) : '';
        var parts = qs ? qs.split('&').filter(function (p) {
            return p && decodeURIComponent(p.split('=')[0]) !== key;
        }) : [];
        if (value !== null && value !== undefined && value !== '') {
            parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(value));
        }
        return base + (parts.length ? '?' + parts.join('&') : '');
    }
    if (!window._bulkDynColsCfgBound) {
        window._bulkDynColsCfgBound = true;
        document.addEventListener('htmx:configRequest', function (evt) {
            var p = evt.detail && evt.detail.path;
            if (typeof p !== 'string' || p.split('?')[0] !== '/bulk') return;
            var keys = _dynHidden();
            evt.detail.path = _bulkSetQueryParam(p, 'dynhide', keys.length ? keys.join(',') : '');
            if (evt.detail.parameters) delete evt.detail.parameters['dynhide'];
        });
    }
    // Re-GET /bulk into the table pane — the same idiom the cross-surface
    // state-changed listener uses; configRequest re-attaches dynhide.
    function _reloadPane() {
        if (window.htmx) htmx.ajax('GET', '/bulk', { target: '#table-pane', swap: 'innerHTML' });
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
    // (r6 item 5: the dismissible boxed hint became a native <details> popover
    //  next to Properties — no JS/persistence needed; closed by default.)

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
            if (c.dyn) return;   // enabled dynamic columns live in the groups below
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
        // r6 item 4 / r7: the FULL derived model as collapsible per-section
        // groups, DEFAULT VISIBLE — a checkbox toggles the key into
        // quam_bulk_dynhidden and re-GETs the pane (the server renders every
        // dyn column except the hidden keys — nothing to hide/show locally).
        // A group starts open only when it has a hidden column (needs
        // attention); otherwise it stays collapsed so the popover itself
        // doesn't turn into a wall of already-visible checkboxes.
        if (DYN.length) {
            var hidden = {};
            _dynHidden().forEach(function (k) { hidden[k] = true; });
            var dynBySec = {}, dynOrder = [], dynNotes = [];
            DYN.forEach(function (c) {
                if (c.kind === 'note') { dynNotes.push(c); return; }
                if (!dynBySec[c.section]) { dynBySec[c.section] = []; dynOrder.push(c.section); }
                dynBySec[c.section].push(c);
            });
            html += '<div class="bulk-colvis-sec bulk-colvis-dyn-head">All properties (derived from this chip)</div>';
            dynOrder.forEach(function (sec) {
                var cs = dynBySec[sec];
                var nHidden = cs.filter(function (c) { return hidden[c.key]; }).length;
                html += '<details class="bulk-colvis-dyn"' + (nHidden ? ' open' : '') +
                    '><summary>' + _esc(sec) + ' <span class="muted">(' +
                    (nHidden ? (cs.length - nHidden) + ' of ' : '') + cs.length + ')</span></summary>';
                cs.forEach(function (c) {
                    html += '<label class="bulk-colvis-item"><input type="checkbox" data-dyn-toggle="' + _esc(c.key) + '"' +
                        (hidden[c.key] ? '' : ' checked') + '> ' + _esc(c.label) +
                        (c.unit ? ' <span class="unit muted">(' + _esc(c.unit) + ')</span>' : '') +
                        (c.kind === 'listedit' ? ' <span class="muted" title="list — edited as JSON">▦</span>'
                            : (c.kind === 'runtime' ? ' <span class="muted" title="runtime — read-only">⟳</span>' : '')) +
                        '</label>';
                });
                html += '</details>';
            });
            dynNotes.forEach(function (c) {
                html += '<div class="bulk-colvis-note muted">' + _esc(c.label) + '</div>';
            });
        }
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
        menu.querySelectorAll('[data-dyn-toggle]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                var k = cb.getAttribute('data-dyn-toggle');
                var arr = _dynHidden().filter(function (x) { return x !== k; });
                if (!cb.checked) arr.push(k);
                _saveDynHidden(arr);
                // A dyn toggle needs a server round-trip (unlike curated columns,
                // which just show/hide client-side) — the reload swaps #table-pane
                // wholesale, which would otherwise reset this <details> to closed
                // (review-r7: "checking a box collapses the menu").
                _reopenColvis = true;
                _reloadPane();
            });
        });
    }
    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── ⚏ Qubits — which ROWS are shown (mirror of the Properties picker) ────
    // Per-chip persisted HIDDEN set; chip-map (grid_location "c,r", row 0 at
    // the BOTTOM → Y flipped) + grouped checkbox list. A row with an unsaved
    // edit can never be hidden — "Apply all" stays "apply what you see". The
    // pair grid follows: a pair hides when either resolved member is hidden
    // (fail-open when the pair id can't be resolved against the qubit ids).
    function _qKey() { return QHIDE_PREFIX + (QMETA.chip || 'chip'); }
    function _qHidden() {
        try {
            var a = JSON.parse(localStorage.getItem(_qKey()) || '[]');
            return new Set(Array.isArray(a) ? a : []);
        } catch (e) { return new Set(); }
    }
    function _saveQHidden(set) {
        try { localStorage.setItem(_qKey(), JSON.stringify(Array.from(set))); } catch (e) {}
    }
    function _rowDirty(tr) { return _cells(tr).some(_isDirty); }
    function _qGroupOf(id) {
        var m = /^q([A-Za-z]+)/.exec(id);
        return m ? m[1].toUpperCase() : '·';
    }
    function _qDirtyIds() {
        var d = {};
        _rows().forEach(function (r) {
            if (_rowDirty(r)) d[r.getAttribute('data-qubit') || ''] = true;
        });
        return d;
    }

    // Core visibility pass — NO applySearch/_recomputeStats here: this also runs
    // from _refreshGlobal (every cell keystroke), and re-running the search there
    // could hide the very row being edited mid-keystroke (its cell value can stop
    // matching a value token).
    function _applyQubitVisCore() {
        var t = table(); if (!t) return;
        var hid = _qHidden();
        var rows = _rows();
        var shown = 0;
        rows.forEach(function (r) {
            var id = r.getAttribute('data-qubit') || '';
            var off = hid.has(id) && !_rowDirty(r);   // unsaved edits never vanish
            r.classList.toggle('bulk-qubit-off', off);
            if (!off) shown++;
        });
        var pill = document.getElementById('bulk-qubit-pill');
        if (pill) {
            pill.hidden = shown === rows.length;
            pill.textContent = shown + ' of ' + rows.length + ' qubits — Show all';
        }
        _applyPairFollow(hid);
    }
    function applyQubitVis() {
        _applyQubitVisCore();
        _recomputeStats();   // min/max + extreme colouring over the visible scope
        applySearch();       // combined "N of M" row count
    }

    function _pairMembers(pid) {
        // Pair ids occur as BOTH 'qA1-qA2' and 'qA2-A1' — resolve every dash
        // segment against the chip's real qubit ids ('A1' → 'qA1'); anything
        // unresolved ⇒ [] ⇒ the pair is never hidden by the qubit selection.
        var ids = QMETA.qubits.map(function (q) { return q.id; });
        var out = [];
        String(pid || '').split('-').forEach(function (p) {
            if (ids.indexOf(p) >= 0) out.push(p);
            else if (ids.indexOf('q' + p) >= 0) out.push('q' + p);
        });
        return out.length === 2 ? out : [];
    }
    function _applyPairFollow(hid) {
        var pt = document.getElementById('bulk-pair-table'); if (!pt) return;
        Array.prototype.slice.call(pt.querySelectorAll('tbody tr[data-pair]')).forEach(function (r) {
            var m = _pairMembers(r.getAttribute('data-pair'));
            var off = m.length === 2 && (hid.has(m[0]) || hid.has(m[1]));
            if (off && _rowDirty(r)) off = false;
            r.classList.toggle('bulk-qubit-off', off);
        });
    }

    function _buildQubitMenu() {
        var menu = document.getElementById('bulk-qubitvis-menu');
        if (!menu) return;
        var hid = _qHidden();
        var dirty = _qDirtyIds();
        var qs = QMETA.qubits;
        var html = '<div class="bulk-colvis-actions">' +
            '<button type="button" class="btn-xs outline" data-qsel="all">All</button>' +
            '<button type="button" class="btn-xs outline" data-qsel="none">None</button>' +
            '<button type="button" class="btn-xs outline" data-qsel="invert">Invert</button></div>';

        // chip map — only when ≥2 qubits carry a parseable grid_location
        var sited = qs.filter(function (q) { return /^\s*\d+\s*,\s*\d+\s*$/.test(q.grid || ''); });
        if (sited.length >= 2) {
            var maxR = -Infinity, minC = Infinity;
            sited.forEach(function (q) {
                var p = q.grid.split(',');
                var c = parseInt(p[0], 10), r = parseInt(p[1], 10);
                if (r > maxR) maxR = r;
                if (c < minC) minC = c;
            });
            html += '<div class="bulk-colvis-sec">Chip map — click to toggle</div>' +
                '<div class="bulk-qmap">';
            sited.forEach(function (q) {
                var p = q.grid.split(',');
                var c = parseInt(p[0], 10), r = parseInt(p[1], 10);
                var off = hid.has(q.id) && !dirty[q.id];
                html += '<button type="button" class="bulk-qmap-cell' + (off ? ' off' : '') + '"' +
                    ' style="grid-column:' + (c - minC + 1) + ';grid-row:' + (maxR - r + 1) + '"' +
                    ' data-qtoggle="' + _esc(q.id) + '" title="' + _esc(q.id) +
                    (dirty[q.id] ? ' — has an unsaved edit (cannot hide)'
                                 : ' — click to ' + (off ? 'show' : 'hide')) + '">' +
                    _esc(q.id) + '</button>';
            });
            html += '</div>';
        }

        // grouped checkbox list (by row letter — feedline groups on lettered chips)
        var groups = [], seen = {};
        qs.forEach(function (q) {
            var g = _qGroupOf(q.id);
            if (!seen[g]) { seen[g] = true; groups.push(g); }
        });
        var multi = groups.length > 1;
        groups.forEach(function (g) {
            if (multi) {
                html += '<div class="bulk-colvis-sec bulk-qgroup-head"><span>' + _esc(g) + '</span>' +
                    '<span><button type="button" class="btn-xs outline" data-qgroup-on="' + _esc(g) + '">show</button> ' +
                    '<button type="button" class="btn-xs outline" data-qgroup-off="' + _esc(g) + '">hide</button></span></div>';
            }
            qs.forEach(function (q) {
                if (_qGroupOf(q.id) !== g) return;
                var isDirty = !!dirty[q.id];
                var checked = !hid.has(q.id) || isDirty;
                html += '<label class="bulk-colvis-item bulk-qubit-item"><span>' +
                    '<input type="checkbox" data-qcb="' + _esc(q.id) + '"' +
                    (checked ? ' checked' : '') + (isDirty ? ' disabled' : '') + '> ' +
                    _esc(q.id) + '</span>' +
                    (isDirty
                        ? '<span class="bulk-qdirty" title="This qubit has an unsaved edit — apply or reset it first">unsaved edit</span>'
                        : '<button type="button" class="bulk-qonly" data-qonly="' + _esc(q.id) + '" title="Show only ' + _esc(q.id) + '">only</button>') +
                    '</label>';
            });
        });
        menu.innerHTML = html;

        if (!menu._qBound) {
            menu._qBound = true;
            menu.addEventListener('click', function (ev) {
                var b = ev.target.closest('[data-qsel],[data-qtoggle],[data-qonly],[data-qgroup-on],[data-qgroup-off]');
                if (!b) return;
                ev.preventDefault();
                var hid2 = _qHidden();
                var dirty2 = _qDirtyIds();
                var all = QMETA.qubits.map(function (q) { return q.id; });
                if (b.hasAttribute('data-qsel')) {
                    var mode = b.getAttribute('data-qsel');
                    if (mode === 'all') hid2 = new Set();
                    else if (mode === 'none') hid2 = new Set(all.filter(function (id) { return !dirty2[id]; }));
                    else all.forEach(function (id) {
                        if (dirty2[id]) { hid2.delete(id); return; }
                        if (hid2.has(id)) hid2.delete(id); else hid2.add(id);
                    });
                } else if (b.hasAttribute('data-qtoggle')) {
                    var id = b.getAttribute('data-qtoggle');
                    if (dirty2[id] || hid2.has(id)) hid2.delete(id); else hid2.add(id);
                } else if (b.hasAttribute('data-qonly')) {
                    var only = b.getAttribute('data-qonly');
                    hid2 = new Set(all.filter(function (id) { return id !== only && !dirty2[id]; }));
                } else {
                    var g = b.getAttribute('data-qgroup-on') || b.getAttribute('data-qgroup-off');
                    var show = b.hasAttribute('data-qgroup-on');
                    all.forEach(function (id) {
                        if (_qGroupOf(id) !== g) return;
                        if (show || dirty2[id]) hid2.delete(id); else hid2.add(id);
                    });
                }
                _saveQHidden(hid2);
                applyQubitVis();
                _buildQubitMenu();
            });
            menu.addEventListener('change', function (ev) {
                var cb = ev.target.closest('input[data-qcb]');
                if (!cb) return;
                var hid2 = _qHidden();
                var id = cb.getAttribute('data-qcb');
                if (cb.checked) hid2.delete(id);
                else if (!_qDirtyIds()[id]) hid2.add(id);
                _saveQHidden(hid2);
                applyQubitVis();
                _buildQubitMenu();
            });
        }
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

        // gather per-column cell haystacks (only over checkbox-visible columns).
        // CACHED across keystrokes — rebuilding ~2000 lowercased strings +
        // closest() walks per key was half the typing lag. Invalidated by
        // _refreshGlobal (any cell input/commit/reset/JSON edit funnels
        // through it) and keyed on the hidden-column set; rows are keyed by
        // ELEMENT (WeakMap), so sorting never stales the cache.
        var rows = _rows();
        var hayKey = Array.from(hide).sort().join(',');
        if (!_hayCache || _hayCache.key !== hayKey) {
            _hayCache = { key: hayKey, rowMap: new WeakMap(), colHay: null };
        }
        var colHay = _hayCache.colHay;
        if (!colHay) {
            colHay = {};
            visCols.forEach(function (c) { colHay[c.key] = []; });
            rows.forEach(function (r) {
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
                _hayCache.rowMap.set(r, hs);
            });
            _hayCache.colHay = colHay;
        }
        var rowHay = rows.map(function (r) { return _hayCache.rowMap.get(r) || []; });

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
            // the count reflects what's actually on screen: search AND ⚏ Qubits
            if (vis && !r.classList.contains('bulk-qubit-off')) shown++;
        });
        var cnt = document.getElementById('bulk-search-count');
        if (cnt) cnt.textContent = q ? (shown + ' of ' + rows.length) : '';
        // r6 item 4 / r7: the search also scans dynamic columns the user has
        // explicitly HIDDEN (label/key/section, AND over tokens) — now a rare
        // case since everything is visible by default, but still actionable:
        // "1 hidden column matches — Show".
        var hint = document.getElementById('bulk-dyncol-hint');
        if (hint) {
            _dynHintKeys = [];
            if (q.length >= 2 && DYN.length) {
                var hiddenKeys = {};
                _dynHidden().forEach(function (k) { hiddenKeys[k] = true; });
                DYN.forEach(function (c) {
                    if (c.kind === 'note' || !hiddenKeys[c.key]) return;
                    var hay = (c.label + ' ' + c.key + ' ' + c.section).toLowerCase();
                    if (tokens.every(function (tok) { return hay.indexOf(tok) >= 0; })) {
                        _dynHintKeys.push(c.key);
                    }
                });
            }
            hint.hidden = !_dynHintKeys.length;
            if (_dynHintKeys.length) {
                hint.textContent = _dynHintKeys.length + ' hidden column' +
                    (_dynHintKeys.length === 1 ? '' : 's') + ' match — Show';
            }
        }
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
            // NATURAL id sort (numeric:true): q2 before q10 — plain string
            // compare listed double-digit chips as q1, q10, q11, q2.
            if (key === '__id__') {
                return va.localeCompare(vb, undefined,
                    { numeric: true, sensitivity: 'base' }) * sortDir;
            }
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
            var allCells = Array.prototype.slice.call(
                t.querySelectorAll('[data-col-key="' + (window.CSS && CSS.escape ? CSS.escape(c.key) : c.key) + '"] .bulk-cell'));
            allCells.forEach(function (cell) { cell.classList.remove('cell-best', 'cell-worst'); });
            // Stats + extreme colouring cover the VISIBLE scope only — with a ⚏
            // Qubits selection active, chip-wide extremes on hidden rows would
            // mislead (and could colour nothing the user can see).
            var cells = allCells.filter(function (cell) {
                var tr = cell.closest('tr');
                return !(tr && tr.classList.contains('bulk-qubit-off'));
            });
            var nums = [];
            cells.forEach(function (cell) { var n = _num(cell.value); if (n !== null) nums.push(n); });
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
        // Any path through here means cell values/dirty state may have changed
        // (typing, mirror writes, apply, reset, JSON edits all funnel through)
        // — drop the search-haystack cache so the next search sees fresh text.
        _hayCache = null;
        // ⚏ Qubits: the dirty state feeds the picker (disabled entries) and the
        // hidden-row force-show guard — refresh both when the dirty-ID SET
        // changes (gated: rebuilding the picker menu on every keystroke of a
        // cell edit was measurable typing overhead). Core pass only: re-running
        // the SEARCH here could hide the row being edited the moment its value
        // stops matching a value token.
        var sig = Object.keys(_qDirtyIds()).sort().join(',');
        if (sig !== _lastDirtySig) {
            _lastDirtySig = sig;
            _applyQubitVisCore();
            _buildQubitMenu();
        }
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
        var _postBatch = function (ups, fspAck) {
            var payload = { updates: ups, expect_chip: window.__chipToken || '' };
            if (fspAck) payload.fsp_ack = fspAck;
            return fetch('/field/edit-batch', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function (resp) { return resp.json().then(function (j) { return { status: resp.status, body: j }; }); });
        };
        return _postBatch(updates, null)
            .then(function handleR(r) {
                // r12-B: an FSP cell in this batch → the compensation offer
                // first; nothing was committed. comp = resend everything plus
                // the compensated amps in ONE batch (one gid, one Ctrl+Z).
                if (r.status === 409 && r.body && r.body.fsp_compensation
                    && window._openFspPopup) {
                    return new Promise(function (resolve) {
                        window._openFspPopup(r.body.fsp_compensation, function (mode, plan) {
                            if (mode === 'cancel') {
                                resolve({ ok: false, cancelled: true });
                                return;    // nothing committed; cells stay dirty
                            }
                            var ups = mode === 'comp'
                                ? updates.concat(window._fspCompUpdates(plan))
                                : updates;
                            _postBatch(ups, mode).then(handleR).then(resolve);
                        });
                    });
                }
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
                        try { window._swapPendingTray(r.body.tray_html); }
                        finally { window._bulkSelfEdit = false; }
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
        mount: function (columns, bandMeta, dynModel, qubitMeta) {
            if (Array.isArray(columns)) COLS = columns;
            // An HTMX swap re-renders the tbody in server (default) order, so the
            // old sort no longer applies — clear it (the fresh header has no caret).
            sortKey = null; sortDir = 1;
            _hayCache = null; _lastDirtySig = null;   // fresh DOM → fresh caches
            if (bandMeta && bandMeta.bands) BANDS = bandMeta.bands;
            DYN = Array.isArray(dynModel) ? dynModel : [];
            if (qubitMeta && typeof qubitMeta === 'object') {
                QMETA = { chip: String(qubitMeta.chip || ''),
                          qubits: Array.isArray(qubitMeta.qubits) ? qubitMeta.qubits : [] };
            }
            var t = table();
            if (!t) return;
            // Restore the persisted search/filter before applySearch runs below.
            var sb0 = document.getElementById('bulk-search');
            if (sb0) { try { sb0.value = localStorage.getItem(SEARCH_KEY) || ''; } catch (e) {} }
            _loadColWidths();
            _applyColWidthStyle();   // re-apply persisted column widths after each (re)render
            _buildColMenu();
            // r7: a dyn-column toggle reloads #table-pane wholesale, which would
            // otherwise reset the fresh server-rendered <details> to closed —
            // reopen it right after the rebuilt menu is in the DOM.
            if (_reopenColvis) {
                _reopenColvis = false;
                var colvisMenu = document.getElementById('bulk-colvis-menu');
                var colvisDet = colvisMenu && colvisMenu.closest('details');
                if (colvisDet) colvisDet.open = true;
            }
            _buildQubitMenu();
            _applyColumnVisibility();
            _applyQubitVisCore();   // restore the persisted ⚏ Qubits selection
            _recomputeStats();
            _setupTopScroll();
            _applyFont();
            _updateTopScroll();
            // flag any already-out-of-band ports on load
            Array.prototype.slice.call(t.querySelectorAll('.bulk-cell[data-lo-field]')).forEach(_validateBand);
            _updateBandWarnCount();
            _markLinkedCells();   // tag shared physical-port cells so edits mirror across the port
            var fsCb = document.getElementById('bulk-freq-sync');
            if (fsCb) fsCb.checked = _freqSyncOn();   // restore the 🔗 toggle across swaps
            if (t._bulkBound) { _refreshGlobal(); return; }
            t._bulkBound = true;

            // Toolbar-press guard (docs/65, generalizing the old Reset-only stamp):
            // a pointerdown on ANY toolbar action fires BEFORE the focused cell's
            // focusout, so record it and let focusout skip its click-away row
            // commit. For Reset that commit would turn "discard" into a commit;
            // for Apply all / Apply&sync the racing row commit shrank the dirty
            // set and _refreshGlobal DISABLED the button before mouseup — the
            // browser then never delivered the click ("Apply all needs two
            // presses"). The relatedTarget check below misses this whenever the
            // browser doesn't focus buttons on mousedown (null relatedTarget).
            ['bulk-reset', 'bulk-apply-all', 'bulk-apply-sync'].forEach(function (bid) {
                var b = document.getElementById(bid);
                if (b && !b._toolbarGuardBound) {
                    b._toolbarGuardBound = true;
                    b.addEventListener('pointerdown', function () { BulkEdit._toolbarPressTs = Date.now(); });
                }
            });

            // Header sort is delegated (no inline onclick) so a click on a resize
            // handle, the column-history clock, or a click right after a drag
            // never triggers a sort.
            t.addEventListener('click', function (e) {
                if (e.target.closest && e.target.closest('.bulk-resize-handle')) return;
                if (e.target.closest && e.target.closest('.bulk-col-hist')) return;
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
                // so also honour the toolbar pointerdown stamp that fires before blur.
                if (BulkEdit._toolbarPressTs && (Date.now() - BulkEdit._toolbarPressTs) < 1000) return;
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
                if (e.key === 'Tab') {
                    // Tab/Shift+Tab hop between EDIT CELLS (spreadsheet
                    // convention) — never through the hover-reveal buttons in
                    // between — wrapping to the next/prev row at the row edge.
                    // Leaving the row commits it via the focusout handler above
                    // (same as click-away). At the grid's very first/last cell
                    // native Tab proceeds out of the grid.
                    var tnext = _tabMove(cell, e.shiftKey ? -1 : 1);
                    if (tnext) { e.preventDefault(); tnext.focus(); tnext.select && tnext.select(); }
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
                // DEBOUNCED (audit: typing here was slow): applySearch re-scans
                // the table and re-toggles ~2000 cells' classes — a full-table
                // reflow on a multi-MB DOM. One pass shortly after the last
                // keystroke instead of one per key.
                if (_searchTimer) clearTimeout(_searchTimer);
                _searchTimer = setTimeout(applySearch, 120);
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
                        // audit-r10: a stage/restore just replaced the state
                        // wholesale — typed text belongs to the OLD state, so
                        // the refresh proceeds without a veto prompt.
                        if (window._stateRestoredRefresh
                            && Date.now() - window._stateRestoredRefresh < 4000) return;
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
                        try { window._swapPendingTray(lastTray); }
                        finally { window._bulkSelfEdit = false; }
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

        // r6 item 4 / r7: un-hide every dynamic column the current search
        // matched (the "N hidden columns match — Show" chip) and re-render.
        showMatchedDynCols: function () {
            if (!_dynHintKeys.length) return;
            var arr = _dynHidden().filter(function (k) { return _dynHintKeys.indexOf(k) < 0; });
            _saveDynHidden(arr);
            _reopenColvis = true;
            _reloadPane();
        },

        // r6 item 4: whole-value JSON editor for list cells (qubit-grid listedit
        // previews AND the pair grid's ▦ badges). Prefills from /field/peek's RAW
        // value, saves the PARSED value through the same atomic /field/edit-batch
        // path (non-string values skip server-side re-parse, so the list commits
        // typed-correctly); client parse errors + server 400s render inline.
        openJsonCell: function (path, btn) {
            var old = document.getElementById('bulk-json-modal');
            if (old && old.parentNode) old.parentNode.removeChild(old);
            var ov = document.createElement('div');
            ov.id = 'bulk-json-modal';
            ov.innerHTML = '<div class="bulk-json-card" role="dialog" aria-modal="true" aria-label="Edit JSON value">'
                + '<div class="bulk-json-head"><span class="bulk-json-path" title="' + _esc(path) + '">' + _esc(path) + '</span>'
                + '<span class="muted bulk-json-keys">Ctrl+Enter save · Esc cancel</span></div>'
                + '<textarea class="bulk-json-ta" spellcheck="false" aria-label="JSON value"></textarea>'
                + '<div class="bulk-json-err" hidden></div>'
                + '<div class="bulk-json-actions">'
                + '<button type="button" class="btn-sm" data-bulk-json-save>Save</button>'
                + '<button type="button" class="btn-sm outline" data-bulk-json-cancel>Cancel</button>'
                + '</div></div>';
            document.body.appendChild(ov);
            var ta = ov.querySelector('.bulk-json-ta');
            function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); }
            function showErr(msg) {
                var el = ov.querySelector('.bulk-json-err');
                el.textContent = msg; el.hidden = false;
            }
            // Prefill from the RAW stored value (peek `values`) — the rendered
            // preview/badge is a summary, not the data. A port-alias path
            // (qubits.*.z.opx_output.exponential_filter) is NOT raw-navigable
            // (the io key is a pointer string), so fall back to peeking the
            // RESOLVED path; the save still posts the alias (edit-batch
            // re-resolves it server-side).
            function prefill(v) {
                ta.value = JSON.stringify(v === undefined ? null : v, null, 2);
                ta.focus();
            }
            fetch('/field/peek?dot_path=' + encodeURIComponent(path))
                .then(function (r) { return r.json(); })
                .then(function (jb) {
                    var v = jb && jb.values ? jb.values[path] : undefined;
                    var ft = jb && jb.resolved ? jb.resolved[path] : null;
                    if ((v === undefined || v === null) && ft && ft.resolved_path
                            && ft.resolved_path !== path) {
                        return fetch('/field/peek?dot_path=' + encodeURIComponent(ft.resolved_path))
                            .then(function (r2) { return r2.json(); })
                            .then(function (jb2) {
                                prefill(jb2 && jb2.values ? jb2.values[ft.resolved_path] : undefined);
                            });
                    }
                    prefill(v);
                })
                .catch(function (err) { showErr('Could not load current value: ' + err); ta.focus(); });
            function save() {
                var parsed;
                try { parsed = JSON.parse(ta.value); }
                catch (ex) { showErr('Invalid JSON: ' + ex.message); return; }
                fetch('/field/edit-batch', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ updates: [{ dot_path: path, value: parsed }],
                                           expect_chip: window.__chipToken || '' })
                }).then(function (r) { return r.json(); }).then(function (jb) {
                    if (!jb || !jb.ok) {
                        showErr((jb && jb.results && jb.results[0] && jb.results[0].error)
                            || (jb && jb.error) || 'Apply failed');
                        return;
                    }
                    // Refresh the cell in place until the next pane render: the
                    // qubit-grid preview span gets new truncated JSON + the red
                    // committed marker; a pair-grid ▦ badge re-derives its dims.
                    var td = btn && btn.closest ? btn.closest('td') : null;
                    var s;
                    try { s = JSON.stringify(parsed); } catch (e2) { s = String(parsed); }
                    var prev = td && td.querySelector('.bulk-cell-list');
                    if (prev) {
                        prev.textContent = s.length > 24 ? s.slice(0, 24) + '…' : s;
                        prev.classList.add('bulk-cell-modified');
                    } else {
                        var inp = td && td.querySelector('input.bulk-cell');
                        if (inp) {
                            var badge = '';
                            if (Array.isArray(parsed)) {
                                var mat = parsed.length && parsed.every(function (r2) { return Array.isArray(r2); });
                                badge = mat ? ('▦ ' + parsed.length + '×' + (parsed[0] ? parsed[0].length : 0))
                                    : ('[ ' + parsed.length + ' ]');
                            }
                            inp.value = badge;
                            inp.setAttribute('data-orig', badge);   // committed, not dirty
                            inp.classList.add('bulk-cell-modified');
                            _hayCache = null;   // badge text changed → fresh search haystacks
                        }
                    }
                    if (jb.tray_html && window._swapPendingTray) {
                        window._bulkSelfEdit = true;            // suppress our own refresh
                        try { window._swapPendingTray(jb.tray_html); }
                        finally { window._bulkSelfEdit = false; }
                    }
                    if (window._diagChanged) window._diagChanged();
                    close();
                }).catch(function (ex) { showErr('Apply failed: ' + ex); });
            }
            ov.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
                else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
            });
            ov.addEventListener('mousedown', function (e) { if (e.target === ov) close(); });
            ov.querySelector('[data-bulk-json-save]').addEventListener('click', save);
            ov.querySelector('[data-bulk-json-cancel]').addEventListener('click', close);
        },

        sort: sort,
        setFreqSync: function (on) {
            try { localStorage.setItem(FREQSYNC_KEY, on ? '1' : '0'); } catch (e) {}
            var cb = document.getElementById('bulk-freq-sync'); if (cb) cb.checked = !!on;
        },
        // ⚏ Qubits pill: one click back to every row (clears the per-chip set).
        showAllQubits: function () {
            _saveQHidden(new Set());
            applyQubitVis();
            _buildQubitMenu();
        },
        setFont: function (scale) { try { localStorage.setItem(FONT_KEY, String(scale)); } catch (e) {} _applyFont(); },
        setLetterSpacing: function (ls) { try { localStorage.setItem(LS_KEY, String(ls)); } catch (e) {} _applyFont(); },
        toggleBold: function () {
            var on = false; try { on = localStorage.getItem(BOLD_KEY) === '1'; } catch (e) {}
            try { localStorage.setItem(BOLD_KEY, on ? '0' : '1'); } catch (e) {}
            _applyFont();
        },
        // "Show all" / "Reset" cover BOTH curated (client-only) and dynamic
        // (server-rendered) columns, so they always reload the pane — a dyn
        // change can't take effect any other way.
        showAllColumns: function () {
            _saveHidden(new Set());
            _saveDynHidden([]);
            _reopenColvis = true;
            _reloadPane();
        },
        resetColumns: function () {
            try { localStorage.removeItem(HIDE_KEY); } catch (e) {}
            try { localStorage.removeItem(DYNHIDDEN_KEY); } catch (e) {}
            _reopenColvis = true;
            _reloadPane();
        },

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

    // Tab order: next/prev edit cell in the row (skipping visible tds with no
    // cell), then the adjacent visible row's first/last cell. null past the
    // grid's edge so native Tab can leave the grid.
    function _tabMove(cell, dc) {
        var td = cell.closest('td');
        var tr = cell.closest('tr');
        var sel = '.bulk-td:not(.bulk-col-hidden):not(.bulk-search-hidden)';
        var tds = Array.prototype.slice.call(tr.querySelectorAll(sel));
        for (var i = tds.indexOf(td) + dc; i >= 0 && i < tds.length; i += dc) {
            var c = tds[i].querySelector('.bulk-cell');
            if (c) return c;
        }
        var rows = _rows().filter(function (r) { return !r.classList.contains('bulk-row-hidden'); });
        for (var ri = rows.indexOf(tr) + dc; ri >= 0 && ri < rows.length; ri += dc) {
            var ntds = Array.prototype.slice.call(rows[ri].querySelectorAll(sel));
            for (var j = dc > 0 ? 0 : ntds.length - 1; j >= 0 && j < ntds.length; j += dc) {
                var nc = ntds[j].querySelector('.bulk-cell');
                if (nc) return nc;
            }
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
