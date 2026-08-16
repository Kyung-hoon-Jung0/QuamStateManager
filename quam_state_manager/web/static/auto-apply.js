/* docs/117 — the auto-apply flusher.
 *
 * When the session is armed, an edit that reaches the working state must reach
 * the LIVE chip without a second press. The write itself is not reimplemented
 * here: `/state/apply-to-live` is still the only thing in SM that writes the
 * live files, and this file is what presses it. That is exactly what the
 * amended covenant says, so the code and the promise cannot drift apart.
 *
 * Why a MutationObserver on the tray rather than hooks in each edit surface:
 * every commit path in the app already ends in a `#pending-tray` swap, and
 * there are THREE different mechanisms doing it — `_swapPendingTray`'s
 * hand-rolled outerHTML replace (app.js), declarative htmx swaps, and OOB
 * swaps from the inspector routes (which htmx announces on `detail.elts`, NOT
 * on the tray, so an event listener alone would miss them). One observer sees
 * all three, and no existing file has to change.
 *
 * Timing is the user's explicit choice: flush IMMEDIATELY (0 ms), and while a
 * flush is in flight, coalesce everything that arrives into exactly one more.
 * A single edit is therefore never delayed, and tabbing across ten rows cannot
 * queue ten live writes.
 */
(function () {
    'use strict';

    var LOG_KEY = 'quam_applied_log_open';
    var _queued = false;      // edits arrived while a flush was in flight
    var _stopped = false;     // a disarm signal landed; stop scheduling
    var _observer = null;

    function tray() { return document.getElementById('pending-tray'); }

    function armed() {
        var t = tray();
        return !!(t && t.getAttribute('data-auto-apply') === '1');
    }

    function pending() {
        var t = tray();
        if (!t) return false;
        var n = parseInt(t.getAttribute('data-change-count') || '0', 10);
        return (n > 0) || t.getAttribute('data-working-dirty') === '1';
    }

    // The shared double-submit guard app.js already uses for doStateSync /
    // applyEditsToLive / overwriteLiveWithWorking. Reusing it is what makes a
    // manual Apply and an auto flush unable to race each other.
    function inFlight() { return !!window._applyInFlight; }

    function flush() {
        if (_stopped || !armed() || !pending()) return;
        if (inFlight()) { _queued = true; return; }
        if (!window.htmx) return;
        window._applyInFlight = true;
        var done = function () {
            window._applyInFlight = false;
            if (_queued) { _queued = false; setTimeout(flush, 0); }
        };
        try {
            var p = window.htmx.ajax('POST', '/state/apply-to-live', {
                source: tray(), target: '#pending-tray', swap: 'outerHTML',
            });
            if (p && typeof p.then === 'function') { p.then(done, done); }
            else { setTimeout(done, 0); }
        } catch (e) {
            window._applyInFlight = false;
        }
    }

    function onTrayChanged() {
        // A disarmed tray is the SERVER agreeing that the session is over —
        // that, and only that, releases the stop. (Clearing it on a timer
        // would re-open the window the disarm exists to close: a stale tray
        // still carrying the attribute would schedule another write.)
        if (!armed()) { _stopped = false; _queued = false; return; }
        if (_stopped) return;
        if (!pending()) { _queued = false; return; }
        flush();
    }

    function toast(msg, level) {
        if (window.showToast) { window.showToast(msg, level || 'warning'); return; }
        var bar = document.getElementById('status-bar');
        if (bar) {
            bar.innerHTML = '<div class="toast toast-' + (level || 'warning')
                + '"><p>' + msg + '</p></div>';
        }
    }

    function observe() {
        var host = document.getElementById('topbar-tray-slot') || document.body;
        if (_observer) _observer.disconnect();
        _observer = new MutationObserver(function () { onTrayChanged(); });
        _observer.observe(host, {
            childList: true, subtree: true, attributes: true,
            attributeFilter: ['data-change-count', 'data-working-dirty',
                              'data-auto-apply', 'data-seq'],
        });
    }

    function applyLogState() {
        var log = document.getElementById('applied-log');
        if (!log) return;
        var open = true;
        try { open = sessionStorage.getItem(LOG_KEY) !== '0'; } catch (e) { /* private mode */ }
        log.classList.toggle('applied-log-collapsed', !open);
    }

    window.AutoApply = {
        // exposed for the selfcheck + for app.js-free testing
        _flush: flush,
        _onTrayChanged: onTrayChanged,
        armed: armed,
        pending: pending,
        /* Someone ELSE held window._applyInFlight and has just released it.
           `_queued` is drained only by this module's own completion handler, so
           without a poke an edit that committed during (say) an Auto-Sync pull
           would sit unapplied until the next tray mutation — while the pill
           still said auto-push was on. docs/120 item 8 review finding. */
        drain: function () {
            if (_queued) { _queued = false; setTimeout(flush, 0); }
            else flush();
        },
        toggleLog: function (btn) {
            var log = document.getElementById('applied-log');
            if (!log) return;
            var collapsed = log.classList.toggle('applied-log-collapsed');
            try { sessionStorage.setItem(LOG_KEY, collapsed ? '0' : '1'); } catch (e) { /* ignore */ }
            if (btn) btn.innerHTML = collapsed ? '&#9656;' : '&#9662;';
        },
        // A disarm from the server is authoritative: it has already forgotten
        // the session, so the client must stop scheduling even if a stale tray
        // still carries the attribute for one render.
        _disarm: function (reason) {
            // Held until a tray renders WITHOUT the armed attribute (see
            // onTrayChanged) — never released on a timer.
            _stopped = true;
            _queued = false;
            if (reason === 'conflict') {
                toast('The live chip changed — auto-apply is OFF. '
                      + 'Choose how to resolve it.', 'warning');
            } else if (reason) {
                toast('Auto-apply is OFF (' + reason + ').', 'warning');
            }
        },
    };

    document.addEventListener('autoApplyDisarm', function (e) {
        var reason = (e && e.detail && e.detail.reason) || '';
        window.AutoApply._disarm(reason);
    });
    // htmx fires a plain (detail-less) event for string triggers too
    document.addEventListener('autoApplyApplied', function () { applyLogState(); });

    document.addEventListener('DOMContentLoaded', function () {
        observe();
        applyLogState();
        onTrayChanged();      // an armed session + pending edits after a reload
    });
    document.addEventListener('htmx:afterSwap', function () { applyLogState(); });
})();

