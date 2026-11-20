/* docs/167 — the sync pill carries the notifications, as a STATE.
 *
 * The user's directive, verbatim in translation: "it must not happen too often
 * — e.g. it must not pop up every time an experiment run finishes. Rather…
 * the Sync button turns blue/rounded? Anyway a small visible mark on the sync
 * button is enough."
 *
 * That is a request to DELETE something, first of all. `#new-run-popup` was
 * already firing a card with a 7-second auto-dismiss once per detected run,
 * on every page. A hundred finished runs made a hundred cards.
 *
 * So: no toast, no modal, no sound. One chip beside the existing status pill,
 * carrying a COUNT rather than a stream of events — a hundred runs read
 * "100 new", once. The popup card survives as what the chip OPENS, which is
 * the same information on demand instead of as an interruption.
 *
 * Two announcements ship, and the reasons the other two did not are worth
 * recording because they are about honesty rather than effort:
 *   - `live changed` was cut: /state/drift's refresh only ever RAISES the
 *     flag on a dirty context (QA diagnostics-r2-12; it cannot lower it
 *     there), so the 5-second poll cannot keep it current, and a
 *     chip that is right only sometimes is worse than no chip. The pill's own
 *     server-rendered `state-status-drifted` state already covers the clean
 *     case honestly.
 *   - autofit's `needs_human` was cut: the engine flag is level-triggered with
 *     no clearing path, so the chip could be shown but never legitimately
 *     dismissed. A per-tab suppression latch would be a lie about a robot that
 *     is still waiting. It needs a server-side clear first.
 *
 * The chip is created LAZILY and removed when nothing is pending, so a page
 * with nothing to say renders exactly the markup it rendered before — the
 * `_ensureNewPill` pattern from dataset-virtual.js, not a hidden element.
 */
