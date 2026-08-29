/* The diff workbench's pane view (docs/141 4z): N sources side by side,
 * only the differing leaves, and a BASELINE the user picks by clicking a
 * pane title. The row set is baseline-independent (a row is listed when
 * any two sources differ), so a switch is a pure re-paint: every cell
 * carries the equality group json_diff assigned server-side (row
 * data-groups = "g0,g1,…", -1 = absent) and is highlighted exactly when
 * its group differs from the baseline column's group — the same rule that
 * decided the row exists, never a second equality in JavaScript. The Δ
 * beside a highlighted numeric cell is window.ValueDelta (docs/76), the
 * one Δ implementation the whole app shares.
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
            var groups = (tr.getAttribute('data-groups') || '').split(',').map(function (g) { return parseInt(g, 10); });
            var cells = tr.querySelectorAll('td.dp-cell');
            var baseCell = cells[base];
            var baseVal = baseCell ? baseCell.getAttribute('data-v') : null;
            var basePresent = groups[base] !== -1;
            Array.prototype.forEach.call(cells, function (td, i) {
                var isBase = i === base;
                var same = !isBase && groups[i] === groups[base];
                td.classList.toggle('dp-base', isBase);
                td.classList.toggle('dp-diff', !isBase && !same);
                td.classList.toggle('dp-same', same);
                var d = td.querySelector('.dp-delta');
                if (!d) return;
                var present = groups[i] !== -1;
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

    function arm(root) {
        if (!root || root._dpArmed) return;
        root._dpArmed = true;
        root.addEventListener('click', function (ev) {
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

    window.DiffPanes = { arm: arm, armAll: armAll, paint: paint };

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
