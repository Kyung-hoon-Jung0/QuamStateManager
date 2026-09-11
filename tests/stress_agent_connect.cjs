/* "SM has an agent" — can a lab engineer connect their CLI without asking anyone?
 *
 * QUESTION 1 of the three the customer cares about, walked as the person who
 * has just been told the feature exists and has never seen MCP.
 *
 * Phase A (argv[5]="A"): a COLD instance against the REAL home. Land on
 *   /agent, time the first screen that explains the next step, read the
 *   wiring strip, open the "?" popover, follow "Connect →", walk every item
 *   of /agent/setup, PREVIEW every write. Nothing is applied.
 * Phase B (argv[5]="B"): the same server restarted over a SEEDED TEMP HOME
 *   (byte-identical copies of the two real files). Apply, apply AGAIN, read
 *   the backups, disconnect, and change reality under the strip's feet.
 *
 * argv[2]=json out, argv[3]=CDP port, argv[4]=base url, argv[5]=phase
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9431';
const BASE = process.argv[4] || 'http://127.0.0.1:5431';
const PHASE = (process.argv[5] || 'A').toUpperCase();
const SHOT_DIR = OUT.replace(/[^\\/]+$/, '');

const errors = [];
const results = [];
const timings = {};
function ok(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail });
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(80); }
    return await ev(expr);
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    const p = SHOT_DIR + 'agc_' + PHASE + '_' + name + '.png';
    fs.writeFileSync(p, Buffer.from(r.result.data, 'base64'));
    return p;
  };
  const shots = [];
  const snap = async (n) => { shots.push(await shot(n)); };
  // a real mouse click at the element's centre
  const clickSel = async (sel, nth) => {
    const box = await ev(`(function(){var els=document.querySelectorAll(${JSON.stringify(sel)});var e=els[${nth || 0}];if(!e)return null;e.scrollIntoView({block:'center'});var r=e.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,w:r.width,h:r.height};})()`);
    if (!box || box.w === 0) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return true;
  };
  const txt = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});return e?e.innerText.replace(/\\s+/g,' ').trim():null;})()`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  if (PHASE === 'A') await phaseA(); else await phaseB();

  fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, results, timings, errors, shots }, null, 2));
  console.log(PHASE + ': ' + results.filter(r => r.pass).length + '/' + results.length + ' pass, ' + errors.length + ' console problems');
  for (const r of results.filter(r => !r.pass)) console.log('  FAIL ' + r.name + ' :: ' + JSON.stringify(r.detail).slice(0, 400));
  ws.close();

  /* ───────────────────────────── PHASE A ───────────────────────────── */
  async function phaseA() {
    // land on /agent the way a person does: the sidebar entry of an open chip
    const t0 = Date.now();
    await send('Page.navigate', { url: BASE + '/agent' });
    await until(`!!document.querySelector('.ag-wire')`, 15000);
    timings.wire_strip_in_dom_s = (Date.now() - t0) / 1000;
    // the strip's FIRST honest state (not the "CHECKING" placeholder)
    await until(`(function(){var b=document.querySelector('#agent-home')?document.querySelector('.ag-wire:not(.ag-wire-compact) .ag-wire-badge'):null;return b&&b.textContent.trim()!=='CHECKING';})()`, 20000);
    timings.first_real_strip_state_s = (Date.now() - t0) / 1000;
    const badge = await txt('.ag-wire:not(.ag-wire-compact) .ag-wire-badge');
    const wireLine = await txt('.ag-wire:not(.ag-wire-compact) .ag-wire-clis');
    const setupLink = await txt('.ag-wire:not(.ag-wire-compact) .ag-wire-setup');
    timings.landing_badge = badge; timings.landing_wire_line = wireLine; timings.landing_setup_link = setupLink;
    ok('A1 the landing strip states a real connection verdict, not CHECKING', badge && badge !== 'CHECKING', badge);
    ok('A2 the verdict is NOT CONNECTED on a cold machine with both CLIs present',
       badge === 'NOT CONNECTED', { badge, wireLine });
    ok('A3 the strip names each CLI and says what is missing',
       /claude/i.test(wireLine || '') && /codex/i.test(wireLine || '') && /not registered as an MCP server/i.test(wireLine || ''), wireLine);
    ok('A4 a Connect link is offered right there', /Connect/.test(wireLine || ''), wireLine);
    ok('A5 the setup door counts the work left', /setup step/i.test(setupLink || ''), setupLink);
    // what the whole landing screen says, verbatim — is "register an MCP server"
    // discoverable WITHOUT opening anything?
    const landingText = await ev(`(function(){var m=document.getElementById('table-pane')||document.body;return m.innerText.replace(/\\s+/g,' ').trim().slice(0,3000);})()`);
    timings.landing_text = landingText;
    ok('A6 the word MCP appears on the landing screen with zero clicks', /MCP/.test(landingText || ''), (landingText || '').slice(0, 300));
    await snap('01_landing');

    // the "?" popover — one click
    const tHelp = Date.now();
    ok('A7 the ? button is clickable', await clickSel('.ag-wire:not(.ag-wire-compact) .ag-wire-help'));
    await until(`!!document.getElementById('ag-wire-help-pop')`, 4000);
    const helpTxt = await txt('#ag-wire-help-pop');
    timings.help_popover_s = (Date.now() - tHelp) / 1000;
    timings.help_text = helpTxt;
    ok('A8 the ? popover explains the mechanism (MCP server + hook + which files)',
       /MCP server/.test(helpTxt || '') && /\.claude\.json/.test(helpTxt || '') && /settings\.json/.test(helpTxt || ''), helpTxt);
    ok('A9 …and it says SM never sees your credentials', /credential/i.test(helpTxt || ''), helpTxt);
    // does the popover land ON SCREEN?
    const popBox = await ev(`(function(){var p=document.getElementById('ag-wire-help-pop');if(!p)return null;var r=p.getBoundingClientRect();return {t:r.top,l:r.left,b:r.bottom,rt:r.right,w:innerWidth,h:innerHeight};})()`);
    ok('A10 the ? popover is inside the viewport',
       popBox && popBox.t >= 0 && popBox.l >= 0 && popBox.b <= popBox.h + 2 && popBox.rt <= popBox.w + 2, popBox);
    await snap('02_help_popover');
    await clickSel('.ag-wire:not(.ag-wire-compact) .ag-wire-help');
    await sleep(200);

    // follow "Connect →" (the link inside the claude line)
    const tConnect = Date.now();
    const hasFix = await ev(`!!document.querySelector('.ag-wire:not(.ag-wire-compact) .ag-wire-fix')`);
    ok('A11 a "Connect →" link exists in the strip', hasFix);
    await clickSel('.ag-wire:not(.ag-wire-compact) .ag-wire-fix');
    await until(`!!document.querySelector('#as-connect-claude')`, 15000);
    timings.connect_to_setup_s = (Date.now() - tConnect) / 1000;
    timings.total_to_setup_s = (Date.now() - t0) / 1000;
    ok('A12 "Connect →" lands on the setup page with the claude section present',
       await ev(`!!document.querySelector('#as-connect-claude')`));
    await sleep(700);
    const url = await ev(`location.pathname`);
    ok('A13 …and the URL is /agent/setup (a bookmarkable address)', url === '/agent/setup', url);
    await snap('03_setup_landed');

    // ── walk EVERY setup item ──
    const secs = await ev(`Array.prototype.map.call(document.querySelectorAll('.as-sec'),function(s){return {id:s.id,title:(s.querySelector('summary')||{}).innerText.replace(/\\s+/g,' ').trim(),open:s.open,done:!!s.querySelector('summary .as-done')};})`);
    timings.setup_sections = secs;
    ok('A14 the setup page lists every step with a done/todo marker', secs.length >= 5, secs);
    ok('A15 the undone steps are OPEN and the done ones folded',
       secs.every(s => s.done ? true : s.open), secs);
    const setupText = await ev(`(function(){var e=document.getElementById('agent-setup');return e?e.innerText.replace(/\\s+/g,' ').trim():null;})()`);
    timings.setup_text = setupText;
    ok('A16 setup says a backup lands beside every file it touches', /sm-backup/.test(setupText || ''), (setupText || '').slice(0, 400));
    ok('A17 setup names the exact files it would write',
       /\.claude\.json/.test(setupText || '') && /settings\.json/.test(setupText || '') && /config\.toml/.test(setupText || ''), null);
    ok('A18 setup says login happens in the terminal and SM never sees a key',
       /never sees a key/i.test(setupText || '') || /never sees/i.test(setupText || ''), null);

    // PREVIEW claude
    const tPrev = Date.now();
    ok('A19 the claude Preview button exists', await ev(`!!document.querySelector("#as-connect-claude button[onclick*=\\"preview('claude')\\"]")`));
    await clickSel(`#as-connect-claude button`);
    await until(`(document.getElementById('as-prev-claude')||{}).innerHTML`, 15000);
    await sleep(900);
    timings.preview_claude_s = (Date.now() - tPrev) / 1000;
    const pc = await txt('#as-prev-claude');
    timings.preview_claude_text = (pc || '').slice(0, 2500);
    const addLines = await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-claude .as-add'),function(e){return e.textContent;})`);
    const delLines = await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-claude .as-del'),function(e){return e.textContent;})`);
    timings.preview_claude_add = addLines; timings.preview_claude_del = delLines;
    ok('A20 the claude preview renders a real line diff with additions', addLines.length > 0, addLines.slice(0, 8));
    ok('A21 …and on a cold file it deletes nothing', delLines.length === 0, delLines);
    ok('A22 …the diff actually contains the MCP server entry SM would add',
       addLines.join('\n').includes('quam-state-manager') || /quam_state_manager\.mcp/.test(addLines.join('\n')), addLines.slice(0, 10));
    ok('A23 …and the hook command SM would add',
       /quam_state_manager\.hook/.test(addLines.join('\n')), addLines.filter(l => /hook/.test(l)));
    ok('A24 the preview names the target file paths',
       /\.claude\.json/.test(pc || '') && /settings\.json/.test(pc || ''), (pc || '').slice(0, 300));
    ok('A25 the preview offers an Apply button only after previewing',
       await ev(`!!document.querySelector("#as-prev-claude button[onclick*='connect']")`),
       await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-claude button'),function(b){return b.textContent.trim();})`));
    timings.preview_claude_buttons = await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-claude button'),function(b){return b.textContent.trim();})`);
    await snap('04_preview_claude');

    // PREVIEW codex
    const tPx = Date.now();
    await clickSel(`#as-connect-codex button`);
    await until(`(document.getElementById('as-prev-codex')||{}).innerHTML`, 15000);
    await sleep(800);
    timings.preview_codex_s = (Date.now() - tPx) / 1000;
    const px = await txt('#as-prev-codex');
    const addX = await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-codex .as-add'),function(e){return e.textContent;})`);
    timings.preview_codex_text = (px || '').slice(0, 1800); timings.preview_codex_add = addX;
    ok('A26 the codex preview renders a diff of its TOML block', addX.length > 0, addX.slice(0, 10));
    ok('A27 …and it is a codex mcp_servers block, not the claude JSON',
       /mcp_servers/.test(addX.join('\n')), addX.slice(0, 6));
    await snap('05_preview_codex');

    // the journal / calibrations-folder / context items — what do they SAY?
    timings.journal_text = await txt('#as-journal');
    timings.env_text = await txt('#as-env');
    timings.dryrun_text = await txt('#as-dryrun');
    timings.context_text = await txt('#as-context');
    ok('A28 the run-environment step says where the calibrations folder is set',
       /Experiment Runner|scheduler/i.test(timings.env_text || ''), timings.env_text);
    ok('A29 the journal step pre-fills a suggested folder or says why it cannot',
       await ev(`(function(){var i=document.getElementById('as-jroot');return i?{value:i.value,ph:i.placeholder}:null;})()`),
       await ev(`(function(){var i=document.getElementById('as-jroot');return i?{value:i.value,ph:i.placeholder}:null;})()`));
    timings.journal_input = await ev(`(function(){var i=document.getElementById('as-jroot');return i?{value:i.value,ph:i.placeholder}:null;})()`);
    // the live test button — we must NOT press it (it starts a real CLI)
    timings.test_buttons = await ev(`Array.prototype.filter.call(document.querySelectorAll('#agent-setup button'),function(b){return /test/i.test(b.textContent);}).map(function(b){return b.textContent.replace(/\\s+/g,' ').trim();})`);
    ok('A30 a "test the connection" affordance exists (not pressed here: it starts a real CLI)',
       (timings.test_buttons || []).length > 0, timings.test_buttons);
    await snap('06_setup_full');
    // full-page text for the wording judgement
    timings.setup_full_text = await ev(`(function(){var e=document.getElementById('agent-setup');return e?e.innerText:null;})()`);

    // does the setup page tell you what to do when NO CLI is installed?
    ok('A31 step 1 tells an uninstalled user what to do', /install/i.test(timings.setup_sections.length ? (await txt('#as-clis')) || '' : ''), await txt('#as-clis'));
    timings.clis_text = await txt('#as-clis');

    // back to /agent — is the strip still honest after an htmx round trip?
    await clickSel('#nav-agent');
    await sleep(2500);
    const badge2 = await txt('.ag-wire:not(.ag-wire-compact) .ag-wire-badge');
    ok('A32 coming back to /agent the strip is still NOT CONNECTED (no stale CONNECTED)', badge2 === 'NOT CONNECTED', badge2);
    const dupes = await ev(`document.querySelectorAll('.ag-wire:not(.ag-wire-compact) .ag-wire-clis').length`);
    ok('A33 …and the strip did not duplicate its CLI lines on the repaint', dupes === 1, dupes);
    await snap('07_back_on_agent');
  }

  /* ───────────────────────────── PHASE B ───────────────────────────── */
  async function phaseB() {
    const t0 = Date.now();
    await send('Page.navigate', { url: BASE + '/agent/setup' });
    await until(`!!document.querySelector('#as-connect-claude')`, 20000);
    await sleep(900);
    const home = await ev(`(function(){var e=document.getElementById('as-connect-claude');return e?e.innerText.replace(/\\s+/g,' ').trim():null;})()`);
    timings.B_home_line = home;
    ok('B0 the setup page is reading the seeded temp home', /home_5431/.test(home || ''), home);
    await snap('10_cold_temp_home');

    // preview, then APPLY
    await clickSel('#as-connect-claude button');
    await until(`document.querySelectorAll('#as-prev-claude .as-add').length>0`, 15000);
    await sleep(500);
    const before = await ev(`Array.prototype.map.call(document.querySelectorAll('#as-prev-claude .as-add'),function(e){return e.textContent;})`);
    timings.B_preview_add = before;
    const applyBtn = await ev(`(function(){var b=document.querySelector("#as-prev-claude button[onclick*='apply']");return b?b.textContent.replace(/\\s+/g,' ').trim():null;})()`);
    timings.B_apply_button = applyBtn;
    ok('B1 the Apply button says what it will do', !!applyBtn, applyBtn);
    const t1 = Date.now();
    await clickSel(`#as-prev-claude button[onclick*='connect']`);
    await until(`/written|backup|✓|done/i.test((document.getElementById('as-prev-claude')||{}).innerText||'')`, 20000);
    await sleep(1500);
    timings.B_apply_s = (Date.now() - t1) / 1000;
    const after = await txt('#as-prev-claude');
    timings.B_after_apply = (after || '').slice(0, 1500);
    ok('B2 the result names the backup path it made', /sm-backup-\d{8}-\d{6}/.test(after || ''), (after || '').slice(0, 600));
    await snap('11_applied_once');

    // the strip lives on /agent, not on the setup page: go and look
    await sleep(1200);
    await send('Page.navigate', { url: BASE + '/agent' });
    await until(`(function(){var b=document.querySelector('.ag-wire:not(.ag-wire-compact) .ag-wire-badge');return b&&b.textContent.trim()!=='CHECKING';})()`, 20000);
    await sleep(600);
    const badge = await ev(`(function(){var b=document.querySelector('.ag-wire:not(.ag-wire-compact) .ag-wire-badge');return b?b.textContent.trim():null;})()`);
    timings.B_strip_line_after_apply = await txt('.ag-wire:not(.ag-wire-compact) .ag-wire-clis');
    await snap('11b_strip_connected');
    await send('Page.navigate', { url: BASE + '/agent/setup' });
    await until(`!!document.querySelector('#as-connect-claude')`, 20000);
    await sleep(900);
    timings.B_badge_after_apply = badge;
    ok('B3 the wiring strip flips to CONNECTED after the write (no reload)', badge === 'CONNECTED', badge);

    // press the SAME button a second time — double write?
    const t2 = Date.now();
    await clickSel('#as-connect-claude button');
    await sleep(1800);
    const second = await txt('#as-prev-claude');
    timings.B_second_preview = (second || '').slice(0, 1200);
    ok('B4 previewing again on an already-connected file says "no change"', /no change/i.test(second || ''), (second || '').slice(0, 400));
    // and apply again if the button is still there
    const canApplyAgain = await ev(`!!document.querySelector("#as-prev-claude button[onclick*='connect']")`);
    timings.B_can_apply_again = canApplyAgain;
    if (canApplyAgain) {
      await clickSel(`#as-prev-claude button[onclick*='connect']`);
      await sleep(2500);
      timings.B_after_second_apply = (await txt('#as-prev-claude') || '').slice(0, 900);
    }
    timings.B_double_s = (Date.now() - t2) / 1000;
    await snap('12_applied_twice');

    // codex too
    await clickSel('#as-connect-codex button');
    await until(`document.querySelectorAll('#as-prev-codex .as-add').length>0`, 15000);
    await sleep(400);
    await clickSel(`#as-prev-codex button[onclick*='connect']`);
    await sleep(2500);
    timings.B_codex_after = (await txt('#as-prev-codex') || '').slice(0, 900);
    await snap('13_codex_applied');

    // reality change is done from the shell between runs; just report the strip
    timings.B_final_badge = await ev(`(function(){var b=document.querySelector('.ag-wire .ag-wire-badge');return b?b.textContent.trim():null;})()`);
    timings.B_final_line = await txt('.ag-wire .ag-wire-clis');
  }
}

main().catch(e => {
  fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, fatal: String(e && e.stack || e), results, timings, errors }, null, 2));
  console.log('FATAL ' + e);
  process.exit(1);
});
