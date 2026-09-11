/* The Agent panel, stressed in real headless Chrome over CDP.
 *
 * AREA: /agent (the home mount) + the body-level float (#agent-popover), on a
 * real 20-qubit customer chip copy.
 *
 * HARD LIMIT honoured here in code, not only in intent: a fetch TRIPWIRE in the
 * page rejects every request that could spawn the user's own logged-in CLI
 * (/plans/<id>/start, /chat/start, /chat/send, /session/arm, /approvals/*), and
 * any attempt is reported as a finding. A `/run` line is the one lever used:
 * it makes a plan CARD deterministically, server-side, with no model involved.
 *
 * Modelled on tests/stress_drive.cjs (same CDP plumbing, same real-keystroke
 * dispatch, same whole-session error collection).
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9415';
const BASE = process.argv[4] || 'http://127.0.0.1:5415';
const SHOT = (n) => OUT.replace(/\.json$/, '_' + n + '.png');

const errors = [];
const results = [];
const shots = [];
const notes = [];
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
}

/* ---- server-side truth, read from node, never from the page ------------- */
async function feed() {
  const r = await fetch(BASE + '/api/agent/chat/cards?after=0');
  const t = await r.text();
  let j = null, parseErr = null;
  try { j = JSON.parse(t); } catch (e) { parseErr = String(e.message); }
  return { status: r.status, json: j, raw: t, parseErr: parseErr };
}
function sessionShape(f) {
  if (!f.json) return { unparseable: f.parseErr };
  return {
    session: f.json.session, file: f.json.file,
    now_state: (f.json.now || {}).state,
    runs: (f.json.live.runs || []).length,
    approvals: (f.json.live.approvals || []).length,
    plans: (f.json.live.plans || []).map(p => ({ id: p.id, status: p.status, started_at: p.started_at, started_by: p.started_by })),
    cards: (f.json.cards || []).length,
  };
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
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(150); }
    return await ev(expr);
  };
  // a REAL keystroke: keyDown with `text` only (a `char` event double-types)
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8, End: 35, Home: 36 };
  const press = async (key, mods) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (mods) p.modifiers = mods;
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key], modifiers: mods || 0 });
  };
  const shot = async (name) => {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    const p = SHOT(name);
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    shots.push(p);
  };
  const focusComposer = async (rootSel) => ev(
    `(function(){var r=document.querySelector(${JSON.stringify(rootSel)}); var t=r&&r.querySelector('.ag-input'); if(!t) return 0; t.focus(); t.value=''; t.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  const composerVal = async (rootSel) => ev(
    `(function(){var r=document.querySelector(${JSON.stringify(rootSel)}); var t=r&&r.querySelector('.ag-input'); return t? t.value : null;})()`);
  const typeIn = async (rootSel, text, fast) => {
    await focusComposer(rootSel);
    for (const ch of text) { await typeChar(ch); if (!fast) await sleep(6); }
  };
  const toasts = async () => ev(`(window.__toasts||[]).slice()`);
  const clearToasts = async () => ev(`window.__toasts=[]; 1`);
  const tripwire = async () => ev(`(window.__blocked||[]).slice()`);

  /* the tripwire + toast recorder, re-installed after every navigation */
  const INSTALL = `(function(){
    if (window.__agRig) return 1;
    window.__agRig = 1;
    window.__toasts = []; window.__blocked = [];
    var BAD = [/\\/plans\\/[^/]+\\/start/, /\\/chat\\/start/, /\\/chat\\/send/, /\\/session\\/arm\\b/, /\\/approvals\\/[^/]+\\/(approve|reject)/, /\\/session\\/stop/];
    var f = window.fetch;
    window.fetch = function(u, o){
      var url = (typeof u === 'string') ? u : (u && u.url) || '';
      var method = ((o && o.method) || (u && u.method) || 'GET').toUpperCase();
      if (method === 'POST' && BAD.some(function(rx){return rx.test(url);})) {
        window.__blocked.push({url: url, at: Date.now()});
        return Promise.resolve(new Response('{"ok":false,"error":"BLOCKED BY STRESS RIG"}', {status: 599, headers:{'Content-Type':'application/json'}}));
      }
      return f.apply(this, arguments);
    };
    var st = window.showToast;
    window.showToast = function(msg, lvl){ window.__toasts.push({msg: String(msg), lvl: lvl||'info'}); if (st) try { return st.apply(this, arguments); } catch(e){} };
    return 1;
  })()`;

  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ══ BEFORE: the server's own record ═════════════════════════════════ */
  const before = await feed();
  ok('BEFORE: the feed is valid JSON and nothing is running',
     before.status === 200 && before.json && !before.json.session && !before.json.file
     && (before.json.live.runs || []).length === 0, sessionShape(before));

  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3000);
  await ev(INSTALL);
  await ev(`window.confirm=function(){window.__confirmed=(window.__confirmed||0)+1; return false}; window.prompt=function(){return ''}; 1`);

  /* ══ 1. the home mounts ══════════════════════════════════════════════ */
  const mounted = await until(`!!document.querySelector('#agent-home .ag-root')`, 12000);
  ok('/agent mounts the panel into #agent-home', mounted);
  const skel = await ev(`(function(){var r=document.querySelector('#agent-home .ag-root'); if(!r) return null;
    return {now:!!r.querySelector('.ag-now'), cards:!!r.querySelector('.ag-cards'), form:!!r.querySelector('.ag-composer'),
            input:!!r.querySelector('.ag-input'), send:!!r.querySelector('.ag-send'), backend:!!r.querySelector('.ag-backend'),
            actor:!!r.querySelector('.ag-actor'), presets:r.querySelectorAll('.ag-preset').length,
            chip:(r.querySelector('.ag-chip')||{}).textContent, qubits:(r.querySelector('.ag-qubits')||{}).textContent};})()`);
  ok('…with the status strip, the feed, the composer and 3 presets',
     skel && skel.now && skel.cards && skel.form && skel.input && skel.send && skel.backend && skel.actor && skel.presets === 3, skel);
  ok('…and the head names THIS chip and its 20 qubits',
     skel && /chip_5415/.test(String(skel.chip)) && /20 qubits/.test(String(skel.qubits)), skel);
  ok('the loading placeholder is gone once mounted',
     !(await ev(`!!document.querySelector('#agent-home .ag-loading')`)));
  ok('the dry-run note renders BESIDE #agent-home, not inside it',
     await ev(`!!document.getElementById('ag-dryrun-note') && !document.querySelector('#agent-home #ag-dryrun-note')`),
     await ev(`(document.getElementById('ag-dryrun-note')||{}).textContent`));
  const nowTxt = await ev(`(document.querySelector('#agent-home .ag-now-main')||{}).textContent || ''`);
  ok('the strip says there is no agent session on this chip',
     /no agent session on this chip/.test(nowTxt), nowTxt.slice(0, 200));
  ok('…and no Arm / Stop / End session door is offered with no session',
     await ev(`(function(){var a=document.querySelector('#agent-home .ag-now-acts'); if(!a) return null; return a.querySelectorAll('.ag-arm, .ag-stop').length;})()`) === 0,
     await ev(`(document.querySelector('#agent-home .ag-now-acts')||{}).textContent`));
  await shot('01_home');

  /* ══ 2. the composer: Enter sends, Shift+Enter breaks a line ═════════ */
  const R = '#agent-home';
  // 2a. Enter on an EMPTY field
  let f0 = await feed();
  await focusComposer(R);
  for (let i = 0; i < 3; i++) { await press('Enter'); await sleep(150); }
  await sleep(700);
  let f1 = await feed();
  ok('Enter three times on an empty composer starts nothing and adds no card',
     f1.json && f1.json.cards.length === f0.json.cards.length
     && f1.json.live.plans.length === f0.json.live.plans.length && !f1.json.session,
     { before: sessionShape(f0), after: sessionShape(f1) });
  ok('…and the box is still enabled afterwards',
     await ev(`!document.querySelector('#agent-home .ag-input').disabled`));

  // 2b. whitespace only
  await focusComposer(R);
  for (const c of '   ') await typeChar(c);
  await press('Enter'); await sleep(700);
  f1 = await feed();
  ok('Enter on whitespace-only starts nothing',
     f1.json && f1.json.cards.length === f0.json.cards.length && !f1.json.session, sessionShape(f1));

  // 2c. Shift+Enter inserts a newline and does NOT send
  await focusComposer(R);
  for (const c of '/run 11_power_rabi') await typeChar(c);
  await press('Enter', 8);            // 8 = Shift
  await sleep(120);
  for (const c of ' q1') await typeChar(c);
  await sleep(200);
  let v = await composerVal(R);
  ok('Shift+Enter breaks a line instead of sending',
     typeof v === 'string' && v.indexOf('\n') >= 0 && /q1/.test(v), JSON.stringify(v));
  f1 = await feed();
  ok('…and Shift+Enter alone created no plan',
     f1.json && f1.json.live.plans.length === f0.json.live.plans.length, sessionShape(f1));
  // clear
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); t.value=''; t.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);

  // 2d. the composer grows, then scrolls inside itself
  const grew = await ev(`(function(){var t=document.querySelector('#agent-home .ag-input');
    var h0=t.getBoundingClientRect().height;
    t.value=Array(30).join('line\\n'); AgentPanel.grow(t);
    var h1=t.getBoundingClientRect().height, ovf=t.style.overflowY;
    t.value=''; AgentPanel.grow(t);
    var h2=t.getBoundingClientRect().height;
    return {h0:Math.round(h0), h1:Math.round(h1), ovf:ovf, h2:Math.round(h2)};})()`);
  ok('the composer grows with the draft, caps, and shrinks back',
     grew && grew.h1 > grew.h0 && grew.ovf === 'auto' && grew.h1 < 260 && Math.abs(grew.h2 - grew.h0) <= 4, grew);

  /* ══ 3. /run lines — the one allowed lever ═══════════════════════════ */
  const runLine = async (line, fast) => {
    await clearToasts();
    await typeIn(R, line, fast);
    await press('Enter');
    await sleep(1400);
    return { val: await composerVal(R), toasts: await toasts() };
  };

  f0 = await feed();
  let res = await runLine('/run');
  f1 = await feed();
  ok('`/run` with nothing after it is refused and the line is KEPT',
     res.val === '/run' && res.toasts.some(t => /usage|could not/i.test(t.msg))
     && f1.json.live.plans.length === f0.json.live.plans.length, res);

  res = await runLine('/run 11_power_rabi');
  f1 = await feed();
  ok('`/run <node>` with no targets makes a 0-target plan or refuses — either way nothing starts',
     f1.json.live.plans.every(p => p.status === 'draft'), { composer: res.val, toasts: res.toasts, plans: sessionShape(f1).plans });
  notes.push('`/run 11_power_rabi` (no targets) -> ' + (res.val === '' ? 'ACCEPTED as a 0-target plan card' : 'refused: ' + JSON.stringify(res.toasts)));

  res = await runLine('/run not_a_real_node q1');
  f1 = await feed();
  ok('a bad node name is refused NOW, names the real ones, and keeps the line',
     res.val === '/run not_a_real_node q1' && res.toasts.some(t => /no node named/i.test(t.msg)), res);

  res = await runLine('/run 11_power_rabi qZZZ');
  ok('a bogus qubit is refused and the line is kept',
     res.val === '/run 11_power_rabi qZZZ' && res.toasts.some(t => /unknown targets/i.test(t.msg)), res);

  for (const weird of ['/run 11_power_rabi "q1"', '/run 11_power_rabi q1\\x', '/run 11_power_rabi 큐빗', '/run 11_power_rabi 🙂', '/run 11_power_rabi -1', '/run 11_power_rabi 0']) {
    res = await runLine(weird, true);
    ok('a hostile target `' + weird.replace('/run 11_power_rabi ', '') + '` is refused without throwing',
       res.val === weird && res.toasts.length > 0, res);
  }

  // the real one, twice in a row
  const nPlansBefore = (await feed()).json.live.plans.length;
  res = await runLine('/run 11_power_rabi q1');
  ok('a valid /run clears the box and says a card is ready',
     res.val === '' && res.toasts.some(t => /plan card ready/i.test(t.msg)), res);
  res = await runLine('/run 11_power_rabi q1');
  await sleep(1500);
  f1 = await feed();
  ok('the SAME /run line twice makes two DRAFT cards and starts neither',
     f1.json.live.plans.length === nPlansBefore + 2 && f1.json.live.plans.slice(-2).every(p => p.status === 'draft' && !p.started_at),
     sessionShape(f1).plans);

  const cardDom = await until(`document.querySelectorAll('#agent-home [data-card^="plan:"]').length >= 2`, 8000);
  ok('…and both cards are in the feed', cardDom,
     await ev(`document.querySelectorAll('#agent-home [data-card^="plan:"]').length`));
  const planCard = await ev(`(function(){var all=document.querySelectorAll('#agent-home [data-card^="plan:"]'); var c=all[all.length-1]; if(!c) return null;
    return {st:(c.querySelector('.ag-plan-st')||{}).textContent, start:!!c.querySelector('.ag-start'), cancel:!!c.querySelector('.ag-cancel'),
            mode:!!c.querySelector('.ag-mode select'), may:c.querySelectorAll('.ag-may li').length,
            open:!!(c.querySelector('details.ag-may-wrap')||{}).open, steps:c.querySelectorAll('.ag-step').length};})()`);
  ok('a draft card shows Start, Cancel, a mode picker and what may change',
     planCard && planCard.st === 'draft' && planCard.start && planCard.cancel && planCard.mode && planCard.may >= 1 && planCard.open, planCard);
  await shot('02_plan_cards');

  /* ══ 4. expanding / collapsing a card ════════════════════════════════ */
  const toggles = [];
  for (let i = 0; i < 6; i++) {
    await ev(`(function(){var a=document.querySelectorAll('#agent-home [data-card^="plan:"] details.ag-may-wrap'); var d=a[a.length-1]; if(d) d.open=!d.open; return 1;})()`);
    await sleep(120);
    toggles.push(await ev(`(function(){var a=document.querySelectorAll('#agent-home [data-card^="plan:"] details.ag-may-wrap'); return (a[a.length-1]||{}).open;})()`));
  }
  ok('the "values that may change" section opens and closes six times cleanly',
     toggles.join(',') === 'false,true,false,true,false,true', toggles);
  // and a poll must not reset it under the reader
  await ev(`(function(){var a=document.querySelectorAll('#agent-home [data-card^="plan:"] details.ag-may-wrap'); a[a.length-1].open=false; return 1;})()`);
  await ev(`AgentPanel.poll(true)`); await sleep(1500);
  ok('a poll does not re-open a section the reader closed',
     (await ev(`(function(){var a=document.querySelectorAll('#agent-home [data-card^="plan:"] details.ag-may-wrap'); return (a[a.length-1]||{}).open;})()`)) === false);

  /* ══ 5. the presets row — fills a draft, starts NOTHING ══════════════ */
  f0 = await feed();
  const presetVals = [];
  for (let i = 0; i < 3; i++) {
    await ev(`(function(){var b=document.querySelectorAll('#agent-home .ag-preset')[${i}]; if(b) b.click(); return 1;})()`);
    await sleep(200);
    presetVals.push((await composerVal(R) || '').slice(0, 40));
  }
  ok('each of the three presets fills the draft', presetVals.every(x => x.length > 20), presetVals);
  // the same preset three times must not compound
  await ev(`(function(){var b=document.querySelectorAll('#agent-home .ag-preset')[0]; for(var i=0;i<3;i++) b.click(); return 1;})()`);
  await sleep(300);
  const pv = await composerVal(R);
  ok('clicking one preset three times leaves ONE draft, not three',
     (pv.match(/1Q bringup on/g) || []).length === 1, pv.slice(0, 80));
  const sel = await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); return {s:t.selectionStart, e:t.selectionEnd, picked:t.value.slice(t.selectionStart,t.selectionEnd)};})()`);
  ok('…with <targets> selected so the next keystroke replaces it', sel && sel.picked === '<targets>', sel);
  await sleep(900);
  f1 = await feed();
  ok('a preset started NOTHING (no session, no new plan, no run)',
     !f1.json.session && !f1.json.file && f1.json.live.plans.length === f0.json.live.plans.length
     && f1.json.live.runs.length === 0, sessionShape(f1));
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); t.value=''; t.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);

  /* ══ 6. the observer toggle ══════════════════════════════════════════ */
  const doors = async () => ev(`(function(){var r=document.querySelector('#agent-home'); if(!r) return null;
    return {start:r.querySelectorAll('.ag-start').length, cancel:r.querySelectorAll('.ag-cancel').length,
            arm:r.querySelectorAll('.ag-arm').length, stop:r.querySelectorAll('.ag-stop').length,
            approve:r.querySelectorAll('.ag-approve').length,
            modeDisabled:Array.prototype.every.call(r.querySelectorAll('.ag-mode select'), function(s){return s.disabled;}),
            observing:!!r.querySelector('.ag-observing'),
            checked:(r.querySelector('.ag-observer input')||{}).checked};})()`);
  const setObs = async (on) => { await ev(`(function(){var c=document.querySelector('#agent-home .ag-observer input'); if(c.checked!==${on}){c.click();} return 1;})()`); await sleep(400); };
  const d0 = await doors();
  await setObs(true);
  const d1 = await doors();
  ok('observer ON hides Start / Cancel on THIS window and disables the mode picker',
     d1 && d1.start === 0 && d1.cancel === 0 && d1.arm === 0 && d1.stop === 0 && d1.approve === 0 && d1.modeDisabled && d1.observing && d1.checked,
     { off: d0, on: d1 });
  ok('…and it is remembered in localStorage',
     (await ev(`localStorage.getItem('quam_agent_observer')`)) === '1');
  await setObs(false);
  const d2 = await doors();
  ok('observer OFF brings the doors back', d2 && d2.start === d0.start && d2.start > 0, { off: d2 });
  // flipped repeatedly
  const flips = [];
  for (let i = 0; i < 10; i++) {
    await ev(`document.querySelector('#agent-home .ag-observer input').click(); 1`);
    await sleep(140);
    const dd = await doors();
    flips.push((dd.checked ? 'on:' : 'off:') + dd.start);
  }
  ok('ten rapid flips stay consistent (on => 0 Start, off => the cards\' Start back)',
     flips.every(x => (x.startsWith('on:') ? x === 'on:0' : /^off:[1-9]/.test(x))), flips);
  await setObs(false);
  await shot('03_observer_off');
  f1 = await feed();
  ok('…and no flip started anything', !f1.json.session && f1.json.live.runs.length === 0, sessionShape(f1));

  /* ══ 7. the actor name field ═════════════════════════════════════════ */
  const actorSet = async (txt) => {
    await ev(`(function(){var a=document.querySelector('#agent-home .ag-actor'); a.focus(); a.value=''; a.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    await ev(`(function(){var a=document.querySelector('#agent-home .ag-actor'); a.value=${JSON.stringify(txt)}; a.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    await sleep(200);
    return ev(`localStorage.getItem('quam_actor_name')`);
  };
  ok('an empty actor name clears the record', (await actorSet('')) === '');
  ok('a 200-character name is stored verbatim (trimmed only)', (await actorSet('N'.repeat(200))) === 'N'.repeat(200));
  ok('a quote in the name does not break the datalist',
     (await actorSet('a"b\'c')) === 'a"b\'c'
     && (await ev(`document.querySelectorAll('#agent-home #ag-actor-list option').length`)) >= 1,
     await ev(`(document.querySelector('#agent-home #ag-actor-list')||{}).innerHTML`));
  ok('…and it is ESCAPED, not injected',
     await ev(`!/<script|<img/i.test((document.querySelector('#agent-home #ag-actor-list')||{}).innerHTML||'')`));
  await actorSet('<img src=x onerror="window.__xss=1">');
  await sleep(300);
  ok('an HTML payload in the actor name does not execute',
     !(await ev(`!!window.__xss`)), await ev(`(document.querySelector('#agent-home #ag-actor-list')||{}).innerHTML`));
  ok('Hangul is accepted by the field and stored', (await actorSet('정경훈')) === '정경훈');

  /* ── 7b. …but does the PANEL still work with that name? ─────────────
     api() puts the actor name in an X-SM-Actor request header. A header value
     must be ISO-8859-1. Measured here, twice: once through the panel's own
     poll, once with a bare fetch carrying the same header. */
  const actorProbe = async (name) => {
    await ev(`AgentPanel.setActor(${JSON.stringify(name)}); 1`);
    const viaPanel = await ev(`AgentPanel.poll(true).then(function(){return JSON.stringify({after:AgentPanel._state.after, unreachable:AgentPanel._state.unreachable});})`);
    const viaFetch = await ev(`fetch('/api/agent/ping',{headers:{'X-SM-Actor':${JSON.stringify(name)}}}).then(function(r){return 'status '+r.status;},function(e){return 'REJECT '+e.name+': '+e.message;})`);
    const banner = await ev(`(function(){var n=document.querySelector('#agent-home .ag-unreachable'); return n? n.textContent : null;})()`);
    return { name: name.slice(0, 24), viaPanel: JSON.parse(viaPanel), viaFetch: viaFetch, banner: banner };
  };
  const pAscii = await actorProbe('kyunghoon');
  ok('an ASCII actor name leaves the panel reachable', pAscii.viaPanel.unreachable === false, pAscii);
  const pLong = await actorProbe('N'.repeat(200));
  ok('a 200-character ASCII actor name leaves the panel reachable', pLong.viaPanel.unreachable === false, pLong);
  const pQuote = await actorProbe('a"b\'c\\d');
  ok('a quote/backslash actor name leaves the panel reachable', pQuote.viaPanel.unreachable === false, pQuote);
  const pHan = await actorProbe('정경훈');
  ok('a HANGUL actor name leaves the panel reachable',
     pHan.viaPanel.unreachable === false, pHan);
  const pEmoji = await actorProbe('kyunghoon 🙂');
  ok('an emoji in the actor name leaves the panel reachable',
     pEmoji.viaPanel.unreachable === false, pEmoji);
  // if it broke: does a /run still reach the server?
  if (pHan.viaPanel.unreachable) {
    await ev(`AgentPanel.setActor('정경훈'); 1`);
    const nBefore = (await feed()).json.live.plans.length;
    await clearToasts();
    await typeIn(R, '/run 11_power_rabi q4', true);
    await press('Enter');
    await sleep(1800);
    const nAfter = (await feed()).json.live.plans.length;
    ok('…and with that name a valid /run still makes its plan card',
       nAfter === nBefore + 1, { plansBefore: nBefore, plansAfter: nAfter, toasts: await toasts(), composer: await composerVal(R) });
    await shot('03b_hangul_actor');
  }
  // clear it, and check the panel recovers without a reload
  await ev(`AgentPanel.setActor(''); 1`);
  const recovered = await ev(`AgentPanel.poll(true).then(function(){return JSON.stringify({unreachable:AgentPanel._state.unreachable, cards:document.querySelectorAll('#agent-home .ag-cards > *').length});})`);
  ok('clearing the actor name brings the panel back without a reload',
     JSON.parse(recovered).unreachable === false, recovered);

  // persistence across a real reload (with a name that works)
  await ev(`AgentPanel.setActor('kyunghoon'); 1`);
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3500);
  await ev(INSTALL);
  await until(`!!document.querySelector('#agent-home .ag-actor')`, 10000);
  ok('the actor name is still in the box after a full page load',
     (await ev(`(document.querySelector('#agent-home .ag-actor')||{}).value`)) === 'kyunghoon',
     await ev(`(document.querySelector('#agent-home .ag-actor')||{}).value`));
  const recents = await ev(`localStorage.getItem('quam_actor_recents')`);
  ok('…and the recents list is capped at 8', (JSON.parse(recents || '[]')).length <= 8, recents);
  await ev(`AgentPanel.setActor(''); 1`);
  await sleep(300);

  /* ══ 8. a 5,000-character LOCAL command ══════════════════════════════ */
  f0 = await feed();
  await clearToasts();
  const big = '/run ' + 'z'.repeat(5000);
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); t.focus(); t.value=${JSON.stringify(big)}; t.dispatchEvent(new Event('input',{bubbles:true})); AgentPanel.grow(t); return 1;})()`);
  const tBig = Date.now();
  await press('Enter');
  await sleep(2500);
  const bigMs = Date.now() - tBig;
  f1 = await feed();
  ok('a 5,000-character /run line is refused locally, keeps the text, and starts nothing',
     (await composerVal(R)) === big && !f1.json.session && f1.json.live.plans.length === f0.json.live.plans.length,
     { ms: bigMs, toasts: (await toasts()).map(t => t.msg.slice(0, 90)) });
  ok('…and the composer is re-enabled, not left dead',
     !(await ev(`document.querySelector('#agent-home .ag-input').disabled`)));
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); t.value=''; t.dispatchEvent(new Event('input',{bubbles:true})); AgentPanel.grow(t); return 1;})()`);

  /* ══ 9. the card feed under injected traffic (no model involved) ═════
     POST /api/agent/event is the hook's own deterministic door: it appends a
     chat event. Used here to make the renderer face a long answer, a run of
     tool calls, a failure, and cards arriving OUT of time order.            */
  const chip = before.json.chip;
  let nBase = 9000;
  const post = async (body) => {
    const r = await fetch(BASE + '/api/agent/event', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Origin': BASE },
      body: JSON.stringify(body)
    });
    return r.status;
  };
  const now = Math.floor(Date.now() / 1000);
  const statuses = [];
  // `n` is the feed CURSOR, so it must rise in posting order (the hook's own
  // counter does). What is out of order here is TIME: the card posted FIRST
  // carries the LATEST ts, so it must end up LAST in the feed.
  statuses.push(await post({ origin: 'chat', chip: chip, n: nBase + 1, ts: now + 40, hook_event_name: 'Text', backend: 'claude', text: 'LATER answer' }));
  statuses.push(await post({ origin: 'chat', chip: chip, n: nBase + 2, ts: now + 1, hook_event_name: 'User', who: 'stress', text: 'earlier line <b>not html</b>' }));
  statuses.push(await post({ origin: 'chat', chip: chip, n: nBase + 3, ts: now + 2, hook_event_name: 'Text', backend: 'claude', text: ('A very long answer. '.repeat(180)) }));
  for (let i = 0; i < 5; i++) {
    statuses.push(await post({ origin: 'chat', chip: chip, n: nBase + 4 + i, ts: now + 3 + i, hook_event_name: 'PreToolUse', tool_name: 'mcp__sm__state', summary: '{"path":"qubits.q' + (i + 1) + '"}' }));
  }
  statuses.push(await post({ origin: 'chat', chip: chip, n: nBase + 9, ts: now + 9, hook_event_name: 'PostToolUseFailure', tool_name: 'mcp__sm__run_node', summary: '{}', error: 'refused: no_env' }));
  ok('the hook door accepted the injected chat events', statuses.every(s => s === 200), statuses);
  ok('(pre-condition) the panel is reachable before the feed checks',
     (await ev(`AgentPanel._state.unreachable`)) === false);
  await ev(`AgentPanel.poll(true)`);
  await sleep(2000);
  const order = await ev(`Array.prototype.map.call(document.querySelectorAll('#agent-home .ag-cards > [data-card]'), function(e){return {c:e.getAttribute('data-card'), t:Number(e.getAttribute('data-ts'))||0};})`);
  ok('cards sit in TIME order whatever order they arrived in',
     order.length > 6 && order.every((x, i) => i === 0 || x.t >= order[i - 1].t)
     && order[order.length - 1].c === 'answer:' + (nBase + 1), order);
  const grp = await ev(`(function(){var h=document.querySelector('#agent-home .ag-cards');
    return {groups:h.querySelectorAll('.ag-toolgroup').length, sum:(h.querySelector('.ag-toolgroup-sum')||{}).textContent,
            hidden:h.querySelectorAll('.ag-in-group[hidden]').length, failed:h.querySelectorAll('.ag-failed').length};})()`);
  ok('five consecutive tool calls fold into ONE row, and the failure stands alone',
     grp && grp.groups === 1 && /5 tool calls/.test(String(grp.sum)) && grp.hidden === 5 && grp.failed >= 1, grp);
  // opened and closed three times
  const gflips = [];
  for (let i = 0; i < 3; i++) {
    await ev(`(function(){var s=document.querySelector('#agent-home .ag-toolgroup summary'); if(s) s.click(); return 1;})()`);
    await sleep(200);
    gflips.push(await ev(`(function(){var h=document.querySelector('#agent-home .ag-cards'); var g=document.querySelector('#agent-home .ag-toolgroup');
      return {open:!!(g&&g.open), hidden:h.querySelectorAll('.ag-in-group[hidden]').length};})()`));
  }
  ok('the tool group opens and closes and its members follow',
     gflips.length === 3 && gflips.every(g => g.open ? g.hidden === 0 : g.hidden === 5), gflips);
  const clamp = await ev(`(function(){var c=document.querySelector('#agent-home .ag-answer .ag-md.ag-clamp'); if(!c) return null;
    var card=c.closest('.ag-card'); var b=card.querySelector('.ag-more');
    return {clamped:true, cut:c.scrollHeight>c.clientHeight+4, more:!!b, label:b?b.textContent:null};})()`);
  ok('a long answer is clamped behind a real "show more"', clamp && clamp.cut && clamp.more, clamp);
  if (clamp) {
    await ev(`(function(){var b=document.querySelector('#agent-home .ag-more'); b.click(); return 1;})()`);
    await sleep(200);
    const openLbl = await ev(`(document.querySelector('#agent-home .ag-more')||{}).textContent`);
    await ev(`(function(){var b=document.querySelector('#agent-home .ag-more'); b.click(); return 1;})()`);
    await sleep(200);
    const shutLbl = await ev(`(document.querySelector('#agent-home .ag-more')||{}).textContent`);
    ok('show more / show less round-trips', openLbl === 'show less' && shutLbl === 'show more', { openLbl, shutLbl });
  }
  ok('a user line with tags in it is escaped, not rendered as HTML',
     await ev(`(function(){var u=document.querySelector('#agent-home .ag-user-text'); return !!u && u.textContent.indexOf('<b>')>=0 && !u.querySelector('b');})()`));
  await shot('04_feed');

  /* ══ 10. scroll the feed to both ends, then a poll ═══════════════════ */
  const scrollTest = await ev(`(async function(){
    var h=document.querySelector('#agent-home .ag-cards');
    if(!h) return null;
    var max=h.scrollHeight-h.clientHeight;
    h.scrollTop=0; await new Promise(r=>setTimeout(r,150));
    var atTop=h.scrollTop;
    h.scrollTop=max; await new Promise(r=>setTimeout(r,150));
    var atBot=h.scrollTop;
    h.scrollTop=0; await new Promise(r=>setTimeout(r,150));
    return {max:Math.round(max), atTop:Math.round(atTop), atBot:Math.round(atBot), now:Math.round(h.scrollTop)};})()`);
  if (scrollTest && scrollTest.max > 20) {
    await ev(`AgentPanel.poll(true)`); await sleep(1800);
    const after = await ev(`Math.round(document.querySelector('#agent-home .ag-cards').scrollTop)`);
    ok('a poll does not drag a reader parked at the top down to the bottom',
       after < 40, { scrolled: scrollTest, afterPoll: after });
  } else {
    notes.push('feed not tall enough to test autoscroll (scrollable height ' + JSON.stringify(scrollTest) + ')');
  }

  /* ══ 11. the wiring strip ════════════════════════════════════════════ */
  const wire = await until(`(function(){var w=document.querySelector('#table-pane [data-ag-wire], main [data-ag-wire]') || document.querySelector('[data-ag-wire]:not(.ag-wire-compact)');
    if(!w) return null; var b=w.querySelector('.ag-wire-badge');
    return {badge:b?b.textContent:null, text:w.textContent, clis:w.querySelectorAll('.ag-wire-cli').length, wraps:w.querySelectorAll('.ag-wire-clis').length};})()`, 15000);
  ok('the wiring strip resolves to a real state, not CHECKING for ever',
     wire && ['CONNECTED', 'NOT CONNECTED', 'NO CLI'].indexOf(wire.badge) >= 0, wire);
  ok('…and it NEVER claims anyone is logged in',
     wire && !/logged ?in|log ?in|authenticated|signed ?in/i.test(wire.text), wire && wire.text);
  ok('…and one line per backend, not more', wire && wire.wraps <= 1 && wire.clis <= 3, wire);
  // 25 repaints
  const W0 = wire.clis;
  for (let i = 0; i < 25; i++) await ev(`AgentPanel.wirePaint(); 1`);
  await sleep(300);
  const wireAfter = await ev(`(function(){var w=document.querySelector('[data-ag-wire]:not(.ag-wire-compact)');
    return w? {clis:w.querySelectorAll('.ag-wire-cli').length, wraps:w.querySelectorAll('.ag-wire-clis').length, text:w.textContent.replace(/\\s+/g,' ').trim().slice(0,160)} : null;})()`);
  ok('25 repaints do not multiply the backend lines',
     wireAfter && wireAfter.clis === W0 && wireAfter.wraps <= 1, { before: W0, after: wireAfter });
  // the ? popover, four clicks
  const pops = [];
  for (let i = 0; i < 4; i++) {
    await ev(`(function(){var b=document.querySelector('[data-ag-wire]:not(.ag-wire-compact) .ag-wire-help'); if(b) b.click(); return 1;})()`);
    await sleep(220);
    pops.push(await ev(`document.querySelectorAll('#ag-wire-help-pop').length`));
  }
  ok('the ? popover toggles and never stacks', pops.every(n => n <= 1) && pops.some(n => n === 1), pops);
  const helpTxt = await ev(`(document.getElementById('ag-wire-help-pop')||{}).textContent||''`);
  if (helpTxt) ok('…and it says SM never sees your credentials', /never sees your credentials/.test(helpTxt), helpTxt.slice(0, 160));
  await ev(`(function(){var p=document.getElementById('ag-wire-help-pop'); if(p&&p.parentNode) p.parentNode.removeChild(p); return 1;})()`);

  /* ══ 12. resize ══════════════════════════════════════════════════════ */
  for (const w of [520, 900, 1500, 700, 1200]) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: 900, deviceScaleFactor: 1, mobile: false });
    await sleep(180);
  }
  await send('Emulation.setDeviceMetricsOverride', { width: 520, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(800);
  const narrow = await ev(`(function(){var r=document.querySelector('#agent-home .ag-root'); if(!r) return null;
    var b=document.body; return {overflowX: b.scrollWidth > window.innerWidth + 2, w:Math.round(r.getBoundingClientRect().width),
      composerVisible: !!r.querySelector('.ag-input') && r.querySelector('.ag-input').getBoundingClientRect().width > 60};})()`);
  ok('at 520px the panel does not push the page into horizontal scroll',
     narrow && !narrow.overflowX && narrow.composerVisible, narrow);
  await shot('05_narrow');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(600);

  /* ══ 13. the float, from three different pages ═══════════════════════ */
  // on /agent itself the float must NOT open (the home mount is the panel)
  await ev(`window.toggleAgentPanel(); 1`);
  await sleep(500);
  ok('on /agent the float stays shut and the composer takes the focus instead',
     (await ev(`document.getElementById('agent-popover').classList.contains('agent-hidden')`)) === true
     && (await ev(`document.activeElement === document.querySelector('#agent-home .ag-input')`)) === true);

  const floatOn = async (url, label) => {
    await send('Page.navigate', { url: BASE + url });
    await sleep(4200);
    await ev(INSTALL);
    const opens = [];
    for (let i = 0; i < 3; i++) {
      await ev(`window.toggleAgentPanel(); 1`);
      await sleep(700);
      opens.push(await ev(`(function(){var p=document.getElementById('agent-popover'); if(!p) return null;
        var w=p.querySelector('[data-ag-wire]');
        return {open:!p.classList.contains('agent-hidden'), mounts:p.querySelectorAll('.ag-root').length,
                cards:p.querySelectorAll('.ag-cards > [data-card]').length,
                clis:w?w.querySelectorAll('.ag-wire-cli').length:null, wraps:w?w.querySelectorAll('.ag-wire-clis').length:null,
                compact:w?w.classList.contains('ag-wire-compact'):null,
                inputs:p.querySelectorAll('.ag-input').length};})()`));
    }
    return opens;
  };
  for (const [url, label] of [['/bulk', 'Live State Edit'], ['/explorer', 'Json Tree View'], ['/journal', 'Calibration log']]) {
    const o = await floatOn(url, label);
    ok('the float opens on ' + url + ' (' + label + ') and closes again',
       o[0] && o[0].open && o[1] && !o[1].open && o[2] && o[2].open, o.map(x => x && x.open));
    ok('…with exactly one mount and one composer after three cycles',
       o[2] && o[2].mounts === 1 && o[2].inputs === 1, o[2]);
    ok('…and the compact strip does not multiply its backend lines',
       o[2] && o[2].compact === true && o[2].wraps <= 1 && o[2].clis <= 3, o[2]);
    ok('…and the float carries the SAME feed (the cards are there)',
       o[2] && o[2].cards >= 3, o[2] && o[2].cards);
    if (url === '/bulk') await shot('06_float_bulk');
  }
  // leave it open and check the strip never claims a login in the float either
  const fw = await ev(`(function(){var w=document.querySelector('#agent-popover [data-ag-wire]'); return w? w.textContent : null;})()`);
  ok('the float strip never claims a login either',
     fw !== null && !/logged ?in|authenticated|signed ?in/i.test(fw), fw);

  // two mounts at once: the float is open, then htmx-navigate to the Agent home
  await ev(`(function(){var p=document.getElementById('agent-popover'); if(p.classList.contains('agent-hidden')) window.toggleAgentPanel(); return 1;})()`);
  await sleep(500);
  await ev(`(function(){var a=document.createElement('a'); a.href='/agent'; a.setAttribute('hx-get','/agent'); a.setAttribute('hx-target','#table-pane'); a.setAttribute('hx-push-url','true');
    document.body.appendChild(a); window.htmx.process(a); a.click(); return 1;})()`);
  await sleep(4500);
  const two = await ev(`(function(){return {home:document.querySelectorAll('#agent-home .ag-root').length,
    float:document.querySelectorAll('#agent-popover .ag-root').length,
    mounts:(AgentPanel._state.mounts||[]).length,
    homeCards:document.querySelectorAll('#agent-home .ag-cards > [data-card]').length,
    floatCards:document.querySelectorAll('#agent-popover .ag-cards > [data-card]').length};})()`);
  ok('with the float open AND the home mounted, both feeds render the same cards',
     two && two.home === 1 && two.float === 1 && two.homeCards >= 3 && two.homeCards === two.floatCards, two);
  await shot('07_two_mounts');
  f1 = await feed();
  ok('…and none of the float work started anything',
     !f1.json.session && !f1.json.file && f1.json.live.runs.length === 0
     && f1.json.live.plans.every(p => p.status === 'draft'), sessionShape(f1));

  /* ══ 14. the tripwire ════════════════════════════════════════════════ */
  const blocked = await tripwire();
  ok('NOTHING in this run even attempted to start a CLI session', blocked.length === 0, blocked);

  /* ══ 15. LAST: a numeric edge value in a /run param ══════════════════
     Done last on purpose: if it poisons the feed, everything after it is
     untrustworthy. Reproduced twice (NaN through the browser, 1e999 through
     a plain request). */
  const poison = { steps: [] };
  const feedOkBefore = await feed();
  poison.steps.push({ what: 'feed before', parseErr: feedOkBefore.parseErr });
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(4000);
  await ev(INSTALL);
  await until(`!!document.querySelector('#agent-home .ag-root')`, 10000);
  const cardsBefore = await ev(`document.querySelectorAll('#agent-home .ag-cards > [data-card]').length`);
  await clearToasts();
  await typeIn(R, '/run 12_ramsey q2 detuning=NaN', true);
  await press('Enter');
  await sleep(2500);
  poison.afterNaN = { composer: await composerVal(R), toasts: await toasts() };
  const fNaN = await feed();
  poison.steps.push({ what: 'feed after NaN', status: fNaN.status, parseErr: fNaN.parseErr, snippet: fNaN.parseErr ? fNaN.raw.slice(Math.max(0, fNaN.raw.indexOf('NaN') - 60), fNaN.raw.indexOf('NaN') + 20) : null });
  ok('a /run param of NaN does not make the feed unparseable',
     !fNaN.parseErr, poison.steps[poison.steps.length - 1]);
  // what the PAGE now sees
  const pageSees = await ev(`(async function(){
    var r = await fetch('/api/agent/chat/cards?after=0');
    var t = await r.text();
    var parsed = null, err = null;
    try { parsed = JSON.parse(t); } catch(e){ err = String(e.message); }
    return {status:r.status, parsed:!!parsed, err:err};})()`);
  ok('…and the browser can still parse the feed it polls',
     pageSees && pageSees.parsed === true, pageSees);
  // does the panel keep working? inject a brand-new card and see if it lands
  await post({ origin: 'chat', chip: chip, n: nBase + 40, ts: Math.floor(Date.now() / 1000) + 100, hook_event_name: 'User', who: 'stress', text: 'CANARY after the NaN plan' });
  await ev(`AgentPanel.poll(true)`);
  await sleep(2500);
  const canary = await ev(`(function(){var h=document.querySelector('#agent-home .ag-cards'); return {n:h.children.length, canary:/CANARY after the NaN plan/.test(h.textContent)};})()`);
  ok('…and a NEW card still reaches the panel afterwards (the feed is not dead)',
     canary && canary.canary === true, { cardsBefore: cardsBefore, after: canary });
  await shot('08_after_nan');
  poison.canary = canary;
  poison.pageSees = pageSees;

  // second, independent reproduction: 1e999 through a plain request
  const r2 = await fetch(BASE + '/api/agent/plans', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'Origin': BASE },
    body: JSON.stringify({ run_line: '/run 12_ramsey q3 span=1e999' })
  });
  const r2t = await r2.text();
  let r2parse = null; try { JSON.parse(r2t); } catch (e) { r2parse = String(e.message); }
  const f1e = await feed();
  poison.repro2 = { postStatus: r2.status, postParseErr: r2parse, feedParseErr: f1e.parseErr };
  ok('REPRO 2 — a /run param of 1e999 also does not make the feed unparseable',
     !f1e.parseErr && !r2parse, poison.repro2);

  /* ══ AFTER ═══════════════════════════════════════════════════════════ */
  const after = await feed();
  ok('AFTER: still no session, no run, and every plan is still a draft',
     after.json ? (!after.json.session && !after.json.file && (after.json.live.runs || []).length === 0
       && (after.json.live.plans || []).every(p => p.status === 'draft' && !p.started_at)) : false,
     after.json ? sessionShape(after) : { unparseable: after.parseErr, note: 'the feed itself broke — see the poison finding' });

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, shots, notes, poison,
    before: sessionShape(before), after: after.json ? sessionShape(after) : { unparseable: after.parseErr } }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length + '   console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  errors.slice(0, 20).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 220)));
  notes.forEach(n => console.log('  NOTE ' + n));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, shots, notes, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
