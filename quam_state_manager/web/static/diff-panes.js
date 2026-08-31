/* The diff workbench's pane view (docs/141 4z): N sources side by side,
 * only the differing leaves, and a BASELINE the user picks by clicking a
 * pane title. The row set is baseline-independent (a row is listed when
 * any two sources differ), so a switch is a pure re-paint: every row
 * carries the PAIRWISE equality json_diff computed server-side (row
 * data-eq = "11000,11000,…" — one string per side, '1' at j iff side i
 * equals side j) and a cell is highlighted exactly when its row of that
 * matrix says it differs from the baseline column — the same rule that
 * decided the row exists, never a second equality in JavaScript. It is a
 * matrix and not a group id because the rule carries a float tolerance and
 * is therefore not transitive (docs/141 4ac). The Δ beside a highlighted
 * numeric cell is window.ValueDelta (docs/76), the one Δ implementation the
 * whole app shares.
 *
 * docs/141 4ai (user): the search box the page never had. Same grammar as
 * every other search in the app (window.SearchQuery — space = AND,
 * standalone | = OR, docs/96), filtering the rows the page already holds.
 * It serves the LIST view too, which had no search either.
 *
 * Bundled with the /diff page ('compare' bundle, base.html). Idempotent per
 * #diff-panes element; re-armed on every htmx swap of the workbench.
 */
