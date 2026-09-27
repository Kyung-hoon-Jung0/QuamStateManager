/* The working copy this window shows moved (w7 final-QA P2 / P3a).
 *
 * ONE signal, `sm:wc-moved` on document, for every way the chip's working
 * copy changes under an open page: a Take live / Pull / Apply (doStateSync),
 * a tray undo or redo, an approval written by the Agent, an Auto-Sync pull, a
 * State History stage, or another window's edit that the drift poll caught.
 * Each of those ends with the tray re-rendered, and the tray carries the
 * server's `data-edit-seq` (routes._edit_seq: the change set plus the working
 * files' mtimes -- it moves exactly when the working copy was written). So
 * this file does not guess which of the half-dozen app events means "the
 * values moved": after any of them it reads the tray's seq and announces a
 * change of it, once (debounced).
 *
 * Who listens: the surfaces that draw from the working copy but are not part
 * of the Live-Edit patch path (LiveSurfacePatch finds nothing on them) --
 * the Z-line distortion page (zline.js) and the Agent approval card's "now"
 * column (agent.js).
 */
(function () {
    'use strict';

    var seen = null;
    var timer = null;
    var DEBOUNCE_MS = 60;

    function traySeq() {
        var t = document.getElementById('pending-tray');
        var s = t && t.getAttribute('data-edit-seq');
        return s ? s : null;
    }

    function check() {
        timer = null;
        var now = traySeq();
        if (now === null) return;               // no chip / a tray without a seq: nothing to compare
        if (seen === null) { seen = now; return; }
        if (now === seen) return;
        var prev = seen;
        seen = now;
        try {
            document.dispatchEvent(new CustomEvent('sm:wc-moved', { detail: { seq: now, prev: prev } }));
        } catch (e) { /* an old engine without CustomEvent: nothing listens there */ }
    }

    function schedule() {
        if (timer) clearTimeout(timer);
        timer = setTimeout(check, DEBOUNCE_MS);
    }

    // Every road a tray re-render takes: _swapPendingTray (sm:tray-swapped,
    // quam:state-changed), an htmx swap into #pending-tray (afterSwap bubbles
    // to document), and the sync/restore announcements that precede or follow
    // one. Reading one attribute is free, so the broad net costs nothing.
    ['sm:tray-swapped', 'quam:state-changed', 'stateRestored', 'liveDriftChanged',
     'stateHistoryChanged', 'quam:undo-step', 'htmx:afterSwap'].forEach(function (n) {
        document.addEventListener(n, schedule);
    });

    function prime() { if (seen === null) seen = traySeq(); }
    document.addEventListener('DOMContentLoaded', prime);
    if (document.readyState !== 'loading') prime();

    window.WcMoved = { seq: traySeq, _check: check, _schedule: schedule };
})();
