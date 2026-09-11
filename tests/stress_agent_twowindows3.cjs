/* Two windows, one chip -- round 3: clean reproductions of everything round 1
 * and 2 raised, plus the two races round 2 could not reach.
 * argv[2]=json out, argv[3]=CDP, argv[4]=base, argv[5]=approval id
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9434';
const BASE = process.argv[4] || 'http://127.0.0.1:5434';
const APID = process.argv[5] || '';

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
        await sleep(step || 120);
      }
    };
    const typeChar = async (ch) => {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    };
    const typeInto = async (sel, text) => {
      await ev('(function(){var e=document.querySelector(' + JSON.stringify(sel) + '); if(!e) return 0; e.focus(); e.value=""; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()');
      for (const ch of text) { await typeChar(ch); await sleep(10); }
      await sleep(200);
    };
    const shot = async (p) => {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result && s.result.data) fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    };
    await send('Page.enable'); await send('Runtime.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    res({ ws, send, ev, until, typeChar, typeInto, shot, label });
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
  await A.ev('AgentPanel.setActor("Kyunghoon"); 1');
  await B.ev('AgentPanel.setActor("Minji"); 1');

  const mkPlan = (C, who, line) => C.ev('fetch("/api/agent/plans",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:JSON.stringify({run_line:' + JSON.stringify(line) + '}),credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){return (j.plan||{}).id||("ERR "+JSON.stringify(j).slice(0,160));})');

  /* ── R1. the mode-change latency, measured cleanly, twice ─────────── */
  const lat = [];
  for (let i = 0; i < 2; i++) {
    const pid = await mkPlan(A, 'Kyunghoon', '/run 02a_fake_resonator q' + (6 + i));
    if (!/^pl-/.test(pid)) { note('plan_make_failed', pid); break; }
    const sel = '#agent-home [data-card=\'plan:' + pid + '\'] .ag-mode select';
    // B must first HAVE the card (the wake path) before the mode is changed
    const seen = await B.until('!!document.querySelector("' + sel + '")', 30000, 100);
    lat.push({ plan: pid, card_ms: seen.ms });
    if (!seen.v) { note('B_never_saw_card', pid); break; }
    await sleep(600);
    const t = Date.now();
    await A.ev('AgentPanel.setPlanMode("' + pid + '","ask-all"); 1');
    const got = await B.until('(function(){var s=document.querySelector("' + sel + '"); return s && s.value==="ask-all";})()', 60000, 100);
    lat[lat.length - 1].mode_ms = got.v ? Date.now() - t : null;
    lat[lat.length - 1].mode_seen = !!got.v;
  }
  note('latency_trials', lat);
  ok('a new plan card reaches the other window in under 2s (both trials)',
     lat.length === 2 && lat.every(x => x.card_ms < 2000), lat);
  ok('a MODE change reaches the other window as fast as the card did (both trials)',
     lat.length === 2 && lat.every(x => x.mode_ms !== null && x.mode_ms < 2000), lat);
  await A.shot(OUT.replace(/\.json$/, '_20_A.png'));
  await B.shot(OUT.replace(/\.json$/, '_21_B.png'));
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* ── R2. A stages an edit; B's tray, twice ───────────────────────── */
  const trayExpr = '(function(){var t=document.getElementById("pending-tray"); return t? {count:t.getAttribute("data-change-count"), seq:t.getAttribute("data-seq")}:null;})()';
  const trays = [];
  for (let i = 0; i < 2; i++) {
    const before = await B.ev(trayExpr);
    const r = await A.ev('fetch("/field/edit",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"dot_path=qubits.q' + (2 + i) + '.T1&value=' + (1.1 + i) + 'e-05",credentials:"same-origin"}).then(function(x){return x.status;})');
    const t = Date.now();
    const got = await B.until('(function(){var t=document.getElementById("pending-tray"); return t && t.getAttribute("data-seq")!=="' + (before && before.seq) + '";})()', 20000, 250);
    trays.push({ edit_status: r, tray_before: before, saw_change: !!got.v, waited_ms: got.ms, tray_after: await B.ev(trayExpr) });
  }
  note('tray_trials', trays);
  ok('A staging an edit is shown in B\'s tray (both trials)', trays.every(x => x.saw_change), trays);
  const aTray = await A.ev(trayExpr);
  note('A_own_tray', aTray);
  ok('the window that made the edit does show it', aTray && aTray.count !== '0', aTray);
  await B.shot(OUT.replace(/\.json$/, '_22_B_tray_after_A_edits.png'));

  /* ── R3. cancel a plan that is ALREADY cancelled (no race needed) ── */
  const pidX = await mkPlan(A, 'Kyunghoon', '/run 02a_fake_resonator q8');
  const mkc = (C, who) => C.ev('fetch("/api/agent/plans/' + pidX + '/cancel",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})');
  const c1 = await mkc(A, 'Kyunghoon');
  await sleep(1200);
  const c2 = await mkc(B, 'Minji');            // sequential, not a race
  await sleep(1500);
  const j = await A.ev('fetch("/api/agent/journal",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j);})');
  const n = ((j || '').match(/q8` cancelled by/g) || []).length;
  const whos = ((j || '').match(/q8` cancelled by [A-Za-z:_]+/g) || []);
  note('resend_cancel', { first: c1, second: c2, journal_lines: n, whos });
  ok('cancelling an ALREADY-cancelled plan does not write a second log line', n === 1, { lines: n, whos, statuses: [c1, c2] });
  const planX = await A.ev('fetch("/api/agent/plans/' + pidX + '",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify((j.plan||{}));})');
  let endedBy = null; try { endedBy = JSON.parse(planX).ended_by; } catch (e) { /* ignore */ }
  note('plan_ended_by_after_two_cancels', endedBy);
  ok('the plan still names the person who actually cancelled it', endedBy === 'human:Kyunghoon', { ended_by: endedBy });

  /* ── R4. the same approval, two people, the same moment ──────────── */
  if (APID) {
    await A.ev('AgentPanel.poll(true)'); await B.ev('AgentPanel.poll(true)'); await sleep(1500);
    const cardA = await A.ev('(function(){var c=document.querySelector("#agent-home [data-card=\'approval:' + APID + '\']"); return c? c.textContent.replace(/\\s+/g," ").slice(0,220):null;})()');
    const cardB = await B.ev('(function(){var c=document.querySelector("#agent-home [data-card=\'approval:' + APID + '\']"); return c? c.textContent.replace(/\\s+/g," ").slice(0,220):null;})()');
    note('approval_card', { A: cardA, B: cardB });
    ok('both windows show the same pending approval', !!cardA && !!cardB, { A: !!cardA, B: !!cardB });
    await A.shot(OUT.replace(/\.json$/, '_23_A_approval.png'));
    await B.shot(OUT.replace(/\.json$/, '_24_B_approval.png'));
    const mk = (C, who, verb) => C.ev('fetch("/api/agent/approvals/' + APID + '/' + verb + '",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"' + who + '"},body:"{}",credentials:"same-origin"}).then(function(r){return r.text().then(function(t){return r.status+" "+t.replace(/\\s+/g," ").slice(0,220);});})');
    const [r1, r2] = await Promise.all([mk(A, 'Kyunghoon', 'approve'), mk(B, 'Minji', 'reject')]);
    note('approval_race', { A_approve: r1, B_reject: r2 });
    const accepted = [r1, r2].filter(x => /^200/.test(x)).length;
    ok('one approval decided by two people at once yields exactly ONE decision', accepted === 1, { A: r1, B: r2 });
    await sleep(1500);
    const after = await A.ev('fetch("/api/agent/approvals",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j).slice(0,700);})');
    note('approvals_after_race', after);
    const jj = await A.ev('fetch("/api/agent/journal",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return JSON.stringify(j);})');
    const apLines = ((jj || '').match(/(approved|rejected) [^"]{0,60}/g) || []);
    note('journal_approval_lines', apLines.slice(0, 6));
    ok('the log records that decision exactly once', apLines.length === 1, apLines.slice(0, 6));
    // and the card must disappear from the OTHER window too
    const goneB = await B.until('!document.querySelector("#agent-home [data-card=\'approval:' + APID + '\']")', 40000, 250);
    note('approval_card_gone_from_B_ms', goneB.v ? goneB.ms : null);
    ok('the decided approval leaves the other person\'s screen', !!goneB.v, { ms: goneB.ms });
  }
  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));

  /* ── R5. the composer: a line that is not /run goes to a MODEL ────
     Demonstrated with fetch stubbed out, so nothing is actually started. */
  const probe = await A.ev('(function(){var calls=[]; var real=window.fetch;'
    + ' window.fetch=function(u,o){ calls.push(String(u)+" "+((o&&o.body)||"").slice(0,80)); return Promise.resolve(new Response("{}",{status:200,headers:{"Content-Type":"application/json"}})); };'
    + ' var ta=document.querySelector("#agent-home .ag-input"); ta.value="run 02a_fake_resonator q9"; ta.dispatchEvent(new Event("input",{bubbles:true}));'
    + ' AgentPanel.submit({preventDefault:function(){}, target:ta});'
    + ' return new Promise(function(res){ setTimeout(function(){ window.fetch=real; res(calls); },400); });})()');
  note('composer_without_slash', probe);
  ok('a composer line WITHOUT the leading slash goes straight to a model turn (no confirm)',
     (probe || []).some(x => /chat\/start/.test(x)), probe);
  const probe2 = await A.ev('(function(){var calls=[]; var real=window.fetch;'
    + ' window.fetch=function(u,o){ calls.push(String(u)+" "+((o&&o.body)||"").slice(0,80)); return Promise.resolve(new Response("{}",{status:200,headers:{"Content-Type":"application/json"}})); };'
    + ' var ta=document.querySelector("#agent-home .ag-input"); ta.value="/run 02a_fake_resonator q9"; ta.dispatchEvent(new Event("input",{bubbles:true}));'
    + ' AgentPanel.submit({preventDefault:function(){}, target:ta});'
    + ' return new Promise(function(res){ setTimeout(function(){ window.fetch=real; res(calls); },400); });})()');
  note('composer_with_slash', probe2);
  ok('the same line WITH the slash makes only a plan card', (probe2 || []).some(x => /agent\/plans/.test(x)) && !(probe2 || []).some(x => /chat\/start/.test(x)), probe2);

  /* ── R6. the name box: what a Hangul name does, end to end ───────── */
  await A.typeInto('#agent-home .ag-actor', '정경훈');
  const box = await A.ev('document.querySelector("#agent-home .ag-actor").value');
  const stored = await A.ev('AgentPanel.actorName()');
  const hdr = await A.ev('(function(){var calls=[]; var real=window.fetch;'
    + ' window.fetch=function(u,o){ calls.push({u:String(u), actor:(o&&o.headers&&o.headers["X-SM-Actor"])||null}); return real.apply(window,arguments); };'
    + ' return AgentPanel.poll(true).then(function(){ window.fetch=real; return calls; });})()');
  note('hangul_end_to_end', { box, stored, header_on_next_call: hdr });
  ok('a Hangul name shown in the box is the name SM records', stored === '정경훈', { box, stored });
  ok('the box at least shows the user what SM kept', box === stored, { box, stored });
  await A.shot(OUT.replace(/\.json$/, '_25_A_hangul_name.png'));

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
