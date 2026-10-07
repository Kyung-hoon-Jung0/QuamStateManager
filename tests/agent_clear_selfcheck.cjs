/* docs/297: Clear / Archived in the Agent panel -- real agent.js under jsdom.
 * The strip offers Clear; the confirm is a sheet (Archive and clear / Delete and
 * clear / Cancel, Escape closes it); a refusal is shown IN the sheet; a clear --
 * here or in another window -- starts the feed over; Archived (N) lists the kept
 * conversations, opens one read-only in place of the live feed, deletes one
 * after a second press. View-only windows get neither Clear nor Delete.
 * Run: node tests/agent_clear_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
// runScripts "dangerously": the strip's buttons are inline onclick handlers, which a browser runs
const dom = new JSDOM('<!doctype html><html><body><div id="agent-home"></div></body></html>',
  { url: 'http://localhost/agent', pretendToBeVisual: true, runScripts: 'dangerously' });
const { window } = dom;
const document = window.document;
const now = Date.now() / 1000;
let seq = 1;
function feed(over) {
  return Object.assign({ ok: true, chip: 'chipA', chip_key: 'chipA-aaaa', qubits: 2, more: false, agent_seq: seq,
    session: {}, file: {}, now: { state: 'idle' }, live: { approvals: [], plans: [], runs: [] },
    conversation: { since_n: 0, archives: 0, resumable: true } }, over);
}
let cards = feed({ last: 2, cards: [
  { n: 1, kind: 'user', text: 'old question', ts: now - 30 },
  { n: 2, kind: 'answer', text: 'old answer', html: '<p>old answer</p>', ts: now - 20 }],
  live: { approvals: [], plans: [{ id: 'pl-done', status: 'done', title: 'finished plan', steps: [], created: now - 40 }], runs: [] } });
const ARCH = [
  { id: 'aaaaaaaaaaaa', title: 'old question', from_ts: now - 30, to_ts: now - 20, messages: 2, cleared_by: 'operator-a' },
  { id: 'bbbbbbbbbbbb', title: 'older one', from_ts: now - 900, to_ts: now - 800, messages: 1, cleared_by: 'operator-b' }];
let clearReply = { status: 409, body: { ok: false, error: 'the agent is still answering; press Stop now or wait for it to finish, then clear' } };
const posts = [];
window.fetch = function (url, opts) {
  const method = (opts && opts.method) || 'GET';
  let st = 200, b = { ok: true };
  if (method === 'POST') posts.push({ url: String(url), body: opts.body ? JSON.parse(opts.body) : null });
  if (/\/chat\/cards/.test(url)) b = cards;
  else if (/\/chat\/backends/.test(url)) b = { ok: true, chip: 'chipA', default: 'claude', backends: { claude: { found: true } } };
  else if (/\/chat\/clear$/.test(url)) { st = clearReply.status; b = clearReply.body; }
  else if (/\/chat\/archives$/.test(url)) b = { ok: true, archives: ARCH.slice() };
  else if (/\/chat\/archives\/aaaaaaaaaaaa$/.test(url)) b = { ok: true, archive: ARCH[0], omitted: 0, cards: [
    { n: 1, kind: 'user', text: 'archived hello', ts: now - 30 }, { n: 2, kind: 'answer', text: 'archived reply', html: '<p>archived reply</p>', ts: now - 20 }] };
  else if (/\/chat\/archives\/bbbbbbbbbbbb\/delete$/.test(url)) { ARCH.splice(1, 1); b = { ok: true }; }
  return Promise.resolve({ status: st, json: () => Promise.resolve(b) });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
window.eval(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'));
const tick = (ms) => new Promise(r => setTimeout(r, ms || 30));
const $ = (s) => document.querySelector('#agent-home ' + s);
const liveHost = () => document.querySelector('#agent-home .ag-root > .ag-cards');
function wake() { seq += 1; cards.agent_seq = seq; document.dispatchEvent(new window.CustomEvent('sm:agent-changed', { detail: { agent_seq: seq } })); }
function esc(el) { el.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); }
(async () => {
  await tick(80);
  ok(/old question/.test(liveHost().textContent) && !!$('[data-card="plan:pl-done"]'), 'precondition: the conversation and a finished plan are on screen');
  const clearBtn = $('.ag-now .ag-clear');
  ok(!!clearBtn && /Clear/.test(clearBtn.textContent), 'the strip offers Clear');
  ok(!$('.ag-archived-link'), 'no Archived link while nothing is archived');

  // the confirm sheet, and Escape
  clearBtn.click();
  let box = $('.ag-clearbox');
  ok(!!box && box.previousElementSibling === $('.ag-now'), 'Clear opens a sheet right under the status strip');
  ok(box.getAttribute('role') === 'region', 'the sheet is a region, never role=group (Pico lays a group out as one row): ' + box.getAttribute('role'));
  ok(/Start a fresh conversation/.test(box.textContent) && /Archive and clear/.test(box.textContent) && /Delete and clear/.test(box.textContent),
     'the sheet offers Archive and clear / Delete and clear: ' + box.textContent.slice(0, 80));
  ok(/keeps no copy in SM/.test(box.textContent) && /agent event log or the CLI's own session files/.test(box.textContent) && /Calibration log/.test(box.textContent),
     'the sheet says exactly what Delete does and does not remove');
  ok(document.activeElement === box.querySelector('.ag-clear-keep'), 'the focus is on Archive and clear');
  esc(box.querySelector('.ag-clear-keep'));
  ok(!$('.ag-clearbox'), 'Escape closes the sheet');
  ok(document.activeElement && document.activeElement.classList.contains('ag-clear'), 'the focus returns to Clear');
  ok(posts.filter(p => /\/clear$/.test(p.url)).length === 0, 'nothing was sent');

  // a refusal is said in the sheet, which stays
  $('.ag-clear').click();
  $('.ag-clearbox .ag-clear-keep').focus();
  $('.ag-clearbox .ag-clear-keep').click();
  ok($('.ag-clearbox').getAttribute('aria-busy') === 'true' && !$('.ag-clearbox .ag-clear-keep').disabled,
     'in flight the sheet is busy, but no button is disabled (a disabled focused button drops the focus)');
  await tick(40);
  ok(document.activeElement === $('.ag-clearbox .ag-clear-keep') && !$('.ag-clearbox').hasAttribute('aria-busy'),
     'after the refusal the focus is still on the pressed button');
  box = $('.ag-clearbox');
  const err = box && box.querySelector('.ag-sheet-err');
  ok(!!box && err && !err.hidden && /still answering/.test(err.textContent), 'a refusal is shown in the sheet: ' + (err && err.textContent));
  ok(/old question/.test(liveHost().textContent), 'a refused clear leaves the feed');
  ok(posts[posts.length - 1].body.keep === 1, 'Archive and clear sends keep=1');
  ok(posts[posts.length - 1].body.since_n === 0, 'and the conversation the sheet was opened on (since_n 0)');

  // the clear goes through: the feed starts over
  clearReply = { status: 200, body: { ok: true, kept: true, ended: false, since_n: 2, archive: ARCH[0] } };
  cards = feed({ last: 2, cards: [], conversation: { since_n: 2, archives: 2, resumable: false } });
  $('.ag-clearbox .ag-clear-keep').click();
  await tick(80);
  ok(!$('.ag-clearbox'), 'the sheet closes on success');
  ok(!/old question/.test(liveHost().textContent) && !$('[data-card^="user:"]'), 'the old conversation is gone from the feed');
  ok(!$('[data-card="plan:pl-done"]'), 'the finished plan card went with it');
  ok(!!$('.ag-welcome'), 'the empty feed shows the welcome again');
  ok(!!$('.ag-archived-link') && /Archived \(2\)/.test($('.ag-archived-link').textContent), 'Archived (N) appears in the strip');
  ok(!$('.ag-now .ag-clear'), 'nothing left to clear: no Clear button');
  ok(document.activeElement === $('.ag-input'), 'the caret is back in the composer');

  // only live work left (a draft, an approval): nothing for Clear to take away
  const ap = { id: 'ap-1', kind: 'writes', node: '17_T1', targets: ['q1'], created: now, why_held: 'ask-writes',
    writes: [{ path: 'qubits.q1.T1', old: 1e-5, new: 2e-5, now: 1e-5, now_known: true }] };
  const draft = { id: 'pl-draft', status: 'draft', title: 'a draft', steps: [], created: now };
  cards = feed({ last: 2, cards: [], live: { approvals: [ap], plans: [draft], runs: [] }, conversation: { since_n: 2, archives: 2, resumable: false } });
  wake(); await tick(60);
  ok(!!$('[data-card="approval:ap-1"]') && !!$('[data-card="plan:pl-draft"]'), 'precondition: an approval and a draft on screen');
  ok(!$('.ag-now .ag-clear'), 'only live work left: no Clear (a clear would take nothing away)');

  // another window cleared: a new epoch starts this feed over too -- but never under a typing hand
  cards = feed({ last: 3, cards: [{ n: 3, kind: 'user', text: 'second talk', ts: now }], live: { approvals: [ap], plans: [draft], runs: [] }, conversation: { since_n: 2, archives: 2, resumable: true } });
  wake(); await tick(60);
  ok(/second talk/.test(liveHost().textContent), 'precondition: a new conversation');
  const inp = $('[data-card="approval:ap-1"] .ag-ap-new');
  inp.focus(); inp.value = '2.5e-05';
  cards = feed({ last: 3, cards: [], live: { approvals: [ap], plans: [draft], runs: [] }, conversation: { since_n: 3, archives: 3, resumable: false } });
  wake(); await tick(80);
  ok(!/second talk/.test(liveHost().textContent), 'a clear from another window empties this feed as well');
  const inp2 = $('[data-card="approval:ap-1"] .ag-ap-new');
  ok(!!inp2 && inp2.value === '2.5e-05' && document.activeElement === inp2,
     'the approval card stays, with the value being typed and the focus: ' + (inp2 && inp2.value));
  ok(!!$('[data-card="plan:pl-draft"]'), 'live work comes back with the next poll');
  inp2.blur();
  cards = feed({ last: 3, cards: [], conversation: { since_n: 3, archives: 3, resumable: false } });
  wake(); await tick(60);
  ok(/Archived \(3\)/.test($('.ag-archived-link').textContent), 'and the count follows');

  // Delete and clear sends keep=0
  cards = feed({ last: 4, cards: [{ n: 4, kind: 'user', text: 'third', ts: now }], conversation: { since_n: 3, archives: 3, resumable: true } });
  wake(); await tick(60);
  clearReply = { status: 200, body: { ok: true, kept: false, ended: true, since_n: 4, archive: null } };
  cards = feed({ last: 4, cards: [], conversation: { since_n: 4, archives: 3, resumable: false } });
  $('.ag-clear').click();
  $('.ag-clearbox .ag-clear-drop').click();
  await tick(80);
  ok(posts[posts.length - 1].body.keep === 0, 'Delete and clear sends keep=0');
  ok(!/third/.test(liveHost().textContent), 'and the feed starts over');

  // Archived: the list, a two-step delete, a read-only view in place of the feed
  $('.ag-archived-link').click();
  await tick(40);
  const arch = $('.ag-archbox');
  ok(!!arch && arch.querySelectorAll('.ag-arch-list li').length === 2, 'Archived lists the kept conversations');
  ok(/older one/.test(arch.textContent) && /1 message\b/.test(arch.textContent) && /2 messages/.test(arch.textContent) && /cleared by operator-b/.test(arch.textContent),
     'each row says what it was, how long, who cleared it: ' + arch.textContent.replace(/\s+/g, ' ').slice(0, 160));
  let li = arch.querySelector('li[data-id="bbbbbbbbbbbb"]');
  li.querySelector('.ag-arch-del').click();
  ok(/Delete for good\?/.test(li.textContent) && document.activeElement === li.querySelector('.ag-arch-del-no'), 'Delete asks once more, the focus on Keep');
  li.querySelector('.ag-arch-del-no').click();
  ok(!!li.querySelector('.ag-arch-del') && posts.filter(p => /\/delete$/.test(p.url)).length === 0, 'Keep keeps it');
  li.querySelector('.ag-arch-del').click();
  li.querySelector('.ag-arch-del-yes').click();
  await tick(60);
  ok(posts.some(p => /\/archives\/bbbbbbbbbbbb\/delete$/.test(p.url)), 'the second press deletes');
  ok($('.ag-archbox').querySelectorAll('.ag-arch-list li').length === 1, 'the list is read again');

  $('.ag-archbox li[data-id="aaaaaaaaaaaa"] .ag-arch-open').click();
  await tick(60);
  const view = $('.ag-archview');
  ok(!$('.ag-archbox'), 'opening one closes the list');
  ok(!!view && liveHost().classList.contains('ag-feed-away'), 'the archived conversation is shown in place of the live feed');
  ok(/archived hello/.test(view.textContent) && /archived reply/.test(view.textContent) && /read-only/.test(view.textContent), 'with its cards, said read-only');
  ok(!/archived hello/.test(liveHost().textContent), 'its cards never enter the live feed');
  // a live card arriving meanwhile goes to the live feed, not the view
  cards = feed({ last: 5, cards: [{ n: 5, kind: 'answer', text: 'live while viewing', html: '<p>live while viewing</p>', ts: now }], conversation: { since_n: 4, archives: 2, resumable: true } });
  wake(); await tick(60);
  ok(/live while viewing/.test(liveHost().textContent) && !/live while viewing/.test(view.textContent), 'a live card lands in the (hidden) live feed');
  view.querySelector('.ag-archview-back').click();
  ok(!$('.ag-archview') && !liveHost().classList.contains('ag-feed-away'), 'Back shows the live feed again');
  ok(document.activeElement === $('.ag-input'), 'and puts the caret in the composer');

  // sending while an archive is open shows the live feed (the answer lands there)
  $('.ag-archived-link').click(); await tick(40);
  $('.ag-archbox .ag-arch-open').click(); await tick(40);
  ok(!!$('.ag-archview'), 'precondition: an archive is open');
  $('.ag-input').value = 'a new line';
  window.AgentPanel.submit({ target: $('.ag-input'), preventDefault() {} });
  await tick(40);
  ok(!$('.ag-archview') && !liveHost().classList.contains('ag-feed-away'), 'sending closes the archive view');

  // view only: no Clear, no Delete -- and a sheet already open closes
  cards = feed({ last: 6, cards: [{ n: 6, kind: 'user', text: 'something to clear', ts: now }], conversation: { since_n: 4, archives: 1, resumable: true } });
  wake(); await tick(60);
  $('.ag-clear').click();
  ok(!!$('.ag-clearbox'), 'precondition: the Clear sheet is open');
  window.AgentPanel.setObserver(true);
  await tick(20);
  ok(!$('.ag-clearbox'), 'turning view-only on closes an open Clear sheet');
  ok(!$('.ag-now .ag-clear'), 'a view-only window offers no Clear');
  window.AgentPanel.clearAsk($('.ag-input'));
  ok(!$('.ag-clearbox'), 'and Clear cannot be opened in it by any other way');
  $('.ag-archived-link').click(); await tick(40);
  ok(!!$('.ag-archbox') && !$('.ag-archbox .ag-arch-del'), 'and its archive list has no Delete');
  ok(!!$('.ag-archbox .ag-arch-open'), 'but it can still read one');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
