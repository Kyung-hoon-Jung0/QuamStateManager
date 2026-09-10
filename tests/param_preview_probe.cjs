// Ask the REAL panel what a range token will select.
//
// Used by tests/test_param_typeahead.py to check the one promise the range
// preview makes: the count it prints is the count the search returns. The
// panel computes it in the browser from the vocabulary payload; the server
// computes it by matching every run. If those two ever disagree, the preview
// is a lie, and a lie about how many runs a filter keeps is worse than no
// preview at all.
//
// argv[2] is {payload, asks:[{key, op, want}]}; stdout is one
// {values, runs, insert} per ask, or null when the panel offers no preview.
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
const SQ_JS = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');
const TH_JS = fs.readFileSync(path.join(STATIC, 'sidebar-typeahead.js'), 'utf8');

const IN = JSON.parse(process.argv[2]);

(async function () {
  const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document; global.CSS = win.CSS;
  win.htmx = { ajax() {}, trigger() {}, process() {} };
  win.fetch = function () {
    return Promise.resolve({ status: 200, ok: true,
                             json: function () { return Promise.resolve(IN.payload); } });
  };
  win._anchorPopover = function () {};
  new win.Function(SQ_JS).call(win, win);
  new win.Function(TH_JS).call(win, win);
  win.document.body.innerHTML = '<textarea id="sidebar-filter-input"></textarea>';

  // the panel fetches its vocabulary on the first keystroke
  const el = win.document.getElementById('sidebar-filter-input');
  el.value = 'm';
  el.selectionStart = el.selectionEnd = 1;
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  await new Promise(function (r) { setTimeout(r, 30); });

  const out = IN.asks.map(function (a) {
    const res = win.SidebarTypeahead.suggest('value', a.key, a.want, { op: a.op });
    if (!res || !res.items || !res.items.length) return null;
    const head = res.items[0];
    // "12 of 30 values · 468 runs" — read back as numbers, so a change to the
    // wording is not read as a change to the arithmetic
    const m = /^(\d+) of (\d+) values · (\d+) runs$/.exec(head.meta || '');
    if (!m) return { meta: head.meta, insert: head.insert };
    return { values: Number(m[1]), of: Number(m[2]), runs: Number(m[3]),
             insert: head.insert, label: head.label };
  });
  console.log(JSON.stringify(out));
  process.exit(0);
})().catch(function (e) {
  console.error('probe error: ' + (e && e.stack || e));
  process.exit(1);
});
