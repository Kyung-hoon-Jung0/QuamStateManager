// The project-state notice shows once per page.
//
// _project_state_notice.html is included by the Datasets table and by a run's
// detail, so with a run open on /datasets the page holds two copies. app.js
// keeps the first in page order and hides the others, after the page loads and
// after every htmx swap; the one left when the other goes shows again.
//
// Run: node tests/project_state_notice_selfcheck.cjs   (needs jsdom)
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const NOTICE = '<div data-project-state-mismatch="1" data-state-rule="folder">'
  + '<div class="ui-notice">Runs come from another state.</div></div>';

function world(table, detail) {
  const page = '<!DOCTYPE html><html><body>'
    + '<div id="table-pane">' + (table ? NOTICE : '') + '<table></table></div>'
    + '<div id="inspector-pane">' + (detail ? NOTICE : '') + '</div></body></html>';
  const dom = new JSDOM(page, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: 'http://localhost/datasets' });
  const win = dom.window;
  win.htmx = { ajax: function () {}, process: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  new win.Function(APP_JS).call(win);
  return win;
}
const shown = (win) => Array.prototype.filter.call(
  win.document.querySelectorAll('[data-project-state-mismatch]'), (e) => !e.hidden);
const settle = (win, target) => target.dispatchEvent(
  new win.CustomEvent('htmx:afterSettle', { bubbles: true, detail: { target: target } }));

const tick = () => new Promise((r) => setTimeout(r, 20));

(async function main() {
// 1. Table and run detail both carry it: one shows, the table's (after load).
{
  const win = world(true, true);
  await tick();
  const s = shown(win);
  ok(s.length === 1, 'a page with both copies shows the notice once (shown ' + s.length + ')');
  ok(s[0] && s[0].closest('#table-pane') !== null, 'the one shown is the first in page order');
}

// 2. Opening another run swaps the detail in again: still once.
{
  const win = world(true, false);
  await tick();
  ok(shown(win).length === 1, 'the table alone shows its notice');
  const pane = win.document.getElementById('inspector-pane');
  pane.innerHTML = NOTICE;
  settle(win, pane);
  ok(shown(win).length === 1, 'a run opened beside the table adds no second notice');
  pane.innerHTML = NOTICE;
  settle(win, pane);
  ok(shown(win).length === 1, 'and the next run opened neither');
}

// 3. The one left when the other goes shows again.
{
  const win = world(true, true);
  await tick();
  const table = win.document.getElementById('table-pane');
  table.innerHTML = '<div>another page</div>';
  settle(win, table);
  const s = shown(win);
  ok(s.length === 1 && s[0].closest('#inspector-pane') !== null,
     'with the table gone the run detail shows its notice');
}

// 4. A run detail opened outside /datasets shows it (it is the only copy).
{
  const win = world(false, true);
  await tick();
  ok(shown(win).length === 1, 'a run detail on its own shows the notice');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed (' + asserts + ' assertions)');
process.exit(0);
})().catch((e) => { console.error('selfcheck crashed: ' + (e && e.stack || e)); process.exit(1); });
