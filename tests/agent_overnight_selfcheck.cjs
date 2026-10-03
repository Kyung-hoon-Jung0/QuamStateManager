/* docs/261 -- the overnight run, in the real agent.js and agent-summary.js under jsdom.
 * Start on an AUTO plan shows the envelope it approves first (stop_by with its deadline in the
 * viewer's zone, max writes, max |Δ|, stop-loss, alerts, the steps) and posts back what it showed;
 * an envelope that changed since is shown again, never armed; an ask-writes plan starts in one
 * press as before; the strip links the night summary; the summary's buttons press the right
 * doors and re-read the page.   Run: node tests/agent_overnight_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
const dom = new JSDOM('<!doctype html><html><body><div id="agent-home"></div><div id="sum-host"></div></body></html>',
  { url: 'http://localhost/agent', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event; global.navigator = window.navigator;
global.localStorage = window.localStorage;
const toasts = [];
window.showToast = function (m, level) { toasts.push({ m: String(m), level: level }); };
window.SnapTime = { display: function (d) { return 'SNAP ' + d.toISOString().slice(0, 16); } };
const now = Date.now() / 1000;
const DEADLINE = now + 11 * 3600 + 4 * 60 + 30;
const steps = [{ i: 0, node: '05_power_rabi', targets: ['qA1'], params: { num_shots: 200 }, status: 'pending' },
               { i: 1, node: '06_ramsey', targets: ['qA2'], params: {}, status: 'pending' }];
const planA = { id: 'pl-a', title: 'night run', status: 'draft', mode: 'auto', created: now - 10, source: 'agent', created_by: 'by_claude',
  counts: { total: 2 }, may_change: [], may_change_total: 0, steps: steps };
const planB = { id: 'pl-b', title: 'by day', status: 'draft', mode: 'ask-writes', created: now - 5, source: 'agent', created_by: 'by_claude',
  counts: { total: 1 }, may_change: [], may_change_total: 0, steps: [steps[0]] };
const planC = { id: 'pl-c', title: 'night with halts', status: 'running', mode: 'auto', created: now - 20, source: 'agent', created_by: 'by_claude',
  counts: { total: 3 }, may_change: [], may_change_total: 0, steps: [
    { i: 0, node: '96_fake', targets: ['qA3'], params: {}, status: 'done', run_id: 5,
      gate: { qA3: { v: 'fail', why: "fit: the node's own outcome for qA3 is failed" } } },
    { i: 1, node: '11_power_rabi', targets: ['qA3'], params: {}, status: 'skipped', halted: 'qA3',
      error: 'target qA3 halted: 2 gate fails in a row (stoploss_target 2)' },
    { i: 2, node: '11_power_rabi', targets: ['qA4'], params: {}, status: 'pending', gate: null }] };
const ENV = { mode: 'auto', stop_by: '06:00', max_writes_per_plan: 200, max_delta: { power_rabi: 0.05 }, stoploss_target: 3, stoploss_plan: 8 };
function envBody(vals) {
  return { ok: true, plan_id: 'pl-a', title: 'night run', status: 'draft', mode: 'auto', envelope: vals, deadline: DEADLINE,
    lines: [{ key: 'stop_by', label: 'stop by', text: '06:00 -- the first 06:00 after Start', at: DEADLINE },
            { key: 'max_writes_per_plan', label: 'max writes', text: vals.max_writes_per_plan + ' per plan -- a run whose writes would pass it is held' },
            { key: 'max_delta', label: 'max |Δ|', text: 'power_rabi 0.05 (in the value\'s own unit)' },
            { key: 'stoploss', label: 'stop-loss', text: 'a target halts after 3 gate fails in a row; the plan halts after 8 gate fails' },
            { key: 'alerts', label: 'alerts', text: 'webhook on -> http://127.0.0.1:5195/... for agent_failure, plan_done, needs_human' }],
    warnings: ['Dry run is ON: SM refuses every run of an auto plan (simulate_on_in_auto).'],
    webhook: { on: true }, steps: steps.map(s => ({ i: s.i, node: s.node, targets: s.targets, params: s.params })) };
}
const feed = { ok: true, chip: 'C', chip_key: 'C-1', last: 0, agent_seq: 1, cards: [], session: {}, file: {}, now: { state: 'idle' },
  live: { approvals: [], plans: [planA, planB, planC], runs: [] } };
const calls = [];
let startAnswers = [];
let summaryHtml = '';
let approveStatus = 200, approveBody = { ok: true, approval: { kind: 'writes' } };
global.fetch = window.fetch = function (url, opts) {
  url = String(url);
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url: url, method: (opts && opts.method) || 'GET', body: body, headers: (opts && opts.headers) || {} });
  let st = 200, b = { ok: true };
  if (/\/chat\/cards/.test(url)) b = feed;
  else if (/\/chat\/backends/.test(url)) b = { ok: true, chip: 'C', default: 'claude', backends: { claude: { found: true } } };
  else if (/\/plans\/pl-a\/envelope$/.test(url)) b = envBody(ENV);
  else if (/\/plans\/pl-[ab]\/start$/.test(url)) { const a = startAnswers.shift() || [200, { ok: true }]; st = a[0]; b = a[1]; }
  else if (/\/approvals\/ap-1\/approve$/.test(url)) { st = approveStatus; b = approveBody; }
  else if (/\/approvals\/ap-1\/reject$/.test(url)) b = { ok: true };
  else if (/^\/agent\/summary/.test(url)) {
    return Promise.resolve({ status: 200, text: () => Promise.resolve(summaryHtml), json: () => Promise.resolve({}) });
  }
  return Promise.resolve({ status: st, json: () => Promise.resolve(b), text: () => Promise.resolve(JSON.stringify(b)) });
};
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-pill.js'), 'utf8'), { filename: 'agent-pill.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent.js'), 'utf8'), { filename: 'agent.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'agent-summary.js'), 'utf8'), { filename: 'agent-summary.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ') : '');
const card = (k) => document.querySelector('[data-card="' + k + '"]');
const starts = () => calls.filter(c => /\/start$/.test(c.url));

(async () => {
  const P = window.AgentPanel;
  await tick(80);
  ok(card('plan:pl-a') && card('plan:pl-b'), 'both plan cards render');
  // the stop-loss's reading of a step, on the step row
  const cRows = Array.from(card('plan:pl-c').querySelectorAll('.ag-step'));
  ok(/fit failed: qA3/.test(txt(cRows[0])) && /outcome for qA3 is failed/.test(cRows[0].querySelector('.ag-step-gate').getAttribute('title')),
     'a finished run whose fit failed says so on its row: ' + txt(cRows[0]));
  ok(/qA3 halted/.test(txt(cRows[1])) && /2 gate fails in a row/.test(cRows[1].querySelector('.ag-step-gate').getAttribute('title')),
     'a step skipped for a halted target says which, with why in the title: ' + txt(cRows[1]));
  ok(!cRows[2].querySelector('.ag-step-gate'), 'a pending step says nothing of the kind');

  // ---- an AUTO plan: Start shows the envelope first, arms nothing yet
  P.startPlan('pl-a');
  await tick(40);
  ok(calls.some(c => /\/plans\/pl-a\/envelope$/.test(c.url) && c.method === 'GET'), 'Start on an auto plan reads its envelope');
  ok(starts().length === 0, 'and arms nothing yet');
  const envEl = card('plan:pl-a').querySelector('.ag-envelope');
  ok(!!envEl, 'the card shows the envelope');
  const t = txt(envEl);
  ['stop by', 'max writes', 'max |Δ|', 'stop-loss', 'alerts'].forEach(l => ok(t.indexOf(l) >= 0, 'the envelope names ' + l));
  ok(/06:00 -- the first 06:00 after Start/.test(t), 'stop_by and what it means');
  ok(/SNAP \d{4}-\d\d-\d\dT\d\d:\d\d/.test(txt(envEl.querySelector('[data-env="stop_by"]'))), 'the deadline in the viewer\'s zone (SnapTime.display)');
  ok(/\(in 11 h \d+ min\)/.test(txt(envEl.querySelector('[data-env="stop_by"]'))), 'and how far away it is: ' + txt(envEl.querySelector('[data-env="stop_by"]')));
  ok(/200 per plan/.test(t) && /power_rabi 0\.05/.test(t) && /halts after 3 gate fails in a row/.test(t), 'every value the Start approves');
  ok(/Dry run is ON/.test(txt(envEl.querySelector('.ag-env-warn'))), 'a Runner that would refuse every run says so');
  const lis = Array.from(envEl.querySelectorAll('.ag-env-steps li')).map(txt);
  ok(lis.length === 2 && /05_power_rabi qA1/.test(lis[0]) && /num_shots=200/.test(lis[0]) && /06_ramsey qA2/.test(lis[1]), 'the steps, as the card shows them: ' + lis.join(' | '));
  ok(!card('plan:pl-a').querySelector('.ag-start:not(.ag-env-ok)'), 'the plain Start is replaced by the envelope\'s own');
  ok(/Start — approve this envelope/.test(txt(envEl.querySelector('.ag-env-ok'))), 'the press says what it approves');

  // ---- Back closes it
  P.closeEnvelope('pl-a');
  await tick(20);
  ok(!card('plan:pl-a').querySelector('.ag-envelope') && card('plan:pl-a').querySelector('.ag-start'), 'Back: the card is as it was');

  // ---- the envelope changed since the card showed it: shown again, never armed
  P.startPlan('pl-a');
  await tick(40);
  const changed = Object.assign({}, ENV, { max_writes_per_plan: 5 });
  startAnswers = [[409, { ok: false, refused: 'envelope_changed', error: 'the envelope changed', envelope: { values: changed, lines: envBody(changed).lines, warnings: [], deadline: DEADLINE } }],
                  [200, { ok: true, driver: { kind: 'terminal', actor: 'by_claude' } }]];
  P.confirmStart('pl-a');
  await tick(40);
  ok(starts().length === 1 && JSON.stringify(starts()[0].body) === JSON.stringify({ envelope: ENV }), 'Start posts exactly the envelope it showed: ' + JSON.stringify(starts()[0].body));
  ok(/5 per plan/.test(txt(card('plan:pl-a').querySelector('.ag-envelope'))), 'a changed envelope is shown again with its new values');
  ok(!card('plan:pl-a').querySelector('.ag-env-warn'), 'and its own warnings');
  ok(toasts.length && /limits changed/.test(toasts[toasts.length - 1].m) && toasts[toasts.length - 1].level === 'error', 'the person is told why');
  P.confirmStart('pl-a');
  await tick(40);
  ok(starts().length === 2 && starts()[1].body.envelope.max_writes_per_plan === 5, 'the second press approves what the second look showed');
  ok(/started/.test(toasts[toasts.length - 1].m), 'started');

  // ---- ask-writes: one press, no envelope step
  const before = calls.length;
  P.startPlan('pl-b');
  await tick(40);
  const after = calls.slice(before);
  ok(!after.some(c => /envelope/.test(c.url)) && after.some(c => /\/plans\/pl-b\/start$/.test(c.url) && JSON.stringify(c.body) === '{}'),
     'an ask-writes plan starts in one press, as before');

  // ---- the strip links the night summary
  const link = document.querySelector('#agent-home a.ag-summary-link');
  ok(link && link.getAttribute('href') === '/agent/summary' && /Night summary/.test(link.textContent), 'the strip links the night summary');

  // ================================================== agent-summary.js
  function section(msg) {
    return '<div id="agent-summary" class="agent-summary" data-plan="pl-a"><p class="ns-msg muted"></p>' +
      '<div class="ns-item" data-ap="ap-1"><button data-ns-act="approve" data-id="ap-1">Write to chip</button>' +
      '<button data-ns-act="reject" data-id="ap-1">Reject</button></div>' +
      '<button class="ns-undo" data-ns-act="undo">Undo</button><span class="marker">' + msg + '</span></div>';
  }
  let localized = 0;
  window.applyLocalTimes = function () { localized++; };
  document.getElementById('sum-host').innerHTML = section('first');
  summaryHtml = section('re-read');
  ok(window.AgentSummary && typeof window.AgentSummary.refresh === 'function', 'agent-summary.js loaded');
  const sumCalls = () => calls.filter(c => /\/approvals\/ap-1\/|\/agent\/summary/.test(c.url));
  let n0 = sumCalls().length;
  document.querySelector('#agent-summary [data-ns-act="approve"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await tick(60);
  let made = sumCalls().slice(n0);
  ok(made[0] && /\/api\/agent\/approvals\/ap-1\/approve$/.test(made[0].url) && made[0].method === 'POST', 'Write to chip presses the approve door');
  ok(made[1] && made[1].url === '/agent/summary?plan=pl-a', 'then the summary is read again for its plan');
  ok(/re-read/.test(txt(document.querySelector('#agent-summary .marker'))), 'the page shows SM\'s answer, re-read');
  ok(txt(document.querySelector('#agent-summary .ns-msg')) === 'Written to the chip.', 'and says what happened, after the re-read');
  ok(localized === 1, 'the re-read section has its times localized (the swap replaced the element)');
  // the htmx path: the section comes back through htmx.ajax and is localized the same way
  window.htmx = { ajax: function (verb, url, o) {
    calls.push({ url: url, method: verb, body: null, headers: {} });
    const cur = document.getElementById('agent-summary'); const tmp = document.createElement('div'); tmp.innerHTML = section('htmx');
    cur.parentNode.replaceChild(tmp.firstChild, cur); return Promise.resolve(); } };
  await window.AgentSummary.refresh();
  ok(/htmx/.test(txt(document.querySelector('#agent-summary .marker'))) && localized === 2 &&
     txt(document.querySelector('#agent-summary .ns-msg')) === 'Written to the chip.', 'through htmx.ajax too: localized, the answer kept');
  delete window.htmx;

  approveStatus = 409; approveBody = { ok: false, error: 'stale_live: the live chip moved' };
  n0 = sumCalls().length;
  document.querySelector('#agent-summary [data-ns-act="approve"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await tick(60);
  const m = document.querySelector('#agent-summary .ns-msg');
  ok(/^Not written: stale_live/.test(txt(m)) && m.classList.contains('ag-err'), 'a refused approve says why: ' + txt(m));

  n0 = sumCalls().length;
  document.querySelector('#agent-summary [data-ns-act="reject"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await tick(60);
  made = sumCalls().slice(n0);
  ok(made[0] && /\/approvals\/ap-1\/reject$/.test(made[0].url) && made[0].method === 'POST', 'Reject presses the reject door');
  ok(txt(document.querySelector('#agent-summary .ns-msg')) === 'Rejected.', 'and says so');

  const undo = document.querySelector('#agent-summary .ns-undo');
  n0 = sumCalls().length;
  document.dispatchEvent(new window.CustomEvent('htmx:afterRequest', { detail: { elt: undo, successful: true, xhr: { status: 200 } } }));
  await tick(60);
  ok(sumCalls().slice(n0).some(c => c.url === '/agent/summary?plan=pl-a'), 'an undo press re-reads the summary');
  ok(/^Undo staged/.test(txt(document.querySelector('#agent-summary .ns-msg'))), 'and says the old values wait in the tray');
  const undo2 = document.querySelector('#agent-summary .ns-undo');
  document.dispatchEvent(new window.CustomEvent('htmx:afterRequest', { detail: { elt: undo2, successful: false,
    xhr: { status: 409, responseText: '<div class="status">Not reverted — qubits.qA1.f_01 has changed since</div>' } } }));
  await tick(60);
  ok(/^Not undone: Not reverted — qubits\.qA1\.f_01 has changed since/.test(txt(document.querySelector('#agent-summary .ns-msg'))), 'a refused undo says why');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
