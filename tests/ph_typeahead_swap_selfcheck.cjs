/* RAM P8 follow-up -- the Changes page's parameter typeahead across the
 * filter's own htmx swaps, under the REAL bundled htmx.
 *
 * The filter box swaps #param-history-root on every debounced keyup, and the
 * box itself is hx-preserve'd (focus and in-flight keys survive). The
 * typeahead's handler therefore survives too -- and it used to hold the
 * datalist it found at bind time: after the first swap it filled a DETACHED
 * list while the box pointed at the fresh, empty one (measured in real Chrome
 * on big30x: 0 options after every swap). Pinned here:
 *   T1 a typeahead response that lands after a swap fills the list the box
 *      points at (the swap happens between the fetch and its answer, the real
 *      order: 250 ms typeahead debounce vs 400 ms filter debounce + request)
 *   T2 the list survives a later swap with its options (hx-preserve)
 *   T3 an older query answering after a newer one never overwrites it
 *
 *   node tests/ph_typeahead_swap_selfcheck.cjs FRAG_A.html FRAG_B.html
 * (the two fragments are the route's real output, rendered by the pytest
 * driver in tests/test_param_history_ram.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
  console.log('SKIP: jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const HTMX_SRC = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'htmx.min.js'), 'utf8');
const [FA, FB] = process.argv.slice(2).map(f => fs.readFileSync(f, 'utf8'));

let failures = 0, asserts = 0;
function check(name, cond, detail) {
  asserts++;
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}

// htmx scans hx-on attributes through XPathEvaluator, which jsdom cannot run;
// the fragments carry no hx-on (settle_config_selfcheck.cjs, same shim)
const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

const sleep = (w, ms) => new Promise(r => w.setTimeout(r, ms));

(async function main() {
  const html = '<!doctype html><html><head>' +
    '<script>' + XPATH_SHIM + '</scr' + 'ipt>' +
    '<script>' + HTMX_SRC + '</scr' + 'ipt>' +
    '</head><body><main id="main">' + FA + '</main></body></html>';
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/param-history/changes' });
  const w = dom.window;
  await new Promise(r => (w.document.readyState !== 'loading') ? r()
    : w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(r, 0)));

  // fetch stub: every request is parked until the test answers it
  const pending = [];
  w.fetch = function (url) {
    return new Promise(res => pending.push({ url: String(url), res }));
  };
  const answer = (i, paths) => pending[i].res({
    ok: true, json: () => Promise.resolve({ ok: true, results: paths.map(p => ({ path: p, changes: 2 })) }) });
  const box = () => w.document.querySelector('#param-history-root input[name=prefix]');
  const list = () => w.document.getElementById(box().getAttribute('list'));
  const type = v => { box().value = v; box().dispatchEvent(new w.Event('input', { bubbles: true })); };
  const swap = frag => w.htmx.swap('#param-history-root', frag, { swapStyle: 'outerHTML' });

  check('T0 fixture: the box and its datalist are rendered',
        !!box() && !!list(), String(!!box()));
  const box0 = box();

  // T1: fetch starts, THEN the filter's swap lands, THEN the answer
  type('qu');
  await sleep(w, 300);
  check('T1a the typeahead fetched', pending.length === 1 && /q=qu$/.test(pending[0].url),
        JSON.stringify(pending.map(p => p.url)));
  swap(FB);
  await sleep(w, 30);
  check('T1b the box survived the swap (same element)', box() === box0);
  answer(0, ['qubits.q1.T1', 'qubits.q2.T1']);
  await sleep(w, 30);
  check('T1c the answer fills the list the box points at',
        list() && list().options.length === 2, list() ? String(list().options.length) : 'no list');

  // T2: another swap keeps the list and its options
  swap(FB);
  await sleep(w, 30);
  check('T2 the list keeps its options across a later swap',
        list() && list().options.length === 2 && list().options[0].value === 'qubits.q1.T1',
        list() ? String(list().options.length) : 'no list');

  // T3: an older answer arriving after a newer one is dropped
  type('qub');
  await sleep(w, 300);
  type('qubi');
  await sleep(w, 300);
  check('T3a two more fetches', pending.length === 3, String(pending.length));
  answer(2, ['qubits.q10.f_01']);
  await sleep(w, 20);
  answer(1, ['stale.old.path']);
  await sleep(w, 20);
  check('T3b the newer query wins', list() && list().options.length === 1 &&
        list().options[0].value === 'qubits.q10.f_01',
        list() ? Array.from(list().options).map(o => o.value).join(',') : 'no list');

  console.log(failures ? `FAILED ${failures} of ${asserts}` : `ALL OK (${asserts} assertions)`);
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
