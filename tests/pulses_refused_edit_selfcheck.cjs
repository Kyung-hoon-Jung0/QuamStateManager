/* A refused Pulses edit keeps the typed text (to fix it in place) and says what is stored.
 * Run: node tests/pulses_refused_edit_selfcheck.cjs  (driven by tests/test_pulses_refused_edit.py)
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const dom = new JSDOM('<body><div id="inspector-pane"><form class="inline-edit pulse-edit-form">'
    + '<input type="text" name="value" class="edit-input" data-param="flat_length" data-committed="44" value="44">'
    + '</form><form class="inline-edit pulse-edit-form">'
    + '<input type="text" name="value" class="edit-input" data-param="amplitude" data-committed="" value="">'
    + '</form></div></body>', { url: 'http://localhost/pulses', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w._debounce = () => {};
w.eval(fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/pulses.js'), 'utf8'));
let n = 0;
function check(fn) { fn(); n++; console.log('ok - check ' + n); }
const [form, blank] = w.document.querySelectorAll('form.pulse-edit-form');
const input = form.querySelector('input');
input.value = '45';
const refuse = (el) => el.dispatchEvent(new w.CustomEvent('htmx:responseError', { bubbles: true, detail: {} }));
refuse(form);
check(() => assert.equal(input.value, '45', 'the typed text stays to be fixed in place'));
check(() => assert.equal(input.getAttribute('aria-invalid'), 'true', 'the field is marked refused'));
const note = form.querySelector('[data-refused-note]');
check(() => assert.ok(note, 'the field says it was not saved'));
check(() => assert.equal(note.textContent, 'Not saved: the stored value is still 44.'));
check(() => assert.ok(note.classList.contains('ui-field-error'), 'the kit field-error part'));
refuse(form);
check(() => assert.equal(form.querySelectorAll('[data-refused-note]').length, 1,
    'a second refusal adds no second line'));
input.value = '46';
input.dispatchEvent(new w.Event('input', { bubbles: true }));
check(() => assert.equal(form.querySelector('[data-refused-note]'), null, 'typing again clears the line'));
check(() => assert.equal(input.hasAttribute('aria-invalid'), false, 'and the mark'));
refuse(blank);
check(() => assert.equal(blank.querySelector('[data-refused-note]').textContent, 'Not saved.',
    'an empty stored value invents nothing'));

// The server's reason goes on the field's own line; app.js shows no toast over the field for it.
const app = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8');
const a0 = app.indexOf('window.htmxErrorText = function');
const a1 = app.indexOf('// Network-level failure', a0);
check(() => assert.ok(a0 > 0 && a1 > a0, 'the shared error-text helper and the toast listener are in app.js'));
const toasts = [];
w.showToast = (msg) => { toasts.push(msg); return null; };
w.eval(app.slice(a0, a1));
const reason = 'This pulse would not run: SNZPulse at q.z.op \u2014 flat_length must be even. Every node would fail.';
const xhr = (status) => ({ status, responseText: '<div><p>Nothing was written \u2014 ' + reason + '</p></div>',
    getResponseHeader: () => null });
const refuseWith = (el, status) => el.dispatchEvent(new w.CustomEvent('htmx:responseError',
    { bubbles: true, detail: { xhr: xhr(status), elt: el, target: w.document.getElementById('inspector-pane') } }));
input.value = '45';
refuseWith(form, 400);
check(() => assert.equal(form.querySelector('[data-refused-note]').textContent,
    'Not saved: the stored value is still 44. ' + reason, 'the field line carries the reason, said once'));
check(() => assert.deepEqual(toasts, [], 'no toast covers the field'));
const other = w.document.createElement('form');
w.document.body.appendChild(other);
refuseWith(other, 400);
check(() => assert.deepEqual(toasts, ['Nothing was written \u2014 ' + reason], 'any other refusal still toasts'));
toasts.length = 0;
refuseWith(form, 500);
check(() => assert.equal(toasts.length, 1, 'a server failure on the field still toasts'));
console.log(n + ' assertions passed');
w.close();
