/* First use of the Agent tab — re-verification round, real headless Chrome.
 *
 * An earlier round found five things on an older build. HEAD has moved (the
 * composer refuses unknown slash commands now, the observer guards Send,
 * errors carry the valid names). This driver re-runs each claim on HEAD and
 * says, per item, whether it still reproduces.
 *
 * Phase is argv[5]: A (focus/counter/help), B (setup error placement),
 * C (CONNECTED badge across four config realities).
 *
 * argv[2]=json out, argv[3]=CDP port, argv[4]=base url, argv[5]=phase
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9451';
const BASE = process.argv[4] || 'http://127.0.0.1:5451';
const PHASE = (process.argv[5] || 'A').toUpperCase();
const SHOTDIR = OUT.replace(/[^\\/]*$/, '');

const errors = [];
const results = [];
const measures = {};
function ok(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail });
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail === undefined ? '' : ' :: ' + JSON.stringify(detail)));
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
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(120); }
    return await ev(expr);
  };
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8, '/': 191, '?': 191 };
  const press = async (key, opts) => {
    const p = { type: 'rawKeyDown', key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (opts && opts.shift) p.modifiers = 8;
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: KEYS[key], modifiers: (opts && opts.shift) ? 8 : 0 });
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    if (r.result && r.result.data) fs.writeFileSync(SHOTDIR + name + '.png', Buffer.from(r.result.data, 'base64'));
  };
  const goto = async (url) => {
    await send('Page.navigate', { url });
    await until(`document.readyState === "complete"`, 15000);
    await sleep(700);
  };

  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');

  if (PHASE === 'A') await phaseA();
  if (PHASE === 'B') await phaseB();
  if (PHASE === 'C') await phaseC();
  if (PHASE === 'D') await phaseD();
  if (PHASE === 'E') await phaseE();
  if (PHASE === 'F') await phaseF();

  fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, results, errors, measures }, null, 1));
  ws.close();

  /* ------------------------------------------------------------------ */
  /* A: item 1 (nothing focuses the composer; "/" and "?" steal it),     */
  /*    item 4 (setup counter 4 -> 5), item 5 (/help says nothing).      */
  /* ------------------------------------------------------------------ */
  async function phaseA() {
    // --- item 1, reading 1 ---
    await send('Page.navigate', { url: BASE + '/agent' });
    await until(`document.readyState === "complete"`, 15000);
    const snap = async (label) => ({
      label,
      active: await ev(`(document.activeElement && (document.activeElement.id || document.activeElement.tagName)) || "none"`),
      tag: await ev(`(document.activeElement && document.activeElement.tagName) || "none"`),
    });
    const at0 = await snap('t=0ms (readyState complete)');
    await sleep(800); const at800 = await snap('t=800ms');
    await sleep(1700); const at2500 = await snap('t=2500ms');
    measures.focus_on_load = [at0, at800, at2500];
    const composerSel = await ev(`(function(){
      var t = document.querySelectorAll('#table-pane textarea, .ag-composer textarea, textarea');
      return Array.prototype.map.call(t, function(e){return (e.id||'')+'|'+(e.className||'')+'|'+(e.offsetParent!==null);}).join(' ;; ');
    })()`);
    measures.textareas_on_agent = composerSel;
    ok('item1: activeElement is BODY at 0/800/2500ms (nothing focuses the composer)',
       at0.tag === 'BODY' && at800.tag === 'BODY' && at2500.tag === 'BODY',
       measures.focus_on_load.map(x => x.label + '=' + x.tag).join(', '));
    await shot('fu_A_01_agent_loaded');

    // --- item 1b: a person just starts typing with a leading "/" ---
    const before = await ev(`(function(){var p=document.getElementById('table-pane');return p?p.innerText.slice(0,300):'no pane';})()`);
    measures.pane_before_slash = before.replace(/\s+/g, ' ').slice(0, 200);
    await ev(`document.body.focus()`);
    await press('/');
    await sleep(250);
    const afterSlashFocus = await ev(`(document.activeElement && (document.activeElement.id||document.activeElement.className||document.activeElement.tagName)) || "none"`);
    // continue typing the rest of the line the person meant
    for (const ch of 'run 02a_resonator_spectroscopy q1') { await typeChar(ch); await sleep(8); }
    await sleep(700);
    const afterType = await ev(`(function(){
      var a=document.activeElement;
      var p=document.getElementById('table-pane');
      return JSON.stringify({active:(a&&(a.id||a.className||a.tagName))||'none',
        val:(a&&a.value!==undefined)?String(a.value):null,
        pane:p?p.innerText.replace(/\\s+/g,' ').slice(0,260):'no pane'});
    })()`);
    measures.after_blind_slash = JSON.parse(afterType);
    await shot('fu_A_02_after_blind_slash');
    ok('item1b: a leading "/" is taken by the global focus-search, not the composer',
       afterSlashFocus !== 'none' && String(afterSlashFocus).toLowerCase().indexOf('composer') < 0
         && measures.after_blind_slash.active !== 'BODY',
       { focus_after_slash: afterSlashFocus, active_after_typing: measures.after_blind_slash.active,
         value: measures.after_blind_slash.val });
    ok('item1b-consequence: the main pane now reads "No results" / is emptied',
       /no result|no match|nothing/i.test(measures.after_blind_slash.pane),
       measures.after_blind_slash.pane.slice(0, 200));

    // --- item 1c: "?" ---
    await goto(BASE + '/agent');
    await ev(`document.body.focus()`);
    await press('?', { shift: true });
    await sleep(400);
    measures.after_question = await ev(`(function(){
      var s=document.getElementById('kb-cheatsheet');
      return JSON.stringify({sheet: !!s, text: s?s.innerText.replace(/\\s+/g,' ').slice(0,300):null,
        active:(document.activeElement&&(document.activeElement.id||document.activeElement.tagName))||'none'});
    })()`);
    measures.after_question = JSON.parse(measures.after_question);
    await shot('fu_A_03_after_question_mark');
    ok('item1c: "?" opens the app cheat sheet instead of typing into the composer',
       measures.after_question.sheet === true, measures.after_question);

    // --- item 4: the setup counter ---
    await goto(BASE + '/agent');
    const readCounter = async () => await ev(`(function(){
      var l=document.querySelector('.ag-wire-setup'); return l?l.textContent.trim():'(no .ag-wire-setup)';
    })()`);
    await until(`(function(){var l=document.querySelector('.ag-wire-setup');return l && /step|Setup/.test(l.textContent);})()`, 8000);
    const c0 = await readCounter();
    const todo0 = await ev(`fetch('/api/agent/setup').then(r=>r.json()).then(j=>JSON.stringify(j.todo))`);
    await shot('fu_A_04_counter_before');
    measures.counter_before = { label: c0, todo: JSON.parse(todo0) };
    ok('item4-pre: the strip names the step "calibrations folder"',
       JSON.parse(todo0).indexOf('calibrations_folder') >= 0, measures.counter_before);
    ok('item4-pre-count: counter reads what todo says', /^4 setup steps/.test(c0), c0);
    measures.note_counter_A = 'phase A stops here; the write (setting the folder) happens in the harness, then A2 re-reads';
  }

  /* ------------------------------------------------------------------ */
  /* B: item 2 — where step 4's error lands.                            */
  /* ------------------------------------------------------------------ */
  async function phaseB() {
    await goto(BASE + '/agent/setup');
    await until(`!!document.getElementById('as-jroot')`, 12000);
    await sleep(600);
    await shot('fu_B_01_setup_loaded');

    const geom = async (label) => JSON.parse(await ev(`(function(){
      var pane=document.getElementById('table-pane');
      var body=document.getElementById('as-body');
      var btn=Array.prototype.find.call(document.querySelectorAll('#as-body button'),function(b){return /Use this folder/.test(b.textContent);});
      var err=document.querySelector('#as-body > .ag-err');
      return JSON.stringify({
        label: ${JSON.stringify(label)},
        paneScrollTop: pane?pane.scrollTop:null,
        paneScrollHeight: pane?pane.scrollHeight:null,
        paneClientHeight: pane?pane.clientHeight:null,
        paneRectTop: pane?Math.round(pane.getBoundingClientRect().top):null,
        btnRectTop: btn?Math.round(btn.getBoundingClientRect().top):null,
        errPresent: !!err,
        errText: err?err.textContent.slice(0,160):null,
        errRectTop: err?Math.round(err.getBoundingClientRect().top):null,
        errOffsetTop: err?err.offsetTop:null,
        bodyChild0: body?(body.firstElementChild?body.firstElementChild.tagName+'.'+body.firstElementChild.className:null):null
      });
    })()`));

    // scroll down to step 4 the way a person does
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('#as-body button'),function(b){return /Use this folder/.test(b.textContent);}); if(b) b.scrollIntoView({block:'center'}); return 1;})()`);
    await sleep(500);
    const pre = await geom('scrolled to step 4');
    measures.B_pre = pre;
    await shot('fu_B_02_scrolled_to_step4');

    // an input the server will refuse (a file, not a folder / a path that cannot be made)
    await ev(`(function(){var e=document.getElementById('as-jroot'); e.focus(); e.value=''; return 1;})()`);
    for (const ch of 'Z:\\\\nope\\\\<<bad>>\\\\journal') { await typeChar(ch); }
    await sleep(200);
    measures.B_typed = await ev(`document.getElementById('as-jroot').value`);
    // press the button for real
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('#as-body button'),function(b){return /Use this folder/.test(b.textContent);}); b.click(); return 1;})()`);
    await sleep(1200);
    const post = await geom('right after the press');
    measures.B_post = post;
    await shot('fu_B_03_after_press');
    ok('item2: the press produced an error element', post.errPresent, post.errText);
    if (post.errPresent) {
      const dy = post.btnRectTop - post.errRectTop;
      measures.B_distance_px = dy;
      ok('item2: the error renders ABOVE the button that was pressed (offscreen)',
         post.errRectTop < post.paneRectTop, { errRectTop: post.errRectTop, paneRectTop: post.paneRectTop, btnRectTop: post.btnRectTop, px_above_button: dy });
    }
    // does it self-delete?
    await sleep(6500);
    const late = await geom('t+6.5s');
    measures.B_late = late;
    ok('item2: the error self-deletes after 6 s', post.errPresent && !late.errPresent,
       { was: post.errPresent, now: late.errPresent });
    await shot('fu_B_04_after_6s');

    // --- the same shape elsewhere on the page? ---
    // writeContext() is the other alertErr caller. Drive it by asking the
    // server to write context with no calibrations folder set.
    measures.B_alertErr_callers = await ev(`(function(){
      var src = window.AgentSetup ? 'mounted' : 'not mounted';
      return src;
    })()`);
    // enumerate every error host the page uses, and where each one is
    measures.B_error_hosts = JSON.parse(await ev(`(function(){
      var ids=['as-prev-claude','as-prev-codex','as-ctx','as-ctx-prev','as-test','as-dryrun-msg','as-body'];
      return JSON.stringify(ids.map(function(i){var e=document.getElementById(i);
        return {id:i, present:!!e, rectTop:e?Math.round(e.getBoundingClientRect().top):null};}));
    })()`));
  }

  /* ------------------------------------------------------------------ */
  /* D: second confirmation of item 1, + item 4 (the counter), driven   */
  /*    through the product's own writes.                               */
  /* ------------------------------------------------------------------ */
  async function phaseD() {
    // ---- item 1, second confirmation, with the search overlay measured ----
    await goto(BASE + '/agent');
    const t0 = await ev(`(document.activeElement||{}).tagName || 'none'`);
    await sleep(800); const t800 = await ev(`(document.activeElement||{}).tagName || 'none'`);
    await sleep(1700); const t2500 = await ev(`(document.activeElement||{}).tagName || 'none'`);
    const composerExists = await ev(`!!document.querySelector('textarea.ag-input')`);
    ok('item1 (2nd): BODY at 0/800/2500ms while a composer exists',
       t0 === 'BODY' && t800 === 'BODY' && t2500 === 'BODY' && composerExists,
       { t0, t800, t2500, composerExists });
    await ev(`document.body.focus()`);
    await press('/');
    await sleep(200);
    for (const ch of 'run ramsey q0') { await typeChar(ch); await sleep(8); }
    await sleep(800);
    measures.D_after_slash = JSON.parse(await ev(`(function(){
      var a=document.activeElement;
      var sr=document.querySelector('#search-results, .search-results, [id*="search-result"]');
      var comp=document.querySelector('textarea.ag-input');
      var body=document.body.innerText.replace(/\\s+/g,' ');
      return JSON.stringify({
        active:(a&&(a.id||a.className||a.tagName))||'none',
        activeValue:(a&&a.value!==undefined)?String(a.value):null,
        composerValue: comp?comp.value:null,
        composerVisible: comp?(comp.offsetParent!==null):null,
        noResults: /No results for/.test(body),
        noResultsText: (body.match(/No results for [^ ]*[^|]{0,60}/)||[''])[0]
      });
    })()`));
    await shot('fu_D_01_second_blind_slash');
    ok('item1 (2nd): the whole line landed in the global search, composer still empty',
       measures.D_after_slash.composerValue === '' && measures.D_after_slash.activeValue === 'run ramsey q0',
       measures.D_after_slash);
    ok('item1 (2nd): the main pane is replaced by "No results for …"',
       measures.D_after_slash.noResults === true, measures.D_after_slash.noResultsText);

    // ---- item 4: the counter, twice ----
    const counterNow = async () => {
      await goto(BASE + '/agent');
      await until(`(function(){var l=document.querySelector('.ag-wire-setup');return l&&/step|Setup/.test(l.textContent);})()`, 9000);
      await sleep(400);
      return {
        label: (await ev(`document.querySelector('.ag-wire-setup').textContent.trim()`)),
        todo: JSON.parse(await ev(`fetch('/api/agent/setup').then(r=>r.json()).then(j=>JSON.stringify(j.todo))`))
      };
    };
    const setCal = async (v) => await ev(`fetch('/scheduler/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({calibrations_folder:${JSON.stringify(v)}})}).then(r=>r.status)`);

    for (const round of [1, 2]) {
      await setCal('');
      const before = await counterNow();
      await shot('fu_D_r' + round + '_counter_before');
      const st = await setCal(process.argv[6] || '');
      const after = await counterNow();
      await shot('fu_D_r' + round + '_counter_after');
      measures['D_counter_round' + round] = { before, after, postStatus: st };
      ok('item4 (round ' + round + '): the counter GOES UP after completing the step it named',
         (after.todo.length > before.todo.length),
         { before: before.label + ' ' + JSON.stringify(before.todo),
           after: after.label + ' ' + JSON.stringify(after.todo) });
    }
    await setCal('');

    // ---- item 5: /help ----
    await goto(BASE + '/help');
    await sleep(600);
    const helpText = await ev(`document.body.innerText`);
    const helpHtml = await ev(`document.documentElement.outerHTML.length`);
    measures.D_help = {
      chars: helpText.length, html: helpHtml,
      mentions: {
        agent: /\bagent\b/i.test(helpText),
        Agent_tab: /Agent tab|Agent cockpit|\/agent/i.test(helpText),
        MCP: /\bMCP\b/.test(helpText),
        slash_run: /\/run\b/.test(helpText),
        ask_writes: /ask-writes|ask writes/i.test(helpText),
        claude: /\bclaude\b/i.test(helpText),
        codex: /\bcodex\b/i.test(helpText),
        calibrate: /calibrat/i.test(helpText),
      },
      head: helpText.replace(/\s+/g, ' ').slice(0, 400)
    };
    await shot('fu_D_02_help');
    const m = measures.D_help.mentions;
    ok('item5: /help mentions none of Agent / MCP / /run / ask-writes',
       !m.Agent_tab && !m.MCP && !m.slash_run && !m.ask_writes, measures.D_help.mentions);
  }

  /* ------------------------------------------------------------------ */
  /* E: item 2 — the OTHER alertErr caller (step 5 "Write"), same shape. */
  /* ------------------------------------------------------------------ */
  async function phaseE() {
    await goto(BASE + '/agent/setup');
    await until(`!!document.getElementById('as-jroot')`, 12000);
    await sleep(600);
    // Reach alertErr through writeContext() without a calibrations folder:
    // AgentSetup.writeContext() posts apply:true and the server refuses.
    await ev(`(function(){var p=document.getElementById('table-pane'); if(p) p.scrollTop = p.scrollHeight; return 1;})()`);
    await sleep(400);
    const pre = JSON.parse(await ev(`(function(){var p=document.getElementById('table-pane');
      return JSON.stringify({scrollTop:p.scrollTop, clientH:p.clientHeight, scrollH:p.scrollHeight, rectTop:Math.round(p.getBoundingClientRect().top)});})()`));
    measures.E_pre = pre;
    await ev(`window.AgentSetup.writeContext()`);
    await sleep(1500);
    measures.E_post = JSON.parse(await ev(`(function(){
      var body=document.getElementById('as-body');
      var err=body.querySelector(':scope > .ag-err');
      var p=document.getElementById('table-pane');
      return JSON.stringify({errPresent:!!err, errText:err?err.textContent.slice(0,200):null,
        errRectTop:err?Math.round(err.getBoundingClientRect().top):null,
        errOffsetTop:err?err.offsetTop:null,
        paneRectTop:Math.round(p.getBoundingClientRect().top), paneScrollTop:p.scrollTop});
    })()`));
    await shot('fu_E_01_writeContext_err');
    ok('item2-sibling: step 5 "Write" uses the same alertErr and lands off-screen too',
       measures.E_post.errPresent && measures.E_post.errRectTop < measures.E_post.paneRectTop,
       measures.E_post);
    await sleep(6500);
    measures.E_late = await ev(`!!document.getElementById('as-body').querySelector(':scope > .ag-err')`);
    ok('item2-sibling: it self-deletes after 6 s too', measures.E_post.errPresent && !measures.E_late,
       { was: measures.E_post.errPresent, now: measures.E_late });
  }

  /* ------------------------------------------------------------------ */
  /* F: recovery cost after the blind "/", the composer's own hint, and  */
  /*    item 5 scoped to the help PANE (not the surrounding shell).      */
  /* ------------------------------------------------------------------ */
  async function phaseF() {
    await goto(BASE + '/agent');
    measures.F_composer = JSON.parse(await ev(`(function(){
      var c=document.querySelector('textarea.ag-input');
      var r=c?c.getBoundingClientRect():null;
      return JSON.stringify({placeholder:c?c.placeholder:null, autofocus:c?c.hasAttribute('autofocus'):null,
        top:r?Math.round(r.top):null, inViewport:r?(r.top<window.innerHeight&&r.bottom>0):null,
        pageSearchBoxes: document.querySelectorAll('#table-pane input[type="search"], #table-pane .tree-search').length});
    })()`));
    await ev(`document.body.focus()`);
    await press('/');
    for (const ch of 'run t1 q0') { await typeChar(ch); await sleep(8); }
    await sleep(800);
    measures.F_overlay = JSON.parse(await ev(`(function(){
      var c=document.querySelector('textarea.ag-input');
      return JSON.stringify({agentVisible: c?(c.offsetParent!==null):null,
        bodyHasNoResults: /No results for/.test(document.body.innerText)});
    })()`));
    // how does a person get back?
    await press('Escape'); await sleep(500);
    const afterEsc = JSON.parse(await ev(`(function(){
      var a=document.activeElement;
      return JSON.stringify({noResults:/No results for/.test(document.body.innerText),
        searchValue:(document.getElementById('global-search')||{}).value,
        active:(a&&(a.id||a.tagName))||'none'});})()`));
    measures.F_after_escape = afterEsc;
    await shot('fu_F_01_after_escape');
    ok('F: Escape does NOT clear the hijacked search / restore the Agent page',
       afterEsc.noResults === true, afterEsc);

    // ---- item 5, second reading, scoped to the help PANE ----
    for (const round of [1, 2]) {
      await goto(BASE + '/help');
      await until(`!!document.getElementById('table-pane')`, 9000);
      await sleep(500);
      const paneText = await ev(`document.getElementById('table-pane').innerText`);
      const hits = {
        Agent: (paneText.match(/Agent/g) || []).length,
        agent_any: (paneText.match(/agent/gi) || []).length,
        MCP: (paneText.match(/MCP/g) || []).length,
        slash_run: (paneText.match(/\/run/g) || []).length,
        ask_writes: (paneText.match(/ask-writes/gi) || []).length,
        claude: (paneText.match(/claude/gi) || []).length,
        codex: (paneText.match(/codex/gi) || []).length,
        Calibration_log: (paneText.match(/Calibration log/g) || []).length,
        chars: paneText.length,
      };
      measures['F_help_round' + round] = hits;
      ok('item5 (round ' + round + '): the help PANE never mentions Agent / MCP / /run / ask-writes',
         hits.Agent === 0 && hits.MCP === 0 && hits.slash_run === 0 && hits.ask_writes === 0, hits);
    }
    await shot('fu_F_02_help_pane');
  }

  /* ------------------------------------------------------------------ */
  /* C: item 3 — the badge is an OR across CLIs.                        */
  /* ------------------------------------------------------------------ */
  async function phaseC() {
    await goto(BASE + '/agent');
    await until(`(function(){var b=document.querySelector('.ag-wire-badge, [class*="ag-wire"] .ag-wire-badge');return b&&b.textContent.trim().length&&b.textContent.trim()!=='CHECKING';})()`, 12000);
    await sleep(800);
    const read = JSON.parse(await ev(`(function(){
      var strip=document.querySelector('.ag-wire');
      var b=document.querySelector('.ag-wire-badge');
      var clis=document.querySelector('.ag-wire-clis');
      return JSON.stringify({
        badge: b?b.textContent.trim():null,
        badgeClass: b?b.className:null,
        clis: clis?clis.innerText.replace(/\\s+/g,' ').trim():null,
        stripText: strip?strip.innerText.replace(/\\s+/g,' ').trim():null,
        setupLink: (document.querySelector('.ag-wire-setup')||{}).textContent
      });
    })()`));
    measures.C_read = read;
    const api = JSON.parse(await ev(`fetch('/api/agent/setup').then(r=>r.json()).then(j=>JSON.stringify({claude:j.claude,codex:j.codex,clis:j.clis,todo:j.todo}))`));
    measures.C_api = api;
    await shot('fu_C_' + (process.argv[6] || 'x'));
    ok('C: badge text captured', !!read.badge, read);
  }
}

main().then(() => process.exit(0), e => { console.error(e); fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, fatal: String(e && e.stack || e), results, errors, measures }, null, 1)); process.exit(1); });
