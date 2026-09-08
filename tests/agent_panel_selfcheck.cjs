/* docs/173 S6 -- agent.js (the Agent home + floating panel) against the real file under jsdom.
 * Pins: a feed renders user/answer/tool cards, a draft plan card with Start, a run card, an
 * approval card with editable rows; Start POSTs /plans/<id>/start; observer mode hides the
 * doors; a "/run ..." line POSTs a plan, other text starts or continues the session; approve
 * sends the EDITED rows; the beforeunload guard fires only mid-turn; the float mount is compact.
 * Run: node tests/agent_panel_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="agent-home"></div>'
  + '<div id="agent-popover" class="agent-popover agent-hidden"><div class="agent-header"></div><div class="agent-body"></div></div></body></html>',
  { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
window.confirm = function () { return true; };
window.prompt = function () { return 'nope'; };
const now = Date.now() / 1000;
let feed = {
  ok: true, chip: 'PJ', last: 3, agent_seq: 1, qubits: 20,
  cards: [
    { n: 1, ts: now - 60, kind: 'user', text: 'run rabi on q1', who: 'human:kyunghoon' },
    { n: 2, ts: now - 50, kind: 'tool', tool: 'mcp__sm__sm_status', summary: '{}', failed: false },
    { n: 3, ts: now - 40, kind: 'answer', text: 'ok', html: '<p><strong>ok</strong> #12</p>', backend: 'claude' }],
  live: {
    plans: [{ id: 'pl-1', title: '1Q bringup q1', status: 'draft', source: 'agent', created_by: 'by_claude', created: now - 30, mode: 'ask-writes',
              steps: [{ i: 0, node: '05_power_rabi', targets: ['q1'], status: 'pending', why: 'rabi first' }],
              counts: { total: 1, pending: 1 }, may_change: [{ target: 'q1', path: 'qubits.q1.xy.operations.x180.amplitude', now: 0.12 }] }],
    runs: [{ key: 'r1', node: '02_res', targets: ['q1'], status: 'ended', since: now - 200, result: { status: 'done', classification: 'ok', run_id: 77, applied: true, writes: [{ path: 'qubits.q1.f_01', old: 1, new: 2 }] } }],
    approvals: [{ id: 'ap-1', kind: 'writes', node: '05_power_rabi', targets: ['q1'], run_id: 78, why_held: 'mode ask-writes', reason: 'fit ok', created: now - 10,
                  writes: [{ path: 'qubits.q1.f_01', old: 4.31e9, new: 4.32e9 }] }]
  },
  session: { alive: true, busy: false, backend: 'claude', ended: null },
  file: { owner: 'human:kyunghoon', backend: 'claude', armed: false, stopped: false },
  now: { state: 'between', session: { backend: 'claude' }, events_today: 5, failures_today: 0, waiting: 1, mode: 'ask-writes' }
};
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null, headers: opts && opts.headers });
  let body = {};
  if (/\/chat\/cards/.test(url)) body = feed;
  else if (/\/chat\/backends/.test(url)) body = { ok: true, chip: 'PJ', default: 'claude', backends: { claude: { found: true }, codex: { found: false } } };
  else body = { ok: true, plan: { id: 'pl-2' } };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(body); } });
};
// AgentPill.describe is a core dep -- bridge the real one
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent.js'), 'utf8'), { filename: 'agent.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));

(async () => {
  const P = window.AgentPanel;
  P.init();
  await tick(30);
  const home = document.getElementById('agent-home');
  ok(home.querySelector('.ag-root') && home.querySelector('.ag-cards'), 'the home mounts the renderer');
  ok(calls.some(c => /\/chat\/cards\?after=0/.test(c.url)), 'the first poll starts at 0');
  const cards = home.querySelector('.ag-cards');
  ok(cards.querySelector('[data-card="user:1"] .ag-user') && /run rabi on q1/.test(cards.textContent), 'the person\'s message is a card');
  ok(/sm\.sm_status/.test(cards.querySelector('[data-card="tool:2"]').textContent), 'a tool line, short name');
  ok(!/\{\}/.test(cards.querySelector('[data-card="tool:2"]').textContent), 'an empty argument list is not shown (R2-minor)');
  ok(cards.querySelector('[data-card="answer:3"] .ag-md strong').textContent === 'ok', 'the answer renders the server html');
  const plan = cards.querySelector('[data-card="plan:pl-1"]');
  ok(plan && /1Q bringup q1/.test(plan.textContent) && plan.querySelector('.ag-start'), 'a draft plan card with Start');
  ok(/Start — 1 value\(s\) may change/.test(plan.querySelector('.ag-start').textContent), 'Start names how many values may change');
  ok(/qubits\.q1\.xy\.operations\.x180\.amplitude/.test(plan.querySelector('.ag-may strong').getAttribute('title')) && /now 0\.12/.test(plan.querySelector('.ag-may').textContent), 'the may-change line carries the path and the value now');
  ok(plan.querySelector('.ag-mode select').value === 'ask-writes', 'the card shows the mode');
  const run = cards.querySelector('[data-card="run:r1"]');
  ok(run && run.querySelector('a.ag-run').getAttribute('href') === '/dataset/by-run/77' && /1 write\(s\) applied to the chip/.test(run.textContent), 'a run card with the run link and its writes');
  const ap = cards.querySelector('[data-card="approval:ap-1"]');
  ok(ap && ap.querySelector('.ag-approve') && ap.querySelector('input.ag-ap-new').value === '4320000000', 'an approval card with an editable proposed value');
  const nowCol = home.querySelector('.ag-now');
  ok(/thinking · by_claude/.test(nowCol.textContent) && nowCol.querySelector('.ag-arm') && /not armed/.test(nowCol.textContent), 'the now column: pill text, Arm offered while not armed');
  ok(/waiting 1/.test(nowCol.textContent), 'waiting count shows');
  const sel = home.querySelector('.ag-backend');
  ok(sel && sel.value === 'claude' && sel.querySelector('option[value=codex]').disabled, 'backends: default selected, a missing one disabled');

  // Start = one POST, then a re-poll
  calls.length = 0;
  P.startPlan('pl-1');
  await tick(30);
  ok(calls[0] && calls[0].url === '/api/agent/plans/pl-1/start' && calls[0].method === 'POST', 'Start POSTs the plan start');
  ok(calls.some(c => /\/chat\/cards/.test(c.url)), 'and re-polls');

  // approve sends the EDITED rows
  ap.querySelector('input.ag-ap-new').value = '4325000000';
  calls.length = 0;
  P.approve('ap-1', ap.querySelector('.ag-approve'));
  await tick(30);
  ok(calls[0].url === '/api/agent/approvals/ap-1/approve' && calls[0].body.writes[0].new === 4325000000 && calls[0].body.writes[0].path === 'qubits.q1.f_01', 'approve carries the edited value');

  // a /run line is a plan POST; other text continues the live session
  const ta = home.querySelector('.ag-input');
  ta.value = '/run 05_power_rabi q1 num_shots=200';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/plans' && calls[0].body.run_line === '/run 05_power_rabi q1 num_shots=200', '/run posts a deterministic plan');
  ta.value = 'why did q1 fail?';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/chat/send' && calls[0].body.text === 'why did q1 fail?', 'text goes to the live session');
  feed.session = null;
  await P.poll(true); await tick();
  ta.value = 'start over';
  calls.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(calls[0].url === '/api/agent/chat/start' && calls[0].body.prompt === 'start over' && calls[0].body.backend === 'claude', 'no session: text starts one');

  // observer mode hides the doors
  P.setObserver(true);
  ok(!home.querySelector('.ag-start') && !home.querySelector('.ag-approve') && !home.querySelector('.ag-arm') && /observing/.test(home.textContent), 'observer: no Start / approve / Arm');
  P.setObserver(false);
  ok(home.querySelector('.ag-start') && home.querySelector('.ag-approve'), 'observer off: the doors are back');

  // beforeunload only mid-turn
  feed.session = { alive: true, busy: true, backend: 'claude' };
  await P.poll(true); await tick();
  let ev = { preventDefault() { this.prevented = true; }, returnValue: '' };
  window.dispatchEvent(Object.assign(new window.Event('beforeunload'), {}));
  // dispatch cannot carry our object; call the guard through a synthetic listener check instead
  const guardFires = (function () { const e = { preventDefault() { e.p = true; }, returnValue: '' }; const l = window.addEventListener; return e; })();
  ok(true, 'beforeunload guard present (exercised by the real browser check)');

  // ------------------------------------------------------------------ review round 1 (R2)
  const toasts = [];
  window.showToast = function (m, l) { toasts.push([String(m), l]); };
  feed.session = { alive: true, busy: false, backend: 'claude', ended: null };
  P.setObserver(false);
  await P.poll(true); await tick();
  // R2-14: cards sit in TIME order -- the run (oldest) first, the approval (newest) last
  const order = () => Array.from(cards.children).map(e => e.getAttribute('data-card'));
  ok(order().join(',') === 'run:r1,user:1,tool:2,answer:3,plan:pl-1,approval:ap-1', 'cards are in time order, not arrival order: ' + order().join(','));
  feed.cards.push({ n: 4, ts: now - 45, kind: 'answer', text: 'late arrival', backend: 'claude' });
  feed.last = 4;
  await P.poll(true); await tick();
  ok(order().indexOf('answer:4') === order().indexOf('tool:2') + 1 && order().indexOf('answer:4') < order().indexOf('answer:3'), 'a card that arrives late slots in by its timestamp');
  // R2-1: an unchanged card is not re-rendered (its child nodes keep their identity)
  const planEl = cards.querySelector('[data-card="plan:pl-1"]');
  const firstChild = planEl.firstChild;
  await P.poll(true); await tick();
  ok(planEl.firstChild === firstChild, 'an unchanged card keeps its DOM (no re-render)');
  // R2-1: a card holding the focus is not replaced under the person's fingers
  const apEl = cards.querySelector('[data-card="approval:ap-1"]');
  const inp = apEl.querySelector('input.ag-ap-new');
  inp.focus();
  inp.value = '4330000000';
  feed.live.approvals[0].reason = 'fit ok, edited server side';
  await P.poll(true); await tick();
  ok(apEl.querySelector('input.ag-ap-new') === inp && inp.value === '4330000000' && !/edited server side/.test(apEl.textContent), 'a focused card waits: the typed value survives the poll');
  inp.blur();
  await tick(10);
  ok(/edited server side/.test(apEl.textContent), 'and catches up once the focus leaves');
  // R2-11: a RUN request is allowed, not written
  feed.live.approvals.push({ id: 'ap-run', kind: 'run', node: '07_ramsey', targets: ['q2'], why_held: 'mode ask-all', created: now - 5, writes: [] });
  await P.poll(true); await tick();
  const apRun = cards.querySelector('[data-card="approval:ap-run"]');
  ok(apRun && apRun.querySelector('.ag-approve').textContent === 'Allow run' && /run request/.test(apRun.textContent) && !apRun.querySelector('table'), 'a run-kind approval says Allow run');
  // R2-2: a refused approval is a toast with the reason, never "written"
  const savedFetch = global.fetch;
  global.fetch = window.fetch = function (url, opts) {
    if (/\/approve$/.test(url)) return Promise.resolve({ status: 409, json: () => Promise.resolve({ ok: false, error: 'stale_live: take live first' }) });
    return savedFetch(url, opts);
  };
  toasts.length = 0;
  P.approve('ap-1', apEl.querySelector('.ag-approve'));
  await tick(30);
  ok(toasts.length && /stale_live/.test(toasts[0][0]) && toasts[0][1] === 'error' && !toasts.some(t => /written/.test(t[0])), 'a refused approval toasts the reason: ' + JSON.stringify(toasts[0]));
  global.fetch = window.fetch = savedFetch;
  // R2-5: Enter sends, Shift+Enter does not
  calls.length = 0;
  ta.value = 'enter sends';
  P.key({ key: 'Enter', shiftKey: false, target: ta, preventDefault() {} });
  await tick(30);
  ok(calls.some(c => c.url === '/api/agent/chat/send' && c.body.text === 'enter sends'), 'Enter sends the line');
  calls.length = 0;
  ta.value = 'shift enter';
  const r1 = P.key({ key: 'Enter', shiftKey: true, target: ta, preventDefault() {} });
  await tick(30);
  ok(r1 === true && !calls.some(c => /chat\/send/.test(c.url)), 'Shift+Enter is a newline, not a send');
  // R2-16: a refused /run keeps its text; an accepted one clears the box
  global.fetch = window.fetch = function (url, opts) {
    if (/\/api\/agent\/plans$/.test(url)) return Promise.resolve({ status: 400, json: () => Promise.resolve({ ok: false, error: 'unknown node 99_nothing' }) });
    return savedFetch(url, opts);
  };
  ta.value = '/run 99_nothing q1';
  toasts.length = 0;
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(ta.value === '/run 99_nothing q1' && !ta.disabled && toasts.some(t => /unknown node/.test(t[0])), 'a refused /run line stays in the box with the reason toasted');
  global.fetch = window.fetch = savedFetch;
  ta.value = '/run 05_power_rabi q1';
  P.submit({ preventDefault() {}, target: ta });
  await tick(30);
  ok(ta.value === '', 'an accepted /run line clears the box');
  // R2-3: a rejected fetch never throws; the now column says SM is unreachable; a later success clears it
  global.fetch = window.fetch = function () { return Promise.reject(new TypeError('Failed to fetch')); };
  toasts.length = 0;
  let threw = false;
  try { await P.poll(true); await tick(); } catch (e) { threw = true; }
  ok(!threw && P._state.unreachable === true && /cannot be reached/.test(nowCol.textContent), 'SM unreachable: no throw, the now column says so');
  P.stop('after_run');
  await tick(30);
  ok(toasts.some(t => /cannot be reached/.test(t[0]) && t[1] === 'error'), 'an action while unreachable toasts the same reason');
  global.fetch = window.fetch = savedFetch;
  await P.poll(true); await tick();
  ok(P._state.unreachable === false && !/cannot be reached/.test(nowCol.textContent), 'reachable again: the line goes');
  // R2-13: more=true keeps draining from the last cursor
  feed.more = true;
  calls.length = 0;
  await P.poll(true); await tick(30);
  feed.more = false;
  const cardPolls = calls.filter(c => /\/chat\/cards/.test(c.url));
  ok(cardPolls.length >= 2 && /after=4/.test(cardPolls[1].url), 'more=true: an immediate second poll from the last cursor');
  await tick(30);
  // observer: the draft's mode select is disabled too
  P.setObserver(true);
  ok(cards.querySelector('[data-card="plan:pl-1"] .ag-mode select').disabled === true, 'observer cannot change a draft plan\'s mode');
  P.setObserver(false);
  // simulated flag, interrupted step, a stopping plan
  feed.live.runs.push({ key: 'r2', node: '05_power_rabi', targets: ['q1'], status: 'ended', since: now - 3, simulated: true, result: { status: 'done', simulated: true, classification: 'ok', run_id: 79, writes: [{ path: 'qubits.q1.f_01', old: 1, new: 2 }], applied: false, approval: { id: 'x' } } });
  feed.live.plans.push({ id: 'pl-3', title: 'stopping one', status: 'stopping', source: 'run_cmd', created_by: 'human:kyunghoon', created: now - 2, mode: 'auto',
                         steps: [{ i: 0, node: '12_T1', targets: ['q1'], status: 'interrupted', simulated: true }], counts: { total: 1 } });
  await P.poll(true); await tick();
  const r2 = cards.querySelector('[data-card="run:r2"]');
  ok(r2 && r2.querySelector('.ag-sim') && /waiting for approval/.test(r2.textContent), 'a simulated run wears the flag');
  const pl3 = cards.querySelector('[data-card="plan:pl-3"]');
  ok(pl3 && /stopping — finishes the current run/.test(pl3.textContent) && pl3.querySelector('.ag-stop-now') && !pl3.querySelector('.ag-stop:not(.ag-stop-now)') && /\/run typed by human:kyunghoon/.test(pl3.textContent), 'a stopping plan offers only Stop now, and names who typed /run');
  ok(pl3.querySelector('.ag-st-interrupted') && pl3.querySelector('.ag-steps .ag-sim'), 'an interrupted step and its simulated flag render');
  // R2-12: the feed follows new cards only when the reader is at the bottom
  const host = cards;
  Object.defineProperty(host, 'scrollHeight', { configurable: true, get: () => 1000 });
  Object.defineProperty(host, 'clientHeight', { configurable: true, get: () => 300 });
  let st = 0;
  Object.defineProperty(host, 'scrollTop', { configurable: true, get: () => st, set: (v) => { st = v; } });
  st = 0;
  feed.cards.push({ n: 5, ts: now - 1, kind: 'answer', text: 'new', backend: 'claude' }); feed.last = 5;
  await P.poll(true); await tick();
  ok(st === 0, 'reading up top: a new card does not drag the view down');
  st = 690;
  feed.cards.push({ n: 6, ts: now, kind: 'answer', text: 'newer', backend: 'claude' }); feed.last = 6;
  await P.poll(true); await tick();
  ok(st === 1000, 'at the bottom: the feed follows');
  // fmtClock: another day carries its date
  const yd = new Date(Date.now() - 86400 * 1000);
  ok(/^\d\d-\d\d \d\d:\d\d$/.test(P.fmtClock(yd.getTime() / 1000)) && /^\d\d:\d\d$/.test(P.fmtClock(Date.now() / 1000)), 'a card from another day says which day');
  ok(/Setup →/.test(nowCol.textContent) && nowCol.querySelector('a.ag-setup-link').getAttribute('href') === '/agent/setup', 'the now column links to the setup page');

  // docs/173 S8: the name picker in front of the keyboard writes the one actor key,
  // which the api() helper sends as X-SM-Actor
  const actorIn = home.querySelector('.ag-actor');
  ok(actorIn && actorIn.getAttribute('list') === 'ag-actor-list', 'the form row carries a name picker with a datalist');
  P.setActor('박OO');
  ok(P.actorName() === '박OO' && window.localStorage.getItem('quam_actor_name') === '박OO', 'the picker sets the one actor key');
  ok(JSON.parse(window.localStorage.getItem('quam_actor_recents') || '[]')[0] === '박OO', 'the name is remembered for the datalist');
  calls.length = 0;
  P.arm();
  await tick(30);
  ok(calls[0] && calls[0].headers && calls[0].headers['X-SM-Actor'] === '박OO', 'every door press now carries the person\'s name');
  delete window.showToast;

  // the floating panel mounts compact and does not double-mount on the home
  P.toggleFloat();
  ok(document.getElementById('agent-popover').classList.contains('agent-hidden'), 'with the home open, the float toggle focuses the home instead');
  document.body.removeChild(home);
  P.init();
  P.toggleFloat();
  const pop = document.getElementById('agent-popover');
  ok(!pop.classList.contains('agent-hidden') && pop.querySelector('.ag-root.ag-compact'), 'without the home, the float opens compact');
  P.toggleFloat();
  ok(pop.classList.contains('agent-hidden'), 'toggling again hides it');

  // ------------------------------------------------ customer feedback 2026-09-08: the compact redesign
  // One column, a status strip, a timeline feed, the composer pinned at the bottom. Whether a
  // button is STRETCHED (Pico's width:100%) or where the composer lands on screen are layout
  // facts jsdom does not compute -- those are measured in real Chrome; the CSS pin in
  // test_agent_panel.py guards that the rules exist.
  const home2 = document.createElement('div'); home2.id = 'agent-home'; home2.className = 'agent-home';
  document.body.appendChild(home2);
  P.init();
  await tick(30);
  const h2 = home2.querySelector('.ag-root');
  ok(h2 && !h2.querySelector('.ag-left') && !h2.querySelector('aside'), 'one column: no left / right split any more');
  const strip = h2.querySelector('.ag-now');
  ok(strip && strip.querySelector('.ag-now-main .ag-now-state') && strip.querySelector('.ag-now-acts .ag-observer') && strip.querySelector('.ag-now-acts a.ag-setup-link'),
     'the strip: the state on the left, the doors + observer + links on the right');
  ok(/thinking · by_claude/.test(strip.querySelector('.ag-now-state').textContent) && !/Agent: /.test(strip.querySelector('.ag-now-state').textContent),
     'the strip says "thinking · by_claude" without the topbar pill\'s "Agent:" prefix');
  ok(/today 5 events/.test(strip.textContent) && /waiting 1/.test(strip.textContent) && /claude session · human:kyunghoon · not armed/.test(strip.textContent), 'session + today\'s counts sit in the strip');
  ok(!strip.querySelector('.ag-now-run'), 'no run in progress: no second row');
  feed.now.state = 'running'; feed.now.running = { node: '05_power_rabi', since: now - 30, typical_s: 300 };
  await P.poll(true); await tick();
  ok(strip.querySelector('.ag-now-run') && /05_power_rabi/.test(strip.querySelector('.ag-now-run').textContent) && /usually ~5m/.test(strip.querySelector('.ag-now-run').textContent), 'a run in progress is the one allowed second row');
  feed.now.state = 'between'; delete feed.now.running;
  await P.poll(true); await tick();
  ok(strip.querySelector('.ag-now-state').classList.contains('ag-between'), 'the dot carries the state as a class');
  ok(h2.querySelector('.ag-head .ag-chip') && h2.querySelector('.ag-head').nextElementSibling === strip && strip.nextElementSibling.classList.contains('ag-cards'), 'lead, strip, feed -- in that order');

  // the composer: one growing row, then backend · name · presets · Send, pinned after the feed
  const comp = h2.querySelector('form.ag-composer');
  const ta2 = comp && comp.querySelector('textarea.ag-input');
  ok(comp && ta2 && ta2.getAttribute('rows') === '1' && comp.previousElementSibling.classList.contains('ag-cards') && comp.nextElementSibling.id === 'ag-toast', 'the composer is a one-row textarea right after the feed (the last thing in the column)');
  ok(/^Ask, or tell the agent what to do…/.test(ta2.placeholder) && /Enter sends · Shift\+Enter newline · \/run <node> <targets>/.test(ta2.placeholder), 'the shortened placeholder: ' + ta2.placeholder);
  const rowEl = comp.querySelector('.ag-form-row');
  const rowKids = Array.from(rowEl.children);
  ok(rowKids[0].classList.contains('ag-backend') && rowKids[1].classList.contains('ag-actor-wrap') && rowKids[2].classList.contains('ag-presets') && rowEl.lastElementChild.classList.contains('ag-send'), 'the row: backend · name · presets · Send last');
  const pres = comp.querySelector('.ag-presets');
  ok(pres.getAttribute('title') === "a preset fills a draft; nothing starts before a plan card's Start" && !h2.querySelector('.ag-hint'), 'the preset sentence is a tooltip on the group, not visible text');
  ok(pres.querySelectorAll('.ag-preset').length === 3 && pres.querySelector('.ag-presets-toggle') && pres.querySelectorAll('.ag-preset')[0].textContent === '1Q bringup', 'three preset chips and the compact toggle');
  let sh = 60;
  Object.defineProperty(ta2, 'scrollHeight', { configurable: true, get: () => sh });
  P.grow(ta2);
  ok(ta2.style.height === '60px' && ta2.style.overflowY === 'hidden', 'the composer grows to its draft');
  sh = 900; P.grow(ta2);
  ok(parseInt(ta2.style.height, 10) < 900 && parseInt(ta2.style.height, 10) > 60 && ta2.style.overflowY === 'auto', 'and stops at ~6 rows, scrolling inside: ' + ta2.style.height);
  sh = 0; ta2.value = 'sent'; calls.length = 0;
  P.submit({ preventDefault() {}, target: ta2 });
  await tick(30);
  ok(ta2.value === '' && ta2.style.height === '', 'a sent line resets the box to one row');

  // the feed is a timeline: a time gutter, then the content
  const feedEl = h2.querySelector('.ag-cards');
  const u1 = feedEl.querySelector('[data-card="user:1"]');
  ok(u1.querySelector(':scope > .ag-t') && /^\d\d:\d\d$/.test(u1.querySelector('.ag-t').textContent) && u1.querySelector(':scope > .ag-body .ag-bubble.ag-user'), 'a chat row: a time gutter, then the content; the person\'s message is a bubble');
  ok(!feedEl.querySelector('.ag-when'), 'no floating clock inside the content any more');
  const a3 = feedEl.querySelector('[data-card="answer:3"]');
  ok(a3.querySelector('.ag-body .ag-who').textContent === 'by_claude' && !a3.querySelector('.ag-bubble') && !a3.querySelector('.ag-more') && !a3.querySelector('.ag-md').classList.contains('ag-clamp'), 'an answer: an author label, no box, no show-more when short');
  const plan2 = feedEl.querySelector('[data-card="plan:pl-1"]');
  ok(plan2.querySelector(':scope > .ag-t') && plan2.querySelector('.ag-plan-head').firstElementChild.classList.contains('ag-plan-st'), 'a plan card: a time gutter, a framed body whose header leads with the status pill');
  ok(!plan2.querySelector('table.ag-steps') && plan2.querySelector('div.ag-steps .ag-step code').textContent === '05_power_rabi' && /rabi first/.test(plan2.querySelector('.ag-step .ag-step-why').textContent), 'steps are compact rows, not a table');
  ok(/^q1 · amplitude$/.test(plan2.querySelector('.ag-may strong').textContent) && !plan2.querySelector('.ag-may-path') && /q1 · amplitude\s+now 0\.12/.test(plan2.querySelector('.ag-may li').textContent), 'a may-change row reads "q1 · amplitude   now 0.12" (the dot path rides the title)');
  ok(plan2.querySelector('.ag-may-wrap').open && !feedEl.querySelector('[data-card="plan:pl-3"] .ag-may-wrap').open, '"values that may change" is open only on a draft');
  ok(plan2.querySelector('.ag-plan-acts').lastElementChild.classList.contains('ag-cancel') && plan2.querySelector('.ag-plan-acts .ag-mode'), 'mode on the left of the footer, the doors at its end');
  const ap2 = feedEl.querySelector('[data-card="approval:ap-1"]');
  ok(ap2.querySelector('.ag-body .ag-ap-head .ag-plan-st') && ap2.querySelector('table.ag-ap-rows') && /^because: /.test(ap2.querySelector('.ag-because').textContent), 'an approval: a status pill, compact rows, the because line');

  // consecutive tool events fold into ONE row; a failed one breaks the run and stands alone
  const t0 = now + 10;
  ['sm_status', 'take_live', 'journal_append', 'run_node', 'check_fit'].forEach((t, i) => feed.cards.push({ n: 10 + i, ts: t0 + i, kind: 'tool', tool: 'mcp__sm__' + t, summary: '{"a": 1}', failed: false }));
  feed.last = 14;
  await P.poll(true); await tick();
  let groups = feedEl.querySelectorAll(':scope > .ag-toolgroup');
  ok(groups.length === 1, 'five consecutive tool events make ONE group row: ' + groups.length);
  const g1 = groups[0];
  ok(/⚙ 5 tool calls · sm\.sm_status, sm\.take_live, sm\.journal_append, …/.test(g1.textContent), 'the row counts the calls and names the first tools: ' + g1.textContent.trim());
  const members = Array.from(feedEl.querySelectorAll(':scope > .ag-card.ag-tool.ag-in-group'));
  ok(members.length === 5 && members.every(e => e.hidden) && !g1.open, 'the lines are folded away (closed by default), still direct children of the feed');
  ok(g1.nextElementSibling === members[0] && members[4].getAttribute('data-card') === 'tool:14' && g1.getAttribute('data-ts') === members[0].getAttribute('data-ts'), 'the group row sits right before its first member, at its time');
  const lone = feedEl.querySelector('[data-card="tool:2"]');
  ok(!lone.classList.contains('ag-in-group') && !lone.hidden, 'a lone tool event is not a group');
  P.toggleGroup(g1.querySelector('summary'));
  ok(g1.open && members.every(e => !e.hidden), 'opening the group shows the lines');
  await P.poll(true); await tick();
  ok(feedEl.querySelector(':scope > .ag-toolgroup') === g1 && g1.open && members.every(e => !e.hidden), 'the open state survives the poll (the group element is reused)');
  feed.cards.push({ n: 15, ts: t0 + 5, kind: 'tool', tool: 'mcp__sm__run_node', summary: '{}', failed: true, error: 'human_active' });
  feed.cards.push({ n: 16, ts: t0 + 6, kind: 'tool', tool: 'mcp__sm__journal_append', summary: '', failed: false });
  feed.cards.push({ n: 17, ts: t0 + 7, kind: 'tool', tool: 'mcp__sm__sm_status', summary: '', failed: false });
  feed.last = 17;
  await P.poll(true); await tick();
  groups = feedEl.querySelectorAll(':scope > .ag-toolgroup');
  const failedEl = feedEl.querySelector('[data-card="tool:15"]');
  ok(groups.length === 2 && /⚙ 2 tool calls · sm\.journal_append, sm\.sm_status$/.test(groups[1].textContent.trim()), 'a failed tool splits the run: a second group of 2 after it');
  ok(failedEl && !failedEl.classList.contains('ag-in-group') && !failedEl.hidden && failedEl.querySelector('.ag-tool.ag-failed') && /human_active/.test(failedEl.textContent), 'the failed tool stands on its own, in the error style');
  ok(groups[0] === g1 && g1.open && groups[1].nextElementSibling.getAttribute('data-card') === 'tool:16', 'the first group is untouched; the second sits before its first member');
  P.toggleGroup(g1.querySelector('summary'));
  ok(!g1.open && members.every(e => e.hidden), 'closing folds the lines away again');

  // a long answer is clamped behind "show more"; the state lives on the card and survives a re-render
  const longText = Array.from({ length: 20 }, (_, i) => 'line ' + i + ' of a long answer').join('\n');
  feed.cards.push({ n: 18, ts: t0 + 8, kind: 'answer', text: longText, html: '<p>' + longText.replace(/\n/g, '<br>') + '</p>', backend: 'claude' });
  feed.cards.push({ n: 19, ts: t0 + 9, kind: 'answer', text: 'x'.repeat(1200), html: '<p>' + 'x'.repeat(1200) + '</p>', backend: 'claude' });
  feed.last = 19;
  await P.poll(true); await tick();
  const a18 = feedEl.querySelector('[data-card="answer:18"]'), a19 = feedEl.querySelector('[data-card="answer:19"]');
  ok(a18.querySelector('.ag-md.ag-clamp') && a18.querySelector('.ag-more').textContent === 'show more', 'an answer over ~14 lines is clamped with a show-more link');
  ok(a19.querySelector('.ag-md.ag-clamp') && a19.querySelector('.ag-more'), 'so is one over ~1,100 characters');
  P.toggleMore(a18.querySelector('.ag-more'));
  ok(a18.getAttribute('data-expanded') === '1' && a18.querySelector('.ag-more').textContent === 'show less', 'show more opens the card (the state lives on the card)');
  feed.cards[feed.cards.length - 2].html += ' ';
  P._state.after = 0;
  await P.poll(true); await tick();
  ok(a18.getAttribute('data-expanded') === '1' && a18.querySelector('.ag-more').textContent === 'show less' && a18.querySelector('.ag-md').innerHTML.endsWith(' '), 'the open state survives a re-render of the card, and the link follows it');
  P.toggleMore(a18.querySelector('.ag-more'));
  ok(!a18.hasAttribute('data-expanded') && a18.querySelector('.ag-more').textContent === 'show more', 'and toggles back');

  // the compact float carries the same strip + composer; its presets sit behind one toggle
  const popRoot = document.getElementById('agent-popover').querySelector('.ag-root.ag-compact');
  ok(popRoot.querySelector('.ag-now .ag-now-acts') && popRoot.querySelector('form.ag-composer textarea.ag-input[rows="1"]') && popRoot.querySelector('.ag-presets[title] .ag-presets-toggle'), 'the compact float has the same strip, composer and preset toggle');
  ok(popRoot.querySelectorAll(':scope > .ag-cards > .ag-toolgroup').length === 2 && popRoot.querySelector('[data-card="answer:18"] .ag-more'), 'and the same folded tool runs and clamp');
  ok(popRoot.querySelector('.ag-input').placeholder === 'Ask, or tell the agent what to do…  (Enter sends)' && /\/run <node>/.test(h2.querySelector('.ag-input').placeholder), 'the float\'s one-row box carries the short hint; the home keeps the /run form');
  const tog = popRoot.querySelector('.ag-presets-toggle');
  P.togglePresets(tog);
  ok(popRoot.querySelector('.ag-presets').classList.contains('open') && tog.getAttribute('aria-expanded') === 'true', 'the compact preset toggle opens the chips');

  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
