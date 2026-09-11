/* Two people, two browser windows, ONE chip -- the shared-lab case.
 *
 * Person A and person B each get their OWN browser context (isolated
 * localStorage: two machines, not two tabs of one), their own name in the
 * Agent composer's name box, and they press things at the same moment.
 *
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
        const v = await ev(expr);
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

const TEXT = 'function(){var r=document.querySelector("#agent-home .ag-root"); return r? r.textContent.replace(/\\s+/g," "):null;}';

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
  note('contexts', { ctxA, ctxB, tA, tB });

  const A = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tA, 'A', errors);
  const B = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tB, 'B', errors);

  const land = async (C) => {
    await C.send('Page.navigate', { url: BASE + '/' });
    return await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000);
  };
  const lA = await land(A), lB = await land(B);
  ok('A lands on the Agent home', !!lA.v, lA.ms + 'ms');
  ok('B lands on the Agent home', !!lB.v, lB.ms + 'ms');
  await A.ev('window.confirm=function(){return true}; window.prompt=function(){return "a note"}; 1');
  await B.ev('window.confirm=function(){return true}; window.prompt=function(){return "a note"}; 1');

  await A.ev('localStorage.setItem("__iso","A"); 1');
  const isoB = await B.ev('localStorage.getItem("__iso")');
  ok('the two windows have SEPARATE storage (two machines, not two tabs)', isoB === null, { B_sees: isoB });

  /* 1. names */
  const NAME_A = '정경훈';   // Hangul, deliberately
  const NAME_B = 'Minji';
  await A.typeInto('#agent-home .ag-actor', NAME_A);
  const aStored = await A.ev('localStorage.getItem("quam_actor_name")');
  const aBox = await A.ev('document.querySelector("#agent-home .ag-actor").value');
  ok('a Hangul name typed into the name box survives into storage', aStored === NAME_A, { typed: NAME_A, stored: aStored, box: aBox });
  note('hangul_name', { typed: NAME_A, stored: aStored, box: aBox });
  await B.typeInto('#agent-home .ag-actor', NAME_B);
  const bStored = await B.ev('localStorage.getItem("quam_actor_name")');
  ok('an ASCII name survives the box', bStored === NAME_B, { stored: bStored });

  /* 2. A makes a plan card */
  await A.typeInto('#agent-home .ag-input', '/run 09a_fake_rabi q1 q2');
  const t0 = Date.now();
  await A.press('Enter');
  const aCard = await A.until('(function(){var c=document.querySelector("#agent-home [data-card^=\'plan:\']"); return c? c.getAttribute("data-card"):null;})()', 25000);
  ok('A sees their own plan card', !!aCard.v, { card: aCard.v, ms: aCard.ms });
  if (!aCard.v) {
    const t = await A.ev('(' + TEXT + ')()');
    note('A_panel_when_no_card', (t || '').slice(0, 600));
    fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));
    await A.shot(OUT.replace(/\.json$/, '_00_no_card.png'));
    console.log('no plan card -- stopping'); process.exit(1);
  }
  const cardId = aCard.v;
  const planId = cardId.split(':')[1];
  note('plan_id', planId);
  const bSel = '#agent-home [data-card=\'' + cardId + '\']';
  const bCard = await B.until('(function(){var c=document.querySelector("' + bSel + '"); return c? c.textContent.replace(/\\s+/g," ").slice(0,300):null;})()', 60000, 120);
  const bMs = Date.now() - t0;
  ok('B sees the card A made', !!bCard.v, { ms: bMs, text: bCard.v });
  note('latency_plan_card_ms', bMs);
  ok('the card says WHO typed it', !!bCard.v && /typed by/.test(bCard.v), bCard.v);
  ok('and the WHO is an identifiable person, not just "a person"',
     !!bCard.v && /typed by human:\S/.test(bCard.v), (bCard.v || '').slice(0, 220));

  await A.shot(OUT.replace(/\.json$/, '_01_A_made_plan.png'));
  await B.shot(OUT.replace(/\.json$/, '_02_B_sees_plan.png'));

  /* 3. mode change latency */
  const modeExpr = '(function(){var s=document.querySelector("' + bSel + ' .ag-mode select"); return s? s.value:null;})()';
  note('mode_before', { a: await A.ev(modeExpr), b: await B.ev(modeExpr) });
  const t1 = Date.now();
  await A.ev('AgentPanel.setPlanMode("' + planId + '","ask-all"); 1');
  const aMode = await A.until('(function(){var s=document.querySelector("' + bSel + ' .ag-mode select"); return s && s.value==="ask-all";})()', 12000);
  ok('A sees its own mode change land', !!aMode.v, { ms: aMode.ms });
  const bMode = await B.until('(function(){var s=document.querySelector("' + bSel + ' .ag-mode select"); return s && s.value==="ask-all";})()', 60000, 120);
  const modeMs = Date.now() - t1;
  ok('B sees the mode A chose', !!bMode.v, { ms: modeMs });
  note('latency_mode_change_ms', modeMs);
  ok('and within a couple of seconds, like every other agent event', !!bMode.v && modeMs < 5000, { ms: modeMs });

  /* 4. the same plan, two people, the same moment */
  const raceA = A.ev('AgentPanel.setPlanMode("' + planId + '","auto"); 1');
  const raceB = B.ev('AgentPanel.setPlanMode("' + planId + '","ask-writes"); 1');
  await Promise.all([raceA, raceB]);
  await sleep(3000);
  const srv = await A.ev('fetch("/api/agent/plans/' + planId + '",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j.plan||j);})');
  let srvMode = null; try { srvMode = JSON.parse(srv).mode; } catch (e) { /* ignore */ }
  await A.ev('AgentPanel.poll(true)'); await B.ev('AgentPanel.poll(true)'); await sleep(1500);
  const seenA = await A.ev(modeExpr), seenB = await B.ev(modeExpr);
  ok('after a simultaneous mode change the server holds exactly one mode', !!srvMode, { server: srvMode });
  note('race_mode', { server: srvMode, A_shows: seenA, B_shows: seenB });
  ok('and BOTH windows end up showing what the server holds', seenA === srvMode && seenB === srvMode, { server: srvMode, A: seenA, B: seenB });
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* 5. observer is per window */
  await B.ev('AgentPanel.setObserver(true); 1');
  await sleep(800);
  const ctlExpr = '(function(){var r=document.querySelector("#agent-home .ag-root"); return {start:r.querySelectorAll(".ag-start").length, cancel:r.querySelectorAll(".ag-cancel").length, stop:r.querySelectorAll(".ag-stop").length, modeDisabled:!!(r.querySelector(".ag-mode select")||{}).disabled, observing:r.querySelectorAll(".ag-observing").length};})()';
  const bCtl = await B.ev(ctlExpr);
  ok('observer hides Start / Cancel on THAT window', bCtl.start === 0 && bCtl.cancel === 0, bCtl);
  ok('and disables its mode picker, and says it is observing', bCtl.modeDisabled && bCtl.observing > 0, bCtl);
  await A.ev('AgentPanel.poll(true)'); await sleep(1500);
  const aCtl = await A.ev(ctlExpr);
  ok('the OTHER window is untouched by it', aCtl.start > 0 && aCtl.cancel > 0 && !aCtl.modeDisabled && aCtl.observing === 0, aCtl);
  await B.shot(OUT.replace(/\.json$/, '_03_B_observer.png'));
  await A.shot(OUT.replace(/\.json$/, '_04_A_normal.png'));
  const obsTry = await B.ev('fetch("/api/agent/plans/' + planId + '/mode",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"Minji"},body:JSON.stringify({mode:"auto"}),credentials:"same-origin"}).then(function(r){return r.status;})');
  note('observer_is_client_side_only', { status_from_observer_window: obsTry });
  await B.ev('AgentPanel.setObserver(false); 1'); await sleep(600);
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* 6. A stages an edit elsewhere; is B told? */
  const trayExpr = '(function(){var t=document.getElementById("pending-tray"); return t? {count:t.getAttribute("data-change-count"), text:t.textContent.replace(/\\s+/g," ").slice(0,160)}:null;})()';
  const trayBefore = await B.ev(trayExpr);
  const edit = await A.ev('fetch("/field/edit",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"path=qubits.q1.T1&value=1.2345e-05",credentials:"same-origin"}).then(function(r){return r.status+"";})');
  note('A_edit_status', edit);
  await sleep(4000);
  const trayAfterB = await B.ev(trayExpr);
  const agentSaysB = await B.ev('(' + TEXT + ')()');
  note('tray_B_before', trayBefore); note('tray_B_after', trayAfterB);
  ok('B is told, somewhere on the Agent screen, that there are edits waiting',
     (trayAfterB && trayAfterB.count && trayAfterB.count !== '0') || /pending|unapplied|waiting edit/i.test(agentSaysB || ''),
     { tray: trayAfterB, agent: (agentSaysB || '').slice(0, 220) });
  await B.shot(OUT.replace(/\.json$/, '_05_B_after_A_edit.png'));
  const apply = await B.ev('(function(){var t=document.getElementById("pending-tray"); var seen = t? (t.getAttribute("data-change-count")||"0") : "0";'
    + ' return fetch("/state/apply-to-live",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"seen_changes="+seen,credentials:"same-origin"})'
    + '  .then(function(r){return r.text().then(function(x){return {status:r.status, seen:seen, body:x.replace(/\\s+/g," ").slice(0,400)};});});})()');
  note('B_apply_with_stale_count', apply);
  ok('an Apply pressed on a stale count is REFUSED, not silently applied',
     !!apply && (apply.status === 409 || /unseen/i.test(apply.body || '')), apply);
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* 7. hostile names */
  const nameCases = [['empty', ''], ['quote+backslash', 'a"b\\c'], ['200 chars', 'N'.repeat(200)], ['hangul', '김민지']];
  const nameOut = [];
  for (const c of nameCases) {
    await A.ev('AgentPanel.setActor(' + JSON.stringify(c[1]) + '); 1');
    const stored = await A.ev('AgentPanel.actorName()');
    let st = null, err = null;
    try {
      st = await A.ev('(function(){var h={"Content-Type":"application/json",Accept:"application/json"};var w=AgentPanel.actorName(); if(w) h["X-SM-Actor"]=w;'
        + ' return fetch("/api/agent/plans/' + planId + '/mode",{method:"POST",headers:h,body:JSON.stringify({mode:"ask-writes"}),credentials:"same-origin"})'
        + '  .then(function(r){return r.status;},function(e){return "FETCH REJECTED: "+e;});})()');
    } catch (e) { err = String(e).slice(0, 220); }
    nameOut.push({ label: c[0], typed: c[1], stored, status: st, err });
    ok('a ' + c[0] + ' name does not break the window', st === 200 || st === 409, { stored, status: st, err });
  }
  note('name_cases', nameOut);

  /* 8. the Calibration log */
  await A.ev('AgentPanel.setActor("Kyunghoon"); 1');
  await A.ev('AgentPanel.setPlanMode("' + planId + '","ask-all"); 1');
  await sleep(1800);
  await A.send('Page.navigate', { url: BASE + '/journal' });
  await A.until('document.body.textContent.length>300', 20000);
  await sleep(2000);
  const log = await A.ev('document.body.textContent.replace(/\\s+/g," ")');
  note('journal_excerpt', (log || '').slice(0, 3000));
  ok('the Calibration log records the plan', /plan/.test(log || ''), (log || '').slice(0, 200));
  ok('and names a person for it', /by human:|Kyunghoon|typed by/i.test(log || ''), null);
  await A.shot(OUT.replace(/\.json$/, '_06_journal.png'));
  await A.send('Page.navigate', { url: BASE + '/' });
  await A.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 25000);
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* 9. both cancel at the same moment */
  const mk = (who) => 'fetch("/api/agent/plans/' + planId + '/cancel",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})';
  const [s1, s2] = await Promise.all([A.ev(mk('Kyunghoon')), B.ev(mk('Minji'))]);
  note('double_cancel_status', { A: s1, B: s2 });
  await sleep(2000);
  const jt = await A.ev('fetch("/api/agent/journal",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j).slice(0,8000);})');
  const cancels = ((jt || '').match(/cancelled by/g) || []).length;
  note('journal_cancel_lines', cancels);
  note('journal_cancel_context', (jt || '').split('cancelled by').slice(0, 3).map(s => s.slice(-60)));
  ok('two people cancelling at once is recorded ONCE, not twice', cancels === 1, { lines: cancels, statuses: { A: s1, B: s2 } });

  /* 10. one window closes mid-flow */
  await B.ev('AgentPanel.poll(true)'); await sleep(1200);
  const bBefore = await B.ev('(' + TEXT + ')()');
  await bsend('Target.closeTarget', { targetId: tA });
  await sleep(5000);
  let bAlive = null, bErr = null;
  try {
    bAlive = await B.ev('(function(){var r=document.querySelector("#agent-home .ag-root"); return {text:r.textContent.replace(/\\s+/g," ").slice(0,220), unreachable:r.querySelectorAll(".ag-unreachable").length};})()');
  } catch (e) { bErr = String(e).slice(0, 220); }
  note('B_after_A_closed', { before: (bBefore || '').slice(0, 160), after: bAlive, err: bErr });
  ok('the surviving window keeps working when the other closes', !!bAlive && !bErr, bAlive);
  ok('and does not falsely claim SM is unreachable', !!bAlive && bAlive.unreachable === 0, bAlive);
  await B.shot(OUT.replace(/\.json$/, '_07_B_after_A_closed.png'));

  /* 11. two TABS of ONE profile (the shared-machine case) */
  const tC = (await bsend('Target.createTarget', { url: BASE + '/', browserContextId: ctxB })).result.targetId;
  const C = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tC, 'C', errors);
  await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000);
  const cName = await C.ev('document.querySelector("#agent-home .ag-actor").value');
  const bName = await B.ev('AgentPanel.actorName()');
  note('same_profile_name_shared', { B: bName, new_tab: cName });
  ok('a second tab of one profile shows the SAME name box content', cName === bName, { B: bName, C: cName });
  await B.ev('AgentPanel.setObserver(true); 1'); await sleep(600);
  await C.send('Page.navigate', { url: BASE + '/' });
  await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000);
  const cObs = await C.ev('(function(){var r=document.querySelector("#agent-home .ag-root"); return {observing:r.querySelectorAll(".ag-observing").length, cancel:r.querySelectorAll(".ag-cancel").length};})()');
  note('same_profile_observer_after_reload', cObs);
  ok('observer set in one tab of a profile reaches a fresh tab of the same profile', cObs.observing > 0, cObs);
  await C.shot(OUT.replace(/\.json$/, '_08_same_profile_tab.png'));

  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks ' + (results.length - bad.length) + '/' + results.length + '  console errors ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  errors.slice(0, 20).forEach(e => console.log('  ERR ' + e.who + ' ' + e.kind + ': ' + String(e.text).slice(0, 200)));
  notes.forEach(n => console.log('  NOTE ' + n.k + ' = ' + JSON.stringify(n.v).slice(0, 600)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  try { fs.writeFileSync(OUT, JSON.stringify({ results, notes, driver: String(e && e.stack || e) }, null, 1)); } catch (x) { /* ignore */ }
  process.exit(1);
});
