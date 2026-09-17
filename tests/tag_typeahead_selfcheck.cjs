/* docs/182 — the words a PERSON typed, in the popup, in the customer's format.
 *
 *   "검색 pop up할때 뜨는건 run 번호: tag 이름 (혹은 note) 이렇게 뜨도록.
 *    note는 내용이 다 담기게 하는게 아니고 그냥 note (검색어 ...) 그냥 이렇게
 *    compact하게."
 *
 * Pins, against the real shipped sidebar-typeahead.js:
 *   T1  a tag row reads `#<run>: <tag>` — the run number first, as asked
 *   T2  a note row reads `#<run>: note (<the word that matched>)` and carries
 *       NONE of the note's other text
 *   T3  accepting inserts the grammar's own scope, so the search finds it
 *   T4  a one-letter stem offers nothing (it would match half the archive)
 *   T5  the cap is SAID, not silently applied
 *   T6  a value the grammar cannot carry is not offered at all (docs/175: a
 *       suggestion that finds nothing is worse than no suggestion)
 *   T7  a value with a space is quoted, because the tokenizer splits on it
 *
 * Run: node tests/tag_typeahead_selfcheck.cjs   (needs jsdom)
 */
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
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM('<!doctype html><html><body>'
  + '<input id="sidebar-filter-input"><div id="status-bar"></div></body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.KeyboardEvent = window.KeyboardEvent;
global.localStorage = window.localStorage;
// CLAUDE.md standing rule: this file is evaluated through `window.eval` without
// `runScripts`, so it compiles in the NODE realm and a bare `fetch` never sees
// `window.fetch`. Bridge both, or a stub is silently ignored (docs/191 N04 —
// the first cut of those pins failed for exactly this and nothing else).
window.fetch = global.fetch = function () { return new window.Promise(function () {}); };
window.requestAnimationFrame = function (f) { return setTimeout(f, 0); };

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'sidebar-typeahead.js'), 'utf8'));

const TV = window.TagVocab;
ok(!!TV, 'TagVocab is exported');

TV._set({
  v: 'x',
  tags: [
    { t: 'flagged', n: 2, r: [12, 11] },
    { t: 'needs rerun', n: 1, r: [9] },
    { t: 'say "hi"', n: 1, r: [8] }
  ],
  notes: [
    { r: 11, w: ['recalibrated', 'fridge', 'cycled'] },
    { r: 7, w: ['todo', 'flagged'] }
  ]
});

// ── T1/T2, as amended by docs/191 N03 ──────────────────────────────────────
//
// The customer's later ask: "파라미터는 param, 태그는 tag, 노트는 note 라고
// 검색어 바로 앞에 표시하면서 뜨게 하자" — so the KIND leads the row as a badge
// and the LABEL is the term itself. docs/182's requirement that the run number
// be visible is kept: it moved to the meta, which is where the rest of this box
// already puts "where / how many".
let rows = TV.items('flag');
let labels = rows.map(function (r) { return r.label; });
ok(labels.indexOf('flagged') >= 0,
  'T1: the label is the TERM itself — ' + labels.join(' | '));
ok(rows.filter(function (r) { return r.label === 'flagged' && r.kind === 'tag'; }).length >= 1,
  'T1: …carrying its kind, so the badge can lead the row');
