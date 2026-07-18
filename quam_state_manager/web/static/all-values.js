/* All values — the completeness tab of Live State Edit.
 *
 * Renders EVERY leaf of merged state+wiring (server: GET /bulk/all-values, gzipped)
 * as a flat, default-collapsed, entity-grouped, virtual-scrolled list. Editable in
 * v2: plain scalars, list/matrix elements (dot-form numeric paths) and RESOLVABLE
 * cross-ref pointers (the server sends the resolved value; /field/edit-batch
 * resolves the write path, so the input edits THROUGH the pointer — a focus
 * fetches /field/peek once for a "writes to … · shared by …" hint). Self-refs /
 * membership arrays / identity keys / DANGLING pointers stay read-only. v2 also
 * renders container rows: arrays ([N] / [R×C]) and empty dicts/lists, each with a
 * ✎ JSON modal that commits the PARSED value (non-string values skip server-side
 * re-parse). Rows are [path, display, kind, modified, extra?]; extra carries
 * {p,d} (xref), {dims} (uniform matrix), {ty:{t,s}} (expected-type chip).
 *
 * Forked from dataset-virtual.js (the proven scroller) + pair-edit.js (path-model
 * dirty + atomic apply), HARDENED for a body that holds <input>s:
 *  - ROW_HEIGHT (28) MUST equal the CSS `.av-table-virtual tbody tr {height:28px}`.
 *  - dirty is keyed by dot_path (a Map), never the DOM — so an edit to a row that
 *    scrolls out of the window or whose group collapses SURVIVES Apply (the input
 *    node is destroyed on rebuild but repainted from the dirty entry).
 *  - FOCUS GUARD: a scroll-driven rebuild is SKIPPED while an in-window .av-input is
 *    focused (deferred to focusout), and a forced rebuild (search/expand/apply)
 *    captures+restores the caret — so a render firing mid-keystroke never drops focus
 *    or characters. datasets never had to solve this (its rows hold no inputs).
 *  - Apply chunks large batches (CHUNK) so /field/edit-batch's single-lock loop can't
 *    freeze the background drift-poll / UI thread.
 */
