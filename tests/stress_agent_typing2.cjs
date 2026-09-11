/* QUESTION 2, confirm pass — every finding of round 1 reproduced a SECOND
 * time from a fresh page, plus the cases round 1 could not separate:
 * a real in-flight keystroke, the "?" key, the sidebar Agent button as a
 * focus door, the Send button, and the honest end-to-end time.
 *
 * Same hard rule: only "/" lines are ever submitted, and chat/start|send is
 * blocked at the fetch boundary so nothing can reach a model.
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9432';
const BASE = process.argv[4] || 'http://127.0.0.1:5432';
const SHOT = OUT.replace(/\.json$/, '');
const errors = [], results = [], measures = {};
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }
function note(k, v) { measures[k] = v; }

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pending = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; errors.push({ kind: 'exception', text: (d.exception && (d.exception.description || d.exception.value)) || d.text }); }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push({ kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (x) => { const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true }); if (rr.result && rr.result.exceptionDetails) { const ex = rr.result.exceptionDetails.exception; throw new Error((ex && (ex.description || ex.value)) || 'eval failed'); } return rr.result.result.value; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const shot = async (tag) => { const s = await send('Page.captureScreenshot', { format: 'png' }); if (s.result && s.result.data) fs.writeFileSync(SHOT + '_' + tag + '.png', Buffer.from(s.result.data, 'base64')); };
  const typeChar = async (ch) => { await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch }); await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch }); };
  const typeText = async (s, d) => { for (const c of s) { await typeChar(c); if (d) await sleep(d); } };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27 };
  const press = async (key, mods) => {
    const modifiers = ((mods && mods.shift) ? 8 : 0);
    const p = { type: 'rawKeyDown', key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key], modifiers };
    if (key === 'Enter') { p.type = 'keyDown'; p.text = '\r'; }
    if (key === 'Tab') { p.type = 'keyDown'; p.text = '\t'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: KEYS[key], modifiers });
  };
  const clickSel = async (sel) => {
    const b = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var r=e.getBoundingClientRect(); if(!r.width) return null; return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+Math.min(r.height/2,14))};})()`);
    if (!b) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: b.x, y: b.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b.x, y: b.y, button: 'left', clickCount: 1 });
    return true;
  };
  const active = async () => ev(`(function(){var a=document.activeElement; return a? {tag:a.tagName,id:a.id||null,cls:a.className||null}:null;})()`);
  const taVal = async () => ev(`(function(){var t=document.querySelector('.ag-input'); return t? t.value : null;})()`);
  const setTa = async (v) => ev(`(function(){var t=document.querySelector('.ag-input'); if(!t) return 0; t.focus(); t.value=${JSON.stringify(v)}; t.dispatchEvent(new Event('input',{bubbles:true})); t.setSelectionRange(t.value.length,t.value.length); return 1;})()`);
  const planCards = async () => ev(`document.querySelectorAll('.ag-cards [data-card^="plan:"]').length`);
  const toasts = async () => ev(`JSON.stringify(Array.prototype.map.call(document.querySelectorAll('#status-bar .toast'),function(t){return (t.textContent||'').trim().slice(0,160);}))`).then(s => JSON.parse(s || '[]'));
  const clearToasts = async () => ev(`(function(){var b=document.getElementById('status-bar'); if(b) b.innerHTML=''; return 1;})()`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `window.__net=[];window.__blocked=[];(function(){var f=window.fetch;window.fetch=function(u,o){var url=String((u&&u.url)||u),meth=(o&&o.method)||'GET';
      try{window.__net.push({m:meth,u:url,t:Date.now()});}catch(e){}
      if(meth==='POST'&&/\\/api\\/agent\\/chat\\/(start|send)/.test(url)){try{window.__blocked.push({u:url});}catch(e){}
        return Promise.resolve(new Response(JSON.stringify({error:'BLOCKED BY TEST DRIVER'}),{status:503,headers:{'Content-Type':'application/json'}}));}
      return f.apply(this,arguments);};})();`
  });

  /* ── 1. the blind "/run" line, reproduced from a cold load ──────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(4000);
  await typeText('/run 04b_power_rabi q1', 20);
  await sleep(500);
  const r1 = { ta: await taVal(), active: await active(),
               global: await ev(`(document.getElementById('global-search')||{}).value||null`),
               composer_gone: await ev(`!document.querySelector('.ag-input')`),
               pane: await ev(`((document.getElementById('table-pane')||{}).textContent||'').replace(/\\s+/g,' ').trim().slice(0,120)`) };
  note('repro_blind_slash', r1);
  ok('REPRO: a blind /run line is eaten by the global search',
     r1.global === 'run 04b_power_rabi q1', r1);
  ok('…and the Agent screen itself is replaced by a search pane', r1.composer_gone || /No results/.test(r1.pane), r1);
  await shot('r2_01_blind_slash_again');

  /* ── 2. "?" typed blind ─────────────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3500);
  await typeChar('?');
  await sleep(400);
  const sheet = await ev(`!!document.getElementById('kb-cheatsheet')`);
  note('blind_question_mark', { sheet, ta: await taVal() });
  ok('a blind "?" opens the shortcut sheet instead of typing', sheet === true, { sheet });
  if (sheet) { await press('Escape'); await sleep(200); }

  /* ── 3. after ONE click, the same keys behave ───────────────────────── */
  await clickSel('.ag-input');
  await sleep(150);
  await typeText('/run 04b_power_rabi q1', 0);
  await sleep(300);
  note('after_click_slash', { ta: await taVal(), global: await ev(`(document.getElementById('global-search')||{}).value||null`) });
  ok('after one click in the box, "/" types normally',
     (await taVal()) === '/run 04b_power_rabi q1', await taVal());

  /* ── 4. the honest end-to-end: load → first plan card ───────────────── */
  const t0 = Date.now();
  await send('Page.navigate', { url: BASE + '/agent' });
  let tUsable = null;
  while (Date.now() - t0 < 15000) { if (await ev(`!!document.querySelector('.ag-input')`)) { tUsable = Date.now() - t0; break; } await sleep(30); }
  await sleep(400);
  const before = await planCards();
  await clickSel('.ag-input');                       // the click a person must make
  const tClick = Date.now() - t0;
  await typeText('/run 04b_power_rabi q1', 45);      // ~45ms/char, a real typing speed
  const tTyped = Date.now() - t0;
  await press('Enter');
  let tCard = null;
  while (Date.now() - t0 < 20000) { if ((await planCards()) > before) { tCard = Date.now() - t0; break; } await sleep(25); }
  note('end_to_end_ms', { composer_present: tUsable, clicked: tClick, typed: tTyped, plan_card: tCard });
  ok('a person can go from page load to a plan card in under 5s', tCard !== null && tCard < 5000, measures.end_to_end_ms);

  /* ── 5. typing WHILE the previous line is in flight (real keys) ─────── */
  await clearToasts();
  const b2 = await planCards();
  await setTa('/run 04b_power_rabi q1');
  await press('Enter');
  await typeText('/run 05_T1 q2', 0);                // straight away, no wait
  await sleep(2500);
  const inflight = { cards: (await planCards()) - b2, box: await taVal(), toasts: await toasts(),
                     disabled_seen: true };
  note('typing_during_inflight', inflight);
  ok('a line typed while the previous one is in flight is not lost',
     inflight.box === '/run 05_T1 q2' || inflight.cards === 2, inflight);
  await shot('r2_02_inflight');

  /* ── 6. /run with no targets — what does the card actually say? ─────── */
  await clearToasts();
  await setTa('/run 05_T1');
  await press('Enter');
  await sleep(1800);
  const card = await ev(`(function(){var cs=document.querySelectorAll('.ag-cards [data-card^="plan:"]'); var c=cs[cs.length-1]; if(!c) return null;
     return {txt:(c.textContent||'').replace(/\\s+/g,' ').trim().slice(0,260), btns:Array.prototype.map.call(c.querySelectorAll('button'),function(b){return b.textContent.trim();})};})()`);
  note('no_target_card', card);
  ok('a /run with no targets is refused, or the card says it runs on nothing',
     card && (/no target|0 target/i.test(card.txt)), card);
  await shot('r2_03_no_target_card');

  /* ── 7. the Send BUTTON with a /run draft ───────────────────────────── */
  await clearToasts();
  const b3 = await planCards();
  await setTa('/run 06a_ramsey q2');
  await clickSel('.ag-send');
  await sleep(1800);
  note('send_button', { cards: (await planCards()) - b3, toasts: await toasts(), box: await taVal() });
  ok('the Send button does the same thing as Enter', (await planCards()) - b3 === 1, measures.send_button);

  /* ── 8. the sidebar Agent button as a focus door ────────────────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3500);
  const agentBtn = await ev(`(function(){var els=document.querySelectorAll('#sidebar a,#sidebar button');
     for (var i=0;i<els.length;i++){ if(/^\\s*(⚗|🤖)?\\s*Agent\\b/.test(els[i].textContent.trim())) return els[i].textContent.trim().slice(0,30); } return null;})()`);
  note('sidebar_agent_button', agentBtn);
  if (agentBtn) {
    await ev(`(function(){var els=document.querySelectorAll('#sidebar a,#sidebar button');
       for (var i=0;i<els.length;i++){ if(/^\\s*(⚗|🤖)?\\s*Agent\\b/.test(els[i].textContent.trim())) { els[i].click(); return 1; } } return 0;})()`);
    await sleep(600);
    note('focus_after_sidebar_agent', await active());
    ok('pressing the sidebar Agent button puts the keyboard in the composer',
       String((await active() || {}).cls).indexOf('ag-input') >= 0, await active());
  }

  /* ── 9. focus ring on every tab stop of the composer row ────────────── */
  await clickSel('.ag-input');
  await sleep(120);
  const rings = [];
  for (let i = 0; i < 6; i++) {
    await press('Tab'); await sleep(110);
    rings.push(await ev(`(function(){var a=document.activeElement; if(!a||a===document.body) return null; var cs=getComputedStyle(a);
       return {el:(a.className||a.tagName).slice(0,24), outline:cs.outlineStyle+' '+cs.outlineWidth, shadow:(cs.boxShadow||'none').slice(0,48)};})()`));
  }
  note('tab_rings', rings);
  ok('every tab stop in the composer row shows a ring',
     rings.every(r => r && ((r.outline && !/none/.test(r.outline)) || (r.shadow && r.shadow !== 'none'))), rings);
  await shot('r2_04_tab_focus');

  /* ── 10. a long multi-line paste: does the box stay usable? ─────────── */
  await setTa('');
  await clickSel('.ag-input');
  const many = Array.from({ length: 12 }, (_, i) => '/run 04b_power_rabi q' + (i + 1)).join('\n');
  await send('Input.insertText', { text: many });
  await sleep(400);
  const box = await ev(`(function(){var t=document.querySelector('.ag-input'); var r=t.getBoundingClientRect();
     return {h:Math.round(r.height), scrollH:t.scrollHeight, overflow:getComputedStyle(t).overflowY, lines:t.value.split('\\n').length,
             composer_bottom:Math.round(document.querySelector('.ag-composer').getBoundingClientRect().bottom), vh:window.innerHeight};})()`);
  note('long_paste_box', box);
  ok('a 12-line paste caps the box height and scrolls inside it',
     box.h < 220 && box.overflow === 'auto', box);
  ok('…and the composer is still fully on screen', box.composer_bottom <= box.vh + 2, box);
  await shot('r2_05_long_paste');
  await setTa('');

  fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length + '   console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 320)));
  errors.slice(0, 10).forEach(e => console.log('  ERR ' + e.kind + ': ' + String(e.text).slice(0, 180)));
  process.exit(0);
}
main().catch(e => { console.error('driver error: ' + (e && e.stack || e)); fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors, driver: String(e && e.stack || e) }, null, 1)); process.exit(1); });
