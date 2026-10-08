// docs/301 F34 -- the sidebar's folder-path autocomplete.
//
// Typing a chip folder into "quam_state folder path..." opened a list of that
// folder's SUBFOLDERS directly over the "State Load" button below the box: the
// button could not be clicked until the list went away, and on a chip folder
// with one scripts subfolder the only row was that subfolder, so a click aimed
// at the button picked it and loaded the wrong folder.
//
//  A1  a typed path that IS a loadable chip folder offers itself first,
//      labelled as one; its subfolders follow
//  A2  picking that row keeps the folder and does not drill into it
//  A3  the list never covers the form's own submit button: it opens below it
//  A4  with no submit button under the box, the list stays where it was
//  A5  submitting the form closes the list; Enter with nothing highlighted
//      still submits (the list never swallows it)
//  A6  a plain folder (no state.json + wiring.json) gets no such row
//
// Run: node tests/path_autocomplete_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const APP_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
let n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }

// the sidebar load form, as base.html renders it
const PAGE = '<!DOCTYPE html><html><body><aside id="sidebar"><div class="sidebar-load">'
  + '<form id="load-form">'
  + '<div class="path-input-group">'
  + '<input type="text" name="folder" id="load-path-input" autocomplete="off">'
  + '<button type="button" class="btn-browse">B</button>'
  + '<button type="button" class="btn-recents">v</button>'
  + '</div>'
  + '<div id="recents-dropdown" hidden></div>'
  + '<button type="submit" class="btn-sm" id="load-btn">State Load</button>'
  + '</form></div></aside>'
  + '<form id="other-form"><div class="path-input-group" id="g2">'
  + '<input type="text" id="other-input"></div></form>'
  + '</body></html>';

const CHIP = 'C:\\data\\chipX\\quam_state';
const ANSWERS = {};
ANSWERS[CHIP] = { path: CHIP, dirs: [CHIP + '\\state_gen_scripts'],
                  has_quam_state: true, parent: 'C:\\data\\chipX' };
ANSWERS['C:\\data\\chipX'] = { path: 'C:\\data\\chipX', has_quam_state: false,
                               dirs: ['C:\\data\\chipX\\quam_state', 'C:\\data\\chipX\\runs'],
                               parent: 'C:\\data' };

function rect(top, bottom, left, right) {
  return { top: top, bottom: bottom, left: left, right: right,
           height: bottom - top, width: right - left, x: left, y: top };
}

