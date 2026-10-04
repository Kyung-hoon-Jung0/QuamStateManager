/* docs/272 -- four small Agent-UI findings, against the real agent.js /
 * agent-pill.js / agent-setup.js / journal.js under jsdom. Each "tab" is its
 * own JSDOM window with its OWN sessionStorage over ONE shared localStorage,
 * which is exactly how a browser splits them. runScripts "dangerously" so the
 * cards' real inline onclick / oninput attributes run, not a copy of them.
 *   C-21  a closed plan's step shows its run's real state, never a stale ▶
 *   C-23  the person's name is per tab; every door in a tab records the same
 *   C-24  one value format on the cards: the feed's display unit, one
 *         unit and one precision per row, params as sent
 *   C-12  "N waiting" leads to the approval cards, never the Calibration log
 * Run: node tests/agent_ui_p3_selfcheck.cjs */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
let VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const source = n => fs.readFileSync(path.join(STATIC, n), 'utf8');
const tick = (ms) => new Promise(r => setTimeout(r, ms || 40));
let passes = 0, fails = 0;
function pin(id, c, detail) { if (c) { passes++; console.log('ok - ' + id); } else { fails++; console.error('FAIL: ' + id + (detail !== undefined ? ' :: ' + detail : '')); } }
function store(initial) {
  const m = new Map(Object.entries(initial || {}));
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: k => { m.delete(k); } };
}

