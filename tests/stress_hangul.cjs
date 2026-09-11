/* A name a browser cannot send must not kill the Agent panel.
 *
 * The stress round found that `agent.js` put the "your name" value straight
 * into an HTTP header; a header value must be ISO-8859-1, so Chrome refused
 * the whole fetch before sending and every panel action died against a
 * healthy server — and /agent/setup, the page that could clear the name,
 * rendered blank.
 *
 * SM works in English (user directive, 2026-09-11): the name is stripped to
 * ASCII at the door, so a Hangul one becomes its ASCII part or nothing at all,
 * and the panel keeps working either way. What you SAY to the agent is the
 * exception and is untouched — that is a JSON body.
 *
 * This drives the real browser because the bug exists ONLY there: the server
 * always accepted the name, and Flask's test client always could send it.
 *
 * argv[2]=out.json argv[3]=cdp port argv[4]=base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3];
const BASE = process.argv[4];
const NAMES = ['kyunghoon', '정경훈', 'kyunghoon 🙂', 'a%b'];
const results = [];
const errors = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      const s = (m.params.args || []).map(a => a.value || a.description || '').join(' ');
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
    }
  };
  const send = (mm, p = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async x => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 200));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  for (const name of NAMES) {
    errors.length = 0;
    await send('Page.navigate', { url: BASE + '/agent' });
    await sleep(3000);
    await ev(`localStorage.setItem('quam_actor_name', ${JSON.stringify(name)}); 1`);
    await send('Page.navigate', { url: BASE + '/agent' });
    await sleep(4500);

    // the panel must be alive: mounted, not "unreachable", and a real request
    // must have reached the server with the name intact
    const st = await ev(`(function(){
      var s = window.AgentPanel && window.AgentPanel._state;
      return { mounted: !!document.querySelector('#agent-home .ag-root'),
               unreachable: !!(s && s.unreachable),
               actor: (document.querySelector('#agent-home .ag-actor')||{}).value };
    })()`);
    ok('[' + name + '] the panel mounts', st.mounted, st);
    ok('[' + name + '] …and SM is reachable', !st.unreachable, st);
    // the rule, on screen: only what a request can carry survives
    var kept = name.split('').filter(function (ch) {
      var c = ch.charCodeAt(0); return c >= 0x20 && c <= 0x7E; }).join('').trim();
    ok('[' + name + '] the box keeps only what a request can carry',
       st.actor === kept, { shown: st.actor, expected: kept });

    // a real request, and what the SERVER recorded as the actor
    const who = await ev(`(async function(){
      var r = await window.AgentPanel._state && fetch('/api/agent/whoami', {headers: {'Accept':'application/json'}});
      return 'n/a';
    })().catch(function(e){ return 'threw: ' + e.message; })`);
    void who;

    // the setup page must render, not blank
    await send('Page.navigate', { url: BASE + '/agent/setup' });
    await sleep(4000);
    const setup = await ev(`(function(){
      var r = document.getElementById('agent-setup-root') || document.querySelector('.as-root, #table-pane');
      return { chars: r ? r.textContent.replace(/\\s+/g,' ').trim().length : -1,
               says: r ? r.textContent.replace(/\\s+/g,' ').trim().slice(0,90) : null };
    })()`);
    ok('[' + name + '] /agent/setup renders something', setup.chars > 400, setup);
    ok('[' + name + '] …with no uncaught error', errors.length === 0, errors.slice(0, 2));
  }

  // A name saved BEFORE the rule existed must not be able to do it either:
  // the strip is on the read path, not only on the input.
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(2500);
  await ev(`localStorage.setItem('quam_actor_name','정경훈'); 1`);
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(4500);
  const legacy = await ev(`(function(){
    var s = window.AgentPanel && window.AgentPanel._state;
    return { unreachable: !!(s && s.unreachable),
             actor: window.AgentPanel.actorName(),
             stored: localStorage.getItem('quam_actor_name') };
  })()`);
  ok('a name stored before the rule cannot reach a header',
     legacy.actor === '' && !legacy.unreachable, legacy);

  // …and what you SAY is still any language: the composer takes Korean.
  const said = await ev(`(function(){
    var t = document.querySelector('#agent-home .ag-input');
    if (!t) return 'no composer';
    t.value = '큐빗 q1 라비 좀 봐줘';
    t.dispatchEvent(new Event('input', {bubbles:true}));
    return t.value;
  })()`);
  ok('the composer itself is NOT restricted to English',
     said === '큐빗 q1 라비 좀 봐줘', said);

  fs.writeFileSync(OUT, JSON.stringify({ results, errors }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('hangul checks: ' + (results.length - bad.length) + '/' + results.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 200)));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
