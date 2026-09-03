/* Undo trail (night session 2026-08-28, user-directed).
 *
 * Every Ctrl+Z / Ctrl+Shift+Z press is answered by a compact panel (bottom
 * right, body-level, closable) that names what just moved — path, the value
 * it went to, the value it came from — and carries ONE button, "go to field",
 * which is the only thing that navigates: it flashes the field if it is on
 * screen, else opens the surface that owns it (the inspector for one qubit /
 * pair, Live Edit for several, the Json tree otherwise) — the same routing
 * UndoNav uses, but on the user's press, not automatically.
 *
 * Two tiers feed it: the in-memory LiveEditUndo (typed-but-unstaged cells,
 * `quam:undo-step`) and the server journal (`cellsReverted`, driven by the
 * /undo and /redo responses). Nothing here re-renders a page; the trail is
 * the fast, always-visible answer while the surfaces repaint in place.
 */
window.UndoTrail = (function () {
    'use strict';
    var MAX = 8;
    var _steps = [];          // newest first: {kind, tier, entries:[{dot_path, value, from}], at}
    var _hidden = false;      // closed by the user for this page
    var _panel = null;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fmt(v) {
        if (v === null || v === undefined) return '—';
        if (typeof v === 'object') { try { return JSON.stringify(v); } catch (e) { return String(v); } }
        return String(v);
    }
    function panel() {
        if (_panel && document.body.contains(_panel)) return _panel;
        var p = document.createElement('div');
        p.id = 'undo-trail';
        p.className = 'undo-trail';
        p.setAttribute('role', 'log');
        p.setAttribute('aria-label', 'Undo trail');
        p.innerHTML = '<div class="undo-trail-head"><strong>Undo / Redo</strong>'
            + '<span class="undo-trail-tools"><button type="button" class="undo-trail-clear" title="Clear the list">clear</button>'
            + '<button type="button" class="undo-trail-close" title="Hide (reappears on the next Ctrl+Z)" aria-label="Hide">&times;</button></span></div>'
            + '<div class="undo-trail-body"></div>';
        p.addEventListener('click', function (ev) {
            var go = ev.target.closest && ev.target.closest('.undo-trail-goto');
            if (go) { ev.preventDefault(); goTo(go.getAttribute('data-path')); return; }
            if (ev.target.closest && ev.target.closest('.undo-trail-close')) { _hidden = true; p.hidden = true; return; }
            if (ev.target.closest && ev.target.closest('.undo-trail-clear')) { _steps = []; render(); }
        });
        document.body.appendChild(p);
        _panel = p;
        return p;
    }

    /* Navigate on demand: flash when visible, else open the owning surface. */
    function goTo(dp) {
        if (!dp) return;
        var nav = window.UndoNav;
        var el = nav && nav.visibleEl ? nav.visibleEl(dp) : null;
        if (el) {
            el.classList.add('leu-flash');
            setTimeout(function () { el.classList.remove('leu-flash'); }, 900);
            try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { el.scrollIntoView(); }
            if (el.focus) { try { el.focus({ preventScroll: true }); } catch (e) {} }
            return;
        }
        if (nav && nav.handle) { nav.handle([{ dot_path: dp }]); return; }
        if (window._navigateToExplorerPath) window._navigateToExplorerPath(dp);
    }

    function render() {
        var p = panel();
        var body = p.querySelector('.undo-trail-body');
        if (!_steps.length) { body.innerHTML = '<p class="muted undo-trail-empty">nothing undone yet</p>'; return; }
        var h = '';
        _steps.forEach(function (st, i) {
            h += '<div class="undo-trail-step' + (i === 0 ? ' undo-trail-latest' : '') + '" data-kind="' + esc(st.kind) + '">';
            h += '<div class="undo-trail-kind">' + (st.kind === 'redo' ? '↷ redo' : st.kind === 'discard' ? '✕ discarded' : '↶ undo')
               + '<span class="muted"> · ' + esc(st.tier === 'memory' ? 'typed' : st.tier === 'live' ? '→ live' : 'staged') + (st.label ? ' · ' + esc(st.label) : '') + '</span></div>';
            st.entries.slice(0, 6).forEach(function (e) {
                h += '<div class="undo-trail-row"><code class="undo-trail-path" title="' + esc(e.dot_path) + '">' + esc(e.dot_path) + '</code>'
                   + '<span class="undo-trail-vals">' + (e.from !== undefined ? '<span class="undo-trail-from">' + esc(fmt(e.from)) + '</span> → ' : '')
                   + '<span class="undo-trail-to">' + esc(fmt(e.value)) + '</span></span>'
                   + '<button type="button" class="btn-xs undo-trail-goto" data-path="' + esc(e.dot_path) + '" title="Show this field (flash it if visible, else open its surface)">go to field</button></div>';
            });
            if (st.entries.length > 6) h += '<div class="muted undo-trail-more">… ' + (st.entries.length - 6) + ' more</div>';
            h += '</div>';
        });
        body.innerHTML = h;
    }

    function push(step) {
        if (!step || !step.entries || !step.entries.length) return;
        step.at = Date.now();
        _steps.unshift(step);
        if (_steps.length > MAX) _steps.length = MAX;
        _hidden = false;
        var p = panel();
        p.hidden = false;
        render();
    }

    // server tier: the /undo and /redo responses (HX-Trigger cellsReverted)
    document.addEventListener('cellsReverted', function (evt) {
        var d = (evt && evt.detail) || {};
        var msg = String(d.message || '');
        var kind = /^Redid|^Redone/i.test(msg) ? 'redo' : /^Discard/i.test(msg) ? 'discard' : 'undo';
        var entries = (d.entries || []).filter(function (e) { return e && e.dot_path; }).map(function (e) {
            // a list's old_value_disp is the grid's cut preview (docs/159); the
            // trail names the real value
            var val = e.deleted ? '(deleted)'
                : (e.old_kind === 'list' && e.old_value_json != null) ? e.old_value_json
                : (e.old_value_disp != null ? e.old_value_disp : e.old_value_str);
            return { dot_path: e.dot_path, value: val, from: undefined };
        });
        // docs/160: the server says where the step landed -- on the live chip
        // (Ctrl+Z writes live) or only in the tray ("staged"). Before, every
        // server step read "staged", which after an apply was the whole reason
        // users thought Ctrl+Z was broken.
        var tier = d.live === true ? 'live' : 'server';
        push({ kind: kind, tier: tier, entries: entries, label: d.tier_note || null });
    });
    // in-memory tier: LiveEditUndo announces each step
    document.addEventListener('quam:undo-step', function (evt) {
        var d = (evt && evt.detail) || {};
        push({ kind: d.kind || 'undo', tier: d.tier || 'memory', entries: d.entries || [], label: d.label || null });
    });
    // a full state replace makes the trail meaningless
    document.addEventListener('stateRestored', function () { _steps = []; if (_panel) render(); });

    return { push: push, goTo: goTo, steps: function () { return _steps.slice(); }, _render: render };
})();