ok(rows.filter(function (r) { return r.kind === 'tag'; })
     .every(function (r) { return /^(#\d+|\d+ runs)$/.test(r.meta || ''); }),
  'T1: …and the run number the customer asked for is in the meta — '
  + rows.filter(function (r) { return r.kind === 'tag'; }).map(function (r) { return r.meta; }).join(' | '));

// ── T2: a note row ─────────────────────────────────────────────────────────
ok(rows.filter(function (r) { return r.kind === 'note' && r.label === 'flagged'; }).length === 1,
  'T2: a note row is the word that matched, marked as a note — ' + labels.join(' | '));
rows = TV.items('fridge');
labels = rows.map(function (r) { return r.label; });
ok(labels.length === 1 && labels[0] === 'fridge' && rows[0].kind === 'note',
  'T2: exactly the matched word — ' + labels.join(' | '));
ok(labels[0].indexOf('recalibrated') < 0 && labels[0].indexOf('cycled') < 0,
  'T2: and none of the note’s other text');
ok(rows[0].meta === '#11', 'T2: …with its run in the meta — ' + rows[0].meta);

// ── T3: what accepting inserts ─────────────────────────────────────────────
rows = TV.items('flag');
const tagRow = rows.filter(function (r) { return r.kind === 'tag' && r.label === 'flagged'; })[0];
ok(tagRow && tagRow.insert === 'tag:flagged',
  'T3: a tag row inserts the grammar’s own tag scope — ' + (tagRow && tagRow.insert));
const noteRow = rows.filter(function (r) { return r.kind === 'note'; })[0];
ok(noteRow && noteRow.insert === 'note:flagged',
  'T3: a note row inserts the note scope — ' + (noteRow && noteRow.insert));
ok(tagRow.meta === '2 runs',
  'T3: …and the row says how many runs the token will actually find — ' + tagRow.meta);

// ── T4: one letter is not a query ──────────────────────────────────────────
ok(TV.items('f').length === 0, 'T4: a one-letter stem offers nothing');
ok(TV.items('').length === 0, 'T4: …and neither does an empty one');

// ── T5: the cap is said ────────────────────────────────────────────────────
const many = [];
for (let i = 0; i < 40; i++) many.push(100 + i);
TV._set({ v: 'y', tags: [{ t: 'bulk', n: many.length, r: many }], notes: [] });
rows = TV.items('bulk');
const said = rows.filter(function (r) { return r.note; });
ok(said.length === 1 && /and \d+ more/.test(said[0].label),
  'T5: the rows it could not show are counted out loud — '
  + (said[0] && said[0].label));
ok(rows.length - said.length <= 8, 'T5: …and the visible rows are capped');

// ── T6/T7: what the grammar can carry ──────────────────────────────────────
ok(TV._scoped('tag', 'say "hi"') === null,
  'T6: a value the tokenizer would mangle is not offered at all');
ok(TV._scoped('tag', 'needs rerun') === 'tag:"needs rerun"',
  'T7: a value with a space is quoted — ' + TV._scoped('tag', 'needs rerun'));
ok(TV._scoped('note', 'todo') === 'note:todo', 'T7: a plain value is bare');

// …and such a tag gets NO row at all — an un-acceptable row in the list is a
// trap, not honesty. It is still counted out loud rather than dropped in
// silence, which is how core/param_vocab treats its own `omitted`.
TV._set({ v: 'z', tags: [{ t: 'say "hi"', n: 1, r: [8] }], notes: [] });
rows = TV.items('say');
ok(rows.filter(function (r) { return !r.note; }).length === 0,
  'T6: …and it is not offered as a row that cannot be accepted');
ok(rows.length === 1 && /cannot be searched/.test(rows[0].label),
  'T6: …but it is said out loud — ' + (rows[0] && rows[0].label));


// ── N03: the KIND leads the row, as a badge ────────────────────────────────
//
// Customer: "파라미터는 param, 태그는 tag, 노트는 note 라고 검색어 바로 앞에
// 표시하면서 뜨게 하자 ... param은 배치형식으로 compact하게 살짝 SM의 푸른색
// 스타일로." Rendered here through the REAL _row/_render, not asserted on the
// item objects alone — a kind nothing draws is not a badge.
{
  const T = window.Typeahead;
  const panel = (function () {
    // _render is private; drive it the way the widget does, through attach's
    // refresh path, by feeding suggest() a world we control.
    // A FRESH id: `attach` is idempotent per input, and SidebarTypeahead has
    // already claimed the real box — attaching to it again is a silent no-op
    // and the REAL suggest answers instead (it said "loading parameters…").
    const box = window.document.createElement('input');
    box.id = 'n03-probe-box';
    window.document.body.appendChild(box);
    T.attach('n03-probe-box', {
      suggest: function () {
        return { items: [
          { label: 'multiplexed', kind: 'param', meta: '2 values', insert: 'multiplexed=' },
          { label: 'flagged', kind: 'tag', meta: '#41', insert: 'tag:flagged' },
          { label: 'fridge', kind: 'note', meta: '#41', insert: 'note:fridge' },
          // carries a kind ON PURPOSE: without it the `it.note ? null :` guard
          // has nothing to suppress and the pin below passes with it deleted.
          { label: 'a heading', note: true, kind: 'param' }
        ] };
      }
    });
    const inp = box;
    inp.value = 'zz';
    inp.setSelectionRange(2, 2);
    inp.dispatchEvent(new window.Event('input', { bubbles: true }));
    return window.document.getElementById('sm-typeahead-panel')
        || window.document.querySelector('.sm-typeahead');
  })();

  ok(!!panel, 'N03: the panel renders');
  const lis = panel ? Array.prototype.slice.call(panel.querySelectorAll('li')) : [];
  const kinds = lis.map(function (li) {
    const k = li.querySelector('.sm-th-kind');
    return k ? k.textContent : null;
  });
  ok(kinds[0] === 'param' && kinds[1] === 'tag' && kinds[2] === 'note',
    'N03: each row wears its own kind — ' + JSON.stringify(kinds));
  ok(lis[0].getAttribute('data-kind') === 'param',
    'N03: …and says so on the row, which is what the stylesheet keys on');

  const first = lis[0];
  const badge = first.querySelector('.sm-th-kind');
  const label = first.querySelector('.sm-th-label');
  ok(badge && label && badge.compareDocumentPosition(label) & window.Node.DOCUMENT_POSITION_FOLLOWING,
    'N03: the badge comes BEFORE the term, which is what was asked');
  ok(label.textContent === 'multiplexed',
    'N03: and the term is the label, not decorated into it — ' + label.textContent);

  ok(kinds[3] === null && !lis[3].hasAttribute('data-kind'),
    'N03: a heading row is not a choice, so it wears no badge');

  // N03: the full text of an ellipsizable label stays reachable
  const tagLi = lis[1], noteLi = lis[2];
  ok(tagLi.title === 'flagged' && noteLi.title === 'fridge',
    'N03: a tag/note row keeps its full text in the title, since the label may be cut');
  ok(!lis[0].title, 'N03: …and a param key, which is never cut, needs none');
}

// ── N04: the vocabulary re-checks itself ───────────────────────────────────
//
// Customer: "note랑 tag는 사용자가 업데이트/신규생성/삭제 할때 잘 작동하게 해야
// 할거야." `load` was called once, lazily, and never again: a tag created while
// the page was open was not offered until a reload, and a deleted one was
// offered for ever — inserting a token that now finds nothing.
{
  ok(typeof window.TagVocab.revalidate === 'function',
    'N04: the vocabulary can be asked whether it moved');

  const seen = [];
  const realFetch = global.fetch;
  window.fetch = global.fetch = function (u) {
    seen.push(String(u));
    return Promise.resolve({ status: 204, json: function () { return Promise.resolve(null); } });
  };
  window.TagVocab._set({ v: 'v1', tags: [{ t: 'flagged', n: 1, r: [1] }], notes: [] });
  window.TagVocab.revalidate();
  ok(seen.length === 1 && /\/workspace\/tag-vocab/.test(seen[0]),
    'N04: it asks the vocabulary route — ' + seen.join(' | '));
  ok(/[?&]v=v1(&|$)/.test(seen[0]),
    'N04: …conditionally on what it already has, so an unchanged archive costs a 204 — '
    + seen[0]);
  window.fetch = global.fetch = realFetch;

  // …and the sidebar box really asks for it when it takes focus. Nothing tested
  // the WIRING, so `revalidate: null` in the attach config passed the sweep.
  let asked = 0;
  const realRv = window.TagVocab.revalidate;
  window.TagVocab.revalidate = function () { asked++; };
  const realBox = window.document.getElementById('sidebar-filter-input');
  realBox.dispatchEvent(new window.Event('focusin', { bubbles: true }));
  ok(asked === 1, 'N04: focusing the sidebar box re-checks the vocabulary — asked ' + asked);
  window.TagVocab.revalidate = realRv;
}

// ── N03: a PARAM row carries its kind, from the REAL suggester ─────────────
(async function () {
  const SB = window.SidebarTypeahead;
  ok(!!SB && typeof SB.suggest === 'function', 'N03: the param suggester is reachable');
  global.fetch = window.fetch = function () {
    return Promise.resolve({
      status: 200, ok: true,
      json: function () {
        return Promise.resolve({ v: 'p1', keys: [
          { k: 'multiplexed', n: 39, v: [['true', 20], ['false', 19]] }
        ] });
      }
    });
  };
  SB.load(true);
  await new Promise(function (r) { setTimeout(r, 10); });
  const got = SB.suggest('key', null, 'multip');
  const row = got && got.items && got.items.filter(function (i) { return !i.note; })[0];
  ok(row && row.label === 'multiplexed',
    'N03: the real suggester offers the key — ' + (row && row.label));
  ok(row && row.kind === 'param',
    'N03: …stamped as a param, so the badge can lead it — ' + (row && row.kind));

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'tag_typeahead_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();


