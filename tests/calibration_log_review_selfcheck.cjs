/* docs/281 review: the Calibration log page's client side.
 * 1. background gates: the day refreshes once they are done -- never under
 *    an open card (it waits and asks again);
 * 2. a very large day's card body arrives on open: its paths are bound and
 *    the active search applies to its lines;
 * 3. a claim names the card it was made on (data folder + number). */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (_) { console.error('jsdom not installed'); process.exit(2); }
const root = path.resolve(__dirname, '..');
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');

const html = `<!doctype html><html><body>
<form id="jr-filters"><input id="jr-q" value=""><input type="hidden" id="jr-day" value="2026-10-03">
<select id="jr-author"><option value="" selected>everyone</option></select></form>
<div id="jr-body">
<p class="jr-gates-pending" data-gates-day="2026-10-03">Checking 1 gate in the background</p>
<div class="jr-cards">
<details class="jr-card jr-run" id="card-1" data-jr-order="0" data-jr-search="12:00:00 scan qa1 #1 needle" data-jr-author="unknown">
<summary>row</summary><div class="jr-body jr-body-lazy">Loading...</div></details>
</div></div></body></html>`;

const dom = new JSDOM(html, { url: 'http://localhost/journal', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const timers = [];
const submits = [];
const posts = [];
let checks = 0;
try {
  w.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  w.clearTimeout = () => {};
  w.htmx = { trigger: (el, ev) => { if (ev === 'submit') submits.push(el.id); }, process() {} };
  w.fetch = (url, opts) => { posts.push({ url, body: JSON.parse(opts.body) }); return new Promise(() => {}); };
  w.eval(source('search-query.js'));
  w.eval(source('journal.js'));
  w.JournalPage.init();

  // 1. the pending note schedules one refresh
  const gate = timers.filter(t => t.ms === 2500);
  assert.equal(gate.length, 1, 'a pending gate schedules a refresh'); checks++;
  d.getElementById('card-1').open = true;            // a person is reading a card
  gate[0].fn();
  assert.equal(submits.length, 0, 'never refreshes under an open card'); checks++;
  const retry = timers.filter(t => t.ms === 2500);
  assert.equal(retry.length, 2, 'it asks again later'); checks++;
  d.getElementById('card-1').open = false;
  retry[1].fn();
  assert.deepEqual(submits, ['jr-filters'], 'refreshes the day once nobody reads'); checks++;

  // 2. a lazy body lands
  d.getElementById('jr-q').value = 'needle';
  const card = d.getElementById('card-1');
  const body = d.createElement('div');
  body.className = 'jr-body';
  body.innerHTML = '<ul class="jr-changes"><li><code class="jr-path" data-path="v">v</code></li></ul>'
    + '<ul class="jr-lines"><li data-jr-search="12:01:00 agent the needle line" data-jr-author="agent">a</li>'
    + '<li data-jr-search="12:02:00 agent another line" data-jr-author="agent">b</li></ul>';
  card.replaceChild(body, card.querySelector('.jr-body-lazy'));
  body.dispatchEvent(new w.CustomEvent('htmx:afterSettle', { bubbles: true }));
  assert.equal(body.querySelector('.jr-path')._jrBound, true, 'its paths open their history'); checks++;
  const lines = body.querySelectorAll('.jr-lines li');
  assert.equal(lines[0].hidden, false, 'a matching line stays'); checks++;
  assert.equal(lines[1].hidden, true, 'the search applies to the new lines'); checks++;

  // 3. a claim names its card
  const claimBox = d.createElement('div');
  claimBox.className = 'jr-claim';
  claimBox.setAttribute('data-run', '5');
  claimBox.setAttribute('data-uid', 'abc123:5');
  claimBox.innerHTML = '<input class="jr-who" value="carol"><input class="jr-note-in" value=""><button class="b">Save</button>';
  d.body.appendChild(claimBox);
  w.JournalPage.claim(claimBox.querySelector('.b'));
  assert.equal(posts.length, 1); checks++;
  assert.deepEqual(posts[0].body, { run_id: '5', uid: 'abc123:5', who: 'carol', note: '' }, 'the claim carries the card'); checks++;
  console.log(`all checks passed (${checks} checks)`);
} finally { w.close(); }
