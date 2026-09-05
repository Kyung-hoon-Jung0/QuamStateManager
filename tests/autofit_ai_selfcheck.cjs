/* docs/169 -- the AI-judge settings block on the Auto Calibrate page, driven
 * against the REAL autofit.js and the REAL template block.
 *
 * Why an executable pin: the first cut of the three handlers read the answer
 * as `d.ok` while fetchJSON answers `{status, body}` -- the settings never
 * loaded, Save said "not saved" over a save that had happened, and Test said
 * "failed: ?" over a probe that had succeeded. Only a real browser caught it.
 *
 * Pins:
 *  1. load: GET /autofit/ai fills the fields + shows only that provider's rows
 *  2. save: POST body is what the form holds (a blank key is OMITTED, never
 *     sent as ""), the status names the provider, the readiness chip is
 *     patched IN PLACE (no page re-pull), the key field is cleared
 *  3. a refused save shows the server's reason
 *  4. probe: the CLI's own words on failure, provider/model on success
 * Run: node tests/autofit_ai_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..', 'quam_state_manager', 'web');
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

// the real block, lifted out of the real template (no Jinja inside it)
const tpl = fs.readFileSync(path.join(ROOT, 'templates', '_autofit.html'), 'utf8');
const start = tpl.indexOf('<details class="autofit-profile autofit-ai"');
const end = tpl.indexOf('</details>', start) + '</details>'.length;
if (start < 0 || end < start) { console.error('FAIL: block not found in template'); process.exit(1); }
const block = tpl.slice(start, end);

const dom = new JSDOM('<!doctype html><html><body>'
  + '<div class="autofit-readiness"><span id="ai-ready-chip" class="autofit-ready-chip off">LLM audit: off</span></div>'
  + block + '</body></html>', { url: 'http://localhost/autofit', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.navigator = window.navigator; global.location = window.location;

// a fetch that answers by route and records what it was sent
const calls = [];
let answers = {};
global.fetch = function (url, opts) {
  calls.push({ url: url, opts: opts || {} });
  const a = answers[(opts && opts.method || 'GET') + ' ' + url] || { status: 404, body: {} };
  return Promise.resolve({ status: a.status, json: () => Promise.resolve(a.body) });
};
window.fetch = global.fetch;

vm.runInThisContext(fs.readFileSync(path.join(ROOT, 'static', 'autofit.js'), 'utf8'), { filename: 'autofit.js' });
const $ = (id) => document.getElementById(id);
const tick = () => new Promise(r => setTimeout(r, 10));
const rowsShown = () => Array.from(document.querySelectorAll('#autofit-ai [data-ai-for]'))
  .filter(e => !e.hidden).map(e => e.getAttribute('data-ai-for')).join('|');

(async () => {
  // 1. load
  answers = { 'GET /autofit/ai': { status: 200, body: { provider: 'anthropic', model: 'claude-x', base_url: '', claude_bin: '', has_api_key: true, claude_code: { available: false, detail: "'claude' is not on PATH" } } } };
  window.autofitInit();
  await tick();
  ok($('ai-provider').value === 'anthropic', 'load: provider select filled from the server');
  ok($('ai-model').value === 'claude-x', 'load: model filled');
  ok(/saved/.test($('ai-api-key').placeholder), 'load: a saved key is announced through the placeholder, never echoed');
  ok(!$('ai-api-key').closest('[data-ai-for]').hidden, 'load: the API-key row shows for anthropic');
  ok($('ai-claude-bin').closest('[data-ai-for]').hidden, 'load: the claude-executable row is hidden for anthropic');
  ok($('ai-status').textContent === '', 'load: no claude-not-found warning while the provider is not claude_code');

  // the user switches to Claude Code
  $('ai-provider').value = 'claude_code';
  window.autofitAiProviderChanged();
  ok($('ai-api-key').closest('[data-ai-for]').hidden && !$('ai-claude-bin').closest('[data-ai-for]').hidden,
     'switch: claude_code hides the key row and shows the executable row');
  ok(rowsShown().split('|').every(v => v.split(' ').indexOf('claude_code') >= 0), 'switch: every visible row is tagged for claude_code');

  // 2. save
  $('ai-api-key').value = '';
  answers = { 'POST /autofit/ai': { status: 200, body: { ok: true, provider: 'claude_code', claude_code: { available: true, detail: 'C:/claude.exe' } } } };
  calls.length = 0;
  window.autofitAiSave();
  await tick();
  const sent = JSON.parse(calls[0].opts.body);
  ok(calls[0].url === '/autofit/ai' && calls[0].opts.method === 'POST', 'save: POST /autofit/ai');
  ok(sent.provider === 'claude_code' && !('api_key' in sent), 'save: body carries the provider and OMITS a blank key');
  ok($('ai-status').textContent === 'saved (claude_code)', 'save: status names the provider, got ' + JSON.stringify($('ai-status').textContent));
  const chip = $('ai-ready-chip');
  ok(chip.textContent === 'LLM audit: claude_code', 'save: the readiness chip is patched in place');
  ok(chip.classList.contains('ok') && !chip.classList.contains('off'), 'save: the chip turns ok');
  ok(calls.length === 1, 'save: no page re-pull (one request, the POST)');
  ok(document.getElementById('autofit-ai') && document.getElementById('autofit-ai').open !== undefined, 'save: the block is still the same element');

  // a typed key travels once, then the field is cleared
  $('ai-api-key').value = 'sk-typed';
  calls.length = 0;
  window.autofitAiSave();
  await tick();
  ok(JSON.parse(calls[0].opts.body).api_key === 'sk-typed', 'save: a typed key is sent');
  ok($('ai-api-key').value === '', 'save: and the field is cleared after');

  // save to off flips the chip back
  $('ai-provider').value = 'off';
  answers = { 'POST /autofit/ai': { status: 200, body: { ok: true, provider: 'off', claude_code: { available: true } } } };
  window.autofitAiSave();
  await tick();
  ok(chip.textContent === 'LLM audit: off' && chip.classList.contains('off') && !chip.classList.contains('ok'), 'save: off turns the chip off');

  // claude_code saved on a machine without the CLI: the status says so
  $('ai-provider').value = 'claude_code';
  answers = { 'POST /autofit/ai': { status: 200, body: { ok: true, provider: 'claude_code', claude_code: { available: false, detail: "'claude' is not on PATH -- install Claude Code" } } } };
  window.autofitAiSave();
  await tick();
  ok(/claude not found/.test($('ai-status').textContent) && /not on PATH/.test($('ai-status').textContent), 'save: a missing CLI is named right after saving');

  // 3. a refused save
  answers = { 'POST /autofit/ai': { status: 400, body: { ok: false, error: "unknown provider 'gemini'" } } };
  window.autofitAiSave();
  await tick();
  ok(/unknown provider 'gemini'/.test($('ai-status').textContent), 'refused save: the server reason is shown, got ' + JSON.stringify($('ai-status').textContent));

  // 4. probe
  answers = { 'POST /autofit/ai/probe': { status: 502, body: { ok: false, error: 'claude: Not logged in \u00b7 Please run /login' } } };
  window.autofitAiProbe();
  await tick();
  ok(/Not logged in/.test($('ai-status').textContent), "probe: the CLI's own words on failure, got " + JSON.stringify($('ai-status').textContent));
  answers = { 'POST /autofit/ai/probe': { status: 200, body: { ok: true, provider: 'claude_code', model: 'claude-haiku-4-5-20251001', sample: '{}' } } };
  window.autofitAiProbe();
  await tick();
  ok(/works/.test($('ai-status').textContent) && /claude_code/.test($('ai-status').textContent) && /haiku/.test($('ai-status').textContent),
     'probe: success names provider and model');
  answers = { 'POST /autofit/ai/probe': { status: 409, body: { ok: false, error: 'provider is off or unconfigured' } } };
  window.autofitAiProbe();
  await tick();
  ok(/unconfigured/.test($('ai-status').textContent), 'probe: an unconfigured provider is said, not "?"');

  console.log(fails ? ('FAILED ' + fails) : ('all checks passed (' + passes + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
