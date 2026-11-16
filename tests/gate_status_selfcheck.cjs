/* jsdom selfcheck: the CZ gate status line's delete offer (static/gate-status.js).
 *   1. Cancel empties the offer slot, makes no request and focuses the row's Delete
 *   2. Esc inside the offer does the same
 *   3. Esc outside an offer does nothing
 * Run: node tests/gate_status_selfcheck.cjs (driven by tests/test_gate_status_routes.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                                      'gate-status.js'), 'utf8');
let n = 0;
function ok(cond, msg) { if (!cond) { console.error('FAIL - ' + msg); process.exit(1); } n++; console.log('ok - ' + msg); }

function page() {
    const dom = new JSDOM('<body><div class="gate-status"><div class="gate-status-row">'
        + '<button class="gate-status-delete">Delete</button></div>'
        + '<div class="gate-status-slot"><div class="gate-delete-offer"><p>x</p>'
        + '<button class="gate-delete-confirm">Delete gate</button>'
        + '<button class="gate-delete-cancel">Cancel</button></div></div></div>'
        + '<input id="other"></body>', { runScripts: 'outside-only' });
    const w = dom.window;
    w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'ui.js'), 'utf8'));
    w.__fetches = 0;
    w.fetch = function () { w.__fetches++; return Promise.resolve(); };
    w.XMLHttpRequest = function () { w.__fetches++; this.open = function () {}; this.send = function () {}; };
    w.eval(SRC);
    return w;
}

let w = page();
let scrolled = false;
const question = w.document.querySelector('.gate-delete-offer');
question.scrollIntoView = () => { scrolled = true; };
question.dispatchEvent(new w.CustomEvent('htmx:afterSettle', { bubbles: true }));
ok(scrolled && question.contains(w.document.activeElement), 'settled question scrolls into view and takes focus');
const before = w.document.querySelector('.gate-status-row').outerHTML;
w.document.querySelector('.gate-delete-cancel').click();
ok(w.document.querySelector('.gate-status-slot').innerHTML === '', 'Cancel empties the offer slot');
ok(w.document.querySelector('.gate-status-row').outerHTML === before, 'the row is as before');
ok(w.document.activeElement === w.document.querySelector('.gate-status-delete'), 'focus is on the row Delete');
ok(w.__fetches === 0, 'no request was made');

w = page();
const ev = new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
w.document.querySelector('.gate-delete-confirm').dispatchEvent(ev);
ok(w.document.querySelector('.gate-status-slot').innerHTML === '', 'Esc in the offer closes it');
ok(w.document.activeElement === w.document.querySelector('.gate-status-delete'), 'Esc puts focus on Delete');

w = page();
w.document.getElementById('other').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
ok(w.document.querySelector('.gate-delete-offer') !== null, 'Esc elsewhere leaves the offer');

/* the zone's undo / pull refresh waits while an offer is open, then runs once */
function zonePage(withOffer) {
    const dom = new JSDOM('<body><section class="gate-status-list"><div class="gate-status">'
        + '<div class="gate-status-row"><button class="gate-status-delete">Delete</button></div>'
        + '<div class="gate-status-slot">' + (withOffer ? '<div class="gate-delete-offer">'
        + '<button class="gate-delete-cancel">Cancel</button></div>' : '') + '</div></div></section></body>',
        { runScripts: 'outside-only' });
    const w = dom.window;
    w.eval(SRC);
    w.__refresh = 0;
    w.document.querySelector('.gate-status-list').addEventListener('gate-list-refresh', function () { w.__refresh++; });
    return w;
}
function confirmOn(w, type, target) {
    const zone = w.document.querySelector('.gate-status-list');
    const e = new w.CustomEvent('htmx:confirm', { bubbles: true, cancelable: true,
        detail: { elt: zone, triggeringEvent: { type: type, target: target } } });
    zone.dispatchEvent(e);
    return e.defaultPrevented;
}
w = zonePage(true);
ok(confirmOn(w, 'cellsReverted') === true, 'undo refresh is held while an offer is open');
ok(w.document.querySelector('.gate-status-list').getAttribute('data-gate-stale') === '1', 'the zone is marked stale');
ok(w.document.querySelector('.gate-delete-offer') !== null, 'the open offer stays');
w.document.querySelector('.gate-delete-cancel').click();
ok(w.__refresh === 1, 'closing the offer runs the held refresh once');
ok(!w.document.querySelector('.gate-status-list').hasAttribute('data-gate-stale'), 'stale mark cleared');
ok(confirmOn(w, 'gate-list-refresh') === false, 'the released refresh itself is not held');

w = zonePage(true);
confirmOn(w, 'stateRestored');
const slot = w.document.querySelector('.gate-status-slot');
slot.innerHTML = '';
slot.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true }));
ok(w.__refresh === 1, 'a server-closed offer also releases the held pull refresh');

w = zonePage(false);
ok(confirmOn(w, 'cellsReverted') === false, 'no offer open: the refresh runs at once');
w.document.querySelector('.gate-status-slot').dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true }));
ok(w.__refresh === 0, 'nothing held, nothing re-run');

/* a gate write from the pulse detail (outside the zone) re-fetches the list;
   the list's own answer (a press inside the zone) does not fetch twice */
w = zonePage(false);
const outside = w.document.createElement('div');
w.document.body.appendChild(outside);
ok(confirmOn(w, 'pulses-changed', outside) === false, 'a write from the pulse detail refreshes the list');
ok(confirmOn(w, 'pulses-changed', w.document.querySelector('.gate-status-delete')) === true,
   "the list's own answer is not fetched again");
ok(!w.document.querySelector('.gate-status-list').hasAttribute('data-gate-stale'), 'and is not held for later');
w = zonePage(true);
const outside2 = w.document.createElement('div');
w.document.body.appendChild(outside2);
ok(confirmOn(w, 'pulses-changed', outside2) === true, 'a pulse detail write waits while a delete offer is open');
w.document.querySelector('.gate-delete-cancel').click();
ok(w.__refresh === 1, 'and runs once the offer closes');
console.log('ALL OK (' + n + ' assertions)');
