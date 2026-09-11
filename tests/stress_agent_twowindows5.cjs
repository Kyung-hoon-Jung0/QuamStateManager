/* Round 5: the sharpest two-person case -- B is CHOOSING the mode (so the
 * select has focus) while A changes it. What does B's screen say, and what
 * would B's Start actually run under?
 * argv[2]=json out, argv[3]=CDP, argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9434';
const BASE = process.argv[4] || 'http://127.0.0.1:5434';
const results = [];
const notes = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }
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
      if (m.method === 'Runtime.exceptionThrown') errors.push({ who: label, kind: 'exception', text: JSON.stringify(m.params.exceptionDetails).slice(0, 200) });
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push({ who: label, kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    };
    const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expr) => {
      const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (rr.result && rr.result.exceptionDetails) throw new Error(label + ': ' + JSON.stringify(rr.result.exceptionDetails).slice(0, 200));
      return rr.result.result.value;
    };
    const until = async (expr, ms, step) => {
      const t = Date.now();
      for (;;) { let v = null; try { v = await ev(expr); } catch (e) { v = null; } if (v) return { v, ms: Date.now() - t }; if (Date.now() - t > ms) return { v: null, ms: Date.now() - t }; await sleep(step || 100); }
    };
    const shot = async (p) => { const s = await send('Page.captureScreenshot', { format: 'png' }); if (s.result && s.result.data) fs.writeFileSync(p, Buffer.from(s.result.data, 'base64')); };
    await send('Page.enable'); await send('Runtime.enable');
    await send('Network.setCacheDisabled', { cacheDisabled: true });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });
    res({ send, ev, until, shot, label });
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
  const list = (await bsend('Target.getTargets', {})).result.targetInfos.filter(t => t.type === 'page');
  for (const t of list) { try { await bsend('Target.closeTarget', { targetId: t.targetId }); } catch (e) { /* ignore */ } }
  await sleep(800);
  const ctxA = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const ctxB = (await bsend('Target.createBrowserContext', {})).result.browserContextId;
  const tA = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxA })).result.targetId;
  const tB = (await bsend('Target.createTarget', { url: 'about:blank', browserContextId: ctxB })).result.targetId;
  const A = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tA, 'A', errors);
  const B = await makeConn('ws://127.0.0.1:' + CDP + '/devtools/page/' + tB, 'B', errors);
  for (const C of [A, B]) { await C.send('Page.navigate', { url: BASE + '/' }); await C.until('!!document.querySelector("#agent-home .ag-root .ag-cards")', 30000); }
  await A.ev('AgentPanel.setActor("Kyunghoon"); 1');
  await B.ev('AgentPanel.setActor("Minji"); 1');

  const pid = await A.ev('fetch("/api/agent/plans",{method:"POST",headers:{"Content-Type":"application/json",Accept:"application/json","X-SM-Actor":"Kyunghoon"},body:JSON.stringify({run_line:"/run 02a_fake_resonator q14"}),credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){return (j.plan||{}).id||"ERR";})');
  note('plan', pid);
  const s = '#agent-home [data-card=\'plan:' + pid + '\'] .ag-mode select';
  await B.until('!!document.querySelector("' + s + '")', 30000, 80);

  // B is choosing the mode: it picks ask-writes, and its select keeps focus
  await B.ev('(function(){var e=document.querySelector("' + s + '"); e.focus(); e.value="ask-writes"; e.dispatchEvent(new Event("change",{bubbles:true})); return 1;})()');
  await sleep(2000);
  const focusedB = await B.ev('(function(){var e=document.querySelector("' + s + '"); return document.activeElement===e;})()');
  note('B_select_focused', focusedB);
  ok('B has the mode picker focused, as a person choosing a mode would', focusedB === true);

  // A now changes the same plan's mode to auto
  await A.ev('fetch("/api/agent/plans/' + pid + '/mode",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:JSON.stringify({mode:"auto"}),credentials:"same-origin"}).then(function(r){return r.status;})');
  await sleep(12000);           // well past both the 4-5s and any push
  const shows = await B.ev('(function(){var e=document.querySelector("' + s + '"); return e? e.value:null;})()');
  const server = await B.ev('fetch("/api/agent/plans/' + pid + '",{headers:{Accept:"application/json"}}).then(function(r){return r.json();}).then(function(j){return (j.plan||{}).mode;})');
  note('after_12s', { B_shows: shows, server_holds: server, B_still_focused: await B.ev('(function(){var e=document.querySelector("' + s + '"); return document.activeElement===e;})()') });
  ok('B\'s screen shows the mode the chip would actually run under', shows === server, { B_shows: shows, server: server });
  await B.shot(OUT.replace(/\.json$/, '_40_B_stale_mode.png'));
  await A.shot(OUT.replace(/\.json$/, '_41_A_mode.png'));

  // and once B clicks away it catches up
  await B.ev('(function(){var e=document.querySelector("' + s + '"); e.blur(); return 1;})()');
  const c = await B.until('(function(){var e=document.querySelector("' + s + '"); return e && e.value==="' + server + '";})()', 40000, 120);
  note('catchup_after_blur_ms', c.v ? c.ms : null);
  ok('it catches up once B clicks away', !!c.v, { ms: c.ms });

  // the feed itself: is a mode change announced anywhere B can see it?
  const feed = await B.ev('(function(){var h=document.querySelector("#agent-home .ag-cards"); return h? h.textContent.replace(/\\s+/g," "):"";})()');
  note('feed_mentions_mode_change', /mode set to/.test(feed));
  ok('the card feed says a person changed the mode', /mode set to/.test(feed), feed.slice(-300));

  // clean up this plan
  await A.ev('fetch("/api/agent/plans/' + pid + '/cancel",{method:"POST",headers:{"Content-Type":"application/json","X-SM-Actor":"Kyunghoon"},body:"{}",credentials:"same-origin"}).then(function(r){return r.status;})');

  fs.writeFileSync(OUT, JSON.stringify({ results, notes, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks ' + (results.length - bad.length) + '/' + results.length + '  console errors ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 300)));
  notes.forEach(n => console.log('  NOTE ' + n.k + ' = ' + JSON.stringify(n.v).slice(0, 300)));
  process.exit(0);
}
main().catch(e => { console.error('driver error: ' + (e && e.stack || e)); try { fs.writeFileSync(OUT, JSON.stringify({ results, notes, driver: String(e) }, null, 1)); } catch (x) {} process.exit(1); });