(function () {
    'use strict';

    var ROW_HEIGHT = 28;     // === CSS .av-table-virtual tbody tr { height:28px }
    var OVERSCAN = 8;
    var DEBOUNCE = 80;       // ms; the filter itself is <4ms at 15k
    var CHUNK = 2000;        // edits per atomic /field/edit-batch POST
    var CONFIRM_OVER = 500;  // confirm before applying more than this many at once
    var TAB_KEY = 'quam_bulk_tab';   // persisted active Live-State-Edit pane ('grid'|'allvalues')

    function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { } }

    var state = {
        rows: [],            // [path, display, kind, modified, extra?]  (+ lazy ._s haystack)
        groups: [],          // {idx,key,label,leafIdxs,editableCount,count,expanded,userExpanded,matchCount}
        rowGroup: [],        // rowIdx -> groupIdx
        rowsByPath: null,    // Map path -> rowIdx (O(dirty) apply reconcile)
        displayItems: [],    // [{type:'group'|'leaf', g, r?}]
        pass: null,          // filter pass[] (when filterActive)
        filterActive: false,
        dirty: null,         // Map path -> {value, orig}
        editingPath: null,
        summary: null,
        etag: null,
        loaded: false,
        loading: false,
        scrollEl: null,
        tbody: null,
        searchTimer: null,
        raf: false,
        lastFirst: -1,
        lastLast: -1,
        asserted: false,
        applying: false,
        xrefHints: new Map()  // path -> null (in flight) | hint text; one peek per path per model
    };

    // Read-only render metadata. v2: 'xref' here is the DANGLING branch only —
    // resolvable pointers render as edit-through inputs; 'list' elements are
    // editable; 'array'/'empty' container rows carry the ✎ JSON-modal button.
    var RO = {
        xref: { glyph: '↗', cls: 'av-xref', title: 'dangling pointer — target missing; raw text shown, read-only' },
        selfref: { glyph: '⟳', cls: 'av-selfref', title: 'config-time self-reference (resolved by generate_config) — read-only' },
        membership: { glyph: '⚠', cls: 'av-membership', title: 'chip-membership array — edit via the chip add/remove controls, not here' },
        skip: { glyph: '', cls: 'av-skip', title: 'identity / type — not editable' }
    };
    var CONTAINER = {
        array: { glyph: '▦', cls: 'av-array', title: 'array — ✎ edits the whole JSON value' },
        empty: { glyph: '∅', cls: 'av-empty', title: 'empty container — ✎ to fill it with JSON' }
    };

    // Editable-input row kinds: scalar, list element, resolvable (non-dangling) xref.
    function isEditableRow(row) {
        var k = row[2];
        return k === 'scalar' || k === 'list'
            || (k === 'xref' && !(row[4] && row[4].d));
    }

    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function attrEsc(s) { return String(s).replace(/(["\\])/g, '\\$1'); }

    // ── grouping ──────────────────────────────────────────────────────────────
    function groupKey(path) {
        var i = path.indexOf('.');
        var top = i < 0 ? path : path.slice(0, i);
        if (top === 'qubits' || top === 'qubit_pairs' || top === 'twpas') {
            var j = path.indexOf('.', i + 1);
            return j < 0 ? path : path.slice(0, j);     // qubits.qA1
        }
        if (top === 'ports' || top === 'wiring' || top === 'network') return top;
        return 'top-level';
    }
    function prettyLabel(key) {
        var d = key.indexOf('.');
        return d < 0 ? key : key.slice(0, d) + ' · ' + key.slice(d + 1);
    }

    function buildModel(rows) {
        state.rows = rows;
        state.groups = [];
        state.rowGroup = new Array(rows.length);
        state.rowsByPath = new Map();
        state.xrefHints = new Map();   // fresh model → re-peek (resolutions may have moved)
        var gmap = {};
        for (var r = 0; r < rows.length; r++) {
            var path = rows[r][0];
            state.rowsByPath.set(path, r);
            var k = groupKey(path);
            var g = gmap[k];
            if (!g) {
                g = { idx: state.groups.length, key: k, label: prettyLabel(k), leafIdxs: [],
                      editableCount: 0, expanded: false, userExpanded: false, matchCount: 0 };
                gmap[k] = g;
                state.groups.push(g);
            }
            g.leafIdxs.push(r);
            if (isEditableRow(rows[r])) g.editableCount++;
            state.rowGroup[r] = g.idx;
        }
    }

    function rebuildDisplay() {
        var items = [];
        for (var gi = 0; gi < state.groups.length; gi++) {
            var g = state.groups[gi];
            if (state.filterActive && g.matchCount === 0) continue;
            items.push({ type: 'group', g: gi });
            if (g.expanded) {
                var L = g.leafIdxs;
                for (var j = 0; j < L.length; j++) {
                    var r = L[j];
                    if (!state.filterActive || state.pass[r]) items.push({ type: 'leaf', g: gi, r: r });
                }
            }
        }
        state.displayItems = items;
    }

    // ── row html ──────────────────────────────────────────────────────────────
    function groupRowHtml(gi) {
        var g = state.groups[gi];
        var caret = g.expanded ? '▾' : '▸';
        var badge = state.filterActive
            ? (g.matchCount + ' / ' + g.leafIdxs.length)
            : (g.editableCount + ' editable · ' + g.leafIdxs.length);
        return '<tr class="av-group-row" data-g="' + gi + '"><td>'
            + '<span class="av-gutter av-caret">' + caret + '</span>'
            + '<span class="av-cell-path av-group-label">' + esc(g.label) + '</span>'
            + '<span class="av-cell-val muted">' + badge + '</span>'
            + '</td></tr>';
    }

    function deepLink(path) {
        var seg = path.split('.');
        if (seg[0] === 'qubits' && seg[1]) return { url: '/qubit/' + seg[1], name: seg[1] };
        if (seg[0] === 'qubit_pairs' && seg[1]) return { url: '/pair/' + seg[1], name: seg[1] };
        return null;
    }

    // Expected-type chip (extra.ty from the server's type policy) — inline inside
    // the 18px value band (never a 4th grid child: the td grid is 3 columns and an
    // extra child would wrap to a new grid ROW and break the 28px height).
    function tyChipHtml(extra) {
        if (!extra || !extra.ty) return '';
        var t = extra.ty.t || '', s = extra.ty.s || '';
        return '<span class="av-ty-chip" title="expected type: ' + esc(t)
            + ' · source: ' + esc(s) + '">'
            + esc(t) + '·' + esc(s === 'inferred' ? 'inf' : s) + '</span>';
    }

    function deepLinkHtml(path) {
        var dl = deepLink(path);
        if (!dl) return '';
        return ' <a class="av-link" href="#" data-av-link="' + esc(dl.url)
            + '" title="Open ' + esc(dl.name) + ' inspector">↗</a>';
    }

    function leafRowHtml(r) {
        var row = state.rows[r];
        var path = row[0], disp = row[1], kind = row[2], mod = row[3], extra = row[4];
        var pe = esc(path);
        if (isEditableRow(row)) {
            var d = state.dirty.get(path);
            var val = d ? d.value : disp;
            var cls = 'av-leaf av-' + kind + (d ? ' av-row-dirty' : '') + (mod ? ' av-row-mod' : '');
            var glyph = '', gtitle = mod ? 'edited — not yet applied to live' : '';
            if (!mod && kind === 'xref') { glyph = '↗'; gtitle = 'pointer — edits write through to the target (focus for details)'; }
            else if (!mod && kind === 'list') { glyph = '▦'; gtitle = 'list / matrix element'; }
            // input + link + chip live inside ONE wrap span (3rd grid child stays single)
            return '<tr class="' + cls + '"><td>'
                + '<span class="av-gutter' + (kind === 'xref' ? ' av-xref-g' : '') + '"'
                + (gtitle ? ' title="' + esc(gtitle) + '"' : '') + '>' + (mod ? '•' : glyph) + '</span>'
                + '<span class="av-cell-path" title="' + pe + '">' + pe + '</span>'
                + '<span class="av-val-wrap">'
                + '<input class="av-input" type="text" spellcheck="false" autocomplete="off"'
                + ' data-dot-path="' + pe + '" value="' + esc(val) + '">'
                + (kind === 'xref' || kind === 'list' ? deepLinkHtml(path) : '')
                + tyChipHtml(extra)
                + '</span></td></tr>';
        }
        if (kind === 'array' || kind === 'empty') {
            var cmeta = CONTAINER[kind];
            var ctitle = cmeta.title + (extra && extra.dims ? ' (' + extra.dims + ' matrix)' : '');
            return '<tr class="av-leaf ' + cmeta.cls + (mod ? ' av-row-mod' : '') + '"><td>'
                + '<span class="av-gutter ' + cmeta.cls + '-g" title="' + esc(ctitle) + '">' + cmeta.glyph + '</span>'
                + '<span class="av-cell-path" title="' + pe + '">' + pe + '</span>'
                + '<span class="av-cell-val" title="' + esc(disp) + '">' + esc(disp)
                + ' <button type="button" class="av-edit-btn" data-av-edit="' + pe
                + '" title="Edit as JSON (Ctrl+Enter saves, Esc cancels)">✎</button></span>'
                + '</td></tr>';
        }
        var meta = RO[kind] || RO.skip;
        var link = (kind === 'xref') ? deepLinkHtml(path) : '';
        return '<tr class="av-leaf ' + meta.cls + '"><td>'
            + '<span class="av-gutter ' + meta.cls + '-g" title="' + esc(meta.title) + '">' + meta.glyph + '</span>'
            + '<span class="av-cell-path" title="' + pe + '">' + pe + '</span>'
            + '<span class="av-cell-val" title="' + esc(disp) + '">' + esc(disp) + link + tyChipHtml(extra) + '</span>'
            + '</td></tr>';
    }

    // ── virtual window ──────────────────────────────────────────────────────────
    function scheduleRender() {
        if (state.raf) return;
        state.raf = true;
        requestAnimationFrame(function () { state.raf = false; renderWindow(false); });
    }

    function renderWindow(force) {
        if (!state.scrollEl || !state.tbody) return;
        var total = state.displayItems.length;
        var scrollTop = state.scrollEl.scrollTop;
        var viewport = state.scrollEl.clientHeight;
        var first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
        var last = Math.min(total, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);
        if (!force && first === state.lastFirst && last === state.lastLast) return;

        // FOCUS GUARD (must-fix #1): a scroll-driven (non-forced) rebuild while an
        // .av-input is focused would replace the live node -> lost focus/caret/IME.
        // Defer a SMALL focused scroll (typing jitter) — keep the live input node.
        // But a fling / scrollbar-drag that moved the window more than a viewport would
        // strand it blank/stale until focusout, so fall through to the caret-save/full-
        // repaint/caret-restore path below instead of deferring (audit P1).
        if (!force && state.editingPath) {
            var ae = document.activeElement;
            if (ae && ae.classList && ae.classList.contains('av-input')
                && ae.getAttribute('data-dot-path') === state.editingPath
                && Math.abs(first - state.lastFirst) * ROW_HEIGHT <= viewport) return;
        }
        // Belt-and-braces for a FORCED rebuild while editing (search/expand/apply):
        // capture the caret, restore it if the same input re-renders in the new window.
        var savePath = null, selS = 0, selE = 0;
        var cur = document.activeElement;
        if (cur && cur.classList && cur.classList.contains('av-input')) {
            savePath = cur.getAttribute('data-dot-path');
            try { selS = cur.selectionStart; selE = cur.selectionEnd; } catch (e) { }
        }

        hideXrefHint();               // fixed-position hint would drift on any repaint
        state.lastFirst = first; state.lastLast = last;
        var topPad = first * ROW_HEIGHT;
        var bottomPad = Math.max(0, (total - last)) * ROW_HEIGHT;
        var html = '<tr class="av-spacer" style="height:' + topPad + 'px"><td></td></tr>';
        for (var i = first; i < last; i++) {
            var it = state.displayItems[i];
            html += it.type === 'group' ? groupRowHtml(it.g) : leafRowHtml(it.r);
        }
        html += '<tr class="av-spacer" style="height:' + bottomPad + 'px"><td></td></tr>';
        state.tbody.innerHTML = html;

        if (savePath) {
            var ni = state.tbody.querySelector('.av-input[data-dot-path="' + attrEsc(savePath) + '"]');
            if (ni) { ni.focus(); try { ni.setSelectionRange(selS, selE); } catch (e2) { } }
        }
        devAssertRowHeight();
    }

    function devAssertRowHeight() {
        if (state.asserted) return;
        state.asserted = true;
        var tr = state.tbody.querySelector('tr:not(.av-spacer)');
        if (tr && tr.offsetHeight !== ROW_HEIGHT) {
            // eslint-disable-next-line no-console
            console.error('[all-values] ROW_HEIGHT drift: tr is ' + tr.offsetHeight
                + 'px but ROW_HEIGHT=' + ROW_HEIGHT + ' — scroll will desync (datasets bug-class).');
        }
    }

    // ── events ──────────────────────────────────────────────────────────────────
    function toggleGroup(gi) {
        var g = state.groups[gi];
        g.expanded = !g.expanded;
        g.userExpanded = g.expanded;          // honored on search-clear (no stale snapshot)
        rebuildDisplay();
        state.lastFirst = -1; renderWindow(true);
    }

    function onTbodyClick(e) {
        var link = e.target.closest ? e.target.closest('[data-av-link]') : null;
        if (link) {
            e.preventDefault();
            var url = link.getAttribute('data-av-link');
            if (window.htmx) window.htmx.ajax('GET', url, { source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML' });
            return;
        }
        var edit = e.target.closest ? e.target.closest('[data-av-edit]') : null;
        if (edit) { openJsonModal(edit.getAttribute('data-av-edit')); return; }
        var grow = e.target.closest ? e.target.closest('.av-group-row') : null;
        if (grow) { toggleGroup(parseInt(grow.getAttribute('data-g'), 10)); }
    }

    // ── xref edit-through hint (fixed-position overlay — NEVER a table row, so
    //    the 28px virtual-scroll index math is untouched) ────────────────────────
    function hideXrefHint() {
        var el = document.getElementById('av-xref-hint');
        if (el && el.parentNode) el.parentNode.removeChild(el);
    }
    function paintXrefHint(inputEl, text) {
        if (!text || !inputEl || !inputEl.isConnected) return;
        hideXrefHint();
        var el = document.createElement('div');
        el.id = 'av-xref-hint';
        el.textContent = text;
        var rect = inputEl.getBoundingClientRect();
        el.style.left = rect.left + 'px';
        el.style.top = (rect.bottom + 2) + 'px';
        document.body.appendChild(el);
    }
    // One /field/peek per path per model: the resolved block tells the user WHERE
    // the edit lands ("writes to <resolved_path>") and which siblings alias it.
    function showXrefHint(inputEl, path) {
        var cached = state.xrefHints.get(path);
        if (cached === null) return;                    // fetch already in flight
        if (cached !== undefined) { paintXrefHint(inputEl, cached); return; }
        state.xrefHints.set(path, null);
        fetch('/field/peek?dot_path=' + encodeURIComponent(path))
            .then(function (r) { return r.json(); })
            .then(function (jb) {
                var ft = jb && jb.resolved ? jb.resolved[path] : null;
                var text = '';
                if (ft && ft.resolved_path && ft.resolved_path !== path) {
                    text = 'writes to ' + ft.resolved_path;
                    if (ft.shared_by && ft.shared_by.length) text += ' · shared by ' + ft.shared_by.join(', ');
                }
                state.xrefHints.set(path, text);
                if (state.editingPath === path) paintXrefHint(inputEl, text);
            })
            .catch(function () { state.xrefHints.delete(path); });   // transient → retry next focus
    }

    // ── ✎ whole-value JSON modal (array + empty-container rows) ────────────────
    function closeJsonModal() {
        var ov = document.getElementById('av-json-modal');
        if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
    }
    function openJsonModal(path) {
        closeJsonModal();
        var ov = document.createElement('div');
        ov.id = 'av-json-modal';
        ov.innerHTML = '<div class="av-modal-card" role="dialog" aria-modal="true" aria-label="Edit JSON value">'
            + '<div class="av-modal-head"><span class="av-modal-path" title="' + esc(path) + '">' + esc(path) + '</span>'
            + '<span class="muted av-modal-keys">Ctrl+Enter save · Esc cancel</span></div>'
            + '<textarea class="av-modal-ta" spellcheck="false" aria-label="JSON value"></textarea>'
            + '<div class="av-modal-err" hidden></div>'
            + '<div class="av-modal-actions">'
            + '<button type="button" class="btn-sm" data-av-save>Save</button>'
            + '<button type="button" class="btn-sm outline" data-av-cancel>Cancel</button>'
            + '</div></div>';
        document.body.appendChild(ov);
        var ta = ov.querySelector('.av-modal-ta');
        function showErr(msg) {
            var e2 = ov.querySelector('.av-modal-err');
            e2.textContent = msg; e2.hidden = false;
        }
        // Prefill from the RAW value (peek `values`) — the row display ([2×2]) is
        // a summary, not the data.
        fetch('/field/peek?dot_path=' + encodeURIComponent(path))
            .then(function (r) { return r.json(); })
            .then(function (jb) {
                var v = jb && jb.values ? jb.values[path] : undefined;
                ta.value = JSON.stringify(v === undefined ? null : v, null, 2);
                ta.focus();
            })
            .catch(function (err) { showErr('Could not load current value: ' + err); ta.focus(); });
        function save() {
            var parsed;
            try { parsed = JSON.parse(ta.value); }
            catch (ex) { showErr('Invalid JSON: ' + ex.message); return; }
            // PARSED value in the JSON body — edit-batch skips re-parse for
            // non-string values, so the container commits typed-correctly.
            fetch('/field/edit-batch', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ updates: [{ dot_path: path, value: parsed }], expect_chip: window.__chipToken || '' })
            }).then(function (r) { return r.json(); }).then(function (jb) {
                if (!jb || !jb.ok) {
                    showErr((jb && jb.results && jb.results[0] && jb.results[0].error)
                        || (jb && jb.error) || 'Apply failed');
                    return;
                }
                containerSaved(path, parsed, jb);
                closeJsonModal();
            }).catch(function (ex) { showErr('Apply failed: ' + ex); });
        }
        ov.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeJsonModal(); }
            else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
        });
        ov.addEventListener('mousedown', function (e) { if (e.target === ov) closeJsonModal(); });
        ov.querySelector('[data-av-save]').addEventListener('click', save);
        ov.querySelector('[data-av-cancel]').addEventListener('click', closeJsonModal);
    }
    // Local display mirror of core/all_values container formatting: enough to
    // repaint THIS row honestly until the next re-pull (state.etag is dropped —
    // a container edit can add/remove leaf rows, only the server can re-derive).
    function containerDisplay(v) {
        if (Array.isArray(v)) {
            if (!v.length) return { disp: '[] empty', kind: 'empty', dims: null };
            var cols = null, uniform = true;
            for (var i = 0; i < v.length; i++) {
                if (!Array.isArray(v[i])) { uniform = false; break; }
                if (cols === null) cols = v[i].length;
                else if (v[i].length !== cols) { uniform = false; break; }
            }
            if (uniform && cols !== null) {
                var dims = v.length + '×' + cols;
                return { disp: '[' + dims + ']', kind: 'array', dims: dims };
            }
            return { disp: '[' + v.length + ']', kind: 'array', dims: null };
        }
        if (v && typeof v === 'object') {
            var n = Object.keys(v).length;
            return n ? { disp: '{…} ' + n + ' key' + (n === 1 ? '' : 's'), kind: 'empty', dims: null }
                : { disp: '{} empty', kind: 'empty', dims: null };
        }
        return { disp: String(v === null ? '' : v), kind: 'empty', dims: null };
    }
    function containerSaved(path, parsed, jb) {
        var r = state.rowsByPath.get(path);
        if (r != null) {
            var row = state.rows[r];
            var cd = containerDisplay(parsed);
            row[1] = cd.disp; row[2] = cd.kind; row[3] = 1; row._s = null;
            if (cd.dims) row[4] = { dims: cd.dims };
            else if (row[4] && row[4].dims) row.length = 4;   // extra held only dims
        }
        state.etag = null;   // structure may have changed → next activation re-pulls rows
        if (jb.tray_html && window._swapPendingTray) {
            window._bulkSelfEdit = true; window._swapPendingTray(jb.tray_html); window._bulkSelfEdit = false;
        }
        if (window._diagChanged) window._diagChanged();
        state.lastFirst = -1; renderWindow(true);
        toast('Applied to the working state — review in the tray, then apply to live.');
    }

    function onTbodyInput(e) {
        var t = e.target;
        if (!t.classList || !t.classList.contains('av-input')) return;
        var path = t.getAttribute('data-dot-path');
        var r = state.rowsByPath.get(path);
        var orig = state.rows[r][1];
        var v = t.value;
        var tr = t.closest('tr');
        if (v === orig) { state.dirty.delete(path); if (tr) tr.classList.remove('av-row-dirty'); }
        else { state.dirty.set(path, { value: v, orig: orig }); if (tr) tr.classList.add('av-row-dirty'); }
        updateDirtyUI();
    }

    function onTbodyFocusIn(e) {
        if (e.target.classList && e.target.classList.contains('av-input')) {
            var p = e.target.getAttribute('data-dot-path');
            state.editingPath = p;
            var r = state.rowsByPath.get(p);
            if (r != null && state.rows[r][2] === 'xref') showXrefHint(e.target, p);
        }
    }
    function onTbodyFocusOut(e) {
        if (e.target.classList && e.target.classList.contains('av-input')) {
            state.editingPath = null;
            hideXrefHint();
            // Tab / click-away COMMITS (like Enter). applyOne no-ops when the path
            // isn't dirty (unchanged value) or while an apply is already in flight.
            // BUT a click on the Apply-all / Apply-to-live / Reset toolbar buttons must
            // NOT commit here: the commit sets state.applying, which makes those buttons'
            // own handlers no-op (Apply looks dead), and for Reset it turns discard into
            // a commit. A pointerdown on those buttons fires before this blur; honour it
            // (relatedTarget is null in some engines) so the toolbar handler runs instead.
            var to = e.relatedTarget;
            var onToolbar = (state.toolbarPressTs && (Date.now() - state.toolbarPressTs) < 1000)
                || (to && to.closest && to.closest('#av-apply, #av-apply-sync, #av-reset'));
            var path = e.target.getAttribute('data-dot-path');
            if (!onToolbar && path && state.dirty.has(path)) applyOne(path, e.target);
            scheduleRender();                 // catch up the window deferred during the edit
        }
    }

    // ── search ────────────────────────────────────────────────────────────────
    function haystack(r) {
        var row = state.rows[r];
        if (row._s) return row._s;
        row._s = (row[0] + ' ' + row[1]).toLowerCase();
        return row._s;
    }
    function parseTokens(q) {
        var raw = q.split(/[\s,]+/).filter(Boolean);
        return raw.map(function (tok) {
            var c = tok.indexOf(':');
            if (c > 0) {
                var k = tok.slice(0, c), v = tok.slice(c + 1);
                if (k === 'path' || k === 'kind' || k === 'is') return { k: k, v: v };
            }
            return { k: 'bare', v: tok };
        });
    }
    function matchToken(r, tk) {
        var row = state.rows[r];
        if (tk.k === 'path') return row[0].toLowerCase().indexOf(tk.v) >= 0;
        if (tk.k === 'kind') return row[2] === tk.v;
        if (tk.k === 'is') {
            if (tk.v === 'modified') return row[3] === 1 || state.dirty.has(row[0]);
            if (tk.v === 'editable') return isEditableRow(row);
            return true;
        }
        return haystack(r).indexOf(tk.v) >= 0;
    }
    function onSearchInput() {
        clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(applyFilter, DEBOUNCE);
    }
    // Keep the kind-chip highlight in sync with the live search box (audit P2): typing a
    // free query de-highlights a stale chip; clearing re-activates the 'All' chip.
    function syncChips(raw) {
        var chips = document.getElementById('av-chips');
        if (!chips) return;
        var all = chips.querySelectorAll('.av-chip');
        for (var i = 0; i < all.length; i++)
            all[i].classList.toggle('active', (all[i].getAttribute('data-kind') || '') === raw);
    }
    function applyFilter() {
        var box = document.getElementById('av-search');
        var raw = (box && box.value || '').trim();
        syncChips(raw);
        var q = raw.toLowerCase();
        if (!q) { clearFilter(); return; }
        var tokens = parseTokens(q);
        state.filterActive = true;
        state.pass = new Array(state.rows.length);
        for (var gi = 0; gi < state.groups.length; gi++) state.groups[gi].matchCount = 0;
        var shown = 0;
        for (var r = 0; r < state.rows.length; r++) {
            var ok = true;
            for (var ti = 0; ti < tokens.length; ti++) { if (!matchToken(r, tokens[ti])) { ok = false; break; } }
            state.pass[r] = ok;
            if (ok) { state.groups[state.rowGroup[r]].matchCount++; shown++; }
        }
        for (var gj = 0; gj < state.groups.length; gj++)
            if (state.groups[gj].matchCount > 0) state.groups[gj].expanded = true;   // auto-open matches
        rebuildDisplay();
        state.lastFirst = -1; renderWindow(true);
        setShowing(shown);
    }
    function clearFilter() {
        syncChips('');                      // re-activate the "All" chip
        state.filterActive = false; state.pass = null;
        for (var gi = 0; gi < state.groups.length; gi++)
            state.groups[gi].expanded = state.groups[gi].userExpanded;   // restore manual state
        rebuildDisplay();
        state.lastFirst = -1; renderWindow(true);
        setShowing(null);
    }
    function setShowing(n) {
        var el = document.getElementById('av-showing');
        if (!el) return;
        el.textContent = (n === null) ? '' : ('Showing ' + n.toLocaleString() + ' of ' + state.rows.length.toLocaleString());
    }

    function expandAll(v) {
        for (var gi = 0; gi < state.groups.length; gi++) { state.groups[gi].expanded = v; state.groups[gi].userExpanded = v; }
        rebuildDisplay(); state.lastFirst = -1; renderWindow(true);
    }

    // ── apply ───────────────────────────────────────────────────────────────────
    function updateDirtyUI() {
        var n = state.dirty.size;
        var dc = document.getElementById('av-dirty-count');
        var ap = document.getElementById('av-apply');
        var aps = document.getElementById('av-apply-sync');
        var rs = document.getElementById('av-reset');
        if (dc) dc.textContent = n ? (n.toLocaleString() + ' un-applied') : '';
        if (ap) ap.disabled = (n === 0 || state.applying);
        if (aps) aps.disabled = (n === 0 || state.applying);
        if (rs) rs.disabled = (n === 0 || state.applying);
    }
    function setApplying(v) {
        state.applying = v;
        var ap = document.getElementById('av-apply');
        if (ap) { ap.disabled = v || state.dirty.size === 0; ap.textContent = v ? 'Applying…' : 'Apply all'; }
        var aps = document.getElementById('av-apply-sync');
        if (aps) aps.disabled = v || state.dirty.size === 0;
    }
    // syncAfter (the ⚡ "Apply to live now" button): commit to the working state,
    // then pull+re-apply+push to the live chip in one shot via doStateSync('apply').
    function applyAll(syncAfter) {
        if (state.dirty.size === 0 || state.applying) return;
        var updates = [];
        state.dirty.forEach(function (d, path) { updates.push({ dot_path: path, value: d.value }); });
        // Always confirm the ⚡ live-push (matches the qubit/pair grids, which always
        // confirm); for the plain working-state apply, only confirm past the big-batch gate.
        if ((syncAfter || updates.length > CONFIRM_OVER)
            && !window.confirm('Apply ' + updates.length +
                (syncAfter ? ' edits and push to the live chip?' : ' edits to the working state?'))) return;
        state._syncAfter = !!syncAfter;
        setApplying(true);
        applyChunks(updates, 0);
    }
    function applyChunks(updates, start) {
        if (start >= updates.length) { setApplying(false); afterApply(); return; }
        var chunk = updates.slice(start, start + CHUNK);
        fetch('/field/edit-batch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates: chunk, expect_chip: window.__chipToken || '' })
        }).then(function (r) { return r.json(); }).then(function (jb) {
            if (!jb.ok) { setApplying(false); applyError(jb, start); return; }   // start = edits already committed
            reconcile(jb.results);
            if (jb.tray_html && window._swapPendingTray) {
                window._bulkSelfEdit = true; window._swapPendingTray(jb.tray_html); window._bulkSelfEdit = false;
            }
            applyChunks(updates, start + CHUNK);
        }).catch(function (err) {
            state._syncAfter = false;   // network error mid-chunk → never auto-push (parity with applyError)
            setApplying(false); toast('Apply failed: ' + err); updateDirtyUI();
        });
    }
    function reconcile(results) {
        for (var i = 0; i < results.length; i++) {
            var res = results[i];
            var r = state.rowsByPath.get(res.dot_path);
            if (r == null) continue;
            if (res.display !== undefined && res.display !== null) state.rows[r][1] = res.display;
            state.rows[r][3] = 1;
            state.rows[r]._s = null;            // refresh lazy haystack
            state.dirty.delete(res.dot_path);
        }
    }
    function afterApply() {
        // The chunked Apply committed edits to the working copy → re-run the safety
        // linter (debounced; unconditional, independent of any tray swap).
        if (window._diagChanged) window._diagChanged();
        updateDirtyUI();
        state.etag = null;                       // next tab open re-pulls fresh modified flags
        state.lastFirst = -1; renderWindow(true);
        if (state._syncAfter) {
            state._syncAfter = false;
            // ⚡ one-click: edits are in the working state — push them to the live chip.
            // applyEditsToLive routes safely (pending-only → merge; saved-but-unapplied
            // → steer to the tray) and toasts the result.
            if (window.applyEditsToLive) window.applyEditsToLive();
        } else {
            toast('Applied to the working state — review in the tray, then apply to live.');
        }
    }
    function applyError(jb, committed) {
        state._syncAfter = false;                // never push a half-applied set to live
        var bad = null;
        if (jb.results) for (var i = 0; i < jb.results.length; i++) if (!jb.results[i].applied) { bad = jb.results[i]; break; }
        updateDirtyUI();
        var at = bad ? (' at ' + bad.dot_path + ': ' + (bad.error || 'invalid')) : '';
        // committed>0 only on a multi-chunk Apply-all where a LATER chunk failed — the
        // earlier chunks ARE in the (safe) working copy, so don't claim a full rollback.
        if (committed > 0)
            toast('Partially applied — ' + committed + ' committed to the working state; this batch rolled back'
                + at + '. Remaining edits kept for retry.');
        else
            toast('Apply rolled back' + (bad ? (' — ' + bad.dot_path + ': ' + (bad.error || 'invalid')) : '')
                + '. Edits kept for retry.');
    }
    function resetDirty() {
        if (state.applying) return;
        state.dirty.clear();
        updateDirtyUI();
        state.lastFirst = -1; renderWindow(true);
    }
    // A5: Enter in a scalar input applies THAT one field to the working copy (matches
    // Live Grid's Enter-applies-row). Same /field/edit-batch + reconcile + tray-swap
    // path as Apply-all, isolated to one dot-path; no full rebuild so focus survives.
    function applyOne(path, inputEl) {
        var d = state.dirty.get(path);
        if (!d || state.applying) return;
        state.applying = true;   // in-flight guard: an Enter + quick blur can fire two
        fetch('/field/edit-batch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates: [{ dot_path: path, value: d.value }], expect_chip: window.__chipToken || '' })
        }).then(function (r) { return r.json(); }).then(function (jb) {
            if (!jb.ok) { applyError(jb); return; }
            reconcile(jb.results);
            if (jb.tray_html && window._swapPendingTray) {
                window._bulkSelfEdit = true; window._swapPendingTray(jb.tray_html); window._bulkSelfEdit = false;
            }
            if (window._diagChanged) window._diagChanged();
            updateDirtyUI();
            var row = inputEl && inputEl.closest ? inputEl.closest('tr') : null;
            if (row) row.classList.remove('av-row-dirty');   // applied → no longer pending in-session
            toast('Applied to the working state — review in the tray, then apply to live.');
        }).catch(function (err) { toast('Apply failed: ' + err); })
          .finally(function () { state.applying = false; });
    }
    function onTbodyKeydown(e) {
        if (e.key !== 'Enter') return;
        var t = e.target;
        if (!t.classList || !t.classList.contains('av-input')) return;
        e.preventDefault();
        applyOne(t.getAttribute('data-dot-path'), t);
    }
    function toast(msg) {
        if (window.showToast) window.showToast(msg);
        else if (window.showMessage) window.showMessage(msg, 'info');
    }

    // ── load ────────────────────────────────────────────────────────────────────
    function setCoverage() {
        var el = document.getElementById('av-coverage');
        if (!el || !state.summary) return;
        var s = state.summary;
        el.textContent = s.total.toLocaleString() + ' leaves · '
            + s.editable.toLocaleString() + ' editable · ' + s.readonly.toLocaleString() + ' read-only'
            + (s.arrays ? ' · ' + s.arrays.toLocaleString() + ' arrays' : '')
            + (s.empties ? ' · ' + s.empties.toLocaleString() + ' empty' : '');
    }
    function applyPayload(data, keepDirty) {
        // keepDirty (audit P1): a re-pull while the user holds unapplied edits must NOT
        // discard them. Rebuild the model from the fresh payload, then re-overlay only
        // surviving dirty paths — drop an edit whose leaf vanished, auto-clear one the
        // server now already equals, and rebase the rest onto the fresh server value so
        // non-dirty rows show the pulled value while edited rows keep the typed value.
        var oldDirty = keepDirty ? state.dirty : null;
        buildModel(data.rows);
        state.summary = data.summary;
        var nextDirty = new Map();
        if (oldDirty) {
            oldDirty.forEach(function (d, path) {
                var r = state.rowsByPath.get(path);
                if (r == null) return;                 // leaf gone → drop the edit
                var fresh = state.rows[r][1];
                if (d.value === fresh) return;          // server now equals the edit → auto-clear
                nextDirty.set(path, { value: d.value, orig: fresh });   // keep edit; rebase baseline
            });
        }
        state.dirty = nextDirty;
        state.filterActive = false; state.pass = null;
        rebuildDisplay();
        state.lastFirst = -1;
        setCoverage(); setShowing(null); updateDirtyUI();
        renderWindow(true);
    }
    function load(cb, keepDirty) {
        if (state.loading) return;
        state.loading = true;
        var headers = state.etag ? { 'If-None-Match': state.etag } : {};
        fetch('/bulk/all-values', { headers: headers }).then(function (r) {
            if (r.status === 304) { state.loading = false; if (cb) cb(); return null; }
            state.etag = r.headers.get('ETag');
            return r.json();
        }).then(function (data) {
            state.loading = false;
            if (!data) return;
            applyPayload(data, keepDirty);
            state.loaded = true;
            if (cb) cb();
        }).catch(function (err) {
            state.loading = false;
            if (state.tbody) state.tbody.innerHTML =
                '<tr><td class="av-loading">Could not load values: ' + esc(String(err)) + '</td></tr>';
        });
    }

    // Wire the All-values DOM. Re-runnable: when /bulk is re-rendered into
    // #table-pane (a full HTMX swap), the old #av-tbody/#av-scroll/#av-search/etc.
    // nodes are DESTROYED and fresh ones parsed — but this module's `state` survives
    // on window, so state.tbody/scrollEl would still point at the detached old nodes
    // and renderWindow() would paint into garbage (the visible tbody stays stuck on
    // "Loading values…"). So we key off the LIVE element identity: if the current
    // #av-tbody is not the node we last bound, re-grab every ref and re-bind every
    // listener onto the fresh nodes. The old nodes are gone, so there's no duplicate
    // listener to worry about; we only ever hold listeners on the live DOM.
    // A1: All-values-only adjustable font scale + letter-spacing + bold, persisted in
    // its OWN localStorage keys (parallel to Live Grid's, so the two tables stay
    // independent and bulk-edit.js is byte-untouched). Sets CSS vars on .av-scroll; the
    // CSS scales font-size ONLY — never the 28px row / 18px line-height — so ROW_HEIGHT=28
    // and the virtual-scroll index math are untouched. Re-applied on every wireDom so the
    // choice survives each HTMX re-render.
    function applyFont() {
        var sc = state.scrollEl;
        if (!sc) return;
        var fs = parseFloat(lsGet('quam_av_fs')); if (!fs || isNaN(fs)) fs = 1;
        if (fs < 0.85) fs = 0.85; else if (fs > 1.3) fs = 1.3;   // clamp to the slider domain so a
        var ls = parseFloat(lsGet('quam_av_ls')); if (isNaN(ls)) ls = 0;   // corrupt quam_av_fs can't drift the 28px row
        if (ls < 0) ls = 0; else if (ls > 0.12) ls = 0.12;
        var bold = lsGet('quam_av_bold') === '1';
        sc.style.setProperty('--av-fs', String(fs));
        sc.style.setProperty('--av-ls', ls + 'em');
        sc.classList.toggle('av-bold', bold);
        var fsS = document.getElementById('av-font-slider'); if (fsS) fsS.value = fs;
        var lsS = document.getElementById('av-ls-slider'); if (lsS) lsS.value = ls;
        var presets = document.querySelectorAll('.av-font-preset');
        for (var i = 0; i < presets.length; i++)
            presets[i].classList.toggle('active', parseFloat(presets[i].getAttribute('data-fs')) === fs);
        var b = document.getElementById('av-bold');
        if (b) { b.classList.toggle('active', bold); b.setAttribute('aria-pressed', bold ? 'true' : 'false'); }
    }

    function wireDom() {
        var tbody = document.getElementById('av-tbody');
        var scrollEl = document.getElementById('av-scroll');
        if (!tbody || !scrollEl) return false;
        if (state._wiredEl === tbody) return true;   // already bound to THIS live tbody

        // Fresh (or first) DOM: re-grab refs + re-bind. Re-arm the virtual window so
        // the next render is forced (lastFirst/Last are about the OLD detached node).
        state.tbody = tbody;
        state.scrollEl = scrollEl;
        state.lastFirst = -1; state.lastLast = -1;
        state.asserted = false;                      // re-assert ROW_HEIGHT on the new DOM
        applyFont();                                 // re-apply the persisted font/spacing to this fresh scroller

        // hideXrefHint on the raw scroll event too: renderWindow early-returns on a
        // same-window scroll, but the fixed-position hint has already drifted.
        scrollEl.addEventListener('scroll', function () { hideXrefHint(); scheduleRender(); }, { passive: true });
        tbody.addEventListener('click', onTbodyClick);
        tbody.addEventListener('input', onTbodyInput);
        tbody.addEventListener('focusin', onTbodyFocusIn);
        tbody.addEventListener('focusout', onTbodyFocusOut);
        tbody.addEventListener('keydown', onTbodyKeydown);   // A5: Enter applies that field
        var search = document.getElementById('av-search');
        if (search) search.addEventListener('input', onSearchInput);
        var ea = document.getElementById('av-expand-all');
        if (ea) ea.addEventListener('click', function () { expandAll(true); });
        var ca = document.getElementById('av-collapse-all');
        if (ca) ca.addEventListener('click', function () { expandAll(false); });
        // Stamp a toolbar-press timestamp on pointerdown (fires before the focused
        // input's blur) so onTbodyFocusOut skips its click-away commit and lets these
        // handlers act on the full dirty set (Apply) or discard it (Reset).
        function _stampToolbar() { state.toolbarPressTs = Date.now(); }
        var ap = document.getElementById('av-apply');
        if (ap) { ap.addEventListener('pointerdown', _stampToolbar); ap.addEventListener('click', function () { applyAll(); }); }   // no event arg → syncAfter stays false
        var aps = document.getElementById('av-apply-sync');
        if (aps) { aps.addEventListener('pointerdown', _stampToolbar); aps.addEventListener('click', function () { applyAll(true); }); }
        var rs = document.getElementById('av-reset');
        if (rs) { rs.addEventListener('pointerdown', _stampToolbar); rs.addEventListener('click', resetDirty); }
        var chips = document.getElementById('av-chips');
        if (chips) chips.addEventListener('click', function (e) {
            var b = e.target.closest ? e.target.closest('.av-chip') : null;
            if (!b) return;
            var s = document.getElementById('av-search');
            if (s) { s.value = b.getAttribute('data-kind') || ''; applyFilter(); }
            var all = chips.querySelectorAll('.av-chip');
            for (var i = 0; i < all.length; i++) all[i].classList.toggle('active', all[i] === b);
        });

        // window 'resize' is bound to the long-lived window, not the swapped DOM, so
        // bind it exactly once (it reads state.scrollEl/tbody live each fire).
        if (!state._resizeBound) {
            window.addEventListener('resize', function () { state.lastFirst = -1; scheduleRender(); });
            state._resizeBound = true;
        }
        state._wiredEl = tbody;
        state._wired = true;
        return true;
    }

    // First activation of the All-values tab: wire + load. Re-activation: re-grab the
    // (possibly fresh-after-swap) DOM, then re-paint the already-loaded model into it;
    // refresh cheaply (conditional GET) only when there are no un-applied edits to
    // clobber. After an HTMX swap the model is intact but the new tbody is empty, so we
    // must always force a render — load()'s 304 path also calls back to scheduleRender.
    function activate() {
        if (!wireDom()) return;                       // DOM not present yet
        if (!state.loaded) { load(); return; }
        renderWindow(true);                           // paint current model into the (maybe fresh) tbody now
        // Re-pull on every (re)activation, PRESERVING any unapplied edits: a 304 keeps
        // the current model; a 200 (working copy changed out-of-band — an external sync/
        // apply) rebuilds with fresh read-only values while edited rows keep the typed
        // value (audit P1 — was previously skipped entirely while dirty, showing stale).
        var keep = !!(state.dirty && state.dirty.size > 0);
        load(function () { scheduleRender(); }, keep);
    }

    // ── tab switching (segmented control in _bulkedit.html) ─────────────────────
    // setup() is called by the _bulkedit.html inline <script> on EVERY render of
    // /bulk (including each HTMX re-render into #table-pane). The segmented buttons
    // are part of the swapped-in markup, so their click listeners die with the old
    // DOM — bind them fresh each render (per-element guard so a stray double-setup
    // can't double-bind). Then RESTORE the persisted active pane: if the user left on
    // "All values", re-enter it via switchPane so the fresh (empty) tbody gets
    // re-wired + re-painted, instead of leaving the default "Loading values…" stuck.
    function setup() {
        var segs = document.querySelectorAll('.bulk-seg');
        if (!segs.length) return;
        for (var i = 0; i < segs.length; i++) {
            (function (seg) {
                if (seg._wired) return;
                seg._wired = true;
                seg.addEventListener('click', function () { switchPane(seg.getAttribute('data-pane')); });
            })(segs[i]);
        }
        // Restore the last-used pane on this fresh DOM. Default 'grid' (the curated
        // grids) preserves the shipped first-open behaviour for users who never
        // touched All values. We only auto-switch INTO 'allvalues'; if the saved pane
        // is 'grid' the template's default-visible grid pane already matches.
        if (lsGet(TAB_KEY) === 'allvalues') switchPane('allvalues', true);
    }
    // restoring=true → triggered by setup() on (re)render, not a user click: don't
    // re-persist (no behavioural change) and it's safe to run before the user ever
    // interacts. activate() handles the fresh-DOM re-wire + re-paint.
    function switchPane(pane, restoring) {
        var panes = document.querySelectorAll('[data-bulk-pane]');
        for (var i = 0; i < panes.length; i++)
            panes[i].hidden = (panes[i].getAttribute('data-bulk-pane') !== pane);
        var segs = document.querySelectorAll('.bulk-seg');
        for (var j = 0; j < segs.length; j++) {
            var on = segs[j].getAttribute('data-pane') === pane;
            segs[j].classList.toggle('active', on);
            segs[j].setAttribute('aria-pressed', on ? 'true' : 'false');
        }
        if (!restoring) lsSet(TAB_KEY, pane);
        if (pane === 'allvalues') activate();
    }

    window.AllValues = {
        setup: setup, switchPane: switchPane, _state: state,
        setFont: function (s) { lsSet('quam_av_fs', String(s)); applyFont(); },
        setLetterSpacing: function (s) { lsSet('quam_av_ls', String(s)); applyFont(); },
        toggleBold: function () { lsSet('quam_av_bold', lsGet('quam_av_bold') === '1' ? '0' : '1'); applyFont(); }
    };
})();
