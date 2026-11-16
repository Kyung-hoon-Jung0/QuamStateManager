/* jsdom selfcheck for the gate row delete question's focus: the question opens
 * with focus on its safe button (Cancel), Cancel returns focus to the row's
 * Delete button, and a confirmed delete lands focus on the next row's first
 * button, else on the gate zone's add button. Runs the real htmx, ui.js and
 * gate-status.js. Run: node tests/gate_delete_focus_selfcheck.cjs
 * (driven by tests/test_gate_delete_focus.py). */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const OFFER = '<div class="gate-delete-offer"><p>Deleting X removes:</p>'
    + '<div class="ui-actions gate-delete-actions">'
    + '<button type="button" class="gate-delete-confirm" hx-post="/del" hx-target="closest .gate-status-list" hx-swap="outerHTML">Delete gate</button>'
    + '<button type="button" class="gate-delete-cancel" hx-get="/close" hx-target="closest .gate-status-slot" hx-swap="innerHTML">Cancel</button>'
    + '</div></div>';
function row(g) {
    return '<div class="gate-status" data-gate="' + g + '"><span>'
        + '<button type="button" class="gate-status-recheck" hx-post="/rc">Re-check</button>'
        + '<button type="button" class="gate-status-delete" hx-get="/offer?gate=' + g + '" hx-target="next .gate-status-slot" hx-swap="innerHTML">Delete</button>'
        + '</span><div class="gate-status-slot" role="status"></div></div>';
}
const PLUS = '<button type="button" id="plus-gate" data-czg-open>+ Gate</button>';
function zone(rows) { return '<div class="gate-status-list" data-gate-pair="p1">' + PLUS + rows.map(row).join('') + '</div>'; }
const ZONE = zone(['A', 'B']);

(async function () {
    const dom = new JSDOM('<!doctype html><body><div id="wrap"></div></body>', {
        url: 'http://localhost/pairs', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    let pending = [];
    class FakeXHR extends w.EventTarget {
        constructor() { super(); this.upload = new w.EventTarget(); }
        open(m, url) { this.responseURL = 'http://localhost' + url; this.url = url; }
        setRequestHeader() {}
        overrideMimeType() {}
        getAllResponseHeaders() { return 'content-type: text/html\r\n'; }
        getResponseHeader(n) { return n.toLowerCase() === 'content-type' ? 'text/html' : null; }
        send() { pending.push(this); }
        abort() { this.dispatchEvent(new w.Event('loadend')); }
        answer(html) {
            this.status = 200; this.readyState = 4; this.response = this.responseText = html;
            this.onload(); this.dispatchEvent(new w.Event('loadend'));
        }
    }
    w.XMLHttpRequest = FakeXHR;
    w.XPathEvaluator = function () {};
    w.XPathEvaluator.prototype.createExpression = function () { return { evaluate() { return { iterateNext() { return null; } }; } }; };
    w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'ui.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'gate-status.js'), 'utf8'));
    const d = w.document;
    function load() {
        d.getElementById('wrap').innerHTML = ZONE;
        w.htmx.process(d.body);
        w.document.body.dispatchEvent(new w.CustomEvent('htmx:load', { bubbles: true }));
    }
    async function answerLast(html) { const x = pending.pop(); x.answer(html); await flush(60); }
    const act = () => (d.activeElement && d.activeElement.className) || '(body)';

    // open the question on row A
    load();
    let del = d.querySelector('[data-gate="A"] .gate-status-delete');
    del.focus(); del.click();
    await answerLast(OFFER);
    ok(act() === 'gate-delete-cancel', 'the question opens with focus on Cancel (got ' + act() + ')');

    // Cancel returns to the row's Delete button
    d.querySelector('.gate-delete-cancel').click();
    await answerLast('');
    ok(d.activeElement === d.querySelector('[data-gate="A"] .gate-status-delete'), 'after Cancel focus is on the row Delete button (got ' + act() + ')');

    // Yes on row A: the row goes, focus lands on row B's first button
    d.querySelector('[data-gate="A"] .gate-status-delete').click();
    await answerLast(OFFER);
    d.querySelector('.gate-delete-confirm').focus();
    d.querySelector('.gate-delete-confirm').click();
    await answerLast(zone(['B']));        // the delete answer re-renders the list without row A
    ok(d.activeElement === d.querySelector('[data-gate="B"] button'), 'after Yes focus is on the next row first button (got ' + act() + ')');

    // Yes on the last row: focus goes to the add button
    d.querySelector('[data-gate="B"] .gate-status-delete').click();
    await answerLast(OFFER);
    d.querySelector('.gate-delete-confirm').focus();
    d.querySelector('.gate-delete-confirm').click();
    await answerLast(zone([]));
    ok(d.activeElement && d.activeElement.id === 'plus-gate', 'after Yes on the last row focus is on the add button (got ' + act() + ')');
    dom.window.close();
    if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
    console.log('all checks passed');
})();
