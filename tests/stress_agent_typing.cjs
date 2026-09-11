/* QUESTION 2 — "how does a person input right away?"
 *
 * A calibration physicist mid-session opens /agent with a chip already loaded
 * and just wants to TYPE. This driver measures the composer itself in real
 * headless Chrome: when it becomes usable, where focus is, what a blind
 * keystroke does, Enter vs Shift+Enter with real key events, the /run grammar
 * and its feedback, the preset buttons, keyboard-only reach, and paste.
 *
 * HARD RULE honoured here: nothing may reach a model. Every line that is ever
 * submitted starts with "/" (a /run line makes a plan CARD deterministically);
 * no preset draft is ever submitted; Start is never pressed.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9432';
const BASE = process.argv[4] || 'http://127.0.0.1:5432';
const SHOT = OUT.replace(/\.json$/, '');

const errors = [];
const results = [];
const measures = {};
function ok(name, cond, detail) { results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail }); }
function note(k, v) { measures[k] = v; }

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
  const shot = async (tag) => {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result && s.result.data) fs.writeFileSync(SHOT + '_' + tag + '.png', Buffer.from(s.result.data, 'base64'));
  };

  // ---- real input primitives -------------------------------------------
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowLeft: 37, ArrowRight: 39, Backspace: 8, End: 35, Home: 36 };
  const press = async (key, mods) => {
    const modifiers = ((mods && mods.shift) ? 8 : 0) | ((mods && mods.ctrl) ? 2 : 0);
    const p = { type: 'rawKeyDown', key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key], modifiers };
    if (key === 'Enter') { p.type = 'keyDown'; p.text = '\r'; }
    if (key === 'Tab') { p.type = 'keyDown'; p.text = '\t'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: KEYS[key], modifiers });
  };
  const typeText = async (s, perCharMs) => { for (const ch of s) { await typeChar(ch); if (perCharMs) await sleep(perCharMs); } };
  const clickSel = async (sel) => {
    const box = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var r=e.getBoundingClientRect(); if(!r.width) return null; return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+Math.min(r.height/2, 14))};})()`);
    if (!box) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return true;
  };
  const active = async () => ev(`(function(){var a=document.activeElement; if(!a) return null; return {tag:a.tagName, id:a.id||null, cls:a.className||null, type:a.type||null, ph:(a.placeholder||'').slice(0,40)};})()`);
  const taVal = async () => ev(`(function(){var t=document.querySelector('.ag-input'); return t? t.value : null;})()`);
  const setTa = async (v) => ev(`(function(){var t=document.querySelector('.ag-input'); if(!t) return 0; t.focus(); t.value=${JSON.stringify(v)}; t.dispatchEvent(new Event('input',{bubbles:true})); t.setSelectionRange(t.value.length,t.value.length); return 1;})()`);
  const netCount = async () => ev(`(window.__net||[]).length`);
  const netSince = async (n) => ev(`JSON.stringify((window.__net||[]).slice(${n}))`).then(s => JSON.parse(s || '[]'));
  const toasts = async () => ev(`JSON.stringify(Array.prototype.map.call(document.querySelectorAll('#status-bar .toast'), function(t){return {lvl:t.className, text:(t.textContent||'').trim()};}))`).then(s => JSON.parse(s || '[]'));
  const clearToasts = async () => ev(`(function(){var b=document.getElementById('status-bar'); if(b) b.innerHTML=''; return 1;})()`);
  const planCards = async () => ev(`document.querySelectorAll('.ag-cards [data-card^="plan:"]').length`);

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  // a network log installed BEFORE the page's own scripts, so "did pressing
  // this button talk to the server?" is answerable rather than guessed
  // …and a HARD BLOCK on the two routes that would spawn the user's own
  // logged-in CLI. Nothing in this round may reach a model; a line that would
  // have is recorded in window.__blocked and answered with a synthetic 503,
  // so the product's behaviour is observable without paying for it.
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `window.__net=[];window.__blocked=[];(function(){var f=window.fetch;window.fetch=function(u,o){
        var url=String((u&&u.url)||u), meth=(o&&o.method)||'GET';
        try{window.__net.push({m:meth,u:url,t:Date.now()});}catch(e){}
        if(meth==='POST' && /\\/api\\/agent\\/chat\\/(start|send)/.test(url)){
          try{window.__blocked.push({u:url,body:(o&&o.body)||null,t:Date.now()});}catch(e){}
          return Promise.resolve(new Response(JSON.stringify({error:'BLOCKED BY THE TEST DRIVER (would have started a CLI session)'}),{status:503,headers:{'Content-Type':'application/json'}}));
        }
        return f.apply(this,arguments);};
      var X=window.XMLHttpRequest.prototype.open;window.XMLHttpRequest.prototype.open=function(m,u){try{window.__net.push({m:m,u:String(u),t:Date.now(),x:1});}catch(e){}return X.apply(this,arguments);};})();`
  });

  /* ══ A. page load → composer usable ═════════════════════════════════ */
  let t0 = Date.now();
  await send('Page.navigate', { url: BASE + '/agent' });
  let tPresent = null, tUsable = null, tFeed = null;
  while (Date.now() - t0 < 20000) {
    const st = await ev(`(function(){var t=document.querySelector('.ag-input');
      var cards=document.querySelector('.ag-cards');
      return {ta: !!t, dis: t? !!t.disabled : null, ro: t? !!t.readOnly : null,
              h: t? Math.round(t.getBoundingClientRect().height) : 0,
              feed: cards? cards.children.length : -1,
              now: (document.querySelector('.ag-now')||{}).textContent ? 1:0,
              rs: document.readyState};})()`).catch(() => null);
    if (st && st.ta && tPresent === null) tPresent = Date.now() - t0;
    if (st && st.ta && !st.dis && st.h > 0 && tUsable === null) tUsable = Date.now() - t0;
    if (st && (st.feed > 0 || st.now) && tFeed === null) tFeed = Date.now() - t0;
    if (tUsable !== null && tFeed !== null) break;
    await sleep(40);
  }
  note('ms_composer_present', tPresent);
  note('ms_composer_usable', tUsable);
  note('ms_feed_first_content', tFeed);
  const perf = await ev(`(function(){var n=performance.getEntriesByType('navigation')[0]||{}; return {dcl:Math.round(n.domContentLoadedEventEnd||0), load:Math.round(n.loadEventEnd||0), resp:Math.round(n.responseEnd||0)};})()`);
  note('nav_timing', perf);
  ok('the composer exists after load', tUsable !== null, { ms: tUsable });

  // where is focus, over time?
  const focusTrack = [];
  for (const at of [0, 800, 2500]) { if (at) await sleep(at - (focusTrack.length ? [0, 800, 2500][focusTrack.length - 1] : 0)); focusTrack.push({ at, el: await active() }); }
  note('focus_after_load', focusTrack);
  ok('focus is put in the composer on load',
     focusTrack.some(f => f.el && f.el.cls && String(f.el.cls).indexOf('ag-input') >= 0), focusTrack);

  /* ── A2. the blind keystroke: type without clicking anything ───────── */
  let n0 = await netCount();
  await typeText('rabi', 25);
  await sleep(300);
  const blind1 = { ta: await taVal(), active: await active(),
                   global: await ev(`(document.getElementById('global-search')||{}).value||null`),
                   side: await ev(`(document.getElementById('sidebar-filter-input')||{}).value||null`) };
  note('blind_plain_typing', blind1);
  ok('typing straight after load lands in the composer', blind1.ta === 'rabi', blind1);

  // and the line a person on this page would actually type: it starts with "/"
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3000);
  await typeText('/run 04b_power_rabi q1', 18);
  await sleep(400);
  const blind2 = { ta: await taVal(), active: await active(),
                   global: await ev(`(document.getElementById('global-search')||{}).value||null`),
                   side: await ev(`(document.getElementById('sidebar-filter-input')||{}).value||null`),
                   sheet: await ev(`!!document.getElementById('kb-cheatsheet')`) };
  note('blind_slash_typing', blind2);
  ok('a blind "/run …" line lands in the composer, not somewhere else',
     blind2.ta === '/run 04b_power_rabi q1', blind2);
  await shot('01_after_blind_slash');

  /* ══ B. the composer, clicked, with real keys ═══════════════════════ */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3200);
  await clearToasts();
  const tClick = Date.now();
  const clicked = await clickSel('.ag-input');
  await sleep(120);
  const afterClick = await active();
  note('ms_click_to_focus', Date.now() - tClick);
  ok('clicking the composer focuses it', clicked && afterClick && String(afterClick.cls).indexOf('ag-input') >= 0, afterClick);
  note('placeholder', await ev(`(document.querySelector('.ag-input')||{}).placeholder`));

  // — Enter on an empty box
  n0 = await netCount();
  await press('Enter');
  await sleep(500);
  ok('Enter on an empty box does nothing at all',
     (await netSince(n0)).filter(r => /agent\/(chat|plans)/.test(r.u)).length === 0 && (await taVal()) === '',
     { net: await netSince(n0), toasts: await toasts() });

  // — Enter on whitespace only
  await setTa('   \n  ');
  n0 = await netCount();
  await press('Enter');
  await sleep(500);
  const wsNet = (await netSince(n0)).filter(r => /agent\/(chat|plans)/.test(r.u));
  ok('Enter on whitespace only sends nothing', wsNet.length === 0, { net: wsNet, value: JSON.stringify(await taVal()) });
  ok('…and it does not silently wipe the box either (or says why nothing happened)',
     true, { value: JSON.stringify(await taVal()), toasts: (await toasts()).map(t => t.text) });

  // — Shift+Enter makes a newline, caret at the end
  await setTa('/run 04b_power_rabi q1');
  await press('Enter', { shift: true });
  await sleep(200);
  let v = await taVal();
  ok('Shift+Enter inserts a newline instead of sending', /\n$/.test(String(v)), JSON.stringify(v));
  const grewTo = await ev(`Math.round((document.querySelector('.ag-input')||{getBoundingClientRect:function(){return{height:0}}}).getBoundingClientRect().height)`);
  await typeText('second line', 0);
  await sleep(200);
  const grew2 = await ev(`Math.round(document.querySelector('.ag-input').getBoundingClientRect().height)`);
  ok('…and the box grows for the second line', grew2 >= grewTo, { one: grewTo, two: grew2 });

  // — caret in the MIDDLE: Shift+Enter breaks there, Enter still sends the whole thing
  await setTa('/run 04b_power_rabi q1');
  await ev(`(function(){var t=document.querySelector('.ag-input'); t.focus(); t.setSelectionRange(4,4); return 1;})()`);
  await press('Enter', { shift: true });
  await sleep(200);
  v = await taVal();
  ok('Shift+Enter with the caret mid-text breaks the line AT the caret',
     v === '/run\n 04b_power_rabi q1', JSON.stringify(v));

  await setTa('/run 04b_power_rabi q1');
  await ev(`(function(){var t=document.querySelector('.ag-input'); t.focus(); t.setSelectionRange(9,9); return 1;})()`);
  await clearToasts();
  let before = await planCards();
  n0 = await netCount();
  let tEnter = Date.now();
  await press('Enter');
  let tFb = null;
  while (Date.now() - tEnter < 8000) {
    const ts = await toasts();
    if (ts.length) { tFb = Date.now() - tEnter; break; }
    if ((await planCards()) > before) { tFb = Date.now() - tEnter; break; }
    await sleep(25);
  }
  note('ms_enter_to_feedback_midcaret', tFb);
  const midToasts = await toasts();
  ok('Enter with the caret mid-text sends the WHOLE line', (await netSince(n0)).some(r => /agent\/plans/.test(r.u)),
     { net: await netSince(n0), toasts: midToasts.map(t => t.text) });
  ok('…and the send is acknowledged within 1s', tFb !== null && tFb < 1000, { ms: tFb });
  ok('…and a line that WORKED clears the box', (await taVal()) === '', JSON.stringify(await taVal()));
  await sleep(900);
  ok('…and a plan CARD appears for it', (await planCards()) > before, { before, after: await planCards() });
  await shot('02_first_plan_card');

  /* ══ C. the /run grammar ════════════════════════════════════════════ */
  const runCases = [
    ['/run 04b_power_rabi q1 num_shots=200', 'with a parameter'],
    ['/run 05_power_rabi q1', 'a node name that is not in the folder'],
    ['/run 04b_power_rabi', 'no targets at all'],
    ['/run 04b_power_rabi qZZ', 'a qubit that does not exist'],
    ['/run 04b_power_rabi q1 q2', 'two targets'],
    ['/run', 'the bare word'],
    ['/runn 04b_power_rabi q1', 'a typo in the command itself'],
    ['/run 04b_power_rabi q1,q2 num_shots=abc', 'comma targets + a non-numeric param'],
  ];
  const runOut = [];
  for (const [line, why] of runCases) {
    await clearToasts();
    before = await planCards();
    await setTa(line);
    n0 = await netCount();
    tEnter = Date.now();
    await press('Enter');
    let fb = null, txt = [];
    while (Date.now() - tEnter < 6000) {
      txt = (await toasts()).map(t => t.lvl + '|' + t.text);
      if (txt.length) { fb = Date.now() - tEnter; break; }
      if ((await planCards()) > before) { fb = Date.now() - tEnter; break; }
      await sleep(25);
    }
    await sleep(700);
    runOut.push({ line, why, ms: fb, toasts: txt, cards_before: before, cards_after: await planCards(),
                  box_after: await taVal(), net: (await netSince(n0)).filter(r => /agent/.test(r.u)).map(r => r.m + ' ' + r.u.replace(BASE, '')) });
  }
  note('run_grammar', runOut);
  const good = runOut.filter(r => r.cards_after > r.cards_before);
  const bad = runOut.filter(r => r.cards_after === r.cards_before);
  ok('every /run line answers within 1.5s', runOut.every(r => r.ms !== null && r.ms < 1500), runOut.map(r => r.line + ' → ' + r.ms + 'ms'));
  ok('a refused line KEEPS its text in the box', bad.every(r => r.box_after === r.line), bad.map(r => r.line + ' → ' + JSON.stringify(r.box_after)));
  ok('an accepted line clears the box', good.every(r => r.box_after === ''), good.map(r => r.line + ' → ' + JSON.stringify(r.box_after)));
  ok('a bad node name is told which node names exist',
     (runOut.find(r => r.line === '/run 05_power_rabi q1') || {}).toasts.join(' ').indexOf('04b_power_rabi') >= 0,
     (runOut.find(r => r.line === '/run 05_power_rabi q1') || {}).toasts);
  ok('a bogus qubit is told which qubits exist',
     (runOut.find(r => r.line === '/run 04b_power_rabi qZZ') || {}).toasts.join(' ').indexOf('q1') >= 0,
     (runOut.find(r => r.line === '/run 04b_power_rabi qZZ') || {}).toasts);
  ok('/run with no targets does not silently make a plan that runs on nothing',
     (runOut.find(r => r.line === '/run 04b_power_rabi') || {}).cards_after ===
     (runOut.find(r => r.line === '/run 04b_power_rabi') || {}).cards_before,
     runOut.find(r => r.line === '/run 04b_power_rabi'));
  await shot('03_after_grammar');

  /* — a slash word that is NOT /run. The driver blocks the two routes that
       spawn a CLI, so what SM would have done is recorded, not paid for. */
  const slashOut = [];
  for (const line of ['/help', '/ru 04b_power_rabi q1', '/RUN 04b_power_rabi q1']) {
    await clearToasts();
    await ev(`window.__blocked=[]; 1`);
    before = await planCards();
    await setTa(line);
    n0 = await netCount();
    tEnter = Date.now();
    await press('Enter');
    let fb = null;
    while (Date.now() - tEnter < 5000) { if ((await toasts()).length || (await planCards()) > before) { fb = Date.now() - tEnter; break; } await sleep(30); }
    await sleep(500);
    slashOut.push({ line, ms: fb, blocked: await ev(`JSON.stringify(window.__blocked||[])`).then(s => JSON.parse(s)),
                    toasts: (await toasts()).map(t => t.text.slice(0, 120)), box: await taVal(),
                    net: (await netSince(n0)).filter(r => /agent/.test(r.u)).map(r => r.m + ' ' + r.u.replace(BASE, '')) });
  }
  note('slash_not_run', slashOut);
  ok('a slash word that is not /run does NOT silently start a CLI session',
     slashOut.every(s => s.blocked.length === 0), slashOut.map(s => s.line + ' → would have posted ' + JSON.stringify(s.blocked.map(b => b.u))));
  await clearToasts();

  // — twice in a row, fast (no settle between)
  await clearToasts();
  before = await planCards();
  await setTa('/run 04b_power_rabi q1');
  await press('Enter');
  await setTa('/run 04b_power_rabi q2');            // immediately, no wait
  await press('Enter');
  await sleep(2500);
  const twice = { before, after: await planCards(), box: await taVal(), toasts: (await toasts()).map(t => t.text) };
  note('twice_quickly', twice);
  ok('two /run lines in a row both land', twice.after - twice.before === 2, twice);

  // — a 5,000 character line
  await clearToasts();
  before = await planCards();
  const giant = '/run ' + 'x'.repeat(4995);
  await setTa(giant);
  note('giant_len', (await taVal()).length);
  tEnter = Date.now();
  await press('Enter');
  let gfb = null;
  while (Date.now() - tEnter < 15000) { if ((await toasts()).length || (await planCards()) > before) { gfb = Date.now() - tEnter; break; } await sleep(30); }
  await sleep(400);
  const gt = await toasts();
  note('giant', { ms: gfb, toast_len: gt.length ? gt[0].text.length : 0, toast_head: gt.length ? gt[0].text.slice(0, 120) : null,
                  cards: (await planCards()) - before,
                  toast_box: await ev(`(function(){var t=document.querySelector('#status-bar .toast'); if(!t) return null; var r=t.getBoundingClientRect(); return {w:Math.round(r.width),h:Math.round(r.height),top:Math.round(r.top)};})()`),
                  doc_overflow: await ev(`document.documentElement.scrollWidth - document.documentElement.clientWidth`) });
  ok('a 5,000-character line answers quickly and does not hang', gfb !== null && gfb < 4000, { ms: gfb });
  ok('…and the refusal does not cover the screen', measures.giant.toast_box === null || measures.giant.toast_box.h < 400, measures.giant.toast_box);
  await shot('04_giant_line');
  await clearToasts();

  /* ══ D. the preset buttons ══════════════════════════════════════════ */
  const presetNames = await ev(`JSON.stringify(Array.prototype.map.call(document.querySelectorAll('.ag-preset'), function(b){return b.textContent;}))`).then(s => JSON.parse(s));
  note('presets', presetNames);
  const presetOut = [];
  for (let i = 0; i < presetNames.length; i++) {
    await setTa('');
    n0 = await netCount();
    before = await planCards();
    await ev(`(function(){var b=document.querySelectorAll('.ag-preset')[${i}]; if(b) b.click(); return 1;})()`);
    await sleep(400);
    presetOut.push({ name: presetNames[i], draft: (await taVal() || '').slice(0, 90),
                     sel: await ev(`(function(){var t=document.querySelector('.ag-input'); return {s:t.selectionStart,e:t.selectionEnd, sub:t.value.slice(t.selectionStart,t.selectionEnd)};})()`),
                     focused: String((await active() || {}).cls).indexOf('ag-input') >= 0,
                     net: (await netSince(n0)).filter(r => /agent/.test(r.u)).map(r => r.m + ' ' + r.u.replace(BASE, '')),
                     cards_delta: (await planCards()) - before });
  }
  note('preset_presses', presetOut);
  ok('every preset fills a draft', presetOut.every(p => p.draft.length > 10), presetOut.map(p => p.name + ': ' + p.draft.slice(0, 40)));
  ok('…and no preset starts anything', presetOut.every(p => p.net.length === 0 && p.cards_delta === 0), presetOut.map(p => p.name + ' → ' + JSON.stringify(p.net)));
  ok('…and each one selects the bit you must replace', presetOut.every(p => /^<.*>$/.test(p.sub || '')), presetOut.map(p => p.sub));
  ok('…and leaves the keyboard in the box', presetOut.every(p => p.focused), presetOut.map(p => p.focused));

  // same preset twice
  await ev(`(function(){var b=document.querySelectorAll('.ag-preset')[0]; b.click(); b.click(); return 1;})()`);
  await sleep(300);
  ok('pressing one preset twice does not double the draft',
     (await taVal()).indexOf('1Q bringup on') === (await taVal()).lastIndexOf('1Q bringup on'), (await taVal()).slice(0, 120));

  // preset → edit → another preset (is the edit thrown away silently?)
  await ev(`(function(){var b=document.querySelectorAll('.ag-preset')[0]; b.click(); return 1;})()`);
  await sleep(200);
  await typeText('q1 q2', 0);                       // the person fills in <targets>
  await sleep(200);
  const edited = await taVal();
  await ev(`(function(){var b=document.querySelectorAll('.ag-preset')[1]; b.click(); return 1;})()`);
  await sleep(300);
  const afterSecond = await taVal();
  note('preset_clobber', { edited: edited.slice(0, 80), after: afterSecond.slice(0, 80) });
  ok('a second preset warns before throwing away what you typed',
     afterSecond.indexOf('q1 q2') >= 0 || (await toasts()).length > 0,
     { edited: edited.slice(0, 60), after: afterSecond.slice(0, 60), toasts: (await toasts()).map(t => t.text) });
  await setTa('');
  await shot('05_presets');

  /* ══ E. keyboard only ═══════════════════════════════════════════════ */
  await clickSel('.ag-input');
  await sleep(150);
  const tabWalk = [];
  for (let i = 0; i < 6; i++) { await press('Tab'); await sleep(120); tabWalk.push(await active()); }
  note('tab_from_composer', tabWalk);
  ok('Tab walks from the composer to Send without leaving the form',
     tabWalk.some(a => a && String(a.cls).indexOf('ag-send') >= 0), tabWalk.map(a => a && (a.cls || a.tag)));
  const ring = await ev(`(function(){var a=document.activeElement; if(!a||a===document.body) return null; var cs=getComputedStyle(a);
     return {outline:cs.outlineStyle+' '+cs.outlineWidth+' '+cs.outlineColor, shadow:(cs.boxShadow||'').slice(0,60), el:a.className||a.tagName};})()`);
  note('focus_ring_on_tabbed_element', ring);
  ok('the focused control shows a visible ring',
     ring && ((ring.outline && !/none/.test(ring.outline) && !/^\D*0px/.test(ring.outline.split(' ')[1] || '')) || (ring.shadow && ring.shadow !== 'none')), ring);

  // Escape in the composer
  await clickSel('.ag-input');
  await setTa('/run 04b_power_rabi q1');
  await press('Escape');
  await sleep(200);
  note('escape', { value: await taVal(), active: await active() });
  ok('Escape in the composer does not throw the draft away', (await taVal()) === '/run 04b_power_rabi q1', await taVal());

  // where focus goes after a send
  await clearToasts();
  await setTa('/run 04b_power_rabi q1');
  await press('Enter');
  await sleep(1600);
  const afterSend = await active();
  note('focus_after_send', afterSend);
  ok('focus stays in the composer after a send',
     afterSend && String(afterSend.cls).indexOf('ag-input') >= 0, afterSend);

  // can the whole flow be driven from the keyboard: reach a plan card's buttons
  const reach = await ev(`(function(){var c=document.querySelector('.ag-cards [data-card^="plan:"]'); if(!c) return null;
     var b=c.querySelectorAll('button,a,select,[tabindex]');
     return {n:b.length, labels:Array.prototype.map.call(b,function(x){return (x.textContent||x.value||'').trim().slice(0,22);}),
             neg:Array.prototype.filter.call(b,function(x){return x.tabIndex<0;}).length};})()`);
  note('plan_card_focusables', reach);
  ok('a plan card\'s own buttons are reachable by Tab', reach && reach.n > 0 && reach.neg === 0, reach);

  /* ══ F. paste ═══════════════════════════════════════════════════════ */
  const pasteCases = [
    ['multi-line', '/run 04b_power_rabi q1\n/run 04b_power_rabi q2\n/run 05_T1 q1'],
    ['tab + emoji + hangul', '/run 04b_power_rabi\tq1 note=\u{1F9CA}컨디션'],
    ['a NUL byte', '/run 04b_power_rabi q1 note=a\u0000b'],
  ];
  const pasteOut = [];
  for (const [tag, text] of pasteCases) {
    await setTa('');
    await clickSel('.ag-input');
    await sleep(100);
    const t = Date.now();
    await send('Input.insertText', { text });
    await sleep(300);
    const got = await taVal();
    pasteOut.push({ tag, ms: Date.now() - t, in_len: text.length, out_len: got.length,
                    kept_newlines: (got.match(/\n/g) || []).length,
                    kept_tab: got.indexOf('\t') >= 0, kept_nul: got.indexOf('') >= 0,
                    same: got === text, out: got.slice(0, 60).replace(/\u0000/g, '<NUL>'),
                    rows: await ev(`Math.round(document.querySelector('.ag-input').getBoundingClientRect().height)`) });
  }
  note('paste', pasteOut);
  ok('a pasted multi-line block arrives whole and the box grows',
     pasteOut[0].kept_newlines === 2 && pasteOut[0].rows > 30, pasteOut[0]);
  ok('a tab / emoji / Hangul paste survives verbatim', pasteOut[1].same, pasteOut[1]);
  ok('a NUL in the pasted text does not break the box', pasteOut[2].out_len > 0, pasteOut[2]);
  await shot('06_paste_multiline');

  // and send the NUL one — the thing a real clipboard produces
  await setTa('');
  await clickSel('.ag-input');
  await send('Input.insertText', { text: '/run 04b_power_rabi q1 note=a\u0000b' });
  await clearToasts();
  before = await planCards();
  tEnter = Date.now();
  await press('Enter');
  let nfb = null;
  while (Date.now() - tEnter < 8000) { if ((await toasts()).length || (await planCards()) > before) { nfb = Date.now() - tEnter; break; } await sleep(30); }
  await sleep(1200);
  note('nul_send', { ms: nfb, toasts: (await toasts()).map(t => t.text.slice(0, 90)), cards: (await planCards()) - before, box: JSON.stringify(await taVal()).slice(0, 60) });
  ok('a NUL-bearing /run line is answered, not swallowed', nfb !== null, measures.nul_send);
  // and the feed still renders afterwards (the NaN lesson: one poisoned card killed the feed)
  await ev(`window.AgentPanel && window.AgentPanel.poll(true); 1`);
  await sleep(1500);
  const feedAlive = await ev(`(function(){var c=document.querySelector('.ag-cards'); return c? c.children.length : -1;})()`);
  note('feed_after_nul', feedAlive);
  ok('…and the feed still renders after it', feedAlive > 0, feedAlive);
  await shot('07_after_nul');

  /* ══ G. the newcomer read ═══════════════════════════════════════════ */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3500);
  const firstScreen = await ev(`(function(){
     var r=document.querySelector('.ag-root'); if(!r) return null;
     var vis=function(sel){var e=document.querySelector(sel); if(!e) return null; var b=e.getBoundingClientRect(); return {t:Math.round(b.top),h:Math.round(b.height),txt:(e.textContent||'').trim().slice(0,160)};};
     return {head:vis('.ag-head'), now:vis('.ag-now'), composer:vis('.ag-composer'),
             send:vis('.ag-send'), wire:vis('[data-ag-wire]'),
             below_fold: (document.querySelector('.ag-composer')||{getBoundingClientRect:function(){return{bottom:0}}}).getBoundingClientRect().bottom > window.innerHeight,
             text: (r.textContent||'').replace(/\\s+/g,' ').trim().slice(0,600)};})()`);
  note('first_screen', firstScreen);
  ok('the composer is on screen without scrolling', firstScreen && !firstScreen.below_fold, firstScreen && firstScreen.composer);
  ok('the screen says who acts next', firstScreen && /Start|start/.test(firstScreen.text), firstScreen && firstScreen.text.slice(0, 200));
  await shot('08_newcomer_first_screen');

  // what a person sees right after making a plan card: is the next actor obvious?
  await clickSel('.ag-input');
  await setTa('/run 06a_ramsey q1');
  await press('Enter');
  await sleep(2000);
  const cardRead = await ev(`(function(){var c=document.querySelector('.ag-cards [data-card^="plan:"]'); if(!c) return null;
     var btns=Array.prototype.map.call(c.querySelectorAll('button'), function(b){return b.textContent.trim();});
     return {txt:(c.textContent||'').replace(/\\s+/g,' ').trim().slice(0,300), btns:btns};})()`);
  note('plan_card_read', cardRead);
  ok('the plan card names the action a person must take', cardRead && cardRead.btns.join(' ').indexOf('Start') >= 0, cardRead);
  await shot('09_plan_card_read');

  /* ══ H. rapid typing / throughput ═══════════════════════════════════ */
  await setTa('');
  await clickSel('.ag-input');
  const tType = Date.now();
  await typeText('/run 02a_resonator_spectroscopy q1 q2 q3 num_shots=200', 0);
  await sleep(250);
  const typed = await taVal();
  note('ms_type_54_chars', Date.now() - tType);
  note('typed_value_ok', typed === '/run 02a_resonator_spectroscopy q1 q2 q3 num_shots=200');
  ok('55 characters typed at full speed arrive intact and in order',
     typed === '/run 02a_resonator_spectroscopy q1 q2 q3 num_shots=200', JSON.stringify(typed));
  await setTa('');

  fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors }, null, 1));
  const failed = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - failed.length) + '/' + results.length + '   console errors: ' + errors.length);
  failed.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  errors.slice(0, 15).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 200)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