(function () {
    'use strict';
    if (window.SyncBadge) return;

    var KINDS = {
        // kind -> {text(payload), title, cls}. Order below is precedence.
        rundone: {
            cls: 'sync-note-run',
            text: function () { return 'run done'; },
            title: 'The experiment queue finished — click to open the runner'
        },
        'new': {
            cls: 'sync-note-new',
            text: function (p) {
                return (p && p.count ? p.count + ' new' : 'new runs');
            },
            // docs/170: the click REFRESHES the run lists (the sidebar's ↻ and,
            // on the Datasets page, its Rescan) -- what the user does by hand
            // to see them. No card: after the refresh the runs are simply there.
            title: 'Runs landed since you last looked; click to refresh the run lists'
        }
    };
    var ORDER = ['rundone', 'new'];

    var pending = {};       // kind -> payload
    var onAck = {};         // kind -> function(payload), set by the feeders

    function tray() { return document.getElementById('pending-tray'); }
    function pill() {
        var t = tray();
        return t ? t.querySelector('.state-status-badge') : null;
    }

    // One rule for "the page already shows this run", used by this chip and by
    // the Datasets "new runs" bar: its row is on screen in the Datasets table,
    // or its sidebar row intersects the viewport with every ancestor group open.
    function listedInTree(uid) {
        // A refresh can expose rows before its scroll anchor is restored, before paint.
        var scroll = window.SidebarScroll;
        if (scroll && scroll.isRestoring && scroll.isRestoring()) return false;
        var tree = document.getElementById('sidebar-tree');
        if (!tree || uid == null) return false;
        var q = (window.CSS && window.CSS.escape) ? window.CSS.escape(String(uid)) : String(uid).split('"').join('');
        var e = tree.querySelector('.tree-entry-click[data-uid="' + q + '"]');
        if (!e) return false;
        var d = e.closest('details');
        while (d) {
            if (!d.open) return false;
            d = d.parentElement ? d.parentElement.closest('details') : null;
        }
        var r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        var top = 0, bottom = window.innerHeight, left = 0, right = window.innerWidth;
        for (var p = e.parentElement; p; p = p.parentElement) {
            var css = window.getComputedStyle(p);
            if (/(auto|scroll|hidden|clip)/.test(css.overflow + css.overflowY + css.overflowX)) {
                var box = p.getBoundingClientRect();
                top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
                left = Math.max(left, box.left); right = Math.min(right, box.right);
            }
        }
        return r.bottom > top && r.top < bottom && r.right > left && r.left < right;
    }
    function runShown(uid) {
        var dv = window.DatasetVirtual;
        if (dv && dv.isOnScreen && dv.isOnScreen(uid)) return true;
        return listedInTree(uid);
    }

    // A counted run the page has shown stays acknowledged (closing its group
    // later does not count it again); the chip counts only the rest.
    var shownNew = {};
    function unseenCount(p) {
        var uids = (p && p.uids) || [], shown = 0;
        for (var i = 0; i < uids.length; i++) {
            if (shownNew[uids[i]] || runShown(uids[i])) { shownNew[uids[i]] = true; shown++; }
        }
        return Math.max(0, p.count - shown);
    }

    function render() {
        var host = tray();
        var anchor = pill();
        var el = host ? host.querySelector('.state-status-notice') : null;
        var kind = null, shownPayload = null;
        for (var i = 0; i < ORDER.length; i++) {
            var p = pending[ORDER[i]];
            if (!p) continue;
            if (ORDER[i] === 'new' && typeof p.count === 'number') {
                var c = unseenCount(p);
                if (!c) continue;          // every counted run is already on the page
                p = { count: c, uids: p.uids, run: p.run };
            }
            kind = ORDER[i]; shownPayload = p; break;
        }
        if (!kind || !host || !anchor) {
            if (el && el.parentNode) el.parentNode.removeChild(el);
            document.dispatchEvent(new CustomEvent('sm:runs-seen'));
            return;
        }
        if (!el) {
            // A sibling of the pill, never a child: the pill IS a button element
            // whose click opens the Review tray, and a chip that means
            // something else cannot live inside it.
            el = document.createElement('button');
            el.type = 'button';
            el.className = 'state-status-notice';
            el.addEventListener('click', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var k = el.getAttribute('data-kind');
                var payload = pending[k];
                clear(k);
                if (onAck[k]) { try { onAck[k](payload); } catch (e) {} }
            });
            // sync-ux 2026-09-25: the pill sits inside the status control's split
            // button now; the chip goes after the WHOLE control
            var after = (anchor.closest && anchor.closest('.sync-control')) || anchor;
            after.parentNode.insertBefore(el, after.nextSibling);
        }
        var spec = KINDS[kind];
        el.setAttribute('data-kind', kind);
        el.className = 'state-status-notice ' + spec.cls;
        var txt = spec.text(shownPayload);
        if (el.textContent !== txt) el.textContent = txt;
        document.dispatchEvent(new CustomEvent('sm:runs-seen'));
        el.title = kind === 'new' && typeof shownPayload.count === 'number'
            ? shownPayload.count + (shownPayload.count === 1 ? ' run' : ' runs')
                + ' landed since you last looked' : spec.title;
    }

    function note(kind, payload) {
        if (!KINDS[kind]) return;
        pending[kind] = payload || {};
        render();
    }
    function clear(kind) {
        if ((!kind || kind === 'new') && pending['new']) {
            (pending['new'].uids || []).forEach(function (uid) { shownNew[uid] = true; });
        }
        if (kind) delete pending[kind]; else pending = {};
        // Seen identities survive a refresh and a later poll of the same runs.
        render();
    }
    function acknowledge(uids) {
        (uids || []).forEach(function (uid) { shownNew[uid] = true; });
        render();
    }
    var latestRun = null;
    function lastRun(data) {
        if (data) latestRun = data.uid ? data : null;
        var r = latestRun;
        var text = r ? ['last:', '#' + r.run_id, r.experiment_name,
            (r.qubits || []).join(','), (r.time || '').slice(0, 5),
            r.indexing ? '(indexing)' : ''].filter(Boolean).join(' ') : '';
        document.querySelectorAll('[data-last-run]').forEach(function (el) {
            el.textContent = text.trim(); el.title = text.trim(); el.hidden = !r;
        });
    }
    function ackHandler(kind, fn) { onAck[kind] = fn; }

    // The tray is OOB-swapped on every mutation, which destroys the chip. Both
    // swap paths are covered: htmx's own event, and the hand-rolled outerHTML
    // assignment in _swapPendingTray (which does not fire htmx:afterSwap, the
    // reason _restoreTrayState exists at all).
    document.body && document.body.addEventListener('htmx:afterSwap', function (ev) {
        var t = ev && ev.target;
        if (t && (t.id === 'pending-tray' || (t.querySelector && t.querySelector('#pending-tray')))) render();
    });
    document.addEventListener('sm:tray-swapped', render);
    window.addEventListener('resize', render);
    document.addEventListener('htmx:afterSwap', function () { lastRun(); });
    document.addEventListener('scroll', function () { if (pending['new']) render(); }, true);
    // The sidebar listing a counted run, or opening the group that holds it,
    // shows it: count again.
    document.addEventListener('htmx:afterSwap', function (ev) {
        var t = ev && ev.detail && ev.detail.target;
        if (pending['new'] && t && t.closest && t.closest('#sidebar-tree')) render();
    });
    document.addEventListener('toggle', function (ev) {
        var t = ev && ev.target;
        if (pending['new'] && t && t.closest && t.closest('#sidebar-tree')) render();
    }, true);

    window.SyncBadge = {
        note: note, clear: clear, onAck: ackHandler, render: render, runShown: runShown,
        lastRun: lastRun, acknowledge: acknowledge,
        isSeen: function (uid) { return !!shownNew[uid]; },
        _pending: function () { return pending; }
    };
})();
