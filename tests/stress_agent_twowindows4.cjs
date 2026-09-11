/* Round 4: a CLEAN two-window latency bench.
 * Every stale target is closed first, both pages are forced visible+focused,
 * and each event kind is measured in the SAME session so the only variable is
 * the route. argv[2]=json out, argv[3]=CDP, argv[4]=base
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
        await sleep(step || 80);
      }
    };
    const shot = async (p) => {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result && s.result.data) fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    };
    await send('Page.enable'); await send('Runtime.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });   // both windows are "in front"
    res({ ws, send, ev, until, shot, label });
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

  // close every stale page target from earlier rounds
  const list = (await bsend('Target.getTargets', {})).result.targetInfos.filter(t => t.type === 'page');
  for (const t of list) { try { await bsend('Target.closeTarget', { targetId: t.targetId }); } catch (e) { /* ignore */ } }
  note('closed_stale_targets', list.length);
  await sleep(800);

  const ctxA = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const ctxB = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const tA = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxA })).result.targetId;
  const tB = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxB })).result.targetId;
  const A = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tA, 'A', errors);
  const B = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tB, 'B', errors);
  const land = async (C) => { await C.send('Page.navigate', { url: BASE + '/' }); return await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000); };
  await land(A); await land(B);
  await A.ev('AgentPanel.setActor("Kyunghoon"); 1');
  await B.ev('AgentPanel.setActor("Minji"); 1');
  const visExpr = '({hidden:document.hidden, vis:document.visibilityState, focus:document.hasFocus()})';
  const vA = await A.ev(visExpr), vB = await B.ev(visExpr);
  note('visibility', { A: vA, B: vB });
  ok('both windows count as visible (so the push path is armed in both)',
     vA.vis === 'visible' && vB.vis === 'visible' && !vA.hidden && !vB.hidden, { A: vA, B: vB });
  await sleep(3000);   // let each window's long-poll settle open

  const mkPlan = (who, line) => A.ev('fetch("/api/agent/plans",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:JSON.stringify({run_line:' + JSON.stringify(line) + '}),credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){return (j.plan||{}).id||("ERR "+JSON.stringify(j).slice(0,150));})');

  const trials = [];
  for (let i = 0; i < 3; i++) {
    const q = 'q' + (11 + i);
    const t0 = Date.now();
    const pid = await mkPlan('Kyunghoon', '/run 02a_fake_resonator ' + q);
    if (!/^pl-/.test(pid)) { note('plan_failed', pid); break; }
    const cardSel = '#agent-home [data-card=\'plan:' + pid + '\']';
    const card = await B.until('!!document.querySelector("' + cardSel + '")', 45000, 60);
    const cardMs = Date.now() - t0;
    await sleep(1500);
    const t1 = Date.now();
    await A.ev('AgentPanel.setPlanMode("' + pid + '","ask-all"); 1');
    const mode = await B.until('(function(){var s=document.querySelector("' + cardSel + ' .ag-mode select"); return s && s.value==="ask-all";})()', 45000, 60);
    const modeMs = Date.now() - t1;
    await sleep(1200);
    const t2 = Date.now();
    await A.ev('fetch("/api/agent/plans/' + pid + '/cancel",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})');
    const cancelled = await B.until('(function(){var c=document.querySelector("' + cardSel + '"); return c && /cancelled/.test(c.textContent);})()', 45000, 60);
    const cancelMs = Date.now() - t2;
    trials.push({ plan: pid, card_ms: card.v ? cardMs : null, mode_ms: mode.v ? modeMs : null, cancel_ms: cancelled.v ? cancelMs : null });
    note('trial' + i, trials[trials.length - 1]);
  }
  note('trials', trials);
  const okAll = (k) => trials.length === 3 && trials.every(t => t[k] !== null);
  const med = (k) => trials.map(t => t[k]).sort((a, b) => a - b)[1];
  note('median_ms', { card: med('card_ms'), mode: med('mode_ms'), cancel: med('cancel_ms') });
  ok('a NEW PLAN reaches the other window in under 2s (3 trials)', okAll('card_ms') && trials.every(t => t.card_ms < 2000), trials.map(t => t.card_ms));
  ok('a CANCEL reaches the other window in under 2s (3 trials)', okAll('cancel_ms') && trials.every(t => t.cancel_ms < 2000), trials.map(t => t.cancel_ms));
  ok('a MODE CHANGE reaches the other window in under 2s (3 trials)', okAll('mode_ms') && trials.every(t => t.mode_ms < 2000), trials.map(t => t.mode_ms));

  await A.shot(OUT.replace(/\.json$/, '_30_A.png'));
  await B.shot(OUT.replace(/\.json$/, '_31_B.png'));

  /* the tray, in a window that stays put */
  const trayExpr = '(function(){var t=document.getElementById("pending-tray"); return t? {count:t.getAttribute("data-change-count"), seq:t.getAttribute("data-seq")}:null;})()';
  const before = await B.ev(trayExpr);
  await A.ev('fetch("/field/edit",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"dot_path=qubits.q12.T1&value=9.9e-06",credentials:"same-origin"}).then(function(r){return r.status;})');
  const tr = await B.until('(function(){var t=document.getElementById("pending-tray"); return t && t.getAttribute("data-change-count")!=="' + (before && before.count) + '";})()', 25000, 250);
  note('tray_push', { before, after: await B.ev(trayExpr), saw: !!tr.v, waited_ms: tr.ms });
  ok('an edit made in A shows up in B\'s tray without B navigating', !!tr.v, { before, after: await B.ev(trayExpr), waited_ms: tr.ms });
  // and after B navigates anywhere, it does know
  await B.send('Page.navigate', { url: BASE + '/journal' });
  await B.until('!!document.getElementById("pending-tray")', 25000);
  const afterNav = await B.ev(trayExpr);
  note('tray_after_B_navigates', afterNav);
  ok('after B navigates, B is told about the waiting edits', afterNav && afterNav.count !== (before && before.count), { before, afterNav });
  await B.shot(OUT.replace(/\.json$/, '_32_B_tray_after_nav.png'));

  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks ' + (results.length - bad.length) + '/' + results.length + '  console errors ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  notes.forEach(n => console.log('  NOTE ' + n.k + ' = ' + JSON.stringify(n.v).slice(0, 300)));
  errors.slice(0, 10).forEach(e => console.log('  ERR ' + e.who + ' ' + e.kind + ': ' + String(e.text).slice(0, 160)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  try { fs.writeFileSync(OUT, JSON.stringify({ results, notes, driver: String(e && e.stack || e) }, null, 1)); } catch (x) { /* ignore */ }
  process.exit(1);
});
