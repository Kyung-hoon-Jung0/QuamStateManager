/* lab-check.js -- "checking with your class's own code..." for EVERY editing door.
 *
 * 2026-09-27: /field/edit and /field/edit-batch (Live Edit grids, Json Tree,
 * plot-click popup, paste / fill-down batches) now ask a LAB pulse class's own
 * code before writing a value that lands in one of its pulses (routes.py
 * _lab_write_refusal). That answer runs a subprocess in the lab env and can
 * take seconds, so the edit must not look frozen: the cell says it is being
 * checked while the answer is on its way, and a refusal is named in place.
 *
 * One seam instead of a patch per surface: window.fetch is wrapped ONCE. For a
 * POST to /field/edit(-batch) it asks GET-cheap POST /field/lab-watch (a cached
 * structure map, never a subprocess) in PARALLEL -- a non-pulse edit is never
 * delayed by it. Only when the server says "lab" AND the edit is still
 * unanswered does the badge appear, anchored to the edited cell. The wrapped
 * promise is returned untouched: callers see exactly the response they saw
 * before. The surfaces keep their own refusal rendering (row error slot, tree
 * chip); the badge adds the one sentence every surface shares.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || window.LabCheck) return;
    var _orig = window.fetch;
    if (typeof _orig !== 'function') return;

    var SHOW_AFTER_MS = 0;       // the watch answer IS the gate
    var REFUSED_LIFE_MS = 9000;
    var _lastRow = null;         // the row/cell the user last edited in

    function _rowOf(el) {
        if (!el || !el.closest) return null;
        return el.closest('.tree-row, td, .bulk-td, tr, .pulse-field, label, li') || el;
    }
    try {
        document.addEventListener('focusin', function (e) {
            var r = _rowOf(e.target);
            if (r) _lastRow = r;
        }, true);
    } catch (e) { /* no document (worker) */ }

    function _cssEsc(s) {
        return (window.CSS && CSS.escape) ? CSS.escape(s)
            : String(s).replace(/["\\]/g, '\\$&');
    }

    function _pathsOf(init) {
        var body = init && init.body, out = [];
        if (!body) return out;
        try {
            if (typeof body === 'string') {
                var t = body.trim();
                if (t.charAt(0) === '{') {
                    var j = JSON.parse(t);
                    (j.updates || []).forEach(function (u) {
                        if (u && typeof u.dot_path === 'string') out.push(u.dot_path);
                    });
                    if (typeof j.dot_path === 'string') out.push(j.dot_path);
                    return out;
                }
                body = new URLSearchParams(t);
            }
            if (body.getAll) return body.getAll('dot_path').filter(Boolean);
        } catch (e) { /* unreadable body: no badge, the edit is untouched */ }
        return out;
    }

    function _anchorFor(paths) {
        for (var i = 0; i < paths.length; i++) {
            var sel = '[data-dot-path="' + _cssEsc(paths[i]) + '"]';
            var els = document.querySelectorAll(sel);
            for (var k = 0; k < els.length; k++) {
                var el = els[k];
                if (el.getClientRects && el.getClientRects().length) return el;
            }
        }
        if (_lastRow && _lastRow.isConnected) return _lastRow;
        var ae = document.activeElement;
        return (ae && ae !== document.body) ? ae : null;
    }

    function _place(badge, anchor) {
        var r = anchor && anchor.isConnected && anchor.getBoundingClientRect
            ? anchor.getBoundingClientRect() : null;
        if (!r || (!r.width && !r.height)) {
            badge.style.left = '50%'; badge.style.top = '64px';
            badge.style.transform = 'translateX(-50%)';
            return;
        }
        var vw = window.innerWidth || 1024;
        badge.style.transform = '';
        badge.style.left = Math.max(4, Math.min(r.left, vw - 380)) + 'px';
        badge.style.top = Math.max(4, r.bottom + 2) + 'px';
    }

    function _badge(anchor) {
        var b = document.createElement('div');
        b.className = 'lab-check-badge';
        b.setAttribute('role', 'status');
        b.setAttribute('aria-live', 'polite');
        b.textContent = "checking with your class's own code\u2026";
        document.body.appendChild(b);
        b._anchor = anchor;
        _place(b, anchor);
        var onScroll = function () { _place(b, anchor); };
        window.addEventListener('scroll', onScroll, true);
        b._unbind = function () { window.removeEventListener('scroll', onScroll, true); };
        return b;
    }

    function _settle(badge, resp) {
        if (!badge) return;
        var done = function () {
            if (badge._unbind) badge._unbind();
            if (badge.parentNode) badge.parentNode.removeChild(badge);
        };
        if (!resp || resp.status !== 400 || !resp.clone) { done(); return; }
        resp.clone().json().then(function (j) {
            if (!j || !j.lab_refused) { done(); return; }
            // Compact: the surface names the full reason itself (row error
            // slot, tree chip); the badge says WHO refused and the class's own
            // first words, and must not bury the cell the user is fixing.
            var full = String(j.error || 'refused by your pulse class');
            var why = full.replace(/^.*?nothing was written:\s*/, '');
            if (why.length > 110) why = why.slice(0, 107) + '\u2026';
            badge.classList.add('lab-check-refused');
            badge.textContent = '\u2717 refused by your ' + (/^Your gate /.test(full) ? 'gate' : 'pulse class') + ' \u2014 nothing written: ' + why;
            badge.title = full + '\n(click to dismiss)';
            badge.onclick = done;
            // A coupled pair (a lab GATE's two pulses must share a field):
            // the server names the other field(s) that must follow; one press
            // sets them all in ONE batch (one check, one Ctrl+Z).
            var follow = Array.isArray(j.lab_follow) ? j.lab_follow : null;
            if (follow && follow.length > 1) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'lab-check-follow';
                var others = follow.slice(1).map(function (u) {
                    return String(u.dot_path).split('.').slice(-2).join('.');
                }).join(', ');
                btn.textContent = 'Set ' + others + ' too (' + follow.length + ' fields, one batch)';
                btn.title = follow.map(function (u) {
                    return u.dot_path + ' = ' + JSON.stringify(u.value);
                }).join('\n');
                btn.onclick = function (ev) {
                    ev.stopPropagation();
                    btn.disabled = true;
                    _setBoth(follow).then(done, done);
                };
                badge.appendChild(document.createTextNode(' '));
                badge.appendChild(btn);
                badge.onclick = null;
                badge.title = full;
                badge._keep = true;      // the offer stays until pressed / dismissed
            }
            // the surface's own error markup can move the cell: re-anchor
            var raf = window.requestAnimationFrame || function (f) { return setTimeout(f, 16); };
            raf(function () { raf(function () { _place(badge, badge._anchor); }); });
            setTimeout(function () { if (!badge._keep) done(); }, REFUSED_LIFE_MS);
        }, done);
    }

    function _setBoth(updates) {
        // through window.fetch, so the badge shows while the class checks it
        return window.fetch('/field/edit-batch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates: updates, group: 'new' })
        }).then(function (r) {
            return r.json().then(function (j) { return [r, j]; });
        }).then(function (rj) {
            var r = rj[0], j = rj[1] || {};
            if (j.tray_html && window._swapPendingTray) {
                try { window._swapPendingTray(j.tray_html); } catch (e) { /* next poll */ }
            }
            if (!r.ok) {
                if (window.showToast) window.showToast(String(j.error || 'not written'), 'error');
                return;
            }
            // repaint every written cell / input by path; it stays PENDING
            // (PendingMarkers.followPending: the red box + its baseline)
            var was = {};
            (j.modified || []).forEach(function (m) {
                if (m && m.resolved_path) was[m.resolved_path] = m.old_display;
            });
            var entries = (j.results || []).filter(function (x) { return x && x.applied; })
                .map(function (x) {
                    var dp = x.resolved_path || x.dot_path;
                    return { dot_path: dp,
                             old_value_disp: String(x.display != null ? x.display : x.new_value),
                             old_kind: 'num', still_pending: true, pending: true,
                             pending_old_disp: was[dp] != null ? was[dp] : null };
                });
            // the refusal the grid row still shows is answered now
            entries.forEach(function (e) {
                var sel = '[data-dot-path="' + _cssEsc(e.dot_path) + '"]';
                [].forEach.call(document.querySelectorAll(sel), function (c) {
                    var row = c.closest && c.closest('tr');
                    var er = row && row.querySelector('.bulk-row-error');
                    if (er) { er.textContent = ''; er.hidden = true; }
                    if (c.classList) c.classList.remove('bulk-cell-bad');
                });
            });
            try {
                document.dispatchEvent(new CustomEvent('cellsReverted', { detail: {
                    entries: entries,
                    message: 'Set ' + entries.length + ' fields together (one Ctrl+Z)' } }));
                document.body.dispatchEvent(new CustomEvent('pulses-changed', { bubbles: true }));
            } catch (e) { /* the values are written regardless */ }
        });
    }

    function _watch(respP, init) {
        var paths = _pathsOf(init);
        if (!paths.length) return;
        var settled = false, resp = null, badge = null;
        var anchor = _anchorFor(paths);
        respP.then(function (r) { settled = true; resp = r; _settle(badge, r); },
                   function () { settled = true; _settle(badge, null); });
        _orig.call(window, '/field/lab-watch', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: paths.slice(0, 4000) })
        }).then(function (r) { return r.json(); }).then(function (j) {
            if (!j || !j.lab || settled) return;
            setTimeout(function () {
                if (settled) return;
                badge = _badge(anchor);
            }, SHOW_AFTER_MS);
        }).catch(function () { /* no badge; the edit itself is untouched */ });
    }

    function _isEditPost(input, init) {
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        var m = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
        if (m !== 'POST') return false;
        url = url.replace(/^https?:\/\/[^/]+/, '');
        return /^\/field\/edit(-batch)?(\?|$)/.test(url);
    }

    window.fetch = function (input, init) {
        var p = _orig.apply(this, arguments);
        try {
            if (_isEditPost(input, init)) _watch(p, init);
        } catch (e) { /* the badge is a courtesy, never a failure */ }
        return p;
    };
    window.LabCheck = { _pathsOf: _pathsOf, _isEditPost: _isEditPost, _setBoth: _setBoth };
})();
