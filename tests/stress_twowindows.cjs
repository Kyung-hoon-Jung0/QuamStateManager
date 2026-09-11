/* TWO PEOPLE, TWO WINDOWS, ONE CHIP.
 *
 * The multi-user case a shared lab hits: one SM server, one open chip, two
 * browsers. A arms / changes a mode / stages an edit; B is looking at the same
 * panel. What does B see, how fast, and does anything say WHO did it?
 *
 * Two ISOLATED browser contexts (separate localStorage) = two people at two
 * machines, which is what "two people" means. The same-profile two-tab case is
 * probed separately in phase F.
 *
 * argv[2]=json out prefix, argv[3]=CDP, argv[4]=base, argv[5]=phase
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9452';
const BASE = process.argv[4] || 'http://127.0.0.1:5452';
const PHASE = process.argv[5] || 'A';

const results = [], notes = [], errors = [], shots = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }
function note(k, v) { notes.push({ k, v }); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

function makeConn(wsUrl, label) {
  return new Promise(async res => {
    const ws = new WebSocket(wsUrl);
    await new Promise(r => ws.onopen = r);
    let id = 0; const pending = new Map();
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        errors.push({ who: label, kind: 'exception', text: String((d.exception && (d.exception.description || d.exception.value)) || d.text).slice(0, 300) });
      }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        errors.push({ who: label, kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ').slice(0, 300) });
      }
    };
    const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expr) => {
      const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (rr.result && rr.result.exceptionDetails) {
        const x = rr.result.exceptionDetails.exception;
        throw new Error(label + ': ' + ((x && (x.description || x.value)) || 'eval failed') + ' :: ' + expr.slice(0, 140));
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
        await sleep(step || 100);
      }
    };
    const shot = async (p) => {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      if (s.result && s.result.data) { fs.writeFileSync(p, Buffer.from(s.result.data, 'base64')); shots.push(p); }
    };
    // a real click at the element's own centre
    const click = async (sel) => {
      const box = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; e.scrollIntoView({block:'center'}); var r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
      if (!box) return false;
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      return true;
    };
    const typeChar = async (ch) => {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    };
    const typeInto = async (sel, text) => {
      await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0; e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
      for (const ch of text) await typeChar(ch);
      await sleep(150);
    };
    const enter = async () => {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', text: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', windowsVirtualKeyCode: 13 });
    };
    await send('Page.enable'); await send('Runtime.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });     // BOTH windows are "in front"
    res({ ws, send, ev, until, shot, click, typeChar, typeInto, enter, label });
  });
}

async function browser() {
  const ver = await (await fetch('http://127.0.0.1:' + CDP + '/json/version')).json();
  const bws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise(r => bws.onopen = r);
  let bid = 0; const bpend = new Map();
  bws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && bpend.has(m.id)) { bpend.get(m.id)(m); bpend.delete(m.id); } };
  const bsend = (method, params = {}) => new Promise(r => { const i = ++bid; bpend.set(i, r); bws.send(JSON.stringify({ id: i, method, params })); });
  return bsend;
}

const api = (path, body, actor, method) => fetch(BASE + path, {
  method: method || (body === undefined ? 'GET' : 'POST'),
  headers: Object.assign({ 'Content-Type': 'application/json', 'Origin': BASE }, actor ? { 'X-SM-Actor': actor } : {}),
  body: body === undefined ? undefined : JSON.stringify(body),
}).then(r => r.json().then(j => ({ status: r.status, body: j })).catch(() => ({ status: r.status, body: {} })));

const CARDS = '#agent-home .ag-root .ag-cards';

async function main() {
  const bsend = await browser();
  const olds = (await bsend('Target.getTargets', {})).result.targetInfos.filter(t => t.type === 'page');
  for (const t of olds) { try { await bsend('Target.closeTarget', { targetId: t.targetId }); } catch (e) { /* ignore */ } }
  await sleep(600);
  const ctxA = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const ctxB = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const tA = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxA })).result.targetId;
  const tB = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxB })).result.targetId;
  const A = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tA, 'A');
  const B = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tB, 'B');
  for (const C of [A, B]) {
    await C.send('Page.navigate', { url: BASE + '/' });
    const r = await C.until('!!document.querySelector("' + CARDS + '")', 40000);
    note(C.label + '_home_ms', r.ms);
    ok(C.label + ' lands on the agent home with a chip open', !!r.v);
  }

  const TA = '#agent-home .ag-root .ag-input';
  const ACTOR = '#agent-home .ag-root .ag-actor';
  const cleanPlans = async () => {
    const ps = ((await api('/api/agent/plans')).body.plans) || [];
    for (const p of ps) if (p.status === 'draft') await api('/api/agent/plans/' + p.id + '/cancel', {}, 'cleanup');
  };

  /* ═════════ A — the name box, and how fast a card crosses ═════════ */
  if (PHASE === 'A') {
    ok('both windows have a name box', await A.ev(`!!document.querySelector("${ACTOR}")`) && await B.ev(`!!document.querySelector("${ACTOR}")`));
    await A.typeInto(ACTOR, 'Kyunghoon');
    ok("A's name is kept", (await A.ev('localStorage.getItem("quam_actor_name")')) === 'Kyunghoon');

    // SM is English-only for this box BY DECISION. Verify the strip IS what happens.
    await B.typeInto(ACTOR, '민지 Minji');
    const bBox = await B.ev(`document.querySelector("${ACTOR}").value`);
    const bStored = await B.ev('localStorage.getItem("quam_actor_name")');
    note('B_box_after_hangul', bBox); note('B_stored_after_hangul', bStored);
    ok('a non-ASCII name is stripped to its ASCII part (by design)', bStored === 'Minji', { box: bBox, stored: bStored });
    ok('…and the BOX shows what was stored (no silent divergence)', bBox === bStored, { box: bBox, stored: bStored });

    await B.typeInto(TA, '큐비트 측정해줘');
    const taVal = await B.ev(`document.querySelector("${TA}").value`);
    ok('the composer accepts Hangul (only the NAME box is ASCII-only)', /[가-힣]/.test(String(taVal)), taVal);
    await B.ev(`(function(){var e=document.querySelector("${TA}"); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);

    const cases = [['empty', ''], ['a quote', 'Kim "Danny" Lee'], ['a backslash', 'LAB\\kyunghoon'],
                   ['200 characters', 'X'.repeat(200)], ['a newline', 'Kyung\nhoon'], ['only Hangul', '민지']];
    const nameRows = [];
    for (const [label, v] of cases) {
      await B.ev(`AgentPanel.setActor(${JSON.stringify(v)}); 1`);
      const stored = await B.ev('localStorage.getItem("quam_actor_name")');
      const r = await B.ev(`fetch("/api/agent/plans",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"` +
        (stored ? `,"X-SM-Actor":${JSON.stringify(stored)}` : '') + `},body:JSON.stringify({run_line:"/run 02a_fake_resonator q3"}),credentials:"same-origin"})` +
        `.then(function(r){return r.json().then(function(j){return {s:r.status, by:(j.plan||{}).created_by||j.error||"?", id:(j.plan||{}).id};});}).catch(function(e){return {s:-1, by:String(e).slice(0,80)};})`);
      nameRows.push({ label, len: v.length, stored, storedLen: (stored || '').length, status: r.s, created_by: r.by });
      if (r.id) await api('/api/agent/plans/' + r.id + '/cancel', {}, 'cleanup');
    }
    note('name_cases', nameRows);
    ok('no hostile name breaks the request', nameRows.every(x => x.status === 200), nameRows.map(x => x.status));
    ok('a newline never rides a header raw', !/\n/.test(String((nameRows.find(x => x.label === 'a newline') || {}).stored || '')));
    ok('an all-Hangul name leaves the person ANONYMOUS rather than named',
       (nameRows.find(x => x.label === 'only Hangul') || {}).created_by === 'human',
       nameRows.find(x => x.label === 'only Hangul'));
    ok('a 200-character name is recorded whole, so the feed must live with it',
       String((nameRows.find(x => x.label === '200 characters') || {}).created_by || '').length > 150,
       String((nameRows.find(x => x.label === '200 characters') || {}).created_by || '').length);
    await B.ev('AgentPanel.setActor("Minji"); 1');
    await B.ev(`(function(){var e=document.querySelector("${ACTOR}"); e.value="Minji"; return 1;})()`);
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1');

    await cleanPlans();
    await sleep(1200);
    const idsExpr = `JSON.stringify(Array.prototype.map.call(document.querySelectorAll("${CARDS} [data-card^='plan:']"), function(e){return e.getAttribute("data-card");}))`;
    const known = await A.ev(idsExpr);
    const t0 = Date.now();
    await A.ev(`(function(){var e=document.querySelector("${TA}"); e.focus(); e.value=""; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()`);
    for (const ch of '/run 02a_fake_resonator q14') await A.typeChar(ch);
    await A.enter();
    const seenA = await A.until(`(function(){var k=${known}; return JSON.parse(${idsExpr}).filter(function(x){return k.indexOf(x)<0;})[0]||null;})()`, 20000, 60);
    const pid = String(seenA.v || '').split(':')[1] || null;
    note('plan_id', pid); note('A_sees_own_card_ms', seenA.ms);
    ok("A's typed /run line makes a card in A's own window", !!pid, seenA.ms);
    const seenB = await B.until(`!!document.querySelector("${CARDS} [data-card='plan:${pid}']")`, 45000, 100);
    note('B_sees_A_card_ms', seenB.ms); note('B_sees_card_since_press_ms', Date.now() - t0);
    ok('B sees the card A made', !!seenB.v, seenB.ms);
    ok('…inside 5 s', seenB.v && seenB.ms < 5000, seenB.ms);
    const origin = await B.ev(`(function(){var e=document.querySelector("${CARDS} [data-card='plan:${pid}'] .ag-plan-head"); return e? e.textContent.replace(/\\s+/g," ").trim() : null;})()`);
    note('B_card_origin_text', origin);
    ok('the card B sees names WHO made it', /Kyunghoon/.test(String(origin)), origin);
    await A.shot(OUT + '_A01_made_card.png'); await B.shot(OUT + '_B01_sees_card.png');
    note('final_plan_id', pid);
  }

  /* ═════════ M — the plan MODE: A changes it, B is watching ═════════ */
  if (PHASE === 'M') {
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1'); await B.ev('AgentPanel.setActor("Minji"); 1');
    await cleanPlans();
    const trials = [];
    for (let round = 1; round <= 2; round++) {
      const mk = await api('/api/agent/plans', { run_line: '/run 02a_fake_resonator q' + (13 + round) }, 'Kyunghoon');
      const pid = mk.body.plan.id;
      const sel = `${CARDS} [data-card='plan:${pid}'] .ag-mode select`;
      await A.until(`!!document.querySelector("${sel}")`, 40000, 80);
      await B.until(`!!document.querySelector("${sel}")`, 40000, 80);
      const t0 = Date.now();
      await A.ev(`AgentPanel.setPlanMode(${JSON.stringify(pid)}, "auto"); 1`);
      await sleep(400);
      const srv = ((await api('/api/agent/plans/' + pid)).body.plan || {});
      const aShows = await A.until(`(document.querySelector("${sel}")||{}).value === "auto"`, 20000, 100);
      const bAt1s = await B.ev(`(document.querySelector("${sel}")||{}).value`);
      if (round === 1) { await A.shot(OUT + '_A20_mode_auto.png'); await B.shot(OUT + '_B20_mode_stale.png'); }
      const bShows = await B.until(`(document.querySelector("${sel}")||{}).value === "auto"`, 60000, 200);
      trials.push({ round, pid, server: srv.mode, A_ms: aShows.ms, B_shows_at_1s: bAt1s, B_ms: bShows.ms, B_got: !!bShows.v });
      note('trial' + round, trials[trials.length - 1]);
      await api('/api/agent/plans/' + pid + '/cancel', {}, 'cleanup');
    }
    ok('A sees its own mode change at once', trials.every(t => t.A_ms < 1500), trials.map(t => t.A_ms));
    ok('the server took the change', trials.every(t => t.server === 'auto'), trials.map(t => t.server));
    ok('B still showed the OLD mode a second later, twice', trials.every(t => t.B_shows_at_1s === 'ask-writes'), trials.map(t => t.B_shows_at_1s));
    ok('B catches up inside 5 s', trials.every(t => t.B_got && t.B_ms < 5000), trials.map(t => t.B_ms));
    const t1 = Date.now();
    const mk2 = await api('/api/agent/plans', { run_line: '/run 09a_fake_rabi q2' }, 'Kyunghoon');
    const p2 = mk2.body.plan.id;
    const wake = await B.until(`!!document.querySelector("${CARDS} [data-card='plan:${p2}']")`, 45000, 80);
    note('B_sees_new_card_ms', wake.ms);
    ok('a NEW card reaches B in under 2 s (so the channel itself works)', wake.v && wake.ms < 2000, wake.ms);
    await api('/api/agent/plans/' + p2 + '/cancel', {}, 'cleanup');
  }

  /* ═════════ C — both press the same thing at the same moment ═════════ */
  if (PHASE === 'C') {
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1'); await B.ev('AgentPanel.setActor("Minji"); 1');
    await cleanPlans();
    const mk = await api('/api/agent/plans', { run_line: '/run 02a_fake_resonator q9' }, 'Kyunghoon');
    const pid = mk.body.plan.id; note('plan_id', pid);
    const sel = `${CARDS} [data-card='plan:${pid}'] .ag-mode select`;
    await A.until(`!!document.querySelector("${sel}")`, 40000, 80);
    await B.until(`!!document.querySelector("${sel}")`, 40000, 80);
    await Promise.all([
      A.ev(`AgentPanel.setPlanMode(${JSON.stringify(pid)}, "auto"); 1`),
      B.ev(`AgentPanel.setPlanMode(${JSON.stringify(pid)}, "ask-all"); 1`),
    ]);
    await sleep(2000);
    const serverMode = ((await api('/api/agent/plans/' + pid)).body.plan || {}).mode;
    note('mode_after_simultaneous', serverMode);
    ok('two simultaneous mode presses leave ONE mode', serverMode === 'auto' || serverMode === 'ask-all', serverMode);
    const jr = await api('/api/agent/journal'); const L = JSON.stringify(jr.body);
    note('mode_lines', { auto: (L.match(/mode set to auto/g) || []).length, askall: (L.match(/mode set to ask-all/g) || []).length });
    ok('the log names BOTH people who pressed', /Kyunghoon/.test(L) && /Minji/.test(L));
    const aSees = await A.until(`(document.querySelector("${sel}")||{}).value === ${JSON.stringify(serverMode)}`, 45000, 200);
    const bSees = await B.until(`(document.querySelector("${sel}")||{}).value === ${JSON.stringify(serverMode)}`, 45000, 200);
    note('converge', { A: aSees.ms, B: bSees.ms });
    ok('both windows end up on the mode the server holds', !!aSees.v && !!bSees.v, { A: aSees.ms, B: bSees.ms });

    const c = await Promise.all([
      A.ev(`fetch("/api/agent/plans/${pid}/cancel",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})`),
      B.ev(`fetch("/api/agent/plans/${pid}/cancel",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Minji"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})`),
    ]);
    note('double_cancel_statuses', c);
    await sleep(1200);
    const L2 = JSON.stringify((await api('/api/agent/journal')).body);
    const nCancel = (L2.match(/cancelled by/g) || []).length;
    note('cancel_lines_in_log', nCancel);
    ok('two simultaneous Cancels are not recorded twice', nCancel <= 1, { statuses: c, lines: nCancel });
    ok('both presses are answered', c.every(x => x === 200 || x === 409), c);

    const aid = process.env.SM_APPROVAL_ID || null;
    note('approval_fixture', aid);
    if (aid) {
      const d = await Promise.all([
        A.ev(`fetch("/api/agent/approvals/${aid}/approve",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:"{}",credentials:"same-origin"}).then(function(r){return r.json().then(function(j){return r.status+"|"+(j.error||(j.approval||{}).status||"");});})`),
        B.ev(`fetch("/api/agent/approvals/${aid}/reject",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Minji"},body:"{}",credentials:"same-origin"}).then(function(r){return r.json().then(function(j){return r.status+"|"+(j.error||(j.approval||{}).status||"");});})`),
      ]);
      note('approve_vs_reject', d);
      const ap = (await api('/api/agent/approvals')).body;
      note('approvals_after', { pending: (ap.pending || []).length, recent: (ap.recent || []).slice(-2) });
      ok('one approval gets ONE verdict', d.filter(x => /^200/.test(x)).length === 1, d);
      ok('…and the loser is told why', d.some(x => /^(404|409)/.test(x)), d);
      const L3 = JSON.stringify((await api('/api/agent/journal')).body);
      const line = (L3.match(/(approved|rejected)[^\\]{0,120}/g) || []).slice(-3);
      note('approval_log_lines', line);
      ok('the Calibration log names WHO decided the approval', /(approved|rejected)[^\\]{0,120}(Kyunghoon|Minji)/.test(L3), line);
    }
    await A.shot(OUT + '_A30_after_races.png'); await B.shot(OUT + '_B30_after_races.png');
  }

  /* ═════════ O — observer is per WINDOW ═════════ */
  if (PHASE === 'O') {
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1'); await B.ev('AgentPanel.setActor("Minji"); 1');
    await cleanPlans();
    const mk = await api('/api/agent/plans', { run_line: '/run 02a_fake_resonator q5' }, 'Kyunghoon');
    const pid = mk.body.plan.id;
    const card = `${CARDS} [data-card='plan:${pid}']`;
    await A.until(`!!document.querySelector("${card}")`, 40000, 80);
    await B.until(`!!document.querySelector("${card}")`, 40000, 80);
    const doors = async (C) => C.ev(`(function(){var c=document.querySelector("${card}"); var r=document.querySelector("#agent-home .ag-root");return {start:!!(c&&c.querySelector(".ag-start")), cancel:!!(c&&c.querySelector(".ag-cancel")), modeDisabled:!!(c&&c.querySelector(".ag-mode select")&&c.querySelector(".ag-mode select").disabled), approve:!!r.querySelector(".ag-approve"), sendVisible:!!r.querySelector(".ag-send"), observing:!!r.querySelector(".ag-observing")};})()`);
    note('A_doors_before', await doors(A)); note('B_doors_before', await doors(B));
    const clicked = await A.click('#agent-home .ag-root .ag-observer input');
    await sleep(900);
    const aAfter = await doors(A), bAfter = await doors(B);
    note('A_doors_observer', aAfter); note('B_doors_while_A_observes', bAfter);
    ok('the observer box is a real click target', clicked === true);
    ok('observer hides Start on A', aAfter.start === false, aAfter);
    ok('observer hides Cancel on A', aAfter.cancel === false, aAfter);
    ok('observer disables the mode picker on A', aAfter.modeDisabled === true, aAfter);
    ok('observer says so on A', aAfter.observing === true, aAfter);
    ok('B is UNAFFECTED — its Start is still there', bAfter.start === true, bAfter);

    await A.ev(`(function(){var e=document.querySelector("${TA}"); e.focus(); e.value=""; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()`);
    for (const ch of 'what is q1 doing') await A.typeChar(ch);
    const before = ((await api('/api/agent/chat/cards?after=0')).body.cards || []).length;
    await A.enter();
    await sleep(1800);
    const after = ((await api('/api/agent/chat/cards?after=0')).body.cards || []).length;
    const toast = await A.ev(`(function(){var t=document.querySelector("#ag-toast:not([hidden]), .toast"); return t? t.textContent.replace(/\\s+/g," ").trim().slice(0,220) : null;})()`);
    const stillTyped = await A.ev(`document.querySelector("${TA}").value`);
    note('observer_send_toast', toast); note('observer_cards_before_after', [before, after]); note('observer_text_kept', stillTyped);
    ok('Send is refused while observing — nothing was sent', after === before, [before, after]);
    ok('…and A is TOLD why', /observ/i.test(String(toast)), toast);
    ok('…and what A typed is not thrown away', String(stillTyped).indexOf('what is q1') === 0, stillTyped);
    ok("B's Send button is still there", bAfter.sendVisible === true, bAfter);
    await A.shot(OUT + '_A40_observer.png'); await B.shot(OUT + '_B40_not_observer.png');

    const plansBefore = ((await api('/api/agent/plans')).body.plans || []).length;
    await A.ev(`(function(){var e=document.querySelector("${TA}"); e.focus(); e.value="/run 02a_fake_resonator q6"; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()`);
    await A.enter(); await sleep(1500);
    const plansAfter = ((await api('/api/agent/plans')).body.plans || []).length;
    note('observer_run_line_plans', [plansBefore, plansAfter]);
    ok('an observer window cannot even make a plan card', plansAfter === plansBefore, [plansBefore, plansAfter]);

    await A.click('#agent-home .ag-root .ag-observer input'); await sleep(700);
    const back = await doors(A);
    ok('unticking gives A its doors back', back.start === true, back);
    const srvGate = await A.ev(`fetch("/api/agent/plans/${pid}/mode",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:JSON.stringify({mode:"ask-all"}),credentials:"same-origin"}).then(function(r){return r.status;})`);
    note('server_accepts_from_any_window', srvGate);
    ok('observer is a WINDOW guard, not a server permission (its own tooltip says so)', srvGate === 200, srvGate);
    await api('/api/agent/plans/' + pid + '/cancel', {}, 'cleanup');
  }

  /* ═════════ E — A stages an edit elsewhere; does B know? ═════════ */
  if (PHASE === 'E') {
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1'); await B.ev('AgentPanel.setActor("Minji"); 1');
    const trayOf = async (C) => C.ev(`(function(){var t=document.getElementById("pending-tray"); if(!t) return null; return {n:t.getAttribute("data-change-count"), txt:(t.textContent||"").replace(/\\s+/g," ").trim().slice(0,140)};})()`);
    note('A_tray_before', await trayOf(A)); note('B_tray_before', await trayOf(B));
    await A.send('Page.navigate', { url: BASE + '/bulk' });
    const grid = await A.until('!!document.querySelector("input.bulk-cell[data-dot-path]")', 90000, 250);
    note('A_bulk_ready_ms', grid.ms);
    const cell = await A.ev(`(function(){var i=document.querySelector('input.bulk-cell[data-dot-path]'); if(!i) return null; return {path:i.getAttribute("data-dot-path"), val:i.value};})()`);
    note('A_cell', cell);
    ok('A reached a real editable cell', !!cell, cell);
    if (cell) {
      await A.ev(`(function(){var all=document.querySelectorAll('input.bulk-cell[data-dot-path]'); var i=all[0]; i.focus(); i.select(); return 1;})()`);
      for (const ch of '4312405999') await A.typeChar(ch);
      await A.enter();
      await sleep(1200);
      await A.ev(`(function(){var i=document.querySelector('input.bulk-cell[data-dot-path]'); i.blur(); return 1;})()`);
      await sleep(3000);
      const aTray = await trayOf(A);
      note('A_tray_after_edit', aTray);
      ok("A's own tray counts the edit", aTray && Number(aTray.n) > 0, aTray);
      const bSaw = await B.until(`(function(){var t=document.getElementById("pending-tray"); return t && Number(t.getAttribute("data-change-count")||0) > 0;})()`, 45000, 250);
      note('B_tray_notices_ms', bSaw.ms); note('B_tray_after', await trayOf(B));
      ok('B — sitting on the Agent tab — learns there are edits it has not seen', !!bSaw.v, bSaw.ms);
      const bSeen = Number(((await trayOf(B)) || {}).n || 0);
      const gate = await B.ev(`fetch("/state/apply-to-live",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"seen_changes=${bSeen}",credentials:"same-origin"}).then(function(r){return r.text().then(function(t){return r.status+"|"+t.replace(/\\s+/g," ").slice(0,260);});})`);
      note('B_apply_with_its_own_count', gate);
      ok('the unseen-edit gate answers B honestly', /^(409|200)/.test(String(gate)), gate);
      const agentTxt = await B.ev(`(function(){var r=document.querySelector("#agent-home .ag-root"); return r? r.textContent.replace(/\\s+/g," ").slice(0,500):"";})()`);
      note('B_agent_panel_text', agentTxt);
      ok('the Agent panel itself mentions the pending edits', /edit|pending|unapplied|change/i.test(agentTxt), agentTxt.slice(0, 240));
      await A.shot(OUT + '_A50_edited.png'); await B.shot(OUT + '_B50_agent_tab.png');
    }
  }

  /* ═════════ X — one window closes mid-flow ═════════ */
  if (PHASE === 'X') {
    await A.ev('AgentPanel.setActor("Kyunghoon"); 1'); await B.ev('AgentPanel.setActor("Minji"); 1');
    await cleanPlans();
    const mk = await api('/api/agent/plans', { run_line: '/run 09a_fake_rabi q11' }, 'Kyunghoon');
    const pid = mk.body.plan.id;
    const card = `${CARDS} [data-card='plan:${pid}']`;
    await B.until(`!!document.querySelector("${card}")`, 40000, 80);
    await A.until(`!!document.querySelector("${card}")`, 40000, 80);
    await A.ev(`(function(){var e=document.querySelector("${TA}"); e.focus(); e.value="half a sentence"; e.dispatchEvent(new Event("input",{bubbles:true})); return 1;})()`);
    await bsend('Target.closeTarget', { targetId: tA });
    await sleep(5000);
    const stillThere = await B.ev(`!!document.querySelector("${card}")`);
    const headTxt = await B.ev(`(function(){var e=document.querySelector("${card} .ag-plan-head"); return e? e.textContent.replace(/\\s+/g," ").trim():null;})()`);
    note('B_card_after_A_closed', headTxt);
    ok("A's card survives A's window closing (it is server state)", stillThere === true);
    ok('…and it still names its author', /Kyunghoon/.test(String(headTxt)), headTxt);
    const errsB = errors.filter(e => e.who === 'B');
    note('B_console_errors_after_close', errsB.length);
    ok('B sees no error when the other window goes', errsB.filter(e => !/EvalError/.test(e.text)).length === 0, errsB.slice(0, 3));
    const banner = await B.ev(`(function(){var t=document.body.textContent||""; var m=t.match(/[^.]*another window[^.]*/i); return m? m[0].replace(/\\s+/g," ").trim().slice(0,200):null;})()`);
    note('B_other_window_banner', banner);
    ok('nothing in B claims to know who else is here, so nothing is now wrong', banner === null, banner);
    const nowTxt = await B.ev(`(function(){var e=document.querySelector("#agent-home .ag-root .ag-now"); return e? e.textContent.replace(/\\s+/g," ").trim().slice(0,220):null;})()`);
    note('B_now_strip_after_close', nowTxt);
    await B.shot(OUT + '_B60_after_A_closed.png');
    await api('/api/agent/plans/' + pid + '/cancel', {}, 'cleanup');
  }

  const out = { phase: PHASE, results, notes, errors, shots };
  fs.writeFileSync(OUT + '_' + PHASE + '.json', JSON.stringify(out, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('PHASE ' + PHASE + ': ' + (results.length - bad.length) + '/' + results.length + ' pass, ' + errors.length + ' console/exception');
  bad.forEach(b => console.log('  FAIL ' + b.name + ' :: ' + String(JSON.stringify(b.detail)).slice(0, 300)));
  notes.forEach(n => console.log('  note ' + n.k + ' = ' + String(JSON.stringify(n.v)).slice(0, 400)));
  errors.slice(0, 12).forEach(e => console.log('  ERR[' + e.who + '] ' + e.kind + ': ' + e.text.slice(0, 200)));
  process.exit(0);
}
main().catch(e => { console.log('DRIVER CRASH: ' + (e && e.stack || e)); fs.writeFileSync(OUT + '_' + PHASE + '.json', JSON.stringify({ phase: PHASE, crash: String(e && e.stack || e), results, notes, errors }, null, 1)); process.exit(1); });
