/* docs/173 S2 -- journal.js against the real file under jsdom.
 * Pins: the since-last-visit marker (per chip, cards newer than the stamp
 * get the dot, the stamp moves), a path token opens the value-history
 * popover with its path, a claim POSTs run/who/note and remembers the name,
 * day() drives the filter form, copyDigest reads the strip.
 * Run: node tests/journal_page_selfcheck.cjs   (needs jsdom) */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const now = Date.now() / 1000;
const dom = new JSDOM('<!doctype html><html><body>'
  + '<form id="jr-filters"><input type="hidden" name="day" id="jr-day" value="2026-09-06"><input type="date" id="jr-day-pick" value="2026-09-06"></form>'
  + '<div id="jr-body"><div class="jr-counts" data-chip="PJ" data-day="2026-09-06"><span id="jr-since" hidden></span></div>'
  + '<div class="jr-digest"><div class="jr-digest-row"><span class="jr-digest-target">q3</span><a class="jr-pill">res spec <small>#1</small> ✓</a><span class="jr-arrow">→</span><a class="jr-pill">rabi <small>#2</small> ✗</a></div></div>'
  + '<details class="jr-card" data-ts="' + (now - 10) + '"><summary>new</summary><div class="jr-claim" data-run="2"><input class="jr-who"><input class="jr-note-in" value="n"><button class="claim-btn">Save</button></div></details>'
  + '<details class="jr-card" data-ts="' + (now - 100000) + '"><summary>old</summary><code class="jr-path" data-path="qubits.q4.f_01">qubits.q4.f_01</code></details>'
  + '</div></body></html>', { url: 'http://localhost/journal', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.navigator = window.navigator; global.localStorage = window.localStorage;
const opened = [];
window.FieldHistory = { open: function (el, p, input) { opened.push([el, p, input]); } };
global.FieldHistory = window.FieldHistory;
const submits = [];
window.htmx = { trigger: function (el, ev) { submits.push([el.id, ev]); } };
global.htmx = window.htmx;
const posts = [];
global.fetch = window.fetch = function (url, opts) {
  posts.push({ url: url, body: JSON.parse(opts.body) });
  return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } });
};

// a previous visit 50,000 s ago: the 10-s-old card is new, the old one is not
window.localStorage.setItem('quam_journal_seen:PJ', String(now - 50000));
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'journal.js'), 'utf8'), { filename: 'journal.js' });
window.JournalPage.init();   // what htmx:afterSwap does on the real page (jsdom's readyState is still 'loading' here)

(async () => {
  const cards = document.querySelectorAll('.jr-card');
  ok(cards[0].classList.contains('jr-new') && !cards[1].classList.contains('jr-new'), 'since marker: only the card newer than the last visit is new');
  ok(document.getElementById('jr-since').hidden === false && /1 new since/.test(document.getElementById('jr-since').textContent), 'since line says how many');
  const stamp = parseFloat(window.localStorage.getItem('quam_journal_seen:PJ'));
  ok(stamp > now - 5, 'the stamp moved to now, per chip');

  document.querySelector('.jr-path').dispatchEvent(new window.Event('click', { bubbles: true }));
  ok(opened.length === 1 && opened[0][1] === 'qubits.q4.f_01', 'a path token opens the value-history popover with its path');

  window.JournalPage.day('2026-09-05');
  ok(document.getElementById('jr-day').value === '2026-09-05' && document.getElementById('jr-day-pick').value === '2026-09-05', 'day() sets both fields');
  ok(submits.length === 1 && submits[0][0] === 'jr-filters' && submits[0][1] === 'submit', 'day() submits the filter form (htmx)');

  document.querySelector('.jr-who').value = '박OO';
  window.JournalPage.claim(document.querySelector('.claim-btn'));
  await new Promise(r => setTimeout(r, 5));
  ok(posts.length === 1 && posts[0].url === '/journal/claim' && posts[0].body.run_id === '2' && posts[0].body.who === '박OO' && posts[0].body.note === 'n', 'claim POSTs run, who, note');
  ok(window.localStorage.getItem('quam_actor_name') === '박OO', 'the name is remembered for next time (docs/173 S8: one key across the app)');
  ok(submits.length === 2, 'and the body re-fetches after a claim');

  const text = window.JournalPage.copyDigest(document.createElement('button'));
  ok(text === 'q3: res spec #1 ✓ -> rabi #2 ✗', 'copyDigest reads the strip as text: ' + JSON.stringify(text));

  console.log(fails ? ('FAILED ' + fails) : ('all checks passed (' + passes + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
