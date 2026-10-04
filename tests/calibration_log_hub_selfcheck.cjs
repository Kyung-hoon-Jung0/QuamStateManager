/* Real journal HTML stays client-filterable while the ledger is building. */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (_) { console.error('jsdom not installed'); process.exit(2); }
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {
  url: 'http://localhost/journal', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, d = w.document;
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
try {
  w.eval(source('search-query.js')); w.eval(source('journal.js'));
  w.JournalPage.init();
  const before = w.localStorage.getItem(w.JournalPage._seenKey('chipX', '2026-10-03'));
  const incoming = new JSDOM(fs.readFileSync(process.argv[3], 'utf8'));
  const body = d.querySelector('#jr-body');
  body.innerHTML = incoming.window.document.querySelector('#jr-body').innerHTML;
  incoming.window.close();
  d.querySelector('#jr-q').value = 'needle';
  body.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: body } }));
  const visible = selector => [...d.querySelectorAll(selector)].filter(el => !el.closest('[hidden]'));
  assert.equal(visible('.jr-loose li').length, 1);
  assert.equal(visible('.jr-unassigned li').length, 1);
  assert.match(d.querySelector('.jr-loose .jr-line-count').textContent, /^1 journal line with run attachments unavailable$/);
  assert.equal(d.querySelector('.jr-counts'), null);
  assert.match(d.querySelector('.jr-history-status').textContent, /building the history \(2\/5\)/);
  assert.equal(w.localStorage.getItem(w.JournalPage._seenKey('chipX', '2026-10-03')), before);
  assert.ok(d.querySelector('.jr-unassigned button[onclick*="adopt"]'));
  console.log('all checks passed');
} finally { w.close(); }
