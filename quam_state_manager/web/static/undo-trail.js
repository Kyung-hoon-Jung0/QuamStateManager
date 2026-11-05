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
    var _shownOn = null;      // the page (path) the panel was last shown on
    var _follow = 0;          // when "go to field" sent the user to another page
    var FOLLOW_MS = 60000;    // a big chip's Live Edit takes tens of seconds

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
    /* T1-F2: the toast sink sits ABOVE an open panel (body.undo-trail-on +
       --undo-trail-h, style.css) -- a multi-line undo toast used to cover the
       panel's "go to field" button. Re-measured whenever the panel changes. */
    function liftToasts() {
        var on = !!(_panel && !_panel.hidden && document.body.contains(_panel));
        document.body.classList.toggle('undo-trail-on', on);
        if (on) {
            var h = Math.ceil(_panel.getBoundingClientRect().height || 0);
            document.body.style.setProperty('--undo-trail-h', h + 'px');
            keepClearOfActions();
        }
    }
    /* The panel must never cover an action button (a grid's Apply all sat
       under it). When a visible enabled button intersects the right-corner
       dock, the panel docks in the left corner of the main pane instead. */
    function _hits(a, b) {
        return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    }
    function _blocked(rect) {
        var btns = document.querySelectorAll('#main button, #main .btn, #main [role="button"]');
        for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            if (b.disabled || (_panel && _panel.contains(b))) continue;
            var r = b.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && _hits(rect, r)) return true;
        }
        return false;
    }
    function dock(p, left) {
        p.style.left = left != null ? left + 'px' : '';
        p.style.right = left != null ? 'auto' : '';
    }
    function keepClearOfActions() {
        var p = _panel;
        if (!p || p.hidden) return;
        dock(p, null);
        if (!_blocked(p.getBoundingClientRect())) return;
        var main = document.getElementById('main');
        dock(p, (main ? Math.max(0, main.getBoundingClientRect().left) : 0) + 13);
        // both corners blocked: the right corner stays (the user can hide it)
        if (_blocked(p.getBoundingClientRect())) dock(p, null);
    }
    var _clearTimer = null;
    window.addEventListener('scroll', function () {
        if (!_panel || _panel.hidden) return;
        clearTimeout(_clearTimer);
        _clearTimer = setTimeout(keepClearOfActions, 80);
    }, true);
    window.addEventListener('resize', function () { if (_panel && !_panel.hidden) keepClearOfActions(); });
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
            if (ev.target.closest && ev.target.closest('.undo-trail-close')) { _hidden = true; p.hidden = true; liftToasts(); return; }
            if (ev.target.closest && ev.target.closest('.undo-trail-clear')) { _steps = []; render(); }
        });
        document.body.appendChild(p);
        _panel = p;
        if (window.ResizeObserver) {
            try { new ResizeObserver(liftToasts).observe(p); } catch (e) {}
        }
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
        if (nav && nav.handle) {
            if (nav.handle([{ dot_path: dp }]) === 'page') _follow = Date.now();
            return;
        }
        if (window._navigateToExplorerPath) { _follow = Date.now(); window._navigateToExplorerPath(dp); }
    }

    function render() {
        var p = panel();
        var body = p.querySelector('.undo-trail-body');
        if (!_steps.length) { body.innerHTML = '<p class="muted undo-trail-empty">nothing undone yet</p>'; liftToasts(); return; }
        var h = '';
        _steps.forEach(function (st, i) {
            h += '<div class="undo-trail-step' + (i === 0 ? ' undo-trail-latest' : '') + '" data-kind="' + esc(st.kind) + '">';
            h += '<div class="undo-trail-kind">' + (st.kind === 'redo' ? '↷ redo' : st.kind === 'discard' ? '✕ discarded' : '↶ undo')
               + '<span class="muted"> · ' + esc(st.tier === 'memory' ? 'typed' : st.tier === 'live' ? '→ live' : 'staged')
               + (st.age ? ' · <span class="undo-trail-age">edit ' + esc(st.age) + '</span>' : '')
               + (st.label ? ' · ' + esc(st.label) : '') + '</span></div>';
            // LE-13: the step's own message, which used to be a separate toast
            if (st.note) h += '<p class="muted undo-trail-note">' + esc(st.note) + '</p>';
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
        liftToasts();
    }

    function push(step) {
        if (!step || !step.entries || !step.entries.length) return;
        step.at = Date.now();
        _steps.unshift(step);
        if (_steps.length > MAX) _steps.length = MAX;
        _hidden = false;
        var p = panel();
        p.hidden = false;
        _shownOn = location.pathname;
        render();
    }

    /* docs/301 F7: the panel is body-level, so it outlived a page change and
       sat over the next page's content (Trends' charts). Leaving the page
       hides it, as x does: the steps stay, and the next step brings it back.
       The page "go to field" opened is the exception -- the user went there
       from the panel. Deferred: a pane swap's own listeners may push the
       address after this one runs (_navigateTablePane). */
    function navigated() {
        setTimeout(function () {
            if (!_panel || _panel.hidden || !document.body.contains(_panel)) return;
            var here = location.pathname;
            if (here === _shownOn) return;            // the same page, re-rendered
            if (_follow && Date.now() - _follow < FOLLOW_MS) {
                _follow = 0;
                _shownOn = here;
                return;
            }
            _follow = 0;
            _panel.hidden = true;
            liftToasts();
        }, 0);
    }
    document.addEventListener('htmx:afterSwap', function (evt) {
        if (evt && evt.target && evt.target.id === 'table-pane') navigated();
    });
    /* Back/Forward swaps the whole body from htmx's snapshot, which can hold
       a COPY of this panel: markup without its listeners, its x dead. Only
       the live panel is ours (the restore detached it; the next step shows
       it again). */
    document.addEventListener('htmx:historyRestore', function () {
        Array.prototype.forEach.call(document.querySelectorAll('.undo-trail'), function (el) {
            if (el !== _panel) el.remove();
        });
    });

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
            // walk G19: a server step names the value it left, when it knows it
            return { dot_path: e.dot_path, value: val,
                     from: (e.from_value_disp != null && !e.deleted) ? e.from_value_disp : undefined };
        });
        // docs/160: the server says where the step landed -- on the live chip
        // (Ctrl+Z writes live) or only in the tray ("staged"). Before, every
        // server step read "staged", which after an apply was the whole reason
        // users thought Ctrl+Z was broken.
        var tier = d.live === true ? 'live' : 'server';
        // T1-F2: a journal step says how old the edit it undid is
        // LE-13: the head of the server's message ("Undone — in SM only, the
        // chip file is unchanged") is shown in the step; app.js then skips
        // the toast. A warning / error still toasts.
        var note = (d.level === 'warning' || d.level === 'error') ? null
            : (msg.split(': ')[0] || null);
        push({ kind: kind, tier: tier, entries: entries, label: d.tier_note || null,
               age: d.age || null, note: note });
    });
    // in-memory tier: LiveEditUndo announces each step
    document.addEventListener('quam:undo-step', function (evt) {
        var d = (evt && evt.detail) || {};
        push({ kind: d.kind || 'undo', tier: d.tier || 'memory', entries: d.entries || [], label: d.label || null });
    });
    // a full state replace makes the trail meaningless; a pull that only
    // moved values in place (W3-5: a node save over an undo, then restored)
    // leaves every step the user took still true, so the trail keeps them
    document.addEventListener('stateRestored', function (evt) {
        var d = (evt && evt.detail) || {};
        if (d.structural === false && Array.isArray(d.changes)) return;
        _steps = []; if (_panel) render();
    });

    return { push: push, goTo: goTo, steps: function () { return _steps.slice(); }, _render: render,
             _liftToasts: liftToasts };
})();
