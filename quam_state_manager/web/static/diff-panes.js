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
 * Bundled with the /diff page ('compare' bundle, base.html). Idempotent per
 * #diff-panes element; re-armed on every htmx swap of the workbench.
 */
(function () {
    'use strict';

    function paint(root, base) {
        var n = parseInt(root.getAttribute('data-n') || '0', 10) || 0;
        if (base < 0 || base >= n) base = 0;
        root.setAttribute('data-base', String(base));
        var heads = root.querySelectorAll('.dp-pane-head');
        Array.prototype.forEach.call(heads, function (th) {
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
       collapsed (walk data-parent up through a path -> row map). Depth
       buttons collapse every container at depth >= d. */
    function applyVisibility(root) {
        var byPath = {};
        var rows = root.querySelectorAll('tr.dp-row');
        // docs/141 4ac: a CONTAINER row always wins the map. The server gives
        // every row a unique data-path now, but keep the rule: a duplicate key
        // must never let a leaf shadow the container whose toggle owns it.
        Array.prototype.forEach.call(rows, function (tr) {
            var k = tr.getAttribute('data-path');
            if (!byPath[k] || tr.classList.contains('dp-dir')) byPath[k] = tr;
        });
        Array.prototype.forEach.call(rows, function (tr) {
            var p = tr.getAttribute('data-parent'), hidden = false, guard = 0;
            while (p && guard++ < 64) {
                var anc = byPath[p];
                if (!anc || anc === tr) break;          // no such ancestor, or a self-loop
                if (anc.hasAttribute('data-collapsed')) { hidden = true; break; }
                p = anc.getAttribute('data-parent');
            }
            tr.hidden = hidden;
        });
        Array.prototype.forEach.call(root.querySelectorAll('tr.dp-dir'), function (tr) {
            var t = tr.querySelector('.dp-toggle');
            var collapsed = tr.hasAttribute('data-collapsed');
            if (t) { t.setAttribute('aria-expanded', collapsed ? 'false' : 'true'); t.textContent = collapsed ? '▸' : '▾'; }
        });
    }
    function setDepth(root, depth) {
        Array.prototype.forEach.call(root.querySelectorAll('tr.dp-dir'), function (tr) {
            var d = parseInt(tr.getAttribute('data-depth'), 10) || 0;
            if (d >= depth) tr.setAttribute('data-collapsed', '1'); else tr.removeAttribute('data-collapsed');
        });
        applyVisibility(root);
        Array.prototype.forEach.call(root.querySelectorAll('.dp-depth'), function (b) {
            b.classList.toggle('outline', parseInt(b.getAttribute('data-depth'), 10) !== depth);
        });
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
                    applyVisibility(root);
                }
                return;
            }
            var dep = ev.target && ev.target.closest ? ev.target.closest('.dp-depth') : null;
            if (dep && root.contains(dep)) {
                ev.preventDefault();
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
    }

    function armAll() {
        var roots = document.querySelectorAll('#diff-panes');
        Array.prototype.forEach.call(roots, arm);
    }

    window.DiffPanes = { arm: arm, armAll: armAll, paint: paint, applyVisibility: applyVisibility, setDepth: setDepth };

    // Every /diff request issued from inside the workbench (tab strip, view
    // toggle, show-more) carries the CURRENT baseline, whatever its button
    // was rendered with.
    document.addEventListener('htmx:configRequest', function (ev) {
        var d = ev.detail || {};
        if (!d.path || d.path.indexOf('/diff') !== 0 || !d.elt) return;
        var root = document.getElementById('diff-root');
        if (!root || !root.contains(d.elt)) return;
        var base = root.getAttribute('data-base');
        if (base == null || base === '') return;
        if (/[?&]base=\d+/.test(d.path)) d.path = d.path.replace(/([?&])base=\d+/, '$1base=' + base);
        if (d.parameters && d.parameters.base != null) d.parameters.base = base;
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', armAll);
    else armAll();
    document.addEventListener('htmx:afterSwap', function () { armAll(); });
})();
