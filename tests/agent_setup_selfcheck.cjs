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
  hook_command: '"py.exe" -m quam_state_manager.hook --backend claude', todo: ['connect_claude', 'allow', 'journal', 'context']
};
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body });
  let resp = { ok: true };
  if (/\/api\/agent\/setup$/.test(url)) resp = status;
  else if (/\/setup\/connect/.test(url) && !body.apply) resp = { ok: true, applied: false, previews: { mcp: { file: 'H/.claude.json', exists: true, before: null, after: { command: 'py.exe' }, changed: true }, hooks: { file: 'H/.claude/settings.json', exists: true, before: { PreToolUse: [] }, after: { PreToolUse: [{ matcher: 'Bash' }] }, changed: true } }, writes: {} };
  else if (/\/setup\/connect/.test(url)) resp = { ok: true, applied: true, writes: { mcp: { file: 'H/.claude.json', backup: 'H/.claude.json.sm-backup-1' } } };
  else if (/\/setup\/context$/.test(url) && (!opts || opts.method === 'GET')) resp = { ok: true, facts: { n_qubits: 20, n_pairs: 30, bias_modes: { opx: 9 }, nodes: ['05_power_rabi'] }, questions: [{ id: 'tunable', kind: 'choice', options: ['flux-tunable', 'fixed-frequency', 'mixed'], question: 'Are the qubits flux-tunable?', detected: 'mixed', why: 'x' }, { id: 'notes', kind: 'text', question: 'Anything else?', detected: '' }] };
  else if (/\/setup\/context$/.test(url)) resp = { ok: true, applied: !!body.apply, block: 'B', previews: { claude: { file: 'D:/lab/cal/CLAUDE.local.md', exists: false, before: '', after: 'a\nb\n', changed: true } }, writes: {} };
  else if (/\/setup\/test/.test(url)) resp = { ok: true, backend: 'claude', elapsed_s: 12.3, done: true, failed: false, answer: 'PJ_10082026 is open: 20 qubits.', tools: ['mcp__sm__sm_status'] };
  return Promise.resolve({ status: 200, json: function () { return Promise.resolve(resp); } });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-setup.js'), 'utf8'), { filename: 'agent-setup.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));

(async () => {
  const A = window.AgentSetup;
  A.init();
  await tick(30);
  const body = document.getElementById('as-body');
  ok(body.querySelectorAll('details.as-sec').length === 6, 'six sections');
  ok(document.getElementById('as-clis').open === false && document.getElementById('as-connect-claude').open === true, 'a done section is folded, an undone one open');
  ok(!document.getElementById('as-connect-codex'), 'no codex here: not asked');
  ok(/not registered as an MCP server/.test(document.getElementById('as-connect-claude').textContent), 'says what is missing');
  ok(document.getElementById('as-jroot').value === 'D:/data/PJ/journal', 'the journal input starts with the suggested folder beside the data');
  // preview -> diff, no write
  calls.length = 0;
  A.preview('claude');
  await tick(30);
  ok(calls[0].url === '/api/agent/setup/connect' && calls[0].body.apply === undefined, 'preview posts without apply');
  const prev = document.getElementById('as-prev-claude');
  ok(prev.querySelectorAll('.as-line.as-add').length >= 1 && /"command": "py.exe"/.test(prev.textContent), 'the diff shows the added lines');
  ok(!!prev.querySelector('.ag-start') && /Write these \(with backups\)/.test(prev.textContent), 'the write is a second click');
  // the diff engine
  const d = A.diffLines('a\nb\nc', 'a\nc\nd');
  ok(JSON.stringify(d) === JSON.stringify([['=', 'a'], ['-', 'b'], ['=', 'c'], ['+', 'd']]), 'line diff: ' + JSON.stringify(d));
  // the click writes and reloads
  calls.length = 0;
  A.connect('claude');
  await tick(30);
  ok(calls[0].body.apply === true && calls.some(c => /\/api\/agent\/setup$/.test(c.url)), 'connect applies then reloads the status');
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
  A.test('claude');
  await tick(30);
  ok(/answered in 12\.3 s/.test(document.getElementById('as-test').textContent) && /PJ_10082026 is open: 20 qubits\./.test(document.getElementById('as-test').textContent), 'the test shows the time and the answer verbatim');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
