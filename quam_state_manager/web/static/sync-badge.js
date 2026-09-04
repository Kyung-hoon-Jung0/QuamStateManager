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
 *   - `live changed` was cut: /state/drift's refresh returns early on a dirty
 *     context, so the 5-second poll cannot keep the flag true there, and a
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
            title: 'New experiment runs have landed — click to see the newest'
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

    function render() {
        var host = tray();
        var anchor = pill();
        var el = host ? host.querySelector('.state-status-notice') : null;
        var kind = null;
        for (var i = 0; i < ORDER.length; i++) {
            if (pending[ORDER[i]]) { kind = ORDER[i]; break; }
        }
        if (!kind || !host || !anchor) {
            if (el && el.parentNode) el.parentNode.removeChild(el);
            return;
        }
        if (!el) {
            // A sibling of the pill, never a child: the pill IS a <button>
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
            anchor.parentNode.insertBefore(el, anchor.nextSibling);
        }
        var spec = KINDS[kind];
        el.setAttribute('data-kind', kind);
        el.className = 'state-status-notice ' + spec.cls;
        el.textContent = spec.text(pending[kind]);
        el.title = spec.title;
    }

    function note(kind, payload) {
        if (!KINDS[kind]) return;
        pending[kind] = payload || {};
        render();
    }
    function clear(kind) {
        if (kind) delete pending[kind]; else pending = {};
        render();
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

    window.SyncBadge = {
        note: note, clear: clear, onAck: ackHandler, render: render,
        _pending: function () { return pending; }
    };
})();
