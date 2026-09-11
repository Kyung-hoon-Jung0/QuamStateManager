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
window.fetch = function () { return new window.Promise(function () {}); };
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

// ── T1: a tag row ──────────────────────────────────────────────────────────
let rows = TV.items('flag');
let labels = rows.map(function (r) { return r.label; });
ok(labels.indexOf('#12: flagged') >= 0,
  'T1: the newest run carrying the tag is listed as "#run: tag" — ' + labels.join(' | '));
ok(labels.indexOf('#11: flagged') >= 0, 'T1: and every other run carrying it');

// ── T2: a note row ─────────────────────────────────────────────────────────
ok(labels.indexOf('#7: note (flagged)') >= 0,
  'T2: a note row names the run and the word that matched — ' + labels.join(' | '));
rows = TV.items('fridge');
labels = rows.map(function (r) { return r.label; });
ok(labels.length === 1 && labels[0] === '#11: note (fridge)',
  'T2: exactly the matched word, in parentheses — ' + labels.join(' | '));
ok(labels[0].indexOf('recalibrated') < 0 && labels[0].indexOf('cycled') < 0,
  'T2: and none of the note’s other text');

// ── T3: what accepting inserts ─────────────────────────────────────────────
rows = TV.items('flag');
const tagRow = rows.filter(function (r) { return r.label === '#12: flagged'; })[0];
ok(tagRow && tagRow.insert === 'tag:flagged',
  'T3: a tag row inserts the grammar’s own tag scope — ' + (tagRow && tagRow.insert));
const noteRow = rows.filter(function (r) { return /note \(/.test(r.label); })[0];
ok(noteRow && noteRow.insert === 'note:flagged',
  'T3: a note row inserts the note scope — ' + (noteRow && noteRow.insert));
ok(tagRow.meta === 'tag · 2 runs',
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

console.log(fails ? 'FAILED (' + fails + ')'
  : 'tag_typeahead_selfcheck ok (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
