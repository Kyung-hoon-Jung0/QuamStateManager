/* docs/173 S7 -- agent-setup.js against the real file under jsdom.
 * Pins: only undone sections open; a connect PREVIEW shows a diff and writes nothing until the
 * click; the diff marks added/removed lines; the journal click posts root + claude_says; the
 * context questions render with the detected answer selected and post the person's answers;
 * the test button shows the elapsed time and the answer verbatim.
 * Run: node tests/agent_setup_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="agent-setup"><div id="as-body"></div></div></body></html>', { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.localStorage = window.localStorage;
window.confirm = function () { return true; };
let status = {
  ok: true, clis: { claude: { found: true, version: '2.1.263' }, codex: { found: false } },
  claude: { mcp: false, hooks: false, allow: false, json: 'H/.claude.json', settings: 'H/.claude/settings.json' },
  codex: { mcp: false, config: 'H/.codex/config.toml' }, context: {}, record: {}, calibrations_folder: 'D:/lab/cal',
  journal: { root: 'D:/inst/journal', configured: false, suggested: 'D:/data/PJ/journal', claude_says: false },
  hook_command: '"py.exe" -m quam_state_manager.hook --backend claude', todo: ['connect_claude', 'allow', 'journal', 'context'],
  global_simulate: true
};
// the Runner's settings route: what is persisted, and whether a queue is running (409)
const BUSY_ERR = "Can't change global_simulate while the scheduler is running — pause or cancel the queue first.";
let settingsPersisted = true, settingsBusy = false, settingsDown = false;
const calls = [];
let savedCtx = null;
let limitsNow = { mode: 'ask-writes', max_writes_per_plan: 200, human_recent_min: 30, stop_by: '', webhook_url: '', max_delta: { ramsey: 2000000 } }, limitsRefuse = null;
function S_reset() { const st = window.AgentSetup && window.AgentSetup._state; if (st) st.answers = {}; }
global.fetch = window.fetch = function (url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const method = (opts && opts.method) || 'GET';
  calls.push({ url: String(url), method, body });
  // the server is gone (restarting / offline): the fetch itself REJECTS, no HTTP status at all
  if (settingsDown && /\/scheduler\/settings$/.test(url)) return Promise.reject(new TypeError('Failed to fetch'));
  let resp = { ok: true }, code = 200;
  if (/\/api\/agent\/setup$/.test(url)) resp = status;
  else if (/\/scheduler\/settings$/.test(url) && method === 'POST') {
    if (settingsBusy) { code = 409; resp = { ok: false, error: BUSY_ERR }; }
    else { settingsPersisted = !!body.global_simulate; resp = { ok: true, settings: { global_simulate: settingsPersisted } }; }
  }
  else if (/\/scheduler\/settings$/.test(url)) resp = { global_simulate: settingsPersisted, env_python: 'py' };
  else if (/\/setup\/connect/.test(url) && !body.apply) resp = { ok: true, applied: false, previews: { mcp: { file: 'H/.claude.json', exists: true, before: null, after: { command: 'py.exe' }, changed: true }, hooks: { file: 'H/.claude/settings.json', exists: true, before: { PreToolUse: [] }, after: { PreToolUse: [{ matcher: 'Bash' }] }, changed: true } }, writes: {} };
  else if (/\/setup\/connect/.test(url)) resp = { ok: true, applied: true, writes: { mcp: { file: 'H/.claude.json', backup: 'H/.claude.json.sm-backup-1' } } };
  else if (/\/setup\/context$/.test(url) && (!opts || opts.method === 'GET')) resp = { ok: true, facts: { n_qubits: 20, n_pairs: 30, bias_modes: { opx: 9 }, nodes: ['05_power_rabi'] }, questions: [{ id: 'tunable', kind: 'choice', options: ['flux-tunable', 'fixed-frequency', 'mixed'], question: 'Are the qubits flux-tunable?', detected: 'mixed', why: 'x' }, { id: 'notes', kind: 'text', question: 'Anything else?', detected: '' }], saved: savedCtx || undefined };
  else if (/\/setup\/context$/.test(url)) resp = { ok: true, applied: !!body.apply, block: 'B', previews: { claude: { file: 'D:/lab/cal/CLAUDE.local.md', exists: false, before: '', after: 'a\nb\n', changed: true }, codex: { file: 'D:/lab/cal/AGENTS.md', exists: true, before: 'lab\n', after: 'lab\n\nB', changed: true, moves_from: 'D:/lab/cal/AGENTS.local.md' } }, writes: {} };
  else if (/\/api\/agent\/limits$/.test(url) && method === 'POST') {
    if (limitsRefuse) { code = 403; resp = { ok: false, refused: 'no_window_proof', error: limitsRefuse }; }
    else { limitsNow = Object.assign({}, limitsNow, { mode: body.mode, max_writes_per_plan: +body.max_writes_per_plan, human_recent_min: +body.human_recent_min, stop_by: body.stop_by, webhook_url: body.webhook_url, max_delta: JSON.parse(body.max_delta) }); resp = { ok: true, limits: limitsNow }; }
  }
  else if (/\/api\/agent\/limits$/.test(url)) resp = { ok: true, chip: 'chipA', limits: limitsNow, modes: ['auto', 'ask-writes', 'ask-all'] };
  else if (/\/setup\/test/.test(url)) resp = { ok: true, backend: 'claude', elapsed_s: 12.3, done: true, failed: false, answer: 'lab-J is open: 20 qubits.', tools: ['mcp__sm__sm_status'] };
  return Promise.resolve({ status: code, json: function () { return Promise.resolve(resp); } });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-setup.js'), 'utf8'), { filename: 'agent-setup.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));

(async () => {
  const A = window.AgentSetup;
  A.init();
  await tick(30);
  const body = document.getElementById('as-body');
  // docs/288: four numbered cards in reading order (+ the limits fold), not nine accordions
  ok(['as-connect', 'as-env', 'as-context', 'as-test-sec'].every((id, i, a) => { const el = document.getElementById(id); const prev = i && document.getElementById(a[i - 1]); return el && el.classList.contains('asx-card') && (!prev || (prev.compareDocumentPosition(el) & 4)); }),
     'four cards in order: connect, where runs live, the device, check it works');
  // 3b: the dry-run card -- from the payload, always open, wired to the Runner's settings route
  {
    const box = document.getElementById('as-dryrun-box');
    const card = document.getElementById('as-dryrun');
    ok(card && box && card !== box && card.contains(box), 'the row is #as-dryrun and the box #as-dryrun-box (two ids -- the first cut gave both the same one)');
    ok(box && box.type === 'checkbox' && box.checked === true, 'the dry-run box is CHECKED from global_simulate: true');
    ok(card && !card.closest('details:not([open])'), 'the switch is never folded away (a switch is not a checklist item)');
    ok(card.closest('#as-env'), 'it sits in "Where runs and notes live", beside the calibrations folder');
    ok(card.querySelector('.asx-dry-text').textContent === "Dry run — the agent's runs are simulated, nothing touches the OPX", 'ON says the runs are simulated and nothing touches the OPX');
    ok(box.getAttribute('onchange') === 'AgentSetup.dryRun(this)', 'the box is wired to AgentSetup.dryRun');
    // flip OFF -> POST {global_simulate:false} -> "Saved — dry run OFF"
    calls.length = 0;
    box.checked = false; A.dryRun(box);
    await tick(30);
    ok(calls[0] && calls[0].url === '/scheduler/settings' && calls[0].method === 'POST' && JSON.stringify(calls[0].body) === '{"global_simulate":false}', 'the change POSTs exactly {global_simulate:false} to /scheduler/settings');
    ok(/Saved — dry run OFF/.test(document.getElementById('as-dryrun-msg').textContent), 'the reply shows inline: ' + document.getElementById('as-dryrun-msg').textContent);
    ok(box.checked === false && box.disabled === false && A._state.data.global_simulate === false, 'the box and the payload copy follow the saved value');
    ok(card.querySelector('.asx-dry-text').textContent === "Live — the agent's runs use the OPX", 'the words follow the saved value without a re-render (OFF says the runs use the OPX)');
    // a running queue: the 409's words VERBATIM, the box back to what is persisted (re-read)
    settingsBusy = true; calls.length = 0;
    box.checked = true; A.dryRun(box);
    await tick(30);
    ok(document.getElementById('as-dryrun-msg').textContent === BUSY_ERR, 'the refusal is the server\'s error text verbatim');
    ok(box.checked === false && box.disabled === false, 'the box went back to the persisted value (OFF)');
    ok(calls.some(c => c.url === '/scheduler/settings' && c.method === 'GET'), 'the persisted value was re-read, not assumed');
    ok(!calls.some(c => /\/api\/agent\/setup$/.test(c.url)), 'no page reload / status re-fetch on a refusal');
    settingsBusy = false;
    // the server is gone mid-click: the fetch REJECTS -> the box is never left disabled, the failure is said, the value goes back
    settingsDown = true; calls.length = 0;
    box.checked = true; A.dryRun(box);
    await tick(30);
    ok(box.disabled === false, 'a rejected fetch does not leave the box disabled');
    ok(box.checked === false && A._state.data.global_simulate === false, 'a rejected fetch reverts to the last persisted value (the re-read rejected too)');
    ok(/Failed to fetch/.test(document.getElementById('as-dryrun-msg').textContent), 'the failure is said in the browser\'s own words');
    settingsDown = false;
    // back ON: the marker follows again
    box.checked = true; A.dryRun(box);
    await tick(30);
    ok(box.checked === true && card.querySelector('.asx-dry-text').textContent === "Dry run — the agent's runs are simulated, nothing touches the OPX", 'the words follow the saved value (ON)');
    // a re-render from a payload saying OFF: unchecked, marked ● (runs touch the OPX), still open
    status.global_simulate = false; A.load();
    await tick(30);
    const box2 = document.getElementById('as-dryrun-box'), card2 = document.getElementById('as-dryrun');
    ok(box2.checked === false && card2.querySelector('.asx-dry-text').textContent === "Live — the agent's runs use the OPX", 'OFF renders unchecked and says the runs use the OPX');
    status.global_simulate = true; A.load();
    await tick(30);
    ok(document.getElementById('as-dryrun-box').checked === true, 'ON renders checked again');
  }
  // docs/288: one tile per CLI -- what is missing is said in words, with ONE button
  const tileC = document.getElementById('as-connect-claude');
  ok(tileC && tileC.classList.contains('asx-tile-todo') && /Not connected/.test(tileC.textContent), 'an unconnected CLI says Not connected');
  ok(/\u25cb SM's tools/.test(tileC.textContent) && !!tileC.querySelector('button[onclick="AgentSetup.preview(\'claude\')"]'), 'says what is missing, with one Connect button');
  ok(/Not installed/.test((document.getElementById('as-connect-codex') || {}).textContent || '') && !document.querySelector('#as-connect-codex button'), 'a CLI not on PATH says Not installed and offers nothing to press');
  ok(document.getElementById('as-test') !== document.getElementById('as-test-sec') && document.getElementById('as-test-sec').contains(document.getElementById('as-test')), 'the test card and its result box are two elements (one id each)');
  ok(document.getElementById('as-jroot').value === 'D:/data/PJ/journal', 'the journal input starts with the suggested folder beside the data');
  // preview -> diff, no write
  calls.length = 0;
  A.preview('claude');
  await tick(30);
  ok(calls[0].url === '/api/agent/setup/connect' && calls[0].body.apply === undefined, 'preview posts without apply');
  const prev = document.getElementById('as-prev-claude');
  ok(prev.querySelectorAll('.as-line.as-add').length >= 1 && /"command": "py.exe"/.test(prev.textContent), 'the diff shows the added lines');
  ok(!!prev.querySelector('.ag-start') && /Connect Claude Code/.test(prev.querySelector('.ag-start').textContent), 'the write is a second click');
  ok(/add SM to its list of tools/.test(prev.textContent) && prev.querySelector('details .as-diff'), 'the confirm says what changes in words; the exact diff is one click away');
  // docs/288 walk: with the confirm open, the tile's own Connect + pin step aside (CSS hides
  // .asx-asking > .asx-tile-acts / .asx-opt) -- the confirm's "Connect Claude Code" is the one press
  ok(document.getElementById('as-connect-claude').classList.contains('asx-asking'), 'the confirm marks its tile as asking');
  A.cancel('claude');
  ok(!document.getElementById('as-connect-claude').classList.contains('asx-asking') && prev.innerHTML === '', 'Cancel clears the confirm and gives the tile its Connect back');
  A.preview('claude');
  await tick(30);
  // the diff engine
  const d = A.diffLines('a\nb\nc', 'a\nc\nd');
  ok(JSON.stringify(d) === JSON.stringify([['=', 'a'], ['-', 'b'], ['=', 'c'], ['+', 'd']]), 'line diff: ' + JSON.stringify(d));
  // the click writes and reloads
  calls.length = 0;
  A.connect('claude');
  await tick(30);
  ok(calls[0].body.apply === true && calls.some(c => /\/api\/agent\/setup$/.test(c.url)), 'connect applies then reloads the status');
  // the pin is an option OF Connect: a connected tile never shows a box SM did not read
  const claudeWas = status.claude;
  status.claude = Object.assign({}, claudeWas, { mcp: true, hooks: true });
  A.load();
  await tick(30);
  ok(/Connected/.test(document.getElementById('as-connect-claude').textContent) && !document.getElementById('as-pin-claude'), 'a connected tile shows no pin box');
  status.claude = claudeWas;
  A.load();
  await tick(30);
  // journal
  document.getElementById('as-jroot').value = 'D:/lab/journal';
  document.getElementById('as-jsays').checked = true;
  calls.length = 0;
  A.journal();
  await tick(30);
  ok(calls[0].url === '/api/agent/setup/journal' && calls[0].body.root === 'D:/lab/journal' && calls[0].body.claude_says === true, 'journal posts the folder and the says toggle');
  // context questions
  A.loadContext();
  await tick(30);
  const sel = document.querySelector('#as-ctx select[data-q="tunable"]');
  ok(sel && sel.value === 'mixed' && /\(detected\)/.test(sel.options[2].textContent), 'the detected answer is preselected and marked');
  sel.value = 'flux-tunable'; A.answer(sel);
  const ta = document.querySelector('#as-ctx textarea[data-q="notes"]'); ta.value = 'cooldown 9'; A.answer(ta);
  calls.length = 0;
  A.previewContext();
  await tick(30);
  ok(calls[0].body.answers.tunable === 'flux-tunable' && calls[0].body.answers.notes === 'cooldown 9' && calls[0].body.local === true && calls[0].body.targets.length === 2 && !calls[0].body.apply, 'the context preview carries the answers, local, both targets, no apply');
  ok(/CLAUDE\.local\.md/.test(document.getElementById('as-ctx-prev').textContent) && document.getElementById('as-ctx-prev').querySelector('.as-add'), 'the block diff shows');
  calls.length = 0;
  A.writeContext();
  await tick(30);
  ok(calls[0].body.apply === true, 'the write is the second click');
  // test
  calls.length = 0;
  /* docs/191 A04: the section collapses only when the status says the test is
     DONE, so a harness whose record is empty keeps it open for the wrong
     reason -- the first version of the pin below passed against a section that
     was never eligible to close. Report the success the way the server does. */
  status.record = status.record || {};
  status.record.tested = { claude: { ok: true, elapsed_s: 12.3 } };
  A.test('claude');
  await tick(30);
  ok(/answered in 12\.3 s/.test(document.getElementById('as-test').textContent) && /lab-J is open: 20 qubits\./.test(document.getElementById('as-test').textContent), 'the test shows the time and the answer verbatim');
  /* docs/191 A04: showing it is not the same as the user SEEING it. A
     successful test is what marks the section done, and `sec` collapses a done
     section -- so the answer arrived and the section shut over it in the same
     breath (measured in real Chrome: "asking claude one read-only question…",
     then a bare "✓ 6. Test" with a real 6.8 s answer inside). While there is a
     result on screen the section stays open. */
  const testSec = document.getElementById('as-test-sec');
  ok(!!testSec && testSec.contains(document.getElementById('as-test')) && /12\.3 s/.test(testSec.textContent),
     'A04: the answer stays on screen in its card after the status reload');
  ok(testSec.classList.contains('asx-card-done') && /\u2713/.test(testSec.querySelector('.asx-step').textContent),
     'A04: and the card is marked done');
  // a re-write starts from what the lab answered last time, never from the detected values alone
  savedCtx = { tunable: 'fixed-frequency', notes: 'Never retry hardware.' };
  S_reset();
  A.loadContext();
  await tick(30);
  const selT = document.querySelector('#as-ctx select[data-q="tunable"]');
  const taN = document.querySelector('#as-ctx textarea[data-q="notes"]');
  ok(selT && selT.value === 'fixed-frequency' && taN && taN.value === 'Never retry hardware.',
     'the form starts from the saved answers (a note is never dropped by a re-write)');
  savedCtx = null;
  // B-02: Codex reads AGENTS.md only -- the UI says so, and a block it never reads is not "written"
  A.loadContext();
  await tick(30);
  ok(/Codex: always AGENTS\.md/.test(document.getElementById('as-ctx').textContent), 'B-02: the context form says Codex always uses AGENTS.md');
  A.previewContext();
  await tick(30);
  ok(/takes SM's block out of D:\/lab\/cal\/AGENTS\.local\.md/.test(document.getElementById('as-ctx-prev').textContent), 'B-02: the preview names the unread file it moves the block out of');
  status.context = { 'claude:local': 'D:/lab/cal/CLAUDE.local.md' };
  status.context_unread = ['D:/lab/cal/AGENTS.local.md'];
  A.load();
  await tick(30);
  const ctxSec = document.getElementById('as-context');
  // docs/288: the section is a card (never folded); "done" is the card's class + its step mark
  ok(!!ctxSec && !ctxSec.closest('details:not([open])') && /Codex never reads/.test(ctxSec.textContent) && /AGENTS\.local\.md/.test(ctxSec.textContent) && !ctxSec.classList.contains('asx-card-done') && !/\u2713/.test(ctxSec.querySelector('.asx-step').textContent),
     'B-02: an unread AGENTS.local.md block keeps the card on screen, not done, and says why');
  status.context_unread = [];
  A.load();
  await tick(30);
  const ctxSec2 = document.getElementById('as-context');
  ok(!!ctxSec2 && !/Codex never reads/.test(ctxSec2.textContent) && ctxSec2.classList.contains('asx-card-done') && /\u2713/.test(ctxSec2.querySelector('.asx-step').textContent), 'B-02: nothing unread -> no warning, card done');
  // docs/252: the limits are the person's, edited here (no other surface could change them)
  ok(!document.getElementById('as-limits'), 'no chip open -> no Limits section');
  status.chip = 'chipA';
  A.load();
  await tick(40);
  const limSec = document.getElementById('as-limits');
  ok(!!limSec && /Safety limits for chipA/.test(limSec.querySelector('summary').textContent), 'a chip open -> Safety limits for <chip>');
  ok(document.getElementById('as-lim-mode').value === 'ask-writes' && document.getElementById('as-lim-maxw').value === '200'
     && document.getElementById('as-lim-recent').value === '30' && document.getElementById('as-lim-delta').value === '{"ramsey":2000000}',
     'the form starts from the saved limits');
  document.getElementById('as-lim-maxw').value = '50';
  document.getElementById('as-lim-hook').value = 'https://hooks.example/x';
  A.saveLimits();
  await tick(40);
  const post = calls.filter(c => /\/api\/agent\/limits$/.test(c.url) && c.method === 'POST').pop();
  ok(post && post.body.max_writes_per_plan === '50' && post.body.webhook_url === 'https://hooks.example/x'
     && post.body.mode === 'ask-writes' && post.body.max_delta === '{"ramsey":2000000}', 'Save posts every field as typed');
  ok(/Saved/.test(document.getElementById('as-lim-msg').textContent) && document.getElementById('as-lim-maxw').value === '50'
     && document.getElementById('as-limits').open, 'saved -> the section stays open and says so, over the saved values');
  limitsRefuse = 'changing the limits needs a press in the SM window. If SM was restarted, reload the page and press again.';
  document.getElementById('as-lim-recent').value = '0';
  A.saveLimits();
  await tick(40);
  ok(/Not saved: changing the limits needs a press in the SM window/.test(document.getElementById('as-lim-msg').textContent),
     "a refusal is said beside the button, in the server's words");
  limitsRefuse = null;
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