/* ── docs/120 item 8: the Auto-Sync popup ────────────────────────────────
 *
 * Only the popover mechanics live here. The three switches POST to
 * /auto-sync/set and every decision that follows -- whether pull is armed,
 * whether live diverged, whether unapplied edits block a replace -- is made on
 * the server, so the covenant is stated in exactly one place.
 */
window.AutoSync = (function () {
    function host() { return document.getElementById('auto-sync-pop-host'); }
    function pop() { return document.getElementById('auto-sync-pop'); }
    function btn() { return document.querySelector('.auto-apply-pill'); }

    function close() {
        var h = host(); if (h) h.innerHTML = '';
        var b = btn(); if (b) b.setAttribute('aria-expanded', 'false');
    }
    function toggle() {
        var h = host(); if (!h) return;
        var p = pop();
        if (p) {                                  // open -> close
            h.innerHTML = '';
            var b0 = btn(); if (b0) b0.setAttribute('aria-expanded', 'false');
            return;
        }
        // Fetched fresh so the switches always show the CURRENT session, and
        // so a tray swap mid-configuration cannot destroy a partial choice.
        var b = btn(); if (b) b.setAttribute('aria-expanded', 'true');
        if (!window.htmx) return;
        window.htmx.ajax('GET', '/auto-sync/panel',
                         { target: '#auto-sync-pop-host', swap: 'innerHTML' })
            .then(function () { syncNested(); _bindAway(); });
    }
    function _bindAway() {
        setTimeout(function () {
            document.addEventListener('click', function away(e) {
                var pp = pop();
                if (!pp) { document.removeEventListener('click', away); return; }
                if (pp.contains(e.target) || (btn() && btn().contains(e.target))) return;
                close();
                document.removeEventListener('click', away);
            });
        }, 0);
    }
    /* "Replace" qualifies a pull rather than being a mode of its own, so it is
       disabled (and visibly so) when pull is off -- a checkbox that cannot mean
       anything should not look like it can. */
    function syncNested() {
        var pull = document.getElementById('as-pull');
        var rep = document.getElementById('as-pull-replace');
        if (!pull || !rep) return;
        rep.disabled = !pull.checked;
        var row = rep.closest('.as-row');
        if (row) row.classList.toggle('as-row-disabled', !pull.checked);
    }
    return { toggle: toggle, close: close, syncNested: syncNested };
})();
