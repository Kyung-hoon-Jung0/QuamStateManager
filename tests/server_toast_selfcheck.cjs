/* docs/301 F27 -- a response's HX-Trigger {"showToast": ...} reaches the person.
 * htmx raises the event with detail {value: "text"} for a string, or with the
 * object itself; nothing listened, so those messages were never shown.
 * Run: node tests/server_toast_selfcheck.cjs (driven by tests/test_undo_after_version_load.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const a = SRC.indexOf('document.addEventListener("showToast", function (e) {');
ok(a > 0, 'app.js listens for the showToast trigger');
const BLOCK = a > 0 ? SRC.slice(a, SRC.indexOf('\n});', a) + 4) : '';

const dom = new JSDOM('<!doctype html><body><form id="f"></form></body>', { runScripts: 'outside-only' });
const w = dom.window;
const shown = [];
w.showToast = function (m, level) { shown.push([m, level]); };
w.eval(BLOCK);
function fire(detail) {
    w.document.getElementById('f').dispatchEvent(new w.CustomEvent('showToast', { bubbles: true, detail }));
}
fire({ value: 'Auto-push is off: the live chip changed.' });
ok(shown.length === 1 && shown[0][0] === 'Auto-push is off: the live chip changed.' && shown[0][1] === 'info',
   'a string trigger (htmx: {value}) is shown as info: ' + JSON.stringify(shown));
fire({ message: 'Nothing to undo: a loaded version is not an edit.', level: 'info' });
ok(shown.length === 2 && shown[1][0].indexOf('Nothing to undo') === 0, 'an object trigger shows its message');
fire({ message: 'careful', level: 'warning' });
ok(shown[2] && shown[2][1] === 'warning', 'its level is kept');
fire({ value: '' });
fire(null);
ok(shown.length === 3, 'an empty trigger shows nothing');

if (fails) { console.error(fails + ' failed'); process.exit(1); }
console.log('all passed');
