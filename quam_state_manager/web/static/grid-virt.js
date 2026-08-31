/* GridVirt — cold-column virtualization for a Live-Edit grid (docs/141 §4ad).
 *
 * This is the mechanism §4n built for the QUBIT grid, lifted out unchanged so
 * the PAIR grid can have it too. §4n said why the lift had to come first:
 * "the qubit grid's mechanism is the one worth generalizing into a shared
 * module before a second consumer appears". Copying ~330 lines into
 * pair-edit.js would have been the third time this project paid for a
 * duplicated implementation drifting (docs/141 §4ac found the literal
 * two-tool selector list born exactly that way).
 *
 * What it does, in one paragraph. A wide grid ships far more cells than fit:
 * the SERVER renders the columns past the client's look-ahead window as empty
 * `<td class="bulk-td-cold">` that keep their identity (`ck-N`, the flag
 * classes, `data-col-key`) plus ONE value map, and the client fills them from
 * `GET /bulk/cells` on demand. On top of that the client detaches (into
 * fragments, never innerHTML) any further column its own estimate calls cold.
 * Both kinds live in one `cold` set, so everything downstream — the whole-chip
 * search, the header stats, path-addressed repaints, keyboard navigation,
 * sorting — sees one mechanism.
 *
 * What is deliberately NOT here: anything that knows what a qubit or a pair
 * is. The owner passes its DOM (`table`, `rows`, `scroller`), its element ids
 * (`styleId`, `noteId`, `mapId`), the row attribute its cells are keyed by,
 * its persisted column widths, the extra query parameters its hydration needs,
 * and the hooks that run when cells land. Everything else is arithmetic.
 *
 * LAYOUT-FREE at init (docs/141 §4i): nothing in `init()` reads offsetLeft,
 * clientWidth or any other geometry — reading one before the first paint
 * forces the layout of the FULL table (~450 ms on the 20Q chip) and every
 * later forced layout during the load pays it again. Coldness is ESTIMATED
 * from each column's value-fit width against `screen.availWidth`; the scroll
 * pass reads real geometry later, on a table that is by then a fraction of
 * the size, and hydrates anything the estimate got wrong the moment it is on
 * screen.
 */
