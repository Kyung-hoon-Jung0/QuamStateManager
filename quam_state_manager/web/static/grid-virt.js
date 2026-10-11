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
    /* TAIL COLLAPSE (w7 liveedit). A cold column is an EMPTY td, but an empty
       td is still a laid-out, painted, hit-tested box: on the 30Q rig (2,389
       pair columns x 69 pairs + 1,264 qubit columns) the ~200k cold tds made
       every Enter cost ~0.6-1.2 s of Style/Layout/PrePaint/Paint/HitTest in
       real Chrome, with the JS itself at ~50 ms. So the maximal run of cold
       columns at the RIGHT end of the grid is taken out of layout with one
       stylesheet (display:none by its ck-N class -- never a write to a td),
       the table keeps its scroll range through a margin of their estimated
       width, and the run is put back from its left end as the scroll nears
       it, or the moment anything asks for one of its columns (hydrateCols /
       ensureTd reveal through that column: the conservative default). Gated
       on the number of cells it would take out, so every grid under it --
       every real chip measured so far -- is untouched. */
    var TAIL_MIN_CELLS = 20000;
    /* A FAR JUMP REVEALS ONLY WHERE IT LANDS (w7 final QA). The collapsed
       set is any set of maximal runs, not only the right-end suffix: a jump
       deep into a run (the scrollbar dragged to the end of the 30Q rig's
       2,389-column pair grid) reveals the columns around the landing and the
       run left of them stays out of layout, its width held by ONE of its
       columns kept on screen as a blank spacer (min-width = the estimated
       width of the whole run, contents hidden). Revealing everything up to
       the far end was one 3.0-3.2 s task (a full style + layout of ~200k
       cells), and revealing it in rAF slices does not help: every slice
       re-lays out the whole grown table (measured 73 -> 294 ms per 50-column
       slice, 13 s in all). The rules live in blocks of TAIL_BLOCK columns,
       one <style> each, and a write reassigns only the blocks whose text
       changed: rewriting one sheet of ~2,400 rules costs 0.3-0.9 s of style
       recalc on its own, emptying a 50-rule block ~0.07 s. */
    var TAIL_BLOCK = 64;
    /* A BOUNDED WINDOW, BOTH SIDES, AND NO SHEET WRITE WHILE SCROLLING (w8
       gridscroll). Two things still made a slow scroll across the 30Q rig's
       pair grid cost 0.25-0.77 s per step:
       - only the RIGHT side was ever taken back out of layout, so a slow
         scroll to the far right ended up laying out the whole table (every
         step re-laid 200k+ layout objects);
       - every reveal rewrote a rule block, and ANY stylesheet change -- even
         one rule matching nothing -- makes Blink walk every element of the
         document to schedule the invalidation (measured on the rig: 19-50 ms
         per sheet change over 247k elements, against 3 ms to toggle a class
         on one column's 70 elements; the walk grows as columns hydrate).
       So the rule blocks are written ONCE, when the tail is planned, and each
       rule lifts itself off a column that carries TAIL_ON
       (`th.ck-N:not(.bulk-virt-on)`); from then on a reveal, a collapse or a
       spacer is a class (and, for a spacer, an inline min-width) on that
       column's own th + tds, found through an index built in the same init
       walk, and the right-end margin is an inline style on the table. Columns
       that leave the window on the LEFT are collapsed too, behind a spacer
       holding their MEASURED width (so what is on screen does not move), and
       any shift a reveal from estimates still causes is scrolled back. */
    var TAIL_ON = 'bulk-virt-on';
    var TAIL_SPACER = 'bulk-virt-spacer';
    /* One layout change per FRAME, across grids (w8). The page's two grids
       scroll together (#table-pane is their one scroller), and when both
       changed their windows in the same rAF each forced the other's layout
       and the frame paid both: 250-340 ms. The first grid to change its
       layout in a frame stamps the frame; the other defers to the next one
       -- a frame later, with nothing forced -- and is OWED that frame: the
       grid whose rAF runs first yields it once, so a scroll that keeps one
       grid changing every frame cannot starve the other (its cells would
       show cold). A debt older than 100 ms (its grid unmounted) lapses. */
    var _busyFrame = -1, _owed = null, _owedTs = 0;
    /* The window's size and grain (w8, measured on the 30Q rig with a real
       wheel). A column stays laid out while its box is within TAIL_KEEP
       viewports past the look-ahead window (BUFFER) on either side; the
       window moves in steps of TAIL_STEP viewports -- a reveal brings that
       much past what the look-ahead needs, a collapse waits until that much
       is out. (KEEP, STEP) = (2, 1): per-step tasks p99 125-129 ms, max 137-
       143; (1, 0.5): slow wheel p99 145-151, over-100 ms 23-25 per 350
       steps; (0.5, 0.25): no better, more steps; (0.5, 0.5): slow wheel p99
       96-101, 2 tasks over 100 ms per 350 steps. */
    var TAIL_KEEP = 0.5;
    var TAIL_STEP = 0.5;

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
        var onReveal = opts.onReveal || function () {};
        // the owner's columns holding an unapplied edit ({key: 1}); a column
        // with one is never taken back out of layout (see tailRecollapse)
        var dirtyCols = opts.dirtyCols || function () { return {}; };
        var tailMin = opts.tailMinCells > 0 ? opts.tailMinCells : TAIL_MIN_CELLS;
        var tailBlock = opts.tailBlock > 0 ? opts.tailBlock : TAIL_BLOCK;
        var phase = opts.phase || function () {};

        var v = null;                 // { html, vals, cold, remote, inflight, wrap, byPath, pathTd, failed }
        var scrollPending = false;
        var me = { yielded: false };  // this instance, for the frame debt (_owed)

        function styleEl() {
            var el = document.getElementById(styleId);
            if (!el) { el = document.createElement('style'); el.id = styleId; document.head.appendChild(el); }
            return el;
        }

        // docs/141 4ae: columns the server has told us it cannot serve. They are
        // out of `cold` (never asked for again) but their tds are still on the
        // page, still empty -- so the instance must stay alive to keep their
        // values in the whole-chip search, and the note must keep saying so.
        /* Block b of the collapse rules: block 0 is `<styleId>-tail`, block
           b > 0 is `<styleId>-tail-<b>`, and 's' holds the spacer's two
           static rules. All of them are written once, at plan time (see
           TAIL_ON); 'm' is the retired margin sheet, cleared if a page still
           has one. */
        function tailEl(b, create) {
            var id = styleId + '-tail' + (b ? '-' + b : '');
            var el = document.getElementById(id);
            if (!el && create) { el = document.createElement('style'); el.id = id; document.head.appendChild(el); }
            return el;
        }

        /* Drop every trace of a collapse: the rules, the classes and inline
           widths `tl` put on its columns (a re-mount on the SAME table keeps
           them, and a column still carrying TAIL_ON would ignore the next
           plan's rules), the table's margin, and the marker class on the heads
           it covered (the group band counts that class as hidden). */
        function tailClear(t, tl) {
            if (tl && tl.els && t && tl.list.length && t.contains(tl.list[0].h)) {
                for (var i = 0; i < tl.list.length; i++) {
                    if (tl.applied[i] && tl.applied[i] !== 'off') colState(tl, i, tl.applied[i], 'off');
                    tl.applied[i] = 'off';
                }
                if (tl.appliedM) { t.style.marginRight = ''; tl.appliedM = ''; }
                (tl.pinned || []).forEach(function (r) { r.style.height = ''; });
                tl.pinned = [];
            }
            ['m', 's'].forEach(function (x) { var e = tailEl(x, false); if (e) e.textContent = ''; });
            for (var b = 0; ; b++) {
                var el = tailEl(b, false);
                if (!el) { if (b) break; else continue; }
                el.textContent = '';
            }
            if (tl) tl.texts = [];
            if (t) {
                Array.prototype.forEach.call(t.querySelectorAll('th.bulk-virt-collapsed'), function (h) {
                    h.classList.remove('bulk-virt-collapsed');
                });
            }
        }

        /* One column's state change on the page. 'off' = out of layout (its
           rule applies), 'on' = TAIL_ON lifts the rule, 'sp:<w>' = on, blank,
           and at least <w> px wide (it holds a whole run). Only the column's
           own elements are written: a class toggle on ~70 elements is ~3 ms of
           style work where a sheet change walked the whole document. */
        function colState(tl, i, from, to) {
            var els = tl.els[i], h = tl.list[i].h;
            var on = to !== 'off', wasOn = !!from && from !== 'off';
            var sp = to.charAt(0) === 's', wasSp = !!from && from.charAt(0) === 's';
            if (on !== wasOn || sp !== wasSp) {
                for (var j = 0; j < els.length; j++) {
                    if (on !== wasOn) els[j].classList.toggle(TAIL_ON, on);
                    if (sp !== wasSp) els[j].classList.toggle(TAIL_SPACER, sp);
                }
            }
            if (sp) h.style.setProperty('min-width', to.slice(3) + 'px', 'important');
            else if (wasSp) h.style.removeProperty('min-width');
            // on screen as a spacer too, so the group band must span it
            h.classList.toggle('bulk-virt-collapsed', !on);
        }

        /* The maximal runs of collapsed columns, as [start, end) index pairs. */
        function tailRuns(tl) {
            var out = [], n = tl.list.length, i = 0;
            while (i < n) {
                if (!tl.col[i]) { i++; continue; }
                var s0 = i;
                while (i < n && tl.col[i]) i++;
                out.push([s0, i]);
            }
            return out;
        }

        // the estimated width of what a run would take if it showed (a
        // hidden column takes none), read from the heads' classes now
        function runWidth(tl, s0, e0) {
            var w = 0;
            for (var i = s0; i < e0; i++) if (!thHidden(tl.list[i].h)) w += tl.list[i].w;
            return w;
        }

        /* A run with revealed columns after it cannot hand its width to the
           table's margin: its LAST shown column stays in layout as a blank
           spacer that holds it. -1 when the run is the right-end suffix, or
           shows nothing (then it takes no width anyway). */
        function runSpacer(tl, s0, e0) {
            if (e0 >= tl.list.length) return -1;
            for (var i = e0 - 1; i >= s0; i--) if (!thHidden(tl.list[i].h)) return i;
            return -1;
        }

        /* The rule blocks, written ONCE when the tail is planned: one rule per
           tail column, each lifted by TAIL_ON, so what is in layout is decided
           by the columns' own classes from then on (see TAIL_ON). One RULE per
           column: a single rule with ~2,700 selectors stopped applying past
           ~1,366 columns in real Chrome (measured on the 30Q rig: ck-1377
           onward stayed laid out). */
        function tailRules(tl) {
            var n = tl.list.length, nb = Math.max(1, Math.ceil(n / tailBlock));
            for (var b = 0; b < nb; b++) {
                var sels = [];
                for (var i = b * tailBlock; i < Math.min(n, (b + 1) * tailBlock); i++) {
                    var ck = tl.list[i].ck;
                    sels.push(tableSel + ' th.' + ck + ':not(.' + TAIL_ON + '),' + tableSel + ' td.' + ck
                              + ':not(.' + TAIL_ON + '){display:none!important}');
                }
                var txt = sels.join('\n');
                if (tl.texts[b] !== txt) { tailEl(b, true).textContent = txt; tl.texts[b] = txt; }
            }
            // a spacer is on screen but blank: its contents hidden, no cold look
            var stxt = tableSel + ' th.' + TAIL_SPACER + '>*,' + tableSel + ' td.' + TAIL_SPACER + '>*{visibility:hidden!important}\n'
                     + tableSel + ' td.' + TAIL_SPACER + '{background:none!important;cursor:default!important}';
            if (tl.texts.s !== stxt) { tailEl('s', true).textContent = stxt; tl.texts.s = stxt; }
        }

        // a spacer's width, to the 1/1000 px: a left spacer holds MEASURED
        // widths, and rounding each run to whole px would move what is on
        // screen by the rounding every time the run grows
        function spx(w) { return String(Math.round(w * 1000) / 1000); }

        /* Bring the page in line with the plan: every collapsed column is out
           of layout, except the last shown column of an inner run, which stays
           as a blank spacer holding the run's width; the right-end run's width
           is the table's margin. Writes only the columns whose state changed.
           Returns true when anything was written. */
        function tailWrite() {
            var tl = v && v.tail; if (!tl) return false;
            var t = table();
            var n = tl.list.length, runs = tailRuns(tl), spacer = {}, margin = 0, hit = false;
            runs.forEach(function (r) {
                var w = runWidth(tl, r[0], r[1]);
                if (r[1] >= n) margin = w;
                else {
                    var sp = runSpacer(tl, r[0], r[1]);
                    if (sp >= 0) spacer[sp] = w;
                }
            });
            tl.margin = runs.length ? margin : 0;
            for (var i = 0; i < n; i++) {
                var want = !tl.col[i] ? 'on' : (i in spacer) ? 'sp:' + spx(spacer[i]) : 'off';
                if (tl.applied[i] === want) continue;
                colState(tl, i, tl.applied[i], want);
                tl.applied[i] = want;
                hit = true;
            }
            var m = runs.length ? Math.round(margin) + 'px' : '';
            if (t && tl.appliedM !== m) { t.style.marginRight = m; tl.appliedM = m; hit = true; }
            return hit;
        }

        function tailSet(s0, e0, on) {
            var tl = v && v.tail; if (!tl) return false;
            var hit = false;
            for (var i = Math.max(0, s0); i < Math.min(e0, tl.list.length); i++) {
                if (tl.col[i] !== on) { tl.col[i] = on; hit = true; }
            }
            return hit;
        }

        /* Put the columns [s0, e0) back. Returns true when anything came
           back. The plan is KEPT when everything shows (empty rules):
           scrolling back left can take the far end out of layout again. */
        function tailRevealRange(s0, e0, leftish) {
            var tl = v && v.tail; if (!tl) return false;
            var before = tl.col.slice();
            if (!tailSet(s0, e0, false)) return false;
            tailWrite();
            try { onReveal(table()); } catch (e) {}
            hydrateRevealed(before, leftish);
            return true;
        }

        /* The columns a reveal just put back get their cells in the same
           step (w8). A LOCAL one -- parked by tailPark, or detached at mount
           -- gets the same nodes back, no fetch: shown empty for a frame it
           would sit at its estimated width and move everything right of it
           when the cells came back. A server-cold one is asked for now (the
           fetch is off the main thread); the pass that revealed it no longer
           reads geometry after its change, so waiting for the next frame's
           pass only delayed the request. */
        function hydrateRevealed(before, leftish) {
            var tl = v && v.tail; if (!tl) return;
            var ks = [];
            for (var i = 0; i < tl.list.length; i++) {
                if (!before[i] || tl.col[i]) continue;
                var k = tl.list[i].k;
                if (!v.cold.has(k) || thHidden(tl.list[i].h)) continue;
                ks.push(k);
                // a server-cold column revealed at or left of what is on screen
                // lands later, maybe wider than its estimate: landed() keeps
                // the screen (a local one is back in this step, and the pass
                // that revealed it keeps the screen for both)
                if (leftish && v.remote.has(k)) (v.leftDue || (v.leftDue = {}))[k] = 1;
            }
            if (ks.length) hydrateCols(ks, { reveal: false });
        }

        /* The scroll pass's reveal. For each run the look-ahead window
           [left, edge] reaches (right to left): contiguous with what shows on
           its left -> from its left end, enough to cover the gap plus one
           viewport (the old reveal); contiguous with a window on its right ->
           from its right end, likewise; a jump into its interior -> ONLY the
           columns the window covers (TAIL_STEP of slack on the left), by
           the estimated widths. One run per pass: the pass runs again on the
           next frame, so every step is its own bounded task. */
        function tailRevealWindow(t, wrap, cw, left, edge) {
            var tl = v && v.tail; if (!tl) return false;
            var n = tl.list.length, runs = tailRuns(tl);
            for (var r = runs.length - 1; r >= 0; r--) {
                var s0 = runs[r][0], e0 = runs[r][1], x0, x1, sp = -1;
                // the suffix reaches as far as the margin written for it (a
                // search that hid columns since leaves that one wider)
                if (e0 >= n) { x0 = t.offsetWidth; x1 = x0 + Math.max(runWidth(tl, s0, e0), tl.margin || 0); }
                else {
                    sp = runSpacer(tl, s0, e0);
                    if (sp < 0) continue;
                    x0 = tl.list[sp].h.offsetLeft;
                    x1 = x0 + (tl.list[sp].h.offsetWidth || 0);
                }
                if (!(edge > x0 && left < x1)) continue;
                var a = s0, b = s0, acc = 0, i, leftish = true;
                if (left <= x0 + cw) {
                    leftish = false;
                    while (b < e0 && acc < (edge - x0) + cw * TAIL_STEP) {
                        if (!thHidden(tl.list[b].h)) acc += tl.list[b].w;
                        b++;
                    }
                } else if (sp >= 0 && edge >= x1 - cw) {
                    a = b = e0;
                    while (a > s0 && acc < (x1 - left) + cw * TAIL_STEP) {
                        if (!thHidden(tl.list[a - 1].h)) acc += tl.list[a - 1].w;
                        a--;
                    }
                } else {
                    var x = x0;
                    a = -1; b = e0;
                    for (i = s0; i < e0; i++) {
                        var wi = thHidden(tl.list[i].h) ? 0 : tl.list[i].w;
                        // TAIL_STEP of slack on the left, no more: anything
                        // past TAIL_KEEP would leave again on the next pass
                        if (a < 0 && x + wi > left - cw * TAIL_STEP) a = i;
                        if (x >= edge) { b = i; break; }
                        x += wi;
                    }
                    if (a < 0) {
                        // past every estimate (the margin outgrew them): its end
                        a = b = e0;
                        while (a > s0 && acc < (edge - left) - cw * BUFFER) {
                            if (!thHidden(tl.list[a - 1].h)) acc += tl.list[a - 1].w;
                            a--;
                        }
                    }
                }
                // what is on screen moves only when an INNER run comes back
                // from widths nobody measured (a run a jump skipped): only then
                // is it read before, and kept after (tailKeep)
                var anc = null;
                if (e0 < n) for (i = a; i < b; i++) if (!tl.list[i].m && !thHidden(tl.list[i].h)) { anc = tailAnchor(wrap); break; }
                if (tailRevealRange(a, b, leftish)) return { anc: anc };
            }
            return false;
        }

        /* The other direction (w7 liveedit), now on BOTH sides (w8). Once a
           jump to the far right had revealed the run, every later Enter paid
           the whole table's layout again (big30x: 0.1 s -> 0.55-1.8 s); and a
           slow scroll to the far right revealed column after column and never
           took any back, so it ended up laying out the whole table. A shown
           column whose box lies more than TAIL_KEEP viewports past the look-
           ahead window -- right of it, or LEFT of it -- goes back out of layout,
           only while it is clean (no unapplied edit, not holding the focus,
           not pinned: a pinned column sticks to the pane's edge and must keep
           its box). Columns are taken TAIL_STEP viewports' worth at a time per
           side, so a slow scroll pays one bounded step per half viewport and
           not one per column. A hydrated column keeps its cells (display:none never
           touches a td) and its width becomes its MEASURED one, so the margin
           (right end) or the spacer (anything left of the window) holding it
           is exact and nothing on screen moves. A column with no box of its
           own (search- or checkbox-hidden) goes with its neighbours. */
        function tailRecollapse(t, wrap, cw) {
            var tl = v && v.tail; if (!tl) return false;
            var n = tl.list.length;
            var sl = wrap ? wrap.scrollLeft : 0, vw = wrap ? wrap.clientWidth : 0;
            var hi = sl + vw + cw * (BUFFER + TAIL_KEEP), lo = sl - cw * (BUFFER + TAIL_KEEP);
            var act = document.activeElement, actK = null;
            if (act && act !== document.body && t.contains(act) && act.closest) {
                var at = act.closest('[data-col-key]');
                actK = at && at.getAttribute('data-col-key');
            }
            // the focus, a pin (it sticks to the pane's edge and must keep its
            // box) and a live multi-cell selection (Ctrl+D / paste act on it)
            // hold their column in layout
            var held = function (e) {
                if (e.k === actK || e.h.classList.contains('bulk-col-pinned')) return true;
                var els = tl.els[tl.pos[e.k]];
                for (var q = 1; q < els.length; q++) if (els[q].classList.contains('bulk-sel')) return true;
                return false;
            };
            // a side goes once TAIL_STEP viewports' worth is out there, or
            // anything is a whole viewport further out (left behind by a jump)
            var side = [], acc = { L: 0, R: 0 }, go = { L: false, R: false }, i;
            for (i = 0; i < n; i++) {
                var e = tl.list[i];
                if (tl.col[i] || thHidden(e.h)) continue;
                var x = e.h.offsetLeft, w = e.h.offsetWidth || 0;
                var s = x > hi ? 'R' : (x + w < lo ? 'L' : null);
                if (!s || held(e)) continue;
                side[i] = s; acc[s] += w || e.w;
                if (s === 'R' ? x > hi + cw : x + w < lo - cw) go[s] = true;
            }
            if (acc.L >= cw * TAIL_STEP) go.L = true;
            if (acc.R >= cw * TAIL_STEP) go.R = true;
            if (!go.L && !go.R) return false;
            // out after this pass: already collapsed, or chosen now
            var out = function (j) { return tl.col[j] || (side[j] && go[side[j]]); };
            // a boxless column follows the nearest boxed column on each side
            var prevOut = [], nextOut = [], p = true;
            for (i = 0; i < n; i++) { prevOut[i] = p; if (!thHidden(tl.list[i].h)) p = !!out(i); }
            p = true;
            for (i = n - 1; i >= 0; i--) { nextOut[i] = p; if (!thHidden(tl.list[i].h)) p = !!out(i); }
            var cand = [];
            for (i = 0; i < n; i++) {
                if (tl.col[i]) continue;
                var e2 = tl.list[i];
                if (thHidden(e2.h)) { if (prevOut[i] && nextOut[i] && !held(e2)) cand.push(i); }
                else if (side[i] && go[side[i]]) cand.push(i);
            }
            // an unapplied edit keeps its column: asked of the owner for the
            // hydrated candidates ONLY, with their cells -- the unscoped form
            // walked every cell of the table on every pass that had one
            var hyd = cand.filter(function (j) { return !v.cold.has(tl.list[j].k); });
            var dirty = {};
            if (hyd.length) {
                var scope = {};
                hyd.forEach(function (j) { scope[tl.list[j].k] = tl.els[j].slice(1); });
                try { dirty = dirtyCols(scope) || {}; } catch (x) { dirty = {}; }
            }
            var hit = [];
            cand.forEach(function (j) {
                var e3 = tl.list[j];
                if (dirty[e3.k]) return;
                if (!thHidden(e3.h)) {
                    // the measured width, to the subpixel: a left spacer is the
                    // sum of these, and anything short of exact moves the screen
                    var r = e3.h.getBoundingClientRect ? e3.h.getBoundingClientRect() : null;
                    var mw = (r && r.width) || e3.h.offsetWidth || 0;
                    if (mw > 0) { e3.w = mw; e3.m = true; }
                }
                hit.push(j);
            });
            if (!hit.length) return false;
            pinRows(t, tl);
            hit.forEach(function (j) { tl.col[j] = true; });
            tailPark(tl, hit);
            tailWrite();
            try { onReveal(t); } catch (e4) {}
            return true;
        }

        /* A collapse must not make a row SHORTER. A row is as tall as its
           tallest laid-out cell; when that cell's column leaves (a two-line
           cell far left of the window), the row shrinks and everything below
           it moves up -- on the 30Q rig the qubit grid lost 158 px when the
           window passed its end, shoving the pair grid the user was scrolling
           sideways upward. So before any collapse each row's height (read on
           the layout the pass already has) becomes its inline height, which a
           table row treats as a minimum: rows can grow as columns come in,
           never shrink as they leave. Cleared with the tail (tailClear). */
        function pinRows(t, tl) {
            var rs = t.rows || [], hs = [], i;
            if (!tl.pinned) tl.pinned = [];
            for (i = 0; i < rs.length; i++) hs.push(rs[i].getBoundingClientRect().height);
            for (i = 0; i < rs.length; i++) {
                var h = Math.round(hs[i] * 1000) / 1000;
                if (h > 0 && h > (parseFloat(rs[i].style.height) || 0) + 0.01) {
                    if (!rs[i].style.height) tl.pinned.push(rs[i]);
                    rs[i].style.height = h + 'px';
                }
            }
        }

        /* A hydrated column taken out of layout also gives its cells back to
           fragments -- the init-time client detach (docs/141 4i), so it is a
           LOCAL cold column again: out of the document, its values kept in
           `vals` for the whole-chip search, and back (the same nodes, no
           fetch) the moment the window reaches it. Out of layout was not
           enough: every <input> still in the document is walked by Chrome's
           own form scan after each landing (a native 150-230 ms task on the
           30Q rig that grows with every column a scroll has hydrated). Only
           clean, fully landed columns (tailRecollapse filtered the held and
           dirty ones; a column with a cell still cold keeps what it has). */
        function tailPark(tl, idxs) {
            var parked = {};
            idxs.forEach(function (i) {
                var k = tl.list[i].k, els = tl.els[i], j;
                if (v.cold.has(k) || (v.dead && v.dead.has(k))) return;
                for (j = 1; j < els.length; j++) if (!els[j].isConnected || els[j].classList.contains('bulk-td-cold')) return;
                for (j = 1; j < els.length; j++) {
                    var td = els[j];
                    var inp = td.querySelector('.bulk-cell');
                    var val = inp ? String(inp.value) : (td.textContent || '');
                    v.vals.set(td, val.toLowerCase());
                    var frag = document.createDocumentFragment();
                    while (td.firstChild) frag.appendChild(td.firstChild);
                    v.html.set(td, frag);
                    td.classList.add('bulk-td-cold');
                }
                v.cold.add(k);
                parked[k] = 1;
            });
            if (Object.keys(parked).length) markHeads(table(), parked);
        }

        /* A caller asking for columns may be about to look at them. Near a
           revealed neighbour (within one block) the run comes back THROUGH
           the column from that side, as it always did from the suffix's left
           end; deeper inside a run only the column itself comes back -- a
           spacer holds what is left of it -- so asking for the last column of
           a 2,389-column grid no longer lays out all of them. */
        function tailRevealKeys(keys) {
            var tl = v && v.tail; if (!tl || !keys) return false;
            var n = tl.list.length, hit = false, before = tl.col.slice();
            keys.forEach(function (k) {
                var p = tl.pos[k];
                if (p == null || !tl.col[p]) return;
                var s0 = p, e0 = p + 1;
                while (s0 > 0 && tl.col[s0 - 1]) s0--;
                while (e0 < n && tl.col[e0]) e0++;
                if (e0 < n && e0 - p <= tailBlock) { if (tailSet(p, e0, false)) hit = true; }
                else if (p - s0 < tailBlock) { if (tailSet(s0, p + 1, false)) hit = true; }
                else if (tailSet(p, p + 1, false)) hit = true;
            });
            if (!hit) return false;
            tailWrite();
            try { onReveal(table()); } catch (e) {}
            hydrateRevealed(before);
            return true;
        }

        function isCollapsed(k) {
            var tl = v && v.tail;
            if (!tl) return false;
            var p = tl.pos[k];
            return p != null && !!tl.col[p];
        }

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
        function markHeads(t, only) {
            if (!t) return;
            // a landing in tail mode re-marks only its own columns' heads (a
            // walk of all 2,389 heads, a query each, per landing otherwise)
            var hs = (only && v && v.headOf)
                ? Object.keys(only).map(function (k) { return v.headOf[k]; }).filter(Boolean)
                : t.querySelectorAll('th.bulk-col-head[data-col-key]');
            Array.prototype.forEach.call(hs, function (h) {
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
                    var sc = scrollerOf(t);
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

        /* The maximal suffix of the header order whose columns are all cold
           and carry a ck class (the rules address ck-N, never an attribute
           selector); null under the cell gate. */
        function tailPlan(order, nRows) {
            var i = order.length;
            while (i > 0 && v.cold.has(order[i - 1].k) && order[i - 1].ck) i--;
            var list = order.slice(i);
            var shown = 0;
            list.forEach(function (e) { if (!thHidden(e.h)) shown++; });
            if (!list.length || shown * nRows < tailMin) return null;
            var pos = {};
            list.forEach(function (e, j) { pos[e.k] = j; });
            // els[j]: column j's th and every td, filled by init's own walk --
            // what a reveal/collapse writes and what a landing fills, without
            // a whole-table scan per step
            return { list: list, pos: pos, col: list.map(function () { return true; }), texts: [], margin: 0,
                     els: list.map(function (e) { return [e.h]; }), applied: [], appliedM: undefined };
        }

        function init() {
            var old = v;
            v = null;
            onState(v);
            styleEl().textContent = '';
            var t = table();
            tailClear(t, old && old.tail);
            if (!t) return null;
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
            var order = [];                 // header order, for the tail plan
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
                order.push({ k: k, h: h, w: Math.max(w, lw), ck: ck ? ck[1] : null });
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
            // byPathAll / pathTd are MULTI-valued (QA F4): one leaf is often
            // claimed by several columns (the curated x90 amp alias AND the
            // dyn DragCosine leaf), and byPath's last writer hid the others,
            // so an undo repaint skipped the alias twin and left it stale.
            v = { html: new Map(), vals: new Map(), cold: cold, wrap: wrap, byPath: {},
                  byPathAll: {}, pathTd: {}, remote: new Set(), inflight: new Map(), failed: 0 };
            var claim = function (p, k) {
                v.byPath[p] = k;
                var all = v.byPathAll[p] || (v.byPathAll[p] = []);
                if (all.indexOf(k) < 0) all.push(k);
            };
            var claimTd = function (p, k, td) {
                claim(p, k);
                (v.pathTd[p] || (v.pathTd[p] = [])).push(td);
            };
            onState(v);
            styleEl().textContent = widths.join('\n');
            phase('virt: plan');
            // planned before the walk below so the walk can index the tail's
            // cells (tailPlan reads only the heads and the cold set)
            var tailP = tailPlan(order, rowsOf().length);
            Array.prototype.forEach.call(tds, function (td) {
                var colKey = td.getAttribute('data-col-key');
                if (!v.cold.has(colKey)) return;
                if (tailP) { var tp = tailP.pos[colKey]; if (tp != null) tailP.els[tp].push(td); }
                if (srv && td.classList.contains('bulk-td-cold') && srv.keys.has(colKey)) {
                    // a server-cold cell: its value + paths come from the map
                    v.remote.add(colKey);
                    var tr = td.parentNode;
                    var ri = srv.rowIndex[tr && tr.getAttribute ? tr.getAttribute(rowAttr) : ''];
                    var ent = (srv.map.cols && srv.map.cols[colKey] && ri != null) ? srv.map.cols[colKey][ri] : null;
                    if (ent) {
                        if (ent[0]) v.vals.set(td, String(ent[0]).toLowerCase());
                        if (ent[1]) claimTd(ent[1], colKey, td);
                        if (ent[2] && ent[2] !== ent[1]) claimTd(ent[2], colKey, td);
                    }
                    return;
                }
                var inp = td.querySelector('.bulk-cell');
                var val = inp ? String(inp.value) : (td.textContent || '');
                if (inp) {
                    var a1 = inp.getAttribute('data-dot-path'), a2 = inp.getAttribute('data-resolved');
                    if (a1) claim(a1, colKey);
                    if (a2) claim(a2, colKey);
                } else {
                    var ls = td.querySelector('.bulk-cell-list[data-path]');
                    if (ls) {
                        claim(ls.getAttribute('data-path'), colKey);
                        // docs/159: /undo names the RESOLVED leaf -- a detached
                        // list column must be findable by it too
                        var lr = ls.getAttribute('data-resolved');
                        if (lr) claim(lr, colKey);
                    }
                }
                v.vals.set(td, val.toLowerCase());
                // the cell's NODES move into a fragment (docs/141 4i): no
                // innerHTML serialisation here, no re-parse on hydrate
                var frag = document.createDocumentFragment();
                while (td.firstChild) frag.appendChild(td.firstChild);
                v.html.set(td, frag);
                td.classList.add('bulk-td-cold');
            });
            v.tail = tailP;
            if (v.tail) {
                // the header order and key -> head, so the scroll pass and a
                // landing never query the whole table for them
                v.order = order;
                v.headOf = {};
                order.forEach(function (e) { v.headOf[e.k] = e.h; });
                tailRules(v.tail);
                tailWrite();
                try { onReveal(t); } catch (e) {}   // the group band spans only what shows
                phase('virt: tail ' + v.tail.list.length + ' columns out of layout');
            }
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
            var hit = false;
            (v.pathTd[dotPath] || []).forEach(function (td) {
                if (!v.remote.has(td.getAttribute('data-col-key'))) return;
                v.vals.set(td, String(disp == null ? '' : disp).toLowerCase());
                hit = true;
            });
            if (hit) patchColdValue.flushHay = true;
            return hit;
        }

        // Returns a Promise that resolves when every named column is here: the
        // local ones (stashed fragments) synchronously, before it is even
        // returned; the server-cold ones after GET /bulk/cells lands. A caller
        // that only needs what can be had NOW ignores the promise.
        function hydrateCols(keys, hopts) {
            if (!v || !keys || !keys.length) return _resolved;
            // a caller asking for a column may be about to look at it: bring
            // the tail back through it first -- unless it only repaints values
            if (!(hopts && hopts.reveal === false)) tailRevealKeys(keys);
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
            var idx = coldTdsOf(set), got = idx ? [] : null;
            var anc = (idx && leftLanding(set)) ? tailAnchor(v.wrap) : null;
            Array.prototype.forEach.call(idx || t.querySelectorAll('td.bulk-td-cold'), function (td) {
                var k = td.getAttribute('data-col-key');
                if (!k || !set[k]) return;
                var h = v.html.get(td);
                if (h != null) {
                    if (typeof h === 'string') td.innerHTML = h;   // an older stash (never, after 4i)
                    else td.appendChild(h);                        // the fragment: nodes back, verbatim
                    v.html.delete(td); v.vals.delete(td);
                }
                td.classList.remove('bulk-td-cold');
                if (got) got.push(td);
            });
            landed(t, set, got, anc);
            return pending || _resolved;
        }

        // does a landing include a column the pass saw LEFT of the viewport?
        function leftLanding(set) {
            var ld = v && v.leftDue, hit = false;
            if (!ld) return false;
            Object.keys(set).forEach(function (k) { if (ld[k]) { hit = true; delete ld[k]; } });
            return hit;
        }

        /* Tail mode: the still-cold tds of the columns in `set`, from the
           tail's own index -- a landing scanned every cold td of the table
           for its few columns (150-190 ms per landing on the 30Q rig's pair
           grid). null -- the caller scans, as ever -- outside tail mode, for a
           column the tail does not index, or when the index is stale (a td
           no longer in the document). */
        function coldTdsOf(set) {
            var tl = v && v.tail;
            if (!tl || !tl.els) return null;
            var out = [], ks = Object.keys(set);
            for (var a = 0; a < ks.length; a++) {
                var i = tl.pos[ks[a]];
                if (i == null) return null;
                var els = tl.els[i];
                for (var j = 1; j < els.length; j++) {
                    if (!els[j].isConnected) return null;
                    if (els[j].classList.contains('bulk-td-cold')) out.push(els[j]);
                }
            }
            return out;
        }

        // the common tail of a hydration, local or remote. `tds` (tail mode
        // only) are the cells that just landed: everything below is scoped to
        // them instead of re-walking the whole table per landing
        function landed(t, set, tds, anc) {
            var wrap0 = v && v.wrap, pin0 = v && v.pinEnd;
            // docs/141 4ae C3: release only when there is nothing left to
            // speak for. A retired column's td is still on the page and still
            // empty, and its value lives in `vals` -- dropping the instance
            // would take that value out of the whole-chip search and leave the
            // cell unexplained.
            // w8: a TAIL is never released -- with nothing left to fetch it
            // still keeps the columns far from the window out of layout, and
            // dropping it put the whole 2,389-column table back in one task.
            if (v && !v.tail && !v.cold.size && !(v.dead && v.dead.size)) {
                v = null; styleEl().textContent = '';
            }
            onState(v);
            markHeads(t, tds ? set : null);     // docs/141 4af B-1
            // docs/109: cold cells were detached with their SERVER-rendered
            // dBm annotations — if the viewer switched the MW-power unit
            // meanwhile, the re-inserted text would be stale; reformat.
            if (window.PhysAmp) {
                if (tds && window.PhysAmp.paintWithin) window.PhysAmp.paintWithin(tds);
                else window.PhysAmp.applyAll(t);
            }
            try { if (tds) onLanded(t, set, tds); else onLanded(t, set); } catch (e) {}
            // the pass left the pane at its END and nobody moved it since:
            // cells that came in wider than their estimates keep it there
            if (pin0 != null && wrap0 && Math.abs(wrap0.scrollLeft - pin0) <= 2) {
                var mx = wrap0.scrollWidth - wrap0.clientWidth;
                if (mx > wrap0.scrollLeft + 1) {
                    wrap0.scrollLeft = mx;
                    if (v) v.pinEnd = wrap0.scrollLeft;
                }
            } else if (anc) {
                // cells wider than their estimate, left of what is on screen
                tailKeep(wrap0, anc);
            }
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
            var idx = coldTdsOf(set), got = idx ? [] : null;
            var anc = (idx && leftLanding(set)) ? tailAnchor(v.wrap) : null;
            Array.prototype.forEach.call(idx || t.querySelectorAll('td.bulk-td-cold'), function (td) {
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
                if (got) got.push(td);
            });
            landed(t, set, got, anc);
        }

        function hydrateCol(key) { return hydrateCols([key]); }
        function ensureTd(td) {
            if (v && td) {
                var k = td.getAttribute('data-col-key');
                if (k && isCollapsed(k)) tailRevealKeys([k]);   // focus needs a box
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
            (window.requestAnimationFrame || function (f) { setTimeout(f, 0); })(function (ts) {
                scrollPending = false;
                pass(ts);
            });
        }

        function pass(ts) {
            if (!v) return;
            var t = table(); if (!t) return;
            // another grid already changed layout in this frame: ours is the
            // next frame's (see _busyFrame) -- before reading any geometry
            if (v.tail && ts != null) {
                if (_busyFrame === ts) { _owed = me; _owedTs = ts; onScroll(); return; }
                if (_owed && _owed !== me && !me.yielded && ts - _owedTs < 100) { me.yielded = true; onScroll(); return; }
                me.yielded = false;
                if (_owed === me) _owed = null;
            }
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
            // at the END of a real scroll range the user asked for the end:
            // a reveal, or cells landing, whose real widths differ from the
            // estimates keeps them there (the old reveal left the last columns
            // ~670 px out of view on the 30Q rig). landed() re-pins too.
            var atEnd = !!wrap && wrap.clientWidth > 0 && wrap.scrollWidth > wrap.clientWidth
                && wrap.scrollLeft > 0 && wrap.scrollLeft >= wrap.scrollWidth - wrap.clientWidth - 2;
            if (v.tail) {
                // a font/letter-spacing/UI-scale change resizes every cell: a
                // width measured before it no longer holds, so a run brought
                // back from those is scrolled back like one from estimates
                // (a style read of the root, never a layout)
                var ppc = pxPerChar();
                if (v.tail.ppc !== ppc) {
                    if (v.tail.ppc != null) v.tail.list.forEach(function (e) { e.m = false; });
                    v.tail.ppc = ppc;
                }
                // ONE layout change per pass, and no geometry read after it:
                // a reveal, else a collapse, else a width sync. A reveal asks
                // for its own columns' cells as it happens (hydrateRevealed);
                // anything else due in the window is left to the NEXT frame's
                // pass, on the layout that frame computes anyway (reading it
                // here forced a second layout per grid per frame)
                var rv = tailRevealWindow(t, wrap, cw, left, edge), changed = !!rv;
                if (rv) {
                    if (atEnd) { try { wrap.scrollLeft = wrap.scrollWidth - wrap.clientWidth; } catch (e) {} }
                    else if (rv.anc) tailKeep(wrap, rv.anc);
                } else if (tailRecollapse(t, wrap, cw)) {
                    // measured widths: the spacer or margin holds them exactly
                    changed = true;
                } else if (tailWrite()) {
                    // nothing moved in or out, but a search or a column toggle
                    // changed what the runs' boxes hold: their widths follow
                    changed = true;
                }
                if (changed) {
                    v.pinEnd = atEnd ? wrap.scrollLeft : null;
                    if (ts != null) _busyFrame = ts;
                    onScroll();
                    return;
                }
            }
            v.pinEnd = atEnd ? wrap.scrollLeft : null;
            var due = [];
            // tail mode: the header order init kept -- the table's heads are
            // fixed until the next init, and querying them walked every cell
            Array.prototype.forEach.call(v.order || t.querySelectorAll('th.bulk-col-head[data-col-key]'), function (h) {
                if (h.h) h = h.h;
                var k = h.getAttribute('data-col-key');
                // a hidden column (search or checkbox) reports offsetLeft 0 --
                // it is not on screen, do not hydrate it; nor is a collapsed one
                if (!v || !v.cold.has(k) || thHidden(h) || isCollapsed(k)) return;
                var x = h.offsetLeft, xw = x + (h.offsetWidth || 0);
                if (x < edge && xw > left) {
                    due.push(k);
                    // cells landing wider than their estimate LEFT of the
                    // viewport move what is on screen: landed() keeps it
                    if (v.tail && wrap && xw <= wrap.scrollLeft) (v.leftDue || (v.leftDue = {}))[k] = 1;
                }
            });
            hydrateCols(due);
        }

        /* What the user is looking at before the tail changes around it: the
           first laid-out head (not a spacer, not pinned) inside the pane, and
           where it sits on screen. tailKeep scrolls by however far a change
           moved it -- a run revealed from ESTIMATED widths, or cells landing
           wider than their estimate, left of the viewport, would otherwise
           shove what is on screen sideways. Tail mode only.
           The two grids share ONE horizontal scroller, and their columns
           have nothing to do with each other: a scroll that keeps one grid
           still moves the other by the same amount. So only a grid that
           fills more than half of the pane's height -- the one being looked
           at -- keeps the screen (measured on the 30Q rig: the qubit grid,
           scrolled out of view above, "kept" its own cells and shoved the
           pair grid the user was reading 26-77 px sideways). */
        function tailAnchor(wrap) {
            var tl = v && v.tail;
            if (!tl || !wrap || !v.order || !wrap.getBoundingClientRect) return null;
            var pr = wrap.getBoundingClientRect();
            if (!(pr.right > pr.left)) return null;
            var t = table(), tr = t && t.getBoundingClientRect();
            if (!tr || !(Math.min(tr.bottom, pr.bottom) - Math.max(tr.top, pr.top) > (pr.bottom - pr.top) / 2)) return null;
            for (var i = 0; i < v.order.length; i++) {
                var e = v.order[i], p = tl.pos[e.k];
                if (p != null && tl.col[p]) continue;          // out of layout, or a spacer
                if (thHidden(e.h) || e.h.classList.contains('bulk-col-pinned')) continue;
                var r = e.h.getBoundingClientRect();
                if (r.left >= pr.right) return null;
                if (r.width > 0 && r.right > pr.left) return { h: e.h, x: r.left };
            }
            return null;
        }
        function tailKeep(wrap, a) {
            if (!a || !wrap || !a.h.isConnected) return;
            var d = a.h.getBoundingClientRect().left - a.x;
            if (Math.abs(d) >= 0.5) {
                try { wrap.scrollLeft = wrap.scrollLeft + d; } catch (e) {}
            }
        }

        return {
            init: init,
            state: function () { return v; },
            drop: function () { tailClear(table(), v && v.tail); v = null; styleEl().textContent = ''; onState(v); },
            isCollapsed: isCollapsed,
            revealAll: function () { var tl = v && v.tail; return tl ? tailRevealRange(0, tl.list.length) : false; },
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
            // every column that claims the path (QA F4) -- colOfPath names one
            colsOfPath: function (p) { return v && v.byPathAll ? (v.byPathAll[p] || []).slice() : []; },
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
        TAIL_MIN_CELLS: TAIL_MIN_CELLS,
        TAIL_BLOCK: TAIL_BLOCK,
        TAIL_KEEP: TAIL_KEEP,
        TAIL_STEP: TAIL_STEP,
    };
})();
