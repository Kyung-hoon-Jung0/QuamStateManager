/* Calibration log search: real read-route HTML and shipped JS under jsdom. */
'use strict';
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const assert = require('assert/strict');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (_) { console.error('jsdom not installed'); process.exit(2); }
const root = path.resolve(__dirname, '..');
const rendered = spawnSync(process.env.JOURNAL_SEARCH_PYTHON || 'python',
  ['-m', 'tests.test_journal_search'], { cwd: root, encoding: 'utf8',
    env: { ...process.env, PYTHONUTF8: '1' }, timeout: 90000 });
assert.equal(rendered.status, 0, rendered.stderr);
const fixture = JSON.parse(rendered.stdout);
const source = name => fs.readFileSync(path.join(root, 'quam_state_manager/web/static', name), 'utf8');
const grammar = source('search-query.js'), journal = source('journal.js');
const selected = process.argv[2];
let passes = 0, failures = 0;

function page(html = fixture.html) {
  const dom = new JSDOM(html, { url: 'http://localhost/journal', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  let clock = 0, next = 0, requests = 0, processed = 0;
  const timers = new Map();
  w.setTimeout = (fn, delay) => { const id = ++next; timers.set(id, { at: clock + delay, fn }); return id; };
  w.clearTimeout = id => timers.delete(id);
  w.htmx = { process() { processed++; }, trigger(el, name) { el.dispatchEvent(new w.Event(name, { bubbles: true, cancelable: true })); } };
  w.fetch = () => { requests++; throw Error('unexpected search request'); };
  d.querySelector('#jr-filters').addEventListener('submit', () => { requests++; });
  w.eval(grammar); w.eval(journal); w.JournalPage.init();
  const input = d.querySelector('#jr-q');
  return { w, d, input, dom,
    type(q) { input.value = q; input.dispatchEvent(new w.Event('input', { bubbles: true })); },
    advance(ms) { clock += ms; for (const [id, timer] of [...timers]) if (timer.at <= clock) { timers.delete(id); timer.fn(); } },
    enter(q) { input.value = q; input.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); },
    search(q) { input.value = q; input.dispatchEvent(new w.Event('search', { bubbles: true })); },
    visible(selector) { return [...d.querySelectorAll(selector)].filter(el => !el.closest('[hidden]')).length; },
    requests: () => requests, processed: () => processed,
  };
}
function pin(name, check, html) {
  if (selected && selected !== name) return;
  const p = page(html);
  try { check(p); passes++; console.log('ok - ' + name); }
  catch (e) { failures++; console.error('FAIL - ' + name + ': ' + e.message); }
  finally { p.dom.window.close(); }
}

pin('typing_is_debounced', p => {
  p.type('ramsey'); p.advance(149); assert.equal(p.visible('.jr-run'), 8);
  p.advance(1); assert.equal(p.visible('.jr-run'), 1);
});
pin('typing_burst_uses_latest_query', p => {
  p.type('ramsey'); p.advance(100); p.type('rabi'); p.advance(50);
  assert.equal(p.visible('.jr-run'), 8); p.advance(100);
  assert.equal(p.visible('#card-2'), 1); assert.equal(p.visible('#card-1'), 0);
});
pin('enter_flushes_without_request', p => {
  p.type('ramsey'); p.enter('rabi'); assert.equal(p.visible('#card-2'), 1);
  assert.equal(p.visible('#card-1'), 0); assert.equal(p.requests(), 0);
});
pin('form_submit_filters_locally', p => {
  p.input.value = 'ramsey'; p.d.querySelector('#jr-filters').dispatchEvent(new p.w.Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(p.visible('.jr-run'), 1); assert.equal(p.requests(), 0);
});
pin('typing_never_requests_the_server', p => {
  p.type('ramsey'); p.advance(150); assert.equal(p.requests(), 0);
  assert.equal(p.d.querySelector('#jr-filters').getAttribute('hx-trigger'), 'submit');
});
pin('same_grammar_and_case_in_every_group', p => {
  p.enter('RAMSEY qA1'); assert.equal(p.visible('.jr-run'), 1); assert.equal(p.visible('.jr-write'), 1);
  assert.equal(p.visible('.jr-loose li'), 0); assert.equal(p.visible('.jr-unassigned li'), 0);
});
pin('standalone_pipe_is_or', p => {
  p.enter('ramsey | rabi'); assert.equal(p.visible('.jr-run'), 2);
  assert.equal(p.visible('.jr-write'), 2); assert.equal(p.visible('.jr-loose li'), 3);
});
pin('embedded_pipe_stays_literal', p => {
  p.enter('ramsey|rabi'); assert.equal(p.visible('[data-jr-search]'), 0);
  assert.equal(p.visible('.jr-empty'), 1);
});
pin('loose_human_and_agent_lines_filter', p => {
  p.enter('ramsey'); assert.equal(p.visible('.jr-loose li'), 2);
  assert.equal(p.visible('.jr-loose li[data-jr-author="agent"]'), 1);
  assert.equal(p.d.querySelector('.jr-loose .jr-line-count').textContent, '2 journal lines not attached to a run');
});
pin('attached_lines_filter', p => {
  p.enter('ramsey'); assert.equal(p.visible('#card-1 .jr-lines li'), 1);
  assert.equal(p.visible('#card-1 .jr-lines li[data-jr-author="human"]'), 0);
});
pin('unassigned_tail_is_searchable', p => {
  p.enter('tailonly'); assert.equal(p.visible('.jr-unassigned li'), 1);
  assert.equal(p.d.querySelector('.jr-unassigned .jr-line-count').textContent,
    '1 journal line from a session that named no chip.');
});
pin('a_line_only_match_is_not_empty', p => {
  p.enter('looseonly'); assert.equal(p.visible('.jr-run,.jr-write'), 0);
  assert.equal(p.visible('.jr-loose'), 1); assert.equal(p.visible('.jr-empty'), 0);
});
pin('a_write_only_match_is_not_empty', p => {
  p.enter('qubits.qA1.ramsey'); assert.equal(p.visible('.jr-run'), 0);
  assert.equal(p.visible('.jr-write'), 1); assert.equal(p.visible('.jr-empty'), 0);
});
pin('an_unassigned_only_match_is_not_empty', p => {
  p.enter('tailonly'); assert.equal(p.visible('.jr-empty'), 0);
});
pin('nothing_matching_anywhere_shows_empty', p => {
  p.enter('missing-token'); assert.equal(p.visible('.jr-empty'), 1);
  assert.equal(p.d.querySelector('.jr-empty').textContent, 'Nothing matches the filter.');
});
pin('run_header_tracks_filtered_count', p => {
  p.enter('ramsey'); assert.equal(p.d.querySelector('.jr-runs-head .jr-sec-count').textContent, '1');
  p.enter('tailonly'); assert.equal(p.visible('.jr-runs-head'), 0);
});
pin('timeline_and_target_count_follow_runs', p => {
  p.enter('ramsey'); assert.equal(p.visible('.jr-pill'), 1); assert.equal(p.visible('.jr-digest-row'), 1);
  assert.equal(p.visible('.jr-seg'), 1); assert.equal(p.d.querySelector('.jr-digest-more').textContent, 'all 1 targets');
  p.enter('tailonly'); assert.equal(p.visible('.jr-digest'), 0);
});
pin('clear_restores_all_groups', p => {
  p.enter('missing-token'); p.search('');
  assert.equal(p.visible('.jr-run'), 8); assert.equal(p.visible('.jr-write'), 2);
  assert.equal(p.visible('.jr-loose li'), 4); assert.equal(p.visible('.jr-unassigned li'), 8);
  assert.equal(p.visible('.jr-digest-row'), 8); assert.equal(p.visible('.jr-empty'), 0);
});
pin('day_totals_are_labelled', p => {
  assert.match(p.d.querySelector('.jr-counts').textContent, /Day totals/);
  p.w.localStorage.setItem('quam_journal_seen:chipX', '0.5'); p.w.JournalPage.markSince();
  assert.match(p.d.querySelector('#jr-since').textContent, /Day total:/);
});
pin('author_change_filters_all_lines', p => {
  const author = p.d.querySelector('#jr-author'); author.add(new p.w.Option('agent', 'agent'));
  author.value = 'agent'; author.dispatchEvent(new p.w.Event('change', { bubbles: true }));
  assert.equal(p.visible('.jr-run,.jr-write'), 0); assert.equal(p.visible('.jr-loose li'), 2);
  assert.equal(p.visible('.jr-unassigned'), 0); assert.equal(p.visible('.jr-empty'), 0);
});
pin('day_navigation_still_requests', p => {
  p.w.JournalPage.day('2026-10-03'); assert.equal(p.requests(), 1);
  assert.equal(p.d.querySelector('#jr-day').value, '2026-10-03');
});
pin('filtered_load_cache_restores_order_and_links', p => {
  assert.equal(p.visible('.jr-run'), 1); p.search('');
  assert.deepEqual([...p.d.querySelectorAll('.jr-cards > details')].map(el => el.id),
    ['card-1','card-2','card-3','card-4','card-5','card-6','card-7','card-8','write-1','write-2']);
  assert.equal(p.processed(), 1); const paths = [];
  p.w.FieldHistory = { open(el, value) { paths.push(value); } };
  p.d.querySelector('#write-2 .jr-path').dispatchEvent(new p.w.Event('click', { bubbles: true }));
  assert.deepEqual(paths, ['qubits.qA2.rabi']);
}, fixture.filtered);
pin('day_swap_reindexes_and_uses_current_query', p => {
  p.input.value = 'rabi'; const body = p.d.querySelector('#jr-body'); body.innerHTML = fixture.swap;
  body.dispatchEvent(new p.w.Event('htmx:afterSwap', { bubbles: true }));
  assert.equal(p.visible('#card-2'), 1); assert.equal(p.visible('#card-1'), 0);
  const author = p.d.querySelector('#jr-author'); author.value = 'human:user-a';
  author.dispatchEvent(new p.w.Event('change', { bubbles: true }));
  assert.equal(p.visible('.jr-run'), 1); p.search(''); assert.equal(p.visible('.jr-run'), 8);
});
pin('server_grammar_matches_client_grammar', p => {
  const counts = [1, 2, 0]; let i = 0;
  for (const html of Object.values(fixture.grammar)) {
    const dom = new JSDOM(html); assert.equal(dom.window.document.querySelectorAll('.jr-run').length, counts[i++]); dom.window.close();
  }
});

if (!passes && !failures) throw Error('unknown pin');
console.log(failures ? `FAILED ${failures}/${passes + failures}` : `all checks passed (${passes} pins)`);
process.exit(failures ? 1 : 0);