(function () {
    'use strict';

    function each(list, fn) { Array.prototype.forEach.call(list, fn); }

    function paint(root, base) {
        var n = parseInt(root.getAttribute('data-n') || '0', 10) || 0;
        if (base < 0 || base >= n) base = 0;
        root.setAttribute('data-base', String(base));
        var heads = root.querySelectorAll('.dp-pane-head');
        each(heads, function (th) {
            th.classList.toggle('dp-base', parseInt(th.getAttribute('data-i'), 10) === base);
        });
        var rows = root.querySelectorAll('tr.dp-row');
        Array.prototype.forEach.call(rows, function (tr) {
            if (!tr.hasAttribute('data-eq')) return;     // a container row (4ab) carries no values
            var eq = (tr.getAttribute('data-eq') || '').split(',');
            var baseEq = eq[base] || '';
            var cells = tr.querySelectorAll('td.dp-cell');
            var baseCell = cells[base];
            var baseVal = baseCell ? baseCell.getAttribute('data-v') : null;
            var isPresent = function (td) { return !!td && !td.querySelector('.dp-absent'); };
            var basePresent = isPresent(baseCell);
            Array.prototype.forEach.call(cells, function (td, i) {
                var isBase = i === base;
                var same = !isBase && baseEq.charAt(i) === '1';
                td.classList.toggle('dp-base', isBase);
                td.classList.toggle('dp-diff', !isBase && !same);
                td.classList.toggle('dp-same', same);
                var d = td.querySelector('.dp-delta');
                if (!d) return;
                var present = isPresent(td);
                // a Δ only where the cell DIFFERS from the baseline: an equal
                // cell would read "0", which says nothing the highlight does not
                if (isBase || same || !present || !basePresent || baseVal === null || !td.hasAttribute('data-v') || !window.ValueDelta) {
                    d.textContent = ''; d.hidden = true; d.className = 'dp-delta';
                    return;
                }
                // ValueDelta.paint manages the chip classes on the element it
                // fills; keep .dp-delta as the layout hook
                d.className = 'dp-delta';
                d.innerHTML = window.ValueDelta.chipHtml(baseVal, td.getAttribute('data-v'));
                d.hidden = !d.innerHTML;
            });
        });
    }

    function syncUrl(base) {
        // the tab strip / view toggle / pickers were rendered with the OLD
        // baseline in their hx-get and hidden input -- carry the new one so a
        // tab switch or picker change keeps it
        // htmx captured each button's path at init, so rewriting hx-get is
        // not enough: the request itself is rewritten (htmx:configRequest
        // below) from #diff-root's data-base; the picker form's hidden
        // input is a plain form field and can simply be set.
        var root = document.getElementById('diff-root');
        if (root) {
            var hidden = root.querySelector('.diff-wb-pickers input[name="base"]');
            if (hidden) hidden.value = String(base);
            root.setAttribute('data-base', String(base));
        }
        try {
            var u = new URL(window.location.href);
            if (u.pathname !== '/diff') return;
            u.searchParams.set('base', String(base));
            window.history.replaceState(window.history.state, '', u.pathname + u.search);
        } catch (e) { /* a detached test realm has no URL to keep */ }
    }

    /* docs/141 4ab: the key tree. A container row (tr.dp-dir) toggles
       data-collapsed; a row is hidden exactly when ANY ancestor container is
       collapsed (walk data-parent up through a path -> row map) -- or, 4ai,
       when the search filtered it out. Depth buttons collapse every container
       at depth >= d. */
    function rowMap(root) {
        // A leaf that is ALSO a container on another side shares its path with
        // its own dir row, and that dir row is the one that can be collapsed:
        // keyed the other way round the walk would find the leaf, whose parent
        // is its own path, and spin until the guard (4ab, fixed in 4ai).
        var byPath = {};
        each(root.querySelectorAll('tr.dp-row'), function (tr) {
            var p = tr.getAttribute('data-path');
            if (!byPath[p] || tr.classList.contains('dp-dir')) byPath[p] = tr;
        });
        return byPath;
    }
    function applyVisibility(root) {
        var byPath = rowMap(root);
        var rows = root.querySelectorAll('tr.dp-row');
        each(rows, function (tr) {
            var hidden = tr.hasAttribute('data-nomatch');
            var p = tr.getAttribute('data-parent'), guard = 0;
            while (!hidden && p && guard++ < 64) {
                var anc = byPath[p];
                if (!anc || anc === tr) break;          // no such ancestor, or a self-loop
                if (anc.hasAttribute('data-collapsed')) { hidden = true; break; }
                p = anc.getAttribute('data-parent');
            }
            tr.hidden = hidden;
        });
        each(root.querySelectorAll('tr.dp-dir'), function (tr) {
            var t = tr.querySelector('.dp-toggle');
            var collapsed = tr.hasAttribute('data-collapsed');
            if (t) { t.setAttribute('aria-expanded', collapsed ? 'false' : 'true'); t.textContent = collapsed ? '▸' : '▾'; }
        });
    }
    function setDepth(root, depth) {
        each(root.querySelectorAll('tr.dp-dir'), function (tr) {
            var d = parseInt(tr.getAttribute('data-depth'), 10) || 0;
            if (d >= depth) tr.setAttribute('data-collapsed', '1'); else tr.removeAttribute('data-collapsed');
        });
        applyVisibility(root);
        each(root.querySelectorAll('.dp-depth'), function (b) {
            b.classList.toggle('outline', parseInt(b.getAttribute('data-depth'), 10) !== depth);
        });
    }

    /* ------------------------------------------------------------------
       docs/141 4ai — search.

       Every row the page holds is already in the DOM, so the filter is
       client-side and a keystroke costs no round trip. A leaf shows when
       SearchQuery's AND-of-OR groups all match its haystack; a container
       shows when a matching leaf is beneath it, and its count chip reports
       how many of its differing keys matched. The haystack is the leaf's dot
       path plus every pane's value in BOTH forms — the raw one (data-v) and
       the grouped one on screen — so 7003542323 and 7,003,542,323 find the
       same row. Values in panes are read whole: a row must never match on
       evidence that is not on screen, and here every pane is.

       A search auto-expands the containers on the way to a hit (a hit you
       cannot see is not a hit) and restores the collapse state it found when
       the box is cleared — unless the user collapsed something themselves
       meanwhile, in which case their state wins and nothing is restored.

       The query rides the URL (?q=) purely so a tab switch / picker change /
       "Show more" comes back with the box still filled; the server never
       filters by it (see routes.diff_view).
       ------------------------------------------------------------------ */

    function groupsOf(q) {
        if (!q) return [];
        return window.SearchQuery ? window.SearchQuery.groups(q) : [[q]];
    }
    function matchesHay(hay, grps) {
        if (!grps.length) return true;
        if (window.SearchQuery) return window.SearchQuery.matchesHay(hay, grps);
        for (var i = 0; i < grps.length; i++) {
            if (hay.indexOf(grps[i][0]) < 0) return false;
        }
        return true;
    }
    /** path + every value cell, raw and as rendered. Cached per row. */
    function hayOf(tr, cellSel) {
        if (tr._dpHay != null) return tr._dpHay;
        var parts = [tr.getAttribute('data-path') || ''];
        each(tr.querySelectorAll(cellSel), function (td) {
            var raw = td.getAttribute('data-v');
            if (raw != null) parts.push(raw);
            parts.push(td.textContent || '');
        });
        tr._dpHay = parts.join(' ').toLowerCase().replace(/\s+/g, ' ');
        return tr._dpHay;
    }
    function collapsedNow(root) {
        var out = [];
        each(root.querySelectorAll('tr.dp-dir[data-collapsed]'), function (tr) {
            out.push(tr.getAttribute('data-path'));
        });
        return out;
    }
    function restoreCollapsed(root, paths) {
        var want = {};
        for (var i = 0; i < paths.length; i++) want[paths[i]] = true;
        each(root.querySelectorAll('tr.dp-dir'), function (tr) {
            if (want[tr.getAttribute('data-path')]) tr.setAttribute('data-collapsed', '1');
            else tr.removeAttribute('data-collapsed');
        });
    }
    function setCount(root, text, none, title) {
        var el = root.querySelector('.dp-search-count');
        if (!el) return;
        el.textContent = text;
        if (title) el.setAttribute('title', title); else el.removeAttribute('title');
        el.classList.toggle('dp-search-none', !!none);
    }
    /** A filtered-to-nothing table is a header and blank space, which reads as
        broken. Say it where the user is looking — under the table, not only
        beside the box. */
    function emptyNote(root, q, kind) {
        var el = root._dpEmptyNote;
        if (!el && !q) return;
        if (!el) {
            el = document.createElement('p');
            el.className = 'muted dp-empty-note';
            var anchor = root.querySelector('.diff-panes-scroll') || root.querySelector('table');
            if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(el, anchor.nextSibling);
            else root.appendChild(el);
            root._dpEmptyNote = el;
        }
        el.hidden = !q;
        el.textContent = q ? ('No ' + kind + ' matches "' + q + '" — the search reads key paths and the values on screen.') : '';
    }
    /** "· N more rows are not loaded" — the page is paged, and a search that
        silently ignored the rest would be a lie. */
    function moreNote(root) {
        var more = parseInt(root.getAttribute('data-more') || '0', 10) || 0;
        return more > 0 ? ' · ' + more.toLocaleString() + ' more not loaded' : '';
    }

    function filterPanes(root, q) {
        q = (q == null ? '' : String(q)).trim();
        var prev = root._dpQ || '';
        root._dpQ = q;
        var leaves = root.querySelectorAll('tr.dp-row.dp-leaf');
        var dirs = root.querySelectorAll('tr.dp-row.dp-dir');
        if (!q) {
            each(leaves, function (tr) { tr.removeAttribute('data-nomatch'); });
            each(dirs, function (tr) {
                tr.removeAttribute('data-nomatch');
                var c = tr.querySelector('.dp-count');
                if (c && c.hasAttribute('data-count')) {
                    var full = c.getAttribute('data-count');
                    c.textContent = full;
                    c.setAttribute('title', full + ' differing key' + (full === '1' ? '' : 's') + ' inside');
                }
            });
            if (prev && root._dpRestore) restoreCollapsed(root, root._dpRestore);
            root._dpRestore = null;
            applyVisibility(root);
            setCount(root, '', false);
            emptyNote(root, '', 'key');
            return;
        }
        if (!prev) root._dpRestore = collapsedNow(root);
        var grps = groupsOf(q.toLowerCase());
        var byPath = rowMap(root);
        var hits = {}, shown = 0;
        each(leaves, function (tr) {
            if (!matchesHay(hayOf(tr, 'td.dp-cell'), grps)) {
                tr.setAttribute('data-nomatch', '1');
                return;
            }
            tr.removeAttribute('data-nomatch');
            shown++;
            var p = tr.getAttribute('data-parent'), guard = 0;
            while (p && guard++ < 64) {
                hits[p] = (hits[p] || 0) + 1;
                var anc = byPath[p];
                p = anc ? anc.getAttribute('data-parent') : '';
            }
        });
        each(dirs, function (tr) {
            var path = tr.getAttribute('data-path');
            var n = hits[path] || 0;
            if (n) {
                tr.removeAttribute('data-nomatch');
                tr.removeAttribute('data-collapsed');     // a hit you cannot see is not a hit
            } else {
                tr.setAttribute('data-nomatch', '1');
            }
            var c = tr.querySelector('.dp-count');
            if (c && c.hasAttribute('data-count')) {
                c.textContent = String(n);
                c.setAttribute('title', n + ' of ' + c.getAttribute('data-count') + ' differing keys match');
            }
        });
        applyVisibility(root);
        setCount(root,
            shown ? (shown.toLocaleString() + ' of ' + leaves.length.toLocaleString() + ' keys' + moreNote(root))
                  : ('no key matches' + moreNote(root)),
            !shown,
            shown ? '' : 'Nothing on this tab matches — the search reads key paths and the values in every pane.');
        emptyNote(root, shown ? '' : q, 'key');
    }

    /** The LIST view: flat rows, same grammar, no tree to keep. */
    function filterList(root, q) {
        q = (q == null ? '' : String(q)).trim();
        root._dpQ = q;
        var rows = root.querySelectorAll('table.diff-wb-list tbody > tr');
        var grps = groupsOf(q.toLowerCase());
        var shown = 0;
        each(rows, function (tr) {
            var ok = !q || matchesHay(hayOf(tr, 'td.diff-list-path, td.diff-list-a, td.diff-list-b'), grps);
            tr.hidden = !ok;
            if (ok) shown++;
        });
        setCount(root, q
            ? (shown ? shown.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' rows' + moreNote(root)
                     : 'no row matches' + moreNote(root))
            : '', !!q && !shown);
        emptyNote(root, (q && !shown) ? q : '', 'row');
    }

    function armSearch(root, filter) {
        var box = root.querySelector('.dp-search');
        if (!box) return;
        root.addEventListener('input', function (ev) {
            if (!ev.target || !ev.target.classList || !ev.target.classList.contains('dp-search')) return;
            filter(root, ev.target.value);
        });
        root.addEventListener('keydown', function (ev) {
            if (ev.key !== 'Escape' || !ev.target.classList || !ev.target.classList.contains('dp-search')) return;
            if (!ev.target.value) return;         // an empty box: let Escape be the app's
            ev.stopPropagation();
            ev.target.value = '';
            filter(root, '');
        });
        if (box.value) filter(root, box.value);   // a query carried in on the URL
    }

    function arm(root) {
        if (!root || root._dpArmed) return;
        root._dpArmed = true;
        root.addEventListener('click', function (ev) {
            var tgl = ev.target && ev.target.closest ? ev.target.closest('.dp-toggle') : null;
            if (tgl && root.contains(tgl)) {
                ev.preventDefault();
                var tr = tgl.closest('tr.dp-dir');
                if (tr) {
                    if (tr.hasAttribute('data-collapsed')) tr.removeAttribute('data-collapsed');
                    else tr.setAttribute('data-collapsed', '1');
                    root._dpRestore = null;        // the user's own state wins over the search's
                    applyVisibility(root);
                }
                return;
            }
            var dep = ev.target && ev.target.closest ? ev.target.closest('.dp-depth') : null;
            if (dep && root.contains(dep)) {
                ev.preventDefault();
                root._dpRestore = null;
                setDepth(root, parseInt(dep.getAttribute('data-depth'), 10) || 0);
                return;
            }
            var btn = ev.target && ev.target.closest ? ev.target.closest('.dp-pane-title') : null;
            if (!btn || !root.contains(btn)) return;
            ev.preventDefault();
            var i = parseInt(btn.getAttribute('data-i'), 10);
            if (isNaN(i)) return;
            paint(root, i);
            syncUrl(i);
        });
        armSearch(root, filterPanes);
    }

    function armList(root) {
        if (!root || root._dpArmed) return;
        root._dpArmed = true;
        armSearch(root, filterList);
    }

    function armAll() {
        each(document.querySelectorAll('#diff-panes'), arm);
        each(document.querySelectorAll('#diff-list'), armList);
    }

    window.DiffPanes = { arm: arm, armAll: armAll, paint: paint, applyVisibility: applyVisibility,
                         setDepth: setDepth, filter: filterPanes, filterList: filterList };

    // Every /diff request issued from inside the workbench (tab strip, view
    // toggle, show-more) carries the CURRENT baseline and search, whatever
    // its button was rendered with.
    document.addEventListener('htmx:configRequest', function (ev) {
        var d = ev.detail || {};
        if (!d.path || d.path.indexOf('/diff') !== 0 || !d.elt) return;
        var root = document.getElementById('diff-root');
        if (!root || !root.contains(d.elt)) return;
        var base = root.getAttribute('data-base');
        if (base != null && base !== '') {
            if (/[?&]base=\d+/.test(d.path)) d.path = d.path.replace(/([?&])base=\d+/, '$1base=' + base);
            if (d.parameters && d.parameters.base != null) d.parameters.base = base;
        }
        // q travels as a PARAMETER only (no button's URL carries one), so
        // htmx appends it exactly once.
        var box = root.querySelector('.dp-search');
        if (box && d.parameters) d.parameters.q = box.value || '';
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', armAll);
    else armAll();
    document.addEventListener('htmx:afterSwap', function () { armAll(); });
})();
