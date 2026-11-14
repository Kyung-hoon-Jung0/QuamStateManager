/* The CZ gate status line's delete offer (_gate_delete_offer.html): Cancel or
   Esc closes the offer, leaves the row as it was and puts the focus back on the
   row's Delete button. No request is made. Delegated, so it works for every
   status line htmx swaps in (pair detail, pulse detail, the create dialog). */
(function () {
    'use strict';
    if (window.GateStatus) return;

    function closeOffer(from) {
        var offer = from && from.closest ? from.closest('.gate-delete-offer') : null;
        if (!offer) return false;
        var row = offer.closest('.gate-status');
        var slot = offer.closest('.gate-status-slot');
        if (slot) slot.innerHTML = '';
        else offer.remove();
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
    /* the offer takes the focus when it opens, so Esc reaches it at once */
    document.addEventListener('htmx:afterSwap', function (ev) {
        var t = ev.target;
        if (!t || !t.classList || !t.classList.contains('gate-status-slot')) return;
        var first = t.querySelector('.gate-delete-cancel');
        if (first && first.focus) first.focus();
    });

    window.GateStatus = { closeOffer: closeOffer };
})();
