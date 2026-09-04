// docs/167 — the notes panel's client, against the REAL notes.js under jsdom.
//
// The module owns no model: it posts, and swaps the HTML the server sent back.
// So what is pinned is what it SENDS and what it does with the answer:
//   - the hand-tuned mark is sent when the box is ticked, and CARRIED THROUGH
//     an edit (an edit that dropped it would silently unmark a value somebody
//     deliberately marked — the failure this feature exists to prevent)
//   - the compare-and-swap rev the row was rendered with goes back with it
//   - a 409 shows the other person's text instead of retrying
//
// Run: node tests/notes_panel_selfcheck.cjs   (needs jsdom)
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

const NOTES_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'notes.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

// The SERVER returns only _notes_panel.html -- the inner div -- so the mock
// must too. Returning the <details> wrapper would nest a second one inside the
// first and the summary's count would end up on a node nothing reads.
function panelOnly(opts) {
  const o = opts || {};
  return '<div id="notes-panel" class="notes-panel" data-count="1">'
    + '<ul class="notes-list"><li class="notes-item" data-subject="qubits.q12" data-rev="3">'
    + '<div class="notes-head"><code class="notes-subject">qubits.q12</code>'
    + (o.tuned ? '<span class="notes-tuned">&#9998; hand-tuned</span>' : '')
    + '<span class="notes-actions">'
    + '<button type="button" class="notes-edit">Edit</button>'
    + '<button type="button" class="notes-del">x</button>'
    + '</span></div><div class="notes-text">flux suspect</div></li></ul>'
    + '<form class="notes-add"><input class="notes-add-subject"><input class="notes-add-text">'
    + '<label class="notes-add-tuned"><input type="checkbox" class="notes-add-tuned-cb"> hand-tuned</label>'
    + '<button type="button" class="notes-add-go">Add note</button></form>'
    + '</div>';
}

function panelHtml(opts) {
  return '<details id="notes-block"><summary class="notes-summary">Notes'
    + ' <span class="notes-count">1</span></summary>' + panelOnly(opts) + '</details>';
}

function world(opts) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + panelHtml(opts) + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win.posts = [];
  win.toasts = [];
  win.showToast = function (m) { win.toasts.push(String(m)); };
  win.answer = { status: 200, body: { ok: true, panel: panelOnly(opts) } };
  // Installed INSIDE the realm — a Node-realm assignment never reaches the
  // realm's bare `fetch` (the docs/144 harness rule).
  win.__record = function (url, body) {
    const seen = {};
    body.forEach(function (v, k) { seen[k] = v; });
    win.posts.push({ url: String(url), body: seen });
  };
  win.__answer = function () { return win.answer; };
  win.eval("window.fetch = function (u, o) {"
    + " window.__record(u, o.body);"
    + " var a = window.__answer();"
    + " return Promise.resolve({ status: a.status, ok: a.status < 400,"
    + "   json: function () { return Promise.resolve(a.body); } });"
    + "};");
  new win.Function(NOTES_JS).call(win);
  return win;
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const click = (win, sel) => win.document.querySelector(sel)
  .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));

(async function () {
  // ── adding ──────────────────────────────────────────────────────────────
  {
    const win = world();
    win.document.querySelector('.notes-add-subject').value = 'qubits.q7';
    win.document.querySelector('.notes-add-text').value = 'refit T1';
    click(win, '.notes-add-go');
    await settle(); await settle();
    ok(win.posts.length === 1 && win.posts[0].url === '/note', 'adding posts to /note');
    ok(win.posts[0].body.subject === 'qubits.q7', 'with the subject');
    ok(win.posts[0].body.text === 'refit T1', 'and the text');
    ok(!win.posts[0].body.hand_tuned, 'and no mark when the box is unticked');
  }
  {
    const win = world();
    win.document.querySelector('.notes-add-subject').value = 'qubits.q7';
    win.document.querySelector('.notes-add-text').value = 'tuned by hand';
    win.document.querySelector('.notes-add-tuned-cb').checked = true;
    click(win, '.notes-add-go');
    await settle(); await settle();
    ok(win.posts[0].body.hand_tuned === '1', 'the ticked box sends the mark');
  }
  {
    const win = world();
    click(win, '.notes-add-go');
    await settle();
    ok(win.posts.length === 0, 'an empty form posts nothing');
    ok(win.toasts.length === 1 && /subject/.test(win.toasts[0]), 'and says why');
  }

  // ── editing: the mark must survive ──────────────────────────────────────
  {
    const win = world({ tuned: true });
    win.prompt = function () { return 'flux STILL suspect'; };
    click(win, '.notes-edit');
    await settle(); await settle();
    ok(win.posts.length === 1, 'editing posts once');
    ok(win.posts[0].body.text === 'flux STILL suspect', 'with the new text');
    ok(win.posts[0].body.hand_tuned === '1',
       'and CARRIES the mark the row was showing — an edit that dropped it '
       + 'would silently unmark a value somebody deliberately marked');
    ok(win.posts[0].body.expect_rev === '3',
       'and the rev the row was rendered with, as the compare-and-swap token');
  }
  {
    const win = world({ tuned: false });
    win.prompt = function () { return 'plain edit'; };
    click(win, '.notes-edit');
    await settle(); await settle();
    ok(!win.posts[0].body.hand_tuned,
       'an unmarked note does not acquire the mark by being edited');
  }
  {
    const win = world();
    win.prompt = function () { return null; };          // cancelled
    click(win, '.notes-edit');
    await settle();
    ok(win.posts.length === 0, 'cancelling the prompt posts nothing');
  }
  {
    const win = world();
    win.prompt = function () { return '   '; };          // emptied
    click(win, '.notes-edit');
    await settle();
    ok(win.posts.length === 0, 'emptying the text is not a delete');
    ok(/delete/.test(win.toasts[0] || ''), 'and it says which button is');
  }

  // ── deleting and re-addressing ──────────────────────────────────────────
  {
    const win = world();
    click(win, '.notes-del');
    await settle(); await settle();
    ok(win.posts[0].url === '/note/delete' && win.posts[0].body.subject === 'qubits.q12',
       'the x deletes by subject');
  }

  // ── the answer ──────────────────────────────────────────────────────────
  {
    const win = world();
    win.answer = { status: 409, body: { ok: false, note_conflict: true,
                                        stored: { text: 'theirs' } } };
    win.prompt = function () { return 'mine'; };
    click(win, '.notes-edit');
    await settle(); await settle();
    ok(win.toasts.length === 1 && /theirs/.test(win.toasts[0]),
       'a conflict shows the OTHER text rather than retrying');
    ok(win.posts.length === 1, 'and does not post again');
  }
  {
    const win = world();
    win.answer = { status: 400, body: { ok: false, error: 'not on this chip' } };
    win.prompt = function () { return 'mine'; };
    click(win, '.notes-edit');
    await settle(); await settle();
    ok(/not on this chip/.test(win.toasts[0] || ''), 'a refusal is reported verbatim');
  }
  {
    const win = world();
    win.answer = { status: 200, body: { ok: true,
      panel: panelOnly().replace('data-count="1"', 'data-count="0"')
                        .replace(/<ul class="notes-list">[\s\S]*?<\/ul>/, '') } };
    click(win, '.notes-del');
    await settle(); await settle();
    ok(win.document.querySelectorAll('.notes-item').length === 0,
       'the server-sent panel replaces the old one');
    ok(win.document.querySelector('.notes-count') === null,
       'and the summary count goes away with the last note');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed (' + asserts + ' assertions)');
})();
