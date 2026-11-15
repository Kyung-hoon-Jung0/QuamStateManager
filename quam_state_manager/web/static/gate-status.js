/* The CZ gate status line's delete offer (_gate_delete_offer.html): Cancel or
   Esc closes the offer, leaves the row as it was and puts the focus back on the
   row's Delete button. No request is made. Delegated, so it works for every
   status line htmx swaps in (pair detail, pulse detail, the create dialog). */
(function () {
    'use strict';
    if (window.GateStatus) return;

    /* The pair's gate zone re-fetches itself on a gate write from elsewhere on
       the page (pulses-changed), an undo, a redo or a sync pull. While a
       delete offer is open in it that refresh waits (the offer would be
       wiped under the user's eyes); it runs once the last offer closes. */
    function offerOpen(zone) {
        return !!(zone && zone.querySelector('.gate-status-slot .gate-delete-offer'));
    }
    function releaseRefresh(zone) {
        if (!zone || !zone.hasAttribute('data-gate-stale') || offerOpen(zone)) return;
        zone.removeAttribute('data-gate-stale');
        zone.dispatchEvent(new CustomEvent('gate-list-refresh'));
    }

    function closeOffer(from) {
        var offer = from && from.closest ? from.closest('.gate-delete-offer') : null;
        if (!offer) return false;
        var row = offer.closest('.gate-status');
        var slot = offer.closest('.gate-status-slot');
        var zone = offer.closest('.gate-status-list');
        if (slot) slot.innerHTML = '';
        else offer.remove();
        releaseRefresh(zone);
        var del = row ? row.querySelector('.gate-status-delete') : null;
        if (del && del.focus) del.focus();
        return true;
    }

    document.addEventListener('click', function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest('.gate-delete-cancel') : null;
        if (btn && closeOffer(btn)) ev.preventDefault();
    });
    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape') return;
        if (closeOffer(ev.target)) { ev.preventDefault(); ev.stopPropagation(); }
    });
    document.addEventListener('htmx:confirm', function (ev) {
        var d = ev.detail || {};
        var zone = d.elt;
        if (!zone || !zone.classList || !zone.classList.contains('gate-status-list')) return;
        var t = d.triggeringEvent;
        if (t && t.type === 'gate-list-refresh') return;
        /* the list's own answer already re-rendered it: its pulses-changed,
           fired from a press inside the zone, needs no second fetch */
        if (t && t.type === 'pulses-changed' && t.target && t.target.nodeType === 1
            && zone.contains(t.target)) { ev.preventDefault(); return; }
        if (!offerOpen(zone)) return;
        ev.preventDefault();
        zone.setAttribute('data-gate-stale', '1');
    });
    /* the offer takes the focus when it opens, so Esc reaches it at once */
    document.addEventListener('htmx:afterSwap', function (ev) {
        var t = ev.target;
        if (!t || !t.classList || !t.classList.contains('gate-status-slot')) return;
        if (!t.querySelector('.gate-delete-offer')) releaseRefresh(t.closest('.gate-status-list'));
        var first = t.querySelector('.gate-delete-cancel');
        if (first && first.focus) first.focus();
    });

    var activating = new Set();
    document.addEventListener('htmx:beforeRequest', function (ev) {
        var b = (ev.detail || {}).elt;
        if (!b || !b.classList || !b.classList.contains('gate-status-activate')) return;
        if (activating.size) { ev.preventDefault(); return; }
        activating.add(b);
        b.disabled = true;
        b.setAttribute('aria-busy', 'true');
    });
    document.addEventListener('htmx:afterRequest', function (ev) {
        var b = (ev.detail || {}).elt;
        if (!activating.delete(b)) return;
        b.disabled = false;
        b.removeAttribute('aria-busy');
    });

    window.GateStatus = { busy: function () { return activating.size > 0; }, closeOffer: closeOffer, offerOpen: offerOpen };
})();