// ---- the feed, as /api/agent/chat/cards sends it (display specs as core/units.display_spec gives them)
const US = { unit: 'µs', scale: 1e6, dp: 2, stored: 's' };
const GHZ = { unit: 'GHz', scale: 1e-9, dp: 4, stored: 'Hz' };
const NS = { unit: 'ns', scale: 1, dp: 0, stored: 'ns' };
const apWrites = [
  { path: 'qubits.q1.T1', old: 4.2e-5, new: 3.3e-5, now: 5.55e-5, now_known: true, display: US },
  { path: 'qubits.q1.f_01', old: 6.25e9, new: 6.25001e9, now: 6.25e9, now_known: true, display: GHZ },
  { path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude', old: 0.370173854595479, new: 0.373876, now: 0.370173854595479, now_known: true }];
const runWrites = [
  { path: 'qubits.q1.T1', old: 4.2e-5, new: 3.3e-5, display: US },
  { path: 'qubits.q1.xy.operations.x180.length', old: 40, new: 44, display: NS },
  { path: 'qubits.q1.resonator.operations.readout.length', old: 40, new: 40.5, display: NS },
  { path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude', old: 0.1, new: 0.11 },
  { path: 'qubits.q1.T2ramsey_fit_seconds', old: 3.931e-5, new: 4.2e-5 },
  { path: 'qubits.q1.n_avg', old: 1000000, new: 2000000 }];          // integers as stored, even past 1e6
const PARAMS = { frequency_span_in_mhz: 20, num_shots: 100000, amp: 0.123456789 };
function plan(id, status, steps, extra) {
  return Object.assign({ id, title: id, status, created: 10, counts: {}, steps }, extra || {});
}
function feed() {
  return {
    ok: true, chip: 'C', chip_key: 'C-1', last: 0, agent_seq: 1, cards: [], session: {}, file: {},
    now: { state: 'waiting', waiting: 1 },
    live: {
      plans: [
        plan('pl-stopped', 'stopped', [
          { i: 0, node: 'n_cancel', targets: ['q1'], status: 'running', run_key: 'rk-cancel', params: PARAMS },
          { i: 1, node: 'n_pending', targets: ['q1'], status: 'cancelled' },
          { i: 2, node: 'n_done', targets: ['q1'], status: 'done' }]),
        plan('pl-done-run', 'stopped', [{ i: 0, node: 'n', targets: ['q1'], status: 'running', run_key: 'rk-done' }]),
        plan('pl-cancelled', 'cancelled', [{ i: 0, node: 'n', targets: ['q1'], status: 'running', run_key: 'rk-live' }]),
        plan('pl-unlisted', 'stopped', [{ i: 0, node: 'n', targets: ['q1'], status: 'running', run_key: 'rk-gone' }]),
        plan('pl-running', 'running', [{ i: 0, node: 'n', targets: ['q1'], status: 'running', run_key: 'rk-live' }],
          { may_change: [{ target: 'q1', path: 'qubits.q1.T1', now: 3.5e-5, label: 'T1', display: US }], may_change_total: 1 }),
        plan('pl-stopping', 'stopping', [{ i: 0, node: 'n', targets: ['q1'], status: 'running', run_key: 'rk-cancel' }])],
      runs: [
        { key: 'rk-cancel', status: 'ended', node: 'n_cancel', targets: ['q1'], since: 20, params: PARAMS, result: { status: 'cancelled', writes: runWrites } },
        { key: 'rk-done', status: 'done', node: 'n', targets: ['q1'], since: 21, result: { status: 'done' } },
        { key: 'rk-live', status: 'running', node: 'n', targets: ['q1'], since: 22, result: {} }],
      approvals: [{ id: 'ap1', kind: 'writes', node: 'n_cancel', targets: ['q1'], created: 30, why_held: 'mode ask-writes', params: PARAMS, writes: apWrites }]
    }
  };
}

// ---- one browser tab
async function tab(local, opts) {
  opts = opts || {};
  const body = opts.body || '<li id="agent-pill" hidden><a class="agent-pill-link" href="/journal" hx-get="/journal" hx-push-url="true"><span class="agent-pill-text"></span></a></li><div id="agent-home"></div>';
  // a link that really navigates shows up as jsdom's "navigation not implemented": record it, print the rest
  const navs = [], vc = new VirtualConsole();
  vc.on('jsdomError', e => { if (/navigation/i.test(e.message)) navs.push(e.message); else console.error(e.stack || e.message); });
  ['log', 'warn', 'error'].forEach(k => vc.on(k, (...a) => console[k](...a)));
  const dom = new JSDOM('<!doctype html><html><body>' + body + '</body></html>',
    { url: 'http://localhost' + (opts.at || '/agent'), pretendToBeVisual: true, runScripts: 'dangerously', virtualConsole: vc });
  const w = dom.window, calls = [], processed = [];
  w.confirm = () => true;
  Object.defineProperty(w, 'localStorage', { value: local, configurable: true });
  if (opts.sessionThrows) Object.defineProperty(w, 'sessionStorage', { get() { throw new Error('blocked'); }, configurable: true });
  else Object.defineProperty(w, 'sessionStorage', { value: opts.session || store(), configurable: true });
  // htmx, as far as a click goes: process() binds a click handler to every [hx-get] it is
  // handed (real htmx: at process time, after any inline onclick); htmxClicks records the
  // htmx navigations that would have happened
  const htmxClicks = [];
  w.htmx = { process: el => {
    processed.push(el);
    [el].concat(Array.from(el.querySelectorAll ? el.querySelectorAll('[hx-get]') : [])).forEach(x => {
      if (!x.getAttribute || !x.getAttribute('hx-get') || x.__stubBound) return;
      x.__stubBound = true;
      x.addEventListener('click', e => { htmxClicks.push(x.getAttribute('hx-get')); e.preventDefault(); });
    });
  } };
  const data = opts.feed || feed();
  w.fetch = (url, o) => {
    calls.push({ url: String(url), opts: o || {} });
    let b = { ok: true };
    if (/chat\/cards/.test(url)) b = data;
    else if (/chat\/backends/.test(url)) b = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
    else if (/api\/agent\/now/.test(url)) b = { ok: true, state: 'idle' };
    return Promise.resolve({ status: 200, json: () => Promise.resolve(b) });
  };
  for (const n of opts.scripts || ['agent-pill.js', 'agent.js']) w.eval(source(n));
  await tick(60);
  const doc = w.document;
  return { w, dom, doc, calls, processed, htmxClicks, data, navs, P: w.AgentPanel,
           card: k => doc.querySelector('[data-card="' + k + '"]'),
           headerOf: re => { const c = calls.filter(x => re.test(x.url)).pop(); return c && c.opts.headers && c.opts.headers['X-SM-Actor']; } };
}
const txt = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

(async () => {
  const local = store({ quam_actor_name: 'Alice' });
  const sessA = store();
  const A = await tab(local, { session: sessA });

  // ------------------------------------------------------------ C-21
  const badges = id => Array.from(A.card('plan:' + id).querySelectorAll('.ag-step-st'));
  const b0 = badges('pl-stopped')[0];
  pin('c21-stopped-step-shows-its-runs-end', txt(b0) === '—' && b0.classList.contains('ag-st-cancelled') && /how its run ended/.test(b0.title) && /stopped/.test(b0.title), txt(b0) + ' / ' + b0.title);
  const bd = badges('pl-done-run')[0];
  pin('c21-a-run-that-finished-reads-done', txt(bd) === '✓' && bd.classList.contains('ag-st-done'), txt(bd) + ' / ' + bd.title);
  const bl = badges('pl-cancelled')[0];
  pin('c21-a-run-still-going-keeps-the-marker', txt(bl) === '▶' && /its run goes on/.test(bl.title) && /cancelled/.test(bl.title), txt(bl) + ' / ' + bl.title);
  const bu = badges('pl-unlisted')[0];
  pin('c21-an-unlisted-run-reads-stopped', txt(bu) === '⏹' && bu.classList.contains('ag-st-stopped') && /no longer listed/.test(bu.title), txt(bu) + ' / ' + bu.title);
  pin('c21-no-closed-plan-shows-the-marker', ['pl-stopped', 'pl-done-run', 'pl-unlisted'].every(id => badges(id).every(b => txt(b) !== '▶')));
  pin('c21-active-plans-keep-the-marker', ['pl-running', 'pl-stopping'].every(id => txt(badges(id)[0]) === '▶' && badges(id)[0].title === 'running'));
  pin('c21-other-steps-say-their-own-state', txt(badges('pl-stopped')[1]) === '—' && badges('pl-stopped')[1].title === 'cancelled' && txt(badges('pl-stopped')[2]) === '✓');

  // ------------------------------------------------------------ C-24
  const rows = Array.from(A.card('approval:ap1').querySelectorAll('tbody tr'));
  const t1 = rows[0].cells;
  pin('c24-a-moved-row-is-one-unit-one-precision', txt(t1[1]) === '55.50 µs ⚠ proposed from 42.00 µs' && t1[1].classList.contains('ag-ap-moved'), txt(t1[1]));
  pin('c24-the-moved-title-says-the-same', t1[1].title === 'the proposal was made from 42.00 µs; SM holds 55.50 µs now', t1[1].title);
  const inT1 = t1[2].querySelector('.ag-ap-new');
  pin('c24-proposed-in-the-unit-beside-the-raw-input', txt(t1[2].querySelector('.ag-ap-value')) === '33.00 µs' && inT1.value === '0.000033'
    && txt(t1[2].querySelector('.ag-ap-unit')) === 's' && /as stored \(s\)/.test(inT1.title), txt(t1[2]) + ' | ' + inT1.value + ' | ' + inT1.title);
  const f = rows[1].cells;
  pin('c24-two-close-values-never-print-alike', txt(f[1]) === '6.25000 GHz' && txt(f[2].querySelector('.ag-ap-value')) === '6.25001 GHz', txt(f[1]) + ' | ' + txt(f[2]));
  const amp = rows[2].cells;
  pin('c24-no-unit-means-no-guessed-unit', txt(amp[1]) === '0.37017' && txt(amp[2].querySelector('.ag-ap-value')) === '0.37388' && !amp[2].querySelector('.ag-ap-unit'), txt(amp[1]) + ' | ' + txt(amp[2]));
  const runRows = Array.from(A.card('run:rk-cancel').querySelectorAll('.ag-writes tr')).map(r => txt(r.cells[1]));
  pin('c24-run-writes-share-unit-and-precision', runRows[0] === '42.00 µs → 33.00 µs' && runRows[1] === '40 ns → 44 ns' && runRows[2] === '40.0 ns → 40.5 ns', runRows.join(' | '));
  pin('c24-run-writes-without-a-unit', runRows[3] === '0.10 → 0.11' && runRows[4] === '3.931e-5 → 4.200e-5' && runRows[5] === '1000000 → 2000000', runRows.join(' | '));
  const runTitle = A.card('run:rk-cancel').querySelector('.ag-writes tr').cells[1].title;
  pin('c24-the-stored-values-ride-the-title', runTitle === 'stored: 0.000042 → 0.000033', runTitle);
  const may = A.card('plan:pl-running').querySelector('.ag-may li');
  pin('c24-the-plans-now-in-its-unit', / now 35\.00 µs/.test(' ' + txt(may)) && /stored: 0\.000035/.test(may.querySelector('[title^="stored"]').title), txt(may));
  const params = ['approval:ap1', 'run:rk-cancel', 'plan:pl-stopped'].map(k => txt(A.card(k).querySelector('.ag-params')));
  pin('c24-params-stay-as-sent', params.every(p => /amp=0\.123456789/.test(p) && /frequency_span_in_mhz=20(\s|$)/.test(p) && /num_shots=100000/.test(p) && !/Hz|µs/.test(p)), params.join(' | '));
  const fr = A.P.fmtRow;
  // the precise collision rule: never two different values as one text, at most 6 extra decimals
  const tight = fr([1.5e-5, 1.5001e-5], US);
  pin('c24-row-rule-distinct', JSON.stringify(tight) === '["15.000 µs","15.001 µs"]', JSON.stringify(tight));
  pin('c24-row-rule-trailing-zeros', JSON.stringify(fr([0.5, 0.25])) === '["0.50","0.25"]' && JSON.stringify(fr([7, 8])) === '["7","8"]'
    && JSON.stringify(fr([null, 'x', true])) === '["null","x","true"]', JSON.stringify([fr([0.5, 0.25]), fr([7, 8]), fr([null, 'x', true])]));
  // typing a new raw value: the formatted value beside it follows, and Write sends what it shows
  inT1.focus();
  inT1.value = '0.00005';
  inT1.dispatchEvent(new A.w.Event('input', { bubbles: true }));
  pin('c24-the-preview-follows-the-typed-value', txt(t1[2].querySelector('.ag-ap-value')) === '50.00 µs' && txt(t1[1]) === '55.50 µs ⚠ proposed from 42.00 µs', txt(t1[2]));
  A.card('approval:ap1').querySelector('.ag-approve').click();
  await tick();
  const sent = A.calls.find(c => /ap1\/approve$/.test(c.url));
  const sw = sent && JSON.parse(sent.opts.body).writes;
  pin('c24-write-sends-the-raw-value-it-showed', sw && sw[0].new === 0.00005 && sw[1].new === 6.25001e9 && sw[2].new === 0.373876, JSON.stringify(sw));

  // ------------------------------------------------------------ C-23
  pin('c23-a-tab-seeds-from-the-shared-name', A.P.actorName() === 'Alice' && sessA.getItem('quam_actor_name') === 'Alice');
  const sessB = store();
  const B = await tab(local, { session: sessB, scripts: ['agent-pill.js', 'agent.js', 'agent-setup.js', 'journal.js'],
    body: '<div id="agent-home"></div><div class="jr-claim" data-run="7"><input class="jr-who"><input class="jr-note-in" value=""><button class="claim-btn">Save</button></div>' });
  A.P.setActor('Bob', A.doc.querySelector('.ag-actor'));
  A.P.stop('now'); B.P.stop('now');
  await tick();
  pin('c23-another-tabs-name-does-not-move-mine', B.P.actorName() === 'Alice' && B.headerOf(/\/stop$/) === 'Alice', B.headerOf(/\/stop$/));
  pin('c23-my-tab-records-mine', A.headerOf(/\/stop$/) === 'Bob' && sessA.getItem('quam_actor_name') === 'Bob', A.headerOf(/\/stop$/));
  pin('c23-the-last-name-set-is-the-new-tab-default', local.getItem('quam_actor_name') === 'Bob');
  const C = await tab(local);
  pin('c23-a-new-tab-starts-from-it', C.P.actorName() === 'Bob');
  pin('c23-the-composer-says-whose-name-it-records', txt(A.doc.querySelector('.ag-actor-record')) === 'records human:Bob · this tab'
    && txt(B.doc.querySelector('.ag-actor-record')) === 'records human:Alice · this tab' && B.doc.querySelector('.ag-actor').value === 'Alice',
    txt(A.doc.querySelector('.ag-actor-record')) + ' | ' + txt(B.doc.querySelector('.ag-actor-record')));
  local.setItem('quam_actor_name', 'Carol');                     // a third tab moved the shared default
  const A2 = await tab(local, { session: sessA });               // ... and tab A reloads
  pin('c23-a-reload-keeps-the-tabs-name', A2.P.actorName() === 'Bob' && txt(A2.doc.querySelector('.ag-actor-record')) === 'records human:Bob · this tab');
  B.w.AgentSetup.disconnect('claude');
  await tick();
  pin('c23-setup-records-the-tabs-name', B.headerOf(/setup\/disconnect$/) === 'Alice', B.headerOf(/setup\/disconnect$/));
  pin('c23-the-journal-prefills-the-tabs-name', B.doc.querySelector('.jr-who').value === 'Alice', B.doc.querySelector('.jr-who').value);
  B.doc.querySelector('.jr-who').value = '';
  B.w.JournalPage.claim(B.doc.querySelector('.claim-btn'));
  await tick();
  const claimCall = B.calls.find(c => /journal\/claim$/.test(c.url));
  pin('c23-an-empty-claim-is-the-tabs-name', claimCall && JSON.parse(claimCall.opts.body).who === 'Alice', claimCall && claimCall.opts.body);
  A.P.setActor('', A.doc.querySelector('.ag-actor'));
  pin('c23-no-name-says-plain-human', txt(A.doc.querySelector('.ag-actor-record')) === 'records human · this tab' && A.P.actorName() === '');
  // a page with no Agent panel on it still fixes its tab's name when it LOADS, not at first use
  const gLocal = store({ quam_actor_name: 'Gil' });
  const G = await tab(gLocal, { body: '<div id="nothing-here"></div>' });
  gLocal.setItem('quam_actor_name', 'Hana');                     // another tab, later
  pin('c23-seeded-at-load-not-at-first-use', G.P.actorName() === 'Gil', G.P.actorName());
  const D = await tab(store({ quam_actor_name: 'Dana' }), { sessionThrows: true });
  pin('c23-blocked-tab-storage-falls-back-to-the-shared-name', D.P.actorName() === 'Dana');

  // ------------------------------------------------------------ C-12
  const link = A.doc.querySelector('.agent-pill-link');
  A.processed.length = 0;
  A.w.AgentPill.render({ state: 'waiting', waiting: 2 });
  pin('c12-the-pill-leads-to-the-approvals', link.getAttribute('href') === '/agent#approvals' && link.getAttribute('hx-get') === '/agent'
    && link.getAttribute('hx-push-url') === '/agent#approvals' && /decide them/.test(A.doc.getElementById('agent-pill').title),
    link.outerHTML.slice(0, 200));
  pin('c12-htmx-re-reads-the-changed-link', A.processed.filter(e => e === link).length === 1, A.processed.length);
  A.w.AgentPill.render({ state: 'waiting', waiting: 3 });
  pin('c12-an-unchanged-link-is-not-reprocessed', A.processed.filter(e => e === link).length === 1);
  A.w.AgentPill.render({ state: 'idle' });
  pin('c12-no-waiting-is-the-calibration-log-again', link.getAttribute('href') === '/journal' && link.getAttribute('hx-get') === '/journal'
    && link.getAttribute('hx-push-url') === 'true' && A.processed.filter(e => e === link).length === 2 && /Calibration log/.test(A.doc.getElementById('agent-pill').title));
  A.w.AgentPill.render({ state: 'waiting', waiting: 1 });
  A.doc.querySelector('.ag-input').focus();
  const ev1 = new A.w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
  link.dispatchEvent(ev1);
  const apCard = A.card('approval:ap1');
  pin('c12-on-the-agent-page-the-pill-reveals-in-place', ev1.defaultPrevented && A.doc.activeElement === apCard && A.htmxClicks.length === 0,
    (A.doc.activeElement && A.doc.activeElement.className) + ' | htmx: ' + A.htmxClicks.join(','));
  pin('c12-never-the-write-button', A.doc.activeElement !== apCard.querySelector('.ag-approve'));
  const strip = A.doc.querySelector('.ag-waiting-link');
  pin('c12-the-strip-leads-to-the-approvals', strip && strip.getAttribute('href') === '/agent#approvals' && strip.getAttribute('hx-get') === '/agent'
    && strip.getAttribute('hx-push-url') === '/agent#approvals' && /waiting 1/.test(txt(strip)), strip && strip.outerHTML.slice(0, 260));
  A.doc.querySelector('.ag-input').focus();
  const ev2 = new A.w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
  if (strip) strip.dispatchEvent(ev2);
  pin('c12-the-strip-reveals-in-place', ev2.defaultPrevented && A.doc.activeElement === apCard && A.htmxClicks.length === 0,
    'htmx: ' + A.htmxClicks.join(','));
  // a page with no visible cards: the float is closed -- the links navigate (htmx) to the Agent page
  const E = await tab(store(), { at: '/journal', body: '<li id="agent-pill"><a class="agent-pill-link" href="/journal" hx-get="/journal" hx-push-url="true"><span class="agent-pill-text"></span></a></li>'
    + '<div id="agent-popover" class="agent-popover agent-hidden"><div class="agent-body"></div></div>' });
  E.P.toggleFloat(); await tick(80); E.P.toggleFloat();          // opened (mounted, cards rendered), then closed
  const eLink = E.doc.querySelector('.agent-pill-link');
  E.w.AgentPill.render({ state: 'waiting', waiting: 1 });
  const ev3 = new E.w.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
  eLink.dispatchEvent(ev3);
  await tick();                                                   // jsdom reports the navigation on its own task
  pin('c12-a-closed-float-does-not-swallow-the-click', !!E.doc.querySelector('#agent-popover [data-card="approval:ap1"]')
    && E.doc.getElementById('agent-popover').classList.contains('agent-hidden') && E.htmxClicks.join() === '/agent'
    && !(E.doc.activeElement && E.doc.activeElement.matches && E.doc.activeElement.matches('[data-card]')), 'htmx: ' + E.htmxClicks.join(','));
  // arriving through the link: #approvals brings the card into view once, then leaves the person alone
  const F = await tab(store(), { at: '/agent#approvals' });
  const fCard = F.card('approval:ap1');
  pin('c12-arriving-at-approvals-reveals-the-card', F.doc.activeElement === fCard, F.doc.activeElement && F.doc.activeElement.tagName);
  F.doc.querySelector('.ag-input').focus();
  F.P.absorb(F.data); await tick();
  pin('c12-only-once-per-panel', F.doc.activeElement === F.doc.querySelector('.ag-input'));

  for (const t of [A, B, C, A2, D, E, F, G]) t.w.close();
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
