/* Dataset table virtual scroller.
 *
 * The full dataset (potentially thousands of runs) is shipped to the browser
 * once as compact JSON in <script id="ds-rows-data">. This module:
 *
 *   1. Parses that JSON into an in-memory array `state.rows`.
 *   2. Renders only the rows that fit in the viewport (~50–100 at a time)
 *      between top/bottom spacer <tr>s sized to the missing rows. The
 *      scrollbar still reflects the full dataset, preserving the YOLO
 *      "all in one page" feel from feedback_pagination_all_option.md.
 *   3. Filters and sorts in-memory (~5 ms over 2000 rows) instead of
 *      walking the DOM (~200 ms before this change).
 *   4. Tracks the compare-bar selection in a Set keyed by run id, so it
 *      survives scroll/filter/sort.
 *   5. Uses event delegation on <tbody> — no per-row inline handlers.
 *
 * Row schema (compact, see core/dataset.py:list_runs_compact):
 *   { id, exp, date, time, q, oc, metric, bm, tags }
 */
(function() {
    'use strict';

    var ROW_HEIGHT = 32;          // Must match CSS in style.css (.datasets-scroll tbody tr)
    var OVERSCAN = 10;            // Buffer rows above and below the viewport.
    var IDLE_DEFER_MS = 5000;     // Defer delta-poll merges if user interacted within this window.
    // Reserved tag backing the ⭐ favorite (shown as the star, not a tag badge).
    // Keep in sync with FAVORITE_TAG in core/dataset.py + app.js.
    var FAVORITE_TAG = 'favorite';

    // Persisted across init() calls so date-tab / Rescan / sidebar-nav-back
    // don't wipe the user's compare checkboxes. The previous behaviour
    // re-created ``state.selected`` on every init (which fires on every
    // HTMX swap of #table-pane) — so a user picking 3 rows then switching
    // date tabs would lose all 3.
    //
    // Folder-aware: if init() detects a different ``data-folder`` than
    // the one previously stamped on the payload, we clear (the rows are
    // a different DatasetStore entirely). Otherwise we prune any selected
    // ids that no longer appear in the new payload — handles deletions /
    // vanished runs cleanly.
    var _persistedSelection = new Set();
    var _persistedFolder = null;
    // Selected-qubit filter, persisted across same-folder HTMX swaps (date-tab /
    // Rescan / nav-back) like the compare selection — cleared on folder change.
    var _persistedQubitFilter = new Set();
    var _persistedPairFilter = new Set();   // selected qubit-pairs → AND filter (like qubits)
    // Multi-folder: selected folder_keys → OR filter (show rows from these folders);
    // empty = all folders. Persisted across same-folder-SET swaps like the qubit
    // filter; reset to "all" when the active-folder set itself changes.
    var _persistedFolderFilter = new Set();
    // Selected param facets (paramKey -> Set of value strings) + numeric ranges
    // (paramKey -> {min,max}), persisted across same-folder swaps like the qubit
    // filter; cleared on folder change.
    var _persistedParamFilter = new Map();
    var _persistedParamRangeFilter = new Map();

    // ── User-configurable columns (mirrors the Bulk Edit Properties pattern) ──
    // The column registry below is the SINGLE source of truth for the colgroup,
    // the header row AND each virtual row — so header and body can never drift
    // (the old bug). Visibility + per-column widths persist per-browser.
    var LS_HIDDEN = 'quam_ds_hidden_cols';   // JSON array of hidden column keys
    var LS_WIDTHS = 'quam_ds_col_widths';    // JSON {colKey: px}
    var colState = { hidden: new Set(), widths: {} };
    var _resize = null;                       // active drag-resize, or null

    // Slack-style scoped search. Short aliases let power users type `q:q0` instead
    // of `qubit:q0`. The placeholder advertises the long form so newcomers learn it.
    var SCOPE_ALIASES = {q: 'qubit', qp: 'pair', e: 'exp', t: 'tag', oc: 'outcome', d: 'date', m: 'metric', n: 'note', p: 'param'};
    var KNOWN_SCOPES = new Set(['qubit', 'pair', 'exp', 'tag', 'outcome', 'date', 'id', 'metric', 'note', 'param', 'is']);

    var state = {
        rows: [],
        pressedRowId: null,       // run uid captured on pointerdown (survives mid-click rebuild)
        rowsById: null,           // Map<uid, row index>
        visible: [],              // Indices into state.rows passing current filters/sort
        selected: _persistedSelection,  // Aliased so mutations write through to the persisted set.
        sortKey: 'id',
        sortDesc: true,
        sortAgg: 'first',         // 'first' | 'max' | 'min' — per-qubit agg for fit-key sorts
        fitKeys: new Set(),       // union of fit-result keys present across rows (built from sm)
        fitCounts: {},            // fit_key -> #runs that have a sortable value
        curatedKeys: [],          // FIT_TARGET_MAP key order (curated-first in the banner)
        knownQubits: new Set(),   // every qubit name across rows (qubit-aware search + the picker)
        qubitFilter: _persistedQubitFilter,  // selected qubits → AND filter (run must contain ALL)
        knownPairs: new Set(),    // every qubit-pair name across rows (pair search + the picker)
        pairFilter: _persistedPairFilter,    // selected pairs → AND filter (run must contain ALL)
        folderFilter: _persistedFolderFilter,  // selected folder_keys → OR filter; empty = all folders
        foldersByKey: {},                    // folder_key -> {key, label, full_path} (from folders_json)
        paramFacets: {},          // {paramKey: {valueStr: count}} built from rows' pm maps
        paramKeyCount: {},        // paramKey -> #runs carrying it (facet coverage)
        paramKeyNumeric: {},      // paramKey -> true if every value is numeric (→ range filter)
        paramKeyMinMax: {},       // paramKey -> [dataMin, dataMax] for numeric keys
        paramFilter: _persistedParamFilter,        // Map<paramKey, Set<valueStr>>: OR within key, AND across keys
        paramRangeFilter: _persistedParamRangeFilter,  // Map<paramKey, {min,max}>: numeric range, AND across keys
        searchTokens: [],         // Free-text tokens (lowercased)
        scopedFilters: [],        // [{key, value, negate}, ...] parsed scoped filters
        unknownScopes: [],        // Surfaced in the filter-count strip as "unknown scope: foo:"
        selectedExps: null,       // Set of experiment names; null = no exp filter
        scrollEl: null,
        tbody: null,
        emptyEl: null,
        rafScheduled: false,
        lastFirst: -1,
        lastLast: -1,
        // Delta polling
        pollTs: 0,
        pollTimer: null,
        pollInFlight: false,
        pollIntervalMs: 60000,
        lastInteractionTs: 0,
        pendingDelta: null,        // Buffered server response held while user is busy
    };

    function tokenize(raw) {
        // Split on whitespace AND commas (so `q2, q5, time` → three AND keywords),
        // keeping "double-quoted" runs as one token (so `tag:"in progress"` survives).
        var out = [];
        var cur = '';
        var inQ = false;
        for (var i = 0; i < raw.length; i++) {
            var ch = raw[i];
            if (ch === '"') { inQ = !inQ; continue; }
            if (!inQ && (/\s/.test(ch) || ch === ',')) {
                if (cur) { out.push(cur); cur = ''; }
                continue;
            }
            cur += ch;
        }
        if (cur) out.push(cur);
        return out;
    }

    function parseQuery(raw) {
        // Splits a search-box value into free-text tokens + scoped filters.
        // Unknown scopes (e.g. typos like `foo:bar`) fall through to free-text
        // matching AND get surfaced in the filter-count strip.
        var freeText = [];
        var scoped = [];
        var unknown = [];
        var tokens = tokenize(raw);
        for (var i = 0; i < tokens.length; i++) {
            var tok = tokens[i];
            var negate = false;
            var body = tok;
            if (body.length > 1 && body.charAt(0) === '-' && (body.indexOf(':') > 0 || body.indexOf('=') > 0)) {
                negate = true;
                body = body.slice(1);
            }
            // Bare key=value → a param facet filter (e.g. reset=active). Handled
            // before the key:value scope match since `=` is not a scope separator.
            var eqm = body.match(/^([A-Za-z][\w.\-]*)=(.+)$/);
            if (eqm) {
                scoped.push({key: 'param', value: body.toLowerCase(), negate: negate});
                continue;
            }
            var m = body.match(/^([a-zA-Z]+):(.*)$/);
            if (!m) {
                freeText.push(tok.toLowerCase());
                continue;
            }
            var key = m[1].toLowerCase();
            var value = m[2].toLowerCase();
            key = SCOPE_ALIASES[key] || key;
            if (!KNOWN_SCOPES.has(key)) {
                freeText.push(tok.toLowerCase());
                unknown.push(m[1]);
                continue;
            }
            if (key === 'is') {
                // Predicate scope — value is a literal token, never blank.
                if (!value) continue;
                scoped.push({key: key, value: value, negate: negate});
            } else {
                // Substring scope — empty value would match every row; drop it.
                if (!value) continue;
                scoped.push({key: key, value: value, negate: negate});
            }
        }
        return {freeText: freeText, scoped: scoped, unknown: unknown};
    }

    function matchScope(row, key, value) {
        // Returns true iff the row matches a single scoped filter. Caller XORs
        // with the negate flag.
        switch (key) {
            case 'qubit':
                if (!row.q) return false;
                for (var i = 0; i < row.q.length; i++) {
                    if (String(row.q[i]).toLowerCase().indexOf(value) !== -1) return true;
                }
                return false;
            case 'pair':
                if (!row.p) return false;
                for (var pi = 0; pi < row.p.length; pi++) {
                    if (String(row.p[pi]).toLowerCase().indexOf(value) !== -1) return true;
                }
                return false;
            case 'exp':
                return (row.exp || '').toLowerCase().indexOf(value) !== -1;
            case 'tag':
                if (!row.tags) return false;
                for (var j = 0; j < row.tags.length; j++) {
                    if (String(row.tags[j]).toLowerCase().indexOf(value) !== -1) return true;
                }
                return false;
            case 'outcome':
                if (!row.oc) return false;
                for (var k in row.oc) {
                    if (String(row.oc[k]).toLowerCase().indexOf(value) !== -1) return true;
                }
                return false;
            case 'date':
                return (row.date || '').toLowerCase().indexOf(value) !== -1;
            case 'id':
                return String(row.id).indexOf(value) !== -1;
            case 'metric':
                if (row.metric == null) return false;
                return String(row.metric).toLowerCase().indexOf(value) !== -1;
            case 'note':
                return (row.note || '').toLowerCase().indexOf(value) !== -1;
            case 'param': {
                // value is `key=val` (bare reset=active / param:reset=active) or a
                // bare key/value substring. Key matches by substring (reset →
                // reset_type); for key=val the value is matched EXACTLY (so
                // `active` ≠ `active_gef`); without `=`, substring over keys+values.
                if (!row.pm) return false;
                var eq = value.indexOf('=');
                var _norm = function (v) { return (v === true ? 'true' : v === false ? 'false' : String(v)).toLowerCase(); };
                if (eq >= 0) {
                    var pk = value.slice(0, eq), pv = value.slice(eq + 1);
                    for (var pkey in row.pm) {
                        if (pkey.toLowerCase().indexOf(pk) !== -1 && _norm(row.pm[pkey]) === pv) return true;
                    }
                    return false;
                }
                for (var pk2 in row.pm) {
                    if (pk2.toLowerCase().indexOf(value) !== -1 || _norm(row.pm[pk2]).indexOf(value) !== -1) return true;
                }
                return false;
            }
            case 'is':
                if (value === 'bookmarked' || value === 'starred') return !!row.bm;
                if (value === 'failed' || value === 'error')
                    return /(error|fail|abort|crash)/.test(String(row.status || '').toLowerCase());
                return false;
        }
        return false;
    }

    function getSelectedExps() {
        // Reuse the global Set maintained by app.js's exp filter chips.
        if (window._selectedExps instanceof Set) return window._selectedExps;
        return new Set();
    }

    function getSelectedTags() {
        // Reuse the global Set maintained by app.js's tag filter chips
        // (Collections page only; empty elsewhere).
        if (window._selectedTags instanceof Set) return window._selectedTags;
        return new Set();
    }

    // How many of the currently-selected tags this row carries (for ranking).
    function tagMatchCount(row) {
        var sel = state.selectedTags;
        if (!sel || sel.size === 0 || !row.tags) return 0;
        var n = 0;
        for (var i = 0; i < row.tags.length; i++) {
            if (sel.has(row.tags[i])) n++;
        }
        return n;
    }

    function _rowHasQubit(row, q) {
        if (!row.q) return false;
        for (var i = 0; i < row.q.length; i++) if (String(row.q[i]).toLowerCase() === q) return true;
        return false;
    }
    function _rowHasAllQubits(row, set) {
        var qs = Array.from(set);
        for (var i = 0; i < qs.length; i++) if (!_rowHasQubit(row, qs[i])) return false;
        return true;
    }
    function _rowHasPair(row, p) {
        if (!row.p) return false;
        for (var i = 0; i < row.p.length; i++) if (String(row.p[i]).toLowerCase() === p) return true;
        return false;
    }
    function _rowHasAllPairs(row, set) {
        var ps = Array.from(set);
        for (var i = 0; i < ps.length; i++) if (!_rowHasPair(row, ps[i])) return false;
        return true;
    }

    function buildSearchText(row) {
        // Pre-built lowercase string used by token search. Cached on the row.
        if (row._s) return row._s;
        var parts = [
            String(row.id),
            '#' + row.id,
            row.exp || '',
            (row.q || []).join(' '),
            (row.p || []).join(' '),
            (row.tags || []).join(' '),
            row.date || '',
            row.time || '',
        ];
        if (row.oc) {
            for (var k in row.oc) {
                parts.push(k);
                parts.push(row.oc[k]);
            }
        }
        if (row.metric != null) parts.push(String(row.metric));
        if (row.note) parts.push(row.note);   // free-text search also matches notes
        if (row.pm) {                         // …and experiment parameter keys/values
            for (var pk in row.pm) { parts.push(pk); parts.push(String(row.pm[pk])); }
        }
        row._s = parts.join(' ').toLowerCase();
        return row._s;
    }

    function escapeHtml(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ── value formatters ────────────────────────────────────────────────────
    function _runDate(row) {
        // Build a Date from "2026-03-03" + "02:10:48" (local). null if unparseable.
        if (!row.date) return null;
        var d = new Date(row.date + 'T' + (row.time || '00:00:00'));
        return isNaN(d.getTime()) ? null : d;
    }
    function fmtRelative(row) {
        var d = _runDate(row);
        if (!d) return row.date ? row.date.substring(5) : '-';
        var sec = Math.floor((Date.now() - d.getTime()) / 1000);
        if (sec < 45) return 'just now';
        if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + 'm ago';
        if (sec < 86400) return Math.round(sec / 3600) + 'h ago';
        if (sec < 7 * 86400) return Math.round(sec / 86400) + 'd ago';
        if (sec < 60 * 86400) return Math.round(sec / (7 * 86400)) + 'w ago';
        return (d.getMonth() + 1) + '/' + d.getDate();
    }
    function fmtDuration(s) {
        if (s == null || s === '' || isNaN(s)) return '-';
        s = Math.round(Number(s));
        if (s < 60) return s + 's';
        if (s < 3600) return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
        return Math.floor(s / 3600) + 'h' + String(Math.floor((s % 3600) / 60)).padStart(2, '0') + 'm';
    }
    function statusChip(status) {
        if (!status) return '<span class="ds-status ds-status-unknown" title="no status">–</span>';
        var s = String(status).toLowerCase(), cls = 'ds-status-unknown';
        if (/(finish|success|done|complet|pass)/.test(s)) cls = 'ds-status-ok';
        else if (/(run|progress|pending|queue)/.test(s)) cls = 'ds-status-run';
        else if (/(error|fail|abort|crash)/.test(s)) cls = 'ds-status-err';
        return '<span class="ds-status ' + cls + '" title="' + escapeHtml(status) + '">' + escapeHtml(status) + '</span>';
    }
    function tagsCell(row) {
        var h = '';
        if (row.tags && row.tags.length) {
            for (var i = 0; i < row.tags.length; i++) {
                if (row.tags[i] === FAVORITE_TAG) continue;  // shown as the ⭐ star, not a badge
                h += '<span class="tag-badge" data-tag="' + escapeHtml(row.tags[i]) +
                     '" title="Click to remove">' + escapeHtml(row.tags[i]) + '</span>';
            }
        }
        return h + '<button class="tag-add-btn" title="Add tag">+</button>';
    }
    function outcomeCell(row) {
        if (!row.oc || Object.keys(row.oc).length === 0) return '-';
        var h = '';
        for (var q in row.oc) {
            var ok = row.oc[q] === 'successful';
            h += '<span class="outcome-badge ' + (ok ? 'outcome-ok' : 'outcome-fail') +
                 '" title="' + escapeHtml(q) + ': ' + escapeHtml(row.oc[q]) + '">' + (ok ? '✓' : '✗') + '</span>';
        }
        return h;
    }

    // ── column registry (single source of truth for colgroup + header + rows) ──
    // structural: always shown, not in the Properties menu. on: default-visible.
    // w: default px width (user-resizable). sortKey/type: enables header sort.
    var COLUMNS = [
        {key: 'select', structural: true, w: 34, cls: 'col-select',
         header: '<input type="checkbox" id="ds-select-all" aria-label="Select every visible row for compare">',
         render: function (r) { return '<input type="checkbox" class="ds-check" value="' + r.uid + '" aria-label="Select run #' + r.id + ' for compare"' + (state.selected.has(r.uid) ? ' checked' : '') + '>'; }},
        {key: 'bookmark', structural: true, w: 30, cls: 'col-bookmark',
         render: function (r) { return '<span class="bookmark-star' + (r.bm ? ' bookmarked' : '') + '" title="' + (r.bm ? 'Bookmarked' : 'Click to bookmark') + '">' + (r.bm ? '★' : '☆') + '</span>'; }},
        {key: 'id', label: 'ID', on: true, w: 58, sortKey: 'id', type: 'num',
         render: function (r) { return '<strong>#' + r.id + '</strong>'; }},
        // Multi-folder only: which data folder a run belongs to. Auto-hidden by
        // visibleColumns() when a single folder is active (no disambiguation needed).
        {key: 'folder', label: 'Folder', on: true, w: 110, cls: 'col-folder', sortKey: 'f', type: 'str',
         render: function (r) { var f = state.foldersByKey[r.f]; if (!f) return ''; return '<span class="folder-chip" title="' + escapeHtml(f.full_path || '') + '">' + escapeHtml(f.label || '') + '</span>'; }},
        {key: 'exp', label: 'Experiment', on: true, w: 230, sortKey: 'exp', type: 'str',
         render: function (r) { return '<code>' + escapeHtml(r.exp || '') + '</code>'; }},
        {key: 'qubits', label: 'Qubits', on: true, w: 96, sortKey: 'qubits', type: 'str',
         // Prefer the intact pair names ("q0-1") for 2Q runs; fall back to single
         // qubits for 1Q runs. (Search still matches the normalized member qubits.)
         render: function (r) { return (r.p && r.p.length) ? escapeHtml(r.p.join(', ')) : ((r.q && r.q.length) ? escapeHtml(r.q.join(', ')) : '-'); }},
        {key: 'status', label: 'Status', on: true, w: 96, sortKey: 'status', type: 'str',
         render: function (r) { return statusChip(r.status); }},
        {key: 'metric', label: 'Key Metric', on: true, w: 96, sortKey: 'metric', type: 'num', cls: 'col-metric',
         render: function (r) { return (r.metric == null || r.metric === '') ? '-' : escapeHtml(r.metric); }},
        {key: 'when', label: 'When', on: true, w: 88, sortKey: 'when', type: 'num',
         render: function (r) { return '<span title="' + escapeHtml((r.date || '') + ' ' + (r.time || '')) + '">' + escapeHtml(fmtRelative(r)) + '</span>'; }},
        {key: 'tags', label: 'Tags', on: true, w: 180, cls: 'col-tags',
         render: function (r) { return tagsCell(r); }},
        {key: 'duration', label: 'Duration', on: false, w: 84, sortKey: 'dur', type: 'num',
         render: function (r) { return escapeHtml(fmtDuration(r.dur)); }},
        {key: 'date', label: 'Date', on: false, w: 66, sortKey: 'date', type: 'str',
         render: function (r) { return r.date ? escapeHtml(r.date.substring(5)) : ''; }},
        {key: 'time', label: 'Time', on: false, w: 74, sortKey: 'time', type: 'str',
         render: function (r) { return escapeHtml(r.time || ''); }},
        {key: 'outcome', label: 'Outcome', on: false, w: 84,
         render: function (r) { return outcomeCell(r); }},
        {key: 'parent', label: 'Parent', on: false, w: 66, sortKey: 'parent', type: 'num',
         render: function (r) { return r.parent != null ? ('#' + escapeHtml(r.parent)) : '-'; }},
        {key: 'note', label: 'Note', on: false, w: 160, cls: 'ds-col-note',
         render: function (r) { return r.note ? ('<span title="' + escapeHtml(r.note) + '">' + escapeHtml(r.note) + '</span>') : '-'; }},
        {key: 'hs', label: 'State', on: false, w: 58,
         render: function (r) { return r.hs ? '<span title="has a saved quam_state snapshot">✓</span>' : '<span class="muted">–</span>'; }},
    ];
    var COL_BY_KEY = {};
    COLUMNS.forEach(function (c) { COL_BY_KEY[c.key] = c; });

    function defaultHidden() {
        var s = new Set();
        COLUMNS.forEach(function (c) { if (!c.structural && !c.on) s.add(c.key); });
        return s;
    }
    function loadColPrefs() {
        try { var h = JSON.parse(localStorage.getItem(LS_HIDDEN) || 'null'); colState.hidden = h ? new Set(h) : defaultHidden(); }
        catch (e) { colState.hidden = defaultHidden(); }
        try { colState.widths = JSON.parse(localStorage.getItem(LS_WIDTHS) || '{}') || {}; }
        catch (e2) { colState.widths = {}; }
    }
    function saveHidden() { try { localStorage.setItem(LS_HIDDEN, JSON.stringify(Array.from(colState.hidden))); } catch (e) {} }
    function saveWidths() { try { localStorage.setItem(LS_WIDTHS, JSON.stringify(colState.widths)); } catch (e) {} }
    function visibleColumns() {
        var multiFolder = Object.keys(state.foldersByKey).length > 1;
        return COLUMNS.filter(function (c) {
            if (c.key === 'folder' && !multiFolder) return false;  // single folder → no Folder column
            return c.structural || !colState.hidden.has(c.key);
        });
    }
    function colWidth(c) { return colState.widths[c.key] != null ? colState.widths[c.key] : c.w; }

    function renderRowHtml(row, cols) {
        cols = cols || visibleColumns();
        var html = '<tr class="clickable-row" data-id="' + row.uid + '" data-folder-key="' + (row.f || '') + '" data-exp="' + escapeHtml(row.exp || '') + '">';
        for (var i = 0; i < cols.length; i++) {
            var c = cols[i];
            html += '<td class="' + (c.cls || '') + '" data-col-key="' + c.key + '">' + c.render(row) + '</td>';
        }
        return html + '</tr>';
    }

    // ── colgroup + header builders (rebuilt on any visibility/sort change) ──
    function buildColgroup() {
        var cg = document.getElementById('datasets-colgroup');
        if (!cg) return;
        cg.innerHTML = visibleColumns().map(function (c) {
            return '<col data-col-key="' + c.key + '" style="width:' + colWidth(c) + 'px">';
        }).join('');
    }
    function buildHeader() {
        var thead = document.getElementById('datasets-thead');
        if (!thead) return;
        var html = '<tr>';
        visibleColumns().forEach(function (c) {
            var sortable = !!c.sortKey;
            var active = sortable && state.sortKey === c.sortKey;
            var cls = (c.cls || '') + (sortable ? ' sortable' : '') + (active ? (state.sortDesc ? ' sort-desc' : ' sort-asc') : '');
            html += '<th class="' + cls.trim() + '" data-col-key="' + c.key + '"' +
                    (sortable ? (' data-sort="' + c.sortKey + '" data-type="' + (c.type || 'str') + '"') : '') +
                    (c.label ? (' title="' + escapeHtml(c.label) + (sortable ? ' · click to sort' : '') + '"') : '') + '>' +
                    (c.header || escapeHtml(c.label || '')) +
                    '<span class="ds-resize-handle" data-col-key="' + c.key + '" title="Drag to resize · double-click to fit" aria-hidden="true"></span>' +
                    '</th>';
        });
        thead.innerHTML = html + '</tr>';
        var master = thead.querySelector('#ds-select-all');
        if (master) master.addEventListener('change', onSelectAll);
        thead.querySelectorAll('th.sortable').forEach(function (th) {
            th.addEventListener('click', function (e) {
                if (e.target.classList && e.target.classList.contains('ds-resize-handle')) return;
                e.stopPropagation();   // we do the virtual sort here; don't let the
                                       // generic document-level th.sortable handler (app.js)
                                       // also run a DOM sort on the virtual window.
                var key = th.getAttribute('data-sort');
                if (state.sortKey === key) state.sortDesc = !state.sortDesc;
                else { state.sortKey = key; state.sortDesc = (th.getAttribute('data-type') === 'num'); state.sortAgg = 'first'; }
                buildHeader();
                applySort();
                state.lastFirst = -1;
                scheduleRender();
                _saveSortPrefs();
                _buildSortBanner();   // keep the Sort banner badges in sync with header clicks
            });
        });
        thead.querySelectorAll('.ds-resize-handle').forEach(function (h) {
            var key = h.getAttribute('data-col-key');
            h.addEventListener('mousedown', function (e) { startResize(e, key); });
            h.addEventListener('dblclick', function (e) { e.preventDefault(); e.stopPropagation(); autoFitCol(key); });
            h.addEventListener('click', function (e) { e.stopPropagation(); });
        });
    }
    function applyColLayout() {
        buildColgroup();
        buildHeader();
        buildColMenu();
        state.lastFirst = -1;
        scheduleRender();
    }

    // ── drag-resize + double-click auto-fit ──
    function startResize(e, key) {
        e.preventDefault();
        e.stopPropagation();
        var col = document.querySelector('#datasets-colgroup col[data-col-key="' + key + '"]');
        var th = document.querySelector('#datasets-thead th[data-col-key="' + key + '"]');
        if (!col) return;
        _resize = { key: key, col: col, startX: e.clientX, startW: th ? th.offsetWidth : colWidth(COL_BY_KEY[key]) };
        document.body.style.cursor = 'col-resize';
        document.addEventListener('mousemove', onResizeMove);
        document.addEventListener('mouseup', onResizeUp);
    }
    function onResizeMove(e) {
        if (!_resize) return;
        var w = Math.max(28, _resize.startW + (e.clientX - _resize.startX));   // min, NO max
        colState.widths[_resize.key] = w;
        // Coalesce the style write to one per frame — a mousemove storm would
        // otherwise force a layout reflow on every pixel of drag.
        _resize.pendW = w;
        if (!_resize.raf) {
            _resize.raf = requestAnimationFrame(function() {
                if (_resize) { _resize.col.style.width = _resize.pendW + 'px'; _resize.raf = 0; }
            });
        }
    }
    function onResizeUp() {
        if (!_resize) return;
        saveWidths();
        _resize = null;
        document.body.style.cursor = '';
        document.removeEventListener('mousemove', onResizeMove);
        document.removeEventListener('mouseup', onResizeUp);
    }
    function autoFitCol(key) {
        // Fit to the widest content currently rendered in this column. Cells are
        // overflow:hidden, so scrollWidth gives the full (clipped) content width.
        if (!state.tbody) return;
        var max = 24;
        state.tbody.querySelectorAll('td[data-col-key="' + key + '"]').forEach(function (td) {
            if (td.scrollWidth > max) max = td.scrollWidth;
        });
        var th = document.querySelector('#datasets-thead th[data-col-key="' + key + '"]');
        if (th && th.scrollWidth > max) max = th.scrollWidth;
        var w = Math.max(max + 14, 30);
        colState.widths[key] = w;
        saveWidths();
        var col = document.querySelector('#datasets-colgroup col[data-col-key="' + key + '"]');
        if (col) col.style.width = w + 'px';
    }

    // ── Properties column-picker menu (ports the Bulk Edit pattern) ──
    function buildColMenu() {
        var menu = document.getElementById('ds-colvis-menu');
        if (!menu) return;
        var html = '<div class="bulk-colvis-actions">' +
            '<button type="button" class="btn-xs" data-ds-colaction="showall">Show all</button>' +
            '<button type="button" class="btn-xs outline" data-ds-colaction="reset">Reset</button></div>';
        COLUMNS.forEach(function (c) {
            if (c.structural) return;
            var on = !colState.hidden.has(c.key);
            html += '<label class="bulk-colvis-item"><input type="checkbox" data-ds-coltoggle="' +
                    escapeHtml(c.key) + '"' + (on ? ' checked' : '') + '> ' + escapeHtml(c.label) + '</label>';
        });
        menu.innerHTML = html;
    }
    function bindColMenu() {
        var menu = document.getElementById('ds-colvis-menu');
        if (!menu || menu._bound) return;
        menu._bound = true;
        menu.addEventListener('change', function (e) {
            var cb = e.target.closest('[data-ds-coltoggle]');
            if (!cb) return;
            var key = cb.getAttribute('data-ds-coltoggle');
            if (cb.checked) colState.hidden.delete(key); else colState.hidden.add(key);
            saveHidden();
            applyColLayout();
        });
        menu.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ds-colaction]');
            if (!b) return;
            if (b.getAttribute('data-ds-colaction') === 'showall') { colState.hidden = new Set(); saveHidden(); }
            else { try { localStorage.removeItem(LS_HIDDEN); localStorage.removeItem(LS_WIDTHS); } catch (e2) {} colState.hidden = defaultHidden(); colState.widths = {}; }
            applyColLayout();
        });
    }

    function applyFilters() {
        // Read live from the global exp + tag filter sets (managed by app.js chips).
        state.selectedExps = getSelectedExps();
        state.selectedTags = getSelectedTags();
        var tokens = state.searchTokens;
        var scoped = state.scopedFilters;
        var visible = [];
        for (var i = 0; i < state.rows.length; i++) {
            var row = state.rows[i];
            // Folder filter (multi-folder) — OR: empty set = all folders shown.
            if (state.folderFilter.size > 0 && !state.folderFilter.has(row.f)) continue;
            if (state.selectedExps.size > 0 && !state.selectedExps.has(row.exp)) continue;
            // Tag filter is OR: keep a row that has ANY selected tag (ranking
            // below floats rows matching the MOST selected tags to the top).
            if (state.selectedTags.size > 0 && tagMatchCount(row) === 0) continue;
            // Qubit picker — AND: the run must contain EVERY selected qubit.
            if (state.qubitFilter.size > 0 && !_rowHasAllQubits(row, state.qubitFilter)) continue;
            // Pair picker — AND: the run must contain EVERY selected qubit-pair.
            if (state.pairFilter.size > 0 && !_rowHasAllPairs(row, state.pairFilter)) continue;
            // Param facets — AND across keys, OR within a key (see _rowMatchesParams).
            if (state.paramFilter.size > 0 && !_rowMatchesParams(row)) continue;
            // Numeric param ranges — AND across keys.
            if (state.paramRangeFilter.size > 0 && !_rowMatchesParamRanges(row)) continue;
            if (tokens.length > 0) {
                // Each comma/space keyword is ANDed. A keyword that exactly names a
                // known qubit means "run contains that qubit" (so `q8` → every run on
                // qubit q8); anything else is a substring match over the row haystack
                // (so `time` → time_of_flight, etc.).
                var text = null;
                var ok = true;
                for (var t = 0; t < tokens.length; t++) {
                    var tok = tokens[t];
                    if (state.knownQubits.has(tok)) {
                        if (!_rowHasQubit(row, tok)) { ok = false; break; }
                    } else if (state.knownPairs.has(tok)) {
                        if (!_rowHasPair(row, tok)) { ok = false; break; }
                    } else {
                        if (text === null) text = buildSearchText(row);
                        if (text.indexOf(tok) === -1) { ok = false; break; }
                    }
                }
                if (!ok) continue;
            }
            if (scoped.length > 0) {
                var okS = true;
                for (var s = 0; s < scoped.length; s++) {
                    var f = scoped[s];
                    var hit = matchScope(row, f.key, f.value);
                    if (hit === f.negate) { okS = false; break; }
                }
                if (!okS) continue;
            }
            visible.push(i);
        }
        state.visible = visible;
        applySort();
        scheduleRender();
        updateFilterCount();
    }

    function applySort() {
        var key = state.sortKey;
        var desc = state.sortDesc;
        var rows = state.rows;
        function val(idx, k) {
            var r = rows[idx];
            // Fit-result key sort: read the sparse per-run `sm` map. Scalar when the
            // qubits agreed; [first,max,min] otherwise — pick per the agg toggle.
            // Missing → null (sorted LAST in both directions, not -Infinity).
            if (state.fitKeys.has(k)) {
                var sv = r.sm && r.sm[k];
                if (sv == null) return null;
                if (typeof sv === 'number') return sv;
                var ai = state.sortAgg === 'max' ? 1 : (state.sortAgg === 'min' ? 2 : 0);
                return sv[ai];
            }
            if (k === 'qubits') return (r.q && r.q[0]) || '';
            if (k === 'metric') { var m = parseFloat(r.metric); return isNaN(m) ? null : m; }
            if (k === 'when') { var d = _runDate(r); return d ? d.getTime() : null; }
            if (k === 'dur') { var s = parseFloat(r.dur); return isNaN(s) ? null : s; }
            if (k === 'parent') { var p = parseFloat(r.parent); return isNaN(p) ? null : p; }
            if (k === 'status') return (r.status || '').toLowerCase();
            return r[k];
        }
        // When tags are selected (Collections), rank rows matching the MOST
        // selected tags to the top; the column sort is the tiebreaker. (No tags
        // selected → pure column sort, so the plain Datasets page is unchanged.)
        var rankByTags = state.selectedTags && state.selectedTags.size > 0;
        // Decorate-sort-undecorate: compute each row's sort value (and tag-match
        // count) ONCE up front so the O(n log n) comparator doesn't re-run
        // val()/_runDate()/new Date() per comparison (was ~2·n·log n Date
        // allocations on a 10k `when` sort).
        var sortVal = new Map();
        var tagCnt = rankByTags ? new Map() : null;
        for (var vi = 0; vi < state.visible.length; vi++) {
            var vidx = state.visible[vi];
            sortVal.set(vidx, val(vidx, key));
            if (rankByTags) tagCnt.set(vidx, tagMatchCount(rows[vidx]));
        }
        state.visible.sort(function(a, b) {
            if (rankByTags) {
                var ca = tagCnt.get(a), cb = tagCnt.get(b);
                if (ca !== cb) return cb - ca;  // more matches first (DESC)
            }
            var va = sortVal.get(a), vb = sortVal.get(b);
            var na = (va === null || va === undefined), nb = (vb === null || vb === undefined);
            if (na || nb) {
                if (na && nb) return rows[a].id - rows[b].id;   // both missing → stable by id
                return na ? 1 : -1;                              // missing sinks LAST in both directions
            }
            if (va === vb) return rows[a].id - rows[b].id;       // value tie → stable by id
            var cmp = (va < vb) ? -1 : 1;
            return desc ? -cmp : cmp;
        });
    }

    function updateFilterCount() {
        var countEl = document.getElementById('dataset-filter-count');
        if (!countEl) return;
        var active = state.searchTokens.length > 0
            || state.scopedFilters.length > 0
            || (state.selectedExps && state.selectedExps.size > 0)
            || (state.qubitFilter && state.qubitFilter.size > 0);
        var msg = active
            ? ('Showing ' + state.visible.length + ' of ' + state.rows.length)
            : '';
        if (state.unknownScopes.length > 0) {
            // Surface typos like `foo:bar` so the user knows the scope was unrecognized
            // and the row count reflects free-text matching of the literal token.
            var uniq = [];
            for (var i = 0; i < state.unknownScopes.length; i++) {
                if (uniq.indexOf(state.unknownScopes[i]) === -1) uniq.push(state.unknownScopes[i]);
            }
            var noun = uniq.length === 1 ? 'scope' : 'scopes';
            var list = uniq.map(function(s) { return '"' + s + ':"'; }).join(', ');
            msg += (msg ? ' · ' : '') + 'unknown ' + noun + ': ' + list;
        }
        countEl.textContent = msg;
        if (state.emptyEl) {
            state.emptyEl.style.display = state.visible.length === 0 ? '' : 'none';
        }
    }

    function scheduleRender(force) {
        // Default force=true (full rebuild). onScroll passes false so a frame whose
        // visible window (first/last) is unchanged skips the innerHTML rebuild — the
        // dedup in renderWindow that was dead while every caller forced. If ANY caller
        // coalesced into this frame forces, the frame forces (a scroll must never skip a
        // pending filter/sort/selection rebuild). While a row PRESS is live, DEFER the
        // render (re-schedule) so tbody is never rebuilt between press→click — that
        // detaches the pressed <tr> and the click misses (intermittent dead click).
        if (force !== false) state.renderForce = true;
        if (state.rafScheduled) return;
        state.rafScheduled = true;
        requestAnimationFrame(function() {
            state.rafScheduled = false;
            if (state.pressActive) { scheduleRender(state.renderForce); return; }
            var f = state.renderForce; state.renderForce = false;
            renderWindow(f);
        });
    }

    function renderWindow(force) {
        if (!state.scrollEl || !state.tbody) return;
        var total = state.visible.length;
        var scrollTop = state.scrollEl.scrollTop;
        var viewport = state.scrollEl.clientHeight;
        var first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
        var last = Math.min(total, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);
        if (!force && first === state.lastFirst && last === state.lastLast) return;
        state.lastFirst = first;
        state.lastLast = last;

        var topPad = first * ROW_HEIGHT;
        var bottomPad = Math.max(0, (total - last)) * ROW_HEIGHT;
        var cols = visibleColumns();
        var nc = cols.length;
        var html =
            '<tr class="ds-spacer" aria-hidden="true" style="height:' + topPad + 'px"><td colspan="' + nc + '"></td></tr>';
        for (var i = first; i < last; i++) {
            html += renderRowHtml(state.rows[state.visible[i]], cols);
        }
        html +=
            '<tr class="ds-spacer" aria-hidden="true" style="height:' + bottomPad + 'px"><td colspan="' + nc + '"></td></tr>';
        state.tbody.innerHTML = html;
    }

    function onScroll() {
        markInteraction();
        // A scroll means this pointer gesture is a SCROLL, not a row press-hold — drop the
        // freeze NOW so renderWindow follows the scroll. Without this a touch finger-scroll
        // (pointerdown, no click) keeps pressActive=true and freezes the virtual window
        // until the 1500ms safety timeout, so the user scrolls into blank spacer rows.
        clearPress();
        scheduleRender(false);   // window-dedup: skip the rebuild if first/last unchanged
    }

    function onTbodyClick(e) {
        // A row click IS an interaction — defer the 60s delta-poll merge so it can't
        // rebuild the rows mid-click (the press→click race). The press has resolved, so
        // renders may resume.
        markInteraction();
        clearPress();
        var t = e.target;

        // Bookmark star
        var book = t.closest && t.closest('.col-bookmark');
        if (book) {
            e.stopPropagation();
            var trB = book.closest('tr');
            if (!trB) return;
            var idB = trB.getAttribute('data-id');   // uid string
            toggleBookmark(idB, book);
            return;
        }

        // Tag remove (badge click) or add (+ button)
        var addBtn = t.closest && t.closest('.tag-add-btn');
        if (addBtn) {
            e.stopPropagation();
            var trA = addBtn.closest('tr');
            if (!trA) return;
            var idA = trA.getAttribute('data-id');   // uid string
            if (typeof window.openTagPicker === 'function') {
                window.openTagPicker(idA, addBtn);
            }
            return;
        }
        var tagBadge = t.closest && t.closest('.tag-badge');
        if (tagBadge) {
            e.stopPropagation();
            var trT = tagBadge.closest('tr');
            if (!trT) return;
            var idT = trT.getAttribute('data-id');   // uid string
            var tag = tagBadge.getAttribute('data-tag');
            if (typeof window.removeDatasetTag === 'function') {
                window.removeDatasetTag(idT, tag, tagBadge);
            }
            return;
        }

        // Checkbox click — let it toggle naturally; capture in 'change' handler instead.
        if (t.closest && t.closest('.col-select')) {
            e.stopPropagation();
            return;
        }

        // Row click → load detail. Use the id captured on pointerdown as a fallback:
        // the virtual scroller can rebuild tbody.innerHTML between press and click,
        // replacing the <tr> with a spacer so t.closest('tr') / its data-id is gone.
        var tr = t.closest && t.closest('tr.clickable-row');
        var id = tr ? tr.getAttribute('data-id') : null;   // uid string
        if (!id) id = state.pressedRowId;   // rebuilt mid-click → captured uid
        state.pressedRowId = null;
        // uid is a non-empty string ("<folder_key>:<run_id>"); a falsy/unset
        // captured id can't yield /dataset/undefined.
        if (!id || !window.htmx) return;
        openDatasetDetail(id);
    }

    // Capture the pressed row id BEFORE any virtual-scroll rebuild can swap the DOM
    // out from under the click. Only for the row-detail path — the star/tag/checkbox
    // affordances handle themselves in onTbodyClick.
    function onTbodyPointerDown(e) {
        // Mark the press as a real interaction so a 60s delta-poll merge (common while a
        // live experiment writes runs) is DEFERRED — without this an idle clicker's poll
        // merge re-sorts + rebuilds tbody.innerHTML between press and click, detaching the
        // pressed row so the click misses (the intermittent dead click). pressActive
        // additionally freezes renderWindow for the brief press window; it's cleared on
        // the click, and on a safety timeout in case the click never lands (drag / text
        // selection / pointercancel).
        markInteraction();
        clearTimeout(state._pressTimer);   // a prior press's timeout must not fire mid-this-press
        state.pressActive = true;
        // Safety net ONLY — the real clears are the click, pointerup/pointercancel, and any
        // scroll (see clearPress). This timeout just guarantees pressActive can't stick if
        // every one of those is somehow missed.
        state._pressTimer = setTimeout(function () { state.pressActive = false; }, 1500);
        var t = e.target;
        if (t.closest && (t.closest('.col-bookmark') || t.closest('.tag-add-btn') ||
                          t.closest('.tag-badge') || t.closest('.col-select'))) {
            state.pressedRowId = null;
            return;
        }
        var tr = t.closest && t.closest('tr.clickable-row');
        state.pressedRowId = tr ? tr.getAttribute('data-id') : null;   // uid string
    }

    // Load a run's detail into #inspector-pane. The `source:'#inspector-pane'` is
    // LOAD-BEARING and was the root cause of the intermittent "datasets frozen / clicking
    // a row does nothing" bug: WITHOUT a source, htmx.ajax uses document.body as the
    // request element, so (a) #inspector-pane's hx-sync="this:replace" is NEVER read
    // (it's a descendant of body, not an ancestor) and (b) every inspector-pane load
    // shares document.body's ONE request queue (config.timeout 0). A slow/stalled prior
    // load then makes this click QUEUE behind it — and a thrown onload wedges that queue
    // for the whole page session (= the dead table) — while a concurrent global-search
    // GET clobbers the detail (last-response-wins). WITH source=the pane, htmx keys the
    // queue on the pane AND reads its hx-sync="this:replace", so a new load ABORTS the
    // in-flight one (true last-click-wins) instead of queuing/racing. The global search
    // input carries hx-sync="#inspector-pane:replace" so it shares the same sync owner.
    // The 300ms re-issue is now only a backstop; it re-fetches once if the pane did NOT
    // end up showing THIS run — empty (aborted to nothing) OR clobbered with something
    // that isn't a dataset detail (#ds-detail-root absent) — never looping on a legit
    // "Run not found" status (that renders no #ds-detail-root but is matched out below
    // via the search-results / status check kept simple: empty OR shows search results).
    function openDatasetDetail(id) {
        if (!window.htmx) return;
        state.lastDetailId = id;
        state._reissuedIds = state._reissuedIds || {};
        window.htmx.ajax('GET', '/dataset/' + id, { source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML' });
        setTimeout(function () {
            var p = document.getElementById('inspector-pane');
            // Per-ID one-shot (not a global flag): a fast second click on a DIFFERENT run
            // keeps its own backstop instead of being suppressed by run A's reissue window.
            if (!p || state.lastDetailId !== id || state._reissuedIds[id]) return;
            // Don't fight a still-in-flight load: htmx marks the source (#inspector-pane)
            // with .htmx-request while its GET is pending. Re-issuing here would ABORT a
            // slow-but-healthy first GET (e.g. dataset_detail's rescan-on-miss during an
            // active experiment) and only add latency — let it finish.
            if (p.classList.contains('htmx-request')) return;
            // Re-issue ONLY if the pane did not end up showing THIS run: empty (aborted to
            // nothing) OR clobbered by a concurrent global-search response. A real dataset
            // detail renders #ds-detail-root; the global search renders .search-panel /
            // .search-table; a "Run not found" toast renders NEITHER, so it is left alone
            // (no loop). NOTE: this previously tested #ds-search-results/.search-results —
            // selectors the app never emits — so the clobber recovery was dead (audit 2026-06-26).
            var empty = p.innerHTML.trim() === '';
            var hasDetail = !!p.querySelector('#ds-detail-root');
            var clobberedBySearch = !hasDetail && !!p.querySelector('.search-panel, .search-table');
            if (empty || clobberedBySearch) {
                state._reissuedIds[id] = true;
                window.htmx.ajax('GET', '/dataset/' + id, { source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML' });
                setTimeout(function () { delete state._reissuedIds[id]; }, 600);
            }
        }, 300);
    }

    function onTbodyChange(e) {
        var t = e.target;
        if (!t.classList || !t.classList.contains('ds-check')) return;
        var id = t.value;   // uid string
        if (t.checked) state.selected.add(id);
        else state.selected.delete(id);
        if (typeof window.updateCompareButton === 'function') {
            window.updateCompareButton();
        }
    }

    function toggleBookmark(runId, bookEl) {
        fetch('/dataset/' + runId + '/bookmark', {method: 'POST'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                // Favoriting is a tag now: patch both the star and the tags so
                // the Collections "favorite" filter/ranking stays live.
                patchRow(runId, {bm: !!data.bookmarked, tags: data.tags || []});
            })
            .catch(function(err) {
                console.warn('Bookmark toggle failed:', err);
                if (typeof window.showToast === 'function') {
                    window.showToast('Could not update bookmark (network error).', 'error');
                }
            });
    }

    function patchRow(runId, fields) {
        if (!state.rowsById) return;
        var idx = state.rowsById.get(runId);
        if (idx == null) return;
        var row = state.rows[idx];
        for (var k in fields) row[k] = fields[k];
        row._s = null;  // Invalidate the cached search text.
        scheduleRender();
    }

    function onSearchInput() {
        var inp = document.getElementById('dataset-search');
        var q = inp ? (inp.value || '').trim() : '';
        if (q) {
            var parsed = parseQuery(q);
            state.searchTokens = parsed.freeText;
            state.scopedFilters = parsed.scoped;
            state.unknownScopes = parsed.unknown;
        } else {
            state.searchTokens = [];
            state.scopedFilters = [];
            state.unknownScopes = [];
        }
        markInteraction();
        applyFilters();
    }

    function markInteraction() {
        state.lastInteractionTs = Date.now();
    }

    // End the press window: renders may resume and the safety timeout is cancelled so a
    // stale prior-press timeout can't reset pressActive during a later press. Called on
    // click (press resolved), pointerup/pointercancel (press ended / browser took over
    // for scrolling), and on any scroll (the gesture was a scroll, not a row hold).
    function clearPress() {
        state.pressActive = false;
        clearTimeout(state._pressTimer);
    }

    function activeRecently() {
        return (Date.now() - state.lastInteractionTs) < IDLE_DEFER_MS;
    }

    function pollDelta() {
        if (state.pollTs <= 0) return;
        if (state.pollInFlight) return;                 // don't stack on a slow server
        // Table swapped away (navigated to another menu) → stop polling so we
        // don't fetch + server-rescan forever against a detached DOM.
        if (!state.scrollEl || !document.body.contains(state.scrollEl)) {
            stopPolling();
            return;
        }
        var dateInp = document.getElementById('ds-active-date');
        var dateVal = dateInp ? (dateInp.value || '') : '';
        var url = '/datasets/changes-since?ts=' + encodeURIComponent(state.pollTs);
        if (dateVal) url += '&date=' + encodeURIComponent(dateVal);
        state.pollInFlight = true;
        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data || typeof data.now !== 'number') return;
                if (activeRecently()) {
                    // Hold the delta until the user is idle to avoid jumping
                    // their search/scroll out from under them.
                    state.pendingDelta = data;
                    return;
                }
                applyDelta(data);
            })
            .catch(function(err) { console.warn('dataset poll failed:', err); })
            .then(function() { state.pollInFlight = false; });   // finally
    }

    function stopPolling() {
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    }

    function applyDelta(data) {
        var changed = false;
        var updated = data.updated || [];
        for (var i = 0; i < updated.length; i++) {
            var row = updated[i];
            row._s = null;
            row.uid = (row.f || '') + ':' + row.id;   // folder-aware identity
            var idx = state.rowsById.get(row.uid);
            if (idx == null) {
                state.rows.push(row);
                state.rowsById.set(row.uid, state.rows.length - 1);
            } else {
                state.rows[idx] = row;
            }
            changed = true;
        }
        var vanished = data.vanished || [];
        for (var v = 0; v < vanished.length; v++) {
            var vid = vanished[v];
            var vidx = state.rowsById.get(vid);
            if (vidx == null) continue;
            // Tombstone — splice would re-index every entry in rowsById.
            // Cheaper: mark and rebuild index lazily during applyFilters.
            state.rows[vidx] = null;
            state.rowsById.delete(vid);
            state.selected.delete(vid);
            changed = true;
        }
        if (vanished.length > 0) {
            // Compact tombstones and rebuild the id→index map.
            var compact = [];
            state.rowsById = new Map();
            for (var r = 0; r < state.rows.length; r++) {
                if (state.rows[r] != null) {
                    state.rowsById.set(state.rows[r].uid, compact.length);
                    compact.push(state.rows[r]);
                }
            }
            state.rows = compact;
        }
        state.pollTs = Math.max(state.pollTs, data.now);   // monotonic — out-of-order responses can't rewind
        if (changed) {
            _rebuildFitKeys();     // a delta may introduce a brand-new fit key / qubit
            _rebuildParamFacets(); // …or a brand-new param key/value facet
            applyFilters();        // re-sorts (sm is on the merged rows → in-slot placement)
            _buildSortBanner();    // surface any new fit-metric / param badge
            _buildQubitPicker();   // surface any new qubit
            _buildPairPicker();    // …and any new qubit-pair
        }
    }

    function flushPendingIfIdle() {
        if (!state.pendingDelta) return;
        if (activeRecently()) return;
        var d = state.pendingDelta;
        state.pendingDelta = null;
        applyDelta(d);
    }

    function startPolling() {
        if (state.pollTimer) clearInterval(state.pollTimer);
        state.pollTimer = setInterval(function() {
            flushPendingIfIdle();
            pollDelta();
        }, state.pollIntervalMs);
    }

    // Header sort + the resize handles are bound inside buildHeader() (the header
    // is JS-built now). The select-all master checkbox lives in the header too, so
    // its handler is bound there — this is the extracted handler.
    function onSelectAll() {
        var master = document.getElementById('ds-select-all');
        if (!master) return;
        var checked = master.checked;
        for (var i = 0; i < state.visible.length; i++) {   // currently-visible (filtered) set
            var id = state.rows[state.visible[i]].uid;
            if (checked) state.selected.add(id);
            else state.selected.delete(id);
        }
        scheduleRender();
        if (typeof window.updateCompareButton === 'function') {
            window.updateCompareButton();
        }
    }

    // ── Sort banner (collapsible badges: Columns + Fit metrics) ──────────────
    var SORT_KEY_LS = 'quam_ds_sort_key', SORT_DESC_LS = 'quam_ds_sort_desc',
        SORT_AGG_LS = 'quam_ds_sort_agg', SORT_COLLAPSE_LS = 'quam_sort_banner_collapsed';
    // Static "Columns" sort badges — map 1:1 to applySort keys + the column headers.
    var SORT_COLUMNS = [
        {key: 'when', label: 'When', type: 'num'},
        {key: 'id', label: 'ID', type: 'num'},
        {key: 'status', label: 'Status', type: 'str'},
        {key: 'metric', label: 'Key Metric', type: 'num'},
        {key: 'dur', label: 'Duration', type: 'num'},
    ];   // 'qubits' is a FILTER picker now, not a sort badge (see _buildQubitPicker)
    var SORT_FIT_CAP = 40;
    var _sortFitExpanded = false;
    var SORT_PARAM_CAP = 40;          // facet badges shown before the "+N more…" cap
    var PARAM_FACET_CARD_CAP = 25;    // a key with >this many distinct values isn't faceted (still typeable)
    var _sortParamExpanded = false;

    function _isColKey(k) { for (var i = 0; i < SORT_COLUMNS.length; i++) if (SORT_COLUMNS[i].key === k) return SORT_COLUMNS[i]; return null; }
    function _defaultDirNum(isNum) { return !!isNum; }   // num → desc(true), str → asc(false)

    function _loadSortPrefs() {
        try {
            var k = localStorage.getItem(SORT_KEY_LS); if (k) state.sortKey = k;
            var d = localStorage.getItem(SORT_DESC_LS); if (d != null) state.sortDesc = (d === '1');
            var a = localStorage.getItem(SORT_AGG_LS); if (a === 'first' || a === 'max' || a === 'min') state.sortAgg = a;
        } catch (e) {}
    }
    function _saveSortPrefs() {
        try {
            localStorage.setItem(SORT_KEY_LS, state.sortKey);
            localStorage.setItem(SORT_DESC_LS, state.sortDesc ? '1' : '0');
            localStorage.setItem(SORT_AGG_LS, state.sortAgg);
        } catch (e) {}
    }

    // Build the fit-key union + counts from the rows' `sm` maps. Cheap (~1ms at
    // 5000 runs) and delta-safe: we just rebuild whenever the rows change, so a
    // newly-arrived key shows up with no server round-trip.
    function _rebuildFitKeys() {
        var keys = new Set(), counts = {}, qubits = new Set(), pairs = new Set();
        for (var i = 0; i < state.rows.length; i++) {
            var r = state.rows[i];
            if (!r) continue;
            if (r.q) for (var j = 0; j < r.q.length; j++) qubits.add(String(r.q[j]).toLowerCase());
            if (r.p) for (var pj = 0; pj < r.p.length; pj++) pairs.add(String(r.p[pj]).toLowerCase());
            if (!r.sm) continue;
            for (var k in r.sm) { keys.add(k); counts[k] = (counts[k] || 0) + 1; }
        }
        state.fitKeys = keys;
        state.fitCounts = counts;
        state.knownQubits = qubits;
        state.knownPairs = pairs;
        // Persisted sort key was a fit key that has since vanished → fall back.
        if (state.sortKey && !keys.has(state.sortKey) && !_isColKey(state.sortKey)) {
            state.sortKey = 'id'; state.sortDesc = true;
        }
    }
    function _orderedFitKeys() {
        var idx = {};
        state.curatedKeys.forEach(function (k, i) { idx[k] = i; });
        return Array.from(state.fitKeys).sort(function (a, b) {
            var ca = (a in idx), cb = (b in idx);
            if (ca && cb) return idx[a] - idx[b];      // curated: map order
            if (ca !== cb) return ca ? -1 : 1;         // curated before the rest
            var na = state.fitCounts[a] || 0, nb = state.fitCounts[b] || 0;
            if (na !== nb) return nb - na;             // then by coverage count
            return a < b ? -1 : (a > b ? 1 : 0);       // then A–Z
        });
    }
    function _caret() { return state.sortDesc ? ' ▼' : ' ▲'; }
    // ── Parameters filter — grouped by key (collapsed by default). Categorical
    //    keys expand to value facets (OR within key); numeric keys expand to a
    //    min/max RANGE. AND across keys + AND with every other filter. ──────────
    var _paramExpandedKeys = new Set();   // which param-key groups are expanded
    function _rebuildParamFacets() {
        var facets = {}, keyCount = {}, allNum = {}, mins = {}, maxs = {};
        for (var i = 0; i < state.rows.length; i++) {
            var r = state.rows[i];
            if (!r || !r.pm) continue;
            for (var k in r.pm) {
                var v = r.pm[k];
                var isNum = (typeof v === 'number' && isFinite(v));
                if (allNum[k] === undefined) allNum[k] = true;
                if (!isNum) allNum[k] = false;
                else {
                    if (mins[k] === undefined || v < mins[k]) mins[k] = v;
                    if (maxs[k] === undefined || v > maxs[k]) maxs[k] = v;
                }
                var vs = (v === true ? 'true' : v === false ? 'false' : String(v));
                if (!facets[k]) facets[k] = {};
                facets[k][vs] = (facets[k][vs] || 0) + 1;
            }
        }
        var numeric = {}, minmax = {};
        for (var kk in facets) {
            var s = 0; for (var vv in facets[kk]) s += facets[kk][vv];
            keyCount[kk] = s;
            if (allNum[kk]) { numeric[kk] = true; minmax[kk] = [mins[kk], maxs[kk]]; }
        }
        state.paramFacets = facets;
        state.paramKeyCount = keyCount;
        state.paramKeyNumeric = numeric;
        state.paramKeyMinMax = minmax;
        // Drop persisted facet selections whose key/value vanished from the payload.
        if (state.paramFilter.size) {
            state.paramFilter.forEach(function (vals, key) {
                if (!facets[key]) { state.paramFilter.delete(key); return; }
                vals.forEach(function (val) { if (!(val in facets[key])) vals.delete(val); });
                if (!vals.size) state.paramFilter.delete(key);
            });
        }
        // Drop persisted ranges whose key is no longer numeric / present.
        if (state.paramRangeFilter.size) {
            state.paramRangeFilter.forEach(function (rng, key) {
                if (!numeric[key]) state.paramRangeFilter.delete(key);
            });
        }
    }
    // Facetable param keys: ≥2 distinct (a single value can't narrow). Numeric keys
    // qualify regardless of cardinality (they get a range); categorical keys are
    // capped so a freak high-card string key can't flood the picker.
    function _orderedParamKeys(filt) {
        var keys = Object.keys(state.paramFacets).filter(function (k) {
            var n = Object.keys(state.paramFacets[k]).length;
            if (n < 2) return false;
            if (state.paramKeyNumeric[k]) return true;
            return n <= PARAM_FACET_CARD_CAP;
        });
        if (filt) keys = keys.filter(function (k) { return k.toLowerCase().indexOf(filt) !== -1; });
        keys.sort(function (a, b) {
            var na = state.paramKeyCount[a] || 0, nb = state.paramKeyCount[b] || 0;
            if (na !== nb) return nb - na;                // key coverage desc
            return a < b ? -1 : (a > b ? 1 : 0);          // then A–Z
        });
        return keys;
    }
    function _paramActive(key, value) {
        var s = state.paramFilter.get(key);
        return !!(s && s.has(value));
    }
    function _paramKeyHasActiveFilter(key) {
        var f = state.paramFilter.get(key);
        if (f && f.size) return true;
        var r = state.paramRangeFilter.get(key);
        return !!(r && (r.min != null || r.max != null));
    }
    function _fmtNum(n) {
        if (n == null) return '?';
        if (n !== 0 && (Math.abs(n) >= 1e6 || Math.abs(n) < 1e-3)) return n.toExponential(2);
        return String(n);
    }
    // One collapsible group per param key: header (caret + key + ·count + active
    // dot); body (when expanded) is value facets, or a min/max range for numerics.
    function _paramGroupHtml(key) {
        var expanded = _paramExpandedKeys.has(key);
        var active = _paramKeyHasActiveFilter(key);
        var head = '<button type="button" class="param-group-head' + (active ? ' active' : '') +
                   '" data-param-group="' + escapeHtml(key) + '" aria-expanded="' + (expanded ? 'true' : 'false') + '">' +
                   '<span class="pg-caret" aria-hidden="true">' + (expanded ? '▾' : '▸') + '</span>' +
                   escapeHtml(key) + '<span class="sort-badge-count">·' + (state.paramKeyCount[key] || 0) + '</span>' +
                   (active ? '<span class="pg-active-dot" aria-hidden="true"></span>' : '') + '</button>';
        var body = '';
        if (expanded && state.paramKeyNumeric[key]) {
            var mm = state.paramKeyMinMax[key] || [null, null];
            var cur = state.paramRangeFilter.get(key) || {};
            body = '<div class="param-group-body param-range" data-param-range="' + escapeHtml(key) + '">' +
                   '<input type="number" class="param-range-min" placeholder="min" value="' + (cur.min != null ? cur.min : '') + '">' +
                   '<span class="param-range-sep">–</span>' +
                   '<input type="number" class="param-range-max" placeholder="max" value="' + (cur.max != null ? cur.max : '') + '">' +
                   '<span class="muted param-range-hint">data: ' + _fmtNum(mm[0]) + ' … ' + _fmtNum(mm[1]) + '</span></div>';
        } else if (expanded) {
            var vals = state.paramFacets[key];
            var ordered = Object.keys(vals).sort(function (a, b) {
                if (vals[a] !== vals[b]) return vals[b] - vals[a];
                return a < b ? -1 : (a > b ? 1 : 0);
            });
            body = '<div class="param-group-body">' + ordered.map(function (val) {
                var on = _paramActive(key, val);
                return '<button type="button" class="exp-chip sort-badge param-facet' + (on ? ' active' : '') +
                       '" aria-pressed="' + (on ? 'true' : 'false') + '" data-param-key="' + escapeHtml(key) +
                       '" data-param-val="' + escapeHtml(val) + '" title="' + escapeHtml(key) + ' = ' + escapeHtml(val) + '">' +
                       escapeHtml(val) + '<span class="sort-badge-count">·' + vals[val] + '</span></button>';
            }).join('') + '</div>';
        }
        return '<div class="param-group' + (expanded ? ' expanded' : '') + '">' + head + body + '</div>';
    }
    // Categorical facets — AND across keys, OR within a key.
    function _rowMatchesParams(row) {
        var pm = row.pm || {};
        var ok = true;
        state.paramFilter.forEach(function (vals, key) {
            if (!ok) return;
            var rv = pm[key];
            if (rv === undefined) { ok = false; return; }
            var rvs = (rv === true ? 'true' : rv === false ? 'false' : String(rv));
            if (!vals.has(rvs)) ok = false;
        });
        return ok;
    }
    // Numeric ranges — AND across keys; row.pm[key] must be a number in [min,max].
    function _rowMatchesParamRanges(row) {
        var pm = row.pm || {};
        var ok = true;
        state.paramRangeFilter.forEach(function (rng, key) {
            if (!ok) return;
            var v = pm[key];
            if (typeof v !== 'number') { ok = false; return; }
            if (rng.min != null && v < rng.min) ok = false;
            else if (rng.max != null && v > rng.max) ok = false;
        });
        return ok;
    }
    function _badgeHtml(key, label, active, isFit, count) {
        var countHtml = (isFit && count != null) ? '<span class="sort-badge-count">·' + count + '</span>' : '';
        var aggHtml = (active && isFit) ? '<span class="sort-badge-agg" data-sort-agg="1" title="per-qubit: first / max / min">[' + state.sortAgg + ' ▾]</span>' : '';
        return '<button type="button" class="exp-chip sort-badge' + (active ? ' active' : '') + '"' +
               ' aria-pressed="' + (active ? 'true' : 'false') + '" data-sort-key="' + escapeHtml(key) +
               '" data-sort-fit="' + (isFit ? '1' : '0') + '" title="Sort by ' + escapeHtml(label) + '">' +
               escapeHtml(label) + countHtml +
               '<span class="sort-badge-caret" aria-hidden="true">' + (active ? _caret() : '') + '</span>' +
               aggHtml + '</button>';
    }
    function _buildSortBanner() {
        var colWrap = document.getElementById('sort-col-badges');
        if (colWrap) colWrap.innerHTML = SORT_COLUMNS.map(function (c) {
            return _badgeHtml(c.key, c.label, state.sortKey === c.key, false, null);
        }).join('');
        var fitWrap = document.getElementById('sort-fit-badges');
        if (fitWrap) {
            var fInp = document.getElementById('sort-key-filter');
            var filt = (fInp && fInp.value || '').trim().toLowerCase();
            var keys = _orderedFitKeys();
            if (filt) keys = keys.filter(function (k) { return k.toLowerCase().indexOf(filt) !== -1; });
            var capped = (!_sortFitExpanded && !filt && keys.length > SORT_FIT_CAP);
            var shown = capped ? keys.slice(0, SORT_FIT_CAP) : keys;
            // Always keep the ACTIVE fit badge visible (even if beyond the cap or
            // filtered out) so the user never loses the control driving the order.
            if (state.fitKeys.has(state.sortKey) && shown.indexOf(state.sortKey) === -1) {
                shown = [state.sortKey].concat(shown);
            }
            var html = shown.map(function (k) { return _badgeHtml(k, k, state.sortKey === k, true, state.fitCounts[k]); }).join('');
            if (capped) html += '<button type="button" class="exp-chip sort-more" data-sort-more="1">+' + (keys.length - SORT_FIT_CAP) + ' more…</button>';
            if (!keys.length) html = '<span class="muted sort-fit-empty">no fit-result metrics in these runs</span>';
            fitWrap.innerHTML = html;
        }
        var paramWrap = document.getElementById('sort-param-badges');
        if (paramWrap) {
            var pInp = document.getElementById('sort-param-filter');
            var pfilt = (pInp && pInp.value || '').trim().toLowerCase();
            var pkeys = _orderedParamKeys(pfilt);
            // A filter-input match auto-expands its groups so values show immediately.
            var phtml = pkeys.map(function (k) {
                if (pfilt && !_paramExpandedKeys.has(k)) _paramExpandedKeys.add(k);
                return _paramGroupHtml(k);
            }).join('');
            if (!pkeys.length) phtml = '<span class="muted sort-fit-empty">no filterable parameters in these runs</span>';
            paramWrap.innerHTML = phtml;
        }
        _syncSortBadgeUI();
    }
    function _syncSortBadgeUI() {
        var sum = document.getElementById('sort-banner-summary');
        if (sum) {
            var col = _isColKey(state.sortKey);
            var label = col ? col.label : state.sortKey;
            var aggTxt = state.fitKeys.has(state.sortKey) ? (' · ' + state.sortAgg) : '';
            var noVal = (state.fitKeys.has(state.sortKey) && !(state.fitCounts[state.sortKey] > 0)) ? ' — no values' : '';
            sum.textContent = 'Sort: ' + label + aggTxt + (state.sortDesc ? ' ▼' : ' ▲') + noVal;
        }
        var thead = document.getElementById('datasets-thead');
        if (thead) thead.querySelectorAll('th.sortable').forEach(function (th) {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.getAttribute('data-sort') === state.sortKey) th.classList.add(state.sortDesc ? 'sort-desc' : 'sort-asc');
        });
    }
    function _applySortChange() {
        applySort();
        state.lastFirst = -1;
        scheduleRender();
        _saveSortPrefs();
        _buildSortBanner();
    }
    function setSort(key, isFit) {
        if (state.sortKey === key) {
            state.sortDesc = !state.sortDesc;     // same key → flip direction
        } else {
            var col = _isColKey(key);
            state.sortKey = key;
            state.sortDesc = _defaultDirNum(isFit ? true : (col ? col.type === 'num' : false));
            state.sortAgg = 'first';              // reset per-qubit agg on key switch
        }
        _applySortChange();
    }
    function cycleAgg() {
        state.sortAgg = state.sortAgg === 'first' ? 'max' : (state.sortAgg === 'max' ? 'min' : 'first');
        _applySortChange();
    }
    function _bindSortBanner() {
        var grid = document.getElementById('sort-filter-grid');
        if (!grid || grid._bound) return;
        grid._bound = true;
        grid.addEventListener('click', function (e) {
            if (e.target.closest('[data-sort-agg]')) { e.stopPropagation(); cycleAgg(); return; }
            if (e.target.closest('[data-sort-more]')) { _sortFitExpanded = true; _buildSortBanner(); return; }
            // Collapse/expand a whole section (Fit metrics / Parameters).
            var sec = e.target.closest('[data-sort-section]');
            if (sec) { sec.closest('.sort-filter-section').classList.toggle('section-collapsed'); return; }
            // Expand/collapse one param-key group.
            var grpHead = e.target.closest('[data-param-group]');
            if (grpHead) {
                var gk = grpHead.getAttribute('data-param-group');
                if (_paramExpandedKeys.has(gk)) _paramExpandedKeys.delete(gk); else _paramExpandedKeys.add(gk);
                _buildSortBanner();
                return;
            }
            // Toggle a categorical value facet.
            var pBadge = e.target.closest('[data-param-key]');
            if (pBadge) { _toggleParamFacet(pBadge.getAttribute('data-param-key'), pBadge.getAttribute('data-param-val')); return; }
            var badge = e.target.closest('[data-sort-key]');
            if (badge) setSort(badge.getAttribute('data-sort-key'), badge.getAttribute('data-sort-fit') === '1');
        });
        // Numeric range inputs (min/max) → live filter on input.
        grid.addEventListener('input', function (e) {
            var inp = e.target.closest('.param-range-min, .param-range-max');
            if (!inp) return;
            var box = inp.closest('[data-param-range]');
            if (!box) return;
            _setParamRange(box.getAttribute('data-param-range'),
                box.querySelector('.param-range-min').value, box.querySelector('.param-range-max').value);
        });
        var filt = document.getElementById('sort-key-filter');
        if (filt) filt.addEventListener('input', function () { _sortFitExpanded = false; _buildSortBanner(); });
        var pFilt = document.getElementById('sort-param-filter');
        if (pFilt) pFilt.addEventListener('input', function () { _buildSortBanner(); });
    }
    function _setParamRange(key, minStr, maxStr) {
        var min = (minStr === '' || minStr == null) ? null : parseFloat(minStr);
        var max = (maxStr === '' || maxStr == null) ? null : parseFloat(maxStr);
        if (min != null && isNaN(min)) min = null;
        if (max != null && isNaN(max)) max = null;
        if (min == null && max == null) state.paramRangeFilter.delete(key);
        else state.paramRangeFilter.set(key, {min: min, max: max});
        markInteraction();
        applyFilters();
        // NB: don't rebuild the banner here — that would re-render the <input> the
        // user is typing in and drop focus. The head's active-dot updates on the
        // next natural rebuild (toggle/collapse); the filtering applies live.
    }
    function _toggleParamFacet(key, value) {
        var set = state.paramFilter.get(key);
        if (set && set.has(value)) {
            set.delete(value);
            if (!set.size) state.paramFilter.delete(key);
        } else {
            if (!set) { set = new Set(); state.paramFilter.set(key, set); }
            set.add(value);
        }
        markInteraction();
        applyFilters();
        _buildSortBanner();
    }
    // ── Qubit filter picker (checkbox dropdown on the "Qubits" control) ──────
    function _qubitCmp(a, b) {
        var na = a.match(/(\d+)\s*$/), nb = b.match(/(\d+)\s*$/);
        if (na && nb) {
            var pa = a.slice(0, na.index), pb = b.slice(0, nb.index);
            if (pa === pb) return parseInt(na[1], 10) - parseInt(nb[1], 10);   // q2 < q10
        }
        return a < b ? -1 : (a > b ? 1 : 0);
    }
    function _updateQubitSummary() {
        var sum = document.getElementById('sort-qubit-summary');
        if (sum) {
            sum.textContent = 'Qubits' + (state.qubitFilter.size ? ' (' + state.qubitFilter.size + ')' : '') + ' ▾';
            sum.classList.toggle('active', state.qubitFilter.size > 0);   // .exp-chip.active fill
        }
    }
    function _buildQubitPicker() {
        var menu = document.getElementById('sort-qubit-menu');
        if (!menu) return;
        var qubits = Array.from(state.knownQubits).sort(_qubitCmp);
        var html = '<div class="bulk-colvis-actions"><button type="button" class="btn-xs" data-qubit-action="clear">Clear</button>' +
                   '<span class="muted" style="font-size:.72em;align-self:center">AND — runs with all checked</span></div>';
        if (!qubits.length) html += '<div class="muted sort-fit-empty" style="padding:.2rem .3rem">no qubits</div>';
        qubits.forEach(function (q) {
            html += '<label class="bulk-colvis-item"><input type="checkbox" data-qubit="' + escapeHtml(q) + '"' +
                    (state.qubitFilter.has(q) ? ' checked' : '') + '> ' + escapeHtml(q) + '</label>';
        });
        menu.innerHTML = html;
        _updateQubitSummary();
    }
    function _bindQubitPicker() {
        var menu = document.getElementById('sort-qubit-menu');
        if (!menu || menu._bound) return;
        menu._bound = true;
        menu.addEventListener('change', function (e) {
            var cb = e.target.closest('[data-qubit]');
            if (!cb) return;
            var q = cb.getAttribute('data-qubit');
            if (cb.checked) state.qubitFilter.add(q); else state.qubitFilter.delete(q);
            _updateQubitSummary();
            markInteraction();
            applyFilters();
        });
        menu.addEventListener('click', function (e) {
            if (e.target.closest('[data-qubit-action="clear"]')) {
                state.qubitFilter.clear();
                _buildQubitPicker();
                markInteraction();
                applyFilters();
            }
        });
    }
    // ── Qubit-PAIR filter picker — mirror of the qubit picker (pairs sort plainly) ──
    function _updatePairSummary() {
        var sum = document.getElementById('sort-pair-summary');
        if (sum) {
            sum.textContent = 'Pairs' + (state.pairFilter.size ? ' (' + state.pairFilter.size + ')' : '') + ' ▾';
            sum.classList.toggle('active', state.pairFilter.size > 0);
        }
    }
    function _buildPairPicker() {
        var menu = document.getElementById('sort-pair-menu');
        if (!menu) return;
        var pairs = Array.from(state.knownPairs).sort();
        var html = '<div class="bulk-colvis-actions"><button type="button" class="btn-xs" data-pair-action="clear">Clear</button>' +
                   '<span class="muted" style="font-size:.72em;align-self:center">AND — runs with all checked</span></div>';
        if (!pairs.length) html += '<div class="muted sort-fit-empty" style="padding:.2rem .3rem">no qubit pairs</div>';
        pairs.forEach(function (p) {
            html += '<label class="bulk-colvis-item"><input type="checkbox" data-pair="' + escapeHtml(p) + '"' +
                    (state.pairFilter.has(p) ? ' checked' : '') + '> ' + escapeHtml(p) + '</label>';
        });
        menu.innerHTML = html;
        _updatePairSummary();
    }
    function _bindPairPicker() {
        var menu = document.getElementById('sort-pair-menu');
        if (!menu || menu._bound) return;
        menu._bound = true;
        menu.addEventListener('change', function (e) {
            var cb = e.target.closest('[data-pair]');
            if (!cb) return;
            var p = cb.getAttribute('data-pair');
            if (cb.checked) state.pairFilter.add(p); else state.pairFilter.delete(p);
            _updatePairSummary();
            markInteraction();
            applyFilters();
        });
        menu.addEventListener('click', function (e) {
            if (e.target.closest('[data-pair-action="clear"]')) {
                state.pairFilter.clear();
                _buildPairPicker();
                markInteraction();
                applyFilters();
            }
        });
    }

    window.toggleSortBannerCollapsed = function () {
        var collapsed = document.body.classList.toggle('sort-banner-collapsed');
        try { localStorage.setItem(SORT_COLLAPSE_LS, collapsed ? '1' : '0'); } catch (e) {}
        var b = document.getElementById('sort-banner-toggle');
        if (b) b.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    };
    // Recovery path from the empty-state: clear search + exp chips + qubit picker.
    window.clearDatasetFilters = function () {
        var inp = document.getElementById('dataset-search');
        if (inp) inp.value = '';
        state.searchTokens = []; state.scopedFilters = []; state.unknownScopes = [];
        if (state.qubitFilter) state.qubitFilter.clear();
        if (state.pairFilter) state.pairFilter.clear();
        if (state.paramFilter) state.paramFilter.clear();
        if (state.paramRangeFilter) state.paramRangeFilter.clear();
        if (window._selectedExps && window._selectedExps.clear) window._selectedExps.clear();
        // Scope to the experiment-filter grid — the Sort banner's sort/fit/param
        // badges are also .exp-chip, and resetting "All active" must not touch them.
        document.querySelectorAll('#exp-filter-grid .exp-chip').forEach(function (c) {
            c.classList.toggle('active', (c.getAttribute('data-exp') || '') === '');
        });
        _buildQubitPicker();
        _buildPairPicker();
        _buildSortBanner();   // reflect the cleared param facets in the badges
        markInteraction();
        applyFilters();
    };
    function _restoreSortCollapsed() {
        var c = false; try { c = localStorage.getItem(SORT_COLLAPSE_LS) === '1'; } catch (e) {}
        document.body.classList.toggle('sort-banner-collapsed', c);
        var b = document.getElementById('sort-banner-toggle');
        if (b) b.setAttribute('aria-expanded', c ? 'false' : 'true');
    }

    function init() {
        var data = document.getElementById('ds-rows-data');
        var tbody = document.getElementById('datasets-tbody');
        var scroll = document.getElementById('datasets-scroll');
        if (!data || !tbody || !scroll) return;

        var rows = [];
        try {
            rows = JSON.parse(data.textContent || '[]');
        } catch (e) {
            console.error('dataset-virtual: failed to parse rows JSON', e);
            return;
        }
        var nowAttr = data.getAttribute('data-now');
        var initialTs = nowAttr ? parseFloat(nowAttr) : 0;
        if (!isFinite(initialTs)) initialTs = 0;

        // Reset rows for a fresh page or HTMX swap. The SELECTION is
        // preserved across same-folder swaps (date-tab change, Rescan,
        // nav-back to /datasets) — see the _persistedSelection /
        // _persistedFolder declarations at the top. A different data
        // folder means a different DatasetStore: row ids are unrelated,
        // so we drop the selection entirely.
        state.rows = rows;
        // Compose the folder-aware uid ("<folder_key>:<run_id>") for each row — the
        // stable identity used for selection, the detail URL, tags and the tree
        // highlight. row.id (int) stays for display (#NNN) + the id: search scope.
        for (var i = 0; i < rows.length; i++) {
            rows[i].uid = (rows[i].f || '') + ':' + rows[i].id;
        }
        // folder_key -> {key, label, full_path} for the Folder column + filter badges.
        state.foldersByKey = {};
        var foldersEl = document.getElementById('ds-folders-data');
        if (foldersEl) {
            try {
                (JSON.parse(foldersEl.textContent || '[]') || []).forEach(function (f) {
                    state.foldersByKey[f.key] = f;
                });
            } catch (e) { /* leave empty — single/unknown folder */ }
        }
        state.rowsById = new Map();
        for (var j = 0; j < rows.length; j++) {
            state.rowsById.set(rows[j].uid, j);
        }
        // Key the persisted selection on folder + view so the Datasets and
        // Collections pages (same DatasetStore/folder) keep independent compare
        // selections — switching between them shouldn't carry checks over.
        var newFolder = (data.getAttribute('data-folder') || '')
                        + '|' + (data.getAttribute('data-view') || 'datasets');
        if (newFolder !== _persistedFolder) {
            _persistedSelection = new Set();
            _persistedQubitFilter = new Set();   // different chip → qubit names unrelated
            _persistedPairFilter = new Set();    // …and pair names too
            _persistedParamFilter = new Map();   // …and param facets are chip-specific too
            _persistedParamRangeFilter = new Map();
            _persistedFolderFilter = new Set();  // active-folder SET changed → reset to "all"
            _persistedFolder = newFolder;
        } else if (_persistedSelection.size > 0) {
            // Prune uids that no longer appear in the fresh payload (deleted
            // runs, vanished folders). Same-folder-set swaps with the same
            // backing data keep every uid intact.
            var liveIds = state.rowsById;
            var pruned = new Set();
            _persistedSelection.forEach(function(uid) {
                if (liveIds.has(uid)) pruned.add(uid);
            });
            _persistedSelection = pruned;
        }
        state.selected = _persistedSelection;
        state.folderFilter = _persistedFolderFilter;   // re-point after folder check
        // Re-sync folder filter chips (server renders them inactive) from the
        // persisted selection — runs on every swap, so ordering vs app.js is moot.
        var folderGrid = document.getElementById('folder-filter-grid');
        if (folderGrid) {
            folderGrid.querySelectorAll('.folder-chip').forEach(function (c) {
                var v = c.getAttribute('data-folder-key') || '';
                c.classList.toggle('active', v === '' ? state.folderFilter.size === 0
                                                       : state.folderFilter.has(v));
            });
        }
        state.scrollEl = scroll;
        state.tbody = tbody;
        state.emptyEl = document.getElementById('datasets-empty');
        state.lastFirst = -1;
        state.lastLast = -1;
        state.pollTs = initialTs;
        state.pendingDelta = null;
        // Honor the configurable interval used by the rest of the app.
        var cfgSecs = (window.UI_CONFIG && window.UI_CONFIG.autoRefreshInterval) || 60;
        state.pollIntervalMs = Math.max(5, cfgSecs) * 1000;

        // HTMX innerHTML swaps replace the entire tree under #table-pane, so
        // listeners on the old tbody/scroll/search nodes are GC'd along with
        // them. Re-binding on every init is correct (and idempotent on the
        // first page load — init runs exactly once before any swap).
        scroll.addEventListener('scroll', onScroll, {passive: true});
        tbody.addEventListener('pointerdown', onTbodyPointerDown);
        // End the press freeze when the pointer lifts or the browser takes the gesture for
        // scrolling (pointercancel) — so a touch finger-scroll never leaves the table frozen
        // waiting on the 1500ms safety timeout. Bound on DOCUMENT, not tbody: a press that
        // starts on a row but releases OUTSIDE the tbody (drag into another pane, lost pointer
        // capture) would otherwise only clear via the 1.5s timer — a visible table freeze.
        document.addEventListener('pointerup', clearPress);
        document.addEventListener('pointercancel', clearPress);
        tbody.addEventListener('click', onTbodyClick);
        tbody.addEventListener('change', onTbodyChange);
        var search = document.getElementById('dataset-search');
        if (search) search.addEventListener('input', onSearchInput);

        // Build the column layout (colgroup + header + Properties menu) from the
        // persisted prefs BEFORE the first render so header & body share one width
        // source (no drift) and hidden/resized columns apply from the start.
        loadColPrefs();
        bindColMenu();
        applyColLayout();

        // Sort banner: curated key order + persisted sort key/dir/agg; build the
        // fit-key union from the rows' sm maps (so it's correct after delta merges).
        var cur = document.getElementById('ds-curated-keys');
        try { state.curatedKeys = cur ? (JSON.parse(cur.textContent || '[]') || []) : []; } catch (e) { state.curatedKeys = []; }
        state.qubitFilter = _persistedQubitFilter;   // re-point after folder check
        state.pairFilter = _persistedPairFilter;     // re-point after folder check
        state.paramFilter = _persistedParamFilter;   // re-point after folder check
        state.paramRangeFilter = _persistedParamRangeFilter;
        _loadSortPrefs();
        _rebuildFitKeys();
        _rebuildParamFacets();   // (also prunes facet selections whose key/value vanished)
        // Drop any selected qubits / pairs that no longer exist in this payload.
        Array.from(state.qubitFilter).forEach(function (q) { if (!state.knownQubits.has(q)) state.qubitFilter.delete(q); });
        Array.from(state.pairFilter).forEach(function (p) { if (!state.knownPairs.has(p)) state.pairFilter.delete(p); });
        _bindSortBanner();
        _bindQubitPicker();
        _bindPairPicker();
        _restoreSortCollapsed();
        _buildSortBanner();
        _buildQubitPicker();
        _buildPairPicker();

        // Pull initial filter state from the existing search input + exp chips.
        // Route through parseQuery so scoped tokens persist across HTMX swaps.
        var inp = document.getElementById('dataset-search');
        var q = inp ? (inp.value || '').trim() : '';
        if (q) {
            var parsed = parseQuery(q);
            state.searchTokens = parsed.freeText;
            state.scopedFilters = parsed.scoped;
            state.unknownScopes = parsed.unknown;
        } else {
            state.searchTokens = [];
            state.scopedFilters = [];
            state.unknownScopes = [];
        }
        applyFilters();

        startPolling();
    }

    window.DatasetVirtual = {
        init: init,
        applyFilters: applyFilters,
        patchRow: patchRow,
        // Used by tag/note handlers in app.js to keep the in-memory store in sync
        // when the server returns updated lists.
        patchTags: function(runId, tags) { patchRow(runId, {tags: tags || []}); },
        patchNote: function(runId, note) { patchRow(runId, {note: note || ''}); },
        getRow: function(runId) {
            if (!state.rowsById) return null;
            var idx = state.rowsById.get(runId);
            return idx == null ? null : state.rows[idx];
        },
        getSelectedIds: function() {
            return Array.from(state.selected);
        },
        clearSelection: function() {
            state.selected.clear();
            scheduleRender();
        },
        // Folder filter (multi-folder). The set lives here so it resets when the
        // active-folder SET changes; app.js owns only the chip UI.
        toggleFolder: function(key) {
            if (key === '') state.folderFilter.clear();
            else if (state.folderFilter.has(key)) state.folderFilter.delete(key);
            else state.folderFilter.add(key);
            applyFilters();
        },
        folderFilterKeys: function() { return Array.from(state.folderFilter); },
    };
})();
