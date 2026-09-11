/* QUESTION 2, probe 3 — the in-flight window measured, human-speed loss,
 * the draft across navigation, and a double Enter. "/" lines only. */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3] || '9432', BASE = process.argv[4] || 'http://127.0.0.1:5432';
const errors = [], results = [], measures = {};
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }
function note(k, v) { measures[k] = v; }
async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push({ kind: 'exception', text: String((m.params.exceptionDetails.exception || {}).description || m.params.exceptionDetails.text) });
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push({ kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') }); };
  const send = (m, p = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method: m, params: p })); });
  const ev = async (x) => { const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true }); if (rr.result && rr.result.exceptionDetails) throw new Error('eval: ' + x.slice(0, 80)); return rr.result.result.value; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const typeChar = async (c) => { await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c, unmodifiedText: c, key: c }); await send('Input.dispatchKeyEvent', { type: 'keyUp', key: c }); };
  const typeText = async (s, d) => { for (const c of s) { await typeChar(c); if (d) await sleep(d); } };
  const press = async (k) => { const kc = { Enter: 13, Tab: 9 }[k]; await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, text: k === 'Enter' ? '\r' : '\t', windowsVirtualKeyCode: kc }); await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, windowsVirtualKeyCode: kc }); };
  const click = async (sel) => { const b = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var r=e.getBoundingClientRect(); return r.width? {x:Math.round(r.left+r.width/2),y:Math.round(r.top+Math.min(r.height/2,14))}:null;})()`); if (!b) return false; await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: b.x, y: b.y, button: 'left', clickCount: 1 }); await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b.x, y: b.y, button: 'left', clickCount: 1 }); return true; };
  const taVal = () => ev(`(function(){var t=document.querySelector('.ag-input'); return t? t.value:null;})()`);
  const setTa = (v) => ev(`(function(){var t=document.querySelector('.ag-input'); t.focus(); t.value=${JSON.stringify(v)}; t.dispatchEvent(new Event('input',{bubbles:true})); t.setSelectionRange(t.value.length,t.value.length); return 1;})()`);
  const cards = () => ev(`document.querySelectorAll('.ag-cards [data-card^="plan:"]').length`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(function(){var f=window.fetch;window.fetch=function(u,o){var url=String((u&&u.url)||u),m=(o&&o.method)||'GET';
    if(m==='POST'&&/\\/api\\/agent\\/chat\\/(start|send)/.test(url)) return Promise.resolve(new Response('{"error":"BLOCKED"}',{status:503,headers:{'Content-Type':'application/json'}}));
    return f.apply(this,arguments);};})();` });

  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(3500);
  await click('.ag-input'); await sleep(150);

  /* 1. how long is the box dead after Enter? (three presses) */
  const windows = [];
  for (let i = 0; i < 3; i++) {
    await setTa('/run 04b_power_rabi q1');
    const t0 = Date.now();
    await press('Enter');
    let dis = false, tOn = null, tOff = null;
    while (Date.now() - t0 < 5000) {
      const d = await ev(`(function(){var t=document.querySelector('.ag-input'); return t? !!t.disabled : null;})()`);
      if (d && tOn === null) { tOn = Date.now() - t0; dis = true; }
      if (dis && !d) { tOff = Date.now() - t0; break; }
      await sleep(4);
    }
    windows.push({ seen_disabled: dis, on_ms: tOn, off_ms: tOff });
    await sleep(600);
  }
  note('disabled_window', windows);
  ok('the composer is not disabled between a press and its answer', windows.every(w => !w.seen_disabled), windows);

  /* 2. human typing speed (45ms/char) straight after Enter */
  await sleep(800);
  await setTa('/run 04b_power_rabi q1');
  await press('Enter');
  await typeText('/run 05_T1 q2', 45);
  await sleep(1500);
  note('human_speed_after_enter', { box: await taVal() });
  ok('at 45 ms/char nothing is lost from the next line', (await taVal()) === '/run 05_T1 q2', await taVal());

  /* 2b. and the mangled leftover, sent: where would it go? */
  const leftover = await taVal();
  note('leftover_would_go_to_model', !!(leftover && leftover.indexOf('/run') !== 0 && leftover.trim()));
  ok('a mangled leftover does not become a model-bound message',
     !(leftover && leftover.trim() && leftover.indexOf('/run') !== 0), leftover);
  await setTa('');

  /* 3. a draft across navigation away and back */
  await setTa('/run 04b_power_rabi q1 num_shots=500');
  await sleep(200);
  await ev(`(function(){var a=document.querySelector('#sidebar a[href="/bulk"], #sidebar a[href="/explorer"]'); if(a){a.click(); return 1;} return 0;})()`);
  await sleep(3500);
  await ev(`(function(){var a=document.getElementById('nav-agent'); if(a){a.click(); return 1;} return 0;})()`);
  await sleep(3500);
  note('draft_after_navigation', { box: await taVal() });
  ok('a draft survives a trip to another page and back',
     (await taVal()) === '/run 04b_power_rabi q1 num_shots=500', await taVal());

  /* 4. Enter pressed twice fast on the same line */
  await click('.ag-input'); await sleep(150);
  const b0 = await cards();
  await setTa('/run 06a_ramsey q1');
  await press('Enter'); await press('Enter');
  await sleep(2200);
  note('double_enter', { cards: (await cards()) - b0, box: await taVal() });
  ok('a double Enter makes ONE card, not two', (await cards()) - b0 === 1, measures.double_enter);

  fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length + '  errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 300)));
  errors.slice(0, 8).forEach(e => console.log('  ERR ' + e.kind + ': ' + String(e.text).slice(0, 160)));
  process.exit(0);
}
main().catch(e => { console.error('driver error: ' + (e && e.stack || e)); fs.writeFileSync(OUT, JSON.stringify({ results, measures, errors, driver: String(e) }, null, 1)); process.exit(1); });
