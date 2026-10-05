/* docs/291: a Calibration log day too large to send whole -- the shipped
 * journal.js under jsdom, on real server HTML (a page of 4 of 10 rows):
 * typing asks the server; a reload of the day keeps the rows shown; "show
 * earlier" puts the rows above, in time order, once; a link to a row not on
 * the page fetches the rows around it or says why it cannot; "Day total"
 * counts the whole day; a filter's answer keeps the visit's marks. */
'use strict';
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const assert = require('assert/strict');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (_) { console.error('jsdom not installed'); process.exit(2); }
const root = path.resolve(__dirname, '..');
const rendered = spawnSync(process.env.BIG_DAY_PYTHON || 'python', ['-m', 'tests.test_calibration_log_big_day'],
  { cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' }, timeout: 120000 });
assert.equal(rendered.status, 0, rendered.stderr);
const fx = JSON.parse(rendered.stdout.split('\n').filter(l => l.startsWith('{')).pop());
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const grammar = source('search-query.js'), journal = source('journal.js');
const selected = process.argv[2];
let passes = 0, failures = 0;

async function page(url = 'http://localhost/journal', before) {
  const dom = new JSDOM(fx.page, { url, runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  let clock = 0, next = 0;
  const timers = new Map(), sent = [], fetched = [], processed = [], scrolled = [];
  const fail = new Set();
  w.setTimeout = (fn, delay) => { const id = ++next; timers.set(id, { at: clock + delay, fn }); return id; };
  w.clearTimeout = id => timers.delete(id);
  w.htmx = {
    process(el) { processed.push(el); },
    trigger(el, name) {
      if (name === 'submit') sent.push(Object.assign({}, el._jrExtra));
      el.dispatchEvent(new w.Event(name, { bubbles: true, cancelable: true }));
    },
  };
  w.fetch = url => {
    fetched.push(url);
    if (fail.has(url)) return Promise.reject(new Error('offline'));
    assert.ok(url in fx.slices, 'an unknown slice was asked for: ' + url);
    return Promise.resolve({ ok: true, text: () => Promise.resolve(fx.slices[url]) });
  };
  w.Element.prototype.scrollIntoView = function () { scrolled.push(this.id); };
  if (before) before(w);
  w.eval(grammar); w.eval(journal);
  // the page script starts on DOMContentLoaded, as in a browser
  for (let i = 0; i < 50 && d.readyState === 'loading'; i++) await new Promise(r => setTimeout(r, 0));
  assert.notEqual(d.readyState, 'loading');
  const input = d.querySelector('#jr-q');
  const body = d.querySelector('#jr-body');
  return { w, d, dom, input, body, sent, fetched, processed, scrolled, fail,
    rows: () => [...d.querySelectorAll('#jr-body .jr-cards > .jr-card')].map(el => el.id),
    type(q) { input.value = q; input.dispatchEvent(new w.Event('input', { bubbles: true })); },
    advance(ms) { clock += ms; for (const [id, t] of [...timers]) if (t.at <= clock) { timers.delete(id); t.fn(); } },
    swap(html) { body.innerHTML = html; body.dispatchEvent(new w.Event('htmx:afterSwap', { bubbles: true })); },
    strip() { const t = d.createElement('template'); t.innerHTML = fx.strip; d.querySelector('.jr-strip-later').replaceWith(t.content); },
    press(where) { return w.JournalPage.more(d.querySelector('.jr-more-' + where + ' .jr-more')); },
  };
}

async function pin(name, check, url, before) {
  if (selected && selected !== name) return;
  const p = await page(url, before);
  try { await check(p); passes++; console.log('ok - ' + name); }
  catch (e) { failures++; console.error('FAIL - ' + name + ': ' + (e && e.stack || e)); }
  finally { p.dom.window.close(); }
}

(async () => {
  await pin('typing_on_a_paged_day_asks_the_server', p => {
    assert.deepEqual(p.rows(), ['card-7', 'card-8', 'card-9', 'card-10']);
    assert.equal(p.d.querySelectorAll('#jr-body [data-jr-search]').length, 0, 'rows carry no search text');
    p.sent.length = 0;
    p.type('qA2'); p.advance(249); assert.equal(p.sent.length, 0, 'debounced');
    p.advance(1); assert.deepEqual(p.sent, [{ reuse: '1' }], 'one request, the day re-filtered');
    assert.equal(p.d.querySelectorAll('#jr-body .jr-card[hidden]').length, 0, 'rows are never filtered here');
    p.sent.length = 0;
    p.type(''); p.advance(250); assert.equal(p.sent.length, 0, 'the filter the page shows asks nothing');
  });

  await pin('the_filter_form_request_carries_the_ask', p => {
    const form = p.d.querySelector('#jr-filters');
    form._jrExtra = { at: 'card-1', from: '' };
    const detail = { elt: form, parameters: { day: fx.day } };
    p.d.dispatchEvent(new p.w.CustomEvent('htmx:configRequest', { detail }));
    assert.deepEqual(detail.parameters, { day: fx.day, at: 'card-1' });
    const other = { elt: p.body, parameters: {} };
    p.d.dispatchEvent(new p.w.CustomEvent('htmx:configRequest', { detail: other }));
    assert.deepEqual(other.parameters, {});
  });

  await pin('a_reload_of_the_day_keeps_its_rows', async p => {
    p.sent.length = 0;
    p.w.JournalPage.day(fx.day);
    assert.deepEqual(p.sent, [{ from: 'card-7' }]);
    await p.press('earlier');
    p.w.JournalPage.day(fx.day);
    assert.deepEqual(p.sent[1], { from: 'card-3' });
    p.w.JournalPage.day('2026-10-03');
    assert.deepEqual(p.sent[2], {}, 'another day starts at its newest rows');
  });

  await pin('show_earlier_puts_the_rows_above_once_in_order', async p => {
    const first = p.d.querySelector('.jr-more-earlier .jr-more');
    assert.equal(first.textContent, 'Show 4 earlier');
    assert.equal(p.d.querySelector('.jr-more-earlier .jr-more-note').textContent, '(6 more on this day)');
    await p.press('earlier');
    assert.deepEqual(p.rows(), ['card-3', 'card-4', 'card-5', 'card-6', 'card-7', 'card-8', 'card-9', 'card-10']);
    assert.equal(p.d.querySelector('.jr-window-n').textContent, '8');
    assert.equal(p.d.querySelector('.jr-more-earlier .jr-more').textContent, 'Show 2 earlier');
    assert.ok(p.processed.some(el => el.id === 'card-3'), 'the new rows are wired (their bodies load on open)');
    await p.press('earlier');
    assert.deepEqual(p.rows(), Array.from({ length: 10 }, (_, i) => 'card-' + (i + 1)));
    assert.equal(p.d.querySelectorAll('.jr-more-row').length, 0, 'nothing more to show');
    assert.equal(p.d.querySelector('.jr-window-n').textContent, '10');
    assert.equal(p.fetched.length, 2);
  });

  await pin('a_failed_slice_can_be_pressed_again', async p => {
    const url = p.d.querySelector('.jr-more-earlier .jr-more').getAttribute('data-url');
    p.fail.add(url);
    await p.press('earlier');
    const btn = p.d.querySelector('.jr-more-earlier .jr-more');
    assert.equal(btn.disabled, false);
    assert.equal(btn.textContent, 'Retry: Show 4 earlier');
    assert.deepEqual(p.rows(), ['card-7', 'card-8', 'card-9', 'card-10']);
    p.fail.delete(url);
    await p.press('earlier');
    assert.equal(p.rows().length, 8);
  });

  await pin('the_strip_arrives_after_the_rows_and_folds_back', p => {
    const later = p.d.querySelector('.jr-strip-later');
    assert.ok(later && later.getAttribute('hx-trigger') === 'load', 'the rows come first');
    assert.equal(p.d.querySelectorAll('.jr-pill').length, 0);
    p.strip();
    // the page's own search never hides what the server rendered for the whole day
    const body = p.d.createElement('div');
    body.className = 'jr-body';
    p.d.getElementById('card-9').appendChild(body);
    body.dispatchEvent(new p.w.Event('htmx:afterSettle', { bubbles: true }));
    assert.equal(p.d.querySelectorAll('.jr-pill:not([hidden])').length, 10, 'every pill stays');
    assert.equal(p.d.querySelector('.jr-runs-head').hidden, false);
    assert.equal(p.d.querySelector('.jr-runs-head .jr-sec-count').textContent, '10', 'the whole day, not the rows held');
    assert.equal(p.d.querySelector('.jr-empty').hidden, true, 'rows are matches');
    const btn = p.d.querySelector('.jr-digest-more');
    assert.equal(btn.textContent, 'all 10 runs');
    assert.ok(p.d.querySelector('.jr-digest').classList.contains('jr-digest-folded'));
    p.w.JournalPage.toggleDigest(btn);
    assert.equal(btn.textContent, 'fewer');
    p.w.JournalPage.toggleDigest(btn);
    assert.equal(btn.textContent, 'all 10 runs', 'folding back says what unfolding shows');
    assert.ok(p.d.querySelector('.jr-digest').classList.contains('jr-digest-folded'));
  });

  await pin('a_link_to_a_row_not_on_the_page_fetches_its_rows', p => {
    p.sent.length = 0;
    p.strip();
    const pill = p.d.querySelector('.jr-pill[href="#card-1"]');
    assert.ok(pill && !p.d.getElementById('card-1'));
    const ev = new p.w.MouseEvent('click', { bubbles: true, cancelable: true });
    pill.dispatchEvent(ev);
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(p.sent, [{ at: 'card-1' }]);
    p.swap(fx.at);
    assert.deepEqual(p.rows().slice(0, 4), ['card-1', 'card-2', 'card-3', 'card-4']);
    assert.deepEqual(p.scrolled, ['card-1']);
    assert.ok(p.d.getElementById('card-1').classList.contains('jr-jumped'));
    // a row on the page: the browser's own jump, nothing asked
    p.strip();
    const near = new p.w.MouseEvent('click', { bubbles: true, cancelable: true });
    p.d.querySelector('.jr-pill[href="#card-2"]').dispatchEvent(near);
    assert.equal(near.defaultPrevented, false);
    assert.equal(p.sent.length, 1);
  });

  await pin('a_jump_that_cannot_land_says_so', p => {
    assert.equal(p.w.JournalPage.reveal('card-99'), true);
    p.swap(fx.page.slice(fx.page.indexOf('<div id="jr-body">') + '<div id="jr-body">'.length));
    const note = p.d.querySelector('.jr-jump-miss');
    assert.ok(note, 'the page says the row is not shown');
    assert.match(note.textContent, /not on this day, or the filter hides it/);
  });

  await pin('a_link_on_load_opens_the_page_on_its_row', p => {
    assert.ok(p.sent.some(x => x.at === 'card-1'), JSON.stringify(p.sent));
  }, 'http://localhost/journal#card-1');

  await pin('day_total_counts_rows_not_on_the_page', async p => {
    // a visit between run 4 and run 5: 6 runs are new, 4 of them on the page
    p.w.localStorage.setItem('quam_journal_seen:chipX', String(fx.ts[3] + 1));
    p.w.JournalPage.markSince();
    assert.match(p.d.querySelector('#jr-since').textContent, /^Day total: 6 new since your last visit/);
    assert.equal(p.d.querySelectorAll('.jr-card.jr-new').length, 4);
    // the earlier rows a press brings are marked against the same visit
    await p.press('earlier');
    assert.deepEqual([...p.d.querySelectorAll('.jr-card.jr-new')].map(el => el.id),
      ['card-5', 'card-6', 'card-7', 'card-8', 'card-9', 'card-10']);
  });

  await pin('a_filters_answer_keeps_the_visit_and_its_marks', async p => {
    p.w.localStorage.setItem('quam_journal_seen:chipX', String(fx.ts[4] + 1));
    p.w.JournalPage.markSince();                       // the day lands: the visit is read, then stamped
    const stamp = p.w.localStorage.getItem('quam_journal_seen:chipX');
    assert.notEqual(stamp, String(fx.ts[4] + 1));
    p.swap(fx.filtered);                               // the server's answer to a filter of the same day
    assert.equal(p.w.localStorage.getItem('quam_journal_seen:chipX'), stamp, 'a filter is not a visit');
    assert.match(p.d.querySelector('#jr-since').textContent, /^Day total: 5 new/);
    assert.deepEqual([...p.d.querySelectorAll('.jr-card.jr-new')].map(el => el.id), ['card-7', 'card-9']);
    await p.press('earlier');                          // the rows a press brings are marked the same way
    assert.deepEqual([...p.d.querySelectorAll('.jr-card.jr-new')].map(el => el.id), ['card-7', 'card-9']);
    assert.ok(p.rows().includes('card-1'));
  });

  if (!passes && !failures) throw Error('unknown pin');
  console.log(failures ? `FAILED ${failures}/${passes + failures}` : `all checks passed (${passes} pins)`);
  process.exit(failures ? 1 : 0);
})();