async function main() {
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true,
                                url: 'http://localhost/' });
  const win = dom.window;
  const fetched = [];
  win.fetch = function (url) {
    fetched.push(url);
    const m = /[?&]path=([^&]*)/.exec(url);
    const p = m ? decodeURIComponent(m[1]) : '';
    const body = ANSWERS[p] || { path: p, dirs: [], has_quam_state: false };
    return win.Promise.resolve({ ok: true, json: function () { return win.Promise.resolve(body); } });
  };
  try { new win.Function(APP_JS).call(win); } catch (e) { /* app.js boot needs a full page */ }
  const doc = win.document;
  ok(typeof win.initPathAutocomplete === 'function', 'precondition: initPathAutocomplete exists');
  const input = doc.getElementById('load-path-input');
  const group = input.parentNode;
  const btn = doc.getElementById('load-btn');
  win.initPathAutocomplete(input);
  const box = group.querySelector('.path-suggestions');
  ok(!!box, 'precondition: the list host is mounted beside the box');

  // geometry: the box row is 0..30px, State Load sits right under it (34..60)
  group.getBoundingClientRect = () => rect(100, 130, 0, 260);
  btn.getBoundingClientRect = () => rect(134, 160, 0, 260);
  Object.defineProperty(box, 'offsetHeight', { configurable: true, get: () => 48 });

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  async function type(v) {
    input.value = v;
    input.dispatchEvent(new win.Event('input'));
    await wait(320);
  }
  const rows = () => Array.prototype.slice.call(box.querySelectorAll('.path-suggestion'));

  // -- A1
  await type(CHIP);
  let r = rows();
  ok(r.length === 2, 'A1: the folder itself and its one subfolder -- got ' + r.length);
  ok(r[0] && r[0].getAttribute('data-path') === CHIP, 'A1: the typed chip folder comes first');
  ok(r[0] && /chip folder/i.test(r[0].textContent), 'A1: and is labelled as one -- got ' + (r[0] && r[0].textContent));
  ok(r[1] && r[1].getAttribute('data-path') === CHIP + '\\state_gen_scripts', 'A1: the subfolder follows');
  ok(!r[0].classList.contains('active'), 'A1: nothing is pre-highlighted');

  // -- A3
  ok(box.style.display === 'block', 'A3: precondition -- the list is open');
  const top = parseFloat(box.style.top);
  ok(top >= 60, 'A3: the list opens below State Load (button bottom is 60px under the row top) -- top=' + box.style.top);

  // -- A2
  const nFetch = fetched.length;
  r[0].dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  await wait(320);
  ok(input.value === CHIP, 'A2: picking the folder keeps it in the box');
  ok(box.style.display === 'none', 'A2: and closes the list');
  ok(fetched.length === nFetch, 'A2: without listing its subfolders again');

  // -- A6
  await type('C:\\data\\chipX');
  r = rows();
  ok(r.length === 2 && !r.some((x) => /chip folder/i.test(x.textContent)),
    'A6: a plain folder offers only its subfolders');
  // picking a subfolder still drills into it, as before
  r[0].dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  await wait(320);
  ok(input.value === CHIP && rows().length === 2 && rows()[0].getAttribute('data-path') === CHIP,
    'A6: picking a subfolder drills into it (the chip folder offers itself)');

  // -- A5
  const kd = new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  input.dispatchEvent(kd);
  ok(!kd.defaultPrevented, 'A5: Enter with nothing highlighted is left to the form (it loads)');
  doc.getElementById('load-form').dispatchEvent(new win.Event('submit', { bubbles: true, cancelable: true }));
  ok(box.style.display === 'none', 'A5: submitting the form closes the list');

  // -- A4: no submit button under the box (the diff page's inputs)
  const other = doc.getElementById('other-input');
  win.initPathAutocomplete(other);
  const box2 = doc.getElementById('g2').querySelector('.path-suggestions');
  other.value = CHIP;
  other.dispatchEvent(new win.Event('input'));
  await wait(320);
  ok(box2.style.display === 'block' && box2.style.top === '',
    'A4: with no submit button below, the list keeps its CSS place -- top=' + box2.style.top);

  // the button BESIDE the box (same row) is never under the list either
  btn.getBoundingClientRect = () => rect(100, 130, 270, 330);
  await type(CHIP);
  ok(box.style.top === '', 'A4: a button on the box row does not move the list -- top=' + box.style.top);
  // ...nor one below it but outside the list's columns
  btn.getBoundingClientRect = () => rect(134, 160, 280, 340);
  await type(CHIP);
  ok(box.style.top === '', 'A4: a button below, beside the list, does not move it -- top=' + box.style.top);
  // ...nor one far below the list's reach
  btn.getBoundingClientRect = () => rect(400, 426, 0, 260);
  await type(CHIP);
  ok(box.style.top === '', 'A4: a button below the list\'s reach does not move it -- top=' + box.style.top);

  // -- A7/A8: submission cancels pending and in-flight completions.
  const assert = require('node:assert/strict');
  const isolated = new JSDOM('<form><div><input></div></form>', { runScripts: 'outside-only' });
  const iw = isolated.window;
  const timers = new Map();
  let timerId = 0;
  iw.setTimeout = function (callback) { timers.set(++timerId, callback); return timerId; };
  iw.clearTimeout = id => timers.delete(id);
  function runTimers() {
    const pending = Array.from(timers.values());
    timers.clear();
    pending.forEach(callback => callback());
  }
  // the signature grew an optional lifecycle scope (F42); anchor on the name
  const start = APP_JS.indexOf('window.initPathAutocomplete = function(inputEl');
  const end = APP_JS.indexOf('/* Folder browser modal', start);
  new iw.Function(APP_JS.slice(start, end)).call(iw);
  const pendingInput = iw.document.querySelector('input');
  const form = pendingInput.form;
  iw.initPathAutocomplete(pendingInput);
  const pendingBox = form.querySelector('.path-suggestions');
  let release;
  const requests = [];
  iw.fetch = function (url) {
    requests.push(url);
    return new iw.Promise(function (resolve) { release = resolve; });
  };
  pendingInput.value = 'C:\\data\\state';
  pendingInput.dispatchEvent(new iw.Event('input'));
  form.dispatchEvent(new iw.Event('submit', { bubbles: true, cancelable: true }));
  runTimers();
  assert.equal(requests.length, 0, 'A7: submitting cancels the pending debounce request');
  assert.equal(pendingBox.style.display, 'none', 'A7: the pending timer cannot reopen the list');

  pendingInput.dispatchEvent(new iw.Event('input'));
  runTimers();
  assert.equal(requests.length, 1, 'A8: precondition -- the browse request is in flight');
  form.dispatchEvent(new iw.Event('submit', { bubbles: true, cancelable: true }));
  release({ json: () => iw.Promise.resolve({ path: pendingInput.value,
    dirs: ['C:\\data\\state\\runs'], has_quam_state: true }) });
  await wait(20);
  assert.ok(pendingBox.style.display === 'none' && pendingBox.children.length === 0,
    'A8: a late browse response cannot reopen the list after submission');
  iw.close();
  console.log('autocomplete cancellation: all 4 checks passed');

  if (fails) { console.error(fails + ' of ' + n + ' check(s) failed'); process.exit(1); }
  console.log('path_autocomplete_selfcheck: all ' + n + ' checks passed');
  process.exit(0);
}

main().catch(function (e) { console.error('UNCAUGHT: ' + ((e && e.stack) || e)); process.exit(1); });
