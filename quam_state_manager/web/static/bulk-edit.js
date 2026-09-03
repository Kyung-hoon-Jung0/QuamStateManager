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

    // r17 one-time reset. The server defaults flipped from "31 curated columns
    // hidden" (T1/T2/χ/f₁₂/ports) to "show everything", but a PERSISTED hidden
    // set outranks the server default (_hiddenSet), so anyone who had ever
    // touched the Properties menu would never have seen the change. A new key
    // makes the flip actually arrive; the legacy value is DROPPED rather than
    // migrated, because it encodes an opt-IN world that no longer exists.
    var HIDE_KEY = 'quam_bulk_hidden_cols_v2';
    try { localStorage.removeItem('quam_bulk_hidden_cols'); } catch (e) {}
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
    // docs/141 4ac: `chip` is the DISPLAY name -- it is also the localStorage
    // key prefix for the Qubits/Pairs pickers, so it must not change. `chipKey`
    // folds the chip's PATH in and is what the /bulk/cells gate compares.
    var _dynHintKeys = [];         // dyn keys matching the current search but hidden
    var _colHintKeys = [];         // RENDERED columns matching the search but checkbox-hidden
    var _pairHintKeys = [];        // the pair grid's hidden matches (via BulkPairEdit)
    var _reopenColvis = false;     // r7: keep the Properties menu open across a dyn-toggle reload
    // ── search-typing performance (audit: "typing in Live Edit is slow") ─────
    // A real chip renders ~150 columns × ~30 rows ≈ 2000 cells; re-scanning all
    // of them AND re-toggling their classes per keystroke froze typing.
    var _searchTimer = null;       // debounce: one applySearch per typing pause
    var _hayCache = null;          // { key, rowMap: WeakMap(row→[hay]), colHay } across keystrokes
    // docs/120 item 8 — a VALUE token restricts both axes, so the surviving
    // grid is the intersection: exactly the cells that ALREADY hold that value.
    // For a filter that is right; for an EDITOR it is a dead end, and it is the
    // customer's own report — searching `amplified` showed only the qubits
    // already set to it, so "이거를 사용자가 SM에서 입력을 실제로 할수가없었음".
    // The search keeps its meaning (silently widening it would break "show me
    // the qubits whose T1 is 12"); the way out is offered instead, in the same
    // shape as the neighbouring hidden-column chip.
    var _valRowsAll = false;       // user asked to keep the rows a value token hid
    var _valRowsQ = null;          // the query that choice belongs to
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
            // docs/141 4n: the viewport the server plans its cold columns for
            // (screen.availWidth — no layout read; the server is conservative
            // with it, never fewer hot columns than _virtInit would keep)
            var vw = (window.screen && window.screen.availWidth) || 0;
            evt.detail.path = _bulkSetQueryParam(evt.detail.path, 'vw', vw > 0 ? String(vw) : '');
            if (evt.detail.parameters) delete evt.detail.parameters['vw'];
        });
    }
    // Re-GET /bulk into the table pane — the same idiom the cross-surface
    // state-changed listener uses; configRequest re-attaches dynhide.
    // pre-customer audit: if the /bulk reload never lands (error, abort,
    // the user navigates away), _consumeEditCarry never runs and the
    // leave-confirm carve-out stays armed — a window in which navigation
    // discards unapplied edits with NO prompt. Disarm on every outcome.
    function _armCarryDisarm() {
        var off = function () {
            document.removeEventListener('htmx:afterRequest', off, true);
            document.removeEventListener('htmx:responseError', off, true);
            document.removeEventListener('htmx:sendError', off, true);
            if (_editCarry) { window._dynReloadAt = 0; }
        };
        document.addEventListener('htmx:afterRequest', off, true);
        document.addEventListener('htmx:responseError', off, true);
        document.addEventListener('htmx:sendError', off, true);
        setTimeout(off, CARRY_TTL_MS);
    }
    function _reloadPane() {
        if (window.htmx) htmx.ajax('GET', '/bulk', { target: '#table-pane', swap: 'innerHTML' });
    }

    /* ── docs/120 item 4: the quick-filter chip bar ────────────────────────
     *
     * The customer's daily loop was "go to the search box and TYPE x180, amp,
     * ro, power ... over and over -- very repetitive, eats time". So the common
     * parameters are chips.
     *
     * The chips are a VIEW OF THE QUERY STRING, never a second filter. Toggling
     * one rewrites #bulk-search and lets the existing search do all the work,
     * which buys three things for free: one chip filters BOTH grids (they read
     * the same input), typing a chip's word by hand lights that chip, and
     * deleting it un-lights it. A parallel filter could disagree with the box;
     * this cannot.
     *
     * The query is rebuilt as `<free text> <chip segment>`, where the segment
     * joins the active terms with ' ' (AND) or ' | ' (OR) -- the docs/96
     * grammar the search already parses, so there is no new matching logic
     * anywhere. Free text the user typed is preserved verbatim.
     */
    var ChipBar = (function () {
        var MODE_KEY = 'quam_bulk_chip_mode';
        var terms = [];          // every term this chip renders, from the server
        var active = [];         // ordered, the ones currently pressed
        var mode = 'and';
        var offerDismissed = false;

        function bar() { return document.getElementById('bulk-chipbar'); }
        function input() { return document.getElementById('bulk-search'); }
        function _readMode() {
            try { return localStorage.getItem(MODE_KEY) === 'or' ? 'or' : 'and'; }
            catch (e) { return 'and'; }
        }
        function _tokens() {
            var el = input();
            return el ? el.value.trim().split(/\s+/).filter(Boolean) : [];
        }
        /* Everything that is NOT one of our chips (and not a bare pipe we
           emitted). Kept verbatim and in order so a user's own query survives
           every chip press. */
        function _freeTokens() {
            var out = [], toks = _tokens();
            for (var i = 0; i < toks.length; i++) {
                var t = toks[i].toLowerCase();
                if (t === '|' || terms.indexOf(t) >= 0) continue;
                out.push(toks[i]);
            }
            return out;
        }
        function _write() {
            var el = input(); if (!el) return;
            var seg = active.join(mode === 'or' ? ' | ' : ' ');
            var free = _freeTokens().join(' ');
            el.value = (free ? free + ' ' : '') + seg;
            try { localStorage.setItem(SEARCH_KEY, el.value); } catch (e) {}
            _paint();
            offerDismissed = false;
            applySearch();
            // The PAIR grid listens on this input's 'input' EVENT (pair-edit.js
            // keeps its own applySearch — deliberately isolated). Assigning
            // .value programmatically fires nothing, so without this dispatch a
            // chip narrowed the qubit grid and left the pair table untouched —
            // while this module's own comment claimed one chip filters both.
            // Review caught it because the selfcheck fixture had no pair table.
            // ...and the flag tells THIS grid's own listener that its scan is
            // already done, so the dispatch costs one pass, not two. Cleared in
            // a finally: leaving it set would mute every later keystroke.
            window._chipDrivenSearch = true;
            try { el.dispatchEvent(new Event('input', { bubbles: true })); }
            catch (e) { /* pre-Event browsers: the qubit grid is already done */ }
            finally { window._chipDrivenSearch = false; }
            _offer();
        }
        function _paint() {
            var b = bar(); if (!b) return;
            Array.prototype.slice.call(b.querySelectorAll('.bulk-chip')).forEach(function (btn) {
                var on = active.indexOf(btn.getAttribute('data-chip-term')) >= 0;
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                btn.classList.toggle('active', on);
            });
            var m = document.getElementById('bulk-chip-mode');
            if (m) {
                m.textContent = mode === 'or' ? 'OR' : 'AND';
                m.setAttribute('data-mode', mode);
                m.setAttribute('aria-pressed', mode === 'or' ? 'true' : 'false');
                m.classList.toggle('chip-mode-or', mode === 'or');
            }
        }
        /* Zero matches with 2+ chips in AND is the one moment the other mode is
           probably what was meant -- so offer it AS a switch. Accepting costs
           the same single click as finding the toggle, which is the whole
           point (the user's own framing). Never shown in OR: if a union
           matches nothing, the other mode cannot help. */
        function _offer() {
            var o = document.getElementById('bulk-chip-offer'); if (!o) return;
            var show = !offerDismissed && mode === 'and' && active.length > 1
                && _visibleColCount() === 0;
            o.hidden = !show;
        }
        /* Both grids, because both are filtered by these chips. Counting only
           the qubit table offered "No matches — try OR?" while the pair table
           below was full of hits, which is a false statement about the screen
           the user is looking at. */
        function _visibleColCount() {
            var n = 0, seen = false;
            ['bulk-table', 'bulk-pair-table'].forEach(function (id) {
                var t = document.getElementById(id);
                if (!t) return;
                seen = true;
                // Count DATA columns only. Subtracting just `.bulk-corner`
                // left the permanent apply-column header in the total, so the
                // minimum was 1 per table and `=== 0` was unreachable on any
                // chip — the "No matches — try OR?" offer could never appear,
                // however many chips you ANDed together. Every real column
                // carries data-col-key; no permanent header does.
                // ...and never the row-name column. `__id__` is the qubit /
                // pair name: always visible, not a value, one per table — so
                // counting it kept the floor at 2 and `=== 0` stayed
                // unreachable even after the apply header was excluded.
                n += t.querySelectorAll(
                    'thead th[data-col-key]:not([data-col-key="__id__"])'
                    + ':not(.bulk-search-hidden):not(.bulk-col-hidden)'
                ).length;
            });
            return seen ? n : 1;   // no grid mounted → never claim "no matches"
        }
        function toggle(term) {
            var i = active.indexOf(term);
            if (i >= 0) active.splice(i, 1); else active.push(term);
            _write();
        }
        /* docs/126 ③ — user-defined patches ("decouple", "joint", …): saved
           per browser, injected beside the server chips, filtered through the
           exact same toggle/_write path. The server chips stay authoritative
           for coverage; these are the lab's own vocabulary on top. */
        var CUSTOM_KEY = 'quam_bulk_custom_chips';
        function _customTerms() {
            try {
                var a = JSON.parse(localStorage.getItem(CUSTOM_KEY) || '[]');
                return Array.isArray(a) ? a.filter(function (t) {
                    return typeof t === 'string' && /^[^\s|]{1,40}$/.test(t);
                }) : [];
            } catch (e) { return []; }
        }
        function _saveCustom(list) {
            try { localStorage.setItem(CUSTOM_KEY, JSON.stringify(list)); } catch (e) {}
        }
        function _injectCustom(b) {
            var scroll = b.querySelector('.bulk-chip-scroll'); if (!scroll) return;
            Array.prototype.slice.call(scroll.querySelectorAll(
                '.bulk-chip-custom, .bulk-chip-add, .bulk-chip-add-input'
            )).forEach(function (n) { n.parentNode.removeChild(n); });
            var server = Array.prototype.slice.call(scroll.querySelectorAll('.bulk-chip'))
                .map(function (x) { return x.getAttribute('data-chip-term'); });
            _customTerms().forEach(function (t) {
                if (server.indexOf(t) >= 0) return;   // the server already offers it
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'bulk-chip bulk-chip-custom';
                btn.setAttribute('data-chip-term', t);
                btn.setAttribute('aria-pressed', 'false');
                btn.title = 'your saved filter patch — × removes it';
                btn.textContent = t;
                var x = document.createElement('span');
                x.className = 'bulk-chip-x';
                x.textContent = '×';
                btn.appendChild(x);
                scroll.appendChild(btn);
            });
            var add = document.createElement('button');
            add.type = 'button';
            add.className = 'bulk-chip bulk-chip-add';
            add.title = 'Save your own filter word as a patch (e.g. "decouple", "joint")';
            add.textContent = '+';
            scroll.appendChild(add);
        }
        function _remount() {
            var b = bar(); if (!b) return;
            _injectCustom(b);
            terms = Array.prototype.slice.call(
                b.querySelectorAll('.bulk-chip:not(.bulk-chip-add)')
            ).map(function (x) { return x.getAttribute('data-chip-term'); })
             .filter(Boolean);
            _paint();
        }
        function _openAdd() {
            var b = bar(); var scroll = b && b.querySelector('.bulk-chip-scroll');
            if (!scroll || scroll.querySelector('.bulk-chip-add-input')) return;
            var inp = document.createElement('input');
            inp.className = 'bulk-chip-add-input';
            inp.placeholder = 'new patch…';
            inp.setAttribute('aria-label', 'New filter patch');
            scroll.insertBefore(inp, scroll.querySelector('.bulk-chip-add'));
            inp.focus();
            function commit() {
                var t = inp.value.trim().toLowerCase();
                if (inp.parentNode) inp.parentNode.removeChild(inp);
                if (!/^[^\s|]{1,40}$/.test(t)) return;   // one token, no pipes
                var cur = _customTerms();
                if (cur.indexOf(t) < 0 && terms.indexOf(t) < 0) {
                    cur.push(t); _saveCustom(cur);
                }
                _remount();
                if (terms.indexOf(t) >= 0 && active.indexOf(t) < 0) active.push(t);
                _write();
            }
            inp.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); commit(); }
                else if (e.key === 'Escape') {
                    if (inp.parentNode) inp.parentNode.removeChild(inp);
                }
            });
            inp.addEventListener('blur', function () {
                setTimeout(function () { if (inp.parentNode) commit(); }, 120);
            });
        }
        function _removeCustom(term) {
            _saveCustom(_customTerms().filter(function (x) { return x !== term; }));
            var i = active.indexOf(term);
            if (i >= 0) active.splice(i, 1);
            // _write BEFORE _remount: once the term leaves `terms`,
            // _freeTokens would keep the word as user-typed text and the
            // filter would silently stay on (caught by selfcheck F9).
            _write();
            _remount();
        }
        function setMode(next) {
            mode = next === 'or' ? 'or' : 'and';
            try { localStorage.setItem(MODE_KEY, mode); } catch (e) {}
            _write();
        }
        /* The box is the truth: re-derive which chips are lit from its tokens.
           Called on every keystroke, so hand-typing `flux` lights Flux. */
        function syncFromQuery() {
            var toks = _tokens().map(function (t) { return t.toLowerCase(); });
            // Order comes from the QUERY, not from the chip row. Deriving it
            // from `terms` re-sorted the selection into render order on every
            // keystroke, so the box visibly reshuffled itself as the user
            // toggled — and, once _write started dispatching `input` for the
            // pair grid, that reshuffle fed straight back into the next write.
            var seen = {};
            active = [];
            toks.forEach(function (t) {
                if (terms.indexOf(t) >= 0 && !seen[t]) { seen[t] = 1; active.push(t); }
            });
            _paint();
        }
        function mount() {
            var b = bar(); if (!b) return;
            _injectCustom(b);   // docs/126 ③ — before terms are read
            terms = Array.prototype.slice.call(
                b.querySelectorAll('.bulk-chip:not(.bulk-chip-add)')
            ).map(function (x) { return x.getAttribute('data-chip-term'); })
             .filter(Boolean);
            mode = _readMode();
            if (b._chipWired) { syncFromQuery(); _paint(); return; }
            b._chipWired = true;
            b.addEventListener('click', function (e) {
                var t = e.target;
                if (!t || !t.classList) return;
                if (t.classList.contains('bulk-chip-x')) {
                    _removeCustom(t.parentNode.getAttribute('data-chip-term'));
                } else if (t.classList.contains('bulk-chip-add')) {
                    _openAdd();
                } else if (t.classList.contains('bulk-chip')) {
                    toggle(t.getAttribute('data-chip-term'));
                } else if (t.id === 'bulk-chip-mode') {
                    setMode(mode === 'and' ? 'or' : 'and');
                } else if (t.id === 'bulk-chip-offer-yes') {
                    setMode('or');
                } else if (t.id === 'bulk-chip-offer-no') {
                    offerDismissed = true;
                    _offer();
                }
            });
            syncFromQuery();
        }
        return { mount: mount, syncFromQuery: syncFromQuery, toggle: toggle,
                 setMode: setMode,
                 _state: function () { return { mode: mode, active: active.slice(), terms: terms.slice() }; } };
    })();

    // ── user font size + weight + letter-spacing (persisted; applied globally) ─
    // Controls live in Settings ▸ Live Edit since docs/120 item 4 (they were in
    // the grid toolbar, where the chip bar now is). The setters are unchanged
    // and stay panel-absent-safe, because base.html loads this file on EVERY
    // page while #bulk-panel exists only on /bulk.
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
        // audit F11 (docs/111): font/letter-spacing/bold rescale every cell —
        // the pinned columns' px insets must follow (deferred so the new
        // metrics are laid out before they are measured).
        if (window.__bulkRepin) setTimeout(window.__bulkRepin, 0);
        // docs/120 item 24: the value-history button caches the focused cell's
        // font + geometry so typing costs no layout. Rescaling every cell is
        // exactly the event that invalidates it, and it can happen WHILE a cell
        // holds focus — so drop the memo here rather than wait for a re-focus.
        if (window.__cellBtnInvalidate) window.__cellBtnInvalidate();
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
    // docs/120 item 19. `tbl.scrollWidth` is a FORCED SYNCHRONOUS LAYOUT, and
    // its four callers each run it straight after writing classes onto
    // thousands of cells — so every call re-laid out a 158-column × 20-row
    // sticky table from scratch. Measured with the CPU profiler on the real
    // 20-qubit chip: 397 ms of self time inside a single 1,429 ms blocked
    // frame at mount, the largest app-code cost on the page by 8×. (The audit
    // agent had blamed `_virtInit`'s layout reads instead; those measure
    // 0.6 ms for all 158 headers — reads in a row share one layout, it is the
    // write-then-read alternation that costs.)
    //
    // Deferring THIS read alone was measured and did not help: the cost simply
    // moved to `_updateStickyOffset` (397 ms -> 369 ms there), which proves the
    // expense is not any one function but the ALTERNATION — the first read
    // after a write pays for the whole re-layout, whoever it happens to be. So
    // both geometry reads are coalesced into one rAF that reads first and
    // writes second, the standard batching order: one forced layout per frame,
    // shared, instead of one per call site.
    //
    // Nothing needs either value synchronously — one sizes a cosmetic scrollbar
    // proxy, the other a sticky offset that is already re-applied on font and
    // column changes.
    // A missing rAF must DEGRADE, not throw: the geometry sync is called from
    // mount, and an exception there takes the whole grid down. (jsdom harnesses
    // hand over individual globals — CLAUDE.md's standing warning — and four
    // selfchecks went red on exactly this.)
    var _raf = (typeof window !== 'undefined' && window.requestAnimationFrame)
        ? window.requestAnimationFrame.bind(window)
        : function (f) { return setTimeout(f, 0); };
    var _layoutPending = false;
    function _syncTableGeometry() {
        if (_layoutPending) return;
        _layoutPending = true;
        _raf(function () {
            _layoutPending = false;
            var t = table();
            var inner = document.getElementById('bulk-scroll-top-inner');
            var grow = t && t.querySelector('.bulk-group-row');
            // READ both, then WRITE both — never interleave.
            var w = (t && inner) ? t.scrollWidth : null;
            var h = grow ? grow.offsetHeight : null;
            if (w !== null) inner.style.width = w + 'px';
            if (h !== null) t.style.setProperty('--bulk-grouphead-h', h + 'px');
        });
    }
    function _updateTopScroll() { _syncTableGeometry(); }
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
    function _updateStickyOffset() { _syncTableGeometry(); }

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
                // docs/111 (#11): a dyn toggle used to DESTROY unsaved edits
                // (the reload swaps the pane wholesale) — carry them across.
                _captureEditCarry();
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
    // docs/141 4s: the Pairs picker -- its own per-chip hidden set, on top of
    // the qubit follow rule below (a hidden qubit still takes its pairs with it)
    function _pKey() { return QHIDE_PREFIX + 'pairs:' + (QMETA.chip || 'chip'); }
    function _pHidden() {
        try {
            var a = JSON.parse(localStorage.getItem(_pKey()) || '[]');
            return new Set(Array.isArray(a) ? a : []);
        } catch (e) { return new Set(); }
    }
    function _savePHidden(set) {
        try { localStorage.setItem(_pKey(), JSON.stringify(Array.from(set))); } catch (e) {}
    }
    function _pairRows() {
        var pt = document.getElementById('bulk-pair-table'); if (!pt) return [];
        return Array.prototype.slice.call(pt.querySelectorAll('tbody tr[data-pair]'));
    }
    function _pairIds() { return _pairRows().map(function (r) { return r.getAttribute('data-pair') || ''; }); }
    function _pDirtyIds() {
        var d = {};
        _pairRows().forEach(function (r) { if (_rowDirty(r)) d[r.getAttribute('data-pair') || ''] = true; });
        return d;
    }
    function _applyPairFollow(hid) {
        var rows = _pairRows(); if (!rows.length) return;
        var phid = _pHidden();
        var shown = 0;
        rows.forEach(function (r) {
            var pid = r.getAttribute('data-pair') || '';
            var m = _pairMembers(pid);
            var off = (m.length === 2 && (hid.has(m[0]) || hid.has(m[1]))) || phid.has(pid);
            if (off && _rowDirty(r)) off = false;      // an unsaved edit never vanishes
            r.classList.toggle('bulk-qubit-off', off);
            if (!off) shown++;
        });
        var pill = document.getElementById('bulk-pair-pill');
        if (pill) {
            pill.hidden = shown === rows.length;
            pill.textContent = shown + ' of ' + rows.length + ' pairs \u2014 Show all';
        }
    }
    function applyPairVis() {
        _applyPairFollow(_qHidden());
        if (window.BulkPairEdit && BulkPairEdit.applySearch) { try { BulkPairEdit.applySearch(); } catch (e) {} }
    }
    function _buildPairMenu() {
        var menu = document.getElementById('bulk-pairvis-menu');
        if (!menu) return;
        var hid = _pHidden(), qhid = _qHidden();
        var dirty = _pDirtyIds();
        var ids = _pairIds();
        var html = '<div class="bulk-colvis-actions">' +
            '<button type="button" class="btn-xs outline" data-psel="all">All</button>' +
            '<button type="button" class="btn-xs outline" data-psel="none">None</button>' +
            '<button type="button" class="btn-xs outline" data-psel="invert">Invert</button></div>';
        ids.forEach(function (pid) {
            var isDirty = !!dirty[pid];
            var m = _pairMembers(pid);
            var followed = m.length === 2 && (qhid.has(m[0]) || qhid.has(m[1]));
            var checked = !hid.has(pid) || isDirty;
            html += '<label class="bulk-colvis-item bulk-qubit-item"><span>' +
                '<input type="checkbox" data-pcb="' + _esc(pid) + '"' +
                (checked ? ' checked' : '') + (isDirty ? ' disabled' : '') + '> ' + _esc(pid) + '</span>' +
                (isDirty
                    ? '<span class="bulk-qdirty" title="This pair has an unsaved edit \u2014 apply or reset it first">unsaved edit</span>'
                    : (followed
                        ? '<span class="bulk-qdirty" title="A qubit of this pair is hidden by the Qubits picker">qubit hidden</span>'
                        : '<button type="button" class="bulk-qonly" data-ponly="' + _esc(pid) + '" title="Show only ' + _esc(pid) + '">only</button>')) +
                '</label>';
        });
        menu.innerHTML = html;
        if (!menu._pBound) {
            menu._pBound = true;
            menu.addEventListener('click', function (ev) {
                var b = ev.target.closest('[data-psel],[data-ponly]');
                if (!b) return;
                ev.preventDefault();
                var hid2 = _pHidden(), dirty2 = _pDirtyIds(), all = _pairIds();
                if (b.hasAttribute('data-psel')) {
                    var mode = b.getAttribute('data-psel');
                    if (mode === 'all') hid2 = new Set();
                    else if (mode === 'none') hid2 = new Set(all.filter(function (id) { return !dirty2[id]; }));
                    else all.forEach(function (id) {
                        if (dirty2[id]) { hid2.delete(id); return; }
                        if (hid2.has(id)) hid2.delete(id); else hid2.add(id);
                    });
                } else {
                    var only = b.getAttribute('data-ponly');
                    hid2 = new Set(all.filter(function (id) { return id !== only && !dirty2[id]; }));
                }
                _savePHidden(hid2);
                applyPairVis();
                _buildPairMenu();
            });
            menu.addEventListener('change', function (ev) {
                var cb = ev.target.closest('input[data-pcb]');
                if (!cb) return;
                var hid2 = _pHidden();
                var id = cb.getAttribute('data-pcb');
                if (cb.checked) hid2.delete(id);
                else if (!_pDirtyIds()[id]) hid2.add(id);
                _savePHidden(hid2);
                applyPairVis();
                _buildPairMenu();
            });
        }
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
                _buildPairMenu();       // docs/141 4s: the 'qubit hidden' badges follow
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
                _buildPairMenu();       // docs/141 4s: the 'qubit hidden' badges follow
            });
        }
    }

    // ── search (columns by label, rows by id, cells by comma-insensitive value) ─
    // docs/141 4al: when the SEARCH hides every row, say so where the rows
    // were, with one click out. The remembered query (quam_bulk_search)
    // survives a chip change by design, so on a new chip it can match nothing
    // in this grid while the toolbar counter shrinks to an easily-missed
    // "0 of N" -- measured as "pairs do not render" on a real chip. The
    // picker's pill already explains ITS hiding; this is the search's half.
    function _emptySearchNote(t, noteId, shown, total, q) {
        var wrap = t && t.closest('.bulk-table-wrap');
        if (!wrap) return;
        var el = document.getElementById(noteId);
        var want = !!q && total > 0 && shown === 0;
        if (!want) { if (el) el.hidden = true; return; }
        if (!el) {
            el = document.createElement('p');
            el.id = noteId;
            el.className = 'bulk-empty-search-note';
            var msg = document.createElement('span');
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn-xs outline';
            btn.textContent = 'Clear search';
            btn.addEventListener('click', function () {
                var sb = document.getElementById('bulk-search');
                if (!sb) return;
                sb.value = '';
                sb.dispatchEvent(new Event('input', { bubbles: true }));
            });
            el.appendChild(msg); el.appendChild(btn);
            wrap.parentNode.insertBefore(el, wrap.nextSibling);
        }
        el.firstChild.textContent = '0 of ' + total + ' rows here match \u201c' + q
            + '\u201d \u2014 the search box above is filtering this grid. ';
        el.hidden = false;
    }

    function applySearch() {
        var t = table(); if (!t) return;
        var inp = document.getElementById('bulk-search');
        var q = inp ? inp.value.trim().toLowerCase() : '';
        var hide = _hiddenSet();
        // A folded column's header shows a name the chip does not use: the
        // strip rewrote `cz_flattop_pulse_q1_q2` into `cz_flattop_pulse_q1`.
        // `search` carries the real operation ids so looking for the name you
        // actually have in state.json finds its column.
        // docs/120 item 4: the SECTION joins the haystack. It is the band name
        // already printed above the column (`_bulk_column_groups`), so a user
        // searching "readout" or "flux" plainly means "that band" — and the
        // quick-filter chips emit exactly these words, which is what lets one
        // short keyword span the four XY-ish sections a chip really has
        // (XY Drive / XY Port / XY+ / XY Port+) instead of needing four chips.
        function _colHay(c) {
            return (c.label + ' ' + c.key + ' ' + (c.section || '')
                    + ' ' + (c.search || '')).toLowerCase();
        }
        var visCols = COLS.filter(function (c) { return !hide.has(c.key); });
        var tokens = q ? q.split(/\s+/) : [];

        // classify each token: matches a column label? a qubit id?
        var ids = _rows().map(function (r) { return (r.getAttribute('data-qubit') || '').toLowerCase(); });
        // The two axes each treat a both-hit token as NEUTRAL, so a token that
        // hit a column AND a row id used to filter NOTHING AT ALL. That is not
        // hypothetical: a real chip's pair-gate columns carry the partner
        // qubit's name in `search` (`cz_SNZ_flux_pulse_qA1`), which made every
        // qubit-id search on that chip a silent no-op. Classification is now
        // EXCLUSIVE by precedence, and a token that NAMES a row (a prefix of
        // some qubit id) wins: the grid is one row per qubit, so "qA1" means
        // "show me that qubit" — reading it as a column filter would show three
        // pair columns across all 21 rows instead. A token that merely occurs
        // INSIDE an id ("a1") keeps the column reading, so ordinary column
        // searching is untouched.
        var tokInfo = tokens.map(function (tok) {
            var named = ids.some(function (id) { return id.indexOf(tok) === 0; });
            var colHit = !named
                && visCols.some(function (c) { return _colHay(c).indexOf(tok) >= 0; });
            var idHit = !colHit && ids.some(function (id) { return id.indexOf(tok) >= 0; });
            return { tok: tok, isCol: colHit, isId: idHit, isVal: !colHit && !idHit };
        });
        // Shared grammar: space = AND across groups, standalone | = OR within
        // one (SearchQuery, tight-binding — `q1 | q2` is one group). The
        // classification above stays per token; the boolean structure is the
        // part every surface now shares. No pipe → singleton groups → the
        // loops below are byte-for-byte the old every-token AND.
        var tokGroups = window.SearchQuery
            ? window.SearchQuery.groupBy(tokInfo, function (ti) { return ti.tok; })
            : tokInfo.map(function (ti) { return [ti]; });

        // A token that doesn't restrict an axis is neutral (true) there —
        // exactly as in the old AND loops, where it was skipped. Inside an OR
        // group a neutral member makes the group pass for that axis, so
        // `q1 | q2` restricts rows and leaves every column visible.
        function colVisible(key, colCells) {
            var c = COLS.filter(function (x) { return x.key === key; })[0];
            for (var g = 0; g < tokGroups.length; g++) {
                var any = false;
                for (var i = 0; i < tokGroups[g].length && !any; i++) {
                    var ti = tokGroups[g][i];
                    if (ti.isCol && !ti.isId) {
                        any = !!c && _colHay(c).indexOf(ti.tok) >= 0;
                    } else if (ti.isVal) {
                        any = colCells.some(function (h) { return h.indexOf(ti.tok) >= 0; });
                    } else {
                        any = true;                       // id token — neutral here
                    }
                }
                if (!any) return false;
            }
            return true;
        }
        // row passes if every group has a member that matches: id tokens match
        // the row id, value tokens match some cell. Column-only tokens don't
        // restrict rows (neutral), exactly as before.
        // `valNeutral` answers the second question the grid needs: would this
        // row survive if the VALUE tokens didn't restrict rows? The difference
        // between the two answers is the number the "show them anyway" chip
        // reports — and, once the user clicks it, the visibility rule itself.
        function rowVisible(id, rowHaystacks, valNeutral) {
            for (var g = 0; g < tokGroups.length; g++) {
                var any = false;
                for (var i = 0; i < tokGroups[g].length && !any; i++) {
                    var ti = tokGroups[g][i];
                    if (ti.isId && !ti.isCol) {
                        any = id.indexOf(ti.tok) >= 0;
                    } else if (ti.isVal) {
                        any = valNeutral
                            || rowHaystacks.some(function (h) { return h.indexOf(ti.tok) >= 0; });
                    } else {
                        any = true;                       // column token — neutral here
                    }
                }
                if (!any) return false;
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
                // docs/105 #1: cold (unhydrated) cells contribute their STORED
                // values - the search must stay whole-chip (docs/85) even for
                // columns whose inputs are not mounted yet.
                if (_virt) {
                    r.querySelectorAll('td.bulk-td-cold').forEach(function (td) {
                        var ck = td.getAttribute('data-col-key');
                        if (hide.has(ck)) return;
                        var cd = _virt.vals.get(td) || '';
                        if (!cd) return;
                        var chh = cd + ' ' + cd.replace(/,/g, '');
                        hs.push(chh);
                        if (colHay[ck]) colHay[ck].push(chh);
                    });
                }
                _hayCache.rowMap.set(r, hs);
            });
            _hayCache.colHay = colHay;
        }
        var rowHay = rows.map(function (r) { return _hayCache.rowMap.get(r) || []; });

        // decide column visibility (search layer, on top of checkbox layer)
        var colSearchHide = {};
        visCols.forEach(function (c) { colSearchHide[c.key] = !colVisible(c.key, colHay[c.key] || []); });
        // docs/126 ③ perf: the class stays on the ~460 THs (the count/offer/
        // reveal machinery reads it there), but the ~9,000 TDs are hidden by
        // ONE generated stylesheet instead of a classList.toggle each — the
        // per-td invalidation was most of the (program) style-recalc block in
        // the 1.2–1.6 s patch press the customer reported. Tab/arrow cell
        // navigation consults _searchHiddenKeys instead of the td class.
        _searchHiddenKeys = {};
        t.querySelectorAll('th.bulk-col-head').forEach(function (el) {
            var k = el.getAttribute('data-col-key');
            if (k === '__id__' || hide.has(k)) return;   // checkbox-hidden handled elsewhere
            el.classList.toggle('bulk-search-hidden', !!colSearchHide[k]);
        });
        var _hideSels = [];
        // Night session 2026-08-28: hide by CLASS (`.ck-<index>`, stamped on every
        // th/td by the template), not by `td[data-col-key="k"]`. Chrome indexes
        // rules by class NAME, so a td tests only the rules for its own classes;
        // an attribute-equals rule is a candidate for EVERY td, and 300 hidden
        // columns x 8,000 tds was the 250-370 ms style recalc measured behind
        // each keystroke (real Chrome trace).
        //
        // 2026-08-28 (daytime, docs/141 4d): the rules are STATIC and the
        // keystroke only toggles `sh-<index>` classes on the TABLE for the
        // columns whose state CHANGED. Replacing the stylesheet's text each
        // keystroke made Chrome invalidate every element matched by any rule
        // of the old and the new sheet -- `elementsStyled: 10,673`, the whole
        // grid, 149 ms -- even when one more letter changed three columns.
        // A class diff on one element invalidates only the descendants its
        // changed classes' rules reach (`#bulk-table.sh-N td.ck-N`): the
        // cost is now proportional to the change, not to the grid.
        var _idxOf = _colIndexMap(t);
        _ensureCkSheet(_idxOf);
        var _want = {};
        Object.keys(colSearchHide).forEach(function (k) {
            if (!colSearchHide[k] || k === '__id__' || hide.has(k)) return;
            _searchHiddenKeys[k] = 1;
            if (_idxOf[k] != null) _want['sh-' + _idxOf[k]] = 1;
            else _hideSels.push('#bulk-table td[data-col-key="' + _cssEsc(k) + '"]');   // no ck class: the old dynamic rule
        });
        var _cur = (t.getAttribute('class') || '').split(/\s+/);
        for (var ci = 0; ci < _cur.length; ci++) {
            if (_cur[ci].indexOf('sh-') === 0 && !_want[_cur[ci]]) t.classList.remove(_cur[ci]);
        }
        Object.keys(_want).forEach(function (c) { if (!t.classList.contains(c)) t.classList.add(c); });
        var _dyn = _hideSels.length ? _hideSels.join(',\n') + ' { display: none !important; }' : '';
        if (_searchHideStyleEl().textContent !== _dyn) _searchHideStyleEl().textContent = _dyn;
        // docs/120 item 28 — SEARCHING FOR A COLUMN MUST MAKE IT USABLE, not
        // just visible. Cold columns (docs/105 virtualization) have their cell
        // contents detached, and hydration only ever fired on scroll, nav or a
        // path repaint — never on search. So a user who typed `T1`, narrowed
        // the grid to exactly that column, and reached for the cell found it
        // EMPTY and un-editable: the same shape as the report that opened this
        // campaign. Reproduced on the customer chip after the gate change
        // widened virtualization to it; latent before that for any chip over
        // the old 4,000-cell threshold.
        //
        // A surviving column is one the user has just asked to look at, so
        // hydrate it. Gated on a non-empty query: with no search every column
        // survives, and hydrating them all would simply undo virtualization.
        // 2026-08-28 (docs/141 4d): hydrate the surviving cold columns that the
        // narrowed grid puts ON SCREEN (the scroll pass, run now), not every
        // survivor. The first letter of any query survives most columns, so
        // "hydrate every survivor" undid the whole virtualization on the
        // first keystroke: 632 ms for `a` on the 20Q chip, measured. The
        // docs/120 #28 case still holds -- `T1` narrows the grid to that
        // column, which is then at the left edge and hydrated; anything
        // further right hydrates on scroll, as always.
        // Any change -- including CLEARING the search, which brings cold
        // (hidden-at-mount) columns back on screen -- schedules the pass in
        // a rAF, which runs BEFORE the next style/layout/paint: no frame of
        // empty tds is painted (the 4e-review note claiming otherwise was
        // wrong -- docs/141 4l-review), and a synchronous pass forced a
        // style+layout INSIDE the keystroke (measured). Hidden-at-mount
        // columns are frozen at their estimate like every other cold one.
        if (_virt) _virtOnScroll();
        // A new query retires the previous "show them anyway" choice — it was
        // made about those tokens, and silently carrying it forward would make
        // the next search quietly stop filtering rows.
        if (q !== _valRowsQ) { _valRowsAll = false; _valRowsQ = q; }
        var _hasVal = tokInfo.some(function (ti) { return ti.isVal; });

        // decide row visibility
        var shown = 0;
        var strandedRows = 0;
        rows.forEach(function (r, i) {
            var id = (r.getAttribute('data-qubit') || '').toLowerCase();
            var vis = rowVisible(id, rowHay[i], _valRowsAll);
            r.classList.toggle('bulk-row-hidden', !vis);
            // the count reflects what's actually on screen: search AND ⚏ Qubits
            if (vis && !r.classList.contains('bulk-qubit-off')) shown++;
            if (!vis && _hasVal && rowVisible(id, rowHay[i], true)) strandedRows++;
        });
        var cnt = document.getElementById('bulk-search-count');
        if (cnt) cnt.textContent = q ? (shown + ' of ' + rows.length) : '';
        _emptySearchNote(t, 'bulk-qubit-empty-search', shown, rows.length, q);
        // The offer only makes sense while a column is still on screen to type
        // into — with every column filtered away too there is nothing to reach.
        var _anyCol = visCols.some(function (c) { return !colSearchHide[c.key]; });
        var vrh = document.getElementById('bulk-valrow-hint');
        if (vrh) {
            var _showVrh = strandedRows > 0 && _anyCol;
            vrh.hidden = !_showVrh && !_valRowsAll;
            if (_valRowsAll && _hasVal) {
                vrh.textContent = 'showing all rows ✗';
                vrh.title = 'Value matches are highlighted; rows without a match '
                          + 'are shown so you can edit them. Click to filter again.';
            } else if (_showVrh) {
                vrh.textContent = strandedRows + ' more row'
                    + (strandedRows === 1 ? '' : 's') + ' — show';
                vrh.title = strandedRows + ' qubit' + (strandedRows === 1 ? '' : 's')
                          + ' have this column but not this value, so the search hid '
                          + 'them. Click to show them and edit them here.';
            }
        }
        // The search ALWAYS scans every column the chip has, including the ones
        // this user hid — a property you can't find is a property that doesn't
        // exist as far as the user is concerned (the r6-item-4 complaint). Two
        // disjoint populations of hidden column:
        //   (a) _colHintKeys — rendered into the DOM but checkbox-hidden; a
        //       pure CSS reveal, no server round-trip.
        //   (b) _dynHintKeys — derived columns the server never rendered
        //       (?dynhide=), so revealing them needs a /bulk reload.
        // Both surface through the one chip: "N hidden columns match — Show".
        // Deliberately a HINT, not an auto-reveal: silently re-showing a column
        // the user deliberately hid would fight them. Values in hidden columns
        // stay out of the row/column haystacks for the same reason — a row must
        // never match on evidence that isn't on screen.
        var hint = document.getElementById('bulk-dyncol-hint');
        if (hint) {
            _dynHintKeys = [];
            _colHintKeys = [];
            _pairHintKeys = [];
            if (q.length >= 2) {
                var _hintGroups = window.SearchQuery ? window.SearchQuery.groups(q)
                    : tokens.map(function (t) { return [t]; });
                var _match = function (c) {
                    var hay = (c.label + ' ' + c.key + ' ' + (c.section || '')).toLowerCase();
                    return window.SearchQuery
                        ? window.SearchQuery.matchesHay(hay, _hintGroups)
                        : tokens.every(function (tok) { return hay.indexOf(tok) >= 0; });
                };
                COLS.forEach(function (c) {
                    if (hide.has(c.key) && _match(c)) _colHintKeys.push(c.key);
                });
                var hiddenKeys = {};
                _dynHidden().forEach(function (k) { hiddenKeys[k] = true; });
                DYN.forEach(function (c) {
                    if (c.kind === 'note' || !hiddenKeys[c.key]) return;
                    if (_match(c)) _dynHintKeys.push(c.key);
                });
                // (c) the PAIR grid's hidden columns — one search box, one chip.
                try {
                    _pairHintKeys = (window.BulkPairEdit && BulkPairEdit.hiddenMatching)
                        ? (BulkPairEdit.hiddenMatching(tokens) || []) : [];
                } catch (e) { _pairHintKeys = []; }
            }
            var nHint = _colHintKeys.length + _dynHintKeys.length + _pairHintKeys.length;
            hint.hidden = !nHint;
            if (nHint) {
                hint.textContent = nHint + ' hidden column' +
                    (nHint === 1 ? '' : 's') + ' match — Show';
            }
        }
        _updateGroupHeader();   // re-span the group band over what's now visible
    }

    // ── sort + per-column min/max ────────────────────────────────────────────
    function sort(key) {
        var t = table(); if (!t) return;
        // a server-cold column has no values to sort by yet: fetch, then sort
        if (_virt && _virt.remote && _virt.remote.has(key)) {
            _virtHydrateCols([key]).then(function () { if (!(_virt && _virt.remote && _virt.remote.has(key))) sort(key); });
            return;
        }
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

    function _recomputeStats(onlyKeys) {
        var t = table(); if (!t) return;
        var hide = _hiddenSet();
        COLS.forEach(function (c) {
            if (onlyKeys && !onlyKeys[c.key]) return;
            // a COLD column has no cells to count: keep the server's numbers
            // (they were wiped and never came back -- docs/141 4l-review)
            // docs/141 4ae: a RETIRED column has no cells either -- it left
            // `cold` but never arrived -- so the same guard must cover it, or
            // the server's numbers are wiped and never come back.
            if (_virt && ((_virt.cold && _virt.cold.has(c.key))
                          || (_virt.dead && _virt.dead.has(c.key)))) return;
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
            _buildPairMenu();
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
            // docs/88: the server rendered this cell "not set" — the column
            // exists because a SIBLING entity carries that leaf, so filling it
            // in has to CREATE the key. Declaring it here (rather than letting
            // the server infer it) keeps the standing rule that a generic
            // bulk/plot edit can never silently create a mistyped path: only a
            // cell the server itself marked missing may ask for creation.
            var up = { dot_path: c.getAttribute('data-dot-path'), value: c.value };
            if (c.getAttribute('data-missing') === '1') up.create = true;
            updates.push(up);
        });
        if (!updates.length) return Promise.resolve({ ok: true, tray_html: null });
        var _postBatch = function (ups, fspAck, typeFix) {
            var payload = { updates: ups, expect_chip: window.__chipToken || '' };
            if (fspAck) payload.fsp_ack = fspAck;
            if (typeFix) payload.type_fix = typeFix;
            return fetch('/field/edit-batch', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function (resp) { return resp.json().then(function (j) { return { status: resp.status, body: j }; }); });
        };
        return _postBatch(updates, null)
            .then(function handleR(r) {
                // r14 ⑩: stored-as-TEXT cell(s) in the batch → the conversion
                // offer first; nothing was committed. Shared confirm wording.
                if (r.status === 409 && r.body && r.body.type_fix) {
                    var conv = window._confirmTypeFix
                        ? window._confirmTypeFix(r.body.type_fix)
                        : window.confirm(r.body.error || 'Convert stored text to a number?');
                    return _postBatch(updates, null, conv ? 'convert' : 'keep').then(handleR);
                }
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
    // docs/141 §4m: the before→after chip is CREATED on first hover, not rendered
    // per cell (4,000+ cells × 4 spans on a 20Q chip). Its "old" text is the
    // cell's data-baseline, which every path sets BEFORE marking a cell modified
    // (the server for a tray-pending cell, applyRow / the cross-table sync /
    // markModified here); the apply sites keep an existing chip in sync.
    function _ensureBA(td, cell) {
        if (td.querySelector('.bulk-ba')) return;
        var ba = document.createElement('span');
        ba.className = 'bulk-ba'; ba.setAttribute('aria-hidden', 'true');
        var o = document.createElement('span'); o.className = 'bulk-ba-old';
        o.textContent = cell.hasAttribute('data-baseline') ? cell.getAttribute('data-baseline') : (cell.getAttribute('data-orig') || '');
        var n = document.createElement('span'); n.className = 'bulk-ba-new';
        var d = document.createElement('span'); d.className = 'bulk-ba-delta'; d.hidden = true;
        ba.appendChild(o); ba.appendChild(document.createTextNode(' → ')); ba.appendChild(n); ba.appendChild(d);
        td.appendChild(ba);
    }
    function _hoverBA(e, show) {
        var td = e.target.closest && e.target.closest('.bulk-td');
        if (!td) return;
        var cell = td.querySelector('.bulk-cell');
        if (!cell || !cell.classList.contains('bulk-cell-modified')) return;
        if (show) _ensureBA(td, cell);
        var newEl = td.querySelector('.bulk-ba-new');
        if (newEl) newEl.textContent = cell.value;
        // docs/76: the hover chip answers "by how much?", not just "from what".
        var oldEl = td.querySelector('.bulk-ba-old');
        var dEl = td.querySelector('.bulk-ba-delta');
        if (dEl && oldEl && window.ValueDelta) {
            window.ValueDelta.paint(dEl, oldEl.textContent, cell.value);
        }
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
    // docs/141 §4m: the inline band message is created the first time a cell
    // actually warns (it sat in every cell before); it goes where the render
    // used to put it — before the physical-output line.
    function _ensureBandMsg(td) {
        var el = document.createElement('span'); el.className = 'bulk-band-msg'; el.hidden = true;
        var phys = td.querySelector('.bulk-phys');
        if (phys) td.insertBefore(el, phys); else td.appendChild(el);
        return el;
    }
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
        if (msg && !msgEl && td) msgEl = _ensureBandMsg(td);
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
        _virtHydrateLocal();         // docs/105 #1 - path-addressed repaint must see every input
                                     // (a server-cold column arrives fresh from the working copy)
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

    // -- cold-column hydration (docs/105 #1) --------------------------------
    // The server render is UNCHANGED (every pin on /bulk HTML holds); over
    // _VIRT_MIN_CELLS the mount detaches the CONTENTS of every td in columns
    // beyond the initial horizontal window, keeping each cell's display value
    // in a map so the search stays whole-chip (docs/85: a value must stay
    // findable even when its column is off-screen). Hydration is ONE-WAY and
    // on-demand: horizontal scroll, keyboard nav entering the column, or any
    // painter that addresses cells by path. Below the threshold nothing
    // changes - small chips are byte-identical, which is also the safety
    // gate every existing test chip rides.
    //
    // docs/120 item 19 — the gate used to be `total cells >= 4000` and the real
    // customer chip has 3,160 in this grid, so the mechanism built for exactly
    // this page DECLINED TO ENGAGE, 840 cells under a threshold that is a proxy
    // for the thing it cares about. Measured on that chip: with it off the
    // mount blocks the main thread for 2.46 s; with it on, 1.85 s and the worst
    // single stall falls from 1,368 ms to 1,016 ms.
    //
    // So gate on the BENEFIT instead of a proxy for it: how many cells would
    // actually go cold. That is already computed below, it needs no new
    // measurement, and it cannot be wrong about a wide-but-short or a
    // narrow-but-tall chip the way a bare cell count is. `_VIRT_MIN_CELLS`
    // stays as a cheap pre-filter so a genuinely small grid never even walks
    // its headers.
    // docs/141 4ad: the mechanism itself now lives in web/static/grid-virt.js
    // so the PAIR grid can have it too (§4n said to generalize it before a
    // second consumer appeared). This file keeps the qubit grid's DOM facts --
    // its table, its element ids, its hooks -- and `_virt` stays a live
    // reference to the instance's state because ~20 call sites read
    // `_virt.cold` / `.remote` / `.byPath` / `.vals` directly.
    var _gv = null;                  // the GridVirt instance
    var _virt = null;                // its state, or null
    // docs/141 4q: the ONE vertical scroller is #table-pane (the wrap is a
    // frame now) -- hydration listens to, and measures, that element. The
    // wrap fallback keeps a table mounted outside the pane (a test page)
    // working; a jsdom harness defines its geometry on the pane.
    function _scrollerOf(t) {
        return document.getElementById('table-pane') || t.closest('.bulk-table-wrap') || t.parentElement;
    }
    // docs/141 4q: the toolbar rows, the chip bar and the pair divider are as
    // wide as their containing block, so position:sticky has no room to hold
    // them while the pane scrolls sideways (real Chrome: the toolbar left at
    // -2475 px). Move them by the pane's scrollLeft instead, one rAF per
    // scroll event, so the search box and Apply all stay where the user is.
    var _BAR_SEL = '.bulk-toolbar, .bulk-chipbar, .bulk-pair-divider, .bulk-dyn-truncated, .bulk-virt-note';
    function _pinBars(scroller) {
        var x = scroller.scrollLeft || 0;
        var tf = x ? 'translateX(' + x + 'px)' : '';
        (scroller.querySelectorAll ? scroller : document).querySelectorAll(_BAR_SEL).forEach(function (b) { if (b.style.transform !== tf) b.style.transform = tf; });
    }
    function _pinBarsToScroll() {
        var t = table(); if (!t) return;
        var s = _scrollerOf(t); if (!s) return;
        if (!s._barsBound) {
            s._barsBound = true;
            var pending = false;
            s.addEventListener('scroll', function () {
                if (pending) return;
                pending = true;
                (window.requestAnimationFrame || function (f) { setTimeout(f, 0); })(function () { pending = false; _pinBars(s); });
            }, { passive: true });
        }
        _pinBars(s);                                   // a re-mount with the pane already scrolled
    }
    // docs/126 ③: search-hidden QUBIT-grid columns, as one stylesheet (tds)
    // + a key set (cell navigation) — see the applySearch note.
    var _searchHiddenKeys = {};
    function _searchHideStyleEl() {
        var el = document.getElementById('bulk-search-hide-style');
        if (!el) { el = document.createElement('style'); el.id = 'bulk-search-hide-style'; document.head.appendChild(el); }
        return el;
    }
    function _cssEsc(k) { return (window.CSS && CSS.escape) ? CSS.escape(k) : String(k).replace(/"/g, '\\"'); }
    // The static hide rules: one per column index, written ONCE per column
    // set and never touched by a keystroke (see applySearch).
    function _ensureCkSheet(idxOf) {
        var el = document.getElementById('bulk-search-ck-style');
        if (!el) { el = document.createElement('style'); el.id = 'bulk-search-ck-style'; document.head.appendChild(el); }
        var idx = Object.keys(idxOf).map(function (k) { return idxOf[k]; }).sort(function (a, b) { return a - b; });
        var sig = idx.length ? idx[0] + '-' + idx[idx.length - 1] + '/' + idx.length : '';
        if (el.getAttribute('data-sig') === sig) return el;
        var rules = idx.map(function (n) { return '#bulk-table.sh-' + n + ' td.ck-' + n + ' { display: none !important; }'; });
        el.textContent = rules.join('\n');
        el.setAttribute('data-sig', sig);
        return el;
    }
    // column key -> the `ck-N` index stamped on its cells (from the header row)
    function _colIndexMap(t) {
        var m = {};
        t.querySelectorAll('th.bulk-col-head[data-col-key]').forEach(function (h) {
            var hit = /(?:^|\s)ck-(\d+)(?:\s|$)/.exec(h.className || '');
            if (hit) m[h.getAttribute('data-col-key')] = hit[1];
        });
        return m;
    }
    /* ── the qubit grid's binding to GridVirt (docs/141 4ad) ───────────────
       Every name below existed before as a local function; they are wrappers
       now so no call site in this file changed. The DOM facts the core cannot
       know are the arguments. */
    function _virtInstance() {
        if (_gv) return _gv;
        if (!window.GridVirt) return null;
        _gv = window.GridVirt.create({
            table: table,
            rows: _rows,
            scroller: _scrollerOf,
            styleId: 'bulk-virt-width-style',
            noteId: 'bulk-virt-note',
            mapId: 'bulk-cold-map',
            tableSel: '#bulk-table',
            rowAttr: 'data-qubit',
            colWidths: function () { return _colWidths; },
            urlParams: function () {
                var q = '';
                var dh = _dynHidden();
                if (dh.length) q += '&dynhide=' + encodeURIComponent(dh.join(','));
                // the path-folded token when the page shipped one, else the
                // display name (an older page); the route accepts both (4ac)
                var tok = QMETA && (QMETA.chipKey || QMETA.chip);
                if (tok) q += '&chip=' + encodeURIComponent(tok);
                return q;
            },
            onLanded: function (t, set) {
                _hayCache = null;        // hydrated inputs join the DOM haystacks
                // a cold column's header stats were left alone; now that its
                // cells are here, compute them -- for these columns only
                try { _recomputeStats(set); } catch (e) {}
            },
            phase: _ph,
            onState: function (st) { _virt = st; },
        });
        return _gv;
    }
    // onState keeps `_virt` live; this stays as the ONE place that reads the
    // core's state, so a future caller cannot reintroduce a stale mirror.
    function _virtSync() { _virt = _gv ? _gv.state() : null; return _virt; }
    function _virtInit() {
        var gv = _virtInstance();
        // docs/141 4ae B-7: no GridVirt means the server-cold half of this
        // table will never be filled. Say so instead of leaving it blank.
        if (!gv) {
            _virt = null;
            if (window.GridVirtMissingNote) window.GridVirtMissingNote(table(), 'bulk-virt-note');
            return;
        }
        gv.init();
        _virtSync();
    }
    function _virtStyleEl() { var gv = _virtInstance(); return gv ? gv.styleEl() : document.createElement('style'); }
    function _virtNote(msg) { var gv = _virtInstance(); if (gv) gv.note(msg); }
    function _thHidden(h) { return window.GridVirt ? window.GridVirt.thHidden(h) : false; }
    function _virtPxPerChar() { return window.GridVirt ? window.GridVirt.pxPerChar() : 8; }
    var _resolved = { then: function (f) { try { f(); } catch (e) {} return _resolved; },
                      catch: function () { return _resolved; } };
    function _virtPatchColdValue(dotPath, disp) {
        if (!_gv) return false;
        var hit = _gv.patchColdValue(dotPath, disp);
        if (hit) _virtPatchColdValue.flushHay = true;
        return hit;
    }
    function _virtHydrateCols(keys) {
        if (!_gv) return _resolved;
        var r = _gv.hydrateCols(keys);
        _virtSync();
        return r && r.then ? r.then(function () { _virtSync(); }) : (_virtSync(), _resolved);
    }
    function _virtHydrateCol(key) { return _virtHydrateCols([key]); }
    function _virtHydrateAll() { return _gv ? _virtHydrateCols(Array.from((_gv.state() || { cold: [] }).cold)) : _resolved; }
    function _virtHydrateLocal() {
        if (!_gv || !_virt) return;
        _virtHydrateCols(Array.from(_virt.cold).filter(function (k) { return !_virt.remote.has(k); }));
    }
    function _virtEnsureTd(td) { if (_gv) { _gv.ensureTd(td); _virtSync(); } }
    function _virtOnScroll(immediate) { if (_gv) { _gv.onScroll(immediate); _virtSync(); } }
    function _virtPass() { if (_gv) { _gv.pass(); _virtSync(); } }

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


    // ── docs/111 (#11): selection · fill-down · paste-a-column · pinning ──
    // The 21-qubit-retune toolkit. Entirely client-side: /bulk HTML is
    // byte-identical (every server pin holds); pin glyphs are JS-injected.
    var _selAnchor = null;            // anchor td of the current selection
    function _selCells() {
        var t = table(); if (!t) return [];
        return Array.prototype.slice.call(t.querySelectorAll('td.bulk-sel'));
    }
    // audit: shift-clicking was a discoverable act with an undiscoverable
    // payoff — the selection painted an outline and the page said nothing.
    // Announce the count, name the keys, and MARK the anchor whose value
    // Ctrl+D propagates (all selected cells looked identical before).
    function _selHintEl() {
        var el = document.getElementById('bulk-sel-hint');
        if (!el) {
            var host = document.getElementById('bulk-dirty-count');
            if (!host || !host.parentNode) return null;
            el = document.createElement('span');
            el.id = 'bulk-sel-hint';
            el.className = 'bulk-sel-hint';
            host.parentNode.insertBefore(el, host);
        }
        return el;
    }
    function _syncSelHint() {
        var el = _selHintEl(); if (!el) return;
        var n = _selCells().length;
        el.textContent = n
            ? (n + ' cell' + (n === 1 ? '' : 's') + ' selected — Ctrl+D fills from the anchor · Esc clears')
            : '';
        var t = table();
        if (t) {
            t.querySelectorAll('td.bulk-sel-anchor').forEach(function (td) {
                td.classList.remove('bulk-sel-anchor');
            });
            if (n && _selAnchor) _selAnchor.classList.add('bulk-sel-anchor');
        }
    }
    function _clearSel() {
        _selCells().forEach(function (td) { td.classList.remove('bulk-sel'); });
        _selAnchor = null;
        _syncSelHint();
    }
    function _colCellTds(colKey) {
        // visible rows only, document order
        return _navRows().map(function (tr) {
            return tr.querySelector('td[data-col-key="' + _cssEsc(colKey) + '"]');
        }).filter(Boolean);
    }
    function _selectRange(fromTd, toTd) {
        var key = fromTd.getAttribute('data-col-key');
        if (!key || key !== toTd.getAttribute('data-col-key')) return false;
        var tds = _colCellTds(key);
        var a = tds.indexOf(fromTd), b = tds.indexOf(toTd);
        if (a < 0 || b < 0) return false;
        _clearSel();
        _selAnchor = fromTd;
        for (var i = Math.min(a, b); i <= Math.max(a, b); i++) {
            if (_editableIn(tds[i])) tds[i].classList.add('bulk-sel');
        }
        _syncSelHint();
        return true;
    }
    function _fillSelection() {
        var sel = _selCells();
        if (!sel.length || !_selAnchor) return 0;
        var src = _editableIn(_selAnchor) || _selAnchor.querySelector('.bulk-cell');
        if (!src) return 0;
        var v = src.value;
        var undo = [], n = 0;
        sel.forEach(function (td) {
            var c = _editableIn(td);
            if (!c || c === src || c.value === v) return;
            undo.push({ dp: c.getAttribute('data-dot-path'), prev: c.value, next: v });
            // the manual path decides f_01<->RF coupling at FOCUS; a
            // programmatic fill must do the same or the twin silently
            // desyncs on exactly the retune this feature exists for.
            if (FREQ_TWIN[_colKeyOf(c)]) _freqFocus(c);
            c.value = v;
            c.dispatchEvent(new Event('input', { bubbles: true }));
            n++;
        });
        if (n && window.LiveEditUndo) {
            window.LiveEditUndo.record('fill-down (' + n + ' cells)', undo);
        }
        if (n && window.showToast) window.showToast('Filled ' + n + ' cell' + (n === 1 ? '' : 's') + ' — review, then Apply');
        return n;
    }
    function _pasteColumn(cell, text) {
        var lines = String(text).replace(/\r/g, '').split('\n')
            .map(function (l) { return l.split('\t')[0].trim(); })
            .filter(function (l, i, arr) { return !(l === '' && i === arr.length - 1); });
        if (lines.length < 2) return false;   // single value → native paste
        var td = cell.closest('td');
        var key = td && td.getAttribute('data-col-key');
        if (!key) return false;
        var tds = _colCellTds(key);
        var start = tds.indexOf(td);
        if (start < 0) return false;
        // audit F13: snapshot every prev BEFORE writing anything — a linked
        // (shared-port) column mirrors each write across its group, so a
        // read-as-you-go prev captured the PREVIOUS pasted value and Ctrl+Z
        // converged on an intermediate instead of the original.
        var plan = [];
        for (var i = 0; i < lines.length && start + i < tds.length; i++) {
            var c = _editableIn(tds[start + i]);
            plan.push({ cell: c, value: lines[i], prev: c ? c.value : null });
        }
        var undo = [], applied = 0, blocked = 0;
        plan.forEach(function (it) {
            if (!it.cell) { blocked++; return; }   // read-only row INSIDE the range
            applied++;
            if (it.prev === it.value) return;
            undo.push({ dp: it.cell.getAttribute('data-dot-path'),
                        prev: it.prev, next: it.value });
            if (FREQ_TWIN[_colKeyOf(it.cell)]) _freqFocus(it.cell);
            it.cell.value = it.value;
            it.cell.dispatchEvent(new Event('input', { bubbles: true }));
        });
        // audit F14: the START cell may hold text the user typed since focus —
        // re-sync LiveEditUndo's focus snapshot so its change-listener does not
        // record a SECOND entry for the same cell on blur.
        if (plan.length && plan[0].cell && window.LiveEditUndo
            && window.LiveEditUndo.resync) {
            window.LiveEditUndo.resync(plan[0].cell);
        }
        var overflow = lines.length - plan.length;
        if (undo.length && window.LiveEditUndo) {
            window.LiveEditUndo.record('pasted column (' + undo.length + ' cells)', undo);
        }
        if (window.showToast) {
            // audit F7: a mid-column read-only skip is NOT overflow — say which
            var why = [];
            if (overflow > 0) why.push(overflow + ' beyond the last row');
            if (blocked > 0) why.push(blocked + ' onto read-only cells');
            window.showToast('Pasted ' + applied + ' value' + (applied === 1 ? '' : 's')
                + (why.length ? ' (' + why.join(', ') + ' ignored)' : '')
                + ' — review, then Apply');
        }
        return true;
    }

    // -- pinning ---------------------------------------------------------
    var PIN_COLS_KEY = 'quam_bulk_pinned_cols';
    function _pinRowsKey() {
        return 'quam_bulk_pinned_rows::' + String(window.__chipToken || 'chip');
    }
    function _pinnedCols() {
        try { return JSON.parse(localStorage.getItem(PIN_COLS_KEY) || '[]'); }
        catch (e) { return []; }
    }
    function _pinnedRows() {
        try { return JSON.parse(localStorage.getItem(_pinRowsKey()) || '[]'); }
        catch (e) { return []; }
    }
    function _applyColPins() {
        var t = table(); if (!t) return;
        // reset
        t.querySelectorAll('.bulk-col-pinned').forEach(function (el) {
            el.classList.remove('bulk-col-pinned');
            el.style.left = ''; el.style.position = '';
        });
        var pins = _pinnedCols().filter(function (k) {
            return t.querySelector('th.bulk-col-head[data-col-key="' + _cssEsc(k) + '"]');
        });
        if (!pins.length) return;
        // audit F12: sticky cannot reorder columns — cumulative insets must
        // follow DOM order or an out-of-order pin pair overlaps at rest.
        var _heads = Array.prototype.slice.call(
            t.querySelectorAll('th.bulk-col-head[data-col-key]'))
            .map(function (h) { return h.getAttribute('data-col-key'); });
        pins.sort(function (a, b) { return _heads.indexOf(a) - _heads.indexOf(b); });
        // the row-header (qubit id) column is the base offset
        var rowHead = t.querySelector('tbody th.bulk-rowhead, tbody tr > th');
        var left = rowHead ? rowHead.offsetWidth : 0;
        pins.forEach(function (k) {
            _virtEnsureTd(t.querySelector('td[data-col-key="' + _cssEsc(k) + '"]'));
            var th = t.querySelector('th.bulk-col-head[data-col-key="' + _cssEsc(k) + '"]');
            var w = th ? th.offsetWidth : 0;
            // audit F4: the CELLS and the header only — the resize-handle span
            // inside the th also carries data-col-key, and an inline
            // position:sticky killed its absolute anchoring (drag-resize and
            // double-click auto-fit died on any pinned column).
            var els = t.querySelectorAll(
                'th.bulk-col-head[data-col-key="' + _cssEsc(k) + '"], ' +
                'td[data-col-key="' + _cssEsc(k) + '"]');
            Array.prototype.forEach.call(els, function (el) {
                el.classList.add('bulk-col-pinned');
                el.style.position = 'sticky';
                el.style.left = left + 'px';
            });
            left += w;
        });
    }
    function _applyRowPins() {
        var t = table(); if (!t) return;
        var tb = t.querySelector('tbody'); if (!tb) return;
        var pins = _pinnedRows();
        t.querySelectorAll('tr.bulk-row-pinned').forEach(function (tr) {
            tr.classList.remove('bulk-row-pinned');
        });
        if (!pins.length) return;
        // float pinned rows to the top, preserving pin order
        for (var i = pins.length - 1; i >= 0; i--) {
            var tr = tb.querySelector('tr[data-qubit="' + _cssEsc(pins[i]) + '"]');
            if (tr) { tr.classList.add('bulk-row-pinned'); tb.insertBefore(tr, tb.firstChild); }
        }
    }
    // audit F11: the sticky insets are a px snapshot — anything that changes
    // real widths (font scale, drag-resize, curated column show/hide) must
    // re-derive them or pinned columns drift over the qubit-name column.
    function _repinAfterLayout() {
        if (!_pinnedCols().length) return;
        _applyColPins();
    }
    function _togglePinCol(key) {
        var arr = _pinnedCols();
        var i = arr.indexOf(key);
        if (i >= 0) arr.splice(i, 1); else arr.push(key);
        try { localStorage.setItem(PIN_COLS_KEY, JSON.stringify(arr)); } catch (e) {}
        _applyColPins();
        _syncPinGlyphs();
    }
    function _togglePinRow(qid) {
        var arr = _pinnedRows();
        var i = arr.indexOf(qid);
        if (i >= 0) arr.splice(i, 1); else arr.push(qid);
        try { localStorage.setItem(_pinRowsKey(), JSON.stringify(arr)); } catch (e) {}
        _applyRowPins();
        _syncPinGlyphs();
    }
    function _syncPinGlyphs() {
        var t = table(); if (!t) return;
        var pc = _pinnedCols(), pr = _pinnedRows();
        t.querySelectorAll('.bulk-pin-col').forEach(function (b) {
            b.classList.toggle('bulk-pin-on', pc.indexOf(b.getAttribute('data-pin')) >= 0);
        });
        t.querySelectorAll('.bulk-pin-row').forEach(function (b) {
            b.classList.toggle('bulk-pin-on', pr.indexOf(b.getAttribute('data-pin')) >= 0);
        });
    }
    function _injectPinGlyphs() {
        var t = table(); if (!t) return;
        t.querySelectorAll('th.bulk-col-head[data-col-key]').forEach(function (th) {
            if (th.querySelector('.bulk-pin-col')) return;
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'bulk-pin bulk-pin-col';
            b.setAttribute('data-pin', th.getAttribute('data-col-key'));
            b.title = 'Pin this column (stays visible while scrolling)';
            b.textContent = '\uD83D\uDCCC';
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();   // never trigger the sort
                _togglePinCol(b.getAttribute('data-pin'));
            });
            th.appendChild(b);
        });
        t.querySelectorAll('tbody tr[data-qubit]').forEach(function (tr) {
            var head = tr.querySelector('th');
            if (!head || head.querySelector('.bulk-pin-row')) return;
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'bulk-pin bulk-pin-row';
            b.setAttribute('data-pin', tr.getAttribute('data-qubit'));
            b.title = 'Pin this qubit row to the top';
            b.textContent = '\uD83D\uDCCC';
            b.addEventListener('click', function () {
                _togglePinRow(b.getAttribute('data-pin'));
            });
            head.appendChild(b);
        });
        _syncPinGlyphs();
    }

    // -- unsaved-edit carry across the dyn-column reload ------------------
    // audit F10: the leave-confirm carve-out and the carry TTL must be ONE
    // number — a slow /bulk (measured 419 ms, but a 452-column chip is worse)
    // landing between them re-raised the exact discard confirm the carve-out
    // exists to remove, over edits that WERE preserved.
    var CARRY_TTL_MS = 15000;
    var _editCarry = null;    // {list: [{dp, value}], at}
    function _captureEditCarry() {
        var t = table(); if (!t) return;
        var list = [];
        _cells(t).forEach(function (c) {
            if (_isDirty(c) && c.getAttribute('data-dot-path')) {
                list.push({ dp: c.getAttribute('data-dot-path'), value: c.value });
            }
        });
        if (list.length) {
            _editCarry = { list: list, at: Date.now() };
            window._dynReloadAt = Date.now();   // the leave-confirm carve-out
            _armCarryDisarm();                  // ...and its guaranteed release
        }
    }
    function _consumeEditCarry() {
        if (!_editCarry || Date.now() - _editCarry.at > CARRY_TTL_MS) {
            _editCarry = null; window._dynReloadAt = 0; return;
        }
        var t = table(); if (!t) { _editCarry = null; window._dynReloadAt = 0; return; }
        // audit F3: a carried edit whose column is COLD (docs/105 detached its
        // inputs, and the fresh pane starts at scrollLeft 0) had no .bulk-cell
        // to land in and was silently dropped — with the leave-confirm
        // suppressed. Hydrate everything once when any carried path is
        // missing, then place them all.
        var missing = _editCarry.list.some(function (it) {
            return !t.querySelector('.bulk-cell[data-dot-path="' + _cssEsc(it.dp) + '"]');
        });
        if (missing) {
            var pending = _virtHydrateAll();
            if (_virt && _virt.remote && _virt.remote.size) {
                var carry = _editCarry;
                _editCarry = null; window._dynReloadAt = 0;
                pending.then(function () { _editCarry = carry; carry.at = Date.now(); _consumeEditCarry(); });
                return;
            }
        }
        var n = 0, lost = 0;
        _editCarry.list.forEach(function (it) {
            var c = t.querySelector('.bulk-cell[data-dot-path="' + _cssEsc(it.dp) + '"]');
            if (!c || c.readOnly) { lost++; return; }
            if (c.value !== it.value) {
                c.value = it.value;
                c.dispatchEvent(new Event('input', { bubbles: true }));
                // audit F2: mount() calls this BEFORE the table's own 'input'
                // listener is bound on a fresh table, so the dispatch alone
                // left carried cells unmarked (row Apply stayed disabled).
                // Do the marking here — deterministic, no ordering dependency.
                _markCellDirty(c);
                if (c.classList.contains('bulk-cell-linked')) _mirrorLinked(c);
                _refreshRow(_rowOf(c));
                n++;
            }
        });
        _refreshGlobal();
        _editCarry = null;
        window._dynReloadAt = 0;   // audit F8: the carve-out is over
        if (window.showToast && (n || lost)) {
            window.showToast(
                n + ' unsaved edit' + (n === 1 ? '' : 's') + ' preserved across the column change'
                + (lost ? ' \u2014 ' + lost + ' could not be restored (the field is gone)' : ''));
        }
    }

    function _bindGridEditing() {
        var t = table(); if (!t || t._geBound) return;
        t._geBound = true;
        t.addEventListener('click', function (ev) {
            var cell = ev.target.closest && ev.target.closest('.bulk-cell');
            if (!cell || cell.readOnly) { if (!ev.shiftKey && !ev.ctrlKey) _clearSel(); return; }
            var td = cell.closest('td');
            if (ev.shiftKey && _selAnchor) {
                if (_selectRange(_selAnchor, td)) ev.preventDefault();
            } else if (ev.ctrlKey || ev.metaKey) {
                // audit F1: the contract is SAME-COLUMN selection ("a range
                // across properties is meaningless") — shift-click enforced it,
                // ctrl-click did not, so Ctrl+D could fill a T1 value into an
                // amplitude column. Both paths refuse a foreign column now.
                if (_selAnchor && _selAnchor.getAttribute('data-col-key')
                    !== td.getAttribute('data-col-key')) return;
                td.classList.toggle('bulk-sel');
                if (!_selAnchor) _selAnchor = td;
                _syncSelHint();
            } else {
                _clearSel();
                _selAnchor = td;
            }
        });
        // audit: bound to the TABLE, Ctrl+D reached the browser's bookmark
        // dialog whenever focus sat anywhere else, and Escape could not clear
        // a selection the user could still see. Document level, gated on a
        // live selection so it can never steal either key otherwise.
        if (!window.__bulkSelKeysBound) {
            window.__bulkSelKeysBound = true;
            document.addEventListener('keydown', function (ev) {
                // KEY first, selection second (measured: querying a 4,851-cell
                // table on every keystroke cost 2.34 ms app-wide — 23 ms/s
                // while typing in the grid, on the typing path itself).
                var isFill = (ev.ctrlKey || ev.metaKey)
                    && (ev.key === 'd' || ev.key === 'D');
                if (!isFill && ev.key !== 'Escape') return;
                // docs/120 item 9: Escape cleared a SELECTION but could not
                // cancel the edit in progress — the one thing every grid on
                // earth binds it to. Worse than inert: the typed value stayed
                // in the box, so the next click away COMMITTED the edit the
                // user had just tried to abandon. Revert-and-keep-focus, the
                // spreadsheet contract; the selection branch below is
                // untouched, so Escape with nothing being typed still clears.
                if (ev.key === 'Escape') {
                    var _ae = document.activeElement;
                    if (_ae && _ae.classList
                            && _ae.classList.contains('bulk-cell')
                            && !_ae.readOnly && _isDirty(_ae)) {
                        ev.preventDefault();
                        ev.stopPropagation();       // don't also close a popover
                        _ae.value = _ae.getAttribute('data-orig');
                        _ae.dispatchEvent(new Event('input', { bubbles: true }));
                        _ae.select();
                        return;
                    }
                }
                if (!_selCells().length) return;
                if (isFill) {
                    ev.preventDefault();
                    _fillSelection();
                } else {
                    _clearSel();
                }
            }, true);
        }
        t.addEventListener('paste', function (ev) {
            var cell = ev.target.closest && ev.target.closest('.bulk-cell');
            if (!cell || cell.readOnly) return;
            var text = ev.clipboardData ? ev.clipboardData.getData('text/plain') : '';
            if (text && text.indexOf('\n') >= 0) {
                if (_pasteColumn(cell, text)) ev.preventDefault();
            }
        });
        // sorting reorders the tbody — re-float pinned rows after the sorter ran
        var thead = t.querySelector('thead');
        if (thead) {
            thead.addEventListener('click', function (ev) {
                // audit F6: the real sort trigger is `thead th[data-col-key]`
                // — which includes the qubit-name corner (class bulk-corner);
                // the old selector missed it, so name-sorting scattered pins.
                if (ev.target.closest && ev.target.closest('thead th[data-col-key]')) {
                    setTimeout(function () { _applyRowPins(); _applyColPins(); }, 0);
                }
            });
        }
    }

    window.__bulkRepin = function () { try { _repinAfterLayout(); } catch (e) {} };
    /* The mount's phase clock (docs/141 4i): window.__bulkMountTimings =
       [[phase, ms], ...] -- each entry is the time the NAMED phase took.
       Cheap (performance.now per phase), always on, read by the real-Chrome
       probe that measures where the seconds between "table on screen" and
       "grid ready" go. */
    var _mountT0 = 0;
    function _ph(label) {
        var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
        if (label !== 'start') (window.__bulkMountTimings = window.__bulkMountTimings || []).push([label, Math.round(now - _mountT0)]);
        _mountT0 = now;
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
                          chipKey: String(qubitMeta.chipKey || ''),
                          qubits: Array.isArray(qubitMeta.qubits) ? qubitMeta.qubits : [] };
            }
            var t = table();
            if (!t) return;
            // Restore the persisted search/filter before applySearch runs below.
            var sb0 = document.getElementById('bulk-search');
            if (sb0) { try { sb0.value = localStorage.getItem(SEARCH_KEY) || ''; } catch (e) {} }
            window.__bulkMountTimings = [];
            _ph('start');
            _loadColWidths();
            _applyColWidthStyle();   // re-apply persisted column widths after each (re)render
            _ph('col widths');
            _buildColMenu();
            _ph('col menu');
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
            _buildPairMenu();
            _ph('qubit menu');
            _applyColumnVisibility();
            _ph('column visibility + search');
            _applyQubitVisCore();   // restore the persisted ⚏ Qubits selection
            _ph('qubit visibility');
            _recomputeStats();
            _ph('stats');
            // docs/109 audit: reformat annotations to the viewer's unit
            // BEFORE virtualization freezes widths + stashes cold HTML.
            if (window.PhysAmp) PhysAmp.applyAll(table());
            _ph('PhysAmp');
            _virtInit();            // docs/105 #1 - after layout is final
            _ph('virtualization');
            // docs/141 4ac (CRITICAL): the mount's first applySearch runs
            // inside _applyColumnVisibility ABOVE, before _virt exists -- and
            // the cold cells' contribution to the haystack is behind
            // `if (_virt)`. A REMEMBERED search (localStorage quam_bulk_search,
            // restored into the box a few lines up) therefore matched nothing
            // in any server-cold column: the grid read "0 of 20" and hid every
            // row, permanently, on the ordinary htmx nav into this page.
            // _hayCache must be dropped too -- it is keyed on the hidden-column
            // set alone, so a second applySearch would reuse the column
            // haystacks built while _virt was still null (measured: without
            // this line the re-run is a no-op).
            var _sb0 = document.getElementById('bulk-search');
            if (_virt && _sb0 && _sb0.value) { _hayCache = null; applySearch(); }
            // the estimate is conservative, not exact: one rAF pass (real
            // geometry of the PRUNED table) hydrates anything on screen it
            // called cold -- a drag-resized or zoomed grid (docs/141 4l-review)
            if (_virt) _virtOnScroll();
            // docs/156: _pinBarsToScroll READS scrollLeft, and every phase
            // below WRITES. A read after a write lays the whole grid out
            // again, so having the read HERE cost the mount two full layouts
            // of the same 53,000 px table -- measured on the 20Q chip at
            // 137 ms (this read) and 124 ms (grid-virt's deferred pass, whose
            // own read is invalidated by the writes that follow this line).
            // It runs at the END of the mount now; see the call below.
            // Later is also more correct: _consumeEditCarry restores the
            // pane's scroll position, and pinning the bars before that pinned
            // them to the PRE-restore offset.
            // docs/111 (#11): selection/fill/paste/pins + the dyn-reload
            // edit carry. Pins re-apply AFTER virtualization (a pinned cold
            // column is hydrated by _applyColPins itself).
            _selAnchor = null;      // a stale anchor from the previous DOM
            _bindGridEditing();
            _injectPinGlyphs();
            _applyRowPins();
            _applyColPins();
            _ph('editing + pins');
            _consumeEditCarry();
            _setupTopScroll();
            _applyFont();
            _updateTopScroll();
            _ph('carry + scroll + font');
            // flag any already-out-of-band ports on load
            Array.prototype.slice.call(t.querySelectorAll('.bulk-cell[data-lo-field]')).forEach(_validateBand);
            _updateBandWarnCount();
            _ph('band validation');
            _markLinkedCells();   // tag shared physical-port cells so edits mirror across the port
            _ph('linked cells');
            // docs/156: the mount's LAST act, and its only geometry read --
            // one layout, which grid-virt's deferred pass then reads for free.
            _pinBarsToScroll();      // docs/141 4q: the toolbar rows follow the pane's sideways scroll
            _ph('pin bars');
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
                if (e.target.closest && e.target.closest('.bulk-col-hist, .key-help-btn')) return;
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
                // ...and the same reasoning covers LEAVING. Clicking a sidebar link
                // blurs the focused cell first, so this handler committed that one
                // row, and only THEN did the leave-guard ask "Leave and discard
                // them?". Accepting the discard then dropped every pasted value
                // EXCEPT the focused one, which survived as a pending edit armed to
                // reach the chip on the next Apply to live — a discard that commits.
                // Anything that swaps #table-pane IS a leave; let the leave-guard own
                // the decision rather than committing behind it.
                if (to && to.closest && to.closest(
                        '#bulk-apply-all, #bulk-apply-sync, #bulk-reset, '
                        + '[hx-target="#table-pane"]')) return;
                var b = row && row.querySelector('.bulk-row-apply');
                if (b && !b.disabled) BulkEdit.applyRow(b);
            });
            // Enter applies the row; arrow keys move between cells (spreadsheet nav).
            t.addEventListener('keydown', function (e) {
                var cell = e.target.closest && e.target.closest('.bulk-cell');
                if (!cell) return;
                // audit: Ctrl/Cmd+Enter belongs to docs/113's Apply-all — without
                // this it ALSO fired applyRow, i.e. two concurrent writes.
                if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
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
                ChipBar.syncFromQuery();   // typing a chip's word lights the chip
                // A chip press already ran applySearch SYNCHRONOUSLY and only
                // dispatches this event to reach the pair grid, which listens
                // here. Without this check the press cost TWO full scans of a
                // 4,480-cell table (measured 12.9-30.5 ms sync + another
                // debounced pass) — so the button built to replace typing cost
                // about twice what typing the same word does.
                if (window._chipDrivenSearch) return;
                // DEBOUNCED (audit: typing here was slow): applySearch re-scans
                // the table and re-toggles ~2000 cells' classes — a full-table
                // reflow on a multi-MB DOM. One pass shortly after the last
                // keystroke instead of one per key.
                if (_searchTimer) clearTimeout(_searchTimer);
                _searchTimer = setTimeout(applySearch, window.__bulkSearchDebounce || 200);   // 200: user-directed 2026-08-28 (docs/141 4d measured 120 vs 200 equal in cost)
            });
            ChipBar.mount();

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
                        // r16 ⓪-2: UndoNav managed navigation — the typing was
                        // STASHED (restored on return), not discarded; a
                        // confirm here would be a lie.
                        if (window._undoNavAt
                            && Date.now() - window._undoNavAt < 4000) return;
                        // docs/111: the dyn-column reload CARRIES the edits
                        // (_captureEditCarry/_consumeEditCarry) — a discard
                        // confirm here would be a lie.
                        if (window._dynReloadAt
                            && Date.now() - window._dynReloadAt < CARRY_TTL_MS) return;
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

        // docs/120 item 8 — the way out of a value search that hid exactly the
        // rows you meant to type into. A toggle, not a one-way door: the second
        // click restores the filter, and any change to the query retires it.
        toggleStrandedRows: function () {
            _valRowsAll = !_valRowsAll;
            applySearch();
        },

        // Un-hide every column the current search matched — the "N hidden
        // columns match — Show" chip. Curated/rendered columns reveal by CSS
        // (no round-trip); dyn columns the server never rendered need the pane
        // reload, so that branch runs LAST and returns.
        showMatchedDynCols: function () {
            var revealed = false;
            if (_pairHintKeys.length && window.BulkPairEdit && BulkPairEdit.showColumns) {
                BulkPairEdit.showColumns(_pairHintKeys);
                _pairHintKeys = [];
                revealed = true;
            }
            if (_colHintKeys.length) {
                var hs = _hiddenSet();
                _colHintKeys.forEach(function (k) { hs.delete(k); });
                _saveHidden(hs);
                _colHintKeys = [];
                _applyColumnVisibility();   // re-runs applySearch → refreshes the chip
                _buildColMenu();            // the checkboxes must agree with the grid
                _recomputeStats();
                revealed = true;
            } else if (revealed) {
                applySearch();              // pair-only reveal — still retire the chip
            }
            if (!_dynHintKeys.length) return;
            var arr = _dynHidden().filter(function (k) { return _dynHintKeys.indexOf(k) < 0; });
            _saveDynHidden(arr);
            _reopenColvis = true;
            _captureEditCarry();   // audit F9
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
            _buildPairMenu();
        },
        // docs/141 4s: the Pairs pill
        showAllPairs: function () {
            _savePHidden(new Set());
            applyPairVis();
            _buildPairMenu();
        },
        _pairsHidden: function () { return Array.from(_pHidden()); },
        chips: ChipBar,   // docs/120 item 4 — quick-filter chip bar
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
            _captureEditCarry();   // audit F9
            _reloadPane();
        },
        resetColumns: function () {
            try { localStorage.removeItem(HIDE_KEY); } catch (e) {}
            try { localStorage.removeItem(DYNHIDDEN_KEY); } catch (e) {}
            _reopenColvis = true;
            _captureEditCarry();   // audit F9
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

    // Keyboard navigation lands on cells you can TYPE in. A read-only cell
    // already declares itself out of the tab order with tabindex="-1"; the
    // handlers below preventDefault() and focus by hand, which overrode that
    // declaration — harmless while ~2% of cells were read-only, unusable once
    // a per-neighbour column renders a blank for every qubit that is not in
    // that pair (a real chip went from 2 to 48 consecutive dead stops).
    function _editableIn(td) {
        _virtEnsureTd(td);           // docs/105 #1 - nav into a cold column hydrates it
        var c = td && td.querySelector('.bulk-cell');
        return c && !c.classList.contains('bulk-cell-ro') ? c : null;
    }

    // A row you can navigate INTO. The grid hides rows two INDEPENDENT ways —
    // the search box (.bulk-row-hidden) and the qubit picker (.bulk-qubit-off)
    // — and both are display:none, but the movement helpers only ever filtered
    // the first. Stepping onto a display:none row makes .focus() a silent
    // no-op, which is exactly why the up/down arrows stopped working once a
    // subset of qubits was picked: the grid looked short, and every vertical
    // press aimed at a row that was still in the DOM but not on the screen.
    function _navRows() {
        return _rows().filter(function (r) {
            return !r.classList.contains('bulk-row-hidden')
                && !r.classList.contains('bulk-qubit-off');
        });
    }

    function _gridMove(cell, dr, dc) {
        var td = cell.closest('td');
        var tr = cell.closest('tr');
        var rows = _navRows();
        var ri = rows.indexOf(tr);
        if (dr) {
            var key = td.getAttribute('data-col-key');
            var ksel = '[data-col-key="'
                + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]';
            // Keep walking rather than giving up on the immediate neighbour:
            // that row's cell in this column may be read-only (a per-neighbour
            // operation the qubit does not carry renders as a blank), and
            // stopping there would strand the caret with nothing happening.
            for (var nri = ri + dr; nri >= 0 && nri < rows.length; nri += dr) {
                var ncv = _editableIn(rows[nri].querySelector(ksel));
                if (ncv) return ncv;
            }
            return null;
        }
        if (dc) {
            var tds = Array.prototype.slice.call(
                tr.querySelectorAll('.bulk-td:not(.bulk-col-hidden):not(.bulk-search-hidden)')
            ).filter(function (x) { return !_searchHiddenKeys[x.getAttribute('data-col-key')]; });
            for (var ci = tds.indexOf(td) + dc; ci >= 0 && ci < tds.length; ci += dc) {
                var nc0 = _editableIn(tds[ci]);
                if (nc0) return nc0;
            }
            return null;
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
        var tds = Array.prototype.slice.call(tr.querySelectorAll(sel))
            .filter(function (x) { return !_searchHiddenKeys[x.getAttribute('data-col-key')]; });
        for (var i = tds.indexOf(td) + dc; i >= 0 && i < tds.length; i += dc) {
            var c = _editableIn(tds[i]);
            if (c) return c;
        }
        var rows = _navRows();
        for (var ri = rows.indexOf(tr) + dc; ri >= 0 && ri < rows.length; ri += dc) {
            var ntds = Array.prototype.slice.call(rows[ri].querySelectorAll(sel))
                .filter(function (x) { return !_searchHiddenKeys[x.getAttribute('data-col-key')]; });
            for (var j = dc > 0 ? 0 : ntds.length - 1; j >= 0 && j < ntds.length; j += dc) {
                var nc = _editableIn(ntds[j]);
                if (nc) return nc;
            }
        }
        return null;
    }

    /* docs/122 item 3 — repaint the cells an undo actually moved.
       The /undo response already names every path it reverted AND the value it
       reverted to, and the grid ignored all of it: `cellsReverted` dispatched
       `quam:state-changed`, whose only listener re-GETs the whole /bulk.
       Measured on the real 20-qubit chip: the undo itself is 55 ms and that
       refetch is 2,418 ms — the same press on /explorer, which has no grid,
       settles in 56 ms and issues no /bulk at all. So the lag was never the
       undo.

       querySelectorAll, not querySelector: two columns can resolve to the SAME
       leaf (an alias pointer, a linked shared-port pair), and patching only the
       first would leave the twin showing the undone value — the exact silent
       staleness the refetch existed to prevent.

       The caller decides whether an authoritative refetch still has to follow;
       this function only reports what it could and could not reach. */
    function _revertPaths(entries) {
        var t = table();
        if (!t || !entries || !entries.length) return { patched: 0, missing: 0 };
        var sel = function (p) {
            // BOTH attributes (docs/124 C-2): /undo names RESOLVED paths while
            // a pointer-alias cell carries its alias in data-dot-path and the
            // resolved leaf in data-resolved — on the real chip that is every
            // x180/x90 amp column. Matching the alias axis only left those
            // cells permanently stale and clean-marked after Ctrl+Z, with the
            // hydrated dyn column absorbing the repaint and reporting the
            // entry covered. The apply path always matched by data-resolved
            // (_syncAppliedAcrossTable); the undo repaint now does too — and
            // the union also lands on alias TWINS (two cells, one leaf), so
            // both get value AND data-orig and the phantom-dirty twin (M-8)
            // cannot arise.
            var q = _cssEsc(p);
            return t.querySelectorAll('.bulk-cell[data-dot-path="' + q + '"]'
                + ', .bulk-cell[data-resolved="' + q + '"]'
                + ', .bulk-cell-list[data-path="' + q + '"]');   // the listedit preview span: found, never value-written
        };
        // A cold column (docs/105) has no .bulk-cell to land in — the same trap
        // _consumeEditCarry hit. Hydrate once if any named path is absent.
        var absent = entries.some(function (e) {
            return e && e.dot_path && !sel(e.dot_path).length;
        });
        _virtPatchColdValue.flushHay = false;
        // docs/141 4l-review: hydrate only the cold columns the named paths
        // live in (the byPath map _virtInit built) -- an undo of a pair-grid
        // or hidden-column path used to un-virtualize the whole grid; a path
        // in no column is `missing` by definition and costs no hydration
        if (absent && _virt) {
            var dueKeys = [];
            entries.forEach(function (e) {
                if (!e || !e.dot_path || sel(e.dot_path).length) return;
                var ck = _virt.byPath && _virt.byPath[e.dot_path];
                // a server-cold column needs no repaint: it is rendered from
                // the (already reverted) working copy when it is fetched --
                // but its SEARCH TEXT is the cold map, which nothing else
                // updates, so patch that one cell (docs/141 4ac).
                if (ck && _virt.remote.has(ck)) {
                    _virtPatchColdValue(e.dot_path,
                        e.old_value_disp != null ? e.old_value_disp : e.old_value_str);
                    return;
                }
                if (ck && dueKeys.indexOf(ck) < 0) dueKeys.push(ck);
            });
            if (dueKeys.length) _virtHydrateCols(dueKeys);
        }
        var patched = 0, missing = 0, rows = [], covered = [], uncovered = [];
        entries.forEach(function (e) {
            if (!e || !e.dot_path) return;
            var cs = sel(e.dot_path);
            if (!cs.length) { missing++; return; }
            // The grids render group_digits; the server ships that exact
            // string as old_value_disp (docs/124 M-9 — writing _fmt_val's
            // 7-sig-fig form here showed a truncated value AND made it the
            // clean baseline the next edit committed from).
            var v = e.old_value_disp != null ? String(e.old_value_disp)
                : (e.old_value_str == null ? '' : String(e.old_value_str));
            // Coverage is a PROMISE that the cell now looks exactly as a fresh
            // server render would. Three cases where a value write cannot keep
            // it (docs/124 M-10 + the readonly gap): a pointer must render as
            // a link, not a value; the docs/56 stored-as-text decorations
            // (quote spans, amber, tooltip) are server-rendered and must
            // appear/disappear with the type; and a path whose every match is
            // readOnly was not repainted at all. Uncovered ⇒ the caller's
            // debounced rebuild repaints honestly (values below still update
            // so the number on screen is right immediately).
            var wrote = 0;
            var honest = e.old_kind !== 'pointer';
            Array.prototype.forEach.call(cs, function (c) {
                // A readonly cell (a list / runtime column) and the qubit
                // grid's list-preview span are FOUND but cannot be repainted
                // by a value write: they stay uncovered (docs/124 M-10) so
                // the caller's rebuild clears the edited preview + the red
                // modified marker. Only a path with NO cell at all is
                // "missing" -- that one is not the grid's to repaint.
                if (c.readOnly || c.tagName !== 'INPUT') return;
                var isStr = c.hasAttribute('data-str-numeric')
                    || c.classList.contains('bulk-cell-str');
                if ((e.old_kind === 'str_numeric') !== isStr) honest = false;
                c.value = v;
                // The server has COMMITTED this value, so it is the new clean
                // baseline — not an edit. Setting data-orig is what keeps the
                // cell out of the dirty set and off the Apply-row button.
                c.setAttribute('data-orig', v);
                // 'input' is what recomputes the docs/109 physical-units
                // sub-line; _markCellDirty then settles the class (value ===
                // data-orig ⇒ clean).
                c.dispatchEvent(new Event('input', { bubbles: true }));
                _markCellDirty(c);
                if (c.classList.contains('bulk-cell-linked')) _mirrorLinked(c);
                var tr = _rowOf(c);
                if (tr && rows.indexOf(tr) < 0) rows.push(tr);
                patched++;
                wrote++;
            });
            if (wrote && honest) covered.push(e.dot_path);
            else uncovered.push(e.dot_path);   // found (cs.length > 0) but not honestly repainted
        });
        rows.forEach(_refreshRow);
        if (_virtPatchColdValue.flushHay) { _hayCache = null; _virtPatchColdValue.flushHay = false; }
        if (patched) _refreshGlobal();
        // `covered` (not a missing COUNT) is what the caller needs: with both
        // grids on screen a qubit leaf is legitimately absent from the pair
        // grid, so summing each surface's misses would demand a full rebuild
        // for every ordinary edit.
        return { patched: patched, missing: missing, covered: covered, uncovered: uncovered };
    }
    BulkEdit.revertPaths = _revertPaths;
    BulkEdit._virtState = function () {
        return _virt ? { cold: Array.from(_virt.cold), remote: Array.from(_virt.remote),
                         inflight: Array.from(_virt.inflight.keys()), failed: _virt.failed || 0 } : null;
    };
    // docs/141 4ae A4: Column History -- and anything else addressed from
    // outside the grid -- needs a way to ask for ONE column. Resolves even when
    // the column cannot be fetched, so the caller falls through to its honest
    // message instead of waiting forever.
    BulkEdit.hydrateColumn = function (key) {
        var gv = _virtInstance();
        if (!gv || !_virt || !gv.isCold(key)) return Promise.resolve(false);
        return gv.hydrateCols([key]).then(function () { return !gv.isCold(key); });
    };
    BulkEdit._virtHydrateCols = _virtHydrateCols;
    BulkEdit._setCarry = function (c) { _editCarry = c; };
    BulkEdit._syncApplied = _syncAppliedAcrossTable;

    // docs/111 test hooks (jsdom selfcheck drives the internals directly)
    BulkEdit._ge = {
        selCells: _selCells, fill: _fillSelection, paste: _pasteColumn,
        pinCol: _togglePinCol, pinRow: _togglePinRow, repin: _repinAfterLayout,
        captureCarry: _captureEditCarry, consumeCarry: _consumeEditCarry,
        applyRowPins: _applyRowPins, applyColPins: _applyColPins,
    };
    window.BulkEdit = BulkEdit;
    // Restore the persisted density scale onto :root at load (this script is eager
    // on every page), so the Review modal honors the user's font/bold choice even
    // if they never opened Bulk Edit this session.
    try { _applyGlobalScale(); } catch (e) {}
    if (document.getElementById('bulk-table') && !window.__bulkAutoMounted) {
        // full-page load path; the partial calls mount(columns) itself with the model
    }

    /* docs/141 4ac (CRITICAL): PaneState's keep-alive (docs/110, extended to
       /bulk by docs/139) re-attaches the parked DOM WITHOUT re-running mount(),
       so nothing re-derives the toolbar rows' translateX -- which 4q made a
       function of #table-pane.scrollLeft. Returning to Live State Edit painted
       the whole control surface (search box, Properties / Qubits / Pairs, Apply
       all, the chip bar) outside the pane until the user happened to scroll.
       PaneState now restores scrollLeft BEFORE this event, so re-deriving from
       the pane is enough; _pinBarsToScroll's own listener is bind-once
       (s._barsBound), so re-entering it cannot stack handlers. */
    document.addEventListener('paneRestored', function (ev) {
        if (!table()) return;                       // not the grid's pane
        try { _pinBarsToScroll(); } catch (e) {}
    });
})();
