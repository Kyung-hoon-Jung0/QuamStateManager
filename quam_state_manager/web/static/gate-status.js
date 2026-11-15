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
    /* One press at a time per pair. While a press (Re-check, Make active, Delete, the
       offer's buttons) is out, every other press button of the pair's zone is disabled (also
       in a zone of that pair rendered meanwhile), and a press tried anyway is refused before
       it is sent. Edit values is a pane navigation and stays free. When the press ends (load,
       error, abort or timeout) the buttons this script disabled are enabled again. The zone
       is then refreshed once only when that is owed: a refresh trigger or refresh answer came
       while the press was out (or a refresh was out when it began), or the press answer did
       not land on screen (error, abort, timeout, its target had left the page). A refresh
       trigger while the zone's refresh is out is owed and runs once when that refresh ends.
       A refresh that replaces the zone carries the press messages standing in it (see
       messagesOf) into the new zone, never the chip-state notices it renders again, and focus inside a replaced zone
       goes to the same control in the new one, else to its first enabled button. */
    var busyPairs = {};   // pair: the press record while a press of that pair is out
    var due = {};         // pair: true when a refresh is owed once the zone is ready
    var swapIn = {};      // pair: what the next zone of that pair takes over
    function pairOf(zone) { return zone ? zone.getAttribute('data-gate-pair') || '' : ''; }
    function zoneOf(pair) {
        var zones = document.querySelectorAll('.gate-status-list');
        for (var i = 0; i < zones.length; i++) if (pairOf(zones[i]) === pair) return zones[i];
        return null;
    }
    function isZone(el) { return !!(el && el.classList && el.classList.contains('gate-status-list')); }
    function busy(zone) { return !!busyPairs[pairOf(zone)]; }
    function zoneOfPress(elt) {
        if (!elt || !elt.closest || (elt.classList && elt.classList.contains('gate-status-edit'))) return null;
        var zone = elt.closest('.gate-status-list');
        return zone && zone !== elt ? zone : null;
    }
    function pressButtons(zone) {
        return Array.prototype.filter.call(
            zone.querySelectorAll('button[hx-get], button[hx-post]'),
            function (b) { return !b.disabled && !b.classList.contains('gate-status-edit'); });
    }
    function rowOf(zone, gate) {
        var rows = zone.querySelectorAll('.gate-status');
        for (var i = 0; i < rows.length; i++) if (rows[i].getAttribute('data-gate') === gate) return rows[i];
        return null;
    }
    function refreshDue(pair) {
        if (!due[pair] || busyPairs[pair]) return;
        var zone = zoneOf(pair);
        if (!zone) { delete due[pair]; return; }              // the zone left the page
        if (!zone.__gateReady) return;                        // its htmx:load runs it
        delete due[pair];
        zone.setAttribute('data-gate-stale', '1');
        releaseRefresh(zone);
    }
    /* the press messages standing in a zone. The server marks what only a press answer renders
       (`data-press-answer`: a row's note, the zone notice of a delete press); a row's slot and
       the zone-level slot are rendered empty, so what stands in them came from a press too.
       Chip-state notices (the Re-check notice, a row's class-file lines) carry no mark and are
       never collected: every refresh renders them again from the state. A message is carried
       only into the refresh its own press owed, and once: everything standing when a press
       begins (earlier answers, carried or painted) is set aside (OLD) and never collected again. */
    var MARK = 'data-press-answer';
    var OLD = 'data-gate-carried';
    var owed = {};        // pair: a debt number while the refresh owed by a press is due or out
    var debts = 0;
    function inRow(n) { return !!(n.parentElement && n.parentElement.closest('.gate-status')); }
    function textOf(n) { return (n.textContent || '').replace(/\s+/g, ' ').trim(); }
    function messagesOf(zone) {
        var out = { zone: [], rows: [] };
        function fresh(n) { return !n.hasAttribute(OLD); }
        Array.prototype.forEach.call(zone.querySelectorAll('[' + MARK + ']'), function (n) {
            if (!inRow(n) && !n.closest('[data-gate-zone-slot]') && fresh(n)) out.zone.push(n);
        });
        var zslot = zone.querySelector('[data-gate-zone-slot]');
        if (zslot) Array.prototype.forEach.call(zslot.children, function (n) { if (fresh(n)) out.zone.push(n); });
        Array.prototype.forEach.call(zone.querySelectorAll('.gate-status'), function (row) {
            var nodes = [];
            Array.prototype.forEach.call(row.children, function (n) {
                if (n.classList.contains('gate-status-slot')) {
                    Array.prototype.forEach.call(n.children, function (m) {
                        if (!m.classList.contains('gate-delete-offer') && fresh(m)) nodes.push(m);
                    });
                } else if (n.hasAttribute(MARK) && fresh(n)) nodes.push(n);
            });
            if (nodes.length) out.rows.push({ gate: row.getAttribute('data-gate'), nodes: nodes });
        });
        return out;
    }
    function setAside(msgs) {
        msgs.zone.forEach(function (n) { n.setAttribute(OLD, ''); });
        msgs.rows.forEach(function (r) { r.nodes.forEach(function (n) { n.setAttribute(OLD, ''); }); });
    }
    /* moved, never copied, and never marked again: the next press sets them aside. A fresh press
       answer of the new zone wins: a row that has one drops the old row messages, and a
       zone-level message whose text the new zone already shows (fresh or carried) is dropped. */
    function carryInto(zone, msgs) {
        var zslot = zone.querySelector('[data-gate-zone-slot]');
        var shown = {};
        Array.prototype.forEach.call(zone.querySelectorAll('[' + MARK + ']'), function (n) {
            if (!inRow(n)) shown[textOf(n)] = true;
        });
        function putZone(n) {
            var t = textOf(n);
            if (!zslot || shown[t]) return;
            shown[t] = true;
            zslot.appendChild(n);
        }
        msgs.zone.forEach(putZone);
        msgs.rows.forEach(function (r) {
            var row = rowOf(zone, r.gate);
            if (!row) { r.nodes.forEach(putZone); return; }
            if (row.querySelector('[' + MARK + ']')) return;   // the fresh answer wins
            var slot = row.querySelector('.gate-status-slot');
            r.nodes.forEach(function (n) { if (slot) slot.appendChild(n); });
        });
    }
    var ACTIONS = ['gate-status-recheck', 'gate-status-activate', 'gate-status-delete',
                   'gate-status-edit', 'gate-delete-confirm', 'gate-delete-cancel'];
    function focusIn(zone) {
        var a = document.activeElement;
        if (!a || a === document.body || !zone.contains(a)) return null;
        var row = a.closest ? a.closest('.gate-status') : null;
        var act = null;
        for (var i = 0; i < ACTIONS.length; i++) if (a.classList && a.classList.contains(ACTIONS[i])) act = ACTIONS[i];
        return { gate: row ? row.getAttribute('data-gate') : null, action: act };
    }
    function focusBack(zone, key) {
        var a = document.activeElement;
        if (!key || (a && a !== document.body && document.body.contains(a))) return;
        var row = key.gate !== null ? rowOf(zone, key.gate) : null;
        var to = row && key.action ? row.querySelector('.' + key.action) : null;
        if (!to || to.disabled) to = zone.querySelector('button:not([disabled])');
        if (!to) { zone.setAttribute('tabindex', '-1'); to = zone; }
        if (to.focus) to.focus();
    }
    document.addEventListener('htmx:beforeRequest', function (ev) {
        var d = ev.detail || {}, elt = d.elt;
        if (isZone(elt)) {                                   // the zone's own refresh
            var list = elt;
            list.__gateRefreshOut = true;
            var lpair = pairOf(list);
            /* a debt ends with the request that pays it, whatever its outcome (swapped,
               an error answer, a network failure, an abort): after a failed one the answer
               simply stays where it stands, and a later refresh owes nothing to it; a newer
               press's debt is its own number and stays */
            var paying = owed[lpair] || 0;
            var lend = function () {
                if (paying && owed[lpair] === paying) delete owed[lpair];
                paying = 0;
                if (!list.__gateRefreshOut) return;
                list.__gateRefreshOut = false;
                refreshDue(lpair);
            };
            if (d.xhr && d.xhr.addEventListener) d.xhr.addEventListener('loadend', lend);
            else lend();
            return;
        }
        var zone = zoneOfPress(elt);
        if (!zone) return;
        var pair = pairOf(zone), done = false;
        var rec = { pair: pair, held: !!zone.__gateRefreshOut, painted: false,
                    off: pressButtons(zone).filter(function (b) { return b !== elt; }) };
        busyPairs[pair] = rec;
        elt.__gatePress = rec;
        setAside(messagesOf(zone));                      // what stands now is an earlier press's
        rec.off.forEach(function (b) { b.disabled = true; });
        function end() {
            if (done) return;
            done = true;
            delete busyPairs[pair];
            rec.off.forEach(function (b) { b.disabled = false; });
            if (rec.held || !rec.painted) { due[pair] = true; owed[pair] = ++debts; }
            refreshDue(pair);
        }
        if (d.xhr && d.xhr.addEventListener) d.xhr.addEventListener('loadend', end);
        else end();
    });
    document.addEventListener('htmx:beforeSwap', function (ev) {
        var d = ev.detail || {}, tgt = d.target;
        var elt = d.requestConfig && d.requestConfig.elt;
        if (!tgt) return;
        var rec = elt && elt.__gatePress;
        if (rec && busyPairs[rec.pair] === rec) {            // a press answer
            rec.painted = !!(d.shouldSwap && document.body.contains(tgt));
            if (rec.painted && isZone(tgt)) swapIn[rec.pair] = { press: true, focus: focusIn(tgt) };
            return;
        }
        if (tgt !== elt || !isZone(tgt)) return;
        var zone = tgt, pair = pairOf(zone);
        if (!document.body.contains(zone)) { d.shouldSwap = false; return; }   // a newer answer replaced it
        if (busy(zone)) {
            d.shouldSwap = false;   // it was out when the press began, so the press owes a refresh
        } else if (offerOpen(zone)) {
            d.shouldSwap = false;
            zone.setAttribute('data-gate-stale', '1');
        } else if (d.shouldSwap) {
            swapIn[pair] = { focus: focusIn(zone), carry: owed[pair] ? messagesOf(zone) : null };
            delete owed[pair];
        }
    });
    document.addEventListener('htmx:load', function (ev) {
        var t = ev.target;
        if (!t || !t.querySelectorAll) return;
        var zones = Array.prototype.slice.call(t.querySelectorAll('.gate-status-list'));
        if (isZone(t)) zones.push(t);
        zones.forEach(function (zone) {
            var pair = pairOf(zone), info = swapIn[pair];
            delete swapIn[pair];
            if (info && info.carry) carryInto(zone, info.carry);
            var rec = busyPairs[pair];
            if (rec && !(info && info.press)) {          // a zone of the pair while a press is out
                var more = pressButtons(zone);
                more.forEach(function (b) { b.disabled = true; });
                rec.off = rec.off.concat(more);
            }
            if (info) focusBack(zone, info.focus);
            zone.__gateReady = true;
            refreshDue(pair);
        });
    });
    document.addEventListener('htmx:confirm', function (ev) {
        var d = ev.detail || {};
        var press = zoneOfPress(d.elt);
        if (press) { if (busy(press)) ev.preventDefault(); return; }   // one press at a time
        var zone = d.elt;
        if (!isZone(zone)) return;
        var t = d.triggeringEvent;
        /* the list's own answer already re-rendered it: its pulses-changed,
           fired from a press inside the zone, needs no second fetch */
        if (t && t.type === 'pulses-changed' && t.target && t.target.nodeType === 1
            && zone.contains(t.target)) { ev.preventDefault(); return; }
        if (busy(zone)) {                               // the refresh after the press covers it
            ev.preventDefault();
            busyPairs[pairOf(zone)].held = true;
            return;
        }
        /* a trigger while this zone's refresh is out runs once after it: htmx discards a
           request whose zone the answer swapped out, so it is owed here */
        if (zone.__gateRefreshOut) { ev.preventDefault(); due[pairOf(zone)] = true; return; }
        if (t && t.type === 'gate-list-refresh') return;
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
    });
    // A swap may remove the button; the request still owns its completion.
    document.addEventListener('htmx:beforeSend', function (ev) {
        var d = ev.detail || {}, b = d.elt, xhr = d.xhr;
        if (!b || !b.classList || !b.classList.contains('gate-status-activate') || !xhr) return;
        activating.add(xhr);
        b.disabled = true;
        b.setAttribute('aria-busy', 'true');
        xhr.addEventListener('loadend', function () {
            activating.delete(xhr);
            if (!b.isConnected) return;
            b.disabled = false;
            b.removeAttribute('aria-busy');
        }, { once: true });
    });

    window.GateStatus = {
        busy: function () { return activating.size > 0; }, closeOffer: closeOffer, offerOpen: offerOpen
    };
})();
