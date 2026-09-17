// The Diff workbench's per-column remove control (docs/197 item 3), EXECUTED.
//
// docs/197 shipped with server-side render pins only — they prove the button is
// there and gated, and prove nothing about what pressing it does. docs/187's
// lesson was exactly this ("the four client pins were greps ... replaced by a
// selfcheck that dispatches the real events at the real file"), so this runs
// the handler out of the real template.
//
// Run: node tests/diff_drop_selfcheck.cjs   (needs jsdom)
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

const ROOT = path.join(__dirname, '..');
const TPL = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_diff_workbench.html'),
  'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

// The handler, lifted from the template exactly as it ships. Extracting it by
// its own marker rather than retyping it is the point: a copy here would pass
// while the shipped one rotted.
const m = TPL.match(/<script>\s*\(function \(\) \{([\s\S]*?)\}\)\(\);\s*<\/script>/);
if (!m) { console.error('FAIL: could not find the handler in the template'); process.exit(1); }
const HANDLER = '(function () {' + m[1] + '})();';

function world(slots, base) {
  // slots: [{name, value}] — value '' means an empty optional picker
  const pickers = slots.map(s =>
    '<label class="diff-side"><span class="diff-side-tag">' + s.name.toUpperCase() + '</span>'
    + (s.value
        ? '<button type="button" class="diff-side-drop" data-slot="' + s.name + '">x</button>'
        : '')
    + '<select name="' + s.name + '">'
    + '<option value=""></option>'
    + (s.value ? '<option value="' + s.value + '" selected>' + s.value + '</option>' : '')
    + '</select></label>').join('');

  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="diff-root">'
    + '<form class="diff-wb-pickers">'
    + '<input type="hidden" name="tab" value="state">'
    + '<input type="hidden" name="view" value="panes">'
    + '<input type="hidden" name="base" value="' + base + '">'
    + pickers + '</form></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._changes = [];
  const form = win.document.querySelector('.diff-wb-pickers');
  form.addEventListener('change', function (e) {
    win._changes.push({ name: e.target.name, value: e.target.value });
  });
  new win.Function(HANDLER).call(win);
  return win;
}

function press(win, slot) {
  const b = win.document.querySelector('.diff-side-drop[data-slot="' + slot + '"]');
  if (!b) return false;
  b.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  return true;
}

function baseOf(win) {
  return win.document.querySelector('input[name="base"]').value;
}

// D1 — the press clears that slot and fires the form's own change. That is the
//      whole action: the server compacts what survives.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '0');
  ok(press(win, 'b'), 'D1: the button exists');
  const sel = win.document.querySelector('select[name="b"]');
  ok(sel.value === '', 'D1: the slot is cleared, got "' + sel.value + '"');
  ok(win._changes.length === 1, 'D1: exactly one change event, got ' + win._changes.length);
  ok(win._changes[0].name === 'b', 'D1: fired on the dropped slot');
}

// D2 — every OTHER slot is untouched, which is why no URL is built by hand.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '0');
  press(win, 'b');
  ok(win.document.querySelector('select[name="a"]').value === 'ra', 'D2: a kept');
  ok(win.document.querySelector('select[name="c"]').value === 'rc', 'D2: c kept');
  ok(win.document.querySelector('input[name="tab"]').value === 'state', 'D2: tab kept');
  ok(win.document.querySelector('input[name="view"]').value === 'panes', 'D2: view kept');
}

// D3 — dropping a column LEFT of the baseline steps the baseline back, so the
//      comparison keeps its baseline instead of silently re-basing.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '2');
  press(win, 'a');
  ok(baseOf(win) === '1', 'D3: base 2 -> 1 when a is dropped, got ' + baseOf(win));
}

// D4 — dropping a column RIGHT of the baseline leaves it alone.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '0');
  press(win, 'c');
  ok(baseOf(win) === '0', 'D4: base untouched, got ' + baseOf(win));
}

// D5 — dropping the BASELINE itself falls back to the first survivor.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '1');
  press(win, 'b');
  ok(baseOf(win) === '0', 'D5: base falls back to 0, got ' + baseOf(win));
}

// D6 — a click anywhere else in the form does nothing. The handler is
//      delegated on the form, which contains the selects people use constantly.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'}], '0');
  win.document.querySelector('select[name="a"]')
     .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(win._changes.length === 0, 'D6: clicking a select drops nothing');
  ok(win.document.querySelector('select[name="a"]').value === 'ra', 'D6: and clears nothing');
}

// D7 — binding is idempotent. The template's script re-runs on every htmx swap
//      of #diff-root; a second binding would fire the change twice and issue
//      two requests for one press.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:'rc'}], '0');
  const form = win.document.querySelector('.diff-wb-pickers');
  new win.Function(HANDLER).call(win);     // the swap re-runs it
  new win.Function(HANDLER).call(win);
  press(win, 'b');
  ok(win._changes.length === 1,
     'D7: one press still fires ONE change after re-binding, got ' + win._changes.length);
  ok(form._dropBound === true, 'D7: the guard is set');
}

// D8 — an empty optional picker carries no button, so nothing can drop it.
{
  const win = world([{name:'a',value:'ra'},{name:'b',value:'rb'},{name:'c',value:''}], '0');
  ok(!press(win, 'c'), 'D8: no control on an empty slot');
  ok(win._changes.length === 0, 'D8: and nothing fired');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('diff_drop_selfcheck: all checks passed (19 assertions)');
process.exit(0);