(function () {
    'use strict';
    if (window.GridVirt) return;

    // The client's own gates. A grid under either is left byte-identical --
    // the safety gate every small chip rides. `MIN_CELLS` is a cheap
    // pre-filter so a genuinely small grid never even walks its headers;
    // `MIN_COLD` is the real one, because it gates on the BENEFIT (how many
    // cells actually go cold) rather than a proxy for it, and so cannot be
    // wrong about a wide-but-short or a narrow-but-tall chip (docs/120 #19).
    var MIN_CELLS = 600;
    var MIN_COLD = 800;
    var BUFFER = 1.5;                   // hydrate up to 1.5 viewports ahead
    var EST_PX_PER_CHAR = 8;            // the 16px-root fallback (see pxPerChar)
    var EST_PAD = 28;

    var _resolved = {
        then: function (f) { try { f(); } catch (e) {} return _resolved; },
        catch: function () { return _resolved; }
    };

    /* The cell font is calc(0.92rem * --bulk-fs) mono with --bulk-ls
       letter-spacing, and the root font is 21px under UI scaling (docs/136
       §18c): a literal 8 px/char froze widths BELOW the hydrated ones there,
       so every hydration widened the column -- the layout churn the freeze
       exists to remove (docs/141 4l-review). A computed style of the root is
       a STYLE read, never a layout; a mono glyph is ~0.62em wide. */
    /* The root font is PICO'S OWN breakpoint ladder (16 -> 21 px at the widest
       step) and it lives in a STYLESHEET: nothing in this app ever writes
       documentElement.style.fontSize, and the S/M/L control writes a
       `data-font-size` ATTRIBUTE feeding --font-size-base, which `body`
       consumes and `html` never does -- so it moves neither the root nor the
       cell's own 0.92rem. Both inline reads the old code did were therefore
       dead, and its 17 fallback undershot the one case the comment above says
       this exists for: measured on the real 20Q chip at a 21 px root, 9.697
       px/char against a true 11.32 advance (-14.3%), 143 of 224 columns
       GROWING on hydration, the pane 53,411 -> 57,899 px (docs/141 4ae B-8;
       with this fix 129 grow, 57,043 -> 57,899, worst column +86 -> +39 px).
       Only a computed style of the root can see 21 px.

       It is a STYLE read, never a LAYOUT one -- the docs/141 4i rule is about
       not forcing LAYOUT. Measured in real Chrome on this grid with
       Performance.getMetrics: 50 reads of getComputedStyle(root).fontSize,
       each after a DOM mutation, forced 0 layouts and 0 style recalcs, against
       50 of each for one `offsetWidth`. (jsdom answers it by evaluating media
       queries, which reads window.innerWidth -- the harness's geometry counter
       sees that ONE script-eval read and nothing at mount.)

       Memoised, and primed at script evaluation: the stylesheets are applied
       by then (render-blocking CSS precedes every script here) but the grid's
       DOM is not yet dirty, and a computed style flushes pending style --
       taking the first read inside the mount instead charged `virt: plan`
       5 -> 14 ms median (48 ms worst) on the 224-column chip, against 8 ms
       primed. The ladder moves only on a resize, which drops the memo. */
    var _rootPxCache = 0;
    function _rootPx() {
        if (!_rootPxCache) {
            try {
                if (window.getComputedStyle) {
                    _rootPxCache = parseFloat(getComputedStyle(document.documentElement).fontSize) || 0;
                }
            } catch (e) {}
        }
        return _rootPxCache;
    }
    try {
        _rootPx();                                   // prime it while style is cheap
        window.addEventListener('resize', function () { _rootPxCache = 0; }, { passive: true });
    } catch (e) {}

    function pxPerChar() {
        // rootPx: see _rootPx above -- a stylesheet fact, not an inline one.
        // --bulk-fs / --bulk-ls ARE inline (bulk-edit.js _applyGlobalScale).
        var rootPx = 17, fs = 1, ls = 0;
        try {
            var st = document.documentElement.style;
            rootPx = _rootPx() || parseFloat(st.fontSize) || 17;
            fs = parseFloat(st.getPropertyValue('--bulk-fs')) || 1;
            var lsRaw = (st.getPropertyValue('--bulk-ls') || '').trim();
            ls = lsRaw.slice(-2) === 'em' ? (parseFloat(lsRaw) || 0) * rootPx * 0.92 * fs : (parseFloat(lsRaw) || 0);
        } catch (e) {}
        var px = rootPx * 0.92 * fs * 0.62 + (isNaN(ls) ? 0 : ls);
        return (isFinite(px) && px > 0) ? px : EST_PX_PER_CHAR;
    }

    // hidden by the column checkboxes or by the search: not on screen (class
    // based, not offsetParent -- jsdom has no layout and the harness must see
    // the same answer as Chrome)
    function thHidden(h) {
        return h.classList.contains('bulk-col-hidden') || h.classList.contains('bulk-search-hidden');
    }

    function create(opts) {
        opts = opts || {};
        var table = opts.table;                       // () -> the <table>
        var rowsOf = opts.rows || function () { return []; };
        var scrollerOf = opts.scroller;               // (t) -> the scroll container
        var styleId = opts.styleId;                   // the width-freeze <style>
        var noteId = opts.noteId;                     // the honest-failure line
        var mapId = opts.mapId;                       // the server's cold map
        var tableSel = opts.tableSel;                 // '#bulk-table' etc, for the width rules
        // no default: a shared core must not carry one grid's DOM fact, and a
        // binding that forgets it should break loudly, not silently read the
        // other grid's rows (tests/test_bulk_virt_server.py::TestGridVirtBinding)
        var rowAttr = opts.rowAttr;
        var colWidths = opts.colWidths || function () { return {}; };
        var urlParams = opts.urlParams || function () { return ''; };
        var onLanded = opts.onLanded || function () {};
        // The owner may hold a live reference to `v` (the qubit grid does:
        // ~20 call sites read .cold / .remote / .byPath directly). The core
        // changes `v` from places the owner never calls -- its own scroll
        // listener, a fetch landing -- so every assignment announces itself
        // and the owner's mirror can never rot.
        var onState = opts.onState || function () {};
        var phase = opts.phase || function () {};

        var v = null;                 // { html, vals, cold, remote, inflight, wrap, byPath, pathTd, failed }
        var scrollPending = false;

        function styleEl() {
            var el = document.getElementById(styleId);
            if (!el) { el = document.createElement('style'); el.id = styleId; document.head.appendChild(el); }
            return el;
        }

        // docs/141 4ae: columns the server has told us it cannot serve. They are
        // out of `cold` (never asked for again) but their tds are still on the
        // page, still empty -- so the instance must stay alive to keep their
        // values in the whole-chip search, and the note must keep saying so.
        function deadNote(mine) {
            var n = mine && mine.dead ? mine.dead.size : 0;
            if (!n) return '';
            return n + ' column' + (n === 1 ? '' : 's') + ' could not be loaded'
                + ' — reload the page to see ' + (n === 1 ? 'it' : 'them');
        }

        // docs/141 4af B-1: the note is an aria-live region, and content that is
        // ALREADY THERE when a live region enters the accessibility tree is not
        // announced. The shipped code created the <p> and filled it in the SAME
        // task -- a MutationObserver on the real page saw `inserted` and
        // `text-added` in one callback batch -- so the one message whose whole
        // job is to be heard once probably never was. It is created EMPTY at
        // mount now, and it is never `hidden`: hidden takes the region back out
        // of the tree, so un-hiding and filling in one task is the same
        // anti-pattern. `_fit_audit.html`'s `#fa-live` is the app's own
        // precedent for the empty-at-render form. An empty <p> lays out at
        // height 0 and its margins are zeroed while empty, so the page is
        // pixel-identical to before it existed.
        function noteEl(create) {
            var el = document.getElementById(noteId);
            if (el || !create) return el;
            var t = table(); if (!t) return null;
            var wrap = t.closest('.bulk-table-wrap') || t.parentElement;
            if (!wrap || !wrap.parentNode) return null;
            el = document.createElement('p');
            el.id = noteId; el.className = 'muted bulk-virt-note';
            el.style.cssText = 'margin:0;font-size:.78em';
            el.setAttribute('role', 'status');
            el.setAttribute('aria-live', 'polite');
            wrap.parentNode.insertBefore(el, wrap);
            return el;
        }

        // docs/141 4af B-1: a cold cell is `role=cell name=""` -- to assistive
        // technology indistinguishable from a parameter the chip genuinely does
        // not carry. Measured on the real 20Q chip with
        // Accessibility.getPartialAXTree: 7,200 of 7,810 data cells, and pair
        // row q1-2 read as 113 cells of which 100 were blank. The honest place
        // to say so once is the COLUMN HEADER -- the name a reader announces
        // when it crosses into a column -- so this is ~100 marks and not 7,200:
        // a per-cell label would make that one row say "not loaded" a hundred
        // times, which is worse than silence. The mark is `visually-hidden`
        // (clip-rect 1px: still in the accessibility tree, unlike display:none)
        // and is position:absolute, so it contributes no layout and `pass()`'s
        // offsetLeft/offsetWidth window is unchanged. It is removed the moment
        // the column lands; a RETIRED column (4ae) says the other, permanent
        // thing, because "still coming" and "never coming" are not one state.
        function markHeads(t) {
            if (!t) return;
            t.querySelectorAll('th.bulk-col-head[data-col-key]').forEach(function (h) {
                var k = h.getAttribute('data-col-key');
                var msg = !v ? '' : (v.dead && v.dead.has(k)) ? 'could not be loaded'
                        : v.cold.has(k) ? 'not loaded' : '';
                var s = h.querySelector('.bulk-col-a11y');
                if (!msg) { if (s && s.parentNode) s.parentNode.removeChild(s); return; }
                if (!s) {
                    s = document.createElement('span');
                    s.className = 'visually-hidden bulk-col-a11y';
                    // before the stats, so the name reads "length not loaded
                    // min .. max", not after the two header buttons
                    h.insertBefore(s, h.querySelector('.bulk-col-stats'));
                }
                if (s.textContent !== msg) s.textContent = msg;
            });
        }

        /* docs/141 4ae B-10: the class style.css hangs the "never coming"
           look on, and the title that is the only per-cell explanation the user
           can reach. Written HERE, on the bounded set one refusal names --
           never on the thousands of merely-cold tds, whose only treatment is a
           flat background rule that costs no DOM writes at all. */
        function markDead(keys) {
            if (!keys || !keys.length) return;
            var t = table(); if (!t) return;
            var set = {};
            keys.forEach(function (k) { set[k] = 1; });
            t.querySelectorAll('tbody td.bulk-td-cold[data-col-key]').forEach(function (td) {
                if (!set[td.getAttribute('data-col-key')]) return;
                td.classList.add('bulk-td-dead');
                td.setAttribute('title', 'This column could not be loaded — reload the page');
            });
        }

        function note(msg) {
            var t = table(); if (!t) return;
            var el = noteEl(true);
            if (!el) return;
            el.textContent = msg || '';
            el.style.margin = msg ? '.15rem 0 .3rem' : '0';
            // docs/141 4ae B-3: `#table-pane` is the ONE scroller (§4q) and the
            // toolbar rows follow a sideways scroll by transform. A note born
            // on a FAILED fetch never reached that code, so it was created at
            // the pane's left edge -- measured 23,676 px off screen, invisible
            // at exactly the moment it fired, because the user had scrolled
            // right and that is WHY hydration ran.
            if (msg) {
                try {
                    var sc = scroller(t);
                    if (sc && sc.scrollLeft) el.style.transform = 'translateX(' + sc.scrollLeft + 'px)';
                } catch (e) { /* a note that cannot be pinned is still a note */ }
            }
        }

        /* docs/141 4n: the SERVER renders the columns past the look-ahead
           window as empty tds (class bulk-td-cold) and ships their values in
           the cold map; init() adopts them into the same structure the
           client-side detach fills, marked `remote` — hydration of a remote
           column is GET /bulk/cells (the page's own cell macro), of a local
           one the stashed fragment. Everything downstream sees one cold set. */
        function serverCold(t) {
            var tds = t.querySelectorAll('tbody td.bulk-td-cold[data-col-key]');
            if (!tds.length) return null;
            var keys = new Set();
            Array.prototype.forEach.call(tds, function (td) { keys.add(td.getAttribute('data-col-key')); });
            var map = { rows: [], cols: {} };
            try {
                var el = document.getElementById(mapId);
                if (el) map = JSON.parse(el.textContent || '{}') || map;
            } catch (e) { map = { rows: [], cols: {} }; }
            var rowIndex = {};
            (map.rows || []).forEach(function (id, i) { rowIndex[id] = i; });
            return { keys: keys, map: map, rowIndex: rowIndex };
        }

        function init() {
            v = null;
            onState(v);
            styleEl().textContent = '';
            var t = table(); if (!t) return null;
            var tds = t.querySelectorAll('tbody td[data-col-key]');
            // server-cold columns are ALREADY empty: they must be adopted
            // whatever the client's own gates say (the server applied the same
            // gates, from the same constants -- tests/test_bulk_virt_server.py
            // pins that the two mirrors agree)
            var srv = serverCold(t);
            if (!srv && tds.length < MIN_CELLS) return null;
            var wrap = scrollerOf(t);
            // NOT window.innerWidth: Blink updates style + layout to answer it
            // (the scrollbar question), i.e. the full-table layout this
            // function exists to avoid -- measured 1.4 s inside "plan" on
            // re-navigation. screen.availWidth needs no layout and bounds the
            // viewport from above (more hot columns than needed, never fewer).
            var edge = ((window.screen && window.screen.availWidth) || 1600) * (1 + BUFFER);
            var cold = new Set();
            var x = 0;
            var row0 = t.querySelector('tbody tr');
            var est = {};
            var pxChar = pxPerChar();
            var widthsNow = colWidths() || {};
            if (row0) {
                Array.prototype.forEach.call(row0.querySelectorAll('td[data-col-key]'), function (td) {
                    var k0 = td.getAttribute('data-col-key');
                    // a drag-resized column has a REAL width in JS (docs/111)
                    // -- the value-fit estimate would call a narrowed column
                    // cold while it sits on screen (docs/141 4l-review)
                    var forced = widthsNow[k0] ? parseFloat(widthsNow[k0]) : 0;
                    if (forced > 0) { est[k0] = forced + EST_PAD; return; }
                    var inp = td.querySelector('.bulk-cell');
                    if (!inp) return;   // a server-cold cell: data-maxlen decides (below)
                    var size = parseInt(inp.getAttribute('size'), 10) || 8;
                    est[k0] = size * pxChar + EST_PAD;
                });
            }
            var widths = [];
            t.querySelectorAll('th.bulk-col-head[data-col-key]').forEach(function (h) {
                var k = h.getAttribute('data-col-key');
                // docs/141 4n: the server's value-fit width (data-maxlen) is
                // the same number the input's size attr carried — it is what
                // makes a server-cold column's freeze exact with no cell to read
                var ml = parseInt(h.getAttribute('data-maxlen'), 10);
                var w = est[k] || ((ml > 0 ? ml : 8) * pxChar + EST_PAD);
                var label = h.querySelector('.bulk-col-label');
                var lw = label ? label.textContent.length * 7.5 + 30 : 0;
                // freeze a cold column at its ESTIMATED value-fit width (by
                // class, no layout read): without it a pruned column shrinks
                // to its header and every hydration widens it again -- a
                // layout churn of ~0.9 s per search keystroke, measured. A
                // hidden-at-mount column is frozen too: its rule is inert
                // while it is display:none and stops the widen-on-scroll.
                var ck = /(?:^|\s)(ck-\d+)(?:\s|$)/.exec(h.className || '');
                var freeze = function () {
                    if (ck) widths.push(tableSel + ' th.' + ck[1] + '{min-width:' + Math.round(Math.max(w, lw)) + 'px}');
                };
                if (thHidden(h)) { cold.add(k); freeze(); return; }
                // a server-cold column is cold whatever the client's estimate
                // says (its cells are not here); it still takes its width
                if (x > edge || (srv && srv.keys.has(k))) { cold.add(k); freeze(); }
                x += Math.max(w, lw);
            });
            if (!srv) {
                if (!cold.size) return null;
                // The real gate: enough cells actually go cold to repay it.
                if (cold.size * rowsOf().length < MIN_COLD) return null;
            }
            // byPath: dot path -> column key for every detached cell, so a
            // path-addressed repaint (undo) hydrates ONE column, not the grid.
            // pathTd (docs/141 4ac): dot path -> the server-cold <td> that
            // holds its value in `vals`. byPath only names the COLUMN, which
            // is enough to decide what to hydrate but not to repair one cell's
            // search text.
            v = { html: new Map(), vals: new Map(), cold: cold, wrap: wrap, byPath: {},
                  pathTd: {}, remote: new Set(), inflight: new Map(), failed: 0 };
            onState(v);
            styleEl().textContent = widths.join('\n');
            phase('virt: plan');
            Array.prototype.forEach.call(tds, function (td) {
                var colKey = td.getAttribute('data-col-key');
                if (!v.cold.has(colKey)) return;
                if (srv && td.classList.contains('bulk-td-cold') && srv.keys.has(colKey)) {
                    // a server-cold cell: its value + paths come from the map
                    v.remote.add(colKey);
                    var tr = td.parentNode;
                    var ri = srv.rowIndex[tr && tr.getAttribute ? tr.getAttribute(rowAttr) : ''];
                    var ent = (srv.map.cols && srv.map.cols[colKey] && ri != null) ? srv.map.cols[colKey][ri] : null;
                    if (ent) {
                        if (ent[0]) v.vals.set(td, String(ent[0]).toLowerCase());
                        if (ent[1]) { v.byPath[ent[1]] = colKey; v.pathTd[ent[1]] = td; }
                        if (ent[2]) { v.byPath[ent[2]] = colKey; v.pathTd[ent[2]] = td; }
                    }
                    return;
                }
                var inp = td.querySelector('.bulk-cell');
                var val = inp ? String(inp.value) : (td.textContent || '');
                if (inp) {
                    var a1 = inp.getAttribute('data-dot-path'), a2 = inp.getAttribute('data-resolved');
                    if (a1) v.byPath[a1] = colKey;
                    if (a2) v.byPath[a2] = colKey;
                } else {
                    var ls = td.querySelector('.bulk-cell-list[data-path]');
                    if (ls) v.byPath[ls.getAttribute('data-path')] = colKey;
                }
                v.vals.set(td, val.toLowerCase());
                // the cell's NODES move into a fragment (docs/141 4i): no
                // innerHTML serialisation here, no re-parse on hydrate
                var frag = document.createDocumentFragment();
                while (td.firstChild) frag.appendChild(td.firstChild);
                v.html.set(td, frag);
                td.classList.add('bulk-td-cold');
            });
            phase('virt: detach ' + v.html.size + ' cells'
                  + (v.remote.size ? ', ' + v.remote.size + ' server-cold columns' : ''));
            // docs/141 4af B-1: both a11y surfaces, at mount -- the live region
            // empty (so a later message is an ADDITION, which is what gets
            // announced) and every cold column's header saying it is not loaded.
            noteEl(true);
            markHeads(t);
            if (wrap && !wrap['_virtScrollBound_' + styleId]) {
                wrap['_virtScrollBound_' + styleId] = true;
                wrap.addEventListener('scroll', function () { onScroll(); }, { passive: true });
            }
            return v;
        }

        /* docs/141 4ac: a server-cold cell's search text lives ONLY in the
           cold map. Callers that learn a new display value for a path but
           deliberately do not repaint the cell (an undo of a remote column,
           the apply echo) must still repair it, or the whole-chip search
           answers from a snapshot taken before the edit. */
        function patchColdValue(dotPath, disp) {
            if (!v || !v.pathTd) return false;
            var td = v.pathTd[dotPath];
            if (!td || !v.remote.has(td.getAttribute('data-col-key'))) return false;
            v.vals.set(td, String(disp == null ? '' : disp).toLowerCase());
            patchColdValue.flushHay = true;
            return true;
        }

        // Returns a Promise that resolves when every named column is here: the
        // local ones (stashed fragments) synchronously, before it is even
        // returned; the server-cold ones after GET /bulk/cells lands. A caller
        // that only needs what can be had NOW ignores the promise.
        function hydrateCols(keys) {
            if (!v || !keys || !keys.length) return _resolved;
            var due = keys.filter(function (k) { return v.cold.has(k); });
            if (!due.length) return _resolved;
            var t = table(); if (!t) { v = null; onState(v); return _resolved; }
            var remote = due.filter(function (k) { return v.remote.has(k); });
            due = due.filter(function (k) { return !v.remote.has(k); });
            var pending = remote.length ? fetchCells(remote) : null;
            if (!due.length) return pending || _resolved;
            // ONE cold-cell scan + ONE PhysAmp pass for the whole batch. The
            // old per-column path (a full-table querySelectorAll AND a
            // whole-table PhysAmp.applyAll per column) is what made a broad
            // patch press cost 1.2–1.6 s on the real 20Q chip (docs/126 ③).
            var set = {};
            due.forEach(function (k) { set[k] = 1; v.cold.delete(k); });
            t.querySelectorAll('td.bulk-td-cold').forEach(function (td) {
                var k = td.getAttribute('data-col-key');
                if (!k || !set[k]) return;
                var h = v.html.get(td);
                if (h != null) {
                    if (typeof h === 'string') td.innerHTML = h;   // an older stash (never, after 4i)
                    else td.appendChild(h);                        // the fragment: nodes back, verbatim
                    v.html.delete(td); v.vals.delete(td);
                }
                td.classList.remove('bulk-td-cold');
            });
            landed(t, set);
            return pending || _resolved;
        }

        // the common tail of a hydration, local or remote
        function landed(t, set) {
            // docs/141 4ae C3: release only when there is nothing left to
            // speak for. A retired column's td is still on the page and still
            // empty, and its value lives in `vals` -- dropping the instance
            // would take that value out of the whole-chip search and leave the
            // cell unexplained.
            if (v && !v.cold.size && !(v.dead && v.dead.size)) {
                v = null; styleEl().textContent = '';
            }
            onState(v);
            markHeads(t);                       // docs/141 4af B-1
            // docs/109: cold cells were detached with their SERVER-rendered
            // dBm annotations — if the viewer switched the MW-power unit
            // meanwhile, the re-inserted text would be stale; reformat.
            if (window.PhysAmp) window.PhysAmp.applyAll(t);
            try { onLanded(t, set); } catch (e) {}
        }

        /* docs/141 4n: fetch the cells of server-cold columns. ONE request per
           batch, a column already in flight is not asked for twice, a failed
           batch stays cold (the next pass asks again) and says so in one line.
           The chip token guards against another chip having been opened in
           this server context since the page rendered (409 → the columns stay
           empty and the note says why). */
        function fetchCells(keys) {
            var mine = v;
            var fresh = keys.filter(function (k) { return !mine.inflight.has(k); });
            var waits = keys.map(function (k) { return mine.inflight.get(k); }).filter(Boolean);
            if (fresh.length) {
                var url = '/bulk/cells?cols=' + encodeURIComponent(fresh.join(',')) + (urlParams() || '');
                var req = fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
                    .then(function (r) {
                        return r.json().catch(function () { return {}; }).then(function (d) {
                            if (!r.ok || !d || !d.ok) {
                                var err = new Error((d && d.error) || ('HTTP ' + r.status));
                                err.status = r.status;
                                throw err;
                            }
                            return d;
                        });
                    })
                    .then(function (d) {
                        fresh.forEach(function (k) { mine.inflight.delete(k); });
                        if (v !== mine) return;                 // a re-mount happened meanwhile
                        applyCells(d.cells || {}, fresh, d.unknown);
                        // docs/141 4ae C4: a success clears a RETRYABLE failure,
                        // never a retirement. Keyed on `failed` alone, one
                        // unrelated column landing erased the only signal that
                        // N cells are permanently blank.
                        if (mine.failed) { mine.failed = 0; note(deadNote(mine)); }
                    })
                    .catch(function (e) {
                        fresh.forEach(function (k) { mine.inflight.delete(k); });
                        if (v !== mine) return;
                        // docs/141 4ad/4ae: retire on an answer that CANNOT change
                        // without a new page. A 400 means the server does not know
                        // these columns and will not know them a second later; a
                        // 409 means another chip is open in this server context,
                        // which is why the note already said "reload the page" --
                        // yet it was retried on every scroll pass anyway, measured
                        // at 72.7 requests/second on one drag. A network error or a
                        // 5xx keeps the columns cold: those answers CAN change.
                        var dead = e && (e.status === 400 || e.status === 409);
                        if (dead) {
                            if (!mine.dead) mine.dead = new Set();
                            fresh.forEach(function (k) {
                                mine.cold.delete(k); mine.remote.delete(k); mine.dead.add(k);
                            });
                            if (v === mine) markDead(fresh);
                            onState(v);
                            markHeads(table());     // docs/141 4af B-1
                            note(deadNote(mine) + (e && e.message ? ' — ' + e.message : ''));
                            return;
                        }
                        mine.failed = (mine.failed || 0) + fresh.length;
                        note(fresh.length + ' column' + (fresh.length === 1 ? '' : 's')
                            + ' could not be loaded — scroll again to retry'
                            + (e && e.message ? ' (' + e.message + ')' : '')
                            + (mine.dead && mine.dead.size ? '. ' + deadNote(mine) : ''));
                    });
                fresh.forEach(function (k) { mine.inflight.set(k, req); });
                waits.push(req);
            }
            return Promise.all(waits).then(function () {}, function () {});
        }

        // land fetched cells: the td's contents become the server's markup
        // (the same macro the page rendered with), the column leaves the cold set
        function applyCells(cells, keys, unknown) {
            var t = table(); if (!t || !v) return;
            // docs/141 4ae C1: the route 400s only when EVERY asked column is
            // unknown; a mixed batch is a 200 carrying `unknown: [...]`, which
            // nothing read. Those keys stayed cold, so a scroll sweep re-asked
            // for them on every pass and never reached the all-unknown batch
            // that would have retired them. Keyed on `unknown`, never on a
            // missing `cells[k]` -- absence is also what a legitimately empty
            // answer looks like.
            if (unknown && unknown.length) {
                if (!v.dead) v.dead = new Set();
                var retired = [];
                unknown.forEach(function (k) {
                    if (!v.cold.has(k) && !v.remote.has(k)) return;
                    v.cold.delete(k); v.remote.delete(k); v.dead.add(k);
                    retired.push(k);
                });
                markDead(retired);
                note(deadNote(v));
                markHeads(t);                   // docs/141 4af B-1
            }
            var set = {};
            keys.forEach(function (k) {
                if (!cells[k]) return;              // not answered: stays cold, retried
                set[k] = 1; v.cold.delete(k); v.remote.delete(k);
            });
            if (!Object.keys(set).length) return;
            t.querySelectorAll('td.bulk-td-cold').forEach(function (td) {
                var k = td.getAttribute('data-col-key');
                if (!k || !set[k]) return;
                var tr = td.parentNode;
                var id = tr && tr.getAttribute ? tr.getAttribute(rowAttr) : '';
                var html = cells[k][id];
                // docs/141 4ae C2-minimal: a row the answer does not carry (it
                // disappeared server-side between the render and the fetch) used
                // to be emptied AND un-marked, so the cell went permanently blank,
                // left the whole-chip search, and looked exactly like a value the
                // chip does not carry. Leave it cold instead: still explained,
                // still searchable, still re-fetchable.
                if (html == null) return;
                td.innerHTML = html;
                v.vals.delete(td);
                td.classList.remove('bulk-td-cold');
            });
            landed(t, set);
        }

        function hydrateCol(key) { return hydrateCols([key]); }
        function ensureTd(td) {
            if (v && td) {
                var k = td.getAttribute('data-col-key');
                // a local column is here before this returns; a server-cold
                // one starts its fetch and is here on the next keypress / pass
                if (k && v.cold.has(k)) hydrateCol(k);
            }
        }
        function hydrateAll() {
            if (!v) return _resolved;
            return hydrateCols(Array.from(v.cold));
        }
        // only what can be had without a request: the client-detached fragments
        function hydrateLocal() {
            if (!v) return;
            hydrateCols(Array.from(v.cold).filter(function (k) { return !v.remote.has(k); }));
        }

        function onScroll(immediate) {
            if (!v) return;
            if (immediate) { scrollPending = false; pass(); return; }
            if (scrollPending) return;
            scrollPending = true;
            (window.requestAnimationFrame || function (f) { setTimeout(f, 0); })(function () {
                scrollPending = false;
                pass();
            });
        }

        function pass() {
            if (!v) return;
            var t = table(); if (!t) return;
            var wrap = v.wrap;
            var cw = (wrap && wrap.clientWidth) || 1200;
            var edge = (wrap ? wrap.scrollLeft + wrap.clientWidth : 0) + cw * BUFFER;
            // docs/141 4n: a WINDOW, not everything left of the edge. A jump
            // to the far right (the scrollbar dragged) used to hydrate every
            // column the user skipped over -- with server-cold columns that
            // was one request for the whole grid (198 columns on the 20Q
            // chip, measured). Columns left of the window stay cold;
            // scrolling back runs this pass again, and keyboard navigation
            // hydrates through ensureTd regardless.
            var left = (wrap ? wrap.scrollLeft : 0) - cw * BUFFER;
            var due = [];
            t.querySelectorAll('th.bulk-col-head[data-col-key]').forEach(function (h) {
                var k = h.getAttribute('data-col-key');
                // a hidden column (search or checkbox) reports offsetLeft 0 --
                // it is not on screen, do not hydrate it
                if (!v || !v.cold.has(k) || thHidden(h)) return;
                var x = h.offsetLeft;
                if (x < edge && x + (h.offsetWidth || 0) > left) due.push(k);
            });
            hydrateCols(due);
        }

        return {
            init: init,
            state: function () { return v; },
            drop: function () { v = null; styleEl().textContent = ''; onState(v); },
            hydrateCols: hydrateCols,
            hydrateCol: hydrateCol,
            hydrateAll: hydrateAll,
            hydrateLocal: hydrateLocal,
            ensureTd: ensureTd,
            onScroll: onScroll,
            pass: pass,
            patchColdValue: patchColdValue,
            note: note,
            styleEl: styleEl,
            // the owner's search needs the stored display text of a cold cell
            valOf: function (td) { return v ? (v.vals.get(td) || '') : ''; },
            isCold: function (k) { return !!v && v.cold.has(k); },
            // docs/141 4ae: a column the server REFUSED. Not cold (never asked
            // for again) and not hot (no cells) -- a caller that must tell
            // "still coming" from "never coming" asks this.
            isDead: function (k) { return !!v && !!v.dead && v.dead.has(k); },
            isRemote: function (k) { return !!v && v.remote.has(k); },
            colOfPath: function (p) { return v && v.byPath ? v.byPath[p] : undefined; },
        };
    }

    window.GridVirt = {
        create: create,
        pxPerChar: pxPerChar,
        thHidden: thHidden,
        MIN_CELLS: MIN_CELLS,
        MIN_COLD: MIN_COLD,
        BUFFER: BUFFER,
        EST_PX_PER_CHAR: EST_PX_PER_CHAR,
        EST_PAD: EST_PAD,
    };
})();
