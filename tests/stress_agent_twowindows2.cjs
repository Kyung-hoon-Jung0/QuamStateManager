/* Two windows, one chip -- round 2: the items round 1 could not reach, plus a
 * second reproduction of everything round 1 called a defect.
 * argv[2]=json out, argv[3]=CDP port, argv[4]=base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9434';
const BASE = process.argv[4] || 'http://127.0.0.1:5434';

const results = [];
const notes = [];
function ok(name, cond, detail) { results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail }); }
function note(k, v) { notes.push({ k, v }); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

function makeConn(wsUrl, label, errors) {
  return new Promise(async res => {
    const ws = new WebSocket(wsUrl);
    await new Promise(r => ws.onopen = r);
    let id = 0; const pending = new Map();
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        errors.push({ who: label, kind: 'exception', text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
      }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        errors.push({ who: label, kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
      }
    };
    const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expr) => {
      const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (rr.result && rr.result.exceptionDetails) {
        const x = rr.result.exceptionDetails.exception;
        throw new Error(label + ': ' + ((x && (x.description || x.value)) || 'eval failed') + ' :: ' + expr.slice(0, 120));
      }
      return rr.result.result.value;
    };
    const until = async (expr, ms, step) => {
      const t = Date.now();
      for (;;) {
        let v = null;
        try { v = await ev(expr); } catch (e) { v = null; }
        if (v) return { v, ms: Date.now() - t };
        if (Date.now() - t > ms) return { v: null, ms: Date.now() - t };
        await sleep(step || 150);
      }
    };
    const typeChar = async (ch) => {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    };
    const KEYS = { Enter: 13, Tab: 9, Escape: 27 };
    const press = async (key) => {
      const p = { type: 'rawKeyDown', key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
      if (key === 'Enter') { p.type = 'keyDown'; p.text = '\r'; }
      await send('Input.dispatchKeyEvent', p);
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: KEYS[key] });
    };
    const typeInto = async (sel, text) => {
      await ev('(function(){var e=document.querySelector(' + JSON.stringify(sel) + '); if(!e) return 0; e.focus(); e.value=""; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()');
      for (const ch of text) { await typeChar(ch); await sleep(6); }
      await sleep(180);
    };
    const shot = async (p) => {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result && s.result.data) fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    };
    await send('Page.enable'); await send('Runtime.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    res({ ws, send, ev, until, typeChar, press, typeInto, shot, label });
  });
}

async function main() {
  const errors = [];
  const ver = await (await fetch('http://127.0.0.1:' + CDP + '/json/version')).json();
  const bws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise(r => bws.onopen = r);
  let bid = 0; const bpend = new Map();
  bws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && bpend.has(m.id)) { bpend.get(m.id)(m); bpend.delete(m.id); } };
  const bsend = (method, params = {}) => new Promise(r => { const i = ++bid; bpend.set(i, r); bws.send(JSON.stringify({ id: i, method, params })); });

  const ctxA = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const ctxB = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const tA = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxA })).result.targetId;
  const tB = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxB })).result.targetId;
  const A = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tA, 'A', errors);
  const B = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tB, 'B', errors);

  const land = async (C) => { await C.send('Page.navigate', { url: BASE + '/' }); return await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000); };
  await land(A); await land(B);
  await A.ev('window.confirm=function(){return true}; window.prompt=function(){return "a note"}; 1');
  await B.ev('window.confirm=function(){return true}; window.prompt=function(){return "a note"}; 1');
  await A.typeInto('#agent-home .ag-actor', 'Kyunghoon');
  await B.typeInto('#agent-home .ag-actor', 'Minji');
  ok('A is named Kyunghoon', (await A.ev('AgentPanel.actorName()')) === 'Kyunghoon');
  ok('B is named Minji', (await B.ev('AgentPanel.actorName()')) === 'Minji');

  /* 1. an ASCII name DOES reach the card feed (the control for round 1) */
  await A.typeInto('#agent-home .ag-input', '/run 02a_fake_resonator q3');
  await A.press('Enter');
  const aCard = await A.until('(function(){var c=document.querySelectorAll("#agent-home [data-card^=\'plan:\']"); return c.length? c[c.length-1].getAttribute("data-card"):null;})()', 25000);
  const cardId = aCard.v; const planId = (cardId || '').split(':')[1];
  note('plan_id', planId);
  const sel = '#agent-home [data-card=\'' + cardId + '\']';
  const bTxt = await B.until('(function(){var c=document.querySelector("' + sel + '"); return c? c.textContent.replace(/\\s+/g," "):null;})()', 60000, 120);
  ok('with an ASCII name, the card names the person', !!bTxt.v && /typed by human:Kyunghoon/.test(bTxt.v), (bTxt.v || '').slice(0, 200));
  note('ascii_card_text', (bTxt.v || '').slice(0, 200));

  /* 2. REPRODUCTION: the mode change latency, a second time */
  const modeSel = sel + ' .ag-mode select';
  const t1 = Date.now();
  await A.ev('AgentPanel.setPlanMode("' + planId + '","ask-all"); 1');
  const bMode = await B.until('(function(){var s=document.querySelector("' + modeSel + '"); return s && s.value==="ask-all";})()', 60000, 100);
  const ms2 = Date.now() - t1;
  note('latency_mode_change_ms_run2', ms2);
  ok('REPRO: B sees a mode change within 2s (it did not in round 1)', !!bMode.v && ms2 < 2000, { ms: ms2 });
  // and the control: a plan CANCEL (a route that does wake) in the same session
  await A.typeInto('#agent-home .ag-input', '/run 02a_fake_resonator q4');
  const t2 = Date.now();
  await A.press('Enter');
  const aCard2 = await A.until('(function(){var c=document.querySelectorAll("#agent-home [data-card^=\'plan:\']"); return c.length>1? c[c.length-1].getAttribute("data-card"):null;})()', 25000);
  const card2 = aCard2.v;
  const b2 = await B.until('(function(){return !!document.querySelector("#agent-home [data-card=\'' + card2 + '\']");})()', 60000, 100);
  const ms3 = Date.now() - t2;
  note('latency_second_plan_card_ms', ms3);
  ok('CONTROL: a new plan card reaches B in under 2s in the same session', !!b2.v && ms3 < 2000, { ms: ms3 });

  /* 3. a REAL staged edit by A; what does B see, and when? */
  const trayExpr = '(function(){var t=document.getElementById("pending-tray"); return t? {count:t.getAttribute("data-change-count"), dirty:t.getAttribute("data-working-dirty"), text:t.textContent.replace(/\\s+/g," ").slice(0,200)}:null;})()';
  note('tray_B_before_edit', await B.ev(trayExpr));
  const edit = await A.ev('fetch("/field/edit",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"dot_path=qubits.q1.T1&value=1.2345e-05",credentials:"same-origin"}).then(function(r){return r.text().then(function(t){return r.status+" "+t.replace(/\\s+/g," ").slice(0,200);});})');
  note('A_edit_result', edit);
  ok('A can stage an edit', /^200/.test(edit || ''), edit);
  const tEdit = Date.now();
  const bTray = await B.until('(function(){var t=document.getElementById("pending-tray"); return t && t.getAttribute("data-change-count") && t.getAttribute("data-change-count")!=="0";})()', 35000, 250);
  note('latency_B_tray_after_A_edit_ms', bTray.v ? Date.now() - tEdit : null);
  ok('B is told there are edits it has not seen', !!bTray.v, { waited_ms: bTray.ms, tray: await B.ev(trayExpr) });
  const agentB = await B.ev('(function(){var r=document.querySelector("#agent-home .ag-root"); return r? r.textContent.replace(/\\s+/g," ").slice(0,400):null;})()');
  note('agent_panel_B_after_edit', agentB);
  ok('and the AGENT panel itself mentions the waiting edits', /pending|unapplied|1 change|edits/i.test(agentB || ''), (agentB || '').slice(0, 200));
  await B.shot(OUT.replace(/\.json$/, '_10_B_after_A_edit.png'));
  await A.shot(OUT.replace(/\.json$/, '_11_A_after_own_edit.png'));

  /* 4. B applies with the count it could see (the docs/120 unseen gate) */
  const apply = await B.ev('(function(){var t=document.getElementById("pending-tray"); var seen=t?(t.getAttribute("data-change-count")||"0"):"0";'
    + 'return fetch("/state/apply-to-live",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"seen_changes="+seen,credentials:"same-origin"})'
    + '.then(function(r){return r.text().then(function(x){return {status:r.status, seen:seen, body:x.replace(/\\s+/g," ").slice(0,500)};});});})()');
  note('B_apply', apply);
  ok('B pressing Apply on an edit it never saw is refused with the reason',
     !!apply && (apply.status === 409 || /unseen/i.test(apply.body || '')), { status: apply && apply.status, seen: apply && apply.seen, body: (apply && apply.body || '').slice(0, 300) });

  /* 5. the focused card: B holds the mode select while A cancels */
  await B.ev('(function(){var s=document.querySelector("' + modeSel + '"); if(s) s.focus(); return document.activeElement===s;})()');
  const focused = await B.ev('(function(){var s=document.querySelector("' + modeSel + '"); return document.activeElement===s;})()');
  note('B_focus_in_card', focused);
  await A.ev('fetch("/api/agent/plans/' + planId + '/cancel",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})');
  await sleep(3000);
  await B.ev('AgentPanel.poll(true)'); await sleep(1500);
  const bStillDraft = await B.ev('(function(){var c=document.querySelector("' + sel + '"); return c? {text:c.textContent.replace(/\\s+/g," ").slice(0,140), hasCancel:!!c.querySelector(".ag-cancel"), hasStart:!!c.querySelector(".ag-start")}:null;})()');
  note('B_card_while_focused_after_A_cancelled', bStillDraft);
  ok('a card the person is focused in still shows the plan A already cancelled (stale by design)',
     !!bStillDraft && bStillDraft.hasStart === true, bStillDraft);
  await B.shot(OUT.replace(/\.json$/, '_12_B_stale_focused_card.png'));
  // now focus leaves -> the card must catch up
  await B.ev('(function(){var s=document.querySelector("' + modeSel + '"); if(s) s.blur(); document.body.focus(); return 1;})()');
  await sleep(2500);
  const bAfterBlur = await B.until('(function(){var c=document.querySelector("' + sel + '"); return c && !c.querySelector(".ag-start");})()', 40000, 250);
  note('B_card_catchup_after_blur_ms', bAfterBlur.ms);
  ok('and it catches up once the focus leaves', !!bAfterBlur.v, { ms: bAfterBlur.ms, card: await B.ev('(function(){var c=document.querySelector("' + sel + '"); return c? c.textContent.replace(/\\s+/g," ").slice(0,160):null;})()') });
  const endedTxt = await B.ev('(function(){var c=document.querySelector("' + sel + '"); return c? c.textContent.replace(/\\s+/g," "):null;})()');
  note('cancelled_card_text', (endedTxt || '').slice(0, 260));
  ok('a cancelled plan names WHO ended it', /by human:Kyunghoon|ended .*Kyunghoon/.test(endedTxt || ''), (endedTxt || '').slice(0, 260));

  /* 6. the Calibration log, read in the browser */
  await B.send('Page.navigate', { url: BASE + '/journal' });
  await B.until('!!document.body && document.body.textContent.length>300', 25000);
  await sleep(2500);
  const log = await B.ev('document.body.textContent.replace(/\\s+/g," ")');
  note('journal_text', (log || '').slice(0, 3500));
  ok('the Calibration log records the plans', /plan/.test(log || ''), null);
  ok('and names the people who acted', /Kyunghoon/.test(log || ''), null);
  ok('a Hangul-named person is NOT in the log (round 1 typed one)', !/정경훈/.test(log || ''), null);
  await B.shot(OUT.replace(/\.json$/, '_13_journal.png'));
  await B.send('Page.navigate', { url: BASE + '/' });
  await B.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000);

  /* 7. double actor on the SAME approval (the highest-stakes press) */
  const aps = await A.ev('fetch("/api/agent/approvals",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j);})');
  note('approvals_now', (aps || '').slice(0, 400));
  let apId = null;
  try { const j = JSON.parse(aps); apId = (j.approvals || j.pending || [])[0] && ((j.approvals || j.pending)[0].id); } catch (e) { /* ignore */ }
  if (apId) {
    const mk = (who, verb) => 'fetch("/api/agent/approvals/' + apId + '/' + verb + '",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:"{}",credentials:"same-origin"}).then(function(r){return r.text().then(function(t){return r.status+" "+t.slice(0,200);});})';
    const [r1, r2] = await Promise.all([A.ev(mk('Kyunghoon', 'reject')), B.ev(mk('Minji', 'reject'))]);
    note('double_reject', { A: r1, B: r2 });
    ok('two people deciding one approval: exactly one decision is accepted',
       [r1, r2].filter(x => /^200/.test(x)).length === 1, { A: r1, B: r2 });
  } else {
    note('double_reject', 'no pending approval to race on');
    ok('an approval existed to race on', false, 'none pending -- injected fixture needed');
  }

  /* 8. both press the observer toggle / both cancel at once, again */
  const p2 = await A.ev('fetch("/api/agent/plans",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"Kyunghoon"},body:JSON.stringify({run_line:"/run 02a_fake_resonator q5"}),credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){return (j.plan||{}).id||JSON.stringify(j).slice(0,150);})');
  note('race_plan_id', p2);
  if (p2 && /^pl-/.test(p2)) {
    const mkc = (who) => 'fetch("/api/agent/plans/' + p2 + '/cancel",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})';
    const [q1, q2] = await Promise.all([A.ev(mkc('Kyunghoon')), B.ev(mkc('Minji'))]);
    note('double_cancel_status_run2', { A: q1, B: q2 });
    await sleep(2000);
    const jr = await A.ev('fetch("/api/agent/journal",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j);})');
    const lines = (jr || '').split('\\n');
    const n = ((jr || '').match(new RegExp('02a_fake_resonator q5` cancelled by', 'g')) || []).length;
    note('journal_lines_for_that_cancel', n);
    const who = ((jr || '').match(new RegExp('q5` cancelled by [^"\\\\\\\\]*', 'g')) || []);
    note('journal_cancel_whos', who.slice(0, 4));
    ok('a plan cancelled by two people at once is ONE line in the log, naming one person', n === 1, { lines: n, whos: who.slice(0, 4), statuses: { A: q1, B: q2 } });
    const srvPlan = await A.ev('fetch("/api/agent/plans/' + p2 + '",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j.plan||j).slice(0,400);})');
    note('race_plan_final', srvPlan);
  }

  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks ' + (results.length - bad.length) + '/' + results.length + '  console errors ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  errors.slice(0, 20).forEach(e => console.log('  ERR ' + e.who + ' ' + e.kind + ': ' + String(e.text).slice(0, 200)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  try { fs.writeFileSync(OUT, JSON.stringify({ results, notes, driver: String(e && e.stack || e) }, null, 1)); } catch (x) { /* ignore */ }
  process.exit(1);
});
