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
    w.__fetches = 0;
    w.fetch = function () { w.__fetches++; return Promise.resolve(); };
    w.XMLHttpRequest = function () { w.__fetches++; this.open = function () {}; this.send = function () {}; };
    w.eval(SRC);
    return w;
}

let w = page();
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
console.log('ALL OK (' + n + ' assertions)');
