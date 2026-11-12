/* agents ver180 D-16..D-21 -- Investigate by agent in the REAL agent-setup.js under jsdom.
 * Pins: "not sure" on every choice; flux_source pre-filled "SM detected" with its counts;
 * every proposal starts "proposed" and nothing enters the answers before Accept/Edit;
 * an unsure answer opens a popup with "not sure"; an invented question is text only;
 * Write carries accepted answers + their evidence and never an unreviewed proposal;
 * a result for another project reads as stale.
 * Run: node tests/agent_investigate_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="as-body"></div></body></html>', { url: 'http://localhost/', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.localStorage = window.localStorage;
const status = {
  ok: true, clis: { claude: { found: true }, codex: { found: true } },
  claude: { mcp: true, hooks: true, allow: true }, codex: { mcp: true }, context: {}, record: {}, calibrations_folder: 'D:/lab/cal',
  journal: { root: 'D:/j', configured: true }, global_simulate: true,
  paths: { project: 'P1', calibrations: { folder: 'D:/lab/cal', source: 'project', entry: { source: 'own' } }, checks: [] }
};
const QS = [
  { id: 'tunable', kind: 'choice', options: ['flux-tunable', 'fixed-frequency', 'mixed', 'unknown'], question: 'Tunable?', detected: 'mixed' },
  { id: 'flux_source', kind: 'choice', options: ['QDAC', 'LF-FEM', 'both (bias tee)', 'none (fixed-frequency)', 'mixed', 'unknown'], question: 'Flux bias?', detected: 'LF-FEM', detected_counts: { opx: 4 } },
  { id: 'purcell', kind: 'choice', options: ['yes', 'no', 'unknown'], question: 'Purcell?', detected: 'unknown' },
  { id: 'squid', kind: 'choice', options: ['symmetric', 'asymmetric', 'no SQUID', 'unknown'], question: 'SQUID?', detected: 'unknown' },
  { id: 'never_touch', kind: 'list', question: 'Never touch?', detected: [], tokens: ['q1', 'q2', 'qubits'] },
  { id: 'notes', kind: 'text', question: 'Notes?', detected: '' }
];
let inv = { ok: true, kind: 'investigate', backend: 'claude', done: true, failed: false, elapsed_s: 9, at: 5, current: true,
  answers: [
    { id: 'purcell', value: 'yes', agent_confidence: 'sure', status: 'proposed', evidence: [{ ok: true, kind: 'file', ref: 'D:/lab/cal/readme.md', line: 4, quote: 'Purcell filter installed' }, { ok: false, kind: 'file', ref: 'D:/lab/cal/other.md', line: 9, quote: 'a filter line', why: 'the quote is not at the cited line' }] },
    { id: 'tunable', value: 'flux-tunable', agent_confidence: 'sure', status: 'proposed', evidence: [{ ok: true, kind: 'state_key', ref: 'qubits.q1.z', quote: 'x' }] },
    { id: 'squid', value: null, agent_confidence: 'unsure', agent_leans: 'symmetric', status: 'proposed', evidence: [{ ok: false, kind: 'file', ref: null, quote: null, why: 'outside the folders SM lets an agent cite' }] },
    { id: 'notes', value: 'cool down weekly', agent_confidence: 'sure', status: 'proposed', evidence: [{ ok: true, kind: 'file', ref: 'D:/lab/cal/n.md', line: 1, quote: 'weekly' }] }
  ],
  asks: ['squid'], also_asked: ['What is the fridge model?'], dropped_ids: 1, dropped_values: 0 };
let invStored = null;
const calls = [];
global.fetch = window.fetch = function (url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  const method = (opts && opts.method) || 'GET';
  calls.push({ url, method, body });
  let resp = { ok: true };
  if (/\/api\/agent\/setup$/.test(url)) resp = status;
  else if (/\/setup\/asks$/.test(url)) resp = { ok: true, running: null, confirm: null, investigate: invStored };
  else if (/\/setup\/investigate$/.test(url)) { resp = inv; invStored = inv; }
  else if (/\/setup\/context$/.test(url) && method === 'GET') resp = { ok: true, facts: { n_qubits: 4, n_pairs: 3, bias_modes: { opx: 4 } }, questions: QS, saved: {},
    never_touch_in_force: { tokens: ['qA1'], sources: [{ from: 'context_block', file: 'D:/lab/cal/CLAUDE.local.md', tokens: ['qA1'] }] } };
  else if (/\/setup\/context$/.test(url)) resp = { ok: true, previews: {}, writes: {} };
  else if (/\/api\/agent\/limits$/.test(url)) resp = { ok: true };
  return Promise.resolve({ status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(resp))) });
};
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'agent-setup.js'), 'utf8'), { filename: 'agent-setup.js' });
const tick = (ms) => new Promise(r => setTimeout(r, ms || 15));
const $ = (s) => document.querySelector(s);

(async () => {
  const A = window.AgentSetup;
  A.init(); await tick(40);
  A.loadContext(); await tick(40);
  // D-18: "not sure" on every choice
  const sels = Array.from(document.querySelectorAll('#as-ctx select'));
  ok(sels.length === 4 && sels.every(s => Array.from(s.options).some(o => o.value === 'unknown' && /^not sure/.test(o.textContent))), 'D-18: every choice question offers "not sure" (saved as unknown)');
  // D-19: flux source pre-filled from what SM detected, the counts beside it
  const fs_ = $('#as-ctx select[data-q="flux_source"]');
  ok(fs_ && fs_.value === '' && /SM detected/.test(Array.from(fs_.options).filter(o => o.value === 'LF-FEM')[0].textContent) && /opx ×4/.test($('[data-counts]').textContent),
     'D-19 / review r5 P2: flux_source marks "SM detected" with the counts, but nothing is chosen for the person');
  // D-20: never_touch is free text with the chip's names offered
  const nt = $('#as-ctx input[data-q="never_touch"]');
  ok(nt && nt.getAttribute('list') === 'as-nt-tokens' && document.querySelectorAll('#as-nt-tokens option').length === 3, 'D-20: never_touch offers the chip names as autocomplete');
  ok(/In force now — the lab-context file D:\/lab\/cal\/CLAUDE\.local\.md: qA1/.test(($('#as-nt-force') || {}).textContent || ''), 'review r6 P2: the box says which list is in force and from where');
  // investigate
  A.investigate('codex');
  ok(Array.from(document.querySelectorAll('#as-investigate button')).every(b => b.disabled) || /asking Codex/.test($('#as-inv').textContent), 'D-23: one ask at a time');
  await tick(40);
  ok(calls.some(c => /\/setup\/investigate$/.test(c.url) && c.body.backend === 'codex'), 'Investigate posts one read-only ask');
  status.codex = { mcp: false }; A.load(); await tick(40); A.loadContext(); await tick(40);
  const cx = $('#as-investigate button[data-investigate="codex"]');
  ok(cx && cx.disabled && /not connected/.test(cx.title), 'review r2 P2-7: Investigate by Codex is disabled with the reason while not connected');
  status.codex = { mcp: true }; A.load(); await tick(40); A.loadContext(); await tick(40);
  const props = Array.from(document.querySelectorAll('[data-prop]'));
  ok(props.length === 4 && props.every(p => p.getAttribute('data-status') === 'proposed'), 'D-21: every answer starts "proposed", sure or not');
  ok(A._state.answers.purcell === undefined && A._state.answers.tunable === undefined, 'D-21: nothing enters the answers before Accept/Edit (not even what SM detected)');
  ok(/Purcell filter installed/.test($('[data-prop="purcell"]').textContent) && /readme\.md:4/.test($('[data-prop="purcell"]').textContent), 'D-17/R-7: the quote is shown beside the answer');
  ok(!/Accept/.test($('[data-prop="squid"]').textContent), 'D-18: an unsure answer has no value to accept');
  // decision D-25: the agent's confidence is labelled as the agent's; SM's facts are neutral
  ok(/agent says: sure/.test($('[data-prop="purcell"] [data-agent-says]').textContent), 'D-25: the card says "agent says: sure", not a verdict');
  ok(!document.querySelector('[data-prop] .asx-ok, [data-prop] .asx-no') && document.querySelector('[data-prop="purcell"] [data-fact="found"]')
     && /SM found the quote at/.test($('[data-prop="purcell"]').textContent), 'D-25: SM states what it checked, with no green or red verdict');
  { const nf = $('[data-prop="purcell"] [data-fact="not-found"]');
    ok(nf && /SM: the quote is not at the cited line/.test(nf.textContent) && !/SM found the quote/.test(nf.textContent),
       'review r5: a citation SM could not find is never rendered as found'); }
  ok(/leans towards symmetric/.test($('[data-prop="squid"]').textContent), 'review r4 P2-6: an unsure card says what the agent leaned towards');
  // the popup: only for SM-sent ids, with "not sure"
  const pop = $('#as-popup');
  ok(pop && pop.getAttribute('data-q') === 'squid' && pop.classList.contains('ui-dialog') && Array.from(pop.querySelectorAll('button[data-pick]')).some(b => b.textContent === 'not sure'), 'D-18: the unsure question opens a popup with its options and "not sure"');
  ok(/What is the fridge model\?/.test($('#as-also-asked').textContent) && document.querySelectorAll('#as-popup').length === 1, 'D-16: an invented question is plain text, never a popup');
  ok(/agent leans: symmetric \(unsure\)/.test(($('#as-popup [data-popup-facts]') || {}).textContent || ''), 'review r4 P2-6: the popup says what the agent leaned towards');
  ok(!document.querySelector('#as-popup button[data-pick].ui-segmented-on'), 'review r4 P2-6: nothing is pre-selected in the popup');
  ok(/AgentSetup\.popupPick\('squid'/.test(pop.querySelector('button[data-pick="unknown"]').getAttribute('onclick')), 'the popup button is wired to popupPick for that question');
  A.popupPick('squid', 'unknown'); await tick(10);
  ok(A._state.answers.squid === 'unknown' && !$('#as-popup'), 'D-18: "not sure" is saved as unknown and the popup closes');
  // accept two, edit one, leave one unreviewed
  A.review('purcell', 'accepted');
  A.review('tunable', 'accepted');
  A.review('notes', 'accepted');
  ok(A._state.evidence.notes && A._state.evidence.notes.length === 1, 'Accept keeps the verified citation');
  A.review('notes', 'edited');
  ok($('[data-prop="purcell"]').getAttribute('data-status') === 'accepted' && $('#as-ctx select[data-q="purcell"]').value === 'yes', 'Accept fills the form');
  // an unreviewed proposal: add one more by hand to the result so one stays untouched
  ok(A._state.answers.notes === 'cool down weekly' && !A._state.evidence.notes, 'Edit takes the value but drops the agent citation');
  A.popupPick('squid', 'asymmetric');
  ok(/asymmetric · your answer/.test($('[data-prop="squid"]').textContent), 'review r2 P2-8: an edited card shows the value the person chose');
  A.review('tunable', 'rejected');
  ok(!A._state.evidence.tunable, 'Reject drops the citation');
  calls.length = 0;
  A.previewContext(); await tick(30);
  const post = calls.find(c => /\/setup\/context$/.test(c.url) && c.method === 'POST');
  ok(post && post.body.answers.purcell === 'yes' && post.body.evidence.purcell && post.body.evidence.purcell[0].ref === 'D:/lab/cal/readme.md'
     && !post.body.evidence.tunable && !post.body.evidence.notes, 'D-21: Write carries accepted answers with their evidence only');
  ok(post.body.answers.tunable === 'flux-tunable', 'a rejected proposal keeps what the form already held (accept had filled it)');
  ok(!post.body.apply, 'nothing is saved before the preview -> Write');
  // never_touch typed as a list
  nt.value = 'q1, qubits.q2.xy'; A.answer(nt);
  ok(JSON.stringify(A._state.answers.never_touch) === '["q1","qubits.q2.xy"]', 'D-20: never_touch is stored as a list');
  inv.answers.push({ id: 'never_touch', value: null, confidence: 'unsure', status: 'proposed', evidence: [] });
  A._state.asks.investigate = inv; A._state.review = {}; A.loadContext(); await tick(40);
  const nt2 = $('#as-ctx input[data-q="never_touch"]'); nt2.value = 'qA3'; A.answer(nt2); await tick(10);
  ok(/qA3 · your answer/.test($('[data-prop="never_touch"]').textContent), 'review r3 P2-7: the never-touch card follows what the person types');
  // D-8: a refocus with nothing moved never wipes the form; a project change re-reads
  window.dispatchEvent(new window.Event('focus')); await tick(40);
  ok(!!$('#as-ctx select[data-q="purcell"]'), 'D-8: a plain refocus keeps the questions on screen (measured wipe in Chrome)');
  let reloaded = 0; A._state.reloadPage = function () { reloaded++; A.load(); };
  status.paths = Object.assign({}, status.paths, { project: 'P2' });
  window.dispatchEvent(new window.Event('focus')); await tick(60);
  ok(reloaded === 1 && !$('#as-ctx select[data-q="purcell"]') && JSON.stringify(A._state.answers) === '{}', 'D-8 / review r3 P2-8: a project change reloads the page (sidebar included), answers not carried over');
  status.paths = Object.assign({}, status.paths, { project: 'P1' });
  // D-23: a result for another project is stale and offers no review
  invStored = Object.assign({}, inv, { current: false, at: 6, key: { project: 'P0' } });
  A.load(); await tick(40); A.loadContext(); await tick(40);
  ok(/Result for project P0/.test($('#as-inv').textContent) && !document.querySelector('[data-prop]') && !$('#as-popup'), 'D-23: a stale result shows as such, with nothing to accept');
  console.log(`\n${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
